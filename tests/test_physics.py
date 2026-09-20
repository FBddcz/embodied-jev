import json

import numpy as np
import pytest

from embodied_jev.physics import RobotWorld
from embodied_jev.planning import candidates
from embodied_jev.runtime import run_headless


@pytest.mark.parametrize("task", ["transfer", "stack", "barrier"])
def test_contact_based_task_and_replay(task):
    session = run_headless(task, seed=7)
    assert session.status == "completed", session.message
    assert session.world.success()
    assert session.world.unsafe_contacts == 0
    assert session.policy.calls == 0
    assert any(row["after"]["held"] for row in session.history)
    assert session.last_frame["observation"]["support_contact"]
    assert session.last_frame["observation"]["gripper"] == "open"
    assert session.last_frame["observation"]["stable_seconds"] >= .4
    assert session.world.model.neq == 1  # Upstream finger equality, no object attachment.
    for index in [0, len(session.frames) // 2, len(session.frames) - 1]:
        frame = session.replay_frame(index)
        assert frame["qpos"] == session.frames[index]["qpos"]
        assert frame["observation"] == session.frames[index]["observation"]
    np.testing.assert_allclose(session.replay_frame(len(session.frames) - 1)["positions"],
                               session.last_frame["positions"], atol=1e-5)
    assert json.loads(json.dumps(session.export()))["success"] is True


def test_candidate_previews_are_isolated():
    world = RobotWorld("barrier")
    qpos, ctrl = world.data.qpos.copy(), world.data.ctrl.copy()
    before = world.observe()
    options = candidates(world, "approach", preview=True)
    assert any(c.admitted and c.id != "hold" for c in options)
    assert all(c.preview for c in options)
    np.testing.assert_array_equal(world.data.qpos, qpos)
    np.testing.assert_array_equal(world.data.ctrl, ctrl)
    assert world.observe() == before


@pytest.mark.parametrize("target", [[2, 0, .2], [.4, 0, float("nan")], [.4, .1]])
def test_invalid_target_does_not_advance_world(target):
    world = RobotWorld()
    qpos = world.data.qpos.copy()
    with pytest.raises(ValueError):
        list(world.motion(target))
    np.testing.assert_array_equal(qpos, world.data.qpos)


def test_motion_records_last_partial_chunk():
    world = RobotWorld()
    frames = list(world.motion(seconds=.65))
    assert frames[-1]["time"] == float(world.data.time)
    assert frames[-1]["qpos"] == world.data.qpos.tolist()
