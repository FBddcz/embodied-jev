import struct
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from embodied_jev.perception import (
    CameraCalibration, PerceptionUnavailable, RGBDFrame, VisualObserver,
    backproject, encode_png, estimate_scene,
)


def synthetic_frame(*, object_xy=(.43, -.17), object_top=.04, object_visible=True,
                    target_xy=(.43, .18), target_visible=True, barrier=False):
    width, height, focal = 640, 480, 580.
    camera = CameraCalibration(np.array([.43, 0, 1.035]), np.array([0., 0., -1.]),
                               np.array([0., 1., 0.]), focal, focal, 319.5, 239.5)
    rgb = np.full((height, width, 3), 180, dtype=np.uint8)
    depth = np.full((height, width), 1.035, dtype=np.float32)
    rows, cols = np.indices(depth.shape)

    def paint(xy, size, top, colour):
        distance = camera.position[2] - top
        x = camera.position[0] + (cols - camera.cx) * distance / focal
        y = camera.position[1] + (camera.cy - rows) * distance / focal
        mask = (np.abs(x - xy[0]) <= size[0] / 2) & (np.abs(y - xy[1]) <= size[1] / 2)
        rgb[mask], depth[mask] = colour, distance

    if target_visible:
        paint(target_xy, (.14, .14), .006, [25, 110, 184])
    if barrier:
        paint((.43, 0), (.23, .036), .11, [235, 164, 38])
    if object_visible:
        paint(object_xy, (.04, .04), object_top, [225, 48, 48])
    return RGBDFrame(rgb, depth, camera)


class SensorWorld:
    """Only camera-independent proprioception is accessible to the observer."""
    model = None
    start_time = 0.
    task = "transfer"
    scene_name = "test scene"
    closed = False
    contact_seconds = 0.

    def __init__(self):
        self.data = SimpleNamespace(time=0.)
        self.position = np.array([.38, -.08, .22])
        self.fingers, self.support, self.forbidden = set(), False, False

    def contacts(self):
        return self.fingers, self.support, self.forbidden

    def __getattr__(self, name):
        if name in {"cube", "target", "observe", "success", "stable_seconds", "max_lift", "scene_config"}:
            raise AssertionError(f"Privileged state read: {name}")
        raise AttributeError(name)


@pytest.fixture
def visual():
    world = SensorWorld()
    observer = VisualObserver(world)
    frame = {"value": synthetic_frame(), "calls": []}

    def render(data):
        frame["calls"].append(threading.get_ident())
        return frame["value"]

    observer._render = render
    yield world, observer, frame
    observer.close()


def test_calibrated_depth_uses_image_axes_and_camera_translation():
    calibration = CameraCalibration(np.array([2., 3., 4.]), np.array([0., 0., -1.]),
                                    np.array([0., 1., 0.]), 10., 10., 1., 1.)
    depth = np.full((3, 3), 2.)
    mask = np.zeros((3, 3), dtype=bool)
    mask[0, 2] = True
    assert np.allclose(backproject(depth, mask, calibration), [[2.2, 3.2, 2.]])
    depth[0, 2] = np.nan
    assert backproject(depth, mask, calibration).shape == (0, 3)


def test_colour_detection_estimates_changed_positions_and_cube_size_prior():
    frame = synthetic_frame(object_xy=(.51, -.13), target_xy=(.36, .16), barrier=True)
    detected = estimate_scene(frame, "barrier")
    assert np.allclose(detected["object"]["position"], [.51, -.13, .02], atol=.0018)
    assert np.allclose(detected["destination"]["position"], [.36, .16, .026], atol=.0018)
    assert np.allclose(detected["barrier"]["position"], [.43, 0, .11], atol=.0018)


def test_rejects_thin_occlusion_sliver_and_invalid_depth():
    frame = synthetic_frame()
    red = frame.rgb[:, :, 0] > 200
    _, columns = np.nonzero(red)
    frame.rgb[red & (np.indices(red.shape)[1] > columns.min() + 3)] = 180
    assert "object" not in estimate_scene(frame, "transfer")
    invalid = synthetic_frame()
    invalid.depth[:] = np.nan
    assert estimate_scene(invalid, "transfer") == {}


def test_observation_has_visual_positions_no_truth_and_reuses_same_frame(visual):
    world, observer, frame = visual
    observation = observer.observe()
    assert np.allclose(observation["object"], [.43, -.17, .02], atol=.0018)
    assert "scene_config" not in observation
    assert observation["perception"]["detector"].startswith("known-colour")
    assert observation["perception"]["source"] == "rgbd"
    again = observer.observe()
    again["object"][0] = -1
    assert observer.observe()["object"][0] > 0
    assert len(frame["calls"]) == 1
    assert frame["calls"][0] != threading.get_ident()
    world.data.time = 1.
    frame["value"] = synthetic_frame(object_xy=(.49, -.12))
    assert np.allclose(observer.observe()["object"][:2], [.49, -.12], atol=.0018)


