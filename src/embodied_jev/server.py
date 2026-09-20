from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import urlsplit
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .physics import TASKS
from .policies import DecisionPolicy, configurations, environment_connection
from .runtime import Session
from .comparison import Comparison, resolve_lanes, resolve_model
from .connection_store import memory_storage

logger = logging.getLogger(__name__)


def _bounded_json(value, limit=8192):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise ValueError("请提供包含有限数值的有效 JSON。") from None
    if len(encoded) > limit:
        raise ValueError(f"JSON 内容不能超过 {limit // 1024} KB。")
    return value


class ScenarioInput(BaseModel):
    scene_config: dict | None = None
    user_context: dict | None = None

    @model_validator(mode="after")
    def validate_scenario(self):
        _bounded_json(self.scene_config)
        _bounded_json(self.user_context)
        if self.user_context is not None:
            from .evidence import validate_user_context
            self.user_context = validate_user_context(self.user_context)
        if self.scene_config is not None:
            from .scenarios import validate_scene_config
            self.scene_config = validate_scene_config(self.task, self.scene_config)
        return self


class Setup(ScenarioInput):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    task: Literal["transfer", "stack", "barrier"] = "transfer"
    seed: int = Field(default=0, ge=0, le=99999)
    provider: Literal["baseline", "jev", "minicpm", "local", "chat", "claude"] = "baseline"
    preview: bool = True
    threshold: float = Field(default=.55, ge=0, le=1)
    max_cycles: int = Field(default=30, ge=1, le=100)
    speed: float = Field(default=1.5, ge=.25, le=4)
    expected_episode_id: str | None = Field(default=None, min_length=1, max_length=64)
    profile_id: str | None = Field(default=None, min_length=1, max_length=64)


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    threshold: float | None = Field(default=None, ge=0, le=1)
    episode_id: str | None = Field(default=None, min_length=1, max_length=64)


class ComparisonLane(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["baseline", "jev", "minicpm", "local", "chat", "claude"]
    model: str | None = Field(default=None, min_length=1, max_length=256)
    profile_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("model", mode="before")
    @classmethod
    def strip_model(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class ComparisonSetup(ScenarioInput):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    lanes: list[ComparisonLane] = Field(min_length=2, max_length=3)
    task: Literal["transfer", "stack", "barrier"] = "transfer"
    seed: int = Field(default=0, ge=0, le=99999)
    preview: bool = True
    threshold: float = Field(default=.55, ge=0, le=1)
    max_cycles: int = Field(default=30, ge=1, le=100)
    speed: float = Field(default=1.5, ge=.25, le=4)
    mode: Literal["sequential", "parallel"] = "sequential"
    expected_comparison_id: str | None = Field(default=None, min_length=1, max_length=64)


class ComparisonControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comparison_id: str = Field(min_length=1, max_length=64)


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["jev", "chat", "local", "claude"]
    url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    api_key: SecretStr = SecretStr("")
    json_mode: bool = True

    @field_validator("model")
    @classmethod
    def validate_model(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Model name must not be blank")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Use an HTTP(S) URL without credentials, query or fragment")
        return value.rstrip("/")


class ModelProfileInput(ConnectionInput):
    id: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value):
        if not value.strip():
            raise ValueError("模型配置名称不能为空。")
        return value.strip()


class DecisionProbe(ComparisonLane):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    observation: dict
    question: str = Field(min_length=1, max_length=4000)
    options: dict[str, str] = Field(min_length=2, max_length=12)

    @field_validator("observation")
    @classmethod
    def validate_observation(cls, value):
        return _bounded_json(value)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value):
        if not value.strip():
            raise ValueError("决策问题不能为空。")
        return value.strip()

    @field_validator("options")
    @classmethod
    def validate_options(cls, value):
        if any(not 1 <= len(key) <= 80 or key != key.strip() or any(ord(char) < 32 for char in key)
               or not description.strip() or len(description) > 1000 for key, description in value.items()):
            raise ValueError("候选名称需为 1–80 个可见字符，描述需为 1–1000 个字符。")
        return value


