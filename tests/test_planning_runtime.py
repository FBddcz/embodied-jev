"""Exercise closed-loop runtime ownership using fake models and cached cameras.

Physics remains real, including execution and replay; no test invokes a model
endpoint or a renderer. These checks establish control/data flow, not planning
competence of any particular model.
"""
import copy
import hashlib
import io
import json
import threading
import zipfile

import numpy as np
import pytest

import embodied_jev.incremental as incremental
import embodied_jev.runtime as runtime
from embodied_jev.perception import PerceptionUnavailable, encode_png
from embodied_jev.physics import RobotWorld


class ScriptedPolicy:
    def __init__(self, provider, connection=None):
        self.provider = provider
        self.model = "fixture-planning-model"
        self.calls = self.tokens = self.output_tokens = 0
        self.latencies = []
        self.last_input = None
        self.seen = []
        self.choices = ["x_pos_10"]
        self.closed = False
        self.entered = self.release = None

    def choose(self, *args, **kwargs):
        raise AssertionError("Incremental execution called the skill policy")

    def choose_plan(self, state, question, options, baseline_choice=None, image=None):
        self.calls += 1
        images = copy.deepcopy(image)
        self.seen.append({"state": copy.deepcopy(state), "options": copy.deepcopy(options),
                          "baseline_choice": baseline_choice, "images": images})
        self.last_input = {"state": copy.deepcopy(state), "decision": {"criteria": copy.deepcopy(options)}}
        if images:
            self.last_input["images"] = [{"view": row["view"], "capture_id": row["capture_id"],
                                           "sha256": hashlib.sha256(row["rgb"]).hexdigest()}
                                          for row in images]
        if self.entered:
            self.entered.set()
            assert self.release.wait(5), "The test did not release the fake model response"
        choice = self.choices[min(self.calls - 1, len(self.choices) - 1)]
        assert choice in options
        return {"choice": choice, "selected_probability": None, "probabilities": {},
                "model_call": True, "latency_ms": 1, "model": self.model,
                "intent": "Take the selected short step", "visual_evidence": "Fixture camera evidence"}

    def close(self):
        self.closed = True


class CachedImageObserver:
    """Reuse a capture at the same sim time until the runtime invalidates it."""
    def __init__(self, world, **kwargs):
        self.world = world
        self.camera_views = list(kwargs.get("camera_views", ["external", "wrist"]))
        self.captures = self.invalidations = 0
        self.last_time = self.observation = self.snapshot = None
        self.closed = False

    def observe(self):
        now = float(self.world.data.time) - self.world.start_time
        if self.last_time == now and self.observation is not None:
            return copy.deepcopy(self.observation)
        self.captures += 1
        truth = self.world.observe()
        metadata = {"source": "vision", "capture_id": f"frame-{self.captures}",
                    "status": "ready", "sim_time": now, "objects": [],
                    "camera_views": list(self.camera_views)}
        observation = {key: copy.deepcopy(truth[key]) for key in
                       ("tcp", "gripper", "finger_contacts", "held", "support_contact", "sim_seconds")}
        observation.update(task="Move the red cube to the target", source="fixture RGB + proprioception",
                           perception=metadata)
        views = {view: {"rgb": encode_png(np.full((2, 2, 3), self.captures * 20 + (view == "wrist"), dtype=np.uint8))}
                 for view in self.camera_views}
        self.snapshot = {"metadata": copy.deepcopy(metadata), "views": views,
                         "rgb": views[self.camera_views[0]]["rgb"]}
        self.last_time, self.observation = now, observation
        return copy.deepcopy(observation)

    def camera_snapshot(self):
        return copy.deepcopy(self.snapshot)

    def invalidate(self):
        self.invalidations += 1
        self.last_time = self.observation = None

    def close(self):
        self.closed = True


@pytest.fixture
def sessions(monkeypatch):
    monkeypatch.setattr(runtime, "DecisionPolicy", ScriptedPolicy)
    monkeypatch.setattr("embodied_jev.perception.ImageObserver", CachedImageObserver)
    created = []

    def make(**kwargs):
        session = runtime.Session(**{"provider": "chat", "control_mode": "incremental",
                                     "preview": False, "speed": 0, "max_cycles": 1, **kwargs})
        created.append(session)
        return session

    yield make
    for session in created:
        session.stop()
        if session.worker:
            session.worker.join(5)
            assert not session.worker.is_alive()


