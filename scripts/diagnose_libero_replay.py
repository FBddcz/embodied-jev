"""Replay recorded actions for post-hoc diagnostics; never sends truth to models."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run", type=Path, required=True)
parser.add_argument("--libero-root", type=Path, required=True)
parser.add_argument("--config-dir", type=Path, required=True)
args = parser.parse_args()
RUN = args.run.resolve()
protocol = json.loads((RUN / "protocol.json").read_text())

sys.path.insert(0, str(RUN / "reproduction"))
from libero_worker import VisionEnvironment


def _model_name(model, kind, index):
    """Read a MuJoCo name without manufacturing semantic labels."""
    try:
        getter = getattr(model, kind + "_id2name", None) or getattr(model, kind)
        value = getter(int(index))
        value = value.name if hasattr(value, "name") else value
    except (AttributeError, IndexError, TypeError, ValueError):
        value = None
    return value if isinstance(value, str) and value else None


def _body_chain(model, body_id):
    """Return the model's actual body ancestry, root first."""
    chain = []
    current = int(body_id)
    while current >= 0:
        chain.append(_model_name(model, "body", current))
        try:
            parent = int(model.body_parentid[current])
        except (IndexError, TypeError):
            break
        if parent == current:
            break
        current = parent
    return list(reversed(chain))


def _geom_descriptor(model, geom_id):
    """Describe one geometry using names and IDs from the loaded model."""
    geom_id = int(geom_id)
    body_id = int(model.geom_bodyid[geom_id])
    name = _model_name(model, "geom", geom_id)
    body_chain = _body_chain(model, body_id)
    mesh = None
    try:
        # mjGEOM_MESH=7; mjGEOM_CYLINDER=5 is not a mesh index.
        mesh_id = int(model.geom_dataid[geom_id]) if int(model.geom_type[geom_id]) == 7 else -1
        if mesh_id >= 0:
            mesh = _model_name(model, "mesh", mesh_id)
    except (AttributeError, IndexError, TypeError, ValueError):
        pass
    return {"id": geom_id, "name": name, "body_id": body_id, "body": body_chain[-1] if body_chain else None,
            "body_chain": body_chain, "mesh": mesh,
            "type": int(model.geom_type[geom_id]),
            "local_position_m": np.asarray(model.geom_pos[geom_id]).tolist(),
            "size": np.asarray(model.geom_size[geom_id]).tolist(),
            "contype": int(model.geom_contype[geom_id]),
            "conaffinity": int(model.geom_conaffinity[geom_id])}


def _geometry_class(descriptor):
    """Classify only from loaded names; unknown remains unknown."""
    text = " ".join(value for value in [descriptor["name"], descriptor["body"], descriptor["mesh"],
                                       *descriptor["body_chain"]] if value).lower()
    if any(token in text for token in ("finger", "gripper", "panda_hand", "right_gripper", "eef")):
        return "robot_gripper"
    if any(token in text for token in ("microwave", "microdoor", "microwindow", "microjoint")):
        return "microwave"
    if any(token in text for token in ("link0", "link1", "link2", "link3", "link4", "link5", "link6", "link7")):
        return "robot_arm"
    return "scene_or_unknown"


def _geometry_catalog(sim, plate_body_names=()):
    catalog = [_geom_descriptor(sim.model, geom_id) for geom_id in range(sim.model.ngeom)]
    for item in catalog:
        item["class"] = _geometry_class(item)
        if any(name in item["body_chain"] for name in plate_body_names if name):
            item["class"] = "plate"
        if item["class"] == "microwave":
            moving_door = any("microdoorroot" in (body or "").lower() for body in item["body_chain"])
            item["part"] = "door_assembly" if moving_door else "housing"
        elif item["class"] == "robot_gripper":
            name = (item["name"] or "").lower()
            item["part"] = "finger_pad" if "pad" in name else "finger" if "finger" in name else "hand"
        else:
            item["part"] = None
    return catalog