def _connection_settings(value, previous):
    url = value.url
    if value.provider == "jev" and url != "https://api.typesafe.ai/v1/systemone":
        raise HTTPException(422, "TypeSafe Jev uses its official endpoint")
    if value.provider == "chat" and not url.endswith("/chat/completions"):
        url += "/chat/completions"
    if value.provider == "claude" and not url.endswith("/messages"):
        url += "/messages"
    key = value.api_key.get_secret_value().strip()
    if not key and previous.get("url") == url and previous.get("provider", value.provider) == value.provider:
        key = previous.get("key", "")
    if value.provider in {"jev", "claude"} and not key:
        raise HTTPException(422, f"{value.provider} API key is required")
    return {"url": url, "model": value.model, "key": key, "json_mode": value.json_mode}


def _without_keys(value, secrets):
    if isinstance(value, str):
        for key in secrets:
            if key:
                value = value.replace(key, "[已隐藏]")
        return value
    if isinstance(value, dict):
        return {_without_keys(key, secrets): _without_keys(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_without_keys(item, secrets) for item in value]
    return value


def _reject_saved_keys(value, secrets):
    encoded = json.dumps(value, ensure_ascii=False)
    if any(key and key in encoded for key in secrets):
        raise HTTPException(422, "输入包含已保存的 API Key，请移除凭证后再提交。")


def _public_profile(profile):
    return _without_keys({key: profile[key] for key in ("id", "name", "provider", "url", "model", "json_mode")}
                         | {"key_configured": bool(profile["key"])}, [profile["key"]])


def _connection_error_message(exc):
    # Do not include exception text, URLs, headers, or provider response bodies.
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        messages = {
            401: "认证失败（HTTP 401）：请检查 API Key 是否正确或已过期。",
            403: "访问被拒绝（HTTP 403）：请确认账号已获 API 和所选模型的访问权限。",
            404: "未找到接口或模型（HTTP 404）：请检查 API 地址和模型名称。",
            429: "请求受限（HTTP 429）：请检查额度与请求频率，稍后重试。",
        }
        if status in messages:
            return messages[status]
        if status >= 500:
            return f"服务暂时不可用（HTTP {status}）：请稍后重试。"
        return f"服务拒绝了连接测试（HTTP {status}）：请检查地址、模型与 API 配置。"
    if isinstance(exc, httpx.TimeoutException):
        return "连接测试超时：请检查网络或模型服务负载，稍后重试。"
    if isinstance(exc, httpx.RequestError):
        return "无法连接模型服务：请检查 API 地址、网络和本地服务是否已启动。"
    if isinstance(exc, (ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError)):
        return "响应格式不符合决策接口：请检查所选接口类型、模型和结构化输出支持。"
    return "连接测试未完成：请检查模型服务状态后重试。"


def _untested_connection():
    return {"status": "untested", "checked_at": None, "model": None, "latency_ms": None,
            "message": "尚未测试。保存配置不会验证 API。"}


def _public_model(model, connection, *, required=True):
    if not isinstance(model, str) or not model.strip() or len(model) > 256:
        if required:
            raise ValueError("Invalid model in provider response")
        return None
    key = connection.get("key", "")
    return model.replace(key, "[已隐藏]") if key else model


def create_app(store=None):
    @asynccontextmanager
    async def lifespan(app):
        yield
        app.state.session.stop()
        if app.state.comparison is not None:
            app.state.comparison.stop()

    app = FastAPI(title="EmbodiedJev", lifespan=lifespan)
    app.state.session = Session()
    app.state.lock = threading.Lock()
    app.state.persistence_lock = threading.Lock()
    app.state.connections = {}
    app.state.model_profiles = {}
    app.state.connection_storage = {}
    app.state.profile_storage = {}
    app.state.storage = memory_storage()
    app.state.store = store
    if store is not None:
        try:
            restored = store.load()
            app.state.connections = restored["connections"]
            app.state.model_profiles = restored["profiles"]
            app.state.connection_storage = restored["connection_storage"]
            app.state.profile_storage = restored["profile_storage"]
            app.state.storage = restored["storage"]
        except Exception:
            app.state.storage = memory_storage("无法恢复系统保存的连接，当前使用内存模式；未读取其他凭证，也未写入明文 Key。")
    app.state.verifications = {}
    app.state.connection_test_ids = {}
    app.state.connection_test_slots = threading.BoundedSemaphore(2)
    app.state.comparison = None
    app.state.comparison_lock = threading.Lock()

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request, exc):
        # Invalid JSON can include credentials or non-finite numbers. Returning
        # Pydantic's raw `input` both exposes those values and can break JSON encoding.
        return JSONResponse(public_result({"detail": [{key: error[key] for key in ("loc", "type", "msg")}
                                                       for error in exc.errors()]}), status_code=422)

    def current_session():
        with app.state.lock:
            return app.state.session

    def current_comparison(comparison_id):
        with app.state.comparison_lock:
            comparison = app.state.comparison
            if comparison is None:
                raise HTTPException(404, "尚未创建模型比较。")
            if comparison.id != comparison_id:
                raise HTTPException(409, "比较已更新，请刷新比较状态后再操作。")
            return comparison

    def check_episode(expected, session):
        if expected is not None and expected != session.id:
            logger.info("stale_episode_request episode_id=%s", session.id,
                        extra={"event": "stale_episode_request", "episode_id": session.id})
            raise HTTPException(409, "实验已被其他页面重置，请刷新当前实验状态后再操作。")

    def connection_snapshot(provider):
        item = app.state.connections.get(provider, environment_connection(provider))
        return {"url": item["url"], "model": item["model"], "key": item.get("key", ""),
                "json_mode": item.get("json_mode", True)}

    def configured_secrets():
        # Caller holds app.state.lock. Inspect only this service's configured values.
        keys = [item.get("key") for item in app.state.connections.values()]
        keys += [item.get("key") for item in app.state.model_profiles.values()]
        keys += [environment_connection(provider).get("key") for provider in ("jev", "chat", "local", "claude")]
        return tuple(sorted({key for key in keys if key}, key=len, reverse=True))

    def public_result(value, extra_secrets=()):
        with app.state.lock:
            secrets = tuple(sorted(set(configured_secrets()) | set(extra_secrets), key=len, reverse=True))
        return _without_keys(value, secrets)

    def persist_configuration(kind, value, provider=None):
        storage = memory_storage()
        if app.state.store is not None:
            try:
                storage = (app.state.store.save_connection(provider, value) if kind == "connection"
                           else app.state.store.save_profile(value))
            except Exception:
                storage = memory_storage("安全存储未完成，本次修改仅保留内存；未写入明文 Key，重启后本次修改不会恢复。")
        with app.state.lock:
            app.state.storage = storage
            if kind == "connection":
                app.state.connection_storage[provider] = storage
            else:
                app.state.profile_storage[value["id"]] = storage
        return storage

    def verification_snapshot(provider, connection):
        saved = app.state.verifications.get(provider)
        if saved and saved["connection"] == connection:
            return dict(saved["result"])
        return _untested_connection()

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            allowed = {f"http://127.0.0.1:{request.url.port}", f"http://localhost:{request.url.port}"}
            if origin and origin not in allowed:
                return JSONResponse({"detail": "Cross-origin control is disabled"}, status_code=403)
        return await call_next(request)

    @app.get("/api/config")
    def config():
        return {"name": "EmbodiedJev", "chinese_name": "行知", "version": "0.1.0", "tasks": TASKS,
                "providers": configurations(app.state.connections), "robot": "Franka Panda", "physics": "MuJoCo 3.13"}

    @app.get("/api/connections")
    def connections():
        result = {}
        with app.state.lock:
            for provider in ("jev", "chat", "local", "claude"):
                item = connection_snapshot(provider)
                result[provider] = {"url": item["url"], "model": item["model"],
                                    "key_configured": bool(item["key"]), "json_mode": item["json_mode"],
                                    "storage": app.state.connection_storage.get(provider, memory_storage()),
                                    "verification": verification_snapshot(provider, item)}
        return public_result(result)

    @app.post("/api/connections")
    def save_connection(value: ConnectionInput):
        # Serialize saves without blocking stop/state requests while the OS asks
        # for Keychain permission. This also keeps disk commits in request order.
        with app.state.persistence_lock:
            with app.state.lock:
                previous = connection_snapshot(value.provider)
                updated = _connection_settings(value, previous)
                _reject_saved_keys({"url": updated["url"], "model": updated["model"]},
                                   (*configured_secrets(), updated["key"]))
                if previous != updated:
                    app.state.verifications.pop(value.provider, None)
                    app.state.connection_test_ids[value.provider] = app.state.connection_test_ids.get(value.provider, 0) + 1
                app.state.connections[value.provider] = updated
                verification = verification_snapshot(value.provider, updated)
            storage = persist_configuration("connection", updated, value.provider)
        return public_result({"saved": True, "provider": value.provider, "key_configured": bool(updated["key"]),
                              "verification": verification, "storage": storage})

    @app.get("/api/model-profiles")
    def model_profiles():
        with app.state.lock:
            result = {"profiles": [{**_public_profile(profile), "storage": app.state.profile_storage.get(profile["id"], memory_storage())}
                                    for profile in app.state.model_profiles.values()], "storage": app.state.storage}
        return public_result(result)

    @app.post("/api/model-profiles")
    def save_model_profile(value: ModelProfileInput):
        with app.state.persistence_lock:
            with app.state.lock:
                previous = app.state.model_profiles.get(value.id) if value.id else None
                if value.id and previous is None:
                    raise HTTPException(404, "未找到该模型配置，请刷新配置列表。")
                profile = {**_connection_settings(value, previous or {}), "id": value.id or uuid.uuid4().hex[:12],
                           "name": value.name, "provider": value.provider}
                _reject_saved_keys({key: profile[key] for key in ("url", "model", "name")},
                                   (*configured_secrets(), profile["key"]))
                app.state.model_profiles[profile["id"]] = profile
            storage = persist_configuration("profile", profile)
        return public_result({**_public_profile(profile), "storage": storage})

    @app.post("/api/decision/probe")
    def decision_probe(value: DecisionProbe):
        if not app.state.connection_test_slots.acquire(blocking=False):
            raise HTTPException(429, "已有两个连接或决策测试正在进行，请稍后重试。", headers={"Retry-After": "1"})
        policy = None
        try:
            with app.state.lock:
                try:
                    selected = resolve_model(value.provider, app.state.connections, model=value.model,
                                             profile_id=value.profile_id, profiles=app.state.model_profiles)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None
                secrets = configured_secrets()
                _reject_saved_keys({"observation": value.observation, "question": value.question,
                                    "options": value.options, "model": value.model}, secrets)
            logger.info("decision_probe_started provider=%s", value.provider,
                        extra={"event": "decision_probe_started", "provider": value.provider})
            try:
                policy = DecisionPolicy(value.provider, selected["connection"])
                decision = policy.choose(value.observation, value.question, value.options, next(iter(value.options)), [])
                result = {"provider": value.provider, "profile_id": value.profile_id, "model": policy.model,
                          "decision": decision, "decision_input": policy.last_input,
                          "message": "规则基线固定选择第一个候选；未调用模型，也未验证语义。" if value.provider == "baseline"
                          else "本次只进行了候选决策，没有执行机器人动作。"}
                logger.info("decision_probe_finished provider=%s", value.provider,
                            extra={"event": "decision_probe_finished", "provider": value.provider})
                return public_result(result, secrets)
            except Exception as exc:
                raise HTTPException(502, _connection_error_message(exc).replace("连接测试", "决策测试")) from None
        finally:
            try:
                if policy is not None:
                    policy.close()
            finally:
                app.state.connection_test_slots.release()

    @app.post("/api/presets/validate")
    def validate_preset_input(value: dict):
        from .presets import validate_preset
        try:
            _bounded_json(value, 16384)
            with app.state.lock:
                _reject_saved_keys(value, configured_secrets())
            return public_result(validate_preset(value))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    def run_connection_test(provider):
        with app.state.lock:
            connection = connection_snapshot(provider)
            secrets = configured_secrets()
            test_id = app.state.connection_test_ids.get(provider, 0) + 1
            app.state.connection_test_ids[provider] = test_id
        started = time.perf_counter()
        logger.info("connection_test_started provider=%s", provider,
                    extra={"event": "connection_test_started", "provider": provider})
        message = None
        policy = None
        try:
            if not connection["url"] or not connection["model"] or (provider in {"jev", "claude"} and not connection["key"]):
                message = "请先填写并保存 API 地址、模型和所需的 API Key，再测试连接。"
                raise ValueError("Missing connection configuration")
            policy = DecisionPolicy(provider, connection)
            result = policy.choose({"purpose": "Connection test; no robot command will execute"},
                                   "Choose ready for a connection test", {"ready": "Ready", "hold": "Hold"}, "ready", [])
            model = _without_keys(_public_model(policy.model, connection), secrets)
            latency = result["latency_ms"]
            if type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0:
                raise ValueError("Invalid connection test latency")
            verification = {"status": "passed", "checked_at": datetime.now(timezone.utc).isoformat(),
                            "model": model, "latency_ms": round(latency),
                            "message": "连接测试通过，模型已返回有效候选动作；机器人未执行动作。"}
        except Exception as exc:
            message = message or _connection_error_message(exc)
            verification = {"status": "failed", "checked_at": datetime.now(timezone.utc).isoformat(),
                            "model": _without_keys(_public_model(connection["model"], connection, required=False), secrets),
                            "latency_ms": round((time.perf_counter() - started) * 1000), "message": message}
        finally:
            if policy is not None:
                policy.close()
        with app.state.lock:
            if app.state.connection_test_ids[provider] != test_id or connection_snapshot(provider) != connection:
                logger.info("connection_test_superseded provider=%s", provider,
                            extra={"event": "connection_test_superseded", "provider": provider})
                raise HTTPException(409, "配置或连接测试已更新，此次结果已丢弃，请查看当前状态后重试。")
            app.state.verifications[provider] = {"connection": connection, "result": verification}
        logger.info("connection_test_finished provider=%s status=%s latency_ms=%s",
                    provider, verification["status"], verification["latency_ms"],
                    extra={"event": "connection_test_finished", "provider": provider,
                           "verification_status": verification["status"], "latency_ms": verification["latency_ms"]})
        if verification["status"] == "failed":
            raise HTTPException(502, verification["message"]) from None
        return public_result({"ok": True, "model": verification["model"], "latency_ms": verification["latency_ms"],
                              "verification": verification}, secrets)

    @app.post("/api/connections/{provider}/test")
    def test_connection(provider: Literal["jev", "chat", "local", "claude"]):
        if not app.state.connection_test_slots.acquire(blocking=False):
            logger.info("connection_test_busy provider=%s", provider,
                        extra={"event": "connection_test_busy", "provider": provider})
            raise HTTPException(429, "已有两个连接测试正在进行，请等待其中一个完成后重试。", headers={"Retry-After": "1"})
        try:
            return run_connection_test(provider)
        finally:
            app.state.connection_test_slots.release()

    @app.get("/api/scene")
    def scene():
        session = current_session()
        with session.lock:
            result = session.world.scene()
        return public_result(result)

    @app.get("/api/state")
    def state():
        return public_result(current_session().snapshot())

    @app.post("/api/reset")
    def reset(setup: Setup):
        with app.state.lock:
            previous = app.state.session
            check_episode(setup.expected_episode_id, previous)
            _reject_saved_keys({"scene_config": setup.scene_config, "user_context": setup.user_context}, configured_secrets())
            try:
                selected = resolve_model(setup.provider, app.state.connections, profile_id=setup.profile_id,
                                         profiles=app.state.model_profiles)
                new = Session(**setup.model_dump(exclude={"expected_episode_id", "profile_id"}), connection=selected["connection"])
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            previous.stop()
            app.state.session = new
        logger.info("episode_reset episode_id=%s previous_episode_id=%s provider=%s task=%s",
                    new.id, previous.id, setup.provider, setup.task,
                    extra={"event": "episode_reset", "episode_id": new.id, "previous_episode_id": previous.id,
                           "provider": setup.provider, "task": setup.task})
        return public_result(new.snapshot())

    @app.post("/api/control/{action}")
    def control(action: Literal["start", "step", "pause", "stop"], options: Control = Control()):
        try:
            with app.state.lock:
                session = app.state.session
                check_episode(options.episode_id, session)
                if action in {"start", "step"}:
                    session.start(single_step=action == "step", threshold=options.threshold)
                else:
                    getattr(session, action)()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        logger.info("episode_control episode_id=%s action=%s", session.id, action,
                    extra={"event": "episode_control", "episode_id": session.id, "action": action})
        return public_result(session.snapshot())

    @app.get("/api/replay/{index}")
    def replay(index: int):
        session = current_session()
        try:
            result = session.replay_frame(index)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return public_result(result)

    @app.get("/api/export")
    def export():
        session = current_session()
        return JSONResponse(public_result(session.export()), headers={"Content-Disposition": f'attachment; filename="embodied-jev-{session.id}.json"'})

    @app.get("/api/comparison")
    def comparison_state():
        with app.state.comparison_lock:
            comparison = app.state.comparison
        return public_result(comparison.snapshot()) if comparison is not None else {"id": None, "status": "empty", "lanes": []}

    @app.post("/api/comparison")
    def create_comparison(setup: ComparisonSetup):
        with app.state.lock:
            saved_connections = {provider: dict(value) for provider, value in app.state.connections.items()}
            profiles = {profile_id: dict(value) for profile_id, value in app.state.model_profiles.items()}
            secrets = configured_secrets()
            _reject_saved_keys({"scene_config": setup.scene_config, "user_context": setup.user_context,
                                "models": [lane.model for lane in setup.lanes]}, secrets)
        try:
            lanes = resolve_lanes([lane.model_dump() for lane in setup.lanes], saved_connections, profiles)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        with app.state.comparison_lock:
            previous = app.state.comparison
            expected = setup.expected_comparison_id
            if (previous is not None and expected != previous.id) or (previous is None and expected is not None):
                raise HTTPException(409, "比较已更新，请刷新比较状态后再创建。")
            if previous is not None:
                previous.stop()
                if previous.has_live_workers():
                    raise HTTPException(409, "比较已停止，仍在等待上一轮请求结束，请稍后重建。")
            try:
                comparison = Comparison(lanes, **setup.model_dump(exclude={"lanes", "expected_comparison_id"}), secrets=secrets)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            app.state.comparison = comparison
        return public_result(comparison.snapshot())

    @app.post("/api/comparison/control/{action}")
    def comparison_control(action: Literal["start", "pause", "stop"], options: ComparisonControl):
        with app.state.comparison_lock:
            comparison = app.state.comparison
            if comparison is None:
                raise HTTPException(404, "尚未创建模型比较。")
            if comparison.id != options.comparison_id:
                raise HTTPException(409, "比较已更新，请刷新比较状态后再操作。")
            try:
                getattr(comparison, action)()
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
        return public_result(comparison.snapshot())

    @app.get("/api/comparison/scene/{lane_id}")
    def comparison_scene(lane_id: str, comparison_id: str = Query(min_length=1, max_length=64)):
        comparison = current_comparison(comparison_id)
        try:
            result = comparison.scene(lane_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        return public_result(result)

    @app.get("/api/comparison/replay")
    def comparison_replay(time: float = Query(ge=0, allow_inf_nan=False), comparison_id: str = Query(min_length=1, max_length=64)):
        comparison = current_comparison(comparison_id)
        try:
            result = comparison.replay(time)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return public_result(result)

    @app.get("/api/comparison/export")
    def comparison_export(comparison_id: str = Query(min_length=1, max_length=64)):
        comparison = current_comparison(comparison_id)
        return JSONResponse(public_result(comparison.export()), headers={
            "Content-Disposition": f'attachment; filename="embodied-jev-comparison-{comparison.id}.json"'})

    web = Path(__file__).parent / "web"
    if web.exists():
        app.mount("/", StaticFiles(directory=web, html=True), name="workbench")
    return app
