from __future__ import annotations

import copy
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np

ASSETS = Path(__file__).parent / "assets" / "panda"
HOME = np.array([0, -0.45, 0, -2.15, 0, 1.75, 0.7854])
DOWN = np.diag([1., -1., -1.])
DT = 0.002
TRAVEL_Z = 0.22
TASKS = {
    "transfer": {"name": "搬运入盘", "goal": "把红色方块放进蓝色托盘，松开夹爪并向上撤离。", "target_z": 0.026},
    "stack": {"name": "方块堆叠", "goal": "把红色方块叠在蓝色方块上，松开夹爪并向上撤离。", "target_z": 0.060},
    "barrier": {"name": "越障搬运", "goal": "越过中间的障碍，把红色方块放进蓝色托盘，松开夹爪并撤离。", "target_z": 0.026},
}


def build_scene(task="transfer", seed=0):
    root = ET.parse(ASSETS / "panda.xml").getroot()
    root.set("model", "EmbodiedJev - Xingzhi")
    root.find("compiler").set("meshdir", str(ASSETS / "assets"))
    root.find("option").set("timestep", str(DT))
    root.remove(root.find("keyframe"))
    hand = root.find(".//body[@name='hand']")
    ET.SubElement(hand, "site", name="tcp", pos="0 0 0.1034", size=".004", rgba="0 0 0 0")
    for geom in root.findall(".//default[@class='collision']//geom"):
        geom.set("friction", "1.8 .02 .002")
    world = root.find("worldbody")
    ET.SubElement(world, "geom", name="table", type="box", pos=".32 0 -.025", size=".55 .43 .025",
                  rgba=".81 .84 .85 1", friction="1 .01 .001")
    rng = np.random.default_rng(seed)
    source = np.array([.43, -.17, .021])
    source[:2] += rng.uniform(-.025, .025, 2)
    cube = ET.SubElement(world, "body", name="cube", pos=" ".join(map(str, source)))
    ET.SubElement(cube, "freejoint", name="cube_joint")
    ET.SubElement(cube, "geom", name="cube_geom", type="box", size=".02 .02 .02", mass=".06",
                  rgba=".88 .19 .19 1", friction="1.8 .02 .002", condim="6", solref=".006 1")
    target = np.array([.43, .18, TASKS[task]["target_z"]])
    if task == "stack":
        ET.SubElement(world, "geom", name="support", type="box", pos=".43 .18 .02", size=".03 .03 .02",
                      rgba=".10 .44 .72 1", friction="1.2 .01 .001")
    else:
        ET.SubElement(world, "geom", name="support", type="box", pos=".43 .18 .003", size=".07 .07 .003",
                      rgba=".10 .44 .72 1", friction="1.2 .01 .001")
        for x, y, sx, sy in [(.354, .18, .006, .082), (.506, .18, .006, .082),
                             (.43, .104, .07, .006), (.43, .256, .07, .006)]:
            ET.SubElement(world, "geom", type="box", pos=f"{x} {y} .012", size=f"{sx} {sy} .012",
                          rgba=".12 .47 .74 1")
    if task == "barrier":
        ET.SubElement(world, "geom", name="barrier", type="box", pos=".43 0 .055", size=".115 .018 .055",
                      rgba=".93 .66 .15 1")
    xml = ET.tostring(root, encoding="unicode")
    return xml, target, source


