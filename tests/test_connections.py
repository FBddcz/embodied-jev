import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from embodied_jev.policies import DecisionPolicy, environment_connection
from embodied_jev.server import create_app


def test_connection_save_redacts_key_and_does_not_call_provider(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Saving a connection must not call a provider")
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(fail))
    with TestClient(create_app()) as client:
        payload = {"provider": "chat", "url": "https://example.invalid/v1", "model": "example/model", "api_key": "test-secret-key"}
        response = client.post("/api/connections", json=payload)
        assert response.status_code == 200
        for path in ["/api/connections", "/api/config", "/api/state", "/api/export"]:
            assert "test-secret-key" not in client.get(path).text
        assert "test-secret-key" not in response.text
        saved = client.get("/api/connections").json()["chat"]
        assert saved["url"] == "https://example.invalid/v1/chat/completions"
        assert saved["verification"] == {
            "status": "untested", "checked_at": None, "model": None, "latency_ms": None,
            "message": "尚未测试。保存配置不会验证 API。"}
        assert client.post("/api/reset", json={"provider": "chat", "max_cycles": 1}).status_code == 200
        # A key is never implicitly reused at a different address.
        client.post("/api/connections", json={**payload, "url": "https://other.invalid/v1", "api_key": ""})
        assert not client.get("/api/connections").json()["chat"]["key_configured"]


def test_chat_contract_and_test_button(monkeypatch):
    def post(url, **kwargs):
        payload = kwargs["json"]
        assert url.endswith("/chat/completions")
        assert payload["response_format"] == {"type": "json_object"}
        decision = json.loads(payload["messages"][1]["content"])["decision"]
        choice = next(iter(decision["criteria"]))
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "model": "test-model", "choices": [{"message": {"content": json.dumps({"choice": choice, "probabilities": {"invented": 1}})}}],
            "usage": {"prompt_tokens": 12}})
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    settings = {"url": "https://example.invalid/v1/chat/completions", "key": "test-secret", "model": "test-model"}
    policy = DecisionPolicy("chat", settings)
    result = policy.choose({}, "Choose", {"move": "Move", "hold": "Hold"}, "move", [])
    assert result["choice"] == "move"
    assert result["probabilities"] == {}
    assert result["selected_probability"] is None
    assert policy.calls == 1 and policy.tokens == 12
    with TestClient(create_app()) as client:
        client.post("/api/connections", json={"provider": "chat", "url": "https://example.invalid/v1", "model": "test-model"})
        assert client.post("/api/connections/chat/test", json={}).json()["ok"]
        verification = client.get("/api/connections").json()["chat"]["verification"]
        assert verification["status"] == "passed" and verification["model"] == "test-model"
        assert datetime.fromisoformat(verification["checked_at"]).utcoffset().total_seconds() == 0
        assert verification["latency_ms"] >= 0
        assert client.get("/api/state").json()["cycles"] == 0


def test_bad_connection_and_foreign_origin_rejected():
    with TestClient(create_app()) as client:
        for url in ["file:///tmp/test", "https://user:password@example.invalid", "https://example.invalid?key=secret"]:
            assert client.post("/api/connections", json={"provider": "chat", "url": url, "model": "test"}).status_code == 422
        assert client.post("/api/connections", json={"provider": "chat", "url": "https://example.invalid", "model": "test"},
                           headers={"Origin": "https://other.invalid"}).status_code == 403


