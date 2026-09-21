"""Shared Meta-World subgoal and motor interfaces for Jev and Chat policies.

Waypoints, tolerances and contact interpretation are controller design. Models
select every subgoal and motor direction; this module never overrides a choice.
No Panda dimensions, fixed phase counter, or benchmark success flag is used.
"""
from __future__ import annotations

import math

# Frozen for the matched Jev/GPT-6 rerun.  The version changes whenever the
# task semantics, geometric conditions, or model-facing prompts change.
VERSION = "metaworld-subgoal-v3"
TASKS = {
    "reach-v3": "Move the hand reference point to the target position.",
    "push-v3": "Push the movable object along the table to its target position.",
    "pick-place-v3": "Grasp and lift the movable object, then carry the object to its target position.",
}
QUESTION = (
    "Choose the next immediate subgoal from current measured geometry and contact. "
    "The object target is a destination for the OBJECT in push/pick-place tasks. "
    "An empty hand moving to that destination does not move the object. "
    "Approach before lowering; grasp only when aligned and near the object. "
    "Closed fingers alone do not prove a grasp. A lost grasp requires recovery. "
    "For pick-place: without a measured grasp, choose approach until grasp_pose_reached is true, then grasp. "
    "With a measured grasp, lift until at_travel_height, then carry until object_goal_xy_aligned, then lower_goal. "
    "Use each option's conditions; all subgoals remain available each cycle."
)
MOTOR_RULE = (
    "Execute the model-selected subgoal using target_minus_hand_mm. "
    "Positive error means positive direction; negative error means negative direction. "
    "Choose hold within axis_tolerance_mm. Follow the selected subgoal's finger intent. "
    "All directions remain available. Each command executes only a short bounded chunk."
)


def point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3 or any(type(x) not in (float, int) or not math.isfinite(x) for x in value):
        raise ValueError("Hierarchy needs three finite coordinates")
    return list(value)


def difference(a, b):
    return [x - y for x, y in zip(a, b)]


def add(a, b):
    return [x + y for x, y in zip(a, b)]


def prepare(observation, initial_observation, history):
    task = observation["task"]
    if task not in TASKS:
        raise ValueError("No validated hierarchy for this Meta-World task")
    facts = observation["manipulation"]
    hand = point(facts["hand_position_m"])
    fingers = point(facts["finger_center_m"])
    obj = point(observation["object_slots"][0]["position"])
    goal = point(observation["goal"])
    initial_obj = point(initial_observation["object_slots"][0]["position"])
    if type(facts["bilateral_contact"]) is not bool:
        raise ValueError("Bilateral contact must be measured as a boolean")
    offset = difference(hand, fingers)
    held = facts["bilateral_contact"] and math.dist(fingers, obj) < .09
    xy = math.dist(fingers[:2], obj[:2])
    obj_to_goal = math.dist(obj, goal)
    travel_z = max(initial_obj[2] + .16, goal[2] + .06)
    subgoals = {}

    def option(name, description, target, finger):
        subgoals[name] = {"description": description, "target_hand_m": point(target), "finger_intent": finger}

    if task == "reach-v3":
        option("reach", "Move the hand reference toward goal; keep fingers open.", goal, "open")
        option("hold", "Hold only when the hand reference is already at goal.", hand, "open")
    elif task == "push-v3":
        delta = difference(goal, obj)
        norm = math.hypot(delta[0], delta[1])
        direction = [delta[0] / norm, delta[1] / norm, 0.] if norm > 1e-8 else [0., 1., 0.]
        behind = [obj[0] - .035 * direction[0], obj[1] - .035 * direction[1], obj[2] + .015]
        above = [behind[0], behind[1], max(obj[2] + .12, fingers[2])]
        push_to = [goal[0] + .02 * direction[0], goal[1] + .02 * direction[1], behind[2]]
        option("approach", "When outside pushing_contact_geometry, open fingers and align behind the object above table if behind_xy_error_mm > 12. Opening also releases an accidental grasp before repositioning.", add(above, offset), "open")
        option("lower", "Outside pushing_contact_geometry and after behind XY alignment, lower to pushing height if push_height_error_mm > 8.", add(behind, offset), "close")
        option("push", "When pushing_contact_geometry is true, stay low and push toward the object goal with fingers closed. Continue pushing as the object moves.", add(push_to, offset), "close")
        option("finish", "Hold only if the object is already at its target.", hand, "close")
    else:
        grasp = [obj[0], obj[1], obj[2] + .005]
        approach_target = [obj[0], obj[1], fingers[2] if xy > .012 else grasp[2]]
        carry = add(goal, difference(hand, obj)) if held else add(goal, offset)
        carry[2] = travel_z + offset[2]
        lower_goal = add(goal, difference(hand, obj)) if held else add(goal, offset)
        option("approach", "Object is not held and grasp_pose_reached is false: open fingers, align XY while holding height, then descend within this same subgoal. Recover a lost grasp here.", add(approach_target, offset), "open")
        option("grasp", "grasp_pose_reached is true but the grasp is not secured: hold XYZ and close until bilateral contact is measured.", hand, "close")
        option("lift", "With measured bilateral grasp, lift vertically toward travel height before horizontal transport.", [hand[0], hand[1], travel_z + offset[2]], "close")
        option("carry", "With measured grasp at travel height, align the held object over the object goal.", carry, "close")
        option("lower_goal", "With measured grasp and object XY aligned over goal, lower the held object toward goal.", lower_goal, "close")
        option("finish", "Hold when the object is already at its target. Evaluation belongs to the environment.", hand, "close" if held else "open")
    state = {
        "version": VERSION, "task": TASKS[task], "task_id": task,
        "units": "millimetres; world XYZ",
        "hand_mm": [round(x * 1000, 2) for x in hand],
        "finger_center_mm": [round(x * 1000, 2) for x in fingers],
        "object_mm": [round(x * 1000, 2) for x in obj],
        "object_goal_mm": [round(x * 1000, 2) for x in goal],
        "gripper_open_fraction": observation["gripper_open_fraction"],
        "object_xy_aligned": xy <= .012,
        "grasp_pose_reached": xy <= .012 and abs(fingers[2] - obj[2] - .005) <= .018,
        "object_goal_xy_aligned": math.dist(obj[:2], goal[:2]) <= .012,
        "bilateral_contact": facts["bilateral_contact"], "grasp_with_contact": held,
        "finger_object_xy_error_mm": round(xy * 1000, 2),
        "finger_grasp_z_error_mm": round(abs(fingers[2] - obj[2] - .005) * 1000, 2),
        "object_rise_mm": round((obj[2] - initial_obj[2]) * 1000, 2),
        "object_goal_distance_mm": round(obj_to_goal * 1000, 2),
        "object_goal_xy_error_mm": round(math.dist(obj[:2], goal[:2]) * 1000, 2),
        "travel_height_mm": round(travel_z * 1000, 2),
        "at_travel_height": fingers[2] >= travel_z - .012,
        "recent_actions": history[-4:],
    }
    if task == "push-v3":
        state.update(behind_xy_error_mm=round(math.dist(fingers[:2], behind[:2]) * 1000, 2),
                     push_height_error_mm=round(abs(fingers[2] - behind[2]) * 1000, 2),
                     pushing_contact_geometry=(abs(fingers[2] - behind[2]) < .012 and xy < .09
                         and -.10 < sum((fingers[i] - obj[i]) * direction[i] for i in (0, 1)) < .025))
    return state, subgoals


