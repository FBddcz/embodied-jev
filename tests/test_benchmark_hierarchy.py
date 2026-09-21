"""Hierarchy information rights, model authority and two-request budgets."""
import copy
import json
from types import SimpleNamespace

import pytest

from embodied_jev import benchmark_hierarchy as h, evaluation as e, policies


def observation(task="pick-place-v3"):
    return {"task": task, "tcp": [0., .6, .2], "goal": [.1, .8, .2],
            "object_slots": [{"position": [.08, .68, .02]}], "gripper_open_fraction": 1.,
            "manipulation": {"hand_position_m": [0., .6, .2], "finger_center_m": [0., .6, .155],
                             "bilateral_contact": False, "translation_metres_per_unit": .01}}


def test_finger_offset_is_measured_instead_of_assuming_panda_tcp():
    obs = observation()
    obs["manipulation"]["hand_position_m"][:2] = [.08, .68]
    obs["manipulation"]["finger_center_m"][:2] = [.08, .68]
    state, options = h.prepare(obs, obs, [])
    assert options["approach"]["target_hand_m"][2] == pytest.approx(.07)
    assert "lower" not in options
    assert state["hand_mm"][2] - state["finger_center_mm"][2] == pytest.approx(45)
    assert "success" not in state and "reward" not in state


def test_unaligned_approach_preserves_current_height_then_descends_after_xy_alignment():
    obs = observation()
    state, options = h.prepare(obs, obs, [])
    assert not state["object_xy_aligned"] and not state["grasp_pose_reached"]
    assert options["approach"]["target_hand_m"] == pytest.approx([.08, .68, .2])
    # Use a different measured offset: the target must follow it, not a fixed 45 mm.
    obs["manipulation"].update(hand_position_m=[.08, .68, .21], finger_center_m=[.08, .68, .15])
    state, options = h.prepare(obs, obs, [])
    assert state["object_xy_aligned"] and not state["grasp_pose_reached"]
    assert options["approach"]["target_hand_m"] == pytest.approx([.08, .68, .085])


def test_measured_grasp_pose_goal_alignment_and_lost_contact_recovery():
    obs = observation()
    obs["manipulation"].update(hand_position_m=[.08, .68, .07], finger_center_m=[.08, .68, .025])
    state, options = h.prepare(obs, obs, [])
    assert state["grasp_pose_reached"] and not state["grasp_with_contact"]
    assert not state["object_goal_xy_aligned"]
    obs["manipulation"]["bilateral_contact"] = True
    assert h.prepare(obs, obs, [])[0]["grasp_with_contact"]
    obs["goal"][:2] = [.08, .68]
    assert h.prepare(obs, obs, [])[0]["object_goal_xy_aligned"]
    obs["manipulation"]["bilateral_contact"] = False
    state, options = h.prepare(obs, obs, [{"subgoal": "carry"}])
    assert not state["grasp_with_contact"]
    assert options["approach"]["finger_intent"] == "open"
    assert {"approach", "grasp", "lift", "carry", "lower_goal", "finish"} == set(options)


@pytest.mark.parametrize("errors_mm", [(-6., 5., 6.), (-5., 0., 5.)])
def test_each_axis_question_has_its_own_coordinates_signed_error_and_tolerance(errors_mm):
    obs = observation("reach-v3")
    hand = obs["manipulation"]["hand_position_m"]
    obs["goal"] = [x + error / 1000 for x, error in zip(hand, errors_mm)]
    state, options = h.prepare(obs, obs, [])
    motor, questions, _ = h.motor_input(state, options, "reach", obs, .5, 5)
    assert motor["axis_tolerance_mm"] == 5.
    for i, axis in enumerate("xyz"):
        prompt = questions[axis]["instructions"]
        assert f"Decide ONLY {axis.upper()}" in prompt
        assert f"Current hand coordinate {hand[i] * 1000:.2f} mm" in prompt
        assert f"target {obs['goal'][i] * 1000:.2f} mm" in prompt
        assert f"error {errors_mm[i]:+.2f} mm" in prompt
        assert "Tolerance is 5 mm" in prompt
        criteria = questions[axis]["criteria"]
        assert "below -5 mm" in criteria["negative"]
        assert "between -5 and +5 mm inclusive" in criteria["hold"]
        assert "above +5 mm" in criteria["positive"]


def test_closed_empty_gripper_does_not_become_a_confirmed_grasp():
    obs = observation(); obs["gripper_open_fraction"] = .1
    state, options = h.prepare(obs, obs, [])
    assert not state["grasp_with_contact"]
    assert options["approach"]["finger_intent"] == "open"
    assert "carry" in options  # Candidates are not secretly filtered to the reference policy's choice.


def test_contact_far_from_object_is_not_a_grasp():
    obs = observation(); obs["manipulation"]["bilateral_contact"] = True
    assert not h.prepare(obs, obs, [])[0]["grasp_with_contact"]


def test_invalid_subgoal_and_unknown_task_fail_explicitly():
    obs = observation(); state, options = h.prepare(obs, obs, [])
    with pytest.raises(ValueError, match="Unknown"):
        h.motor_input(state, options, "invented", obs, .5, 5)
    with pytest.raises(ValueError, match="No validated"):
        h.prepare(observation("door-open-v3"), obs, [])


