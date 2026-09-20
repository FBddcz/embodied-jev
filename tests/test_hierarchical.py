"""Control and evidence contracts; fake API answers do not measure model ability."""
import copy
import json
import threading
import time

import httpx
import pytest

from embodied_jev import hierarchical as h
from embodied_jev.policies import DecisionPolicy
from embodied_jev.runtime import Session


def observation():
    return {"tcp": [.38, -.08, .22], "object": [.44, -.15, .02],
            "destination": [.43, .18, .026], "gripper": "open", "held": False,
            "grasp_secured": False, "support_contact": False, "finger_contacts": [],
            "source": "RGB-D detector", "perception": {"source": "rgbd"},
            "success": "oracle-sentinel", "recommended_stage": "oracle-sentinel"}


def motor_input(subgoal="approach"):
    return h.motor_questions(h.hierarchy_state(observation(), []), subgoal)


def channel_choices(**overrides):
    return {**dict(x="hold", y="hold", z="hold", gripper="open"), **overrides}


def native_answer(choices, menus):
    return {name: {"choice": choice, "probabilities": {
        option: .9 if option == choice else .1 / (len(menus[name]) - 1)
        for option in menus[name]}, "confidence": .4} for name, choice in choices.items()}


def response(url, answers, **extras):
    return httpx.Response(200, request=httpx.Request("POST", url), json={
        "model": "fixture-resolved", "answers": answers,
        "usage": {"input_tokens": 17, "output_tokens": 9}, **extras})


def policy(provider="jev"):
    return DecisionPolicy(provider, connection={"url": "https://example.invalid", "key": "fixture-secret", "model": "fixture"})


def test_geometry_uses_measured_inputs_without_oracle_or_stage_filtering():
    state, questions = motor_input()
    assert "oracle-sentinel" not in json.dumps(state)
    assert state["cube_mm"] == [440, -150, 20]
    assert state["grasp_minus_tcp_mm"] == [60, -70, -199]
    assert set(h.SUBGOALS) == {"approach", "grasp", "lift", "carry", "lower", "release", "withdraw", "finish"}
    for subgoal in h.SUBGOALS:
        _, questions = h.motor_questions(state, subgoal)
        assert all(set(questions[axis]["criteria"]) == {"negative", "hold", "positive"} for axis in "xyz")
    with pytest.raises(ValueError, match="direct-image"):
        h.hierarchy_state({**observation(), "perception": {"source": "vision"}}, [])
    with pytest.raises(ValueError, match="直接图像"):
        Session(control_mode="hierarchical", observation_mode="vision", provider="chat")


def test_direction_is_not_corrected_and_steps_are_bounded():
    state, _ = motor_input()
    # X error is positive, but the model's incorrect negative choice must survive.
    choices = channel_choices(x="negative", y="negative", z="positive", gripper="close")
    selected, serialised = h.assemble_motion(observation(), state, {k: {"choice": v} for k, v in choices.items()})
    assert serialised["delta_xyz"] == [-.012, -.012, .012]
    assert serialised["channels"] == choices and selected.gripper == "close"
    state["target_minus_tcp_mm"] = [0, 1, 3]
    _, serialised = h.assemble_motion(observation(), state, {k: {"choice": v} for k, v in choices.items()})
    assert serialised["delta_xyz"] == [-.002, -.002, .003]
    selected, _ = h.assemble_motion({**observation(), "tcp": [.201, 0, .22]}, state,
                                  {k: {"choice": v} for k, v in choices.items()})
    assert not selected.admitted and "workspace" in selected.rejection


def test_jev_batches_four_questions_and_preserves_probabilities(monkeypatch):
    state, questions = motor_input()
    choices = channel_choices(x="positive", y="negative")
    answers = native_answer(choices, {k: v["criteria"] for k, v in questions.items()})
    answers["x"]["probabilities"] = {"positive": .33, "hold": .34, "negative": .33}
    captured = []
    def post(url, **kwargs):
        captured.append(copy.deepcopy(kwargs["json"]))
        return response(url, answers)
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    p = policy()
    result = p.choose_channels(state, questions)
    assert len(captured) == p.calls == 1
    assert captured[0]["questions"] == questions
    assert p.last_input == {"state": state, "questions": questions}
    assert p.tokens == 17 and p.output_tokens == 9 and len(p.latencies) == 1
    assert result["x"]["choice"] == "positive"
    assert result["x"]["probability_warning"] == "choice_below_reported_max"
    for name in questions:
        assert result[name]["probabilities"] == answers[name]["probabilities"]
        assert result[name]["shared_request"] and result[name]["provider_confidence"] == .4
    assert "fixture-secret" not in json.dumps(p.last_input)
    p.close()


@pytest.mark.parametrize("fault", ["missing", "extra", "unknown", "bad_probability"])
def test_incomplete_or_invalid_channels_fail_whole_request(monkeypatch, fault):
    state, questions = motor_input()
    answers = native_answer(channel_choices(), {k: v["criteria"] for k, v in questions.items()})
    if fault == "missing":
        del answers["z"]
    elif fault == "extra":
        answers["other"] = answers["x"]
    elif fault == "unknown":
        answers["x"]["choice"] = "jump"
    else:
        answers["x"]["probabilities"]["hold"] = float("nan")
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(lambda url, **kw: response(url, answers)))
    with pytest.raises(ValueError):
        policy().choose_channels(state, questions)


