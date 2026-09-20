from concurrent.futures import ThreadPoolExecutor
import json
import logging
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from embodied_jev.policies import DecisionPolicy
from embodied_jev.server import create_app


def _connection(provider="chat"):
    return {"provider": provider, "url": "https://example.invalid/v1" if provider == "chat" else "http://127.0.0.1:9123/decide",
            "model": "test-model", "api_key": "concurrency-test-secret"}


def _response(url, **kwargs):
    payload = kwargs["json"]
    if "questions" in payload:
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "model": "test-model", "answers": {"action": {"choice": "ready", "probabilities": {"ready": .9, "hold": .1}}}})
    content = json.loads(next(m["content"] for m in payload["messages"] if m["role"] == "user"))
    choice = next(iter(content["decision"]["criteria"]))
    return httpx.Response(200, request=httpx.Request("POST", url), json={
        "model": "test-model", "choices": [{"message": {"content": json.dumps({"choice": choice})}}]})


@pytest.mark.parametrize("action", ["start", "step", "pause", "stop"])
def test_stale_control_cannot_change_replacement_episode(action):
    with TestClient(create_app()) as client:
        old_id = client.get("/api/state").json()["id"]
        new = client.post("/api/reset", json={"seed": 1, "expected_episode_id": old_id}).json()
        assert new["id"] != old_id
        response = client.post(f"/api/control/{action}", json={"episode_id": old_id})
        assert response.status_code == 409 and "刷新" in response.json()["detail"]
        state = client.get("/api/state").json()
        assert state["id"] == new["id"] and state["status"] == "idle" and state["cycles"] == 0
        assert client.post("/api/control/stop", json={"episode_id": new["id"]}).json()["status"] == "stopped"


def test_competing_resets_only_construct_one_replacement(monkeypatch):
    app = create_app()
    original_session = type(app.state.session)
    constructed = []
    barrier = threading.Barrier(2)

    def construct(**kwargs):
        constructed.append(kwargs)
        return original_session(**kwargs)

    monkeypatch.setattr("embodied_jev.server.Session", construct)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        old_id = client.get("/api/state").json()["id"]

        def reset(seed):
            barrier.wait(timeout=5)
            return client.post("/api/reset", json={"seed": seed, "expected_episode_id": old_id})

        pending = [pool.submit(reset, seed) for seed in (1, 2)]
        responses = [future.result(timeout=5) for future in pending]
        assert sorted(response.status_code for response in responses) == [200, 409]
        assert len(constructed) == 1 and "expected_episode_id" not in constructed[0]
        winner = next(response.json() for response in responses if response.status_code == 200)
        assert client.get("/api/state").json()["id"] == winner["id"]


def test_legacy_controls_without_episode_id_remain_compatible():
    with TestClient(create_app()) as client:
        assert client.post("/api/reset", json={"seed": 2}).status_code == 200
        assert client.post("/api/control/stop", json={}).json()["status"] == "stopped"


@pytest.mark.parametrize("operation", ["stop", "reset"])
def test_stop_and_reset_do_not_wait_for_old_http_inference(monkeypatch, operation):
    entered, release = threading.Event(), threading.Event()

    def delayed(url, **kwargs):
        entered.set()
        assert release.wait(5)
        return _response(url, **kwargs)

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(delayed))
    app = create_app()
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        client.post("/api/connections", json=_connection())
        client.post("/api/reset", json={"provider": "chat", "preview": False})
        old = app.state.session
        initial = old.world.data.qpos.copy()
        client.post("/api/control/start", json={"episode_id": old.id})
        try:
            assert entered.wait(5)
            path = "/api/reset" if operation == "reset" else "/api/control/stop"
            payload = {"expected_episode_id": old.id} if operation == "reset" else {"episode_id": old.id}
            response = pool.submit(client.post, path, json=payload).result(timeout=2)
            assert response.status_code == 200
            assert not release.is_set() and old.worker.is_alive()
            if operation == "reset":
                replacement = response.json()
                assert replacement["id"] != old.id and replacement["status"] == "idle"
            else:
                assert response.json()["status"] == "stopped"
        finally:
            release.set()
            old.worker.join(5)
        assert not old.worker.is_alive() and old.cycles == 0
        assert (old.world.data.qpos == initial).all()


