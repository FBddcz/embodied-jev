"""Isolated LIBERO RGB-D worker; no model credentials or object-state policy input.

Run this file with the LIBERO environment's Python. The main application talks
newline-delimited JSON over stdio. Camera geometry is calibration, not semantic
object ground truth. Task predicates are exposed only in evaluation results.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

try:
    from .benchmark_worker import image_packet, validate_action
except ImportError:  # Standalone worker, deliberately independent of app dependencies.
    from benchmark_worker import image_packet, validate_action

LIBERO_REVISION = "8f1084e3132a39270c3a13ebe37270a43ece2a01"


def bootstrap(root, config_dir):
    """Avoid LIBERO's interactive first-import prompt and global ~/.libero writes."""
    root, config_dir = Path(root).resolve(), Path(config_dir).resolve()
    base = root / "libero/libero"
    for name in ("assets", "bddl_files", "init_files"):
        if not (base / name).is_dir():
            raise ValueError(f"LIBERO root is missing {name}")
    source_manifest = root / ".libero-source.json"
    if source_manifest.is_file():
        provenance = json.loads(source_manifest.read_text())
        revision = provenance["revision"]
        for item in provenance["files"]:
            path = (root / item["path"]).resolve()
            path.relative_to(root)
            data = path.read_bytes()
            digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if digest != item["sha"]:
                raise ValueError("LIBERO source/asset hash mismatch: " + item["path"])
    else:
        revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if revision != LIBERO_REVISION:
        raise ValueError(f"Expected LIBERO {LIBERO_REVISION}; found {revision}")
    import yaml
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {"benchmark_root": str(base), "bddl_files": str(base / "bddl_files"),
              "init_states": str(base / "init_files"), "assets": str(base / "assets"),
              "datasets": str(root / "datasets")}
    path = config_dir / "config.yaml"
    if path.exists() and yaml.safe_load(path.read_text()) != config:
        raise ValueError("Conflicting LIBERO config; use a new --config-dir")
    if not path.exists():
        path.write_text(yaml.safe_dump(config))
    os.environ["LIBERO_CONFIG_PATH"] = str(config_dir)
    os.environ.setdefault("MUJOCO_GL", "cgl" if sys.platform == "darwin" else "egl")
    sys.path.insert(0, str(root))
    return revision