def motor_input(state, subgoals, selected, observation, max_scale, repeat):
    if selected not in subgoals:
        raise ValueError("Unknown selected subgoal")
    target = subgoals[selected]
    hand = point(observation["manipulation"]["hand_position_m"])
    delta = difference(target["target_hand_m"], hand)
    metres_per_unit = observation["manipulation"]["translation_metres_per_unit"]
    if not 0 < max_scale <= 1 or type(repeat) is not int or repeat < 1 or not math.isfinite(metres_per_unit) or metres_per_unit <= 0:
        raise ValueError("Invalid hierarchy action scale")
    scales = [min(max_scale, max(.08, abs(error) / (metres_per_unit * repeat))) for error in delta]
    motor = {**state, "selected_subgoal": {"id": selected, **target},
             "target_minus_hand_mm": [round(x * 1000, 2) for x in delta],
             "axis_tolerance_mm": 5., "axis_amplitudes": dict(zip("xyz", scales)),
             "repeat_env_steps": repeat}
    questions = {}
    for index, axis in enumerate("xyz"):
        error = motor["target_minus_hand_mm"][index]
        questions[axis] = {
            "type": "choice",
            "instructions": MOTOR_RULE + f" Current subgoal: {selected}. Decide ONLY {axis.upper()}. "
                            f"Current hand coordinate {hand[index] * 1000:.2f} mm; target {target['target_hand_m'][index] * 1000:.2f} mm; "
                            f"signed target-minus-current error {error:+.2f} mm. Tolerance is 5 mm.",
            "criteria": {
                "negative": f"Decrease {axis.upper()} when its signed error is below -5 mm.",
                "hold": f"Keep {axis.upper()} unchanged when its signed error is between -5 and +5 mm inclusive.",
                "positive": f"Increase {axis.upper()} when its signed error is above +5 mm.",
            },
        }
    questions["gripper"] = {"type": "choice", "instructions": MOTOR_RULE + " Select finger command.",
                            "criteria": {"open": "Open fingers.", "hold": "Retain last command.", "close": "Close fingers."}}
    return motor, questions, scales
