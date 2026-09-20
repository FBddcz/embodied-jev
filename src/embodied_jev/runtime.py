from __future__ import annotations

import copy
import json
import math
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from .physics import RobotWorld
from .planning import PHASES, baseline_phase, candidates, eligible_phases, phase_options
from .policies import DecisionPolicy, minicpm_status
from .evidence import PROMPT_VERSION, validate_user_context
from .eventlog import write_event

POLICY_VERSION = PROMPT_VERSION


class Session:
    def __init__(self, task="transfer", seed=0, provider="baseline", preview=True,
                 threshold=.55, max_cycles=30, speed=1.5, connection=None,
                 scene_config=None, user_context=None):
        self.id = uuid.uuid4().hex[:12]
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

    def _event(self, event, message, level="info"):
        with self.lock:
            entry = {"time": datetime.now(timezone.utc).isoformat(), "event": event,
                     "level": level, "message": message, "cycle": self.cycles,
                     "episode_id": self.id}
            self.events.append(entry)
        write_event(entry)

    def _record(self, frame):
        with self.lock:
            self.last_frame = frame
            self.frames.append({"time": frame["time"], "qpos": frame["qpos"],
                                "observation": frame["observation"], "cycle": self.cycles,
                                "phase": self.phase})

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
        if self.worker is None:
            self.policy.close()

    def _wait(self):
        while not self.cancel.is_set():
            if self.wake.wait(.1):
                return not self.cancel.is_set()
        return False

    def _run(self):
        try:
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
                    observation = self.world.observe()
                    model_observation = {**observation, **({"user_context": self.user_context} if self.user_context else {})}
                    phases = eligible_phases(self.world)
                    self.stage = "deciding"
                    self.last_decision = None
                    self.last_intent = None
                    self.last_decision_inputs = {"phase": None, "action": None}
                    self.current_candidates = []
                self._event("deciding", "正在选择操作阶段")
                intent = self._choose("phase", model_observation,
                    "Choose the next phase that makes progress toward the goal, given the measured geometry and contacts. "
                    "Avoid repeating a motion that has already reached its target.",
                    phase_options(self.world, phases), baseline_phase(self.world), self.history)
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
                options = candidates(shadow, phase, self.preview)
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
                before = self.world.observe()
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
                after = self.world.observe()
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
            self.policy.close()

    def _choose(self, stage, *args):
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
            return self.policy.choose(*args)
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
        if len({h["phase"] for h in recent}) != 1:
            return False
        if any(math.dist(recent[0]["before"][k], recent[-1]["after"][k]) >= .002
               for k in ("tcp", "object")):
            return False
        for item in recent:
            before, after = item["before"], item["after"]
            if any(math.dist(before[k], after[k]) >= .002 for k in ("tcp", "object")):
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
            return frame

    def export(self):
        with self.lock:
            return {"format": "embodied-jev-episode-v1", "id": self.id, "task": self.world.task,
                    "seed": self.world.seed, "scene_hash": self.world.scene_hash, "provider": self.policy.provider,
                    "profile_id": self.profile_id, "scene_config": copy.deepcopy(self.world.scene_config),
                    "user_context": copy.deepcopy(self.user_context),
                    "preview": self.preview, "status": self.status, "success": self.last_frame["observation"]["success"],
                    "model": self.policy.model, "threshold": self.threshold, "max_cycles": self.max_cycles,
                    "policy_version": POLICY_VERSION,
                    "history": list(self.history), "frames": list(self.frames),
                    "events": list(self.events),
                    "model_calls": self.policy.calls, "input_tokens": self.policy.tokens,
                    "output_tokens": self.policy.output_tokens,
                    "model_runtime": minicpm_status() if self.policy.provider == "minicpm" else None,
                    "model_latency_ms": list(self.policy.latencies), "last_decision": self.last_decision,
                    "last_intent": self.last_intent,
                    "last_decision_inputs": dict(self.last_decision_inputs),
                    "wall_seconds": self.snapshot()["wall_seconds"], "message": self.message,
                    "observation_source": "privileged simulator geometry and contacts"}


def run_headless(task="transfer", seed=0, preview=True, max_cycles=30,
                 provider="baseline", threshold=.55, timeout=600, scene_config=None, user_context=None):
    session = Session(task=task, seed=seed, preview=preview, max_cycles=max_cycles,
                      speed=0, provider=provider, threshold=threshold,
                      scene_config=scene_config, user_context=user_context)
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
