from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import threading
import time
import uuid
import zipfile
from collections import deque
from datetime import datetime, timezone

from .physics import RobotWorld
from .planning import PHASES, baseline_phase, candidates, eligible_phases, phase_options
from .policies import DecisionPolicy, minicpm_status
from .evidence import PROMPT_VERSION, validate_user_context
from .eventlog import write_event

POLICY_VERSION = PROMPT_VERSION


def validate_camera_views(value, observation_mode):
    if value is None:
        return ["external", "wrist"] if observation_mode in {"rgbd", "vision"} else []
    if not isinstance(value, (list, tuple)) or any(not isinstance(view, str) or view not in {"external", "wrist"} for view in value):
        raise ValueError("相机只能选择 external 或 wrist")
    if len(set(value)) != len(value):
        raise ValueError("相机视角不能重复")
    if not value and observation_mode in {"rgbd", "vision"}:
        raise ValueError("视觉观测至少需要一种相机；无相机请使用 privileged 结构化状态模式")
    return [view for view in ("external", "wrist") if view in value]


def validate_intervention(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"kind", "after_cycle", "delta_xy"}:
        raise ValueError("扰动需要 kind、after_cycle 和 delta_xy")
    if value["kind"] not in {"object_shift", "target_shift"}:
        raise ValueError("未知扰动类型")
    if type(value["after_cycle"]) is not int or not 1 <= value["after_cycle"] <= 199:
        raise ValueError("扰动时刻必须为 1–199 步")
    delta = value["delta_xy"]
    if not isinstance(delta, (list, tuple)) or len(delta) != 2 or any(
            type(x) not in (int, float) or not math.isfinite(x) or abs(x) > .06 for x in delta):
        raise ValueError("扰动 XY 位移必须在 ±0.06 米内")
    if not any(delta):
        raise ValueError("扰动位移不能全为零")
    return {"kind": value["kind"], "after_cycle": value["after_cycle"], "delta_xy": list(delta)}


