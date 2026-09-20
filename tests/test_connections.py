import json

import httpx
import pytest
from fastapi.testclient import TestClient

from embodied_jev.policies import DecisionPolicy
from embodied_jev.server import create_app


def test_connection_save_redacts_key_and_does_not_call_provider(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Saving a connection must not call a provider")
    monkeypatch.setattr(httpx, "post", fail)
    with TestClient(create_app()) as client:
        payload = {"provider": "chat", "url": "https://example.invalid/v1", "model": "example/model", "api_key": "test-secret-key"}
        response = client.post("/api/connections", json=payload)
        assert response.status_code == 200
        for path in ["/api/connections", "/api/config", "/api/state", "/api/export"]:
            assert "test-secret-key" not in client.get(path).text
        assert "test-secret-key" not in response.text
        assert client.get("/api/connections").json()["chat"]["url"] == "https://example.invalid/v1/chat/completions"
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
    monkeypatch.setattr(httpx, "post", post)
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
        assert client.get("/api/state").json()["cycles"] == 0


def test_bad_connection_and_foreign_origin_rejected():
    with TestClient(create_app()) as client:
        for url in ["file:///tmp/test", "https://user:password@example.invalid", "https://example.invalid?key=secret"]:
            assert client.post("/api/connections", json={"provider": "chat", "url": url, "model": "test"}).status_code == 422
        assert client.post("/api/connections", json={"provider": "chat", "url": "https://example.invalid", "model": "test"},
                           headers={"Origin": "https://other.invalid"}).status_code == 403


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
    monkeypatch.setattr(httpx, "post", post)
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
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: httpx.Response(200,
        request=httpx.Request("POST", url), json={"content": content, "stop_reason": stop_reason}))
    policy = DecisionPolicy("claude", {"url": "https://example.invalid/v1/messages", "key": "test", "model": "test"})
    with pytest.raises(ValueError):
        policy.choose({}, "Choose", {"move": "Move", "hold": "Hold"}, "move", [])