def run_to_budget(session):
    session.start()
    session.worker.join(10)
    assert not session.worker.is_alive(), "Runtime did not finish its bounded action budget"
    assert session.status == "exhausted", session.message
    return session


def forbidden_skill(*args, **kwargs):
    raise AssertionError("A model-controlled motor decision consulted a scripted skill")


def test_model_choice_bypasses_skills_and_executes_the_exact_selected_target(sessions, monkeypatch):
    for name in ("eligible_phases", "baseline_phase", "candidates", "phase_options"):
        monkeypatch.setattr(runtime, name, forbidden_skill)
    monkeypatch.setattr(incremental, "baseline_choice", forbidden_skill)
    session = sessions()
    session.policy.choices = ["y_neg_10"]
    before = session.world.position.copy()
    original_motion = RobotWorld.motion
    actual = []

    def track_motion(world, target=None, gripper=None, seconds=.6, emit=True):
        if world is session.world:
            actual.append((list(target), gripper, seconds))
        yield from original_motion(world, target, gripper, seconds, emit)

    monkeypatch.setattr(RobotWorld, "motion", track_motion)
    run_to_budget(session)
    expected = np.asarray(session.history[0]["before"]["tcp"]) + [0, -.01, 0]
    assert len(actual) == 1
    np.testing.assert_allclose(actual[0][0], expected, atol=1e-8)
    assert actual[0][1] is None
    assert session.world.position[1] < before[1] - .006
    assert session.history[0]["action"]["id"] == "y_neg_10"
    assert session.history[0]["action"]["delta_xyz"] == [0, -.01, 0]
    assert session.history[0]["executed"] is True
    assert session.policy.seen[0]["baseline_choice"] is None
    assert session.policy.calls == 1 and session.last_intent is None
    assert session.snapshot()["control_mode"] == session.export()["control_mode"] == "incremental"


@pytest.mark.parametrize("provider,control_mode", [
    ("baseline", "incremental"), ("local", "incremental"), ("jev", "incremental"),
    ("minicpm", "incremental"), ("chat", "skills"), ("claude", "skills"),
])
def test_incompatible_vision_modes_fail_before_allocating_any_resources(monkeypatch, provider, control_mode):
    allocations = []

    def resource(*args, **kwargs):
        allocations.append(True)
        raise AssertionError("Invalid mode allocated resources")

    monkeypatch.setattr(runtime, "RobotWorld", resource)
    monkeypatch.setattr(runtime, "DecisionPolicy", resource)
    monkeypatch.setattr("embodied_jev.perception.ImageObserver", resource)
    with pytest.raises(ValueError, match="直接图像模式"):
        runtime.Session(provider=provider, control_mode=control_mode, observation_mode="vision")
    assert allocations == []


@pytest.mark.parametrize("provider", ["chat", "claude"])
def test_direct_vision_sends_both_fresh_images_and_no_object_or_target_coordinates(sessions, provider):
    session = sessions(provider=provider, observation_mode="vision")
    run_to_budget(session)
    request = session.policy.seen[0]
    state = request["state"]["observation"]
    assert {"object", "destination", "scene_config", "success", "relative_geometry"}.isdisjoint(state)
    assert state["perception"]["source"] == "vision"
    assert [image["view"] for image in request["images"]] == ["external", "wrist"]
    assert request["images"][0]["rgb"] != request["images"][1]["rgb"]
    assert {image["capture_id"] for image in request["images"]} == {state["perception"]["capture_id"]}
    for frame in session.frames:
        assert {"object", "destination"}.isdisjoint(frame["observation"])
    saved = session.export()["history"][0]["decision_inputs"]["action"]
    assert saved["state"] == request["state"]
    assert [image["view"] for image in saved["images"]] == ["external", "wrist"]
    assert all("rgb" not in image for image in saved["images"])
    assert session.observer.closed and session.policy.closed