@pytest.mark.parametrize("provider", ["chat", "claude"])
def test_generated_choices_never_get_probabilities(monkeypatch, provider):
    state, questions = motor_input()
    choices = channel_choices()
    def post(url, **kwargs):
        if provider == "chat":
            return response(url, {}, choices=[{"finish_reason": "stop", "message": {"content": json.dumps(choices)}}])
        assert kwargs["json"]["tools"][0]["input_schema"]["required"] == list(questions)
        return response(url, {}, stop_reason="tool_use", content=[
            {"type": "text", "text": "Selecting channels."},
            {"type": "tool_use", "name": "select_channels", "input": choices}])
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    result = policy(provider).choose_channels(state, questions)
    assert all(row["probabilities"] == {} and row["selected_probability"] is None for row in result.values())


def test_explicit_baseline_and_local_cancellation(monkeypatch):
    state, questions = motor_input()
    baseline = DecisionPolicy("baseline")
    with pytest.raises(ValueError, match="Invalid baseline"):
        baseline.choose_channels(state, questions, baseline_choices=channel_choices(x="invalid"))
    rows = baseline.choose_channels(state, questions, baseline_choices=channel_choices())
    assert baseline.calls == 0 and all(not row["model_call"] for row in rows.values())
    local = policy("minicpm")
    calls = []
    def choose(*args, **kwargs):
        calls.append(args)
        local.last_input = {"test_pass": len(calls)}
        return {"choice": "hold"}
    monkeypatch.setattr(local, "choose_plan", choose)
    state["note"] = "fixture-secret"
    assert local.choose_channels(state, questions, continue_run=lambda: len(calls) < 1) is None
    assert len(calls) == 1 and list(local.last_input["channel_inputs"]) == ["x"]
    assert "fixture-secret" not in json.dumps(local.last_input)


def mock_runtime(monkeypatch, *, intent="approach", choices=None, entered=None, release=None, low=None):
    calls = []
    def post(url, **kwargs):
        payload = copy.deepcopy(kwargs["json"])
        calls.append(payload)
        menus = {k: v["criteria"] for k, v in payload["questions"].items()}
        if set(menus) == {"action"}:
            assert set(menus["action"]) == set(h.SUBGOALS)
            if entered:
                entered.set()
                assert release.wait(5)
            answers = native_answer({"action": intent}, menus)
        else:
            assert payload["state"]["selected_subgoal"] == intent
            answers = native_answer(choices or channel_choices(x="positive"), menus)
            if low:
                answers[low]["probabilities"] = {k: 1 / 3 for k in menus[low]}
        return response(url, answers)
    monkeypatch.setattr(DecisionPolicy, "_post", staticmethod(post))
    def forbidden(*args, **kwargs):
        raise AssertionError("The model path must never consult a rule decision")
    monkeypatch.setattr(h, "baseline_subgoal", forbidden)
    monkeypatch.setattr(h, "baseline_channels", forbidden)
    session = Session(provider="jev", connection={"url": "https://example.invalid", "key": "fixture-secret", "model": "fixture"},
                      control_mode="hierarchical", threshold=.5, max_cycles=1, preview=False, speed=0)
    return session, calls


def test_runtime_dependency_physics_and_export(monkeypatch):
    s, calls = mock_runtime(monkeypatch)
    try:
        s.start()
        s.worker.join(5)
        assert not s.worker.is_alive() and s.status == "exhausted", s.message
        export = s.export()
        row = export["history"][0]
        assert len(calls) == export["model_calls"] == 2
        assert export["policy_version"] == h.PROMPT_VERSION
        assert row["intent"]["choice"] == "approach" and row["executed"]
        assert row["after"]["tcp"][0] > row["before"]["tcp"][0] + .009
        assert row["decision"]["probabilities"] == {} and row["decision"]["selected_probability"] is None
        assert row["decision_inputs"]["phase"]["state"] == calls[0]["state"]
        assert row["decision_inputs"]["action"] == {k: calls[1][k] for k in ("state", "questions")}
        assert "fixture-secret" not in json.dumps(export)
    finally:
        s.stop()


def test_stop_during_intent_prevents_motor_request(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    s, calls = mock_runtime(monkeypatch, entered=entered, release=release)
    try:
        s.start()
        assert entered.wait(5)
        s.stop()
        release.set()
        s.worker.join(5)
        assert not s.worker.is_alive() and len(calls) == 1 and not s.history
        assert s.last_decision_inputs["phase"] is not None
    finally:
        release.set()
        s.stop()
        s.worker.join(5)


def test_low_channel_probability_prevents_all_motion(monkeypatch):
    s, calls = mock_runtime(monkeypatch, low="y")
    before = s.world.observe()["tcp"]
    try:
        s.start()
        deadline = time.monotonic() + 5
        while s.status == "running" and time.monotonic() < deadline:
            time.sleep(.01)
        assert s.status == "uncertain", s.message
        assert len(calls) == 2 and s.cycles == 0 and not s.history
        assert s.world.observe()["tcp"] == before
        assert "y" in s.message
    finally:
        s.stop()
        s.worker.join(5)


def test_model_finish_does_not_override_physical_success(monkeypatch):
    s, _ = mock_runtime(monkeypatch, intent="finish", choices=channel_choices())
    try:
        s.start()
        s.worker.join(5)
        assert s.status == "exhausted" and not s.export()["success"]
    finally:
        s.stop()


def test_workspace_rejection_count_is_not_doubled(monkeypatch):
    s, _ = mock_runtime(monkeypatch)
    try:
        obs = s.world.observe()
        state, _ = motor_input()
        selected, serialised = h.assemble_motion(obs, state,
            {k: {"choice": v} for k, v in channel_choices().items()})
        selected.admitted, selected.rejection = False, "fixture workspace rejection"
        serialised.update(admitted=False, rejection=selected.rejection)
        s.wake.set()
        s._execute_increment(obs, selected, [selected], [serialised], {"choice": selected.id})
        assert s.history[0]["rejected_count"] == 1 and not s.history[0]["executed"]
    finally:
        s.stop()
