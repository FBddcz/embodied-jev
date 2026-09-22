"""Standalone simulator worker. Run with the benchmark's Python, not this package's.

Only newline-delimited JSON crosses stdin/stdout. Simulator imports and logs are
isolated from the app and its incompatible MuJoCo dependency. No model runs here.
"""
from __future__ import annotations

import contextlib
import base64
import hashlib
import importlib.metadata
import io
import json
import math
import sys


def validate_action(action, size):
    import numpy as np
    value = np.asarray(action, dtype=float)
    if value.shape != (size,) or not np.isfinite(value).all() or (abs(value) > 1).any():
        raise ValueError(f"Expected {size} finite normalized action values in [-1, 1]")
    return value


def image_packet(array):
    """Keep transport explicit; policy profiles own orientation and resizing."""
    import numpy as np
    from PIL import Image
    array = np.asarray(array)
    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("Camera must return RGB uint8 pixels")
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    data = buffer.getvalue()
    return {"encoding": "png", "data": base64.b64encode(data).decode("ascii"),
            "width": array.shape[1], "height": array.shape[0],
            "sha256": hashlib.sha256(data).hexdigest()}


def axis_angle(quaternion):
    """LIBERO/robosuite XYZW quaternion to shortest axis-angle vector."""
    import numpy as np
    q = np.asarray(quaternion, dtype=float)
    if q.shape != (4,) or not np.isfinite(q).all() or np.linalg.norm(q) < 1e-8:
        raise ValueError("Invalid proprioceptive quaternion")
    q = q / np.linalg.norm(q)
    if q[3] < 0:
        q = -q
    length = np.linalg.norm(q[:3])
    return np.zeros(3) if length < 1e-8 else q[:3] * (2 * math.atan2(length, q[3]) / length)