def test_adaptive_amplitudes_are_bounded_and_all_motor_options_remain_available():
    obs = observation(); state, options = h.prepare(obs, obs, [])
    motor, questions, scales = h.motor_input(state, options, "approach", obs, .5, 5)
    assert all(0 < scale <= .5 for scale in scales)
    assert set(questions["x"]["criteria"]) == {"positive", "negative", "hold"}
    assert motor["target_minus_hand_mm"][0] > 0


class Worker:
    packets = []
    def __init__(self, python, log):
        type(self).packets = []
    def request(self, packet, timeout=60):
        type(self).packets.append(packet)
        if packet["command"] == "reset":
            return {"observation": observation(), "metadata": {}}
        return {"observation": observation(), "action": packet["action"],
                "success": False, "terminated": False, "truncated": False}
    def close(self):
        pass


def arguments(tmp_path, **overrides):
    return SimpleNamespace(**{**dict(worker_python="fixture", max_steps=10, max_calls=3,
        timeout=30., action_repeat=5, control_mode="hierarchical", observation_mode="privileged",
        policy="jev", threshold=0., action_scale=.5), **overrides})


def policy_factory(monkeypatch, clock=None, confidence=.9):
    calls = []
    class Policy:
        def __init__(self, provider):
            self.calls, self.latencies, self.model, self.last_input = 0, [], "fixture", None
        def choose_plan(self, state, question, options):
            self.calls += 1; calls.append("subgoal"); self.last_input = copy.deepcopy(state)
            if clock is not None: clock[0] = 100.
            return {"choice": "approach", "selected_probability": confidence}
        def choose_channels(self, state, questions):
            self.calls += 1; calls.append("motor"); self.last_input = copy.deepcopy(state)
            return {k: {"choice": v, "selected_probability": .9} for k, v in
                    dict(x="negative", y="hold", z="hold", gripper="close").items()}
        def close(self):
            pass
    monkeypatch.setattr(policies, "DecisionPolicy", Policy)
    monkeypatch.setattr(e, "Worker", Worker)
    return calls


def episode(tmp_path, args):
    return e.external_episode({"backend": "metaworld"},
        {"id": "pick-0", "task": "pick-place-v3", "seed": 0}, args, tmp_path)


def test_budget_reserves_two_requests_and_does_not_correct_wrong_motor_direction(tmp_path, monkeypatch):
    calls = policy_factory(monkeypatch)
    row = episode(tmp_path, arguments(tmp_path))
    assert (row["steps"], row["model_calls"], row["status"]) == (5, 2, "call_budget")
    assert calls == ["subgoal", "motor"]
    assert Worker.packets[0]["control_mode"] == "hierarchical"
    assert all(p["action"] == [-.5, 0., 0., 1.] for p in Worker.packets[1:])
    trace = [json.loads(line) for line in (tmp_path / "pick-0-trace.jsonl").read_text().splitlines()]
    assert trace[0]["subgoal"]["choice"] == "approach"
    assert trace[0]["motor_input"]["target_minus_hand_mm"][0] > 0
    assert all("subgoal" not in r for r in trace[1:])


def test_late_subgoal_does_not_start_motor_request_or_execute(tmp_path, monkeypatch):
    clock = [0.]
    monkeypatch.setattr(e.time, "monotonic", lambda: clock[0])
    calls = policy_factory(monkeypatch, clock=clock)
    row = episode(tmp_path, arguments(tmp_path))
    assert (row["steps"], row["model_calls"], row["status"]) == (0, 1, "timeout")
    assert calls == ["subgoal"]


def test_low_probability_subgoal_stops_before_motor_request(tmp_path, monkeypatch):
    calls = policy_factory(monkeypatch, confidence=.1)
    row = episode(tmp_path, arguments(tmp_path, threshold=.5))
    assert (row["steps"], row["model_calls"], row["status"]) == (0, 1, "uncertain")
    assert calls == ["subgoal"]


@pytest.mark.parametrize("provider", ["jev", "chat"])
def test_both_providers_use_identical_two_level_request_contract(tmp_path, monkeypatch, provider):
    import httpx
    sent = []
    def post(self, url, **kwargs):
        body = kwargs["json"]; sent.append(body)
        is_subgoal = len(sent) == 1
        choices = {"action": "approach"} if is_subgoal else dict(x="positive", y="positive", z="hold", gripper="open")
        if provider == "jev":
            response = {"answers": {name: {"choice": choices[name], "probabilities": {
                option: 1. if option == choices[name] else 0. for option in spec["criteria"]}}
                for name, spec in body["questions"].items()}}
        else:
            content = {"choice": "approach"} if is_subgoal else choices
            response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]}
        return httpx.Response(200, request=httpx.Request("POST", url), json={"model": "fixture", **response})
    monkeypatch.setattr(policies.DecisionPolicy, "_post", post)
    monkeypatch.setattr(e, "Worker", Worker)
    connection = {"url": "https://fixture.invalid/v1", "key": "fixture-only", "model": "fixture", "json_mode": True}
    row = episode(tmp_path, arguments(tmp_path, policy=provider, _connection=connection, max_steps=2))
    assert row["steps"] == 2 and row["model_calls"] == 2 and len(sent) == 2
    assert "fixture-only" not in json.dumps(row)
