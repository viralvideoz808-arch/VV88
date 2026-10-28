from datetime import datetime, timedelta, timezone

import pytest

from carlos import compliance
from carlos.db import now_iso
from carlos.messaging import get_or_create_conversation, queue


@pytest.mark.parametrize("text,expected", [
    ("STOP", True), ("stop.", True), ("Unsubscribe", True), ("please stop messaging me", True),
    ("leave me alone", True), ("don't contact me", True),
    ("Can I stop by tomorrow?", False), ("I need to cancel my booking", False), ("what's the price", False)])
def test_opt_out_detection(text, expected):
    assert compliance.is_opt_out(text) is expected


def lead(db, **kw):
    row = {"platform": "threads", "source_id": kw.pop("source_id", "th:1"), "handle": "mia", "text": "need a car",
           "reply_mode": "threads_reply", "status": "qualified", "posted_at": now_iso(), "created_at": now_iso(),
           "updated_at": now_iso(), **kw}
    return db.insert("leads", row)


def item(db, oid):
    return db.one("SELECT * FROM outbox WHERE id = ?", (oid,))


def test_opted_out_people_are_never_messaged(db):
    lid = lead(db)
    compliance.record_opt_out(db, "threads", "MIA")
    oid = queue(db, kind="outreach", channel="threads_reply", target="123", lead_id=lid, body="hi", approved=True)
    with pytest.raises(compliance.Blocked, match="opted out"):
        compliance.check_send(db, item(db, oid), "UTC")


def test_daily_cap(db):
    db.set_setting("outreach.daily_caps", {"threads_reply": 1})
    first = queue(db, kind="outreach", channel="threads_reply", target="1", lead_id=lead(db), body="x", approved=True)
    db.run("UPDATE outbox SET status = 'sent', sent_at = ? WHERE id = ?", (now_iso(), first))
    second = queue(db, kind="outreach", channel="threads_reply", target="2", lead_id=lead(db, source_id="th:2",
                                                                                          handle="b"), body="x")
    with pytest.raises(compliance.Blocked, match="daily cap"):
        compliance.check_send(db, item(db, second), "UTC")


def test_quiet_hours(db):
    db.set_setting("outreach.quiet_hours", [21, 8])
    late = datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc)
    noon = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    assert compliance.in_quiet_hours(db, "UTC", late)
    assert not compliance.in_quiet_hours(db, "UTC", noon)
    oid = queue(db, kind="outreach", channel="threads_reply", target="1", lead_id=lead(db), body="x")
    with pytest.raises(compliance.Blocked, match="quiet"):
        compliance.check_send(db, item(db, oid), "UTC", now=late)


def test_private_reply_rules(db):
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    lid = lead(db, platform="instagram", source_id="igc:1", reply_mode="ig_private_reply", posted_at=old)
    oid = queue(db, kind="outreach", channel="ig_private_reply", target="c1", lead_id=lid, body="x")
    with pytest.raises(compliance.Blocked, match="7 days"):
        compliance.check_send(db, item(db, oid), "UTC")


def test_one_outreach_per_lead(db):
    lid = lead(db)
    a = queue(db, kind="outreach", channel="threads_reply", target="1", lead_id=lid, body="x")
    db.run("UPDATE outbox SET status = 'sent', sent_at = ? WHERE id = ?", (now_iso(), a))
    b = queue(db, kind="outreach", channel="threads_reply", target="1", lead_id=lid, body="again")
    with pytest.raises(compliance.Blocked, match="already"):
        compliance.check_send(db, item(db, b), "UTC")


def test_dm_24h_window(db):
    conv = get_or_create_conversation(db, "instagram_dm", "igsid-1", handle="andre")
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    db.update("conversations", conv["id"], {"last_inbound_at": stale})
    oid = queue(db, kind="reply", channel="instagram_dm", target="igsid-1", conversation_id=conv["id"], body="hi")
    with pytest.raises(compliance.Blocked, match="24-hour"):
        compliance.check_send(db, item(db, oid), "UTC")
    db.update("conversations", conv["id"], {"last_inbound_at": now_iso()})
    compliance.check_send(db, item(db, oid), "UTC")
