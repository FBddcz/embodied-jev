from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from embodied_jev.policies import DecisionPolicy
from embodied_jev.server import create_app


def profile_payload(provider="chat", name="测试平台", **overrides):
    urls = {"jev": "https://api.typesafe.ai/v1/systemone", "claude": "https://api.anthropic.com/v1",
            "chat": "https://platform-a.invalid/v1", "local": "http://127.0.0.1:9001/decide"}
    return {"name": name, "provider": provider, "url": urls[provider], "model": "profile-model",
            "api_key": "profile-private-key", **overrides}


def probe_payload(provider="baseline", **overrides):
    return {"provider": provider, "observation": {"relative_geometry": {"distance_m": .2}, "gripper": "open"},
            "question": "根据输入选择候选。", "options": {"move": "向目标移动", "hold": "保持不动"}, **overrides}


def provider_response(url, payload, choice="hold"):
    if "questions" in payload:
        body = {"model": payload["model"], "answers": {"action": {"choice": choice, "probabilities": {"move": .1, "hold": .9}}}}
    elif "tools" in payload:
        body = {"model": payload["model"], "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "name": "select_action", "input": {"choice": choice}}]}
    else:
        body = {"model": payload["model"], "choices": [{"message": {"content": json.dumps({"choice": choice})}}]}
    return httpx.Response(200, request=httpx.Request("POST", url), json=body)


def test_profiles_are_memory_only_redacted_and_saved_without_calls(monkeypatch):
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(lambda *args, **kwargs: pytest.fail("Saving must not call APIs")))
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/model-profiles").json()["profiles"] == []
        first = client.post("/api/model-profiles", json=profile_payload()).json()
        second = client.post("/api/model-profiles", json=profile_payload(name="第二平台", url="https://platform-b.invalid/v1", api_key="second-private-key")).json()
        assert first["id"] != second["id"] and first["url"].endswith("/chat/completions")
        assert first["key_configured"] and second["key_configured"]
        assert app.state.connections == {}
        response = client.get("/api/model-profiles")
        assert len(response.json()["profiles"]) == 2
        assert "profile-private-key" not in response.text and "second-private-key" not in response.text
        updated = client.post("/api/model-profiles", json=profile_payload(id=first["id"], api_key="", name="改名")).json()
        assert updated["key_configured"] and app.state.model_profiles[first["id"]]["key"] == "profile-private-key"
        changed = client.post("/api/model-profiles", json=profile_payload(id=first["id"], url="https://other.invalid/v1", api_key="")).json()
        assert changed["id"] == first["id"] and not changed["key_configured"]
    with TestClient(create_app()) as client:
        assert client.get("/api/model-profiles").json()["profiles"] == []


@pytest.mark.parametrize("provider,suffix", [("jev", "/v1/systemone"), ("claude", "/v1/messages"), ("chat", "/v1/chat/completions"), ("local", "/decide")])
def test_profiles_share_connection_validation_and_endpoint_normalization(provider, suffix):
    with TestClient(create_app()) as client:
        response = client.post("/api/model-profiles", json=profile_payload(provider))
        assert response.status_code == 200 and response.json()["url"].endswith(suffix)
        assert "profile-private-key" not in response.text
        invalid = client.post("/api/model-profiles", json=profile_payload(provider, url="https://name:profile-private-key@example.invalid"))
        assert invalid.status_code == 422 and "profile-private-key" not in invalid.text
        assert client.post("/api/model-profiles", json=profile_payload(provider, id="missing-profile")).status_code == 404


def test_single_workbench_selects_profile_without_changing_default_connection():
    app = create_app()
    with TestClient(app) as client:
        profile = client.post("/api/model-profiles", json=profile_payload()).json()
        selected = client.post("/api/reset", json={"provider": "chat", "profile_id": profile["id"]})
        assert selected.status_code == 200
        assert selected.json()["profile_id"] == profile["id"]
        assert app.state.session.policy.connection["url"] == profile["url"]
        assert app.state.session.policy.connection["key"] == "profile-private-key"
        assert app.state.connections == {}
        before = app.state.session.id
        assert client.post("/api/reset", json={"provider": "claude", "profile_id": profile["id"]}).status_code == 422
        assert client.post("/api/reset", json={"provider": "chat", "profile_id": "missing"}).status_code == 422
        assert app.state.session.id == before
        assert "profile-private-key" not in client.get("/api/export").text