def _contact_snapshot(sim, catalog):
    """Collect contacts after a replayed step; never passed to a policy."""
    by_id = {item["id"]: item for item in catalog}
    pairs = {}
    relevant = []
    for index in range(int(sim.data.ncon)):
        contact = sim.data.contact[index]
        first, second = by_id[int(contact.geom1)], by_id[int(contact.geom2)]
        if first["id"] > second["id"]:
            first, second = second, first
        key = (first["id"], second["id"])
        entry = pairs.setdefault(key, {"count": 0, "geom1_id": first["id"], "geom2_id": second["id"],
            "geom1": first["name"], "geom2": second["name"],
            "geom1_class": first["class"], "geom2_class": second["class"],
            "geom1_part": first["part"], "geom2_part": second["part"],
            "min_distance_m": float(contact.dist), "penetrating_count": 0, "active_constraint_count": 0})
        entry["count"] += 1
        entry["min_distance_m"] = min(entry["min_distance_m"], float(contact.dist))
        entry["penetrating_count"] += int(float(contact.dist) <= 0)
        entry["active_constraint_count"] += int(int(contact.efc_address) >= 0)
        classes = {first["class"], second["class"]}
        if classes & {"microwave", "plate", "robot_gripper", "robot_arm"}:
            relevant.append({"geom1": first["name"], "geom2": second["name"],
                             "geom1_id": first["id"], "geom2_id": second["id"],
                             "geom1_class": first["class"], "geom2_class": second["class"],
                             "geom1_part": first["part"], "geom2_part": second["part"],
                             "distance_m": float(contact.dist), "position_world_m": np.asarray(contact.pos).tolist(),
                             "constraint_active": int(contact.efc_address) >= 0})
    values = list(pairs.values())
    def count_pair(left, right, field="count"):
        return sum(item[field] for item in values
                   if {item["geom1_class"], item["geom2_class"]} == {left, right})
    microwave = [item for item in catalog if item["class"] == "microwave"]
    plate = [item for item in catalog if item["class"] == "plate"]
    gripper = [item for item in catalog if item["class"] == "robot_gripper"]
    arm = [item for item in catalog if item["class"] == "robot_arm"]
    valid_gripper = bool(microwave and gripper)
    valid_robot = bool(valid_gripper and arm)
    valid_plate_gripper = bool(plate and gripper)
    valid_plate_robot = bool(valid_plate_gripper and arm)
    return {"total_contacts": int(sim.data.ncon), "geometry_pairs": values,
            "relevant_contacts": relevant,
            "classification_status": "available" if valid_robot or valid_plate_robot else "unavailable",
            "classification_status_by_target": {"microwave": "available" if valid_robot else "unavailable",
                                                 "plate": "available" if valid_plate_robot else "unavailable"},
            "counts": {"gripper_microwave": count_pair("robot_gripper", "microwave") if valid_gripper else None,
                        "gripper_microwave_penetrating": count_pair("robot_gripper", "microwave", "penetrating_count") if valid_gripper else None,
                        "gripper_microwave_active_constraint": count_pair("robot_gripper", "microwave", "active_constraint_count") if valid_gripper else None,
                        "robot_microwave": count_pair("robot_arm", "microwave") + count_pair("robot_gripper", "microwave") if valid_robot else None,
                        "gripper_plate": count_pair("robot_gripper", "plate") if valid_plate_gripper else None,
                        "gripper_plate_penetrating": count_pair("robot_gripper", "plate", "penetrating_count") if valid_plate_gripper else None,
                        "gripper_plate_active_constraint": count_pair("robot_gripper", "plate", "active_constraint_count") if valid_plate_gripper else None,
                        "robot_plate": count_pair("robot_arm", "plate") + count_pair("robot_gripper", "plate") if valid_plate_robot else None}}


def _model_id(model, kind, name):
    try:
        getter = getattr(model, kind + "_name2id", None)
        return int(getter(name) if getter else getattr(model, kind)(name).id)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None


def _region_geometry(sim, site_name):
    """Read the actual compiled target site, including its world transform."""
    site_id = _model_id(sim.model, "site", site_name)
    if site_id is None:
        return None
    center = np.asarray(sim.data.site_xpos[site_id], dtype=float)
    rotation = np.asarray(sim.data.site_xmat[site_id], dtype=float).reshape(3, 3)
    half_size = np.asarray(sim.model.site_size[site_id], dtype=float)
    if not all(np.isfinite(value).all() for value in (center, rotation, half_size)):
        return None
    is_box = int(sim.model.site_type[site_id]) == 6
    corners = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]) * half_size
    world_corners = center + corners @ rotation.T if is_box else None
    horizontal = bool(is_box and np.all(np.abs(rotation[2, :2]) < 1e-6)
                      and np.all(np.abs(rotation[:2, 2]) < 1e-6))
    return {"site_id": site_id, "site_name": _model_name(sim.model, "site", site_id),
            "center_world_m": center.tolist(), "rotation_local_to_world": rotation.tolist(),
            "half_size_m": half_size.tolist(), "site_type": int(sim.model.site_type[site_id]),
            "world_box_corners_m": world_corners.tolist() if world_corners is not None else None,
            "world_xy_bounds_m": [world_corners[:, :2].min(axis=0).tolist(), world_corners[:, :2].max(axis=0).tolist()]
                if world_corners is not None else None,
            "horizontal_box": horizontal,
            "source": "Compiled MuJoCo target site world pose and size; site selected from parsed BDDL goal"}


