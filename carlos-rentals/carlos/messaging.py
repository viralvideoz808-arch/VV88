"""Conversations, the outbox, and the dispatcher that sends approved messages through the right channel."""

from __future__ import annotations

import logging
import secrets

from carlos import compliance
from carlos.channels import demo
from carlos.channels.base import ChannelError, SendResult, redact
from carlos.channels.email import send_email
from carlos.config import Settings
from carlos.db import Database, now_iso

log = logging.getLogger(__name__)


def get_or_create_conversation(db: Database, channel: str, external_id: str, *, name: str = "", handle: str = "",
                               lead_id: int | None = None, email: str = "", phone: str = "") -> dict:
    row = db.one("SELECT * FROM conversations WHERE channel = ? AND external_id = ?", (channel, external_id))
    if row:
        updates = {k: v for k, v in (("contact_name", name), ("contact_handle", handle), ("email", email),
                                     ("phone", phone), ("lead_id", lead_id)) if v and not row.get(k)}
        if updates:
            db.update("conversations", row["id"], updates)
            row.update(updates)
        return row
    stamp = now_iso()
    cid = db.insert("conversations", {
        "channel": channel, "external_id": external_id, "lead_id": lead_id, "contact_name": name[:120],
        "contact_handle": handle[:120], "email": email, "phone": phone, "created_at": stamp, "updated_at": stamp,
    }, ignore=True)
    if not cid:  # created by a concurrent request
        return db.one("SELECT * FROM conversations WHERE channel = ? AND external_id = ?", (channel, external_id))
    if lead_id:
        db.run("UPDATE leads SET conversation_id = ? WHERE id = ?", (cid, lead_id))
    return db.one("SELECT * FROM conversations WHERE id = ?", (cid,))


def add_inbound(db: Database, conversation: dict, text: str, external_id: str = "") -> int:
    if external_id and db.one("SELECT 1 FROM messages WHERE conversation_id = ? AND external_id = ?",
                              (conversation["id"], external_id)):
        return 0  # webhook redelivery
    stamp = now_iso()
    mid = db.insert("messages", {"conversation_id": conversation["id"], "direction": "in", "author": "customer",
                                 "body": text[:4000], "external_id": external_id, "created_at": stamp})
    db.run("UPDATE conversations SET last_inbound_at = ?, updated_at = ?, needs_reply = 1, "
           "status = CASE status WHEN 'closed' THEN 'open' ELSE status END WHERE id = ?",
           (stamp, stamp, conversation["id"]))
    platform = compliance.platform_of(conversation["channel"])
    if compliance.is_opt_out(text):
        compliance.record_opt_out(db, platform, conversation["contact_handle"] or conversation["external_id"])
        db.run("UPDATE conversations SET status = 'opted_out', needs_reply = 0 WHERE id = ?", (conversation["id"],))
        if conversation.get("lead_id"):
            db.run("UPDATE leads SET status = 'opted_out', updated_at = ? WHERE id = ?", (stamp, conversation["lead_id"]))
    elif conversation.get("lead_id"):
        db.run("UPDATE leads SET status = CASE WHEN status IN ('contacted', 'qualified', 'new') THEN 'engaged' "
               "ELSE status END, updated_at = ? WHERE id = ?", (stamp, conversation["lead_id"]))
    return mid


def add_outbound(db: Database, conversation_id: int, text: str, author: str, external_id: str = "") -> int:
    stamp = now_iso()
    mid = db.insert("messages", {"conversation_id": conversation_id, "direction": "out", "author": author,
                                 "body": text, "external_id": external_id, "created_at": stamp})
    db.run("UPDATE conversations SET last_outbound_at = ?, updated_at = ?, needs_reply = 0 WHERE id = ?",
           (stamp, stamp, conversation_id))
    return mid


def history(db: Database, conversation_id: int, limit: int = 30) -> list[dict]:
    rows = db.all("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?", (conversation_id, limit))
    return list(reversed(rows))


def queue(db: Database, *, kind: str, channel: str, body: str, target: str = "", lead_id: int | None = None,
          conversation_id: int | None = None, agent: str = "", approved: bool = False, reason: str = "") -> int:
    status = "approved" if approved else "pending_approval"
    oid = db.insert("outbox", {
        "kind": kind, "channel": channel, "target": target, "lead_id": lead_id, "conversation_id": conversation_id,
        "body": body.strip(), "status": status, "agent": agent, "reason": reason, "created_at": now_iso(),
        "decided_at": now_iso() if approved else None,
    })
    return oid


def ref_code(db: Database, lead_id: int) -> str:
    row = db.one("SELECT ref_code FROM leads WHERE id = ?", (lead_id,))
    if row and row["ref_code"]:
        return row["ref_code"]
    code = secrets.token_hex(3)
    db.run("UPDATE leads SET ref_code = ? WHERE id = ?", (code, lead_id))
    return code


