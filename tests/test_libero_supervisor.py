"""Candidate fidelity, temporal grounding, sensor rights and execution budgets."""
import base64
import copy
import hashlib
import json
import math
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from embodied_jev import libero_supervisor as supervisor
from embodied_jev.benchmark_worker import image_packet
from embodied_jev.libero_policy import ModelClient
from embodied_jev.policies import DecisionPolicy


def camera(depth=.5, shade=0):
    raw = np.full((128, 128), depth, dtype="<f4").tobytes()
    return {**image_packet(np.full((128, 128, 3), shade, dtype=np.uint8)),
            "depth_encoding": "float32-le-metres", "depth": base64.b64encode(raw).decode(),
            "depth_sha256": hashlib.sha256(raw).hexdigest(),
            "intrinsics": [[100, 0, 64], [0, 100, 64], [0, 0, 1]],
            "camera_to_world": np.eye(4).tolist()}


def observation(step=0):
    return {"task": "close the microwave", "step": step, "tcp": [.1, .1, .6],
            "quaternion_xyzw": [0, 0, 0, 1], "gripper_qpos": [.04, -.04],
            "frame": "world; metres; TCP = robot0_eef_pos"}


def candidates():
    def candidate(cid, delta, kind):
        return {"id": cid, "kind": kind, "expected_effect": "A visible object movement",
                "when_use": "The contact side is visible",
                "plan": {"stage": cid, "intent": "Move to the selected contact side",
                         "visual_evidence": "The door edge is visible",
                         "target": {"kind": "relative", "delta_m": delta},
                         "rotation_delta": [0, 0, 0], "gripper": "close", "max_motor_steps": 4}}
    return {"assessment": {"progress": "unknown", "target_visibility": "partial",
                           "contact": "unknown", "evidence": "The wrist obscures the contact patch"},
            "candidates": [candidate("c0", [.15, 0, 0], "advance"),
                           candidate("c1", [0, -.15, 0], "reposition")]}


def test_temporal_images_keep_labels_and_ground_only_current_depth():
    old = {view: camera(.25, shade=10) for view in ("external", "wrist")}
    now = {view: camera(.75, shade=40) for view in ("external", "wrist")}
    previous_rgb = {view: supervisor.annotated_image(packet, observation()["tcp"])
                    for view, packet in old.items()}
    answer = candidates()
    answer["candidates"][0]["plan"]["target"] = {
        "kind": "pixel", "camera": "external", "pixel": [64, 64], "offset_m": [0, 0, .1]}
    captured = {}

    def request(stage, state, **kwargs):
        captured.update(stage=stage, state=state, **kwargs)
        return answer

    bundle, current_rgb, _ = supervisor.plan_candidates(
        SimpleNamespace(request=request), observation(20), now, previous_rgb, [])
    assert set(captured["images"]) == {"BEFORE_external", "BEFORE_wrist", "NOW_external", "NOW_wrist"}
    assert captured["images"]["BEFORE_external"] == previous_rgb["external"]
    assert captured["images"]["NOW_external"] == current_rgb["external"]
    assert current_rgb["external"] != previous_rgb["external"]
    plan = bundle["candidates"][0]["plan"]
    np.testing.assert_allclose(plan["target_xyz"], [0, 0, .85])
    assert plan["grounding"]["depth_sha256"] == now["external"]["depth_sha256"]
    assert plan["grounding"]["observation_step"] == 20
    assert bundle["assessment"]["contact"] == "unknown"
    assert "visual estimate" in bundle["assessment"]["source"]


@pytest.mark.parametrize("change", ["invalid_id", "duplicate_id", "oversized_move", "oversized_lifetime", "nan"])
def test_invalid_candidate_is_rejected_before_execution(change):
    answer = candidates()
    candidate = answer["candidates"][1]
    if change == "invalid_id":
        candidate["id"] = "hidden"
    elif change == "duplicate_id":
        candidate["id"] = "c0"
    elif change == "oversized_move":
        candidate["plan"]["target"]["delta_m"][0] = .21
    elif change == "oversized_lifetime":
        candidate["plan"]["max_motor_steps"] = 5
    else:
        candidate["plan"]["rotation_delta"][2] = float("nan")
    with pytest.raises(ValueError):
        supervisor.resolve_candidates(answer, observation(), {"external": camera()})


