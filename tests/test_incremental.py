"""Motor choice generation must not smuggle a task planner into model inputs."""
import copy

import numpy as np
import pytest

from embodied_jev.incremental import baseline_choice, incremental_candidates, planning_state
from embodied_jev.physics import RobotWorld


def test_motor_menu_ignores_goal_object_success_and_grasp_state():
    first = {"tcp": [.43, -.08, .22], "object": [.41, -.17, .02],
             "destination": [.43, .18, .026], "held": False, "success": False}
    second = {**first, "object": [.57, .27, .3], "destination": [.3, -.25, .1],
              "held": True, "success": True, "task": "A completely different goal"}
    a, b = incremental_candidates(first), incremental_candidates(second)
    assert [item.serialise() for item in a] == [item.serialise() for item in b]
    assert len(a) == len({item.id for item in a}) == 21
    assert all(item.admitted and item.phase == "incremental" for item in a)
    assert {item.id for item in a} == {
        f"{axis}_{sign}_{step}" for axis in "xyz" for sign in ("pos", "neg") for step in (40, 10, 2)
    } | {"open", "close", "hold"}
    for item in a:
        delta = np.asarray(item.target) - first["tcp"]
        if item.id in {"open", "close", "hold"}:
            assert not np.any(delta)
        else:
            assert np.count_nonzero(delta) == 1
            assert np.max(np.abs(delta)) == pytest.approx(int(item.id.rsplit("_", 1)[1]) / 1000)
            assert item.seconds == .45 and item.gripper is None
    assert next(item for item in a if item.id == "open").seconds == .65
    assert next(item for item in a if item.id == "hold").seconds == .3


def test_boundary_rejection_keeps_the_exact_step_and_opposite_choices():
    menu = {item.id: item for item in incremental_candidates({"tcp": [.649, -.319, .021]})}
    assert len(menu) == 21
    assert not menu["x_pos_2"].admitted
    assert menu["x_pos_2"].target[0] == pytest.approx(.651)
    assert not menu["y_neg_2"].admitted and not menu["z_neg_2"].admitted
    assert "not clipped" in menu["x_pos_2"].rejection
    assert all(menu[key].admitted for key in ("x_neg_2", "x_neg_10", "x_neg_40", "y_pos_2", "z_pos_2", "open", "close", "hold"))


@pytest.mark.parametrize("tcp", [[.4, .1], [.4, .1, float("nan")], [.4, .1, float("inf")]])
def test_invalid_tcp_is_rejected(tcp):
    with pytest.raises(ValueError, match="three finite coordinates"):
        incremental_candidates({"tcp": tcp})


def test_planning_history_has_signed_changes_rejections_and_short_public_intent():
    before = {"task": "place object", "tcp": [.4, 0., .2], "object": [.4, 0., .02], "held": False}
    after = {**before, "tcp": [.39, .002, .2], "object": [.4, .01, .02]}
    history = [{"cycle": cycle, "before": before, "after": after,
                "action": {"id": "x_neg_10", "target": [.39, 0., .2], "gripper": None, "seconds": .45},
                "executed": cycle != 7, "rejection": "collision" if cycle == 7 else None,
                "decision": {"intent": "p" * 260, "visual_evidence": "red object is visible",
                             "private_reasoning": "must never be retained"}}
               for cycle in range(8)]
    state = planning_state(after, history)
    recent = state["recent_outcomes"]
    assert [row["cycle"] for row in recent] == list(range(2, 8))
    assert recent[0]["tcp_delta_m"] == [-.01, .002, 0.]
    assert recent[0]["object_delta_m"] == [0., .01, 0.]
    assert recent[-1]["executed"] is False and recent[-1]["rejection"] == "collision"
    assert len(state["previous_intention"]["intent"]) == 240
    assert "private_reasoning" not in str(state)
    after["tcp"][0] = .6
    assert state["observation"]["tcp"][0] == .39


def test_image_only_evidence_never_inherits_geometry_or_rollout_truth():
    calibration = {"position": [0., 0., 1.], "fx": 400., "coordinate_frame": "world"}
    observation = {"task": "place red object", "source": "direct camera",
                   "tcp": [.4, -.1, .2], "gripper": "open", "held": False,
                   "finger_contacts": [], "support_contact": False,
                   "perception": {"source": "vision", "capture_id": 3, "objects": [],
                                  "calibration": calibration, "width": 640, "height": 480}}
    history = [{"before": {**observation, "object": [.4, -.1, .02]},
                "after": {**observation, "destination": [.4, .2, .02]},
                "action": {"id": "hold", "target": observation["tcp"], "preview": {"target_error_m": .02}}}]
    state = planning_state(observation, history)
    assert state["observation"]["perception"]["calibration"] == calibration
    assert not {"object", "destination", "relative_geometry", "success", "scene_config"} & state["observation"].keys()
    assert "destination" not in state["recent_outcomes"][0]["after"]
    assert "object_delta_m" not in state["recent_outcomes"][0]
    assert "target_error_m" not in str(state)
    # Even accidental legacy fields cannot cross an explicitly direct-vision boundary.
    polluted = {**observation, "object": [9, 9, 9], "destination": [8, 8, 8],
                "success": True, "scene_config": {"source_xy": [9, 9]}}
    polluted["perception"] = {**observation["perception"], "objects": [{"position": [9, 9, 9]}]}
    assert planning_state(polluted, history) == state
    with pytest.raises(ValueError, match="requires measured"):
        baseline_choice(observation, incremental_candidates(observation))


def test_rgbd_evidence_retains_measured_barrier_without_skill_height_guidance():
    observation = {"tcp": [.4, -.1, .2], "object": [.41, -.16, .021],
                   "destination": [.45, .2, .026], "visual_barrier": [.43, 0., .11],
                   "relative_geometry": {"travel_tcp_height_m": .22}, "success": False,
                   "perception": {"source": "rgbd", "objects": [{"id": "object", "position": [.41, -.16, .021]}]}}
    state = planning_state(observation, [])
    assert state["observation"]["visual_barrier"] == [.43, 0., .11]
    assert state["observation"]["object"] == observation["object"]
    assert "travel_tcp_height_m" not in str(state)
    assert "success" not in state["observation"]


def test_explicit_baseline_quantizes_script_target_without_changing_the_menu():
    observation = RobotWorld().observe()
    menu = incremental_candidates(observation)
    original = copy.deepcopy([item.serialise() for item in menu])
    choice = baseline_choice(observation, menu)
    assert choice in {item.id for item in menu if item.admitted}
    assert [item.serialise() for item in menu] == original
    chosen = next(item for item in menu if item.id == choice)
    delta = np.asarray(chosen.target) - observation["tcp"]
    assert np.count_nonzero(delta) == 1
