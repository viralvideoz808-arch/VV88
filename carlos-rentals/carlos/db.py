"""SQLite storage. One short-lived connection per operation keeps it safe across web threads and agent threads."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  make TEXT NOT NULL, model TEXT NOT NULL, year INTEGER NOT NULL, trim TEXT DEFAULT '',
  body TEXT NOT NULL DEFAULT 'sedan',
  listing TEXT NOT NULL CHECK (listing IN ('rent', 'sale')),
  daily_rate REAL, weekly_rate REAL, deposit REAL, sale_price REAL,
  mileage INTEGER, transmission TEXT DEFAULT 'Automatic', fuel TEXT DEFAULT 'Gas',
  seats INTEGER DEFAULT 5, doors INTEGER DEFAULT 4, mpg TEXT DEFAULT '',
  color TEXT DEFAULT '', color_hex TEXT DEFAULT '#9aa3ad',
  features TEXT NOT NULL DEFAULT '[]', description TEXT DEFAULT '', photos TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'available', featured INTEGER NOT NULL DEFAULT 0, sample INTEGER NOT NULL DEFAULT 0,
  vin TEXT DEFAULT '', location TEXT DEFAULT '', views INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
  id INTEGER PRIMARY KEY,
  ref TEXT UNIQUE NOT NULL,
  vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
  name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT DEFAULT '',
  start_date TEXT NOT NULL, end_date TEXT NOT NULL,
  pickup TEXT DEFAULT '', driver_age INTEGER, extras TEXT NOT NULL DEFAULT '[]',
  days INTEGER NOT NULL, subtotal REAL NOT NULL, extras_total REAL NOT NULL DEFAULT 0,
  fees REAL NOT NULL DEFAULT 0, tax REAL NOT NULL DEFAULT 0, total REAL NOT NULL, deposit REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending', source TEXT DEFAULT 'website', lead_id INTEGER, notes TEXT DEFAULT '',
  reminder_sent INTEGER NOT NULL DEFAULT 0, thanks_sent INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bookings_vehicle ON bookings(vehicle_id, status);
CREATE TABLE IF NOT EXISTS inquiries (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL, vehicle_id INTEGER REFERENCES vehicles(id),
  name TEXT NOT NULL, email TEXT DEFAULT '', phone TEXT DEFAULT '', message TEXT DEFAULT '',
  details TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'new', source TEXT DEFAULT 'website',
  lead_id INTEGER, followed_up INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL, source_id TEXT NOT NULL,
  handle TEXT DEFAULT '', name TEXT DEFAULT '', profile_url TEXT DEFAULT '', post_url TEXT DEFAULT '',
  text TEXT DEFAULT '', posted_at TEXT DEFAULT '', found_by TEXT DEFAULT '',
  reply_mode TEXT NOT NULL DEFAULT 'manual',
  intent TEXT NOT NULL DEFAULT 'unknown', score INTEGER NOT NULL DEFAULT 0, needs TEXT NOT NULL DEFAULT '{}',
  reason TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'new',
  conversation_id INTEGER, ref_code TEXT UNIQUE,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_contact_at TEXT,
  UNIQUE (platform, source_id)
);
CREATE INDEX IF NOT EXISTS leads_status ON leads(status, score);
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY,
  channel TEXT NOT NULL, external_id TEXT NOT NULL,
  lead_id INTEGER, contact_name TEXT DEFAULT '', contact_handle TEXT DEFAULT '',
  email TEXT DEFAULT '', phone TEXT DEFAULT '',
  mode TEXT NOT NULL DEFAULT 'auto', status TEXT NOT NULL DEFAULT 'open',
  needs_reply INTEGER NOT NULL DEFAULT 0, last_inbound_at TEXT, last_outbound_at TEXT,
  summary TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (channel, external_id)
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id),
  direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
  author TEXT NOT NULL DEFAULT 'customer', body TEXT NOT NULL, external_id TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL, channel TEXT NOT NULL, target TEXT NOT NULL DEFAULT '',
  lead_id INTEGER, conversation_id INTEGER, body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending_approval', agent TEXT DEFAULT '', reason TEXT DEFAULT '',
  error TEXT DEFAULT '', external_id TEXT DEFAULT '',
  created_at TEXT NOT NULL, decided_at TEXT, sent_at TEXT
);
CREATE INDEX IF NOT EXISTS outbox_status ON outbox(status, channel);
CREATE TABLE IF NOT EXISTS optouts (
  platform TEXT NOT NULL, handle TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (platform, handle)
);
CREATE TABLE IF NOT EXISTS agent_runs (
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL, trigger TEXT NOT NULL DEFAULT 'schedule', status TEXT NOT NULL DEFAULT 'running',
  summary TEXT DEFAULT '', started_at TEXT NOT NULL, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS agent_runs_agent ON agent_runs(agent, id);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pageviews (day TEXT NOT NULL, path TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, path));
"""