def test_jev_selected_candidate_is_not_replaced_by_probability_argmax():
    bundle = supervisor.resolve_candidates(candidates(), observation(), {"external": camera()})
    options = supervisor.choice_options(bundle)
    probabilities = dict.fromkeys(options, 0.)
    probabilities.update(c0_normal=.6, c1_cautious=.4)
    client = SimpleNamespace(provider="jev", request=lambda *a, **kw: {
        "selection": {"choice": "c1_cautious", "probabilities": probabilities}})
    candidate, profile, selected, observed = supervisor.select_candidate(client, {}, bundle)
    assert selected == "c1_cautious" and candidate["id"] == "c1" and profile == "cautious"
    assert observed == probabilities


@pytest.fixture
def run_mock_episode(monkeypatch, tmp_path):
    """Use real request parsing/metering and episode logic; fake only transport/physics."""
    def run(*, selections=("c1_cautious",), answer=None, max_steps=20,
            max_calls=80, expire_after=None, translate=True, rotate=False):
        workers, clients, requests = [], [], []
        answer = answer or candidates()
        choice_index = 0

        class FakeWorker:
            def __init__(self, *args, **kwargs):
                self.steps, self.actions, self.closed = 0, [], False
                self.xyz, self.angle = np.array(observation()["tcp"]), 0.
                workers.append(self)

            def request(self, request, **kwargs):
                if request["command"] == "step":
                    action = request["action"]
                    self.actions.append(list(action))
                    self.steps += 1
                    if translate:
                        self.xyz += np.array(action[:3]) * .005
                    if rotate:
                        self.angle += action[5] * .05
                obs = {**observation(self.steps), "tcp": self.xyz.tolist(),
                       "quaternion_xyzw": [0, 0, math.sin(self.angle/2), math.cos(self.angle/2)]}
                images = {view: camera(shade=self.steps) for view in ("external", "wrist")}
                return {"observation": obs, "images": images,
                        "metadata": {"initial_success": False, "private_truth": "EVAL_ONLY_CANARY"},
                        "success": False, "truncated": self.steps >= max_steps,
                        "object_pose": "EVAL_ONLY_CANARY", "reward": "EVAL_ONLY_CANARY"}

            def close(self):
                self.closed = True

        def client_factory(*args, **kwargs):
            client = ModelClient(*args, **kwargs)
            clients.append(client)
            return client

        def post(policy, url, **kwargs):
            nonlocal choice_index
            payload = kwargs["json"]
            if policy.provider == "chat":
                state = json.loads(payload["messages"][1]["content"][0]["text"])
                stage = "vision_candidates" if "comparison" in state else "candidate_selection"
            else:
                state, stage = payload["state"], "candidate_selection"
            requests.append({"provider": policy.provider, "stage": stage, "state": copy.deepcopy(state),
                             "payload": copy.deepcopy(payload)})
            if stage == "vision_candidates":
                result = copy.deepcopy(answer)
                body = {"model": "gpt-6-astra-test", "usage": {"prompt_tokens": 100, "completion_tokens": 10},
                        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}
            else:
                selected = selections[min(choice_index, len(selections)-1)]
                choice_index += 1
                if policy.provider == "jev":
                    options = payload["questions"]["selection"]["criteria"]
                    result = {"selection": {"choice": selected,
                                           "probabilities": {key: float(key == selected) for key in options}}}
                    body = {"model": "jev-1.13.0", "usage": {"input_tokens": 50, "output_tokens": 5}, "answers": result}
                else:
                    body = {"model": "gpt-6-astra-test", "usage": {"prompt_tokens": 50, "completion_tokens": 5},
                            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"selection": selected})}}]}
            if stage == expire_after:
                clients[-1].budget.deadline = 0
            return httpx.Response(200, json=body, request=httpx.Request("POST", "https://example.test"))

        monkeypatch.setattr(supervisor, "Worker", FakeWorker)
        monkeypatch.setattr(supervisor, "ModelClient", client_factory)
        monkeypatch.setattr(DecisionPolicy, "_post", post)
        args = SimpleNamespace(budget_mode="bounded", worker_python="unused", libero_root="unused",
                               camera_size=128, max_steps=max_steps, max_calls=max_calls, timeout=1200,
                               max_usd=20, action_repeat=5, action_scale=.5)
        connections = {"chat": {"url": "https://example.test", "model": "gpt-6-astra", "key": "fake-api-credential"},
                       "jev": {"url": "https://example.test", "model": "jev-1.13.0", "key": "fake-api-credential"}}
        row = supervisor.run_episode(args, {"id": "test-case"}, "gpt6-jev", tmp_path / "episode", connections)
        return row, workers[0], requests
    return run