class Dispatcher:
    """Sends approved outbox items. Each item is checked against the compliance rules right before sending."""

    def __init__(self, db: Database, settings: Settings, meta=None, threads=None, email_fn=send_email):
        self.db, self.s = db, settings
        self._meta, self._threads, self._email = meta, threads, email_fn

    def meta(self):
        if self._meta is None:
            from carlos.channels.meta import MetaClient
            self._meta = MetaClient(self.s)
        return self._meta

    def threads(self):
        if self._threads is None:
            from carlos.channels.threads import ThreadsClient
            self._threads = ThreadsClient(self.s)
        return self._threads

    def _is_sandbox(self, item: dict) -> bool:
        if item["target"].startswith("demo") or item["target"].startswith("sandbox"):
            return True
        if item["channel"] in ("webchat", "email"):
            return False
        plat = compliance.platform_of(item["channel"])
        return (plat == "threads" and not self.s.threads_enabled) or \
               (plat in ("instagram", "facebook") and not self.s.meta_enabled)

    def _send(self, item: dict) -> SendResult:
        ch, target, text = item["channel"], item["target"], item["body"]
        if self._is_sandbox(item):
            return demo.sandbox_send(ch, target, text)
        if ch == "webchat":
            return SendResult(True, f"web-{item['id']}")  # the browser polls the conversation
        if ch == "email":
            conv = self.db.one("SELECT * FROM conversations WHERE id = ?", (item["conversation_id"],)) or {}
            to = target or conv.get("email", "")
            subject, _, body = text.partition("\n\n") if text.startswith("Subject:") else ("", "", text)
            return self._email(self.s, to, subject.removeprefix("Subject:").strip() or self.s.business_name, body or text)
        if ch in ("instagram_dm", "messenger"):
            return self.meta().send_message(target, text)
        if ch in ("ig_private_reply", "fb_private_reply"):
            return self.meta().private_reply(target, text)
        if ch == "ig_comment_reply":
            return self.meta().reply_ig_comment(target, text)
        if ch == "fb_comment_reply":
            return self.meta().reply_fb_comment(target, text)
        if ch == "fb_post":
            return self.meta().page_post(text, link=self.s.site_url)
        if ch == "threads_reply":
            return self.threads().reply(target, text)
        if ch == "threads_post":
            return self.threads().post(text)
        raise ChannelError(f"no sender for channel {ch}")

    def send_item(self, item: dict) -> str:
        """Returns the new status: sent, blocked (stays approved, retried later), or failed."""
        try:
            compliance.check_send(self.db, item, self.s.timezone)
        except compliance.Blocked as e:
            self.db.run("UPDATE outbox SET error = ? WHERE id = ?", (str(e), item["id"]))
            reason = str(e)
            permanent = any(w in reason for w in ("opted out", "do-not-contact", "only one", "already reached",
                                                  "7 days", "24-hour"))
            if permanent:
                self.db.run("UPDATE outbox SET status = 'blocked' WHERE id = ?", (item["id"],))
                return "blocked"
            return "waiting"
        try:
            result = self._send(item)
        except ChannelError as e:
            self.db.run("UPDATE outbox SET status = 'failed', error = ? WHERE id = ?", (redact(str(e))[:500], item["id"]))
            self.db.log("dispatcher", "send.failed", f"#{item['id']} {item['channel']}: {redact(str(e))[:300]}")
            return "failed"
        stamp = now_iso()
        self.db.run("UPDATE outbox SET status = 'sent', sent_at = ?, external_id = ?, error = '' WHERE id = ?",
                    (stamp, result.external_id, item["id"]))
        conv_id = item.get("conversation_id")
        if item.get("lead_id") and item["kind"] == "outreach":
            lead = self.db.one("SELECT * FROM leads WHERE id = ?", (item["lead_id"],))
            if lead and not conv_id:
                ext = (f"demo:{lead['source_id']}" if lead["source_id"].startswith("demo")
                       else f"{item['channel']}:{item['target']}")
                conv = get_or_create_conversation(self.db, item["channel"], ext, name=lead["name"],
                                                  handle=lead["handle"], lead_id=lead["id"])
                conv_id = conv["id"]
                self.db.run("UPDATE outbox SET conversation_id = ? WHERE id = ?", (conv_id, item["id"]))
                if lead["text"]:
                    self.db.insert("messages", {"conversation_id": conv_id, "direction": "in", "author": "customer",
                                                "body": lead["text"], "external_id": f"post:{lead['source_id']}",
                                                "created_at": lead["posted_at"] or stamp})
            self.db.run("UPDATE leads SET status = CASE WHEN status IN ('new', 'qualified') THEN 'contacted' "
                        "ELSE status END, last_contact_at = ?, updated_at = ? WHERE id = ?",
                        (stamp, stamp, item["lead_id"]))
        if conv_id:
            add_outbound(self.db, conv_id, item["body"], author=item["agent"] or "staff",
                         external_id=result.external_id)
        self.db.log(item["agent"] or "staff", "send.ok", f"#{item['id']} {item['channel']} → {item['target'][:60]}")
        return "sent"

    def flush(self, limit: int = 50) -> dict:
        counts: dict[str, int] = {}
        for item in self.db.all("SELECT * FROM outbox WHERE status = 'approved' ORDER BY id LIMIT ?", (limit,)):
            status = self.send_item(item)
            counts[status] = counts.get(status, 0) + 1
        return counts