class Session:
    def __init__(self, task="transfer", seed=0, provider="baseline", preview=True,
                 threshold=.55, max_cycles=30, speed=1.5, connection=None,
                 scene_config=None, user_context=None, observation_mode="privileged",
                 control_mode="skills", intervention=None, shuffle_candidates=False, camera_views=None):
        if observation_mode not in {"privileged", "rgbd", "vision"}:
            raise ValueError("Unknown observation mode")
        if control_mode not in {"skills", "incremental"}:
            raise ValueError("Unknown control mode")
        if observation_mode == "vision" and (control_mode != "incremental" or provider not in {"chat", "claude"}):
            raise ValueError("直接图像模式需要逐步 XYZ 控制和支持图像的 OpenAI 兼容或 Claude 接口")
        self.id = uuid.uuid4().hex[:12]
        self.observation_mode = observation_mode
        self.control_mode = control_mode
        self.camera_views = validate_camera_views(camera_views, observation_mode)
        self.intervention = validate_intervention(intervention)
        self.interventions = []
        self.shuffle_candidates = bool(shuffle_candidates)
        self.observer = None
        self.perception_history = []
        self.image_frames = {}
        self.user_context = validate_user_context(user_context)
        self.world = RobotWorld(task, seed, scene_config=scene_config)
        self.policy = DecisionPolicy(provider, connection)
        self.profile_id = (connection or {}).get("profile_id")
        self.preview = preview
        self.threshold, self.max_cycles, self.speed = threshold, max_cycles, speed
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.wake = threading.Event()
        self.worker = None
        self.status = "idle"
        self.stage = "ready"
        self.phase = None
        self.cycles = 0
        self.history = []
        self.events = deque(maxlen=200)
        self.frames = []
        if observation_mode == "rgbd":
            from .perception import VisualObserver
            self.observer = VisualObserver(self.world, **({"camera_views": self.camera_views} if camera_views is not None else {}))
        elif self.camera_views:
            from .perception import ImageObserver
            self.observer = ImageObserver(self.world, **({"camera_views": self.camera_views} if camera_views is not None else {}))
        try:
            self.observation = self._observe()
        except Exception:
            self._close_resources()
            raise
        self.last_frame = self.world.frame()
        self.current_candidates = []
        self.last_decision = None
        self.last_intent = None
        self.last_decision_inputs = {"phase": None, "action": None}
        self.message = None
        self.single_step = False
        self.started = None
        self.finished = None
        self._record(self.last_frame)
        self._event("created", "实验已就绪")

    def _observe(self):
        try:
            observation = self.observer.observe() if self.observer else self.world.observe()
            if self.observation_mode == "privileged" and self.observer:
                # Optional preview cameras never change the policy's state input.
                observation = self.world.observe()
        except Exception:
            snapshot = self.camera_snapshot()
            if snapshot:
                self.perception_history.append(copy.deepcopy(snapshot["metadata"]))
                self._archive_capture(snapshot)
            raise
        self.observation = copy.deepcopy(observation)
        capture = self.camera_snapshot()
        metadata = capture["metadata"] if capture else observation.get("perception")
        if metadata and (not self.perception_history or
                         self.perception_history[-1].get("capture_id") != metadata.get("capture_id")):
            self.perception_history.append(copy.deepcopy(metadata))
            if capture:
                self._archive_capture(capture)
        return observation

    def camera_snapshot(self):
        # The observer owns its render thread; HTTP reads only cached bytes.
        return self.observer.camera_snapshot() if self.observer else None

    def _archive_capture(self, capture):
        self.image_frames[str(capture["metadata"]["capture_id"])] = {
            view: frame["rgb"] for view, frame in capture.get("views", {"external": capture}).items()}

    def camera_manifest(self):
        return [{"capture_id": capture, "view": view, "file": f"captures/{capture}-{view}.png",
                 "sha256": hashlib.sha256(pixels).hexdigest(), "byte_length": len(pixels)}
                for capture, views in self.image_frames.items() for view, pixels in views.items()]

    def camera_archive(self):
        with self.lock:
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps({"episode_id": self.id,
                    "observation_mode": self.observation_mode, "camera_views": self.camera_views,
                    "frames": self.camera_manifest()}, indent=2))
                for capture, views in self.image_frames.items():
                    for view, pixels in views.items():
                        archive.writestr(f"captures/{capture}-{view}.png", pixels)
            return stream.getvalue()

    def _close_resources(self):
        self.policy.close()
        if self.observer:
            self.observer.close()

    def _event(self, event, message, level="info"):
        with self.lock:
            entry = {"time": datetime.now(timezone.utc).isoformat(), "event": event,
                     "level": level, "message": message, "cycle": self.cycles,
                     "episode_id": self.id}
            self.events.append(entry)
        write_event(entry)

    def _record(self, frame):
        with self.lock:
            if self.observer:
                frame = dict(frame)
                observation = copy.deepcopy(self.observation)
                now = float(frame["time"]) - self.world.start_time
                observation["sim_seconds"] = round(now, 3)
                metadata = observation.get("perception", {})
                metadata["age_sim_seconds"] = round(max(0, now - metadata.get("sim_time", now)), 3)
                frame["observation"] = observation
            self.last_frame = frame
            self.frames.append({"time": frame["time"], "qpos": frame["qpos"],
                                "observation": frame["observation"], "cycle": self.cycles,
                                "phase": self.phase, "evaluation_target": self.world.target.tolist()})

    def start(self, single_step=False, threshold=None):
        with self.lock:
            if self.status in {"completed", "stopped", "error", "exhausted", "stalled"}:
                raise ValueError("Reset the episode before starting again")
            if self.status == "running":
                return  # Repeated requests must not replace an in-flight single-step mode.
            if threshold is not None:
                if not 0 <= threshold <= 1:
                    raise ValueError("Threshold must be in [0, 1]")
                self.threshold = threshold
            self.status = "running"
            self.single_step = single_step
            self.message = None
            if self.started is None:
                self.started = time.perf_counter()
            self._event("step" if single_step else "started", "单步执行" if single_step else "开始运行")
            self.wake.set()
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._run, daemon=True)
                self.worker.start()

    def pause(self):
        with self.lock:
            if self.status == "running":
                self.status = "paused"
                self.wake.clear()
                self._event("paused", "已暂停")

    def stop(self):
        with self.lock:
            was_active = not self.cancel.is_set()
            self.cancel.set()
            self.wake.set()
            if self.status != "completed":
                self.status = "stopped"
            self.finished = time.perf_counter()
            if was_active:
                self._event("stopped", "实验已停止")
        if self.worker is None or not self.worker.is_alive():
            self._close_resources()

    def _wait(self):
        while not self.cancel.is_set():
            if self.wake.wait(.1):
                return not self.cancel.is_set()
        return False

    def _run(self):
        try:
            if self.control_mode == "incremental":
                self._run_incremental()
                return
            while not self.cancel.is_set():
                if not self._wait():
                    return
                if self.cycles >= self.max_cycles:
                    with self.lock:
                        if self.cancel.is_set():
                            return
                        self.status, self.message = "exhausted", "已达到动作预算"
                        self.finished = time.perf_counter()
                        self._event("budget_exhausted", self.message, "warning")
                    return
                with self.lock:
                    if self.cancel.is_set():
                        return
                    self._apply_intervention()
                    observation = self._observe()
                    if self.observer:
                        self._record(self.world.frame())
                    model_observation = {**observation, **({"user_context": self.user_context} if self.user_context else {})}
                    phases = eligible_phases(self.world, observation)
                    self.stage = "deciding"
                    self.last_decision = None
                    self.last_intent = None
                    self.last_decision_inputs = {"phase": None, "action": None}
                    self.current_candidates = []
                self._event("deciding", "正在选择操作阶段")
                intent = self._choose("phase", model_observation,
                    "Choose the next phase that makes progress toward the goal, given the measured geometry and contacts. "
                    "Avoid repeating a motion that has already reached its target.",
                    phase_options(self.world, phases, observation), baseline_phase(self.world, observation), self.history)
                if intent is None or not self._wait():
                    return
                with self.lock:
                    if self.cancel.is_set():
                        return
                    self.last_intent = intent
                if intent["selected_probability"] is not None and intent["selected_probability"] < self.threshold:
                    self._uncertain(intent)
                    continue
                phase = intent["choice"]
                self.phase = phase
                if phase == "finish":
                    self._finish()
                    return
                with self.lock:
                    if self.cancel.is_set():
                        return
                    self.stage = "previewing"
                    shadow = self.world.clone()
                options = candidates(shadow, phase, self.preview, observation=observation)
                with self.lock:
                    if self.cancel.is_set():
                        return
                    self.current_candidates = [c.serialise() for c in options]
                legal = [c for c in options if c.admitted]
                progress = [c for c in legal if c.id != "hold"]
                if not progress:
                    raise ValueError("动作预演未找到可执行的前进动作")
                menu = {c.id: json.dumps({"motion": c.id, "phase": phase,
                    "target_tcp": [round(v, 4) for v in c.target] if c.target else None,
                    "gripper_command": c.gripper or "unchanged", "duration_seconds": c.seconds,
                    "preview": c.preview}, separators=(",", ":")) for c in legal}
                decision = self._choose("action", model_observation,
                    "Select the action that progresses the chosen phase. Hold only when moving is unjustified. "
                    f"Chosen phase: {phase}. All offered actions passed the configured simulation checks.",
                    menu, progress[0].id, self.history)
                if decision is None or not self._wait():
                    return
                if decision["selected_probability"] is not None and decision["selected_probability"] < self.threshold:
                    self._uncertain(decision)
                    continue
                selected = next(c for c in legal if c.id == decision["choice"])
                with self.lock:
                    if self.cancel.is_set():
                        return
                    self.cycles += 1
                    self.last_decision = decision
                    self.stage = "executing"
                before = copy.deepcopy(observation)
                bad_contacts = self.world.unsafe_contacts
                motion = self.world.motion(selected.target, selected.gripper, selected.seconds)
                while True:
                    if not self._wait():
                        return
                    # Stop/pause and each physics chunk share ownership of the world.
                    with self.lock:
                        if self.cancel.is_set():
                            return
                        if not self.wake.is_set():
                            continue
                        try:
                            frame = next(motion)
                        except StopIteration:
                            break
                        self._record(frame)
                        if self.world.unsafe_contacts > bad_contacts:
                            raise ValueError("执行层检测到台面或障碍接触，已停止")
                    if self.speed > 0 and self.cancel.wait(.04 / self.speed):
                        return
                with self.lock:
                    after = self._observe()
                    if self.observer:
                        self._record(self.world.frame())
                record = {"cycle": self.cycles, "phase": phase, "label": selected.label, "intent": intent,
                          "decision": decision, "action": selected.serialise(), "before": before, "after": after,
                          "candidates": [c.serialise() for c in options],
                          "decision_inputs": dict(self.last_decision_inputs),
                          "rejected_count": sum(not c.admitted for c in options)}
                with self.lock:
                    self.history.append(record)
                    self.stage = "observing"
                    self._event("action_completed", f"完成动作：{selected.label}")
                if self.world.success():
                    self._finish()
                    return
                if self._stalled():
                    with self.lock:
                        if self.cancel.is_set():
                            return
                        self.status, self.stage = "stalled", "observing"
                        self.message = "连续三次重复选择未带来位姿或接触变化，已停止推理；请查看历史决策后重置。"
                        self.finished = time.perf_counter()
                        self._event("stalled", self.message, "warning")
                    return
                if self.single_step:
                    self.pause()
        except Exception as exc:
            with self.lock:
                if not self.cancel.is_set():
                    import httpx
                    if isinstance(exc, httpx.HTTPStatusError):
                        message = f"模型接口请求失败：HTTP {exc.response.status_code}；请在模型连接中测试配置。"
                    elif isinstance(exc, httpx.HTTPError):
                        message = f"模型接口请求失败：{type(exc).__name__}；请检查连接。"
                    else:
                        message = str(exc)
                    self.status, self.message = "error", message
                    self.finished = time.perf_counter()
                    self._event("error", self.message, "error")
        finally:
            self._close_resources()

    def _apply_intervention(self):
        if not self.intervention or self.interventions or self.cycles < self.intervention["after_cycle"]:
            return
        event = self.world.perturb(self.intervention["kind"], self.intervention["delta_xy"])
        event.update(after_cycle=self.cycles, sim_time=float(self.world.data.time) - self.world.start_time)
        self.interventions.append(event)
        if self.observer:
            self.observer.invalidate()
        self._event("external_intervention", f"外部评测扰动：{event['kind']}，发生于动作 {self.cycles} 后", "warning")

    def _run_incremental(self):
        from .incremental import incremental_candidates, planning_state, baseline_choice, PLANNING_INSTRUCTIONS
        import random

        while not self.cancel.is_set():
            if not self._wait():
                return
            with self.lock:
                if self.cancel.is_set():
                    return
                if self.cycles >= self.max_cycles:
                    self.status, self.message = "exhausted", "已达到动作预算"
                    self.finished = time.perf_counter()
                    self._event("budget_exhausted", self.message, "warning")
                    return
                self._apply_intervention()
                observation = self._observe()
                self._record(self.world.frame())
                model_observation = {**observation, **({"user_context": self.user_context} if self.user_context else {})}
                state = planning_state(model_observation, self.history)
                options = incremental_candidates(observation)
                if self.shuffle_candidates:
                    random.Random(self.world.seed * 1009 + self.cycles).shuffle(options)
                legal = [option for option in options if option.admitted]
                if not legal:
                    raise ValueError("没有工作区内的短步动作")
                serialised = []
                for option in options:
                    item = option.serialise()
                    item["delta_xyz"] = [round(a - b, 5) for a, b in zip(option.target, observation["tcp"])]
                    serialised.append(item)
                menu = {item["id"]: json.dumps({key: item[key] for key in
                        ("delta_xyz", "gripper", "seconds")}, separators=(",", ":"))
                        for item in serialised if item["admitted"]}
                default = baseline_choice(observation, options) if self.policy.provider == "baseline" else None
                images = None
                if self.observation_mode == "vision":
                    capture = self.camera_snapshot()
                    images = [{"rgb": capture["views"][view]["rgb"], "view": view,
                               "capture_id": capture["metadata"]["capture_id"]}
                              for view in self.camera_views]
                self.phase, self.stage = "incremental", "deciding"
                self.current_candidates = serialised
                self.last_decision, self.last_intent = None, None
                self.last_decision_inputs = {"phase": None, "action": None}
            self._event("deciding", "正在根据当前观测规划一个短步动作")
            decision = self._choose("action", state, PLANNING_INSTRUCTIONS, menu,
                                    baseline_choice=default, image=images, plan=True)
            if decision is None or not self._wait():
                return
            if decision["selected_probability"] is not None and decision["selected_probability"] < self.threshold:
                self._uncertain(decision)
                continue
            selected = next(option for option in legal if option.id == decision["choice"])
            rejection = None
            with self.lock:
                if self.cancel.is_set():
                    return
                self.stage = "previewing"
                shadow = self.world.clone()
            try:
                # Safety checks only the model's selected command, never ranks
                # progress or exposes future object positions to the policy.
                if self.preview:
                    bad = shadow.unsafe_contacts
                    for _ in shadow.motion(selected.target, selected.gripper, selected.seconds, emit=False):
                        pass
                    if shadow.unsafe_contacts > bad:
                        rejection = "所选动作的仿真预演发生机械臂与台面或障碍接触"
                else:
                    _, error = shadow.solve_ik(selected.target)
                    if error > .004:
                        rejection = "所选短步目标不可达"
            except (ValueError, RuntimeError):
                rejection = "所选短步未通过可执行性检查"
            if not self._wait():
                return
            with self.lock:
                if self.cancel.is_set():
                    return
                self.cycles += 1
                self.last_decision = decision
                self.stage = "executing" if rejection is None else "observing"
            before = copy.deepcopy(observation)
            if rejection is None:
                bad = self.world.unsafe_contacts
                motion = self.world.motion(selected.target, selected.gripper, selected.seconds)
                while True:
                    if not self._wait():
                        return
                    with self.lock:
                        if self.cancel.is_set():
                            return
                        if not self.wake.is_set():
                            continue
                        try:
                            frame = next(motion)
                        except StopIteration:
                            break
                        self._record(frame)
                        if self.world.unsafe_contacts > bad:
                            raise ValueError("执行层检测到台面或障碍接触，已停止")
                    if self.speed > 0 and self.cancel.wait(.04 / self.speed):
                        return
            with self.lock:
                after = self._observe()
                self._record(self.world.frame())
                action = next(item for item in serialised if item["id"] == selected.id).copy()
                action.update(admitted=rejection is None, rejection=rejection,
                              preview={"source": "simulator_safety_filter", "safe": rejection is None} if self.preview else None)
                self.history.append({"cycle": self.cycles, "phase": "incremental", "label": selected.label,
                    "intent": None, "decision": decision, "action": action,
                    "executed": rejection is None, "rejection": rejection,
                    "before": before, "after": after, "candidates": serialised,
                    "decision_inputs": copy.deepcopy(self.last_decision_inputs),
                    "rejected_count": sum(not option.admitted for option in options) + int(rejection is not None)})
                self.stage = "observing"
                self._event("action_rejected" if rejection else "action_completed",
                            rejection or f"完成短步：{selected.label}", "warning" if rejection else "info")
            if self.world.success():
                self._finish()
                return
            if self._stalled():
                with self.lock:
                    if self.cancel.is_set():
                        return
                    self.status, self.stage = "stalled", "observing"
                    self.message = "连续三次相同短步未产生位姿或接触变化，已停止；没有切换规则策略。"
                    self.finished = time.perf_counter()
                    self._event("stalled", self.message, "warning")
                return
            if self.single_step:
                self.pause()

    def _choose(self, stage, *args, plan=False, **kwargs):
        # Admit each decision under the control lock, then release it before
        # inference. A stop/pause during preview must not start another request;
        # an already admitted request may finish, but cancellation discards its answer.
        while True:
            if not self._wait():
                return None
            with self.lock:
                if self.cancel.is_set():
                    return None
                if not self.wake.is_set():
                    continue
                self.policy.last_input = None
                break
        try:
            return self.policy.choose_plan(*args, **kwargs) if plan else self.policy.choose(*args)
        except Exception as exc:
            # Even a gateway exception can contain a secret. Retain only safe diagnostics.
            import httpx
            reason = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
            raise RuntimeError(f"模型决策失败（{reason}），请查看模型连接测试结果。") from None
        finally:
            with self.lock:
                # Keep inspectable input even when the answer is uncertain,
                # invalid, or discarded after a stop. Never copy connection data.
                self.last_decision_inputs[stage] = copy.deepcopy(self.policy.last_input)

    def _stalled(self):
        """Stop repeated non-progress; this never substitutes a different action."""
        if len(self.history) < 3:
            return False
        recent = self.history[-3:]
        if self.control_mode == "incremental" and len({h["action"]["id"] for h in recent}) != 1:
            return False
        if len({h["phase"] for h in recent}) != 1:
            return False
        if any(math.dist(recent[0]["before"][k], recent[-1]["after"][k]) >= .002
               for k in ("tcp", "object") if k in recent[0]["before"] and k in recent[-1]["after"]):
            return False
        for item in recent:
            before, after = item["before"], item["after"]
            if any(math.dist(before[k], after[k]) >= .002 for k in ("tcp", "object") if k in before and k in after):
                return False
            if any(before[k] != after[k] for k in ("held", "gripper", "finger_contacts", "support_contact")):
                return False
        return True

    def _uncertain(self, decision):
        with self.lock:
            if self.cancel.is_set():
                return
            self.status, self.stage = "uncertain", "deciding"
            self.last_decision = decision
            self.message = "候选动作概率低于设定门槛，等待人工处理"
            self.wake.clear()
            self._event("uncertain", self.message, "warning")

    def _finish(self):
        with self.lock:
            if self.cancel.is_set():
                return
            if not self.world.success():
                raise ValueError("物理成功条件尚未满足")
            self.status, self.stage = "completed", "verified"
            self.finished = time.perf_counter()
            self._event("completed", "物理成功条件验证通过")

    def snapshot(self):
        with self.lock:
            return {"id": self.id, "status": self.status, "stage": self.stage, "phase": self.phase,
                    "task": self.world.task, "seed": self.world.seed, "speed": self.speed,
                    "cycles": self.cycles, "max_cycles": self.max_cycles, "provider": self.policy.provider,
                    "profile_id": self.profile_id, "scene_config": copy.deepcopy(self.world.scene_config),
                    "observation_mode": self.observation_mode,
                    "camera_views": list(self.camera_views),
                    "control_mode": self.control_mode, "intervention": copy.deepcopy(self.intervention),
                    "interventions": copy.deepcopy(self.interventions), "shuffle_candidates": self.shuffle_candidates,
                    "perception": copy.deepcopy((self.camera_snapshot() or {}).get("metadata")),
                    "user_context": copy.deepcopy(self.user_context),
                    "preview": self.preview, "threshold": self.threshold, "message": self.message,
                    "frame": self.last_frame, "history": list(self.history), "candidates": self.current_candidates,
                    "events": list(self.events),
                    "last_decision": self.last_decision, "last_intent": self.last_intent,
                    "last_decision_inputs": dict(self.last_decision_inputs),
                    "frame_count": len(self.frames),
                    "model_calls": self.policy.calls, "input_tokens": self.policy.tokens,
                    "output_tokens": self.policy.output_tokens,
                    "model_runtime": minicpm_status() if self.policy.provider == "minicpm" else None,
                    "wall_seconds": round((self.finished or time.perf_counter()) - self.started, 2) if self.started else 0}

    def replay_frame(self, index):
        with self.lock:
            if not 0 <= index < len(self.frames):
                raise ValueError("Frame index out of range")
            saved = self.frames[index]
            world = self.world.clone()
            world.data.qpos[:] = saved["qpos"]
            import mujoco
            mujoco.mj_forward(world.model, world.data)
            frame = world.frame()
            frame.update(time=saved["time"], observation=saved["observation"])
            if "evaluation_target" in saved:
                for gid in world.target_geom_ids:
                    for axis in (0, 1):
                        frame["positions"][gid][axis] += saved["evaluation_target"][axis] - world.target[axis]
            return frame

    def export(self):
        with self.lock:
            from .incremental import PROMPT_VERSION as INCREMENTAL_VERSION
            return {"format": "embodied-jev-episode-v1", "id": self.id, "task": self.world.task,
                    "seed": self.world.seed, "scene_hash": self.world.scene_hash, "provider": self.policy.provider,
                    "profile_id": self.profile_id, "scene_config": copy.deepcopy(self.world.scene_config),
                    "observation_mode": self.observation_mode,
                    "camera_views": list(self.camera_views),
                    "control_mode": self.control_mode, "intervention": copy.deepcopy(self.intervention),
                    "interventions": copy.deepcopy(self.interventions), "shuffle_candidates": self.shuffle_candidates,
                    "perception_history": copy.deepcopy(self.perception_history),
                    "camera_manifest": self.camera_manifest(),
                    "user_context": copy.deepcopy(self.user_context),
                    "preview": self.preview, "status": self.status, "success": bool(self.world.success()),
                    "model": self.policy.model, "threshold": self.threshold, "max_cycles": self.max_cycles,
                    "policy_version": (INCREMENTAL_VERSION if self.control_mode == "incremental" else POLICY_VERSION),
                    "history": list(self.history), "frames": list(self.frames),
                    "events": list(self.events),
                    "model_calls": self.policy.calls, "input_tokens": self.policy.tokens,
                    "output_tokens": self.policy.output_tokens,
                    "model_runtime": minicpm_status() if self.policy.provider == "minicpm" else None,
                    "model_latency_ms": list(self.policy.latencies), "last_decision": self.last_decision,
                    "last_intent": self.last_intent,
                    "last_decision_inputs": dict(self.last_decision_inputs),
                    "wall_seconds": self.snapshot()["wall_seconds"], "message": self.message,
                    "observation_source": (" + ".join(self.camera_views) + " RGB pixels with simulated proprioception and contacts"
                                           if self.observation_mode == "vision" else
                                           "RGB-D color perception with simulated proprioception and contacts"
                                           if self.observation_mode == "rgbd" else "privileged simulator geometry and contacts"),
                    "safety_source": "privileged simulator safety filter" if self.preview else "live simulator contact checks",
                    "evaluation_source": "privileged simulator physical success conditions"}


