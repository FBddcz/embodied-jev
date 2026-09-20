"""Task-independent motor choices and bounded evidence for closed-loop planning.

Only the explicitly named baseline comparator imports task skills. Model menus
are a fixed motor vocabulary, and contain no object-derived waypoints.
"""
from __future__ import annotations

import copy

import numpy as np

from .evidence import validate_user_context
from .planning import Candidate, baseline_phase, candidates as skill_candidates

PROMPT_VERSION = "incremental-planning-v2"
PLANNING_INSTRUCTIONS = (
    "Plan toward the stated task goal using the current sensors and recent actual outcomes. "
    "Choose exactly one offered fixed incremental robot action for this decision; the next "
    "decision receives a new observation. Coordinates and increments are in metres in the "
    "world XYZ frame, not image pixels. Use the camera calibration to interpret image "
    "directions. A translation preserves the gripper command. Closing the gripper does not "
    "prove a grasp: held means both fingers contact the object while closed. Support contact "
    "reports physical contact, not proof of correct placement. Account for obstacles, "
    "occlusion, observed movement, rejected actions and discrepancies from your intended "
    "outcome. No task phase or movement sequence is prescribed. The action IDs and step "
    "sizes are fixed; do not invent an action or assume automatic alignment. If an image "
    "is attached, use its visible evidence alongside the available sensors; missing object "
    "coordinates are unknown, not zero. You may give a brief public intent and a short "
    "visual_evidence description of what is visibly supported, not private reasoning."
)

_LOW = np.array([.20, -.32, .020])
_HIGH = np.array([.65, .34, .42])
_STEPS = ((40, .04), (10, .01), (2, .002))
_SENSORS = ("source", "tcp", "gripper", "held", "finger_contacts", "grasp_secured",
            "support_contact", "forbidden_contact", "sim_seconds")
_VISUAL_POSES = ("object", "destination", "visual_barrier")
_CAMERA_FIELDS = ("source", "capture_id", "status", "sim_time", "age_sim_seconds",
                  "width", "height", "camera_view", "depth_units", "message")
_CALIBRATION_FIELDS = ("position", "forward", "up", "fx", "fy", "cx", "cy",
                       "coordinate_frame", "units")
_DETECTION_FIELDS = ("id", "label", "visible", "position", "tracked", "age_sim_seconds",
                     "method", "pixel_count", "extent_xy_m")


def _tcp(observation):
    point = np.asarray(observation["tcp"], dtype=float)
    if point.shape != (3,) or not np.isfinite(point).all():
        raise ValueError("TCP must contain three finite coordinates")
    return point


def incremental_candidates(observation):
    """Offer all 21 motor commands, using only the measured TCP position.

    Workspace-invalid targets remain visible as rejected candidates. Never clip
    them, reduce their step size, or select a direction from task geometry.
    """
    point = _tcp(observation)
    result = []
    for axis, name in enumerate("xyz"):
        for sign, direction in ((1, "pos"), (-1, "neg")):
            for millimetres, step in _STEPS:
                target = point.copy()
                target[axis] += sign * step
                result.append(Candidate(
                    f"{name}_{direction}_{millimetres}",
                    f"{name.upper()} {sign * step:+.3f} m", "incremental",
                    target.tolist(), None, .45))
    result.extend([
        Candidate("open", "张开夹爪", "incremental", point.tolist(), "open", .65),
        Candidate("close", "闭合夹爪", "incremental", point.tolist(), "close", .65),
        Candidate("hold", "保持当前位姿", "incremental", point.tolist(), None, .3),
    ])
    for option in result:
        target = np.asarray(option.target)
        if np.any(target < _LOW) or np.any(target > _HIGH):
            option.admitted = False
            option.rejection = "Target outside workspace; fixed increment was not clipped"
    return result


def _is_image_only(observation):
    perception = observation.get("perception")
    return isinstance(perception, dict) and perception.get("source") == "vision"


