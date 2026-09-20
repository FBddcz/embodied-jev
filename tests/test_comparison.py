from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from embodied_jev.comparison import Comparison, resolve_lanes
from embodied_jev.policies import DecisionPolicy
from embodied_jev.server import create_app


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.01)
    assert predicate()


def baseline_lanes(count=2):
    return resolve_lanes([{"provider": "baseline"}] * count, {})


def cleanup(comparison):
    comparison.stop()
    if comparison.worker is not None:
        comparison.worker.join(5)
    for lane in comparison.lanes:
        if lane["session"].worker is not None:
            lane["session"].worker.join(5)


@pytest.mark.parametrize("mode", ["sequential", "parallel"])
def test_real_baseline_lanes_complete_same_task_and_seed(mode):
    comparison = Comparison(baseline_lanes(), task="transfer", seed=0, preview=False, threshold=0, speed=0, mode=mode)
    try:
        initial = comparison.snapshot()
        assert initial["status"] == "idle" and initial["max_parallel"] == (1 if mode == "sequential" else 2)
        assert {lane["status"] for lane in initial["lanes"]} == {"queued"}
        assert len({lane["session"]["id"] for lane in initial["lanes"]}) == 2
        assert initial["lanes"][0]["session"]["frame"]["qpos"] == initial["lanes"][1]["session"]["frame"]["qpos"]
        comparison.start()
        worker = comparison.worker
        comparison.start()
        assert comparison.worker is worker
        wait_until(lambda: comparison.snapshot()["status"] == "done")
        final = comparison.snapshot()
        for lane in final["lanes"]:
            assert lane["status"] == "done" and lane["session"]["status"] == "completed"
            assert lane["session"]["frame"]["observation"]["success"]
            assert lane["session"]["cycles"] == 8
            assert lane["session"]["model_calls"] == 0
        exported = comparison.export()
        assert len(exported["lanes"]) == 2
        assert exported["lanes"][0]["episode"]["frames"] == exported["lanes"][1]["episode"]["frames"]
    finally:
        cleanup(comparison)


def test_replay_aligns_simulation_time_and_never_uses_future_outcomes():
    comparison = Comparison(baseline_lanes(), preview=False, threshold=0, speed=0)
    try:
        comparison.start()
        wait_until(lambda: comparison.snapshot()["status"] == "done")
        initial = comparison.replay(0)
        assert all(not lane["frame"]["observation"]["success"] and lane["decision"] is None for lane in initial["lanes"])
        session = comparison.lanes[0]["session"]
        origin = session.frames[0]["time"]
        action_time = session.frames[1]["time"] - origin
        during = comparison.replay(action_time)
        for lane in during["lanes"]:
            assert lane["frame_time"] == pytest.approx(action_time)
            assert lane["frame"]["cycle"] == lane["decision"]["cycle"] == 1
            assert lane["decision"]["phase"] == "approach"
            assert lane["decision"]["completed"] is False and lane["decision"]["after"] is None
            assert "wall_seconds" not in lane and "model_calls" not in lane
        end = comparison.replay(comparison.snapshot()["replay"]["max_time"])
        assert all(lane["frame"]["observation"]["success"] for lane in end["lanes"])
        assert all(lane["decision"]["completed"] for lane in end["lanes"])
        assert all(lane["clamped"] for lane in comparison.replay(1000)["lanes"])
        with pytest.raises(ValueError):
            comparison.replay(float("nan"))
    finally:
        cleanup(comparison)


@pytest.mark.parametrize("mode,expected_active", [("sequential", 1), ("parallel", 2)])
def test_scheduler_bounds_active_lanes_and_pause_preserves_workers(monkeypatch, mode, expected_active):
    original = DecisionPolicy.choose
    release, entered = threading.Event(), threading.Event()
    seen = set()
    lock = threading.Lock()

    def delayed(self, *args):
        with lock:
            first = id(self) not in seen
            seen.add(id(self))
            if len(seen) == expected_active:
                entered.set()
        if first:
            assert release.wait(5)
        return original(self, *args)

    monkeypatch.setattr(DecisionPolicy, "choose", delayed)
    comparison = Comparison(baseline_lanes(3), preview=False, max_cycles=1, speed=0, mode=mode)
    try:
        comparison.start()
        assert entered.wait(5)
        snapshot = comparison.snapshot()
        assert sum(lane["status"] == "running" for lane in snapshot["lanes"]) == expected_active
        assert sum(lane["status"] == "queued" for lane in snapshot["lanes"]) == 3 - expected_active
        workers = [lane["session"].worker for lane in comparison.lanes]
        comparison.pause()
        assert comparison.snapshot()["status"] == "paused"
        release.set()
        time.sleep(.05)
        assert all(lane["session"].cycles == 0 for lane in comparison.lanes)
        comparison.start()
        for lane, previous in zip(comparison.lanes, workers):
            if previous is not None:
                assert lane["session"].worker is previous
        wait_until(lambda: comparison.snapshot()["status"] == "done")
        assert len(seen) == 3
        assert all(lane["session"].cycles == 1 for lane in comparison.lanes)
    finally:
        release.set()
        cleanup(comparison)