def test_comparison_snapshots_independent_platform_keys_and_profile_ids(monkeypatch):
    requests = []

    def post(url, **kwargs):
        payload = kwargs["json"]
        requests.append((url, kwargs["headers"], payload["model"]))
        decision = json.loads(payload["messages"][1]["content"])["decision"]
        return provider_response(url, payload, next(iter(decision["criteria"])))

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    app = create_app()
    with TestClient(app) as client:
        first = client.post("/api/model-profiles", json=profile_payload()).json()
        second = client.post("/api/model-profiles", json=profile_payload(name="第二平台", url="https://platform-b.invalid/v1", api_key="second-private-key")).json()
        created = client.post("/api/comparison", json={"lanes": [
            {"provider": "chat", "profile_id": first["id"]},
            {"provider": "chat", "profile_id": second["id"], "model": "second-model"}],
            "mode": "parallel", "preview": False, "threshold": 0, "max_cycles": 1, "speed": 4}).json()
        assert [lane["profile_id"] for lane in created["lanes"]] == [first["id"], second["id"]]
        client.post("/api/model-profiles", json=profile_payload(id=first["id"], api_key="changed-private-key"))
        client.post("/api/comparison/control/start", json={"comparison_id": created["id"]})
        deadline = time.monotonic() + 5
        while app.state.comparison.snapshot()["status"] != "done" and time.monotonic() < deadline:
            time.sleep(.01)
        assert app.state.comparison.snapshot()["status"] == "done"
        assert sorted(requests) == sorted([
            (first["url"], {"Authorization": "Bearer profile-private-key"}, "profile-model"),
            (second["url"], {"Authorization": "Bearer second-private-key"}, "second-model")])
        exported = client.get("/api/comparison/export", params={"comparison_id": created["id"]})
        assert [lane["episode"]["profile_id"] for lane in exported.json()["lanes"]] == [first["id"], second["id"]]
        assert not any(key in exported.text for key in ("profile-private-key", "second-private-key", "changed-private-key"))


def test_baseline_probe_is_explicitly_fixed_and_never_moves_robot(monkeypatch):
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(lambda *args, **kwargs: pytest.fail("Baseline has no external call")))
    app = create_app()
    with TestClient(app) as client:
        session = app.state.session
        before = session.world.data.qpos.copy()
        result = client.post("/api/decision/probe", json=probe_payload())
        assert result.status_code == 200
        assert result.json()["decision"]["choice"] == "move"
        assert not result.json()["decision"]["model_call"] and result.json()["decision_input"] is None
        assert "固定选择第一个" in result.json()["message"]
        assert app.state.session is session and session.cycles == 0 and (session.world.data.qpos == before).all()


@pytest.mark.parametrize("provider", ["jev", "chat", "local", "claude"])
def test_probe_uses_real_provider_adapter_and_preserves_arbitrary_geometry(monkeypatch, provider):
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        return provider_response(url, kwargs["json"])

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    with TestClient(create_app()) as client:
        profile = client.post("/api/model-profiles", json=profile_payload(provider)).json()
        payload = probe_payload(provider, profile_id=profile["id"], model="probe-model")
        response = client.post("/api/decision/probe", json=payload)
        assert response.status_code == 200 and len(calls) == 1
        result = response.json()
        assert result["decision"]["choice"] == "hold" and result["model"] == "probe-model"
        assert result["decision_input"]["state"]["observation"] == payload["observation"]
        assert result["decision_input"]["decision"]["criteria"] == payload["options"]
        assert result["profile_id"] == profile["id"]
        assert "profile-private-key" not in response.text
        assert client.get("/api/state").json()["cycles"] == 0