def test_official_jev_preset_and_resolved_model(monkeypatch):
    monkeypatch.delenv("TYPESAFE_MODEL", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        assert url == "https://api.typesafe.ai/v1/systemone"
        assert kwargs["headers"] == {"Authorization": "Bearer typesafe-test-secret"}
        assert kwargs["follow_redirects"] is False
        payload = kwargs["json"]
        assert payload["model"] == "jev-latest"
        assert payload["questions"]["action"]["type"] == "choice"
        keys = list(payload["questions"]["action"]["criteria"])
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "model": "jev-1.13.0", "answers": {"action": {
                "choice": keys[0], "probabilities": {keys[0]: .8, keys[1]: .2}, "confidence": .4}},
            "usage": {"input_tokens": 20, "output_tokens": 2}})
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    with TestClient(create_app()) as client:
        preset = client.get("/api/connections").json()["jev"]
        assert preset["model"] == "jev-latest"
        assert not preset["key_configured"]
        payload = {"provider": "jev", "url": preset["url"], "model": preset["model"]}
        assert client.post("/api/connections", json=payload).status_code == 422
        payload["api_key"] = "typesafe-test-secret"
        assert client.post("/api/connections", json={**payload, "url": "https://other.invalid"}).status_code == 422
        assert client.post("/api/connections", json=payload).status_code == 200
        assert calls == []
        result = client.post("/api/connections/jev/test", json={}).json()
        assert result["ok"] and result["model"] == "jev-1.13.0"
        assert len(calls) == 1
        assert client.get("/api/state").json()["cycles"] == 0
        for path in ("/api/connections", "/api/config", "/api/state", "/api/export"):
            assert "typesafe-test-secret" not in client.get(path).text
    settings = {**environment_connection("jev"), "key": "typesafe-test-secret"}
    policy = DecisionPolicy("jev", settings)
    answer = policy.choose({}, "Choose", {"move": "Move", "hold": "Hold"}, "move", [])
    assert answer["model"] == "jev-1.13.0"
    assert answer["selected_probability"] == .8
    assert answer["provider_confidence"] == .4
    assert policy.tokens == 20 and policy.output_tokens == 2


def test_claude_native_contract_and_key_redaction(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        assert url == "https://api.anthropic.com/v1/messages"
        assert kwargs["headers"] == {"x-api-key": "claude-test-secret", "anthropic-version": "2023-06-01"}
        payload = kwargs["json"]
        assert "response_format" not in payload and payload["max_tokens"] > 0
        assert payload["tool_choice"]["name"] == "select_action"
        assert payload["tool_choice"]["disable_parallel_tool_use"]
        keys = payload["tools"][0]["input_schema"]["properties"]["choice"]["enum"]
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "model": "claude-test", "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "name": "select_action", "input": {"choice": keys[0]}}],
            "usage": {"input_tokens": 15, "output_tokens": 6}})
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    with TestClient(create_app()) as client:
        settings = {"provider": "claude", "url": "https://api.anthropic.com/v1", "model": "claude-test", "api_key": "claude-test-secret"}
        assert client.post("/api/connections", json=settings).status_code == 200
        assert calls == []
        for path in ("/api/connections", "/api/config", "/api/state", "/api/export"):
            assert "claude-test-secret" not in client.get(path).text
        assert client.post("/api/connections/claude/test", json={}).json()["ok"]
        assert client.post("/api/reset", json={"provider": "claude"}).status_code == 200
    policy = DecisionPolicy("claude", {"url": settings["url"] + "/messages", "key": settings["api_key"], "model": "claude-test"})
    answer = policy.choose({}, "Choose", {"move": "Move", "hold": "Hold"}, "move", [])
    assert answer["choice"] == "move" and answer["probabilities"] == {}
    assert answer["selected_probability"] is None
    assert policy.tokens == 15 and policy.output_tokens == 6


@pytest.mark.parametrize("content,stop_reason", [
    ([{"type": "text", "text": '{"choice":"move"}'}], "end_turn"),
    ([{"type": "tool_use", "name": "select_action", "input": {"choice": "unknown"}}], "tool_use"),
    ([{"type": "tool_use", "name": "select_action", "input": {"choice": "move"}}], "max_tokens"),
    ([{"type": "tool_use", "name": "select_action", "input": {"choice": "move"}}] * 2, "tool_use"),
])
def test_claude_rejects_invalid_or_truncated_decisions(monkeypatch, content, stop_reason):
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(lambda url, **kwargs: httpx.Response(200,
        request=httpx.Request("POST", url), json={"content": content, "stop_reason": stop_reason})))
    policy = DecisionPolicy("claude", {"url": "https://example.invalid/v1/messages", "key": "test", "model": "test"})
    with pytest.raises(ValueError):
        policy.choose({}, "Choose", {"move": "Move", "hold": "Hold"}, "move", [])


def _ready_response(url, **kwargs):
    return httpx.Response(200, request=httpx.Request("POST", url), json={
        "model": kwargs["json"]["model"],
        "choices": [{"message": {"content": '{"choice":"ready"}'}}]})


