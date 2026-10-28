"""The agent team and the scheduler that runs it."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from carlos.agents.base import Agent, Context
from carlos.agents.concierge import Concierge
from carlos.agents.fleet import Fleet
from carlos.agents.followup import FollowUp
from carlos.agents.outreach import Outreach
from carlos.agents.qualifier import Qualifier
from carlos.agents.scout import Scout
from carlos.config import Settings
from carlos.db import Database, parse_iso
from carlos.llm import Claude
from carlos.messaging import Dispatcher

log = logging.getLogger(__name__)

AGENTS: dict[str, Agent] = {a.name: a for a in (Scout(), Qualifier(), Outreach(), Concierge(), FollowUp(), Fleet())}
PIPELINE = ("scout", "qualifier", "outreach", "concierge", "followup", "fleet")


def make_context(db: Database, settings: Settings, claude: Claude | None = None, dispatcher: Dispatcher | None = None,
                 meta=None, threads=None) -> Context:
    return Context(db=db, settings=settings, claude=claude or Claude(settings),
                   dispatcher=dispatcher or Dispatcher(db, settings, meta=meta, threads=threads),
                   meta=meta, threads=threads)


def run_agent(db: Database, settings: Settings, name: str, trigger: str = "manual", **kw) -> dict:
    return AGENTS[name].execute(make_context(db, settings, **kw), trigger=trigger)


def run_pipeline(db: Database, settings: Settings, trigger: str = "manual", **kw) -> list[dict]:
    return [run_agent(db, settings, name, trigger, **kw) for name in PIPELINE]


def due_agents(db: Database, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    if db.get_setting("agents_paused"):
        return []
    due = []
    for name in PIPELINE:
        if not db.get_setting(f"agent.{name}.enabled"):
            continue
        last = db.one("SELECT started_at FROM agent_runs WHERE agent = ? ORDER BY id DESC LIMIT 1", (name,))
        interval = timedelta(minutes=max(1, int(db.get_setting(f"agent.{name}.interval") or 30)))
        started = parse_iso(last["started_at"]) if last else None
        if not started or now - started >= interval:
            due.append(name)
    return due


class Scheduler:
    """Background loop inside the console process. Agents run one at a time in a worker thread."""

    def __init__(self, db: Database, settings: Settings, tick_seconds: float = 20):
        self.db, self.settings, self.tick = db, settings, tick_seconds
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    async def loop(self) -> None:
        # Mark runs left 'running' by a previous crash.
        self.db.run("UPDATE agent_runs SET status = 'error', summary = 'interrupted (server restarted)' "
                    "WHERE status = 'running'")
        while True:
            try:
                for name in due_agents(self.db):
                    await self.run(name, "schedule")
            except Exception:  # keep the loop alive whatever happens
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.tick)

    async def run(self, name: str, trigger: str = "manual") -> dict:
        async with self.lock:
            return await asyncio.to_thread(run_agent, self.db, self.settings, name, trigger)

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.get_running_loop().create_task(self.loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