@pytest.mark.parametrize("failure", ["unauthorized", "timeout", "invalid"])
def test_probe_errors_are_safe_and_release_capacity(monkeypatch, failure):
    def post(url, **kwargs):
        request = httpx.Request("POST", url)
        if failure == "timeout":
            raise httpx.ReadTimeout("profile-private-key private-response", request=request)
        return httpx.Response(401 if failure == "unauthorized" else 200, request=request,
                              json={"error": "profile-private-key private-response"})

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    with TestClient(create_app()) as client:
        profile = client.post("/api/model-profiles", json=profile_payload()).json()
        for _ in range(3):
            response = client.post("/api/decision/probe", json=probe_payload("chat", profile_id=profile["id"]))
            assert response.status_code == 502
            assert "profile-private-key" not in response.text and "private-response" not in response.text
        assert client.post("/api/decision/probe", json=probe_payload()).status_code == 200


def test_probe_and_connection_tests_share_nonblocking_capacity(monkeypatch):
    entered, release = threading.Barrier(3), threading.Event()

    def post(url, **kwargs):
        entered.wait(timeout=5)
        assert release.wait(5)
        return provider_response(url, kwargs["json"])

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    with TestClient(create_app()) as client, ThreadPoolExecutor(max_workers=3) as pool:
        profile = client.post("/api/model-profiles", json=profile_payload()).json()
        pending = [pool.submit(client.post, "/api/decision/probe", json=probe_payload("chat", profile_id=profile["id"])) for _ in range(2)]
        try:
            entered.wait(timeout=5)
            assert pool.submit(client.post, "/api/connections/chat/test").result(timeout=2).status_code == 429
            assert pool.submit(client.post, "/api/decision/probe", json=probe_payload()).result(timeout=2).status_code == 429
        finally:
            release.set()
        assert all(future.result(timeout=5).status_code == 200 for future in pending)
        assert client.post("/api/decision/probe", json=probe_payload()).status_code == 200


@pytest.mark.parametrize("change", [
    {"options": {"only": "one"}}, {"options": {str(index): "option" for index in range(13)}},
    {"options": {"move": "", "hold": "hold"}}, {"question": " "},
    {"question": "x" * 4001}, {"observation": {"large": "x" * 8192}},
    {"provider": "baseline", "profile_id": "missing"},
])
def test_probe_rejects_invalid_input_before_external_calls(monkeypatch, change):
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(lambda *args, **kwargs: pytest.fail("Invalid input must not make requests")))
    with TestClient(create_app()) as client:
        assert client.post("/api/decision/probe", json=probe_payload(**change)).status_code == 422


def test_scene_and_user_context_are_shared_without_overriding_physics():
    scene = {"name": "自定义入盘", "source_xy": [.44, -.18], "target_xy": [.44, .18]}
    context = {"tcp": [99, 99, 99], "held": True, "goal": "补充说明"}
    with TestClient(create_app()) as client:
        response = client.post("/api/reset", json={"scene_config": scene, "user_context": context})
        assert response.status_code == 200
        result = response.json()
        assert result["scene_config"]["name"] == scene["name"] and result["user_context"] == context
        assert result["frame"]["observation"]["tcp"] != context["tcp"]
        assert result["frame"]["observation"]["held"] is False
        comparison = client.post("/api/comparison", json={"scene_config": scene, "user_context": context,
            "lanes": [{"provider": "baseline"}, {"provider": "baseline"}]}).json()
        assert comparison["config"]["user_context"] == context
        assert all(lane["session"]["scene_config"] == result["scene_config"] for lane in comparison["lanes"])
        assert all(lane["session"]["user_context"] == context for lane in comparison["lanes"])


@pytest.mark.parametrize("path,payload", [
    ("/api/reset", {"scene_config": {"success": True}}),
    ("/api/reset", {"user_context": {"large": "x" * 8192}}),
    ("/api/reset", {"user_context": {"not_finite": float("nan")}}),
    ("/api/decision/probe", probe_payload(observation={"not_finite": float("inf")})),
])
def test_json_size_finite_values_and_scene_fields_are_validated(path, payload):
    with TestClient(create_app()) as client:
        response = client.post(path, content=json.dumps(payload), headers={"Content-Type": "application/json"})
        assert response.status_code == 422
        assert client.get("/api/state").json()["status"] == "idle"