def _plate_targets(task, sim):
    """Resolve plate bodies and their official goal sites without task coordinates."""
    parsed = task.parsed_problem
    plates = parsed.get("objects", {}).get("plate", [])
    targets = {}
    for name in plates:
        body_id = task.obj_body_id.get(name)
        goals = [goal for goal in parsed.get("goal_state", [])
                 if len(goal) == 3 and goal[1] == name and goal[2] in task.object_sites_dict]
        goal = goals[0] if len(goals) == 1 else None
        site = goal[2] if goal else None
        targets[name] = {"object_name": name, "body_id": int(body_id) if body_id is not None else None,
            "body_name": _model_name(sim.model, "body", body_id) if body_id is not None else None,
            "goal_predicate": goal, "target_site": site,
            "bddl_region": parsed.get("regions", {}).get(site) if site else None,
            "target_region_initial": _region_geometry(sim, site) if site else None,
            "position_definition": "MuJoCo object root body origin, also used by LIBERO ObjectState; not mesh centroid",
            "distance_definition": "XY distance from object body origin to target site's horizontal box footprint; not the full success predicate"}
    return targets


def _object_progress(task, sim, targets):
    progress = {}
    for name, spec in targets.items():
        body_id = spec["body_id"]
        position = np.asarray(sim.data.body_xpos[body_id], dtype=float) if body_id is not None else None
        if position is not None and not np.isfinite(position).all():
            position = None
        site = spec["target_site"]
        region = _region_geometry(sim, site) if site else None
        item = {"body_position_world_m": position.tolist() if position is not None else None,
            "target_site": site, "target_center_world_m": region["center_world_m"] if region else None,
            "target_region_world_xy_bounds_m": region["world_xy_bounds_m"] if region else None,
            "target_region_half_size_m": region["half_size_m"] if region else None,
            "target_delta_xy_m": None, "distance_to_target_center_xy_m": None,
            "distance_to_target_region_xy_m": None, "within_target_xy": None,
            "official_goal_satisfied": None, "status": "unavailable"}
        if position is not None and region:
            center, rotation, size = (np.asarray(region[key]) for key in
                                      ("center_world_m", "rotation_local_to_world", "half_size_m"))
            item["target_delta_xy_m"] = (center[:2] - position[:2]).tolist()
            item["distance_to_target_center_xy_m"] = float(np.linalg.norm(center[:2] - position[:2]))
            if region["horizontal_box"]:
                local_delta = rotation.T @ (position - center)
                outside = np.maximum(np.abs(local_delta[:2]) - size[:2], 0)
                item["distance_to_target_region_xy_m"] = float(np.linalg.norm(outside))
                item["within_target_xy"] = bool(np.all(np.abs(local_delta[:2]) < size[:2]))
                item["status"] = "available"
            if spec["goal_predicate"]:
                item["official_goal_satisfied"] = bool(task._eval_predicate(spec["goal_predicate"]))
        progress[name] = item
    return progress

def _robot_snapshot(sim):
    joints = {}
    for name in sim.model.joint_names:
        if not name.startswith("robot0_joint"):
            continue
        joint_id = sim.model.joint_name2id(name)
        dof = sim.model.get_joint_qvel_addr(name)
        position = float(sim.data.get_joint_qpos(name))
        bounds = np.asarray(sim.model.jnt_range[joint_id]).tolist()
        limited = bool(sim.model.jnt_limited[joint_id])
        joints[name] = {"position_rad": position, "velocity_rad_s": float(sim.data.qvel[dof]),
            "limits_rad": bounds if limited else None,
            "limit_margin_rad": min(position-bounds[0], bounds[1]-position) if limited else None,
            "actuator_torque_nm": float(sim.data.qfrc_actuator[dof]),
            "constraint_torque_nm": float(sim.data.qfrc_constraint[dof])}
    return joints


