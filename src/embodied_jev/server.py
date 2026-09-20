from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import threading
from urllib.parse import urlsplit
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .physics import TASKS
from .policies import DecisionPolicy, configurations, environment_connection
from .runtime import Session


class Setup(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    task: Literal["transfer", "stack", "barrier"] = "transfer"
    seed: int = Field(default=0, ge=0, le=99999)
    provider: Literal["baseline", "jev", "minicpm", "local", "chat", "claude"] = "baseline"
    preview: bool = True
    threshold: float = Field(default=.55, ge=0, le=1)
    max_cycles: int = Field(default=30, ge=1, le=100)
    speed: float = Field(default=1.5, ge=.25, le=4)


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    threshold: float | None = Field(default=None, ge=0, le=1)


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["jev", "chat", "local", "claude"]
    url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    api_key: SecretStr = SecretStr("")
    json_mode: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Use an HTTP(S) URL without credentials, query or fragment")
        return value.rstrip("/")


def create_app():
    @asynccontextmanager
    async def lifespan(app):
        yield
        app.state.session.stop()

    app = FastAPI(title="EmbodiedJev", lifespan=lifespan)
    app.state.session = Session()
    app.state.lock = threading.Lock()
    app.state.connections = {}

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
        for provider in ("jev", "chat", "local", "claude"):
            item = app.state.connections.get(provider, environment_connection(provider))
            result[provider] = {"url": item["url"], "model": item["model"],
                                "key_configured": bool(item.get("key")), "json_mode": item.get("json_mode", True)}
        return result

    @app.post("/api/connections")
    def save_connection(value: ConnectionInput):
        url = value.url
        if value.provider == "jev" and url != "https://api.typesafe.ai/v1/systemone":
            raise HTTPException(422, "TypeSafe Jev uses its official endpoint")
        if value.provider == "chat" and not url.endswith("/chat/completions"):
            url += "/chat/completions"
        if value.provider == "claude" and not url.endswith("/messages"):
            url += "/messages"
        with app.state.lock:
            previous = app.state.connections.get(value.provider, environment_connection(value.provider))
            key = value.api_key.get_secret_value().strip()
            if not key and previous["url"] == url:
                key = previous.get("key", "")
            if value.provider == "jev" and not key:
                raise HTTPException(422, "TypeSafe API key is required")
            if value.provider == "claude" and not key:
                raise HTTPException(422, "Claude API key is required")
            app.state.connections[value.provider] = {"url": url, "model": value.model.strip(), "key": key, "json_mode": value.json_mode}
        return {"saved": True, "provider": value.provider, "key_configured": bool(key)}

    @app.post("/api/connections/{provider}/test")
    def test_connection(provider: Literal["jev", "chat", "local", "claude"]):
        try:
            policy = DecisionPolicy(provider, app.state.connections.get(provider))
            result = policy.choose({"purpose": "Connection test; no robot command will execute"},
                                   "Choose ready for a connection test", {"ready": "Ready", "hold": "Hold"}, "ready", [])
            return {"ok": True, "model": policy.model, "latency_ms": round(result["latency_ms"])}
        except Exception as exc:
            # Provider exceptions can contain request details; never return them with credentials.
            import httpx
            message = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
            raise HTTPException(502, f"Connection test failed: {message}") from None

    @app.get("/api/scene")
    def scene():
        return app.state.session.world.scene()

    @app.get("/api/state")
    def state():
        return app.state.session.snapshot()

    @app.post("/api/reset")
    def reset(setup: Setup):
        try:
            new = Session(**setup.model_dump(), connection=app.state.connections.get(setup.provider))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with app.state.lock:
            app.state.session.stop()
            app.state.session = new
        return new.snapshot()

    @app.post("/api/control/{action}")
    def control(action: Literal["start", "step", "pause", "stop"], options: Control = Control()):
        try:
            with app.state.lock:
                session = app.state.session
                if action in {"start", "step"}:
                    session.start(single_step=action == "step", threshold=options.threshold)
                else:
                    getattr(session, action)()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return session.snapshot()

    @app.get("/api/replay/{index}")
    def replay(index: int):
        try:
            return app.state.session.replay_frame(index)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/export")
    def export():
        session = app.state.session
        return JSONResponse(session.export(), headers={"Content-Disposition": f'attachment; filename="embodied-jev-{session.id}.json"'})

    web = Path(__file__).parent / "web"
    if web.exists():
        app.mount("/", StaticFiles(directory=web, html=True), name="workbench")
    return app
