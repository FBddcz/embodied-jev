"""Experimental calibrated RGB-D observations for the three coloured-block scenes.

Object poses come from rendered pixels, not MuJoCo body or geometry positions.
Robot TCP and contact feedback remain simulated proprioceptive sensors. This is a
small colour detector with a known 4 cm cube prior, not a general vision model.
"""
from __future__ import annotations

import copy
import struct
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

import mujoco
import numpy as np

from .physics import TASKS, TRAVEL_Z

CUBE_SIZE_M = .04
OBJECT_MEMORY_SECONDS = 4.
HELD_MEMORY_SECONDS = 8.
DESTINATION_MEMORY_SECONDS = 12.


class PerceptionUnavailable(ValueError):
    """A required visual estimate is missing or too old to control the robot."""


@dataclass(frozen=True)
class CameraCalibration:
    position: np.ndarray
    forward: np.ndarray
    up: np.ndarray
    fx: float
    fy: float
    cx: float
    cy: float

    def serialise(self):
        return {"position": self.position.tolist(), "forward": self.forward.tolist(),
                "up": self.up.tolist(), "fx": self.fx, "fy": self.fy,
                "cx": self.cx, "cy": self.cy, "coordinate_frame": "world", "units": "metres"}


@dataclass(frozen=True)
class RGBDFrame:
    rgb: np.ndarray
    depth: np.ndarray
    calibration: CameraCalibration


def backproject(depth, mask, calibration):
    """Unproject axial depth; image Y points down and world up follows the camera."""
    rows, columns = np.nonzero(mask & np.isfinite(depth) & (depth > 0) & (depth < 5))
    distances = depth[rows, columns].astype(float)
    forward = np.array(calibration.forward, dtype=float, copy=True)
    up = np.array(calibration.up, dtype=float, copy=True)
    forward /= np.linalg.norm(forward)
    up /= np.linalg.norm(up)
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    return (np.asarray(calibration.position) + distances[:, None] * forward
            + (((columns - calibration.cx) / calibration.fx) * distances)[:, None] * right
            + (((calibration.cy - rows) / calibration.fy) * distances)[:, None] * up)


def estimate_scene(frame: RGBDFrame, task: str):
    """Estimate known coloured objects from RGB and depth, using no segmentation IDs."""
    rgb, depth = np.asarray(frame.rgb), np.asarray(frame.depth)
    if rgb.ndim != 3 or rgb.shape[2] != 3 or depth.shape != rgb.shape[:2]:
        raise ValueError("RGB and depth image sizes must match")
    if task not in TASKS:
        raise ValueError("Unknown visual task")
    r, g, b = np.moveaxis(rgb.astype(float), -1, 0)
    masks = {"object": (r > 45) & (r > 1.45 * g) & (r > 1.45 * b),
             "destination": (b > 45) & (b > 1.45 * r) & (b > 1.15 * g),
             "barrier": (r > 80) & (g > 60) & (r > 1.5 * b) & (g > 1.3 * b)}
    detected = {}
    for name, mask in masks.items():
        points = backproject(depth, mask, frame.calibration)
        # The camera looks down on this known tabletop workspace. Filtering by
        # workspace bounds rejects distant background, not hidden simulator IDs.
        points = points[(points[:, 2] > -.01) & (points[:, 2] < .45)
                        & (points[:, 0] > .15) & (points[:, 0] < .75)
                        & (np.abs(points[:, 1]) < .4)]
        if len(points) < 20:
            continue
        if name == "object":
            top_z = float(np.percentile(points[:, 2], 95))
            surface = points[points[:, 2] >= top_z - .004]
            if len(surface) < 16:
                continue
            low, high = np.min(surface[:, :2], axis=0), np.max(surface[:, :2], axis=0)
            # A narrow red sliver behind the gripper is not a reliable centre.
            if np.any(high - low < .026) or np.any(high - low > .065):
                continue
            position = [*(((low + high) / 2).tolist()), top_z - CUBE_SIZE_M / 2]
        else:
            # Blue tray floor / block top is the support plane. The destination
            # denotes the centre of the red cube when resting on that plane.
            percentile = 15 if name == "destination" and task != "stack" else 95
            surface_z = float(np.percentile(points[:, 2], percentile))
            # A tray's upper rim defines its XY centre without the perspective
            # bias from partly hidden floor pixels. Its lower plane sets height.
            xy_plane_z = float(np.percentile(points[:, 2], 95))
            surface = points[np.abs(points[:, 2] - xy_plane_z) < .004]
            if len(surface) < 20:
                continue
            low, high = np.min(surface[:, :2], axis=0), np.max(surface[:, :2], axis=0)
            if np.any(high - low < (.035 if name == "destination" else .01)):
                continue
            position = [*(((low + high) / 2).tolist()),
                        surface_z + CUBE_SIZE_M / 2 if name == "destination" else surface_z]
        detected[name] = {"position": np.asarray(position, dtype=float),
                          "pixel_count": len(points), "extent_xy_m": (high - low).tolist()}
    return detected


