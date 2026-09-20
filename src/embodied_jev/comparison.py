from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
import logging
import math
import threading
import time
import uuid
from urllib.parse import urlsplit

from .policies import MODEL, configurations, environment_connection
from .runtime import Session

logger = logging.getLogger(__name__)
API_PROVIDERS = {"jev", "chat", "local", "claude"}
TERMINAL = {"completed", "error", "exhausted", "stalled", "timeout", "uncertain", "stopped"}


def resolve_model(provider, connections, *, model=None, profile_id=None, profiles=None):
    """Resolve an immutable per-run connection; never copy credentials to output."""
    ready = {item["id"]: item["ready"] for item in configurations(connections)}
    if profile_id is not None:
        profile = (profiles or {}).get(profile_id)
        if profile is None:
            raise ValueError("未找到该模型配置，请刷新配置列表。")
        if profile["provider"] != provider or provider not in API_PROVIDERS:
            raise ValueError("模型配置与所选接口类型不一致。")
        connection = {key: profile[key] for key in ("url", "model", "key", "json_mode")}
        connection["profile_id"] = profile_id
    else:
        if not ready.get(provider):
            raise ValueError(f"{provider} 尚未配置，请先在模型连接中完成配置。")
        connection = dict(connections.get(provider, environment_connection(provider)))
    if provider not in API_PROVIDERS:
        if model is not None:
            raise ValueError("规则基线和本地 MiniCPM 使用固定模型，不支持模型名覆盖。")
        return {"provider": provider, "model": MODEL if provider == "minicpm" else "baseline", "connection": None, "profile_id": None}
    selected_model = model if model is not None else connection.get("model", "")
    parsed = urlsplit(connection.get("url", ""))
    if not isinstance(selected_model, str) or not selected_model.strip() or len(selected_model) > 256:
        raise ValueError(f"{provider} 需要有效的模型名称。")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{provider} 需要有效的 API 地址。")
    if provider in {"jev", "claude"} and not connection.get("key"):
        raise ValueError(f"{provider} 尚未配置 API Key。")
    connection["model"] = selected_model.strip()
    return {"provider": provider, "model": connection["model"], "connection": connection, "profile_id": profile_id}


def resolve_lanes(lanes, connections, profiles=None):
    """Validate every lane before constructing a world or stopping an old run."""
    if not 2 <= len(lanes) <= 3:
        raise ValueError("请选择 2–3 路模型进行比较。")
    return [resolve_model(item["provider"], connections, model=item.get("model"),
                          profile_id=item.get("profile_id"), profiles=profiles) for item in lanes]


