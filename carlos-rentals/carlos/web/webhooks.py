"""Meta webhooks: Messenger + Instagram DMs and comments on our posts arrive here in real time.

Subscribe your Meta app to: Page → messages, feed; Instagram → messages, comments. Callback URL:
{CARLOS_SITE_URL}/webhooks/meta, verify token: META_VERIFY_TOKEN. Every POST is checked against
X-Hub-Signature-256 (HMAC-SHA256 of the raw body with META_APP_SECRET).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from carlos.agents.scout import store_lead
from carlos.config import Settings
from carlos.db import Database
from carlos.messaging import add_inbound, get_or_create_conversation

log = logging.getLogger(__name__)


def valid_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def _ts(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def handle_meta_event(db: Database, settings: Settings, payload: dict) -> dict:
    """Store DMs as conversation messages and comments as leads. Returns counts for logging/tests."""
    obj = payload.get("object")
    counts = {"messages": 0, "comments": 0}
    own_ids = {settings.fb_page_id, settings.ig_user_id} - {""}
    for entry in payload.get("entry", []):
        for ev in entry.get("messaging", []):
            msg = ev.get("message") or {}
            sender = (ev.get("sender") or {}).get("id", "")
            if not sender or msg.get("is_echo") or sender in own_ids:
                continue
            text = msg.get("text") or ("[attachment]" if msg.get("attachments") else "")
            if not text:
                continue
            channel = "instagram_dm" if obj == "instagram" else "messenger"
            conv = get_or_create_conversation(db, channel, sender)
            if add_inbound(db, conv, text, external_id=msg.get("mid", "")):
                counts["messages"] += 1
        for change in entry.get("changes", []):
            field, v = change.get("field"), change.get("value") or {}
            if obj == "instagram" and field == "comments":
                frm = v.get("from") or {}
                if frm.get("id") in own_ids:
                    continue
                item = {"platform": "instagram", "source_id": f"igc:{v.get('id')}", "comment_id": v.get("id", ""),
                        "handle": frm.get("username", ""), "user_id": frm.get("id", ""), "text": v.get("text", ""),
                        "posted_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                        "reply_mode": "ig_private_reply", "found_by": "instagram comment (live)",
                        "media_id": (v.get("media") or {}).get("id", "")}
            elif obj == "page" and field == "feed" and v.get("item") == "comment" and v.get("verb") == "add":
                frm = v.get("from") or {}
                if frm.get("id") in own_ids:
                    continue
                item = {"platform": "facebook", "source_id": f"fbc:{v.get('comment_id')}",
                        "comment_id": v.get("comment_id", ""), "handle": frm.get("name", ""),
                        "user_id": frm.get("id", ""), "text": v.get("message", ""),
                        "posted_at": _ts((v.get("created_time") or 0) * 1000) if v.get("created_time") else "",
                        "reply_mode": "fb_private_reply", "found_by": "facebook comment (live)"}
            else:
                continue
            if item["text"] and store_lead(db, item):
                counts["comments"] += 1
    if any(counts.values()):
        db.log("webhook", "meta", json.dumps(counts))
    return counts

