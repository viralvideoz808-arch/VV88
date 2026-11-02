"""Pick posting times: best slots first, per-platform daily caps, minimum spacing, jitter.

Posting faster than an account can absorb splits its reach and trips spam heuristics, so
spacing and caps are enforced here rather than left to the platform to punish.
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timedelta, timezone

from vv88.config import PlatformConfig, PublishingConfig


def _slot_times(slots_utc: list[str], start: datetime, days: int) -> list[datetime]:
    out = []
    for d in range(days + 1):
        day = (start + timedelta(days=d)).date()
        for s in slots_utc:
            hh, mm = (int(x) for x in s.split(":"))
            out.append(datetime(day.year, day.month, day.day, hh, mm, tzinfo=timezone.utc))
    return sorted(out)


def next_slot(
    existing: list[float],
    pcfg: PlatformConfig,
    slots_utc: list[str],
    now: float | None = None,
    jitter_minutes: int = 7,
    horizon_days: int = 7,
    rng: random.Random | None = None,
) -> float | None:
    """Earliest slot that respects the daily cap and minimum gap, or None if the week is full."""
    rng = rng or random.Random()
    now = time.time() if now is None else now
    start = datetime.fromtimestamp(now, tz=timezone.utc)
    taken = sorted(existing)
    gap = pcfg.min_gap_minutes * 60
    for slot in _slot_times(slots_utc, start, horizon_days):
        t = slot.timestamp() + rng.uniform(-jitter_minutes, jitter_minutes) * 60
        if t < now + 120:
            continue
        day_start = slot.replace(hour=0, minute=0).timestamp()
        same_day = sum(1 for x in taken if day_start <= x < day_start + 86400)
        if same_day >= pcfg.daily_cap:
            continue
        if any(abs(t - x) < gap for x in taken):
            continue
        return t
    return None


def approve_post(store, pub: PublishingConfig, post_id: int, caption: str | None = None,
                 title: str | None = None) -> bool:
    """Approve a pending post, optionally with edited copy; False if it isn't pending any more.

    The post keeps its slot if that is still ahead. A slot that passed while it waited moves to the next
    free one (daily cap and spacing apply), so approving a backlog never fires it all at once.
    """
    row = store.post(post_id)
    if row is None or row["status"] != "pending":
        return False
    try:
        content = json.loads(row["content"] or "{}") or {}
    except ValueError:
        content = {}
    if caption is not None:
        content["caption"] = caption
    if title is not None:
        content["title"] = title
    content["approved"] = True
    when = float(row["scheduled_at"])
    now = time.time()
    pcfg = pub.platforms.get(row["platform"])
    if when < now + 120 and pcfg is not None:
        existing = store.post_times(row["platform"], since=now - 86400, exclude_id=post_id)
        when = next_slot(existing, pcfg, pub.slots_utc, jitter_minutes=pub.jitter_minutes) or now
    return store.approve_post(post_id, when, content)
