"""The web dashboard: one page with the Start / Stop button, served by FastAPI."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .config import Settings
from .engine import Engine

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("pumpscan.server")


async def keep_awake(url: str, every: float = 600) -> None:
    """Request our own public URL so free hosts never see the service as idle."""
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            await asyncio.sleep(every)
            try:
                await client.get(url)
            except httpx.HTTPError as exc:
                log.warning("keep-alive ping to %s failed: %s", url, exc)


class RulesIn(BaseModel):
    min_buy_votes: int | None = Field(None, ge=1, le=25)
    window_minutes: float | None = Field(None, ge=1, le=120)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = Engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.autostart:
            await engine.start()
        pinger = asyncio.create_task(keep_awake(settings.keepalive_url)) if settings.keepalive_url else None
        yield
        if pinger:
            pinger.cancel()
        await engine.stop()

    app = FastAPI(title="pumpscan", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine

    if settings.password:
        expected = settings.password.encode()

        @app.middleware("http")
        async def require_password(request: Request, call_next):
            """HTTP Basic auth: the browser asks once; any username, the configured password."""
            if request.url.path == "/healthz":
                return await call_next(request)
            header = request.headers.get("authorization", "")
            ok = False
            if header.lower().startswith("basic "):
                try:
                    _, _, given = base64.b64decode(header[6:]).partition(b":")
                    ok = secrets.compare_digest(given, expected)
                except (binascii.Error, ValueError):
                    ok = False
            if not ok:
                return PlainTextResponse("password required", status_code=401,
                                         headers={"WWW-Authenticate": 'Basic realm="pumpscan"'})
            return await call_next(request)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state")
    async def state():
        return engine.state()

    @app.post("/api/start")
    async def start():
        await engine.start()
        return {"running": engine.running}

    @app.post("/api/stop")
    async def stop():
        await engine.stop()
        return {"running": engine.running}

    @app.post("/api/rules")
    async def rules(body: RulesIn):
        if body.min_buy_votes is None and body.window_minutes is None:
            raise HTTPException(400, "nothing to change")
        if body.min_buy_votes is not None:
            engine.rules.min_buy_votes = body.min_buy_votes
        if body.window_minutes is not None:
            engine.rules.window_minutes = body.window_minutes
        return {"rules": engine.state()["rules"]}

    return app