class RobotWorld:
    def __init__(self, task="transfer", seed=0):
        if task not in TASKS:
            raise ValueError("Unknown task")
        self.task, self.seed = task, seed
        xml, self.target, self.source = build_scene(task, seed)
        self.scene_hash = hashlib.sha256(xml.replace(str(ASSETS), "ASSETS").encode()).hexdigest()
        self.model = mujoco.MjModel.from_xml_string(xml)
        self.data = mujoco.MjData(self.model)
        self.arm_q = np.array([self.model.jnt_qposadr[self.model.joint(f"joint{i}").id] for i in range(1, 8)])
        self.arm_dof = np.array([self.model.jnt_dofadr[self.model.joint(f"joint{i}").id] for i in range(1, 8)])
        self.ranges = np.array([self.model.jnt_range[self.model.joint(f"joint{i}").id] for i in range(1, 8)])
        self.tcp = self.model.site("tcp").id
        self.cube_id = self.model.body("cube").id
        self.cube_geom = self.model.geom("cube_geom").id
        self.cube_dof = self.model.jnt_dofadr[self.model.joint("cube_joint").id]
        self.support_id = self.model.geom("support").id
        self.left = self.model.body("left_finger").id
        self.right = self.model.body("right_finger").id
        self.closed = False
        self.stable_seconds = 0.
        self.contact_seconds = 0.
        self.max_lift = 0.
        self.unsafe_contacts = 0
        self.steps = 0
        self.data.qpos[self.arm_q] = HOME
        self.data.ctrl[:7] = HOME
        self.data.ctrl[7] = 255
        for name in ("finger_joint1", "finger_joint2"):
            self.data.qpos[self.model.jnt_qposadr[self.model.joint(name).id]] = .04
        mujoco.mj_forward(self.model, self.data)
        q, err = self.solve_ik([.38, -.08, TRAVEL_Z])
        if err > .004:
            raise RuntimeError(f"Initial IK failed: {err}")
        self.data.qpos[self.arm_q] = self.data.ctrl[:7] = q
        mujoco.mj_forward(self.model, self.data)
        for _ in range(250):
            self.tick()
        self.steps = 0
        self.start_time = float(self.data.time)

    @property
    def position(self):
        return self.data.site_xpos[self.tcp].copy()

    @property
    def cube(self):
        return self.data.xpos[self.cube_id].copy()

    def contacts(self):
        fingers, support, forbidden = set(), False, False
        for contact in self.data.contact[:self.data.ncon]:
            if contact.dist > .0005:
                continue
            g1, g2 = int(contact.geom1), int(contact.geom2)
            b1, b2 = int(self.model.geom_bodyid[g1]), int(self.model.geom_bodyid[g2])
            if self.cube_geom in (g1, g2):
                other = g2 if g1 == self.cube_geom else g1
                body = int(self.model.geom_bodyid[other])
                if body == self.left:
                    fingers.add("left")
                if body == self.right:
                    fingers.add("right")
                support = support or other == self.support_id
            # Ignore the mounted base, finger/object contacts and permitted support contact.
            for body, other_geom in [(b1, g2), (b2, g1)]:
                name = self.model.body(body).name or ""
                other_name = self.model.geom(other_geom).name or ""
                if name in {"hand", "left_finger", "right_finger", "link5", "link6", "link7"} and other_name in {"table", "barrier"}:
                    forbidden = True
        return fingers, support, forbidden

    def tick(self):
        self.data.qfrc_applied[self.arm_dof] = self.data.qfrc_bias[self.arm_dof]
        mujoco.mj_step(self.model, self.data)
        self.steps += 1
        fingers, support, forbidden = self.contacts()
        self.unsafe_contacts += int(forbidden)
        self.contact_seconds = self.contact_seconds + DT if len(fingers) == 2 and self.closed else 0.
        speed = np.linalg.norm(self.data.qvel[self.cube_dof:self.cube_dof + 3])
        on_target = np.linalg.norm(self.cube[:2] - self.target[:2]) < .025
        self.stable_seconds = self.stable_seconds + DT if support and on_target and speed < .025 and not self.closed else 0.
        self.max_lift = max(self.max_lift, float(self.cube[2] - self.source[2]))
        if not np.isfinite(self.data.qpos).all():
            raise RuntimeError("MuJoCo state became non-finite")

    def success(self):
        return bool(self.stable_seconds >= .4 and not self.closed and self.position[2] >= .17)

    def solve_ik(self, target):
        d = mujoco.MjData(self.model)
        d.qpos[:] = self.data.qpos
        target = np.asarray(target)
        q_desired = np.empty(4)
        mujoco.mju_mat2Quat(q_desired, DOWN.flatten())
        jp, jr = np.zeros((3, self.model.nv)), np.zeros((3, self.model.nv))
        q_current, q_inverse, q_error, rotation = np.empty(4), np.empty(4), np.empty(4), np.empty(3)
        for _ in range(180):
            mujoco.mj_forward(self.model, d)
            error = target - d.site_xpos[self.tcp]
            mujoco.mju_mat2Quat(q_current, d.site_xmat[self.tcp])
            mujoco.mju_negQuat(q_inverse, q_current)
            mujoco.mju_mulQuat(q_error, q_desired, q_inverse)
            mujoco.mju_quat2Vel(rotation, q_error, 1.)
            if np.linalg.norm(error) < .0004 and np.linalg.norm(rotation) < .004:
                break
            mujoco.mj_jacSite(self.model, d, jp, jr, self.tcp)
            jac = np.vstack([jp[:, self.arm_dof], jr[:, self.arm_dof]])
            delta = jac.T @ np.linalg.solve(jac @ jac.T + .002 * np.eye(6), np.r_[error, rotation])
            d.qpos[self.arm_q] = np.clip(d.qpos[self.arm_q] + np.clip(delta, -.15, .15), self.ranges[:, 0], self.ranges[:, 1])
        return d.qpos[self.arm_q].copy(), float(np.linalg.norm(error))

    def clone(self):
        other = object.__new__(RobotWorld)
        other.__dict__ = self.__dict__.copy()
        other.data = copy.copy(self.data)
        return other

    def motion(self, target=None, gripper=None, seconds=.6, emit=True):
        target = self.position if target is None else np.asarray(target, dtype=float)
        if target.shape != (3,) or not np.isfinite(target).all():
            raise ValueError("Target must contain three finite coordinates")
        if gripper not in {None, "open", "close"} or not np.isfinite(seconds) or not 0 < seconds <= 10:
            raise ValueError("Invalid gripper command or motion duration")
        if np.any(target < [.20, -.32, .020]) or np.any(target > [.65, .34, .42]):
            raise ValueError("Target outside workspace")
        q, error = self.solve_ik(target)
        if error > .004:
            raise ValueError(f"Unreachable target ({error:.4f} m)")
        start = self.data.ctrl[:7].copy()
        if gripper is not None:
            self.closed = gripper == "close"
            self.data.ctrl[7] = 0 if self.closed else 255
        count = max(1, int(seconds / DT))
        for i in range(1, count + 1):
            t = min(1., i / (count * .75))
            blend = t ** 3 * (10 - 15 * t + 6 * t * t)
            self.data.ctrl[:7] = start + (q - start) * blend
            self.tick()
            if i % 20 == 0 or i == count:
                yield self.frame() if emit else None

    def observe(self):
        fingers, support, forbidden = self.contacts()
        return {"task": TASKS[self.task]["goal"], "source": "MuJoCo geometry and contacts", "units": "metres",
                "tcp": self.position.round(5).tolist(), "object": self.cube.round(5).tolist(),
                "destination": self.target.tolist(), "gripper": "closed" if self.closed else "open",
                "finger_contacts": sorted(fingers), "held": len(fingers) == 2 and self.closed,
                "grasp_secured": self.contact_seconds >= .16, "support_contact": support,
                "stable_seconds": round(self.stable_seconds, 3), "success": bool(self.success()),
                "forbidden_contact": forbidden, "max_lift_m": round(self.max_lift, 4),
                "sim_seconds": round(float(self.data.time) - getattr(self, "start_time", 0), 3)}

    def frame(self):
        mujoco.mj_forward(self.model, self.data)
        return {"time": float(self.data.time), "qpos": self.data.qpos.tolist(),
                "positions": self.data.geom_xpos.round(5).tolist(),
                "rotations": self.data.geom_xmat.round(6).tolist(), "observation": self.observe()}

    def scene(self):
        geometries = []
        for i in range(self.model.ngeom):
            if int(self.model.geom_group[i]) == 3:
                continue
            material = int(self.model.geom_matid[i])
            color = self.model.mat_rgba[material] if material >= 0 else self.model.geom_rgba[i]
            geometries.append({"id": i, "name": self.model.geom(i).name or "", "type": int(self.model.geom_type[i]),
                               "size": self.model.geom_size[i].tolist(), "color": color.tolist(),
                               "mesh": int(self.model.geom_dataid[i])})
        mesh_ids = {g["mesh"] for g in geometries if g["type"] == int(mujoco.mjtGeom.mjGEOM_MESH)}
        meshes = {}
        for i in mesh_ids:
            va, vn = self.model.mesh_vertadr[i], self.model.mesh_vertnum[i]
            fa, fn = self.model.mesh_faceadr[i], self.model.mesh_facenum[i]
            meshes[str(i)] = {"vertices": self.model.mesh_vert[va:va + vn].tolist(),
                              "faces": self.model.mesh_face[fa:fa + fn].tolist()}
        return {"geometries": geometries, "meshes": meshes, "target": self.target.tolist(),
                "task": self.task, "seed": self.seed, "scene_hash": self.scene_hash}
