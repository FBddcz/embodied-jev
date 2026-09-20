import json
from pathlib import Path

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


def test_recorded_jev_mismatch_preserves_choice_probabilities_and_execution(monkeypatch):
    from embodied_jev.runtime import Session

    # Captured from Jev 1.13.0 on 2026-09-20: choice 18%, another option 19%.
    response = json.loads((Path(__file__).parent / "fixtures" / "jev-choice-probability-mismatch.json").read_text())
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(lambda url, **kwargs: httpx.Response(
        200, request=httpx.Request("POST", url), json=response)))
    session = Session(provider="jev", connection={"url": "https://example.invalid", "key": "fixture-secret", "model": "jev-latest"},
                      seed=7, control_mode="incremental", max_cycles=1, threshold=0, speed=0)
    try:
        before = session.world.observe()["tcp"]
        session.start()
        session.worker.join(5)
        assert not session.worker.is_alive()
        exported = session.export()
        assert exported["status"] == "exhausted" and exported["model_calls"] == 1
        row = exported["history"][0]
        assert row["executed"] and row["action"]["id"] == "y_neg_40"
        assert row["decision"]["probabilities"] == response["answers"]["action"]["probabilities"]
        assert row["decision"]["selected_probability"] == .18
        assert row["decision"]["probability_warning"] == "choice_below_reported_max"
        assert row["after"]["tcp"][1] < before[1] - .03
        assert "fixture-secret" not in json.dumps(exported)
        # Other typed providers still enforce their strict contract.
        with pytest.raises(ValueError, match="highest-probability"):
            validate_answer(response["answers"]["action"], row["decision"]["probabilities"])
    finally:
        session.stop()


@pytest.mark.parametrize("answer", [
    {"choice": "unknown", "probabilities": {"a": .5, "b": .5}},
    {"choice": [], "probabilities": {"a": .5, "b": .5}},
    {"choice": "a", "probabilities": {"a": .5}},
    {"choice": "a", "probabilities": {"a": -.1, "b": 1.1}},
    {"choice": "a", "probabilities": {"a": float("nan"), "b": .5}},
    {"choice": "a", "probabilities": {"a": .8, "b": .8}},
])
def test_jev_choice_authority_does_not_relax_probability_integrity(answer):
    with pytest.raises(ValueError):
        validate_answer(answer, ["a", "b"], require_highest=False)
