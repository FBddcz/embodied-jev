from __future__ import annotations

import json
import threading
import time
import uuid

from .physics import RobotWorld
from .planning import PHASES, baseline_phase, candidates, eligible_phases
from .policies import DecisionPolicy, minicpm_status


class Session:
    def __init__(self, task="transfer", seed=0, provider="baseline", preview=True,
                 threshold=.55, max_cycles=30, speed=1.5, connection=None):
        self.id = uuid.uuid4().hex[:12]
        self.world = RobotWorld(task, seed)
        self.policy = DecisionPolicy(provider, connection)
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
        self.frames = []
        self.last_frame = self.world.frame()
        self.current_candidates = []
        self.last_decision = None
        self.message = None
        self.single_step = False
        self.started = None
        self.finished = None
        self._record(self.last_frame)

    def _record(self, frame):
        with self.lock:
            self.last_frame = frame
            self.frames.append({"time": frame["time"], "qpos": frame["qpos"],
                                "observation": frame["observation"], "cycle": self.cycles,
                                "phase": self.phase})

    def start(self, single_step=False, threshold=None):
        with self.lock:
            if self.status in {"completed", "stopped", "error", "exhausted"}:
                raise ValueError("Reset the episode before starting again")
            if threshold is not None:
                if not 0 <= threshold <= 1:
                    raise ValueError("Threshold must be in [0, 1]")
                self.threshold = threshold
            self.status = "running"
            self.single_step = single_step
            self.message = None
            if self.started is None:
                self.started = time.perf_counter()
            self.wake.set()
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._run, daemon=True)
                self.worker.start()

    def pause(self):
        with self.lock:
            if self.status == "running":
                self.status = "paused"
                self.wake.clear()

    def stop(self):
        with self.lock:
            self.cancel.set()
            self.wake.set()
            if self.status != "completed":
                self.status = "stopped"
            self.finished = time.perf_counter()

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
                        self.status, self.message = "exhausted", "已达到动作预算"
                        self.finished = time.perf_counter()
                    return
                with self.lock:
                    if self.cancel.is_set():
                        return
                    observation = self.world.observe()
                    phases = eligible_phases(self.world)
                    self.stage = "deciding"
                    self.last_decision = None
                    self.current_candidates = []
                intent = self.policy.choose(observation,
                    "Choose the next manipulation phase using measured contacts and object geometry. "
                    "Never claim success unless observation.success is true.",
                    {p: PHASES[p] for p in phases}, baseline_phase(self.world), self.history)
                if not self._wait():
                    return
                if intent["selected_probability"] is not None and intent["selected_probability"] < self.threshold:
                    self._uncertain(intent)
                    continue
                phase = intent["choice"]
                self.phase = phase
                if phase == "finish":
                    self._finish()
                    return
                with self.lock:
                    self.stage = "previewing"
                with self.lock:
                    shadow = self.world.clone()
                options = candidates(shadow, phase, self.preview)
                with self.lock:
                    self.current_candidates = [c.serialise() for c in options]
                legal = [c for c in options if c.admitted]
                progress = [c for c in legal if c.id != "hold"]
                if not progress:
                    raise ValueError("动作预演未找到可执行的前进动作")
                menu = {c.id: f"{c.label}; target={c.target}; predicted outcome={json.dumps(c.preview)}" for c in legal}
                decision = self.policy.choose(observation,
                    "Select the action that progresses the chosen phase. Hold only when moving is unjustified. "
                    f"Chosen phase: {phase}. All offered actions passed the configured simulation checks.",
                    menu, progress[0].id, self.history)
                if not self._wait():
                    return
                if decision["selected_probability"] is not None and decision["selected_probability"] < self.threshold:
                    self._uncertain(decision)
                    continue
                selected = next(c for c in legal if c.id == decision["choice"])
                with self.lock:
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
                          "rejected_count": sum(not c.admitted for c in options)}
                with self.lock:
                    self.history.append(record)
                    self.stage = "observing"
                if self.world.success():
                    self._finish()
                    return
                if self.single_step:
                    self.pause()
        except Exception as exc:
            with self.lock:
                if not self.cancel.is_set():
                    self.status, self.message = "error", str(exc)
                    self.finished = time.perf_counter()

    def _uncertain(self, decision):
        with self.lock:
            if self.cancel.is_set():
                return
            self.status, self.stage = "uncertain", "deciding"
            self.last_decision = decision
            self.message = "候选动作概率低于设定门槛，等待人工处理"
            self.wake.clear()

    def _finish(self):
        with self.lock:
            if self.cancel.is_set():
                return
            if not self.world.success():
                raise ValueError("物理成功条件尚未满足")
            self.status, self.stage = "completed", "verified"
            self.finished = time.perf_counter()

    def snapshot(self):
        with self.lock:
            return {"id": self.id, "status": self.status, "stage": self.stage, "phase": self.phase,
                    "task": self.world.task, "seed": self.world.seed, "speed": self.speed,
                    "cycles": self.cycles, "max_cycles": self.max_cycles, "provider": self.policy.provider,
                    "preview": self.preview, "threshold": self.threshold, "message": self.message,
                    "frame": self.last_frame, "history": list(self.history), "candidates": self.current_candidates,
                    "last_decision": self.last_decision, "frame_count": len(self.frames),
                    "model_calls": self.policy.calls, "input_tokens": self.policy.tokens,
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
                    "preview": self.preview, "status": self.status, "success": self.last_frame["observation"]["success"],
                    "model": self.policy.model, "threshold": self.threshold, "max_cycles": self.max_cycles,
                    "history": list(self.history), "frames": list(self.frames),
                    "model_calls": self.policy.calls, "input_tokens": self.policy.tokens,
                    "model_runtime": minicpm_status() if self.policy.provider == "minicpm" else None,
                    "model_latency_ms": list(self.policy.latencies), "last_decision": self.last_decision,
                    "wall_seconds": self.snapshot()["wall_seconds"], "message": self.message,
                    "observation_source": "privileged simulator geometry and contacts"}


def run_headless(task="transfer", seed=0, preview=True, max_cycles=30,
                 provider="baseline", threshold=.55, timeout=600):
    session = Session(task=task, seed=seed, preview=preview, max_cycles=max_cycles,
                      speed=0, provider=provider, threshold=threshold)
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
                break
    session.worker.join(1)
    return session