AGENT_NAMES = ("scout", "qualifier", "outreach", "concierge", "followup", "fleet")

DEFAULTS: dict[str, Any] = {
    "agents_paused": False,
    # minutes between scheduled runs
    "agent.scout.interval": 30,
    "agent.qualifier.interval": 5,
    "agent.outreach.interval": 10,
    "agent.concierge.interval": 1,
    "agent.followup.interval": 60,
    "agent.fleet.interval": 60,
    **{f"agent.{name}.enabled": True for name in AGENT_NAMES},
    # Lead discovery
    "scout.keywords": [
        "need a rental car", "looking to rent a car", "car rental recommendations", "rent a car this weekend",
        "need a car for the week", "looking to buy a used car", "in the market for a car", "selling my car",
        "trade in my car", "need an suv rental", "airport car rental",
    ],
    "scout.hashtags": ["carrental", "rentacar", "usedcarsforsale", "carforsale"],
    "scout.locations": [],
    "scout.max_age_days": 7,
    # Outreach rules
    "outreach.require_approval": True,
    "outreach.min_score": 55,
    "outreach.daily_caps": {"threads_reply": 15, "ig_private_reply": 25, "fb_private_reply": 25,
                            "ig_comment_reply": 40, "fb_comment_reply": 40},
    "outreach.quiet_hours": [21, 8],
    # Conversations
    "concierge.auto_reply": True,
    "concierge.require_approval": False,
    "concierge.handoff_keywords": ["accident", "lawyer", "police", "refund", "chargeback", "complaint", "manager",
                                   "damage", "stolen", "insurance claim"],
    # Website content
    "site.hours": "Mon–Sat 8am–7pm · Sun 10am–4pm",
    "site.tagline": "Drive something you'll talk about.",
    "site.announcement": "",
    # Rental rules
    "rental.tax_rate": 0.07,
    "rental.min_age": 21,
    "rental.young_driver_age": 25,
    "rental.young_driver_fee": 25.0,
    "rental.extras": {
        "insurance": {"label": "Collision damage waiver", "per_day": 19.0},
        "gps": {"label": "GPS navigation", "per_day": 7.0},
        "child_seat": {"label": "Child safety seat", "per_day": 9.0},
        "extra_driver": {"label": "Additional driver", "per_day": 12.0},
        "delivery": {"label": "Delivery & pickup to your address", "flat": 45.0},
    },
    "rental.max_days": 60,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.init()

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA busy_timeout = 30000")
        try:
            yield c
        finally:
            c.close()

    def init(self) -> None:
        with self.conn() as c:
            c.execute("PRAGMA journal_mode = WAL")
            c.executescript(SCHEMA)

    # -- query helpers -------------------------------------------------------------------------
    def all(self, sql: str, args: tuple | list = ()) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple | list = ()) -> dict | None:
        with self.conn() as c:
            row = c.execute(sql, args).fetchone()
            return dict(row) if row else None

    def scalar(self, sql: str, args: tuple | list = ()) -> Any:
        with self.conn() as c:
            row = c.execute(sql, args).fetchone()
            return row[0] if row else None

    def run(self, sql: str, args: tuple | list = ()) -> int:
        """Execute a write; returns lastrowid for inserts, rowcount otherwise."""
        with self._lock, self.conn() as c:
            cur = c.execute(sql, args)
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    def insert(self, table: str, row: dict, ignore: bool = False) -> int:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        verb = "INSERT OR IGNORE" if ignore else "INSERT"
        with self._lock, self.conn() as c:
            cur = c.execute(f"{verb} INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
            return cur.lastrowid if cur.rowcount else 0

    def update(self, table: str, row_id: int, fields: dict) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.run(f"UPDATE {table} SET {sets} WHERE id = ?", [*fields.values(), row_id])

    # -- settings ------------------------------------------------------------------------------
    def get_setting(self, key: str) -> Any:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return DEFAULTS.get(key)
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return DEFAULTS.get(key)

    def set_setting(self, key: str, value: Any) -> None:
        self.run("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, json.dumps(value)))

    def all_settings(self) -> dict[str, Any]:
        out = dict(DEFAULTS)
        for row in self.all("SELECT key, value FROM settings"):
            try:
                out[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                pass
        return out

    # -- audit trail ---------------------------------------------------------------------------
    def log(self, actor: str, kind: str, detail: str = "") -> None:
        self.run("INSERT INTO events (at, actor, kind, detail) VALUES (?, ?, ?, ?)", (now_iso(), actor, kind, detail[:2000]))

    def count_view(self, path: str) -> None:
        day = now_iso()[:10]
        self.run("INSERT INTO pageviews (day, path, count) VALUES (?, ?, 1) "
                 "ON CONFLICT(day, path) DO UPDATE SET count = count + 1", (day, path[:200]))


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
