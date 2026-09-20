"""Local contract tests: mocked responses only, no paid or live provider calls."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from embodied_jev.policies import DecisionPolicy
from embodied_jev.server import create_app


PROVIDERS = {
    "jev": ("https://api.typesafe.ai/v1/systemone", "https://api.typesafe.ai/v1/systemone"),
    "local": ("http://127.0.0.1:9123/decide", "http://127.0.0.1:9123/decide"),
    "chat": ("https://example.invalid/v1", "https://example.invalid/v1/chat/completions"),
    "claude": ("https://api.anthropic.com/v1", "https://api.anthropic.com/v1/messages"),
}
SECRET = "matrix-test-secret-never-display"
PRIVATE_BODY = "private-provider-error-body"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_failed_model_request_counts_attempt_and_latency(monkeypatch, provider):
    def fail(url, **kwargs):
        raise httpx.ReadTimeout("private-error", request=httpx.Request("POST", url))
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(fail))
    policy = DecisionPolicy(provider, {"url": PROVIDERS[provider][1], "model": "model", "key": SECRET})
    with pytest.raises(httpx.ReadTimeout):
        policy.choose({}, "choose", {"ready": "Ready", "hold": "Hold"}, "ready", [])
    assert policy.calls == 1
    assert len(policy.latencies) == 1 and policy.latencies[0] >= 0


@pytest.mark.parametrize("provider", PROVIDERS)
def test_response_metadata_cannot_echo_credentials_into_decision(monkeypatch, provider):
    body = _body(provider)
    body["model"] = f"model-{SECRET}"
    if provider in {"jev", "local"}:
        body["answers"]["action"]["confidence"] = SECRET
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(lambda url, **kwargs:
        httpx.Response(200, request=httpx.Request("POST", url), json=body)))
    policy = DecisionPolicy(provider, {"url": PROVIDERS[provider][1], "model": "model", "key": SECRET})
    answer = policy.choose({}, "choose", {"ready": "Ready", "hold": "Hold"}, "ready", [])
    assert SECRET not in json.dumps(answer)
    assert SECRET not in policy.model
    assert answer["provider_confidence"] is None


def _payload(provider):
    return {"provider": provider, "url": PROVIDERS[provider][0], "model": "requested-model", "api_key": SECRET}


def _body(provider, choice="ready"):
    if provider in {"jev", "local"}:
        return {"model": "resolved-model", "answers": {"action": {
            "choice": choice, "probabilities": {"ready": .9, "hold": .1}, "confidence": .7}},
            "usage": {"input_tokens": 12, "output_tokens": 1}}
    if provider == "chat":
        return {"model": "resolved-model", "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"choice": choice})}}], "usage": {"prompt_tokens": 12, "completion_tokens": 6}}
    return {"model": "resolved-model", "stop_reason": "tool_use", "content": [{
        "type": "tool_use", "name": "select_action", "input": {"choice": choice}}],
        "usage": {"input_tokens": 12, "output_tokens": 6}}


@pytest.mark.parametrize("provider", PROVIDERS)
def test_provider_contract_verifies_only_after_explicit_test(monkeypatch, provider):
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        assert url == PROVIDERS[provider][1]
        assert kwargs["follow_redirects"] is False
        expected_headers = ({"x-api-key": SECRET, "anthropic-version": "2023-06-01"}
                            if provider == "claude" else {"Authorization": f"Bearer {SECRET}"})
        assert kwargs["headers"] == expected_headers
        payload = kwargs["json"]
        assert payload["model"] == "requested-model"
        if provider in {"jev", "local"}:
            assert payload["questions"]["action"]["type"] == "choice"
            assert set(payload["questions"]["action"]["criteria"]) == {"ready", "hold"}
            state = payload["state"]
        else:
            content = json.loads(next(m["content"] for m in payload["messages"] if m["role"] == "user"))
            assert set(content["decision"]["criteria"]) == {"ready", "hold"}
            state = content["state"]
        assert "no robot command will execute" in state["observation"]["purpose"]
        return httpx.Response(200, request=httpx.Request("POST", url), json=_body(provider))

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    with TestClient(create_app()) as client:
        assert client.post("/api/connections", json=_payload(provider)).status_code == 200
        assert calls == []
        assert client.get("/api/connections").json()[provider]["verification"]["status"] == "untested"
        response = client.post(f"/api/connections/{provider}/test")
        assert response.status_code == 200
        assert response.json()["model"] == "resolved-model"
        assert response.json()["verification"]["status"] == "passed"
        verification = client.get("/api/connections").json()[provider]["verification"]
        assert verification == response.json()["verification"]
        assert verification["model"] == "resolved-model" and verification["latency_ms"] >= 0
        assert calls == [PROVIDERS[provider][1]]
        assert client.get("/api/state").json()["cycles"] == 0
        assert SECRET not in response.text


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("failure,expected", [
    (401, "认证失败"), (403, "访问被拒绝"), (404, "未找到接口或模型"),
    (429, "请求受限"), (503, "服务暂时不可用"), ("timeout", "超时"), ("network", "无法连接"),
])
def test_provider_failure_matrix_is_actionable_and_redacted(monkeypatch, provider, failure, expected):
    def post(url, **kwargs):
        request = httpx.Request("POST", url, headers={"Authorization": f"Bearer {SECRET}"})
        if failure == "timeout":
            raise httpx.ReadTimeout(f"{SECRET} {PRIVATE_BODY}", request=request)
        if failure == "network":
            raise httpx.ConnectError(f"{SECRET} {PRIVATE_BODY}", request=request)
        return httpx.Response(failure, request=request, json={"error": f"{SECRET} {PRIVATE_BODY}"})

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    with TestClient(create_app()) as client:
        client.post("/api/connections", json=_payload(provider))
        response = client.post(f"/api/connections/{provider}/test")
        assert response.status_code == 502
        assert expected in response.json()["detail"]
        record = client.get("/api/connections").json()[provider]["verification"]
        assert record["status"] == "failed" and record["checked_at"]
        assert record["message"] == response.json()["detail"]
        assert record["latency_ms"] >= 0
        for text in (response.text, client.get("/api/connections").text):
            assert SECRET not in text and PRIVATE_BODY not in text
            assert "Authorization" not in text
        assert client.get("/api/state").json()["cycles"] == 0


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("failure", ["empty", "not_object", "invalid_json", "invalid_choice"])
def test_provider_malformed_response_never_passes_verification(monkeypatch, provider, failure):
    def post(url, **kwargs):
        request = httpx.Request("POST", url)
        if failure == "invalid_json":
            return httpx.Response(200, request=request, text=f"{SECRET} {PRIVATE_BODY}")
        body = {} if failure == "empty" else [] if failure == "not_object" else _body(provider, SECRET)
        return httpx.Response(200, request=request, json=body)

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    with TestClient(create_app()) as client:
        client.post("/api/connections", json=_payload(provider))
        response = client.post(f"/api/connections/{provider}/test")
        assert response.status_code == 502
        assert "响应格式" in response.json()["detail"]
        connections = client.get("/api/connections")
        assert connections.json()[provider]["verification"]["status"] == "failed"
        for text in (response.text, connections.text):
            assert SECRET not in text and PRIVATE_BODY not in text


@pytest.mark.parametrize("provider", PROVIDERS)
def test_provider_cannot_echo_key_through_resolved_model(monkeypatch, provider):
    def post(url, **kwargs):
        body = _body(provider)
        body["model"] = f"model-{SECRET}"
        return httpx.Response(200, request=httpx.Request("POST", url), json=body)

    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    with TestClient(create_app()) as client:
        client.post("/api/connections", json=_payload(provider))
        response = client.post(f"/api/connections/{provider}/test")
        assert response.status_code == 200
        assert SECRET not in response.text
        assert SECRET not in client.get("/api/connections").text


@pytest.mark.parametrize("provider", PROVIDERS)
def test_http_client_is_lazy_reused_and_closed(monkeypatch, provider):
    client_type = httpx.Client
    clients, requests = [], []

    def handle(request):
        requests.append(request)
        assert str(request.url) == PROVIDERS[provider][1]
        assert SECRET not in str(request.url)
        if provider == "claude":
            assert request.headers["x-api-key"] == SECRET
            assert "authorization" not in request.headers
        else:
            assert request.headers["authorization"] == f"Bearer {SECRET}"
            assert "x-api-key" not in request.headers
        return httpx.Response(200, json=_body(provider))

    def make_client():
        client = client_type(transport=httpx.MockTransport(handle))
        clients.append(client)
        return client

    monkeypatch.setattr("embodied_jev.policies.httpx.Client", make_client)
    policy = DecisionPolicy(provider, {"url": PROVIDERS[provider][1], "key": SECRET, "model": "requested-model"})
    assert clients == []
    policy.choose({}, "Choose", {"ready": "Ready"}, "ready", [])
    assert clients == []
    for _ in range(2):
        policy.choose({}, "Choose", {"ready": "Ready", "hold": "Hold"}, "ready", [])
    assert len(clients) == 1 and len(requests) == 2
    assert not clients[0].is_closed
    assert "authorization" not in clients[0].headers and "x-api-key" not in clients[0].headers
    policy.close()
    policy.close()
    assert clients[0].is_closed
    # Pausing releases sockets; resuming can establish a new pool.
    policy.choose({}, "Choose", {"ready": "Ready", "hold": "Hold"}, "ready", [])
    assert len(clients) == 2 and len(requests) == 3
    policy.close()
    assert all(client.is_closed for client in clients)


def test_http_clients_do_not_share_credentials_between_policies(monkeypatch):
    client_type = httpx.Client
    clients, requests = [], []

    def make_client():
        client_id = len(clients)

        def handle(request):
            requests.append((client_id, str(request.url), request.headers["authorization"]))
            return httpx.Response(200, json=_body("chat"))

        client = client_type(transport=httpx.MockTransport(handle))
        clients.append(client)
        return client

    monkeypatch.setattr("embodied_jev.policies.httpx.Client", make_client)
    original = {"url": "https://first.invalid/v1/chat/completions", "key": "first-secret", "model": "model"}
    first = DecisionPolicy("chat", original)
    second = DecisionPolicy("chat", {"url": "https://second.invalid/v1/chat/completions", "key": "second-secret", "model": "model"})
    original["key"] = "changed-outside-policy"
    for policy in (first, second, first):
        policy.choose({}, "Choose", {"ready": "Ready", "hold": "Hold"}, "ready", [])
    assert requests == [
        (0, "https://first.invalid/v1/chat/completions", "Bearer first-secret"),
        (1, "https://second.invalid/v1/chat/completions", "Bearer second-secret"),
        (0, "https://first.invalid/v1/chat/completions", "Bearer first-secret"),
    ]
    first.close()
    second.close()
    assert len(clients) == 2 and all(client.is_closed for client in clients)


@pytest.mark.parametrize("success", [True, False])
def test_connection_probe_always_closes_temporary_http_client(monkeypatch, success):
    client_type = httpx.Client
    clients = []

    def handle(request):
        if success:
            return httpx.Response(200, json=_body("chat"))
        raise httpx.ReadTimeout("Simulated timeout", request=request)

    def make_client():
        client = client_type(transport=httpx.MockTransport(handle))
        clients.append(client)
        return client

    monkeypatch.setattr("embodied_jev.policies.httpx.Client", make_client)
    with TestClient(create_app()) as client:
        client.post("/api/connections", json=_payload("chat"))
        assert clients == []
        response = client.post("/api/connections/chat/test")
        assert response.status_code == (200 if success else 502)
        assert len(clients) == 1 and clients[0].is_closed
