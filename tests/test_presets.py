import json
from pathlib import Path

import httpx
import pytest

from embodied_jev.evidence import compact_observation, validate_user_context
from embodied_jev.policies import DecisionPolicy
from embodied_jev.presets import load_preset, validate_preset
from embodied_jev.runtime import Session


def test_example_preset_roundtrips_and_does_not_load_code():
    example = Path(__file__).parents[1] / "examples" / "transfer-preset.json"
    preset = load_preset(example)
    assert validate_preset(json.loads(json.dumps(preset))) == preset
    with pytest.raises(ValueError):
        validate_preset({**preset, "python": "print('do not execute')"})
    with pytest.raises(ValueError):
        validate_preset({**preset, "task": "invented-task"})


@pytest.mark.parametrize("value", [[], {"x": float("nan")}, {"large": "中" * 3000},
                                  {"nested": [{"api_key": "do-not-store"}]}, {"Authorization": "Bearer private"}])
def test_context_rejects_invalid_or_oversized_json(value):
    with pytest.raises(ValueError):
        validate_user_context(value)


def test_partial_custom_geometry_is_not_mistaken_for_robot_observation():
    custom = {"relative_geometry": {"distance": 3}, "intent": "test"}
    assert compact_observation(custom) == custom


def test_context_is_appended_without_overwriting_physical_observation(monkeypatch):
    requests = []
    def post(url, **kwargs):
        payload = kwargs["json"]
        requests.append(payload)
        keys = list(payload["questions"]["action"]["criteria"])
        return httpx.Response(200, request=httpx.Request("POST", url), json={"model": "test", "answers": {
            "action": {"choice": keys[0], "probabilities": {key: float(index == 0) for index, key in enumerate(keys)}}}})
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    context = {"tcp": [999, 999, 999], "note": "external observation"}
    session = Session(provider="local", preview=False, speed=0, max_cycles=1, user_context=context,
                      scene_config={"source_xy": [.42, -.17], "target_xy": [.44, .18]},
                      connection={"url": "http://test.invalid", "model": "test", "key": "", "profile_id": "p1"})
    context["note"] = "changed by caller"
    session.start()
    session.worker.join(5)
    try:
        state = requests[0]["state"]
        assert state["user_context"]["tcp"] == [999, 999, 999]
        assert state["observation"]["tcp"] != state["user_context"]["tcp"]
        assert state["user_context"]["note"] == "external observation"
        assert state["observation"]["scene_config"]["target_xy"] == [.44, .18]
        exported = session.export()
        assert exported["profile_id"] == "p1"
        assert exported["history"][0]["decision_inputs"]["action"]["state"]["user_context"] == exported["user_context"]
        assert exported["history"][0]["before"]["tcp"] != [999, 999, 999]
    finally:
        session.stop()
        session.worker.join(5)