class VisionEnvironment:
    cameras = {"external": "agentview", "wrist": "robot0_eye_in_hand"}

    def __init__(self, request):
        import numpy as np
        revision = bootstrap(request["libero_root"], request["config_dir"])
        from libero.libero import benchmark
        from libero.libero.envs.env_wrapper import ControlEnv
        case = request["case"]
        suite = benchmark.get_benchmark_dict()[case["suite"]](task_order_index=0)
        task_id = case["task_id"]
        if not 0 <= task_id < suite.get_num_tasks():
            raise ValueError("LIBERO task_id out of range")
        task = suite.get_task(task_id)
        states = suite.get_task_init_states(task_id)
        if not 0 <= case["init_index"] < len(states):
            raise ValueError("LIBERO init_index out of range")
        self.size = request.get("camera_size", 384)
        if type(self.size) is not int or not 128 <= self.size <= 768:
            raise ValueError("Camera size must be 128..768")
        self.horizon = request["horizon"]
        self.steps = 0
        self.language = task.language
        self.initial_state = np.asarray(states[case["init_index"]], dtype=np.float64)
        settle = request.get("settle_steps", 10)
        self.env = ControlEnv(bddl_file_name=suite.get_task_bddl_file_path(task_id),
            use_camera_obs=True, has_offscreen_renderer=True, camera_depths=True,
            camera_names=list(self.cameras.values()), camera_heights=self.size, camera_widths=self.size,
            controller="OSC_POSE", control_freq=20, horizon=self.horizon + settle,
            initialization_noise=None)
        self.env.seed(case["seed"])
        self.env.reset()
        self.raw = self.env.set_init_state(self.initial_state)
        # Same fixed warm-up for both methods, outside the scored action budget.
        for _ in range(settle):
            self.raw, _, _, _ = self.env.step(np.array([0., 0., 0., 0., 0., 0., -1.]))
        state_hash = hashlib.sha256(np.asarray(self.env.get_sim_state(), dtype="<f8").tobytes()).hexdigest()
        controller = self.env.robots[0].controller
        self.metadata = {
            "benchmark": "LIBERO", "libero_revision": revision, "case": case, "task_name": task.name,
            "language": self.language, "initial_state_sha256": hashlib.sha256(self.initial_state.astype("<f8").tobytes()).hexdigest(),
            "settled_state_sha256": state_hash, "settle_steps": settle,
            "versions": {name: importlib.metadata.version(name) for name in ("mujoco", "robosuite", "numpy", "torch")},
            "camera_names": self.cameras, "camera_size": self.size, "camera_transform": "vertical flip to top-left origin",
            "observation": "RGB-D + camera calibration + robot proprioception; no object poses or task predicates",
            "success_source": "official env.check_success()", "controller": "OSC_POSE", "control_hz": 20,
            "action_output_min": np.asarray(controller.output_min).tolist(),
            "action_output_max": np.asarray(controller.output_max).tolist(),
            "action_frame": "world XYZ and world axis-angle increments; gripper -1=open +1=close",
            "initial_success": bool(self.env.check_success()),
        }

    def observation(self):
        import numpy as np
        # Only robot sensors. Do not attach object names, poses, reward or contacts
        # classified by hidden object identity to this policy-facing dictionary.
        return {"task": self.language, "step": self.steps,
                "tcp": np.asarray(self.raw["robot0_eef_pos"]).tolist(),
                "quaternion_xyzw": np.asarray(self.raw["robot0_eef_quat"]).tolist(),
                "gripper_qpos": np.asarray(self.raw["robot0_gripper_qpos"]).tolist(),
                "frame": "world; metres; TCP = robot0_eef_pos"}

    def capture(self):
        import numpy as np
        from robosuite.utils.camera_utils import get_camera_intrinsic_matrix, get_camera_extrinsic_matrix, get_real_depth_map
        images = {}
        for view, camera in self.cameras.items():
            # Robosuite 1.4.1 renders with bottom-left origin. Both RGB and depth
            # are flipped identically; camera_utils calibration uses top-left.
            rgb = np.ascontiguousarray(self.raw[camera + "_image"][::-1])
            depth = np.ascontiguousarray(get_real_depth_map(self.env.sim, self.raw[camera + "_depth"])[::-1].squeeze(), dtype="<f4")
            images[view] = {**image_packet(rgb), "depth_encoding": "float32-le-metres",
                "depth": base64.b64encode(depth.tobytes()).decode("ascii"),
                "depth_sha256": hashlib.sha256(depth.tobytes()).hexdigest(),
                "intrinsics": get_camera_intrinsic_matrix(self.env.sim, camera, self.size, self.size).tolist(),
                "camera_to_world": get_camera_extrinsic_matrix(self.env.sim, camera).tolist()}
        return images

    def reset_result(self):
        return {"observation": self.observation(), "images": self.capture(), "metadata": self.metadata}

    def step(self, action, capture=True):
        action = validate_action(action, 7)
        self.raw, _, done, _ = self.env.step(action)
        self.steps += 1
        success = bool(self.env.check_success())
        return {"observation": self.observation(), "success": success,
                "truncated": bool(done or self.steps >= self.horizon), "action": action.tolist(),
                **({"images": self.capture()} if capture or success else {})}

    def close(self):
        self.env.close()


def main():
    backend = None
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                with contextlib.redirect_stdout(sys.stderr):
                    if request["command"] == "reset":
                        if backend is not None:
                            backend.close()
                        backend = VisionEnvironment(request)
                        result = backend.reset_result()
                    elif request["command"] == "step" and backend is not None:
                        result = backend.step(request["action"], request.get("capture", True))
                    elif request["command"] == "close":
                        break
                    else:
                        raise ValueError("Invalid command or missing reset")
                payload = {"ok": True, **result}
            except Exception as exc:
                import traceback
                traceback.print_exc(file=sys.stderr)
                payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
            print(json.dumps(payload, allow_nan=False), flush=True)
    finally:
        if backend is not None:
            with contextlib.redirect_stdout(sys.stderr):
                backend.close()


if __name__ == "__main__":
    main()
