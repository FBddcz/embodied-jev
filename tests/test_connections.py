import json

import httpx
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
