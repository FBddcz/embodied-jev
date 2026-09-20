import httpx
import pytest

from embodied_jev.policies import DecisionPolicy, validate_answer


@pytest.mark.parametrize("answer", [
    {"choice": "a", "probabilities": {"a": .2, "b": .8}},
    {"choice": "c", "probabilities": {"a": .8, "b": .2}},
    {"choice": "a", "probabilities": {"a": 1}},
    {"choice": "a", "probabilities": {"a": float("nan"), "b": .2}},
    {"choice": "a", "probabilities": {"a": True, "b": 0}},
    {"choice": "a", "probabilities": {"a": .8, "b": .8}},
    [],
])
def test_reject_invalid_probability_contract(answer):
    with pytest.raises(ValueError):
        validate_answer(answer, ["a", "b"])


@pytest.mark.parametrize("provider", ["jev", "local"])
def test_http_request_contract_and_probability_semantics(monkeypatch, provider):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("EMBODIED_LOCAL_URL", "http://127.0.0.1:9000/v1/systemone")
    def post(url, *, json, **kwargs):
        assert json["questions"]["action"]["criteria"] == {"a": "Move", "b": "Hold"}
        assert json["state"]["observation"] == {"tcp": [.4, 0, .2]}
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "model": "test-resolved-model", "answers": {"action": {
                "choice": "a", "probabilities": {"a": .8, "b": .2}, "confidence": .31}},
            "usage": {"input_tokens": 32}})
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    policy = DecisionPolicy(provider)
    answer = policy.choose({"tcp": [.4, 0, .2]}, "Choose", {"a": "Move", "b": "Hold"}, "a", [])
    assert answer["selected_probability"] == .8
    assert answer["provider_confidence"] == .31
    assert policy.calls == 1 and policy.tokens == 32
    assert policy.model == "test-resolved-model"
    assert policy.last_input == {"state": {"observation": {"tcp": [.4, 0, .2]}, "recent_outcomes": []},
                                 "decision": {"type": "choice", "instructions": "Choose", "criteria": {"a": "Move", "b": "Hold"}}}
    policy.choose({}, "Choose", {"lift": "Lift"}, "lift", [])
    assert policy.last_input is None


def test_singleton_does_not_load_or_call_model(monkeypatch):
    monkeypatch.setenv("EMBODIED_MINICPM", "1")
    policy = DecisionPolicy("minicpm")
    def fail(*args):
        pytest.fail("No model call is needed for a singleton menu")
    monkeypatch.setattr(policy, "_local_inference", fail)
    answer = policy.choose({}, "Choose", {"lift": "Lift"}, "lift", [])
    assert not answer["model_call"]
    assert answer["probabilities"] == {}
    assert answer["selected_probability"] is None


def test_missing_provider_does_not_fall_back(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="not configured"):
        DecisionPolicy("jev")
