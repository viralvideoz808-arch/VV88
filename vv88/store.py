"""SQLite state: campaigns, sources, clips, scheduled/published posts and view metrics."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from vv88.models import Campaign, RenderedClip, SourceVideo

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    score REAL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    url TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    start REAL NOT NULL,
    "end" REAL NOT NULL,
    style TEXT NOT NULL,
    length_bucket TEXT NOT NULL,
    hook_type TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    status TEXT NOT NULL,              -- scheduled | posted | failed | skipped
    scheduled_at REAL NOT NULL,
    posted_at REAL,
    remote_id TEXT,
    url TEXT,
    content TEXT,
    error TEXT,
    UNIQUE(clip_id, platform)
);
CREATE TABLE IF NOT EXISTS metrics (
    post_id INTEGER NOT NULL,
    fetched_at REAL NOT NULL,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_metrics_post ON metrics(post_id, fetched_at);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        # The DB holds OAuth tokens: keep the folder and file private to the owner.
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        if os.name == "posix":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---- key/value cache -------------------------------------------------
    def kv_get(self, key: str, max_age_s: float | None = None) -> Any | None:
        row = self.db.execute("SELECT value, updated_at FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        if max_age_s is not None and time.time() - row["updated_at"] > max_age_s:
            return None
        return json.loads(row["value"])

    def kv_set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO kv(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), time.time()),
        )
        self.db.commit()

    # ---- campaigns -------------------------------------------------------
    def upsert_campaigns(self, campaigns: Iterable[Campaign]) -> int:
        n = 0
        now = time.time()
        for c in campaigns:
            self.db.execute(
                "INSERT INTO campaigns(id, data, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (c.id, json.dumps(c.to_dict()), now),
            )
            n += 1
        self.db.commit()
        return n

    def campaigns(self, active_only: bool = True) -> list[Campaign]:
        rows = self.db.execute("SELECT data FROM campaigns").fetchall()
        out = [Campaign.from_dict(json.loads(r["data"])) for r in rows]
        if active_only:
            out = [c for c in out if c.status == "active"]
        return out

    def campaign(self, campaign_id: str) -> Campaign | None:
        row = self.db.execute("SELECT data FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        return Campaign.from_dict(json.loads(row["data"])) if row else None

    def set_campaign_score(self, campaign_id: str, score: float) -> None:
        self.db.execute("UPDATE campaigns SET score=? WHERE id=?", (score, campaign_id))
        self.db.commit()

    # ---- sources ---------------------------------------------------------
    def add_source(self, src: SourceVideo) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO sources(id, campaign_id, url, data, created_at) VALUES(?,?,?,?,?)",
            (src.id, src.campaign_id, src.url, json.dumps(asdict(src)), time.time()),
        )
        self.db.commit()

    def source_by_url(self, url: str) -> SourceVideo | None:
        row = self.db.execute("SELECT data FROM sources WHERE url=?", (url,)).fetchone()
        return SourceVideo(**json.loads(row["data"])) if row else None

    def source(self, source_id: str) -> SourceVideo | None:
        row = self.db.execute("SELECT data FROM sources WHERE id=?", (source_id,)).fetchone()
        return SourceVideo(**json.loads(row["data"])) if row else None

    # ---- clips -----------------------------------------------------------
    def add_clip(self, clip: RenderedClip) -> None:
        self.db.execute(
            'INSERT OR REPLACE INTO clips(id, source_id, campaign_id, start, "end", style, length_bucket, '
            "hook_type, data, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                clip.id,
                clip.source_id,
                clip.campaign_id,
                clip.start,
                clip.end,
                clip.style,
                clip.length_bucket,
                clip.hook_type,
                json.dumps(asdict(clip)),
                time.time(),
            ),
        )
        self.db.commit()

    def clip(self, clip_id: str) -> RenderedClip | None:
        row = self.db.execute("SELECT data FROM clips WHERE id=?", (clip_id,)).fetchone()
        return RenderedClip(**json.loads(row["data"])) if row else None

    def clip_ranges(self, source_id: str) -> list[tuple[float, float]]:
        rows = self.db.execute('SELECT start, "end" FROM clips WHERE source_id=?', (source_id,)).fetchall()
        return [(r["start"], r["end"]) for r in rows]

    # ---- posts -----------------------------------------------------------
    def schedule_post(self, clip_id: str, platform: str, when: float, content: dict) -> int | None:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO posts(clip_id, platform, status, scheduled_at, content) VALUES(?,?,?,?,?)",
            (clip_id, platform, "scheduled", when, json.dumps(content)),
        )
        self.db.commit()
        return cur.lastrowid if cur.rowcount else None

    def due_posts(self, now: float | None = None) -> list[sqlite3.Row]:
        now = time.time() if now is None else now
        return self.db.execute(
            "SELECT * FROM posts WHERE status='scheduled' AND scheduled_at<=? ORDER BY scheduled_at",
            (now,),
        ).fetchall()

    def mark_post(self, post_id: int, status: str, remote_id: str = "", url: str = "", error: str = "") -> None:
        self.db.execute(
            "UPDATE posts SET status=?, remote_id=?, url=?, error=?, posted_at=? WHERE id=?",
            (status, remote_id, url, error, time.time() if status == "posted" else None, post_id),
        )
        self.db.commit()

    def reschedule(self, post_id: int, when: float) -> None:
        self.db.execute("UPDATE posts SET scheduled_at=? WHERE id=?", (when, post_id))
        self.db.commit()

    def post_times(self, platform: str, since: float) -> list[float]:
        """Scheduled or posted times for a platform (used for caps and spacing)."""
        rows = self.db.execute(
            "SELECT COALESCE(posted_at, scheduled_at) AS t FROM posts "
            "WHERE platform=? AND status IN ('scheduled','posted') AND COALESCE(posted_at, scheduled_at)>=?",
            (platform, since),
        ).fetchall()
        return sorted(r["t"] for r in rows)

    def posts_for_metrics(self, min_age_s: float, max_age_s: float) -> list[sqlite3.Row]:
        now = time.time()
        return self.db.execute(
            "SELECT * FROM posts WHERE status='posted' AND remote_id != '' AND posted_at BETWEEN ? AND ?",
            (now - max_age_s, now - min_age_s),
        ).fetchall()

    def add_metric(self, post_id: int, views: int, likes: int = 0, comments: int = 0, shares: int = 0) -> None:
        self.db.execute(
            "INSERT INTO metrics(post_id, fetched_at, views, likes, comments, shares) VALUES(?,?,?,?,?,?)",
            (post_id, time.time(), views, likes, comments, shares),
        )
        self.db.commit()

    def performance_rows(self) -> list[sqlite3.Row]:
        """One row per published post with its latest view count and clip/campaign attributes."""
        return self.db.execute(
            """
            SELECT p.id AS post_id, p.platform, p.posted_at, c.style, c.length_bucket, c.hook_type,
                   c.campaign_id, json_extract(cp.data, '$.category') AS category,
                   json_extract(cp.data, '$.reward_per_1k_usd') AS reward_per_1k,
                   (SELECT views FROM metrics m WHERE m.post_id = p.id
                     ORDER BY fetched_at DESC LIMIT 1) AS views
            FROM posts p
            JOIN clips c ON c.id = p.clip_id
            LEFT JOIN campaigns cp ON cp.id = c.campaign_id
            WHERE p.status = 'posted'
            """
        ).fetchall()