reports = []
for path in sorted(RUN.glob("*/*/episode.json")):
    row = json.loads(path.read_text())
    env = VisionEnvironment({"libero_root": str(args.libero_root.resolve()),
        "config_dir": str(args.config_dir.resolve()), "case": row["case"],
        "horizon": 1_000_000_000 if protocol["budget"].get("mode") == "wall-time" else protocol["budget"]["max_steps"], "camera_size": protocol["budget"]["camera_size"], "settle_steps": 10})
    try:
        assert env.metadata["settled_state_sha256"] == row["metadata"]["settled_state_sha256"]
        sim = env.env.sim
        task = env.env.env
        names = [name for name in sim.model.joint_names if "top_level" in name or "microjoint" in name]
        object_targets = _plate_targets(task, sim)
        geometry_catalog = _geometry_catalog(sim, [spec["body_name"] for spec in object_targets.values()])
        tracked_geometry = {group: [item for item in geometry_catalog if item["class"] == group_name]
                            for group, group_name in (("microwave", "microwave"),
                                                      ("plate", "plate"),
                                                      ("robot_gripper", "robot_gripper"),
                                                      ("robot_arm", "robot_arm"))}
        def joint_state():
            return {name: float(sim.data.get_joint_qpos(name)) for name in names}
        states = [{"step": 0, "joints": joint_state(), "robot_joints": _robot_snapshot(sim), "contacts": _contact_snapshot(sim, geometry_catalog),
                   "object_progress": _object_progress(task, sim, object_targets)}]
        errors = []
        verified_steps = []
        frames = {f["step"]: f for f in row["frames"]}
        for decision in row["decisions"]:
            for step in range(decision["step"]+1, decision["end_step"]+1):
                result = env.step(decision["action"], capture=False)
                if result["observation"]["step"] != step:
                    raise ValueError("Recorded action steps are not a continuous replay")
                errors.append(float(np.max(np.abs(np.array(result["observation"]["tcp"])-frames[step]["observation"]["tcp"]))))
                verified_steps.append(step)
                states.append({"step": step, "joints": joint_state(), "robot_joints": _robot_snapshot(sim),
                               "contacts": _contact_snapshot(sim, geometry_catalog),
                               "object_progress": _object_progress(task, sim, object_targets)})
        report = {"mode": row["mode"], "id": row["id"], "success": bool(env.env.check_success()),
                  "max_tcp_replay_error_m": max(errors, default=0), "geometry_catalog": geometry_catalog,
                  "replay_verified_steps": len(verified_steps),
                  "replay_complete": verified_steps == list(range(1, row["steps"]+1)),
                  "object_targets": object_targets,
                  "tracked_geometry": tracked_geometry, "states": states}
        reports.append(report)
        print(json.dumps({**{k:v for k,v in report.items() if k not in {"states", "geometry_catalog", "tracked_geometry", "object_targets"}},
            "initial": {"step": 0, "joints": states[0]["joints"], "object_progress": states[0]["object_progress"]},
            "final": {"step": states[-1]["step"], "joints": states[-1]["joints"], "object_progress": states[-1]["object_progress"]},
            "tracked_geometry_counts": {key: len(items) for key, items in tracked_geometry.items()},
            "steps_with_gripper_microwave_contact": sum((state["contacts"]["counts"]["gripper_microwave"] or 0) > 0 for state in states)
                if tracked_geometry["microwave"] and tracked_geometry["robot_gripper"] else None,
            "steps_with_gripper_plate_contact": sum((state["contacts"]["counts"]["gripper_plate"] or 0) > 0 for state in states)
                if tracked_geometry["plate"] and tracked_geometry["robot_gripper"] else None,
            "range": {name: [min(s["joints"][name] for s in states), max(s["joints"][name] for s in states)] for name in names}}), flush=True)
    finally:
        env.close()
target = RUN / "replay-diagnostics.json"
target.write_text(json.dumps({"scope": "Post-hoc action replay; joint, object, goal-region and contact truth never sent to models",
                              "contact_diagnostics": "Contact geometry is read only during replay; counts and names are not policy inputs.",
                              "episodes": reports}, indent=2)+"\n")
print(target)
