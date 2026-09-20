import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest
from fastapi.testclient import TestClient

from embodied_jev.perception import CameraCalibration, ImageObserver, RGBDFrame
from embodied_jev.physics import RobotWorld
from embodied_jev.runtime import Session
from embodied_jev.server import create_app


class Sensors:
    model = None
    task = "transfer"
    start_time = 0
    position = np.array([.38, -.08, .22])
    closed = False
    contact_seconds = 0

    def __init__(self):
        self.data = SimpleNamespace(time=0)

    def contacts(self):
        return set(), False, False

    def __getattr__(self, name):
        raise AssertionError(f"Image observer read forbidden simulator field {name}")


def pixels(value=20):
    calibration = CameraCalibration(np.array([.4, 0, 1.]), np.array([0., 0., -1.]),
                                    np.array([0., 1., 0.]), 10, 10, 1, 1)
    return RGBDFrame(np.full((4, 4, 3), value, dtype=np.uint8),
                     np.ones((4, 4), dtype=np.float32), calibration)


def fake_image_observer(world, **kwargs):
    observer = ImageObserver(world, **{"width": 4, "height": 4,
                                       "camera_views": ["external", "wrist"], **kwargs})
    observer._model = SimpleNamespace(ncam=1)
    observer.rendered_views = []

    def render(data, view="external"):
        observer.rendered_views.append(view)
        return pixels(20 if view == "external" else 80)

    observer._render = render
    return observer


def test_direct_images_need_no_detection_or_object_coordinates(monkeypatch):
    monkeypatch.setattr("embodied_jev.perception.estimate_scene", lambda *args: pytest.fail("detector invoked"))
    observer = fake_image_observer(Sensors())
    try:
        observation = observer.observe()
        assert not {"object", "destination", "relative_geometry", "scene_config", "success"} & observation.keys()
        assert observation["perception"]["source"] == "vision"
        assert observation["perception"]["objects"] == []
        snapshot = observer.camera_snapshot()
        assert set(snapshot["views"]) == {"external", "wrist"}
        assert snapshot["views"]["external"]["rgb"] != snapshot["views"]["wrist"]["rgb"]
        assert set(observation["perception"]["views"]) == {"external", "wrist"}
    finally:
        observer.close()


@pytest.mark.parametrize("views", [["external"], ["wrist"], ["external", "wrist"]])
def test_only_enabled_cameras_are_rendered_and_archived_in_view_order(views):
    observer = fake_image_observer(Sensors(), camera_views=views)
    try:
        observation = observer.observe()
        snapshot = observer.camera_snapshot()
        assert observer.rendered_views == views
        assert list(snapshot["views"]) == views
        assert list(observation["perception"]["views"]) == views
        assert observation["perception"]["camera_views"] == views
        assert observation["perception"]["primary_view"] == views[0]
        assert snapshot["rgb"] == snapshot["views"][views[0]]["rgb"]
        observer.observe()
        assert observer.rendered_views == views  # Cached HTTP/same-time reads do not render disabled views.
    finally:
        observer.close()


def test_invalidation_refreshes_frames_without_advancing_simulation():
    world = Sensors()
    observer = fake_image_observer(world)
    try:
        before = observer.observe()
        observer._render = lambda data, view="external": pixels(100)
        assert observer.observe()["perception"]["capture_id"] == before["perception"]["capture_id"]
        first = observer.camera_snapshot()["rgb"]
        observer.invalidate()
        after = observer.observe()
        assert after["perception"]["capture_id"] == before["perception"]["capture_id"] + 1
        assert before["sim_seconds"] == after["sim_seconds"]
        assert first != observer.camera_snapshot()["rgb"]
    finally:
        observer.close()


def test_wrist_camera_is_mounted_on_hand_and_moves_with_robot():
    world = RobotWorld()
    camera_id = world.model.camera("wrist_camera").id
    assert world.model.cam_bodyid[camera_id] == world.model.body("hand").id
    before = world.data.cam_xpos[camera_id].copy()
    direction = -world.data.cam_xmat[camera_id].reshape(3, 3)[:, 2]
    assert np.dot(direction, world.cube - before) > 0
    for _ in world.motion(world.position + [.02, -.02, -.03], seconds=.5):
        pass
    mujoco.mj_forward(world.model, world.data)
    assert np.linalg.norm(world.data.cam_xpos[camera_id] - before) > .02


def test_archive_preserves_each_captured_view_and_its_hash(monkeypatch):
    monkeypatch.setattr("embodied_jev.perception.ImageObserver", fake_image_observer)
    session = Session(provider="chat", connection={"url": "https://example.test/v1/chat/completions", "key": "", "model": "test"},
                      control_mode="incremental", observation_mode="vision")
    try:
        export = session.export()
        assert len(export["camera_manifest"]) == 2
        with zipfile.ZipFile(io.BytesIO(session.camera_archive())) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["episode_id"] == session.id
            for item in manifest["frames"]:
                data = archive.read(item["file"])
                assert hashlib.sha256(data).hexdigest() == item["sha256"]
                assert len(data) == item["byte_length"]
        assert "data:image" not in json.dumps(export)
    finally:
        session.stop()


def test_camera_routes_select_views_and_reject_stale_archive(monkeypatch):
    monkeypatch.setattr("embodied_jev.perception.ImageObserver", fake_image_observer)
    with TestClient(create_app()) as client:
        app = client.app
        app.state.connections["chat"] = {"url": "https://example.test/v1/chat/completions", "key": "", "model": "test"}
        response = client.post("/api/reset", json={"provider": "chat", "control_mode": "incremental", "observation_mode": "vision"})
        assert response.status_code == 200, response.text
        state = response.json()
        query = {"episode_id": state["id"], "capture_id": state["perception"]["capture_id"]}
        external = client.get("/api/perception/rgb.png", params={**query, "view": "external"})
        wrist = client.get("/api/perception/rgb.png", params={**query, "view": "wrist"})
        assert external.status_code == wrist.status_code == 200
        assert external.content != wrist.content
        archive = client.get("/api/export/cameras.zip", params={"episode_id": state["id"]})
        assert archive.status_code == 200
        assert archive.headers["content-type"] == "application/zip"
        assert client.get("/api/export/cameras.zip", params={"episode_id": "stale"}).status_code == 409


@pytest.mark.parametrize("enabled,disabled", [("external", "wrist"), ("wrist", "external")])
def test_disabled_camera_route_never_returns_a_different_view(monkeypatch, enabled, disabled):
    monkeypatch.setattr("embodied_jev.perception.ImageObserver", fake_image_observer)
    with TestClient(create_app()) as client:
        response = client.post("/api/reset", json={"camera_views": [enabled]})
        assert response.status_code == 200, response.text
        state = response.json()
        assert state["observation_mode"] == "privileged"
        assert state["camera_views"] == [enabled]
        query = {"episode_id": state["id"], "capture_id": state["perception"]["capture_id"]}
        visible = client.get("/api/perception/rgb.png", params={**query, "view": enabled})
        missing = client.get("/api/perception/rgb.png", params={**query, "view": disabled})
        assert visible.status_code == 200
        assert missing.status_code == 404
        assert client.app.state.session.observer.rendered_views == [enabled]
        with zipfile.ZipFile(io.BytesIO(client.app.state.session.camera_archive())) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["camera_views"] == [enabled]
            assert {item["view"] for item in manifest["frames"]} == {enabled}