@pytest.mark.parametrize("change", [
    {"url": "https://other.invalid/v1"}, {"model": "new-model"},
    {"api_key": "new-test-secret"}, {"json_mode": False},
])
def test_connection_verification_expires_only_when_configuration_changes(monkeypatch, change):
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(_ready_response))
    payload = {"provider": "chat", "url": "https://example.invalid/v1", "model": "test-model", "api_key": "test-secret"}
    with TestClient(create_app()) as client:
        client.post("/api/connections", json=payload)
        assert client.post("/api/connections/chat/test").status_code == 200
        previous = client.get("/api/connections").json()["chat"]["verification"]
        # An empty password input keeps the existing key at the same endpoint.
        result = client.post("/api/connections", json={**payload, "api_key": ""})
        assert result.json()["verification"] == previous
        assert client.post("/api/connections", json={**payload, **change}).json()["verification"]["status"] == "untested"
        connections = client.get("/api/connections").json()
        assert connections["chat"]["verification"]["checked_at"] is None
        assert next(p for p in client.get("/api/config").json()["providers"] if p["id"] == "chat")["ready"]


@pytest.mark.parametrize("restore_old_configuration", [False, True])
def test_inflight_test_cannot_verify_replaced_configuration(monkeypatch, restore_old_configuration):
    entered, release = threading.Event(), threading.Event()
    call_count = 0

    def post(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            entered.set()
            assert release.wait(5), "Timed out waiting for configuration replacement"
        return _ready_response(url, **kwargs)

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    payload = {"provider": "chat", "url": "https://example.invalid/v1", "model": "old-model"}
    with TestClient(create_app()) as client, ThreadPoolExecutor(max_workers=1) as pool:
        client.post("/api/connections", json=payload)
        pending = pool.submit(client.post, "/api/connections/chat/test")
        try:
            assert entered.wait(5)
            client.post("/api/connections", json={**payload, "model": "new-model"})
            if restore_old_configuration:
                client.post("/api/connections", json=payload)
            assert client.post("/api/connections/chat/test").status_code == 200
            current = client.get("/api/connections").json()["chat"]["verification"]
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 409
        assert client.get("/api/connections").json()["chat"]["verification"] == current


def test_newer_failed_test_is_not_overwritten_by_older_success(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    call_count = 0

    def post(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            entered.set()
            assert release.wait(5)
            return _ready_response(url, **kwargs)
        return httpx.Response(429, request=httpx.Request("POST", url), json={"error": "secret-provider-body"})

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    with TestClient(create_app()) as client, ThreadPoolExecutor(max_workers=1) as pool:
        client.post("/api/connections", json={"provider": "chat", "url": "https://example.invalid/v1", "model": "test-model"})
        pending = pool.submit(client.post, "/api/connections/chat/test")
        try:
            assert entered.wait(5)
            assert client.post("/api/connections/chat/test").status_code == 502
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 409
        assert client.get("/api/connections").json()["chat"]["verification"]["status"] == "failed"


def test_environment_connection_verification_tracks_effective_configuration(monkeypatch):
    monkeypatch.setenv("EMBODIED_API_BASE", "https://example.invalid/v1")
    monkeypatch.setenv("EMBODIED_API_MODEL", "test-model")
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(_ready_response))
    with TestClient(create_app()) as client:
        assert client.post("/api/connections/chat/test").status_code == 200
        monkeypatch.setenv("EMBODIED_API_MODEL", "changed-model")
        assert client.get("/api/connections").json()["chat"]["verification"]["status"] == "untested"


def test_missing_connection_is_reported_without_external_call(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(lambda *args, **kwargs: pytest.fail("Unconfigured provider must not be called")))
    with TestClient(create_app()) as client:
        response = client.post("/api/connections/jev/test")
        assert response.status_code == 502
        assert "先填写并保存" in response.json()["detail"]
        assert client.get("/api/connections").json()["jev"]["verification"]["status"] == "failed"


def test_blank_model_rejected():
    with TestClient(create_app()) as client:
        assert client.post("/api/connections", json={"provider": "chat", "url": "https://example.invalid/v1", "model": "   "}).status_code == 422