def test_uncertain_lane_finishes_without_rule_fallback_or_blocking_queue(monkeypatch):
    original = DecisionPolicy.choose
    comparison = Comparison(baseline_lanes(), preview=False, max_cycles=1, speed=0, threshold=.55)
    first = comparison.lanes[0]["session"].policy

    def abstain(self, *args):
        if self is first:
            return {"choice": "approach", "selected_probability": .1, "model_call": True}
        return original(self, *args)

    monkeypatch.setattr(DecisionPolicy, "choose", abstain)
    try:
        comparison.start()
        wait_until(lambda: comparison.snapshot()["status"] == "done")
        assert comparison.lanes[0]["session"].status == "uncertain"
        assert comparison.lanes[0]["session"].cycles == 0
        assert comparison.lanes[1]["session"].status == "exhausted"
        assert comparison.lanes[1]["session"].cycles == 1
    finally:
        cleanup(comparison)


def test_same_provider_models_use_isolated_configuration_and_exports_redact_secrets(monkeypatch):
    requests = []

    def post(url, **kwargs):
        requests.append(kwargs)
        payload = kwargs["json"]
        decision = json.loads(payload["messages"][1]["content"])["decision"]
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "model": payload["model"], "choices": [{"message": {"content": json.dumps({"choice": next(iter(decision["criteria"]))})}}]})

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    connections = {"chat": {"url": "https://example.invalid/v1/chat/completions", "model": "configured", "key": "comparison-private-key"}}
    lanes = resolve_lanes([{"provider": "chat", "model": "model-a"}, {"provider": "chat", "model": "model-b"}], connections)
    comparison = Comparison(lanes, preview=False, max_cycles=1, threshold=0, speed=0, mode="parallel")
    try:
        assert connections["chat"]["model"] == "configured"
        comparison.start()
        wait_until(lambda: comparison.snapshot()["status"] == "done")
        assert {request["json"]["model"] for request in requests} == {"model-a", "model-b"}
        assert all(request["headers"] == {"Authorization": "Bearer comparison-private-key"} for request in requests)
        # The first phase is a singleton; only the action selection calls its API.
        assert all(lane["session"].policy.calls == 1 for lane in comparison.lanes)
        for value in (comparison.snapshot(), comparison.export(), comparison.replay(0)):
            assert "comparison-private-key" not in json.dumps(value)
    finally:
        cleanup(comparison)


def test_comparison_routes_leave_single_workbench_untouched():
    app = create_app()
    with TestClient(app) as client:
        main = app.state.session
        assert client.get("/api/comparison").json()["status"] == "empty"
        payload = {"lanes": [{"provider": "baseline"}, {"provider": "baseline"}], "seed": 2}
        response = client.post("/api/comparison", json=payload)
        assert response.status_code == 200
        created = response.json()
        comparison_id = created["id"]
        assert created["mode"] == "sequential"
        for lane in created["lanes"]:
            scene = client.get(f"/api/comparison/scene/{lane['id']}", params={"comparison_id": comparison_id})
            assert scene.status_code == 200 and scene.json()["seed"] == 2
        replay = client.get("/api/comparison/replay", params={"time": 0, "comparison_id": comparison_id})
        assert replay.status_code == 200 and len(replay.json()["lanes"]) == 2
        exported = client.get("/api/comparison/export", params={"comparison_id": comparison_id})
        assert exported.status_code == 200 and exported.json()["format"] == "embodied-jev-comparison-v1"
        assert comparison_id in exported.headers["Content-Disposition"]
        assert client.post("/api/comparison/control/stop", json={"comparison_id": comparison_id}).status_code == 200
        assert app.state.session is main and main.status == "idle" and main.cycles == 0


