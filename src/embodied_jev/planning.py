from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .physics import TRAVEL_Z, RobotWorld

PHASES = {
    "approach": "移至物体上方", "descend": "下降对准", "grasp": "闭合夹爪",
    "lift": "抬升物体", "carry": "移向目标", "lower": "降低放置",
    "release": "松开夹爪", "withdraw": "向上撤离", "recover": "张开重试", "finish": "完成",
}


def eligible_phases(world: RobotWorld):
    s = world.observe()
    p, cube, target = world.position, world.cube, world.target
    near_destination = np.linalg.norm(cube[:2] - target[:2]) < .025
    if s["success"]:
        return ["finish"]
    if not world.closed and near_destination and s["support_contact"]:
        return ["withdraw"]
    if s["held"]:
        if np.linalg.norm(p[:2] - target[:2]) < .02:
            return ["lower", "release"] if abs(cube[2] - target[2]) < .012 else ["lower", "carry"]
        if cube[2] < TRAVEL_Z - .045:
            return ["lift"]
        return ["carry", "lift"]
    if world.closed:
        return ["recover", "grasp"]
    if np.linalg.norm(p[:2] - cube[:2]) > .007:
        return ["approach"]
    if abs(p[2] - cube[2]) > .006:
        return ["descend", "approach"]
    return ["grasp", "approach"]


def baseline_phase(world):
    eligible = eligible_phases(world)
    if "release" in eligible and abs(world.cube[2] - world.target[2]) < .012:
        return "release"
    return eligible[0]


@dataclass
class Candidate:
    id: str
    label: str
    phase: str
    target: list[float] | None
    gripper: str | None
    seconds: float
    admitted: bool = True
    rejection: str | None = None
    preview: dict | None = None

    def serialise(self):
        return asdict(self)


def candidates(world, phase, preview=True):
    p, cube, dest = world.position, world.cube, world.target
    target, grip, duration = p.copy(), None, .8
    if phase == "approach":
        target = cube + [0, 0, .14]
        grip = "open"
    elif phase == "descend":
        target = cube + [0, 0, .001]
    elif phase == "grasp":
        grip, duration = "close", .65
    elif phase == "lift":
        target = np.r_[p[:2], TRAVEL_Z]
        duration = 1.1
    elif phase == "carry":
        target = np.r_[dest[:2] + (p - cube)[:2], TRAVEL_Z]
        duration = 1.6
    elif phase == "lower":
        target = dest + (p - cube) + [0, 0, .002]
        duration = 1.2
    elif phase in {"release", "recover"}:
        grip, duration = "open", .8
    elif phase == "withdraw":
        target = np.r_[p[:2], TRAVEL_Z]
        duration = 1.0
    elif phase != "finish":
        raise ValueError("Unknown phase")
    result = [Candidate("direct", PHASES[phase], phase, target.tolist(), grip, duration)]
    if np.linalg.norm(target - p) > .03:
        result.append(Candidate("gentle", "减速执行", phase, target.tolist(), grip, duration * 1.5))
    result.append(Candidate("hold", "保持当前位姿", phase, p.tolist(), None, .3))
    if preview:
        for option in result:
            shadow = world.clone()
            before_bad = shadow.unsafe_contacts
            initial_z = shadow.cube[2]
            try:
                for _ in shadow.motion(option.target, option.gripper, option.seconds, emit=False):
                    pass
                state = shadow.observe()
                option.preview = {"tcp": state["tcp"], "object": state["object"], "held": state["held"],
                                  "support_contact": state["support_contact"],
                                  "target_error_m": round(float(np.linalg.norm(shadow.cube - shadow.target)), 4)}
                if shadow.unsafe_contacts > before_bad:
                    option.admitted, option.rejection = False, "预演发生机械臂与台面或障碍接触"
                elif phase in {"lift", "carry"} and world.observe()["held"] and not state["held"]:
                    option.admitted, option.rejection = False, "预演丢失双侧抓取接触"
                elif phase == "carry" and shadow.cube[2] < initial_z - .045:
                    option.admitted, option.rejection = False, "预演物体下落"
            except (ValueError, RuntimeError) as exc:
                option.admitted, option.rejection = False, str(exc)
    return result