def test_one_selection_controls_multiple_servo_blocks_without_changing_candidate(run_mock_episode):
    row, worker, requests = run_mock_episode()
    assert [request["stage"] for request in requests] == ["vision_candidates", "candidate_selection"]
    assert row["steps"] == 20 and len(row["decisions"]) == 4
    assert {decision["selection"] for decision in row["decisions"]} == {"c1_cautious"}
    assert all(action[0] == 0 and -.2 <= action[1] < 0 and action[2] == 0 for action in worker.actions)
    assert all(action[-1] == 1 for action in worker.actions)
    assert row["metrics"]["requests"] == 2
    assert row["metrics"]["estimated_usd"] == pytest.approx(.0015 + 50*.042/1e6)
    assert row["metrics"]["models"] == ["gpt-6-astra-test", "jev-1.13.0"]
    assert worker.closed


def test_policy_requests_never_receive_evaluation_truth_or_depth_bytes(run_mock_episode):
    row, _, requests = run_mock_episode()
    assert "EVAL_ONLY_CANARY" not in json.dumps(requests)
    selection = requests[1]["state"]
    assert set(selection["measured_robot"]) == set(observation())
    assert selection["contact_sensor"] == "unavailable; visual contact is an estimate"
    assert selection["visual_assessment"]["contact"] == "unknown"
    assert not any(key in selection for key in ("images", "depth", "success", "reward", "object_pose"))
    assert all("depth" not in candidate["plan"]["grounding"] for candidate in selection["candidate_details"])
    assert row["status"] == "step_budget"


def test_reobserve_keeps_closed_gripper_and_observes_new_steps(run_mock_episode):
    answer = candidates()
    answer["candidates"][1]["plan"]["max_motor_steps"] = 1
    row, worker, requests = run_mock_episode(selections=("c1_cautious", "reobserve", "c0_normal"),
                                             answer=answer, max_steps=15)
    assert [checkpoint["step"] for checkpoint in row["checkpoints"]] == [0, 5, 10]
    assert worker.actions[5:10] == [[0., 0., 0., 0., 0., 0., 1.]] * 5
    assert requests[4]["state"]["robot"]["step"] == 10
    assert row["checkpoints"][2]["previous_checkpoint_index"] == 1
    assert row["decisions"][1]["selection"] == "reobserve"


def test_checkpoint_keeps_the_actual_request_state_without_later_evidence(run_mock_episode):
    answer = candidates()
    answer["candidates"][1]["plan"]["max_motor_steps"] = 1
    row, _, requests = run_mock_episode(answer=answer, max_steps=15)
    plans = [request for request in requests if request["stage"] == "vision_candidates"]
    for checkpoint, sent in zip(row["checkpoints"], plans):
        assert checkpoint["planner_state"] == sent["state"]
    assert "observed_after" not in plans[1]["state"]["history"][-1]
    assert row["checkpoints"][1]["selector_state"]["recent_results"][-1]["observed_after"]["observation_step"] == 5


@pytest.mark.parametrize("late_stage", ["vision_candidates", "candidate_selection"])
def test_late_response_cannot_trigger_any_physical_action(run_mock_episode, late_stage):
    row, worker, requests = run_mock_episode(expire_after=late_stage)
    assert row["status"] == "time_budget" and row["steps"] == 0
    assert not worker.actions and not row["decisions"]
    assert row["metrics"]["requests"] == len(requests)


def test_arrived_waypoint_still_executes_new_gripper_command(run_mock_episode):
    answer = candidates()
    answer["candidates"][1]["plan"]["target"]["delta_m"] = [0, 0, 0]
    row, worker, _ = run_mock_episode(answer=answer, max_steps=5)
    assert row["steps"] == 5
    assert worker.actions == [[0., 0., 0., 0., 0., 0., 1.]] * 5


def test_admitted_last_request_can_execute_without_requesting_again(run_mock_episode):
    row, worker, requests = run_mock_episode(max_calls=2)
    assert len(requests) == 2
    assert row["steps"] == 20 and len(worker.actions) == 20


def test_rotational_progress_is_not_mistaken_for_tcp_stall(run_mock_episode):
    answer = candidates()
    answer["candidates"][1]["plan"]["target"]["delta_m"] = [0, 0, 0]
    answer["candidates"][1]["plan"]["rotation_delta"] = [0, 0, .4]
    row, worker, requests = run_mock_episode(answer=answer, translate=False, rotate=True)
    assert len(requests) == 2
    assert len(row["checkpoints"]) == 1 and row["steps"] == 20
    assert all(action[5] > 0 for action in worker.actions)