@pytest.mark.parametrize("requested,expected", [
    (["external"], ["external"]), (["wrist"], ["wrist"]),
    (["external", "wrist"], ["external", "wrist"]),
    (["wrist", "external"], ["external", "wrist"]),
])
def test_vision_transmits_exactly_enabled_views_in_canonical_order(sessions, requested, expected):
    session = sessions(observation_mode="vision", camera_views=requested)
    run_to_budget(session)
    request = session.policy.seen[0]
    assert session.camera_views == session.observer.camera_views == expected
    assert session.snapshot()["camera_views"] == session.export()["camera_views"] == expected
    assert [image["view"] for image in request["images"]] == expected
    assert [row["view"] for row in session.history[0]["decision_inputs"]["action"]["images"]] == expected
    assert {row["view"] for row in session.camera_manifest()} == set(expected)
    assert all(image["capture_id"] == request["state"]["observation"]["perception"]["capture_id"]
               for image in request["images"])


def test_privileged_without_cameras_allocates_no_observer_and_sends_no_images(sessions, monkeypatch):
    def forbidden_observer(*args, **kwargs):
        raise AssertionError("A camera was allocated when none was enabled")

    monkeypatch.setattr("embodied_jev.perception.ImageObserver", forbidden_observer)
    monkeypatch.setattr("embodied_jev.perception.VisualObserver", forbidden_observer)
    session = sessions(observation_mode="privileged", camera_views=[])
    run_to_budget(session)
    assert session.observer is None and session.camera_snapshot() is None
    assert session.camera_views == session.camera_manifest() == session.perception_history == []
    assert session.policy.seen[0]["images"] is None
    assert {"object", "destination"} <= session.policy.seen[0]["state"]["observation"].keys()


@pytest.mark.parametrize("views", [["external"], ["wrist"], ["external", "wrist"]])
def test_privileged_camera_previews_never_become_policy_image_input(sessions, views):
    session = sessions(observation_mode="privileged", camera_views=views)
    run_to_budget(session)
    request = session.policy.seen[0]
    state = request["state"]["observation"]
    assert session.observer is not None and session.observer.camera_views == views
    assert request["images"] is None
    assert "perception" not in state and "images" not in session.history[0]["decision_inputs"]["action"]
    assert {"object", "destination"} <= state.keys()
    assert state["source"] == "MuJoCo geometry and contacts"
    assert session.snapshot()["perception"]["camera_views"] == views
    assert {row["view"] for row in session.export()["camera_manifest"]} == set(views)
    assert all("perception" not in frame["observation"] for frame in session.frames)


@pytest.mark.parametrize("mode", ["vision", "rgbd"])
def test_visual_modes_with_no_camera_fail_before_allocating_resources(monkeypatch, mode):
    allocations = []

    def resource(*args, **kwargs):
        allocations.append(True)
        raise AssertionError("Invalid empty-camera setup allocated a resource")

    monkeypatch.setattr(runtime, "RobotWorld", resource)
    monkeypatch.setattr(runtime, "DecisionPolicy", resource)
    monkeypatch.setattr("embodied_jev.perception.ImageObserver", resource)
    monkeypatch.setattr("embodied_jev.perception.VisualObserver", resource)
    with pytest.raises(ValueError, match="至少需要一种相机"):
        runtime.Session(provider="chat", control_mode="incremental", observation_mode=mode, camera_views=[])
    assert allocations == []