def test_portable_preset_validation_returns_only_normalized_data():
    with TestClient(create_app()) as client:
        payload = {"format": "embodied-jev-preset-v1", "name": "测试预设", "task": "transfer",
                   "scene_config": {"name": "新场景"}, "user_context": {"material": "plastic"}}
        response = client.post("/api/presets/validate", json=payload)
        assert response.status_code == 200 and response.json()["scene_config"]["target_xy"] == [.43, .18]
        assert client.post("/api/presets/validate", json={**payload, "api_key": "not-an-executable-config"}).status_code == 422
        assert client.get("/api/state").json()["cycles"] == 0


@pytest.mark.parametrize("path,payload", [
    ("/api/reset", {"user_context": {"note": "profile-private-key"}}),
    ("/api/comparison", {"lanes": [{"provider": "baseline"}, {"provider": "baseline"}],
                         "user_context": {"note": "profile-private-key"}}),
    ("/api/decision/probe", probe_payload(observation={"note": "profile-private-key"})),
])
def test_accidentally_pasted_saved_keys_are_rejected_before_storage_or_request(monkeypatch, path, payload):
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(lambda *args, **kwargs: pytest.fail("Credentials must not be sent as model input")))
    app = create_app()
    with TestClient(app) as client:
        client.post("/api/model-profiles", json=profile_payload())
        response = client.post(path, json=payload)
        assert response.status_code == 422 and "profile-private-key" not in response.text
        assert app.state.session.user_context == {} and app.state.comparison is None


def test_probe_redacts_another_profile_secret_even_after_it_changes(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def post(url, **kwargs):
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "model": "model-second-private-key", "choices": [{"message": {"content": '{"choice":"hold"}'}}]})

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    with TestClient(create_app()) as client, ThreadPoolExecutor(max_workers=1) as pool:
        first = client.post("/api/model-profiles", json=profile_payload()).json()
        second = client.post("/api/model-profiles", json=profile_payload(name="其他平台", api_key="second-private-key")).json()
        pending = pool.submit(client.post, "/api/decision/probe", json=probe_payload("chat", profile_id=first["id"]))
        try:
            assert entered.wait(5)
            client.post("/api/model-profiles", json=profile_payload(id=second["id"], api_key="changed-second-key"))
        finally:
            release.set()
        response = pending.result(timeout=5)
        assert response.status_code == 200
        assert not any(key in response.text for key in ("profile-private-key", "second-private-key", "changed-second-key"))
        assert "[已隐藏]" in response.text


def test_comparison_export_redacts_secrets_from_profiles_outside_its_lanes():
    app = create_app()
    with TestClient(app) as client:
        other = client.post("/api/model-profiles", json=profile_payload()).json()
        comparison_id = client.post("/api/comparison", json={"lanes": [{"provider": "baseline"}, {"provider": "baseline"}]}).json()["id"]
        # Simulate an upstream metadata echo and later credential rotation.
        app.state.comparison.lanes[0]["session"].policy.model = "model-profile-private-key"
        client.post("/api/model-profiles", json=profile_payload(id=other["id"], api_key="replacement-key"))
        response = client.get("/api/comparison/export", params={"comparison_id": comparison_id})
        assert "profile-private-key" not in response.text and "replacement-key" not in response.text
        assert "profile-private-key" not in json.dumps(app.state.comparison.export())


def test_validation_errors_do_not_expose_saved_key_in_input_or_location():
    with TestClient(create_app()) as client:
        client.post("/api/model-profiles", json=profile_payload())
        response = client.post("/api/decision/probe", json=probe_payload(options={"profile-private-key": [], "hold": "Hold"}))
        assert response.status_code == 422 and "profile-private-key" not in response.text
        assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])
