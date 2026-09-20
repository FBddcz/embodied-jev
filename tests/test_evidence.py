from embodied_jev.evidence import compact_observation, decision_state, choice_messages
from embodied_jev.physics import RobotWorld
from embodied_jev.planning import candidates


def test_evidence_tracks_observed_motion_without_recommending_an_action():
    world = RobotWorld()
    before = world.observe()
    action = candidates(world, "approach", preview=False)[0]
    for _ in world.motion(action.target, action.gripper, action.seconds, emit=False):
        pass
    after = world.observe()
    evidence = decision_state(after, [{"phase": "approach", "action": action.serialise(), "before": before, "after": after}])
    assert evidence["observation"]["relations"]["tcp_aligned_with_object_xy"]
    assert not evidence["observation"]["relations"]["tcp_at_object_height"]
    assert evidence["observation"]["object_held"] == after["held"]
    assert evidence["recent_outcomes"][0]["tcp_displacement_m"] > .03
    assert "recommended_action" not in str(evidence)
    assert "after" not in evidence["recent_outcomes"][0]


def test_custom_connection_test_evidence_is_preserved():
    evidence = {"tcp": [.4, 0, .2]}
    assert compact_observation(evidence) == evidence
    spec = {"instructions": "Choose", "criteria": {"left": "Move left", "hold": "Wait"}}
    messages = choice_messages(decision_state(evidence, []), spec)
    import json
    options = json.loads(messages[1]["content"])["options"]
    assert options == [{"letter": "A", "description": "Move left"}, {"letter": "B", "description": "Wait"}]