@pytest.mark.parametrize("lanes", [
    [{"provider": "baseline"}], [{"provider": "baseline"}] * 4,
    [{"provider": "baseline"}, {"provider": "chat"}],
    [{"provider": "baseline", "model": "pretend-model"}, {"provider": "baseline"}],
    [{"provider": "baseline", "api_key": "unaccepted-input"}, {"provider": "baseline"}],
])
def test_invalid_lane_configuration_is_rejected_before_any_world_replacement(monkeypatch, lanes):
    monkeypatch.delenv("EMBODIED_API_BASE", raising=False)
    monkeypatch.delenv("EMBODIED_API_MODEL", raising=False)
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/api/comparison", json={"lanes": lanes})
        assert response.status_code == 422
        assert app.state.comparison is None and app.state.session.status == "idle"


def test_minicpm_uses_fixed_weights_and_exposes_serialization_note(monkeypatch):
    monkeypatch.setenv("EMBODIED_MINICPM", "1")
    app = create_app()
    with TestClient(app) as client:
        payload = {"mode": "parallel", "lanes": [{"provider": "minicpm"}, {"provider": "baseline"}]}
        response = client.post("/api/comparison", json=payload)
        assert response.status_code == 200
        assert any("MiniCPM" in note and "串行" in note for note in response.json()["notes"])
        invalid = {**payload, "expected_comparison_id": response.json()["id"],
                   "lanes": [{"provider": "minicpm", "model": "different-weight"}, {"provider": "baseline"}]}
        assert client.post("/api/comparison", json=invalid).status_code == 422
        assert app.state.comparison.status == "idle"


@pytest.mark.parametrize("action", ["start", "pause", "stop"])
def test_stale_comparison_controls_cannot_change_replacement(action):
    with TestClient(create_app()) as client:
        payload = {"lanes": [{"provider": "baseline"}, {"provider": "baseline"}]}
        old = client.post("/api/comparison", json=payload).json()["id"]
        assert client.post("/api/comparison", json=payload).status_code == 409
        new = client.post("/api/comparison", json={**payload, "expected_comparison_id": old}).json()["id"]
        assert client.post(f"/api/comparison/control/{action}", json={"comparison_id": old}).status_code == 409
        snapshot = client.get("/api/comparison").json()
        assert snapshot["id"] == new and snapshot["status"] == "idle"
        assert client.get("/api/comparison/replay", params={"comparison_id": old, "time": 0}).status_code == 409


def test_rebuild_cancels_delayed_old_outputs_and_bounds_retired_workers(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = DecisionPolicy.choose

    def delayed(self, *args):
        entered.set()
        assert release.wait(5)
        return original(self, *args)

    monkeypatch.setattr(DecisionPolicy, "choose", delayed)
    app = create_app()
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        payload = {"lanes": [{"provider": "baseline"}, {"provider": "baseline"}], "preview": False}
        old_id = client.post("/api/comparison", json=payload).json()["id"]
        previous = app.state.comparison
        client.post("/api/comparison/control/start", json={"comparison_id": old_id})
        try:
            assert entered.wait(5)
            response = pool.submit(client.post, "/api/comparison", json={**payload, "expected_comparison_id": old_id}).result(timeout=2)
            assert response.status_code == 409 and "等待上一轮请求" in response.json()["detail"]
            assert previous.status == "stopped" and app.state.comparison is previous
            assert previous.lanes[1]["session"].worker is None
        finally:
            release.set()
        wait_until(lambda: not previous.has_live_workers())
        new = client.post("/api/comparison", json={**payload, "expected_comparison_id": old_id})
        assert new.status_code == 200 and new.json()["id"] != old_id
        assert all(lane["session"].cycles == 0 for lane in previous.lanes)
        assert all(lane["session"].cycles == 0 for lane in app.state.comparison.lanes)


def test_competing_comparison_creation_accepts_only_one_expected_id():
    app = create_app()
    barrier = threading.Barrier(2)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        payload = {"lanes": [{"provider": "baseline"}, {"provider": "baseline"}]}
        old_id = client.post("/api/comparison", json=payload).json()["id"]

        def create(seed):
            barrier.wait(timeout=5)
            return client.post("/api/comparison", json={**payload, "seed": seed, "expected_comparison_id": old_id})

        responses = [future.result(timeout=5) for future in (pool.submit(create, 1), pool.submit(create, 2))]
        assert sorted(response.status_code for response in responses) == [200, 409]
