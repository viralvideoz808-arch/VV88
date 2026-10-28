"""The web dashboard: one page with the Start / Stop button, served by FastAPI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import Settings
from .engine import Engine

STATIC = Path(__file__).parent / "static"


class RulesIn(BaseModel):
    min_buy_votes: int | None = Field(None, ge=1, le=25)
    window_minutes: float | None = Field(None, ge=1, le=120)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = Engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await engine.stop()

    app = FastAPI(title="pumpscan", lifespan=lifespan)
    app.state.engine = engine

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