def test_selected_collision_is_logged_and_next_model_receives_rejection_without_fallback(sessions, monkeypatch):
    monkeypatch.setattr(incremental, "baseline_choice", forbidden_skill)
    session = sessions(preview=True, max_cycles=2)
    session.policy.choices = ["x_pos_10", "z_pos_10"]
    original_motion = RobotWorld.motion
    checked, executed = [], []

    def chosen_only_preflight(world, target=None, gripper=None, seconds=.6, emit=True):
        if world is not session.world:
            checked.append(list(target))
            if len(checked) == 1:
                world.unsafe_contacts += 1
            return
        executed.append(list(target))
        yield from original_motion(world, target, gripper, seconds, emit)

    monkeypatch.setattr(RobotWorld, "motion", chosen_only_preflight)
    run_to_budget(session)
    first, second = session.history
    assert [row["action"]["id"] for row in session.history] == ["x_pos_10", "z_pos_10"]
    assert len(checked) == 2 and len(executed) == 1
    assert first["executed"] is False and first["action"]["admitted"] is False
    assert "接触" in first["rejection"]
    assert first["before"]["tcp"] == first["after"]["tcp"]
    assert first["before"]["sim_seconds"] == first["after"]["sim_seconds"]
    outcome = session.policy.seen[1]["state"]["recent_outcomes"][0]
    assert outcome["option"] == "x_pos_10" and outcome["executed"] is False
    assert outcome["rejection"] == first["rejection"]
    assert outcome["tcp_delta_m"] == [0., 0., 0.]
    assert second["executed"] is True
    np.testing.assert_allclose(executed[0], second["action"]["target"])
    assert [event["event"] for event in session.events].count("action_rejected") == 1
    assert session.cycles == session.policy.calls == 2


def test_incremental_action_budget_stops_before_an_extra_model_call(sessions):
    session = sessions(max_cycles=2)
    session.policy.choices = ["x_pos_2", "y_pos_2"]
    run_to_budget(session)
    assert len(session.history) == session.cycles == session.policy.calls == 2
    assert session.events[-1]["event"] == "budget_exhausted"
    assert session.policy.closed


def test_cancelled_incremental_answer_never_moves_the_robot(sessions):
    session = sessions(max_cycles=5)
    session.policy.entered, session.policy.release = threading.Event(), threading.Event()
    initial = session.world.data.qpos.copy()
    session.start()
    try:
        assert session.policy.entered.wait(5)
        session.stop()
    finally:
        session.policy.release.set()
    session.worker.join(5)
    assert not session.worker.is_alive()
    assert session.status == "stopped"
    assert session.cycles == 0 and session.history == []
    np.testing.assert_array_equal(session.world.data.qpos, initial)
    assert session.policy.calls == 1 and session.policy.closed


def test_failed_perception_capture_is_archived_before_runtime_stops(sessions):
    session = sessions(observation_mode="vision", camera_views=["wrist"])
    failed_pixels = encode_png(np.full((2, 2, 3), 233, dtype=np.uint8))

    def failed_observation():
        session.observer.snapshot = {
            "metadata": {"source": "vision", "capture_id": "failed-frame", "status": "unavailable",
                         "camera_views": ["wrist"], "objects": [], "sim_time": 0},
            "views": {"wrist": {"rgb": failed_pixels}}, "rgb": failed_pixels}
        raise PerceptionUnavailable("感知失效测试")

    session.observer.observe = failed_observation
    session.start()
    session.worker.join(5)
    assert not session.worker.is_alive() and session.status == "error"
    assert session.cycles == session.policy.calls == 0
    assert session.perception_history[-1]["capture_id"] == "failed-frame"
    assert session.perception_history[-1]["status"] == "unavailable"
    frames = session.export()["camera_manifest"]
    failed = [row for row in frames if row["capture_id"] == "failed-frame"]
    assert len(failed) == 1 and failed[0]["view"] == "wrist"
    assert failed[0]["sha256"] == hashlib.sha256(failed_pixels).hexdigest()
    with zipfile.ZipFile(io.BytesIO(session.camera_archive())) as archive:
        assert archive.read(failed[0]["file"]) == failed_pixels
    assert session.observer.closed and session.policy.closed


def test_stop_during_stall_detection_preserves_cancelled_status(sessions, monkeypatch):
    monkeypatch.setattr(RobotWorld, "motion", stationary_motion)
    session = sessions(max_cycles=5)
    session.policy.choices = ["hold"]
    reached, release = threading.Event(), threading.Event()
    original_stalled = session._stalled

    def blocked_stall_detection():
        stalled = original_stalled()
        if stalled:
            reached.set()
            assert release.wait(5), "Test did not release the blocked stall check"
        return stalled

    session._stalled = blocked_stall_detection
    session.start()
    try:
        assert reached.wait(5), "Three stationary actions did not reach the stall check"
        session.stop()
    finally:
        release.set()
    session.worker.join(5)
    assert not session.worker.is_alive()
    assert session.status == "stopped" and session.cancel.is_set()
    assert session.cycles == session.policy.calls == 3
    assert session.events[-1]["event"] == "stopped"
    assert not any(event["event"] == "stalled" for event in session.events)