class Comparison:
    """One bounded group of independent episodes; a lane keeps Session's control flow."""

    def __init__(self, lanes, *, task="transfer", seed=0, preview=True, threshold=.55,
                 max_cycles=30, speed=1.5, mode="sequential", scene_config=None, user_context=None, secrets=()):
        if mode not in {"sequential", "parallel"} or not 2 <= len(lanes) <= 3:
            raise ValueError("比较模式或模型数量无效。")
        self.id = uuid.uuid4().hex[:12]
        self.mode = mode
        self.max_parallel = 1 if mode == "sequential" else 2
        self.config = {"task": task, "seed": seed, "preview": preview, "threshold": threshold,
                       "max_cycles": max_cycles, "speed": speed,
                       "scene_config": deepcopy(scene_config), "user_context": deepcopy(user_context)}
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.changed = threading.Event()
        self.worker = None
        self.status = "idle"
        self.lanes = []
        self._secrets = tuple(sorted(set(secrets) | {lane["connection"].get("key") for lane in lanes
                                                   if lane.get("connection") and lane["connection"].get("key")}, key=len, reverse=True))
        try:
            for index, lane in enumerate(lanes):
                session = Session(**self.config, provider=lane["provider"], connection=lane["connection"])
                self.lanes.append({"id": f"lane-{index + 1}", "provider": lane["provider"],
                                   "profile_id": lane.get("profile_id"), "requested_model": lane["model"],
                                   "status": "queued", "session": session})
        except Exception:
            for lane in self.lanes:
                lane["session"].stop()
            raise
        self._event("comparison_created")

    def _event(self, event, lane=None):
        fields = {"event": event, "comparison_id": self.id}
        if lane is not None:
            fields["lane_id"] = lane["id"]
            fields["provider"] = lane["provider"]
        logger.info("%s comparison_id=%s lane_id=%s", event, self.id, lane["id"] if lane else "", extra=fields)

    def _public(self, value):
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, "[已隐藏]")
            return value
        if isinstance(value, dict):
            return {self._public(key): self._public(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._public(item) for item in value]
        return value

    def _refresh_locked(self):
        for lane in self.lanes:
            if lane["status"] in {"queued", "done", "stopped"}:
                continue
            session = lane["session"]
            with session.lock:
                status = session.status
                if status == "uncertain":
                    # This experiment records abstention. Do not silently lower its
                    # threshold, retry forever, or replace the model with a baseline.
                    session.cancel.set()
                    session.wake.set()
                    session.finished = session.finished or time.perf_counter()
                alive = session.worker is not None and session.worker.is_alive()
            if status in TERMINAL and not alive:
                lane["status"] = "stopped" if status == "stopped" else "done"
                self._event("comparison_lane_finished", lane)
        if self.status not in {"idle", "stopped", "done"} and all(lane["status"] in {"done", "stopped"} for lane in self.lanes):
            self.status = "done"
            self._event("comparison_finished")

    def start(self):
        with self.lock:
            if self.status == "running":
                return
            if self.status in {"done", "stopped"}:
                raise ValueError("此比较已结束，请创建新的比较。")
            self.status = "running"
            self._refresh_locked()
            for lane in self.lanes:
                if lane["status"] == "paused":
                    session = lane["session"]
                    with session.lock:
                        resumable = session.status == "paused" and not session.cancel.is_set()
                    if resumable:
                        session.start()
                    lane["status"] = "running"
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._run, name=f"comparison-{self.id}", daemon=True)
                self.worker.start()
            self.changed.set()
            self._event("comparison_started")

    def pause(self):
        with self.lock:
            if self.status != "running":
                return
            self.status = "paused"
            for lane in self.lanes:
                if lane["status"] == "running":
                    lane["session"].pause()
                    lane["status"] = "paused"
            self.changed.set()
            self._event("comparison_paused")

    def stop(self):
        with self.lock:
            self.cancel.set()
            self.changed.set()
            if self.status in {"done", "stopped"}:
                return
            self.status = "stopped"
            for lane in self.lanes:
                if lane["status"] != "done":
                    lane["session"].stop()
                    lane["status"] = "stopped"
            self._event("comparison_stopped")

    def has_live_workers(self):
        with self.lock:
            return any(lane["session"].worker is not None and lane["session"].worker.is_alive() for lane in self.lanes)

    def _run(self):
        while not self.cancel.is_set():
            with self.lock:
                self._refresh_locked()
                if self.status in {"done", "stopped"}:
                    return
                if self.status == "running":
                    active = sum(lane["status"] in {"running", "paused"} for lane in self.lanes)
                    for lane in self.lanes:
                        if active >= self.max_parallel:
                            break
                        if lane["status"] == "queued":
                            lane["session"].start()
                            lane["status"] = "running"
                            active += 1
                            self._event("comparison_lane_started", lane)
            self.changed.wait(.03)
            self.changed.clear()

    @staticmethod
    def _bounds(session):
        with session.lock:
            return session.frames[0]["time"], max(0, session.frames[-1]["time"] - session.frames[0]["time"])

    def snapshot(self):
        with self.lock:
            self._refresh_locked()
            lanes = [{"id": lane["id"], "provider": lane["provider"], "requested_model": lane["requested_model"],
                      "profile_id": lane["profile_id"],
                      "model": lane["session"].policy.model, "status": lane["status"], "session": lane["session"].snapshot()}
                     for lane in self.lanes]
            ends = [self._bounds(lane["session"])[1] for lane in self.lanes]
            notes = ["各路使用独立仿真；阶段选择完成后才进行动作选择。"]
            if any(lane["provider"] == "minicpm" for lane in self.lanes):
                notes.append("本地 MiniCPM 共享权重与推理锁；并行模式下 MiniCPM 推理仍串行执行。")
            if self.mode == "parallel":
                notes.append("并行最多运行两路；共享硬件与网络，耗时不能视为隔离环境性能基准。")
            return self._public({"id": self.id, "status": self.status, "mode": self.mode, "max_parallel": self.max_parallel,
                                 "config": self.config, "lanes": lanes, "notes": notes,
                                 "stopping": self.status == "stopped" and self.has_live_workers(),
                                 "replay": {"max_time": max(ends), "common_time": min(ends), "time_basis": "elapsed_simulation_seconds"}})

    def scene(self, lane_id):
        with self.lock:
            lane = next((item for item in self.lanes if item["id"] == lane_id), None)
            if lane is None:
                raise ValueError("未找到该比较通道。")
            session = lane["session"]
        with session.lock:
            return {**session.world.scene(), "comparison_id": self.id, "lane_id": lane_id}

    @staticmethod
    def _historical_decision(session, saved):
        cycle = saved["cycle"]
        if not cycle:
            return None
        record = next((record for record in session.history if record["cycle"] == cycle), None)
        if record is None:
            if cycle != session.cycles or not session.last_decision:
                return None
            return {"cycle": cycle, "phase": saved["phase"], "intent": deepcopy(session.last_intent),
                    "decision": deepcopy(session.last_decision), "candidates": deepcopy(session.current_candidates),
                    "decision_inputs": None, "before": None, "after": None, "completed": False}
        record = deepcopy(record)
        record["completed"] = record["after"]["sim_seconds"] <= saved["observation"]["sim_seconds"] + 1e-6
        if not record["completed"]:
            record["after"] = None
        return record

    def replay(self, requested_time):
        if not math.isfinite(requested_time) or requested_time < 0:
            raise ValueError("回放时间必须是非负有限秒数。")
        with self.lock:
            lanes = list(self.lanes)
        result = []
        for lane in lanes:
            session = lane["session"]
            with session.lock:
                origin = session.frames[0]["time"]
                times = [max(0, frame["time"] - origin) for frame in session.frames]
                index = max(0, bisect_right(times, requested_time + 1e-9) - 1)
                saved = session.frames[index]
                frame = session.replay_frame(index)
                frame.update(cycle=saved["cycle"], phase=saved["phase"])
                result.append({"id": lane["id"], "provider": lane["provider"], "frame": frame, "frame_index": index,
                               "frame_time": times[index], "available_until": times[-1], "clamped": requested_time > times[-1] + 1e-9,
                               "decision": self._historical_decision(session, saved)})
        return self._public({"id": self.id, "time": requested_time, "lanes": result})

    def export(self):
        with self.lock:
            return self._public({"format": "embodied-jev-comparison-v1", "id": self.id, "status": self.status,
                                 "mode": self.mode, "max_parallel": self.max_parallel, "config": self.config,
                                 "time_basis": "elapsed_simulation_seconds", "lanes": [
                                     {"id": lane["id"], "provider": lane["provider"], "requested_model": lane["requested_model"],
                                      "profile_id": lane["profile_id"],
                                      "status": lane["status"], "time_origin": self._bounds(lane["session"])[0],
                                      "episode": lane["session"].export()} for lane in self.lanes]})