class MetaWorld:
    def __init__(self, case, horizon, observation_mode="privileged", control_mode="skills"):
        import gymnasium as gym
        import metaworld  # noqa: F401: registers official Gymnasium environments
        self.observation_mode = observation_mode
        self.control_mode = control_mode
        if control_mode not in {"skills", "hierarchical"} or control_mode == "hierarchical" and observation_mode != "privileged":
            raise ValueError("Meta-World hierarchy requires privileged observations")
        render = {"render_mode": "rgb_array", "camera_name": "corner2", "width": 256, "height": 256} if observation_mode == "vision" else {}
        self.env = gym.make("Meta-World/MT1", env_name=case["task"], seed=case["seed"], **render)
        self.case, self.horizon, self.steps = case, horizon, 0
        self.raw = None
        self.expert = None

    def observation(self):
        import numpy as np
        v = np.asarray(self.raw, dtype=float)
        if v.shape != (39,) or not np.isfinite(v).all():
            raise ValueError("Unsupported Meta-World observation schema; expected v3 39D state")
        if getattr(self, "observation_mode", "privileged") == "vision":
            return {"source": "rendered Meta-World RGB plus proprioception",
                    "task": self.case["task"], "tcp": v[:3].tolist(), "gripper_open_fraction": float(v[3])}
        result = {"source": "privileged Meta-World v3 state; no images",
                "task": self.case["task"], "units": "metres; world XYZ",
                "tcp": v[:3].tolist(), "gripper_open_fraction": float(v[3]),
                "object_slots": [{"position": v[a:a + 3].tolist(), "quaternion": v[a + 3:a + 7].tolist()}
                                 for a in (4, 11)],
                "previous_state": v[18:36].tolist(), "goal": v[36:39].tolist(),
                "note": "Object slots may be zero-padded. Goal is from the official goal-visible observation."}
        if getattr(self, "control_mode", "skills") == "hierarchical":
            env = self.env.unwrapped
            result["manipulation"] = {
                "hand_position_m": v[:3].tolist(),
                "finger_center_m": np.asarray(env.tcp_center, dtype=float).tolist(),
                "bilateral_contact": bool(env.touching_main_object),
                "translation_metres_per_unit": float(env.action_scale),
                "note": "Legacy tcp is the hand reference, not the finger center. Contact is measured on both pads; closed fingers alone do not prove a grasp.",
            }
        return result

    def policy_input(self):
        images = {"external": image_packet(self.env.render())} if self.observation_mode == "vision" else {}
        return {"prompt": self.case["task"].removesuffix("-v3").replace("-", " "),
                "state": self.raw[:4].tolist(), "images": images, "step": self.steps}

    def reset(self):
        self.raw, _ = self.env.reset(seed=self.case["seed"])
        dt = float(self.env.unwrapped.dt)
        return {"observation": self.observation(), "policy_input": self.policy_input(), "metadata": {
            "benchmark": "Meta-World", "version": importlib.metadata.version("metaworld"),
            "mujoco": importlib.metadata.version("mujoco"), "action_size": 4,
            "protocol": "individual MT1 goal-visible task; custom evaluation subset, not MT10/ML10",
            "success_source": "official info.success", "reset_seed": self.case["seed"],
            **({"hierarchy_observation": "hand-and-finger-center-plus-bilateral-contact-v1"}
               if self.control_mode == "hierarchical" else {}),
            "observation_mode": self.observation_mode, "camera_names": {"external": "corner2"} if self.observation_mode == "vision" else {},
            "action_spec": {"space": "metaworld_xyz", "size": 4, "normalized": True, "frame": "world",
                            "gripper": {"open": -1., "close": 1.}, "control_hz": 1 / dt}}}

    def step(self, action=None, scripted=False, capture=True):
        if scripted:
            from metaworld import policies
            # Use the installed official expert for plumbing validation only.
            name = "Sawyer" + "".join(p.capitalize() for p in self.case["task"].split("-")[:-1]) + "V3Policy"
            if self.expert is None:
                self.expert = getattr(policies, name)()
            # Some upstream experts return unbounded proportional effort;
            # normalize only this explicit comparator, never a model's action.
            import numpy as np
            action = np.clip(self.expert.get_action(self.raw), -1, 1)
        action = validate_action(action, 4)
        self.raw, _, terminated, truncated, info = self.env.step(action)
        self.steps += 1
        return {"observation": self.observation(), "success": bool(info["success"]),
                "terminated": bool(terminated), "truncated": bool(truncated or self.steps >= self.horizon),
                "action": action.tolist(),
                **({"policy_input": self.policy_input()} if capture or info["success"] else {})}

    def close(self):
        self.env.close()