@pytest.mark.parametrize("path,method", [
    ("/api/state", "snapshot"), ("/api/scene", "scene"),
    ("/api/replay/0", "replay_frame"), ("/api/export", "export"),
])
def test_reads_do_not_hold_global_control_lock(monkeypatch, path, method):
    app = create_app()
    old = app.state.session
    entered, release = threading.Event(), threading.Event()
    owner = old.world if method == "scene" else old
    original = getattr(owner, method)

    def slow_read(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(owner, method, slow_read)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(client.get, path)
        try:
            assert entered.wait(5)
            # A blocked view of this world must not block API settings or reads.
            saved = pool.submit(client.post, "/api/connections", json=_connection()).result(timeout=2)
            assert saved.status_code == 200 and app.state.session is old
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200


def test_export_keeps_one_episode_when_reset_happens_during_read(monkeypatch):
    app = create_app()
    old = app.state.session
    entered, release = threading.Event(), threading.Event()
    original = old.export

    def delayed_export():
        entered.set()
        assert release.wait(5)
        return original()

    monkeypatch.setattr(old, "export", delayed_export)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(client.get, "/api/export")
        try:
            assert entered.wait(5)
            new = pool.submit(client.post, "/api/reset", json={"expected_episode_id": old.id}).result(timeout=2)
            assert new.status_code == 200 and new.json()["id"] != old.id
        finally:
            release.set()
        exported = pending.result(timeout=5)
        assert exported.json()["id"] == old.id
        assert f"embodied-jev-{old.id}.json" in exported.headers["Content-Disposition"]
        assert client.get("/api/state").json()["id"] == new.json()["id"]


def test_connection_tests_have_bounded_nonblocking_concurrency(monkeypatch):
    entered = threading.Barrier(3)
    release = threading.Event()
    count = 0
    count_lock = threading.Lock()

    def delayed(url, **kwargs):
        nonlocal count
        with count_lock:
            count += 1
            ordinal = count
        if ordinal <= 2:
            entered.wait(timeout=5)
            assert release.wait(5)
        return _response(url, **kwargs)

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(delayed))
    app = create_app()
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=3) as pool:
        for provider in ("chat", "local"):
            client.post("/api/connections", json=_connection(provider))
        pending = [pool.submit(client.post, f"/api/connections/{provider}/test") for provider in ("chat", "local")]
        try:
            entered.wait(timeout=5)
            ids_before = dict(app.state.connection_test_ids)
            excess = pool.submit(client.post, "/api/connections/chat/test").result(timeout=2)
            assert excess.status_code == 429 and excess.headers["Retry-After"] == "1"
            assert "两个连接测试" in excess.json()["detail"]
            assert app.state.connection_test_ids == ids_before
            assert count == 2
            assert client.get("/api/state").status_code == 200
        finally:
            release.set()
        assert all(future.result(timeout=5).status_code == 200 for future in pending)
        assert client.post("/api/connections/chat/test").status_code == 200
        assert count == 3


@pytest.mark.parametrize("configured", [True, False])
def test_failed_connection_tests_release_capacity(monkeypatch, configured):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    def timeout(url, **kwargs):
        raise httpx.ReadTimeout("concurrency-test-secret private-response-body", request=httpx.Request("POST", url))

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(timeout))
    with TestClient(create_app()) as client:
        if configured:
            client.post("/api/connections", json=_connection())
        provider = "chat" if configured else "jev"
        for _ in range(3):
            assert client.post(f"/api/connections/{provider}/test").status_code == 502


def test_server_event_logs_exclude_keys_and_provider_responses(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="embodied_jev.server")

    def rejected(url, **kwargs):
        return httpx.Response(401, request=httpx.Request("POST", url, headers={"Authorization": "Bearer concurrency-test-secret"}),
                              json={"error": "private-response-body concurrency-test-secret"})

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(rejected))
    with TestClient(create_app()) as client:
        client.post("/api/connections", json=_connection())
        assert client.post("/api/connections/chat/test").status_code == 502
        client.post("/api/reset", json={})
    records = [record.__dict__ for record in caplog.records if record.name == "embodied_jev.server"]
    assert {record["event"] for record in records} >= {"connection_test_started", "connection_test_finished", "episode_reset"}
    assert "concurrency-test-secret" not in repr(records)
    assert "private-response-body" not in repr(records)
    assert "Authorization" not in repr(records)