def _sensor_evidence(observation, *, image_only=False, camera=True):
    evidence = {key: copy.deepcopy(observation[key]) for key in _SENSORS if key in observation}
    if "task" in observation:
        evidence["goal"] = observation["task"]
    evidence["units"] = "metres"
    if not image_only:
        evidence.update({key: copy.deepcopy(observation[key])
                         for key in _VISUAL_POSES if key in observation})
    perception = observation.get("perception")
    if camera and isinstance(perception, dict):
        metadata = {key: copy.deepcopy(perception[key]) for key in _CAMERA_FIELDS if key in perception}
        calibration = perception.get("calibration")
        if isinstance(calibration, dict):
            metadata["calibration"] = {key: copy.deepcopy(calibration[key])
                                       for key in _CALIBRATION_FIELDS if key in calibration}
        views = perception.get("views")
        if isinstance(views, dict):
            metadata["views"] = {name: {"mount": row.get("mount"), "calibration": {
                key: copy.deepcopy(row.get("calibration", {})[key])
                for key in _CALIBRATION_FIELDS if key in row.get("calibration", {})}}
                for name, row in views.items() if name in {"external", "wrist"} and isinstance(row, dict)}
        if not image_only and isinstance(perception.get("objects"), list):
            metadata["objects"] = [{key: copy.deepcopy(row[key]) for key in _DETECTION_FIELDS if key in row}
                                   for row in perception["objects"][:3] if isinstance(row, dict)]
        evidence["perception"] = metadata
    return evidence


def _public_intention(record):
    decision = record.get("decision") or {}
    return {key: decision[key][:240] for key in ("intent", "visual_evidence")
            if isinstance(decision.get(key), str) and decision[key]}


def planning_state(observation, history):
    """Retain actual sensors and six attempted/executed actions, never an oracle.

    Direct vision cannot inherit object poses from mixed or legacy history.
    Scene configuration, success truth, rollout predictions, recommended travel
    height and raw image bytes are deliberately outside this evidence schema.
    """
    image_only = _is_image_only(observation)
    state = {"observation": _sensor_evidence(observation, image_only=image_only),
             "goal_conditions": {"object": "resting stably in/on the requested destination for at least 0.4 seconds",
                                 "gripper": "open and no longer holding the object", "final_tcp_z_min_m": .17},
             "recent_outcomes": []}
    for record in history[-6:]:
        before, after = record.get("before", {}), record.get("after", {})
        action = record.get("action") or {}
        transition = {"cycle": record.get("cycle"), "option": action.get("id"),
                      "executed": record.get("executed", True),
                      "after": _sensor_evidence(after, image_only=image_only or _is_image_only(after), camera=False)}
        if action.get("target") is not None:
            transition["target_tcp"] = copy.deepcopy(action["target"])
        transition["gripper_command"] = action.get("gripper") or "unchanged"
        if "seconds" in action:
            transition["duration_seconds"] = action["seconds"]
        if "tcp" in before and "tcp" in after:
            transition["tcp_delta_m"] = (np.asarray(after["tcp"]) - np.asarray(before["tcp"])).round(5).tolist()
        if not image_only and not _is_image_only(before) and not _is_image_only(after) and "object" in before and "object" in after:
            transition["object_delta_m"] = (np.asarray(after["object"]) - np.asarray(before["object"])).round(5).tolist()
        rejection = record.get("rejection") or action.get("rejection")
        if rejection:
            transition["rejection"] = str(rejection)[:240]
        intention = _public_intention(record)
        if intention:
            transition["public_intention"] = intention
        state["recent_outcomes"].append(transition)
    if history:
        intention = _public_intention(history[-1])
        if intention:
            state["previous_intention"] = intention
    if observation.get("user_context"):
        state["user_context"] = validate_user_context(observation["user_context"])
    return state


def baseline_choice(observation, candidates):
    """Hand-written skill comparator, solely for the explicitly selected baseline.

    This quantizes a scripted skill's target to the same fixed motor menu. It is
    not a model fallback and must never be used to rank or prune model options.
    """
    if _is_image_only(observation) or not all(key in observation for key in ("object", "destination")):
        raise ValueError("The rule baseline requires measured object and destination coordinates")
    legal = {option.id: option for option in candidates if option.admitted}
    if not legal:
        raise ValueError("No admitted incremental action")
    phase = baseline_phase(None, observation)
    desired = skill_candidates(None, phase, preview=False, observation=observation)[0]
    if desired.gripper and desired.gripper in legal:
        actual = "close" if observation.get("gripper") == "closed" else "open"
        if desired.gripper != actual:
            return desired.gripper
    point, target = _tcp(observation), np.asarray(desired.target)
    current_distance = np.linalg.norm(target - point)
    moves = [option for option in legal.values() if option.id not in {"open", "close", "hold"}]
    if moves:
        selected = min(moves, key=lambda option: float(np.linalg.norm(np.asarray(option.target) - target)))
        if np.linalg.norm(np.asarray(selected.target) - target) < current_distance - 1e-6:
            return selected.id
    if "hold" in legal:
        return "hold"
    raise ValueError("No admitted baseline motion makes progress")