def run_headless(task="transfer", seed=0, preview=True, max_cycles=30,
                 provider="baseline", threshold=.55, timeout=600, scene_config=None, user_context=None,
                 observation_mode="privileged", control_mode="skills", intervention=None,
                 shuffle_candidates=False, connection=None, camera_views=None):
    session = Session(task=task, seed=seed, preview=preview, max_cycles=max_cycles,
                      speed=0, provider=provider, threshold=threshold,
                      scene_config=scene_config, user_context=user_context, observation_mode=observation_mode,
                      control_mode=control_mode, intervention=intervention, shuffle_candidates=shuffle_candidates,
                      connection=connection, camera_views=camera_views)
    session.start()
    deadline = time.monotonic() + timeout
    while session.worker.is_alive():
        session.worker.join(.05)
        with session.lock:
            if session.status == "uncertain" or time.monotonic() >= deadline:
                # Preserve uncertainty as an evaluation outcome; no automatic retry or fallback.
                session.cancel.set()
                session.wake.set()
                if session.status != "uncertain":
                    session.status, session.message = "timeout", f"Episode exceeded {timeout} seconds"
                session.finished = time.perf_counter()
                if session.status == "timeout":
                    session._event("timeout", session.message, "warning")
                break
    session.worker.join(1)
    return session