def test_missing_initial_target_fails_and_still_exports_camera(visual):
    _, observer, frame = visual
    frame["value"] = synthetic_frame(target_visible=False)
    with pytest.raises(PerceptionUnavailable, match="未使用仿真真值"):
        observer.observe()
    snapshot = observer.camera_snapshot()
    assert snapshot["metadata"]["status"] == "unavailable"
    assert snapshot["rgb"].startswith(b"\x89PNG")


def test_barrier_is_required_only_in_barrier_task(visual):
    world, observer, frame = visual
    world.task = "barrier"
    with pytest.raises(PerceptionUnavailable):
        observer.observe()
    frame["value"] = synthetic_frame(barrier=True)
    observation = observer.observe()
    assert observation["visual_barrier"][2] == pytest.approx(.11, abs=.001)
    assert observation["relative_geometry"]["travel_tcp_height_m"] == .22


def test_destination_tracking_expires_even_when_object_is_visible(visual):
    world, observer, frame = visual
    observer.observe()
    frame["value"] = synthetic_frame(target_visible=False)
    world.data.time = 10.
    observation = observer.observe()
    assert observation["perception"]["objects"][1]["tracked"]
    world.data.time = 12.01
    with pytest.raises(PerceptionUnavailable):
        observer.observe()


def test_unheld_occlusion_expires_without_truth_fallback(visual):
    world, observer, frame = visual
    initial = observer.observe()["object"]
    frame["value"] = synthetic_frame(object_visible=False)
    world.data.time = 2.
    observation = observer.observe()
    assert observation["object"] == initial
    assert observation["perception"]["objects"][0]["tracked"]
    world.data.time = 4.01
    with pytest.raises(PerceptionUnavailable):
        observer.observe()
    assert observer.camera_snapshot()["metadata"]["objects"][0]["position"] is None


def test_contact_tracking_moves_with_tcp_without_resetting_visual_age(visual):
    world, observer, frame = visual
    initial = np.array(observer.observe()["object"])
    frame["value"] = synthetic_frame(object_visible=False)
    world.position = initial + [0, 0, .001]
    world.closed, world.fingers = True, {"left", "right"}
    world.data.time = 1.
    observer.observe()
    world.position += [.01, .2, .18]
    world.data.time = 3.
    observation = observer.observe()
    assert np.allclose(observation["object"], initial + [.01, .2, .18], atol=1e-5)
    row = observation["perception"]["objects"][0]
    assert row["method"] == "last_seen_plus_tcp_contact"
    assert row["age_sim_seconds"] == 3.
    world.data.time = 8.01
    with pytest.raises(PerceptionUnavailable):
        observer.observe()


def test_release_boundary_keeps_last_held_position_but_does_not_extend_age(visual):
    world, observer, frame = visual
    initial = np.array(observer.observe()["object"])
    frame["value"] = synthetic_frame(object_visible=False)
    world.position = initial + [0, 0, .001]
    world.closed, world.fingers = True, {"left", "right"}
    world.data.time = 1.
    observer.observe()
    world.position += [0, .35, .006]
    world.data.time = 5.
    carried = observer.observe()["object"]
    world.closed, world.fingers = False, set()
    world.data.time = 6.
    released = observer.observe()
    assert released["object"] == carried
    assert released["perception"]["objects"][0]["age_sim_seconds"] == 6.
    world.data.time = 6.1
    with pytest.raises(PerceptionUnavailable):
        observer.observe()


def test_visual_success_uses_measured_alignment_and_sensor_contacts(visual):
    world, observer, frame = visual
    frame["value"] = synthetic_frame(object_xy=(.43, .18), object_top=.046)
    world.position = np.array([.43, .18, .22])
    world.support = True
    assert not observer.observe()["success"]
    world.data.time = .5
    observation = observer.observe()
    assert observation["success"]
    assert observation["stable_seconds"] == .5
    world.data.time = 1.
    world.support = False
    assert not observer.observe()["success"]


def test_png_formats_and_snapshot_copy_are_safe_for_http_reader(visual):
    _, observer, frame = visual
    observer.observe()
    snapshot = observer.camera_snapshot()
    for name, expected_depth, colour in (("rgb", 8, 2), ("depth", 16, 0), ("depth_display", 8, 0)):
        png = snapshot[name]
        width, height, bits, kind = struct.unpack(">IIBB", png[16:26])
        assert (width, height, bits, kind) == (640, 480, expected_depth, colour)
    snapshot["metadata"]["status"] = "modified"
    assert observer.camera_snapshot()["metadata"]["status"] == "ready"
    assert len(frame["calls"]) == 1
    with pytest.raises(ValueError):
        encode_png(np.zeros((2, 2), dtype=float))


def test_renderer_close_happens_on_the_same_worker_thread(visual):
    _, observer, frame = visual
    observer.observe()
    closed = []
    observer._renderer = SimpleNamespace(close=lambda: closed.append(threading.get_ident()))
    observer.close()
    observer.close()
    assert closed == frame["calls"]
    with pytest.raises(PerceptionUnavailable, match="关闭"):
        observer.observe()