def test_shuffled_menu_order_depends_on_seed_and_cycle_not_task_coordinates(sessions):
    first = sessions(seed=7, shuffle_candidates=True)
    changed = sessions(seed=7, shuffle_candidates=True)
    unshuffled = sessions(seed=7, shuffle_candidates=False)
    changed.world.perturb("object_shift", [.015, 0])
    changed.world.perturb("target_shift", [.02, 0])
    for session in (first, changed, unshuffled):
        session.policy.choices = ["hold"]
        run_to_budget(session)
    a, b, c = [session.policy.seen[0] for session in (first, changed, unshuffled)]
    assert a["state"]["observation"]["object"] != b["state"]["observation"]["object"]
    assert a["state"]["observation"]["destination"] != b["state"]["observation"]["destination"]
    assert list(a["options"]) == list(b["options"])
    assert a["options"] == b["options"] == c["options"]
    assert list(a["options"]) != list(c["options"])
    assert len(a["options"]) == 21
    for description in a["options"].values():
        assert set(json.loads(description)) == {"delta_xyz", "gripper", "seconds"}


def stationary_motion(world, target=None, gripper=None, seconds=.6, emit=True):
    """Keep time fixed to expose cached-observer invalidation regressions."""
    if emit:
        yield world.frame()


def test_same_time_intervention_invalidates_camera_before_the_next_model_input(sessions, monkeypatch):
    monkeypatch.setattr(RobotWorld, "motion", stationary_motion)
    session = sessions(observation_mode="vision", max_cycles=2,
                       intervention={"kind": "target_shift", "after_cycle": 1, "delta_xy": [.02, 0]})
    session.policy.choices = ["hold"]
    run_to_budget(session)
    first, second = session.policy.seen
    before, after = first["state"]["observation"], second["state"]["observation"]
    assert before["sim_seconds"] == after["sim_seconds"]
    assert before["perception"]["capture_id"] == "frame-1"
    assert after["perception"]["capture_id"] == "frame-2"
    assert session.observer.invalidations == 1 and session.observer.captures == 2
    assert [row["capture_id"] for row in second["images"]] == ["frame-2", "frame-2"]
    assert first["images"][0]["rgb"] != second["images"][0]["rgb"]
    assert len(session.interventions) == 1
    assert session.interventions[0]["after_cycle"] == 1
    assert any(event["event"] == "external_intervention" for event in session.events)
    assert [row["action"]["id"] for row in session.history] == ["hold", "hold"]
    assert [row["capture_id"] for row in session.export()["perception_history"]] == ["frame-1", "frame-2"]


def test_target_shift_replay_preserves_old_and_new_target_geometries(sessions, monkeypatch):
    monkeypatch.setattr(RobotWorld, "motion", stationary_motion)
    session = sessions(max_cycles=2,
                       intervention={"kind": "target_shift", "after_cycle": 1, "delta_xy": [.02, -.01]})
    session.policy.choices = ["hold"]
    original_frame = copy.deepcopy(session.last_frame)
    original_target = session.world.target.copy()
    run_to_budget(session)
    shifted_target = session.world.target.copy()
    old = session.replay_frame(0)
    new = session.replay_frame(len(session.frames) - 1)
    for gid in session.world.target_geom_ids:
        np.testing.assert_allclose(old["positions"][gid], original_frame["positions"][gid], atol=1e-7)
        np.testing.assert_allclose(np.asarray(new["positions"][gid]) - old["positions"][gid], [.02, -.01, 0], atol=1e-7)
    np.testing.assert_allclose(session.frames[0]["evaluation_target"], original_target)
    np.testing.assert_allclose(session.frames[-1]["evaluation_target"], shifted_target)
    np.testing.assert_array_equal(session.world.target, shifted_target)
    assert old["time"] == new["time"]  # Equal timestamps must not erase the intervention boundary.
