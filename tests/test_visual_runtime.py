"""Visual observations must control planning, not merely decorate the UI."""
import copy
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from embodied_jev.evidence import compact_observation
from embodied_jev.perception import encode_png
from embodied_jev.physics import RobotWorld
from embodied_jev.planning import baseline_phase, candidates, eligible_phases, phase_options
from embodied_jev.runtime import Session
from embodied_jev.server import create_app


class NoOracle:
    def __getattr__(self, name):
        raise AssertionError(f"Planning read simulator field {name}")


def test_all_stage_and_target_planning_uses_supplied_estimates():
    observation = RobotWorld().observe()
    observation.update(object=[.49, -.23, .03], destination=[.46, .22, .06])
    observation["relative_geometry"]["travel_tcp_height_m"] = .27
    world = NoOracle()
    phases = eligible_phases(world, observation)
    assert phases == ["approach"]
    assert baseline_phase(world, observation) == "approach"
    assert "approach" in phase_options(world, phases, observation)
    assert candidates(world, "approach", False, observation)[0].target == pytest.approx([.49, -.23, .17])
    assert candidates(world, "descend", False, observation)[0].target == pytest.approx([.49, -.23, .031])
    for phase in ("grasp", "lift", "carry", "lower", "release", "withdraw", "recover", "finish"):
        result = candidates(world, phase, False, observation)
        assert result
    assert candidates(world, "lift", False, observation)[0].target[2] == .27


def test_visual_preview_does_not_send_oracle_future_positions():
    world = RobotWorld()
    observation = world.observe()
    observation["perception"] = {"source": "rgbd"}
    options = candidates(world, "approach", True, observation)
    assert all(set(option.preview) == {"source", "safe"} for option in options)
    assert all(option.preview["source"] == "simulator_safety_filter" for option in options)


class FakeVisualObserver:
    """Deliberately biased measurements expose accidental reads of true poses."""
    def __init__(self, world):
        self.world, self.closed = world, False
        self.captures = 0
        self.snapshot = None

    def observe(self):
        result = self.world.observe()
        result["object"][0] += .035
        result["destination"][1] += .02
        result.pop("scene_config", None)
        result["source"] = "test RGB-D estimate"
        self.captures += 1
        metadata = {"capture_id": str(self.captures), "source": "rgbd", "status": "ready",
                    "sim_time": result["sim_seconds"], "latency_ms": 1, "objects": []}
        result["perception"] = metadata
        pixels = encode_png(np.zeros((2, 2, 3), dtype=np.uint8))
        self.snapshot = {"metadata": copy.deepcopy(metadata), "rgb": pixels, "depth": pixels,
                         "depth_display": pixels}
        return result

    def camera_snapshot(self):
        return copy.deepcopy(self.snapshot)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_camera(monkeypatch):
    monkeypatch.setattr("embodied_jev.perception.VisualObserver", FakeVisualObserver)


def test_runtime_uses_estimates_in_actions_inputs_history_and_frames(fake_camera):
    session = Session(observation_mode="rgbd", preview=False, speed=0)
    expected = np.asarray(session.observation["object"]) + [0, 0, .14]
    seen = []
    original = session.policy.choose

    def choose(observation, *args):
        seen.append(copy.deepcopy(observation))
        return original(observation, *args)

    session.policy.choose = choose
    try:
        session.start(single_step=True)
        deadline = time.monotonic() + 15
        while session.status == "running" and time.monotonic() < deadline:
            time.sleep(.01)
        assert session.status == "paused", session.message
        assert session.history[0]["action"]["target"] == pytest.approx(expected)
        assert all(item["source"] == "test RGB-D estimate" for item in seen)
        assert all(frame["observation"]["source"] == "test RGB-D estimate" for frame in session.frames)
        assert session.history[0]["before"]["source"] == "test RGB-D estimate"
        assert session.history[0]["after"]["source"] == "test RGB-D estimate"
        compact = compact_observation(seen[0])
        assert compact["perception"]["source"] == "rgbd"
        assert "scene_config" not in compact
        exported = session.export()
        assert exported["observation_mode"] == "rgbd"
        assert exported["evaluation_source"] == "privileged simulator physical success conditions"
        assert exported["perception_history"]
    finally:
        session.stop()
        session.worker.join(3)
    assert session.observer.closed


def test_camera_routes_are_cached_and_bound_to_episode_and_capture(fake_camera):
    app = create_app()
    with TestClient(app) as client:
        initial = client.get("/api/state").json()
        assert client.get("/api/perception", params={"episode_id": initial["id"]}).status_code == 404
        response = client.post("/api/reset", json={"observation_mode": "rgbd"})
        assert response.status_code == 200
        current = response.json()
        camera = app.state.session.observer
        captures = camera.captures
        query = {"episode_id": current["id"], "capture_id": current["perception"]["capture_id"]}
        assert client.get("/api/perception", params=query).json()["source"] == "rgbd"
        for kind in ("rgb", "depth"):
            image = client.get(f"/api/perception/{kind}.png", params=query)
            assert image.status_code == 200
            assert image.headers["content-type"] == "image/png"
            assert image.headers["cache-control"] == "no-store"
            assert image.content.startswith(b"\x89PNG")
        assert camera.captures == captures
        assert client.get("/api/perception/rgb.png", params={**query, "capture_id": "old"}).status_code == 409
        assert client.get("/api/perception", params={"episode_id": initial["id"]}).status_code == 409
        client.post("/api/reset", json={})
        assert camera.closed
        assert client.get("/api/perception/rgb.png", params=query).status_code == 409


def test_invalid_observation_mode_preserves_existing_episode():
    with TestClient(create_app()) as client:
        before = client.get("/api/state").json()["id"]
        assert client.post("/api/reset", json={"observation_mode": "unknown"}).status_code == 422
        assert client.get("/api/state").json()["id"] == before


def test_missing_perception_stops_without_model_call_or_truth_fallback(fake_camera):
    from embodied_jev.perception import PerceptionUnavailable
    session = Session(observation_mode="rgbd", preview=False, speed=0)

    def missing():
        session.observer.snapshot["metadata"].update(capture_id="missing", status="unavailable")
        raise PerceptionUnavailable("目标被遮挡且已超时")

    session.observer.observe = missing
    session.start()
    session.worker.join(3)
    assert session.status == "error"
    assert session.cycles == session.policy.calls == 0
    assert not session.history
    assert session.snapshot()["perception"]["status"] == "unavailable"
    assert session.export()["perception_history"][-1]["status"] == "unavailable"
    assert session.observer.closed