def encode_png(array):
    """Encode RGB8, gray8 or gray16 PNG without another image dependency."""
    array = np.asarray(array)
    if array.dtype == np.uint8 and array.ndim == 3 and array.shape[2] == 3:
        bit_depth, colour_type = 8, 2
    elif array.dtype == np.uint8 and array.ndim == 2:
        bit_depth, colour_type = 8, 0
    elif array.dtype == np.uint16 and array.ndim == 2:
        bit_depth, colour_type = 16, 0
        array = array.astype(">u2")
    else:
        raise ValueError("PNG requires RGB8, gray8 or gray16 pixels")
    height, width = array.shape[:2]

    def chunk(name, value):
        return struct.pack(">I", len(value)) + name + value + struct.pack(">I", zlib.crc32(name + value) & 0xffffffff)

    raw = b"".join(b"\0" + row.tobytes() for row in array)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, bit_depth, colour_type, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 3)) + chunk(b"IEND", b""))


class VisualObserver:
    """Render only on one owning thread; HTTP readers consume immutable PNG caches.

    Call observe while the simulation is stationary, normally at action boundaries.
    The render worker owns its OpenGL context and receives a copy of MjData. The
    physics world itself is never accessed from that thread.
    """
    def __init__(self, world, *, width=640, height=480, geometry=True, camera_views=None):
        self.world = world
        self.geometry = geometry
        self.camera_views = (["external", "wrist"] if getattr(world.model, "ncam", 0) else ["external"]) if camera_views is None else list(camera_views)
        if not self.camera_views or any(view not in {"external", "wrist"} for view in self.camera_views):
            raise ValueError("相机观测需要至少一种有效视角")
        self.width, self.height = width, height
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev-rgbd")
        self._renderer = None
        self._model = world.model
        self._observe_lock = threading.RLock()
        self._cache_lock = threading.Lock()
        self._closed = False
        self._snapshot = None
        self._last_observation = None
        self._last_time = None
        self._seen = {}
        self._last_tcp = None
        self._last_held = False
        self._held_offset = None
        self._initial_object_z = None
        self._stable_seconds = 0.
        self._max_lift = 0.
        self._capture_id = 0

    def _render(self, data, view="external"):
        if self._renderer is None:
            self._renderer = mujoco.Renderer(self._model, height=self.height, width=self.width)
        camera = mujoco.MjvCamera()
        camera.lookat[:] = [.43, 0, .035]
        # A slight side view lets an elevated hand uncover the block. A strict
        # overhead view remained occluded even after the withdrawal primitive.
        camera.distance, camera.azimuth, camera.elevation = 1., 180., -65.
        renderer = self._renderer
        renderer.disable_depth_rendering()
        renderer.update_scene(data, camera if view == "external" else "wrist_camera")
        rgb = renderer.render().copy()
        renderer.enable_depth_rendering()
        depth = renderer.render().copy()
        renderer.disable_depth_rendering()
        left, right = renderer.scene.camera
        # MuJoCo's mono image uses the midpoint of the two stored eye cameras.
        near = float(left.frustum_near)
        fy = self.height * near / (float(left.frustum_top) - float(left.frustum_bottom))
        calibration = CameraCalibration(
            (left.pos.astype(float) + right.pos.astype(float)) / 2,
            (left.forward.astype(float) + right.forward.astype(float)) / 2,
            (left.up.astype(float) + right.up.astype(float)) / 2,
            fy, fy, (self.width - 1) / 2, (self.height - 1) / 2)
        return RGBDFrame(rgb, depth, calibration)

    def invalidate(self):
        """An explicit external intervention can change a frame at the same sim time."""
        with self._observe_lock:
            self._last_time = None
            self._last_observation = None
            self._seen.clear()
            self._held_offset = None
            self._last_held = False

    @staticmethod
    def _encoded_frame(frame):
        valid = np.isfinite(frame.depth) & (frame.depth > 0) & (frame.depth < 65.535)
        depth_mm = np.where(valid, np.clip(frame.depth * 1000, 0, 65535), 0).astype(np.uint16)
        display = np.where(valid, np.clip((1.25 - frame.depth), 0, 1) * 255, 0).astype(np.uint8)
        return {"rgb": encode_png(frame.rgb), "depth": encode_png(depth_mm),
                "depth_display": encode_png(display), "calibration": frame.calibration.serialise()}

    def _tracked_objects(self, detections, tcp, held, sim_time):
        rows, estimates = [], {}
        labels = {"object": "红色方块", "destination": "蓝色目标", "barrier": "黄色障碍"}
        names = ("object", "destination", "barrier") if self.world.task == "barrier" else ("object", "destination")
        for name in names:
            detected = detections.get(name)
            previous = self._seen.get(name)
            # On the release boundary the last held estimate is still useful;
            # the next unheld observation must meet the shorter static limit.
            limit = (HELD_MEMORY_SECONDS if held or self._last_held else OBJECT_MEMORY_SECONDS) if name == "object" else DESTINATION_MEMORY_SECONDS
            age = 0. if detected else sim_time - previous["time"] if previous else None
            position, tracked = None, False
            method = "rgb_colour_and_depth"
            if detected:
                position = detected["position"].copy()
                self._seen[name] = {"position": position.copy(), "time": sim_time}
                if name == "object" and held:
                    self._held_offset = position - tcp
            elif previous and age <= limit:
                tracked, position = True, previous["position"].copy()
                method = "last_seen_static"
                if name == "object" and held:
                    if not self._last_held or self._held_offset is None:
                        self._held_offset = position - tcp
                    position = tcp + self._held_offset
                    method = "last_seen_plus_tcp_contact"
                elif name == "object" and self._last_held and self._last_tcp is not None and self._held_offset is not None:
                    position = self._last_tcp + self._held_offset
                    # Keep the propagated release location while retaining the
                    # last *visual* timestamp, so expiry is never silently reset.
                    previous["position"] = position.copy()
            if position is not None:
                estimates[name] = position
            row = {"id": name, "label": labels[name], "visible": detected is not None,
                   "position": position.round(5).tolist() if position is not None else None,
                   "tracked": tracked, "age_sim_seconds": round(age, 3) if age is not None else None,
                   "method": method if position is not None else "unavailable"}
            if detected:
                row.update(pixel_count=detected["pixel_count"], extent_xy_m=detected["extent_xy_m"])
            rows.append(row)
        return rows, estimates

    def observe(self):
        with self._observe_lock:
            if self._closed:
                raise PerceptionUnavailable("视觉相机已经关闭，请重置实验")
            sim_time = float(self.world.data.time) - getattr(self.world, "start_time", 0)
            if self._last_time == sim_time and self._last_observation is not None:
                return copy.deepcopy(self._last_observation)
            started = time.perf_counter()
            try:
                data = copy.copy(self.world.data)
                frames = {view: (self._executor.submit(self._render, data).result(timeout=30)
                                 if view == "external" else
                                 self._executor.submit(self._render, data, view).result(timeout=30))
                          for view in self.camera_views}
                frame = next(iter(frames.values()))
            except Exception as exc:
                # Backend exceptions may contain local graphics-driver details;
                # the UI gets a useful, fixed message without leaking them.
                raise PerceptionUnavailable("RGB-D 相机无法渲染；请检查本地 OpenGL 或无界面渲染环境") from exc
            detections = {}
            if self.geometry:
                # External detections take precedence; another enabled view may
                # fill occluded objects. Disabled views are never rendered/read.
                for view_frame in frames.values():
                    for name, detection in estimate_scene(view_frame, self.world.task).items():
                        detections.setdefault(name, detection)
            tcp = np.asarray(self.world.position, dtype=float)
            fingers, support, forbidden = self.world.contacts()
            held = len(fingers) == 2 and self.world.closed
            rows, estimates = self._tracked_objects(detections, tcp, held, sim_time) if self.geometry else ([], {})
            required = (("object", "destination", "barrier") if self.world.task == "barrier" else ("object", "destination")) if self.geometry else ()
            missing = [name for name in required if name not in estimates]
            self._capture_id += 1
            metadata = {
                "capture_id": self._capture_id, "source": "rgbd" if self.geometry else "vision",
                "status": "unavailable" if missing else "partial" if any(row["tracked"] for row in rows) else "ready",
                "captured_at": datetime.now(timezone.utc).isoformat(), "sim_time": round(sim_time, 4),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "objects": rows, "width": self.width, "height": self.height,
                "calibration": frame.calibration.serialise(),
                "camera_view": " + ".join(self.camera_views),
                "camera_views": list(self.camera_views),
                "primary_view": self.camera_views[0],
                "capture_schedule": "fresh synchronized views at decision/action boundaries; not a continuous video stream",
                "depth_units": "millimetres", "depth_invalid_value": 0,
                "depth_display_range_m": [.25, 1.25],
                "detector": "known-colour RGB masks + calibrated metric depth" if self.geometry else "none; raw RGB to model",
                "proprioception": "simulated TCP, gripper command and finger/support contacts",
                "tracking_limits_sim_seconds": {"unheld_object": OBJECT_MEMORY_SECONDS,
                    "held_object": HELD_MEMORY_SECONDS, "destination": DESTINATION_MEMORY_SECONDS},
                "tracking_assumption": "static unheld objects; no slip while two finger contacts persist",
            }
            if self.geometry:
                metadata["cube_size_prior_m"] = CUBE_SIZE_M
            else:
                metadata["message"] = "已启用视角的 RGB 图像可用于模型输入；不含物体或目标坐标。"
            metadata["views"] = {view: {"calibration": view_frame.calibration.serialise(),
                                       "mount": "fixed" if view == "external" else "robot hand"}
                                 for view, view_frame in frames.items()}
            if missing:
                metadata["message"] = "看不到必需的方块、目标或障碍，且没有有效视觉定位；已停止，未使用仿真真值补齐。"
            elif any(row["tracked"] for row in rows):
                metadata["message"] = "部分物体被遮挡，正在限时使用上次视觉定位与夹爪反馈。"
            encoded = {view: self._encoded_frame(view_frame) for view, view_frame in frames.items()}
            snapshot = {**encoded[self.camera_views[0]], "metadata": metadata, "views": encoded}
            metadata["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            with self._cache_lock:
                self._snapshot = snapshot
            if missing:
                raise PerceptionUnavailable(metadata["message"])
            if not self.geometry:
                observation = {"task": TASKS[self.world.task]["goal"],
                    "source": " + ".join(self.camera_views) + " RGB pixels + simulated proprioception",
                    "units": "metres", "tcp": tcp.round(5).tolist(),
                    "gripper": "closed" if self.world.closed else "open", "finger_contacts": sorted(fingers),
                    "held": held, "grasp_secured": self.world.contact_seconds >= .16,
                    "support_contact": support, "forbidden_contact": forbidden,
                    "sim_seconds": round(sim_time, 3), "perception": copy.deepcopy(metadata)}
                self._last_time, self._last_tcp, self._last_held = sim_time, tcp.copy(), held
                self._last_observation = copy.deepcopy(observation)
                return observation
            cube, target = estimates["object"], estimates["destination"]
            travel_height = max(TRAVEL_Z, float(estimates["barrier"][2]) + .06) if "barrier" in estimates else TRAVEL_Z
            if travel_height > .42:
                raise PerceptionUnavailable("视觉估计的障碍高度超出可用工作区，请调整场景")
            elapsed = max(0., sim_time - self._last_time) if self._last_time is not None else 0.
            old_cube = np.asarray(self._last_observation["object"]) if self._last_observation else cube
            speed = float(np.linalg.norm(cube - old_cube)) / elapsed if elapsed else float("inf")
            aligned = np.linalg.norm(cube[:2] - target[:2]) < .025
            stable = support and aligned and speed < .025 and not self.world.closed
            self._stable_seconds = self._stable_seconds + elapsed if stable else 0.
            if self._initial_object_z is None:
                self._initial_object_z = float(cube[2])
            self._max_lift = max(self._max_lift, float(cube[2]) - self._initial_object_z)
            observation = {
                "task": TASKS[self.world.task]["goal"], "source": "RGB-D estimates + simulated proprioception",
                "units": "metres", "scene_name": self.world.scene_name,
                "tcp": tcp.round(5).tolist(), "object": cube.round(5).tolist(),
                "destination": target.round(5).tolist(),
                "relative_geometry": {
                    "tcp_object_xy_distance_m": round(float(np.linalg.norm(tcp[:2] - cube[:2])), 4),
                    "tcp_above_object_m": round(float(tcp[2] - cube[2]), 4),
                    "object_destination_xy_distance_m": round(float(np.linalg.norm(cube[:2] - target[:2])), 4),
                    "object_above_destination_m": round(float(cube[2] - target[2]), 4),
                    "travel_tcp_height_m": round(travel_height, 4),
                },
                "gripper": "closed" if self.world.closed else "open", "finger_contacts": sorted(fingers),
                "held": held, "grasp_secured": self.world.contact_seconds >= .16,
                "support_contact": support, "forbidden_contact": forbidden,
                "stable_seconds": round(self._stable_seconds, 3),
                "success": bool(self._stable_seconds >= .4 and not self.world.closed and tcp[2] >= .17),
                "max_lift_m": round(self._max_lift, 4), "sim_seconds": round(sim_time, 3),
                "perception": copy.deepcopy(metadata),
            }
            if "barrier" in estimates:
                observation["visual_barrier"] = estimates["barrier"].round(5).tolist()
            self._last_time, self._last_tcp, self._last_held = sim_time, tcp.copy(), held
            self._last_observation = copy.deepcopy(observation)
            return observation

    def camera_snapshot(self):
        with self._cache_lock:
            return copy.deepcopy(self._snapshot)

    def close(self):
        with self._observe_lock:
            if self._closed:
                return
            self._closed = True

            def close_renderer():
                if self._renderer is not None:
                    self._renderer.close()
                    self._renderer = None

            try:
                self._executor.submit(close_renderer).result(timeout=30)
            finally:
                self._executor.shutdown(wait=True, cancel_futures=True)


class ImageObserver(VisualObserver):
    """Pixel observations with no colour detection or supplied object coordinates."""
    def __init__(self, world, **kwargs):
        super().__init__(world, geometry=False, **kwargs)
