from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field

from carlos.config import Settings
from carlos.db import Database, now_iso
from carlos.llm import Claude
from carlos.messaging import Dispatcher

log = logging.getLogger(__name__)


@dataclass
class Context:
    db: Database
    settings: Settings
    claude: Claude
    dispatcher: Dispatcher
    meta: object | None = None
    threads: object | None = None
    notes: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.notes.append(text)


class Agent:
    name = "agent"
    title = "Agent"
    description = ""

    def run(self, ctx: Context) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def execute(self, ctx: Context, trigger: str = "schedule") -> dict:
        run_id = ctx.db.insert("agent_runs", {"agent": self.name, "trigger": trigger, "started_at": now_iso()})
        status, summary = "ok", ""
        try:
            summary = self.run(ctx) or "nothing to do"
        except Exception as e:  # one agent failing never stops the others
            status, summary = "error", f"{type(e).__name__}: {e}"
            log.error("agent %s failed: %s", self.name, traceback.format_exc())
        ctx.db.run("UPDATE agent_runs SET status = ?, summary = ?, finished_at = ? WHERE id = ?",
                   (status, summary[:1000], now_iso(), run_id))
        return {"agent": self.name, "status": status, "summary": summary}