class Libero:
    def __init__(self, case, horizon, observation_mode="privileged"):
        from libero.libero import benchmark
        from libero.libero.envs.env_wrapper import ControlEnv
        self.case, self.steps, self.observation_mode = case, 0, observation_mode
        suite = benchmark.get_benchmark_dict()[case["suite"]](task_order_index=0)
        task_id = case["task_id"]
        if not 0 <= task_id < suite.get_num_tasks():
            raise ValueError("LIBERO task_id out of range")
        task = suite.get_task(task_id)
        self.language = task.language
        self.states = suite.get_task_init_states(task_id)
        if not 0 <= case["init_index"] < len(self.states):
            raise ValueError("LIBERO init_index out of range; never wrap initial-state indices")
        self.env = ControlEnv(bddl_file_name=suite.get_task_bddl_file_path(task_id),
                              use_camera_obs=observation_mode == "vision", has_offscreen_renderer=observation_mode == "vision",
                              camera_names=["agentview", "robot0_eye_in_hand"], camera_heights=256, camera_widths=256,
                              controller="OSC_POSE", control_freq=20, horizon=horizon)
        self.env.seed(case["seed"])
        self.raw = None

    def observation(self):
        import numpy as np
        if self.observation_mode == "vision":
            return {"source": "rendered LIBERO RGB plus proprioception", "task": self.language,
                    "tcp": np.asarray(self.raw["robot0_eef_pos"]).tolist(),
                    "gripper_qpos": np.asarray(self.raw["robot0_gripper_qpos"]).tolist()}
        # Explicit allowlist: evaluation predicates, reward and private simulator
        # attributes never become policy input. Object poses remain privileged.
        allowed = {k: np.asarray(v, dtype=float).tolist() for k, v in self.raw.items()
                   if k.endswith(("_pos", "_quat")) or k == "robot0_gripper_qpos"}
        return {"source": "privileged LIBERO named poses; no images", "task": self.language,
                "units": "metres; world frame; quaternions as emitted by LIBERO", "poses": allowed}

    def policy_input(self):
        import numpy as np
        state = np.concatenate([self.raw["robot0_eef_pos"], axis_angle(self.raw["robot0_eef_quat"]),
                                self.raw["robot0_gripper_qpos"]])
        images = {view: image_packet(self.raw[key]) for view, key in
                  (("external", "agentview_image"), ("wrist", "robot0_eye_in_hand_image"))} if self.observation_mode == "vision" else {}
        return {"prompt": self.language, "state": state.tolist(), "images": images, "step": self.steps}

    def reset(self):
        self.env.reset()
        self.raw = self.env.set_init_state(self.states[self.case["init_index"]])
        return {"observation": self.observation(), "policy_input": self.policy_input(), "metadata": {
            "benchmark": "LIBERO", "suite": self.case["suite"], "action_size": 7,
            "robosuite": importlib.metadata.version("robosuite"),
            "init_index": self.case["init_index"], "reset_seed": self.case["seed"],
            "controller": "OSC_POSE, 20 Hz, normalized XYZ + axis-angle + gripper",
            "protocol": "custom rollout using official initial state and success predicate",
            "observation_mode": self.observation_mode,
            "camera_names": {"external": "agentview", "wrist": "robot0_eye_in_hand"} if self.observation_mode == "vision" else {},
            "camera_orientation": "raw MuJoCo output; policy profile specifies transforms",
            "action_spec": {"space": "libero_osc_pose", "size": 7, "normalized": True, "frame": "world",
                            "gripper": {"open": -1., "close": 1.}, "control_hz": 20.},
            "success_source": "official env.check_success()"}}

    def step(self, action=None, scripted=False, capture=True):
        if scripted:
            raise ValueError("No LIBERO scripted baseline is supplied")
        action = validate_action(action, 7)
        self.raw, _, done, _ = self.env.step(action)
        self.steps += 1
        success = bool(self.env.check_success())
        return {"observation": self.observation(), "success": success,
                "terminated": False, "truncated": bool(done), "action": action.tolist(),
                **({"policy_input": self.policy_input()} if capture or success else {})}

    def close(self):
        self.env.close()


def main():
    backend = None
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                with contextlib.redirect_stdout(sys.stderr):
                    if request["command"] == "reset":
                        if backend is not None:
                            backend.close()
                        cls = {"metaworld": MetaWorld, "libero": Libero}[request["backend"]]
                        mode = request.get("observation_mode", "privileged")
                        if mode not in {"privileged", "vision"}:
                            raise ValueError("Worker supports privileged or vision observations")
                        control = request.get("control_mode", "skills")
                        if control != "skills" and request["backend"] != "metaworld":
                            raise ValueError("External hierarchy is currently Meta-World only")
                        extra = {"control_mode": control} if request["backend"] == "metaworld" else {}
                        backend = cls(request["case"], request["horizon"], mode, **extra)
                        result = backend.reset()
                    elif request["command"] == "step" and backend is not None:
                        result = backend.step(request.get("action"), request.get("scripted", False), request.get("capture", True))
                    elif request["command"] == "close":
                        break
                    else:
                        raise ValueError("Invalid worker command or missing reset")
                payload = {"ok": True, **result}
            except Exception as exc:
                import traceback
                traceback.print_exc(file=sys.stderr)
                payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
            print(json.dumps(payload, allow_nan=False), flush=True)
    finally:
        if backend is not None:
            with contextlib.redirect_stdout(sys.stderr):
                backend.close()


if __name__ == "__main__":
    main()
