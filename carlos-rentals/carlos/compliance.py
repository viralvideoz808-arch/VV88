"""The rules every outgoing message passes through.

They keep the business inside Meta's platform terms and US messaging norms:
- no cold DMs: the agents only message people who wrote to the business first, or reply publicly to a public post;
- Messenger/Instagram replies only inside the 24-hour window after the customer's last message;
- one private reply per comment, within 7 days of the comment;
- opt-outs are permanent and checked before every send;
- daily caps and quiet hours for anything the business starts;
- the assistant says it is a virtual assistant (California's B.O.T. Act and similar rules).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from carlos.db import Database, now_iso, parse_iso

# channel -> how the business may reach the person
CHANNELS = {
    "threads_reply": "Public reply to a public Threads post",
    "ig_comment_reply": "Public reply to a comment on our Instagram post",
    "fb_comment_reply": "Public reply to a comment on our Facebook post",
    "ig_private_reply": "One private DM in answer to a comment on our Instagram post",
    "fb_private_reply": "One private Messenger reply to a comment on our Facebook post",
    "instagram_dm": "Instagram DM conversation the customer started",
    "messenger": "Messenger conversation the customer started",
    "webchat": "Chat on the CAR-LOS website",
    "email": "Email to someone who gave us their address",
    "threads_post": "Post on our own Threads account",
    "fb_post": "Post on our own Facebook Page",
}
INITIATED_BY_BUSINESS = {"threads_reply", "ig_private_reply", "fb_private_reply", "ig_comment_reply", "fb_comment_reply"}
WINDOWED = {"instagram_dm": timedelta(hours=24), "messenger": timedelta(hours=24)}
PRIVATE_REPLY_WINDOW = timedelta(days=7)

OPT_OUT = re.compile(
    r"^\s*(stop|stop all|unsubscribe|quit|opt[\s-]?out)\s*[.!]*\s*$"
    r"|\b(stop (messaging|texting|contacting) me|leave me alone|don'?t (message|contact|text) me|"
    r"remove me|not interested,? (thanks|thank you)?\s*$|unsubscribe)\b",
    re.IGNORECASE,
)

AI_DISCLOSURE = "I'm the CAR-LOS virtual assistant — a real team member can jump in any time."


class Blocked(Exception):
    """A send that the rules don't allow. The message stays in the outbox with the reason."""


def is_opt_out(text: str) -> bool:
    return bool(OPT_OUT.search(text or ""))


def record_opt_out(db: Database, platform: str, handle: str) -> None:
    if handle:
        db.run("INSERT OR IGNORE INTO optouts (platform, handle, created_at) VALUES (?, ?, ?)",
               (platform, handle.lower(), now_iso()))
        db.log("compliance", "opt_out", f"{platform}:{handle}")


def opted_out(db: Database, platform: str, handle: str) -> bool:
    if not handle:
        return False
    return bool(db.one("SELECT 1 FROM optouts WHERE platform IN (?, '*') AND handle = ?", (platform, handle.lower())))


def platform_of(channel: str) -> str:
    if channel.startswith("ig_") or channel == "instagram_dm":
        return "instagram"
    if channel.startswith("fb_") or channel == "messenger":
        return "facebook"
    if channel.startswith("threads"):
        return "threads"
    return channel


def in_quiet_hours(db: Database, tz: str, at: datetime | None = None) -> bool:
    start, end = db.get_setting("outreach.quiet_hours") or [21, 8]
    try:
        local = (at or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz))
    except Exception:  # unknown zone name: fall back to UTC rather than blocking everything
        local = (at or datetime.now(timezone.utc))
    h = local.hour
    return (h >= start or h < end) if start > end else (start <= h < end)


def sent_today(db: Database, channel: str) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(microsecond=0).isoformat()
    return int(db.scalar("SELECT COUNT(*) FROM outbox WHERE channel = ? AND status = 'sent' AND sent_at >= ?",
                         (channel, since)) or 0)


def check_send(db: Database, item: dict, tz: str, now: datetime | None = None) -> None:
    """Raise Blocked if this outbox item may not be sent right now."""
    now = now or datetime.now(timezone.utc)
    channel = item["channel"]
    if channel not in CHANNELS:
        raise Blocked(f"unknown channel {channel}")
    lead = db.one("SELECT * FROM leads WHERE id = ?", (item["lead_id"],)) if item.get("lead_id") else None
    conv = db.one("SELECT * FROM conversations WHERE id = ?", (item["conversation_id"],)) \
        if item.get("conversation_id") else None

    handle = (lead or {}).get("handle") or (conv or {}).get("contact_handle") or ""
    if opted_out(db, platform_of(channel), handle):
        raise Blocked("this person opted out")
    if lead and lead["status"] in ("opted_out", "do_not_contact"):
        raise Blocked("lead is marked do-not-contact")

    if channel in INITIATED_BY_BUSINESS:
        caps = db.get_setting("outreach.daily_caps") or {}
        cap = int(caps.get(channel, 10))
        if sent_today(db, channel) >= cap:
            raise Blocked(f"daily cap reached for {channel} ({cap}/24h)")
        if in_quiet_hours(db, tz, now):
            raise Blocked("quiet hours")
        if channel in ("ig_private_reply", "fb_private_reply"):
            posted = parse_iso((lead or {}).get("posted_at"))
            if posted and now - posted > PRIVATE_REPLY_WINDOW:
                raise Blocked("private replies must go out within 7 days of the comment")
            done = db.scalar("SELECT COUNT(*) FROM outbox WHERE channel = ? AND target = ? AND status = 'sent'",
                             (channel, item["target"]))
            if done:
                raise Blocked("a comment can receive only one private reply")
        if lead:
            touched = db.scalar("SELECT COUNT(*) FROM outbox WHERE lead_id = ? AND kind = 'outreach' "
                                "AND status = 'sent'", (lead["id"],))
            if touched and item.get("kind") == "outreach":
                raise Blocked("already reached out to this lead once")

    if channel in WINDOWED:
        last_in = parse_iso((conv or {}).get("last_inbound_at"))
        if not last_in or now - last_in > WINDOWED[channel]:
            raise Blocked("outside the 24-hour messaging window — reply from the Meta Business Suite inbox instead")


def with_disclosure(text: str, first_message: bool) -> str:
    return f"{text}\n\n{AI_DISCLOSURE}" if first_message and AI_DISCLOSURE not in text else text
