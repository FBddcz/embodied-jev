import copy
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from embodied_jev.physics import RobotWorld, TASKS, build_scene
from embodied_jev.runtime import run_headless
from embodied_jev.scenarios import SCENE_DEFAULTS, validate_scene_config


@pytest.mark.parametrize("task", ["transfer", "stack", "barrier"])
def test_normalized_defaults_preserve_seeded_scene_and_template_goal(task):
    normalized = validate_scene_config(task)
    assert validate_scene_config(task, normalized) == normalized
    xml, target, source = build_scene(task, 7)
    normalized_xml, normalized_target, normalized_source = build_scene(task, 7, normalized)
    assert normalized_xml == xml
    np.testing.assert_array_equal(target, normalized_target)
    np.testing.assert_array_equal(source, normalized_source)
    np.testing.assert_array_equal(source[:2], np.array([.43, -.17]) + np.random.default_rng(7).uniform(-.025, .025, 2))
    renamed = RobotWorld(task, 7, {"name": "我的场景"})
    original = RobotWorld(task, 7)
    assert renamed.scene_hash == original.scene_hash
    assert renamed.observe()["task"] == TASKS[task]["goal"]
    assert not renamed.success()


def test_explicit_source_is_physical_and_does_not_apply_seed_jitter():
    config = {"source_xy": [.42, -.18], "target_xy": [.46, .20]}
    first_xml, _, first_source = build_scene("transfer", 0, config)
    second_xml, _, second_source = build_scene("transfer", 91, config)
    assert first_xml == second_xml
    np.testing.assert_array_equal(first_source, second_source)
    np.testing.assert_array_equal(first_source[:2], config["source_xy"])
    world = RobotWorld("transfer", 0, config)
    np.testing.assert_allclose(world.cube[:2], config["source_xy"], atol=.001)
    np.testing.assert_array_equal(world.target[:2], config["target_xy"])
    assert world.scene_hash != RobotWorld("transfer", 0).scene_hash


@pytest.mark.parametrize("task", ["transfer", "stack", "barrier"])
def test_target_support_and_all_tray_walls_move_together(task):
    target = [.46, .20]
    original_xml, _, _ = build_scene(task, 0)
    moved_xml, _, _ = build_scene(task, 0, {"target_xy": target})
    original = ET.fromstring(original_xml).find("worldbody").findall("geom")
    moved = ET.fromstring(moved_xml).find("worldbody").findall("geom")
    assert len(original) == len(moved)
    changed = 0
    for before, after in zip(original, moved):
        offset = np.zeros(3)
        if before.get("name") not in {"table", "barrier"}:
            offset[:2] = np.asarray(target) - SCENE_DEFAULTS["target_xy"]
            changed += 1
        np.testing.assert_allclose(np.fromstring(after.get("pos"), sep=" "),
                                   np.fromstring(before.get("pos"), sep=" ") + offset)
        assert after.get("size") == before.get("size")
    assert changed == (1 if task == "stack" else 5)


def test_barrier_height_changes_physics_from_the_table_surface():
    world = RobotWorld("barrier", scene_config={"barrier_height": .14})
    geom = world.model.geom("barrier")
    assert geom.pos[2] == pytest.approx(.07)
    assert geom.size[2] == pytest.approx(.07)
    assert geom.pos[2] - geom.size[2] == pytest.approx(0)
    assert world.scene_hash != RobotWorld("barrier").scene_hash
    assert world.scene()["scene_config"]["barrier_height"] == .14
    assert world.observe()["scene_config"]["barrier_height"] == .14
    assert not world.success()


@pytest.mark.parametrize("task,config", [
    ("transfer", {"source_xy": [True, -.18]}),
    ("transfer", {"source_xy": [.42, float("nan")]}),
    ("transfer", {"source_xy": [.29, -.18]}),
    ("transfer", {"target_xy": [.46, .29]}),
    ("transfer", {"target_xy": [.46]}),
    ("transfer", {"target_xy": None}),
    ("transfer", {"source_xy": [.43, .18]}),
    ("transfer", {"name": " "}),
    ("transfer", {"name": "x" * 81}),
    ("transfer", {"goal": "pretend the task succeeded"}),
    ("transfer", {"barrier_height": .11}),
    ("barrier", {"barrier_height": .161}),
    ("barrier", {"barrier_height": float("inf")}),
    ("barrier", {"source_xy": [.43, -.04]}),
    ("barrier", {"target_xy": [.43, .08]}),
    ("barrier", {"source_xy": [.30, -.26], "target_xy": [.55, -.26]}),
    ("barrier", {"source_xy": [.58, -.18], "target_xy": [.58, .18]}),
])
def test_invalid_or_colliding_scene_parameters_are_rejected(task, config):
    with pytest.raises(ValueError):
        validate_scene_config(task, config)


def test_endpoint_precheck_rejects_gripper_collision_even_when_objects_are_separate():
    # The cube clears the wall, but an open finger at the grasp pose intersects it.
    config = {"source_xy": [.43, -.05]}
    validate_scene_config("barrier", config)
    with pytest.raises(ValueError, match="关键位姿.*相交"):
        RobotWorld("barrier", scene_config=config)


def test_presets_metadata_and_clones_do_not_mutate_builtin_or_live_geometry():
    defaults = copy.deepcopy(SCENE_DEFAULTS)
    tasks = copy.deepcopy(TASKS)
    config = {"name": "偏右目标", "source_xy": [.42, -.18], "target_xy": [.46, .20]}
    world = RobotWorld("transfer", scene_config=config)
    config["source_xy"][0] = .5
    assert world.scene_config["source_xy"] == [.42, -.18]
    observed = world.observe()
    observed["scene_config"]["target_xy"][0] = .5
    assert world.scene_config["target_xy"] == [.46, .20]
    shadow = world.clone()
    shadow.scene_config["source_xy"][0] = .5
    shadow.target[0] = .5
    shadow.source[0] = .5
    assert world.scene_config["source_xy"] == [.42, -.18]
    assert world.target[0] == .46 and world.source[0] == .42
    assert SCENE_DEFAULTS == defaults and TASKS == tasks
    assert world.scene()["scene_name"] == "偏右目标"


def test_custom_scene_completes_a_real_contact_based_baseline_episode():
    config = {"name": "移动源与目标", "source_xy": [.42, -.18], "target_xy": [.46, .20]}
    session = run_headless("transfer", seed=7, scene_config=config, max_cycles=12)
    assert session.status == "completed", session.message
    assert session.world.success() and session.world.unsafe_contacts == 0
    assert session.policy.calls == 0
    assert any(record["after"]["held"] for record in session.history)
    assert session.world.max_lift > .15
    exported = session.export()
    assert exported["scene_config"] == validate_scene_config("transfer", config)
    assert exported["success"] and exported["history"][-1]["after"]["support_contact"]
