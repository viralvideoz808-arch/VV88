import json
from datetime import date, timedelta
from types import SimpleNamespace

from carlos.agents import make_context, run_agent, run_pipeline
from carlos.agents.concierge import find_dates, handle_conversation, rule_reply, webchat_message
from carlos.agents.qualifier import heuristic
from carlos.channels.base import SendResult
from carlos.config import Settings
from carlos.llm import Claude
from carlos.messaging import add_inbound, get_or_create_conversation


def test_qualifier_rules():
    assert heuristic("Need a rental car this weekend, recommendations?", "threads", [])["intent"] == "rent"
    assert heuristic("Looking to buy a used SUV under 25k", "threads", [])["needs"] == {"budget": 25000,
                                                                                      "vehicle": "suv"}
    assert heuristic("Thinking about selling my Civic", "threads", [])["intent"] == "sell"
    ad = heuristic("🔥 SALE 🔥 Camry 18k DM us we ship nationwide", "threads", [])
    assert ad["intent"] == "not_relevant" and ad["score"] < 10
    near = heuristic("need a rental car in Miami tomorrow", "threads", ["Miami"])
    far = heuristic("need a rental car tomorrow", "threads", ["Miami"])
    assert near["score"] > far["score"]


def test_find_dates():
    today = date(2026, 10, 28)  # a Wednesday
    assert find_dates("2026-11-20 to 2026-11-23", today) == (date(2026, 11, 20), date(2026, 11, 23))
    assert find_dates("from 11/20 to 11/22", today) == (date(2026, 11, 20), date(2026, 11, 22))
    assert find_dates("Saturday to Monday", today) == (date(2026, 10, 31), date(2026, 11, 2))
    assert find_dates("this weekend", today) == (date(2026, 10, 30), date(2026, 11, 1))
    assert find_dates("how much?", today) is None


def test_demo_pipeline_end_to_end(db, settings):
    results = {r["agent"]: r for r in run_pipeline(db, settings)}
    assert all(r["status"] == "ok" for r in results.values()), results
    assert db.scalar("SELECT COUNT(*) FROM leads WHERE status = 'qualified'") >= 4
    drafts = db.all("SELECT * FROM outbox WHERE kind = 'outreach' AND status = 'pending_approval'")
    assert drafts and all(settings.site_url + "/r/" in d["body"] for d in drafts)
    assert all(len(d["body"]) <= 480 for d in drafts)
    # sample DMs were answered, with the AI disclosure on the first reply
    replies = db.all("SELECT * FROM messages WHERE author = 'concierge'")
    assert len(replies) == 2 and all("virtual assistant" in r["body"] for r in replies)
    # approve outreach → sandbox send → sample customers answer → concierge replies in the thread
    db.run("UPDATE outbox SET status = 'approved' WHERE kind = 'outreach'")
    run_agent(db, settings, "outreach")
    assert db.scalar("SELECT COUNT(*) FROM leads WHERE status = 'contacted'") == len(drafts)
    run_agent(db, settings, "concierge")
    assert db.scalar("SELECT COUNT(*) FROM leads WHERE status = 'engaged'") == len(drafts)
    assert db.scalar("SELECT COUNT(*) FROM conversations WHERE needs_reply = 1") == 0


def test_require_approval_off_sends_directly(db, settings):
    db.set_setting("outreach.require_approval", False)
    run_agent(db, settings, "scout")
    run_agent(db, settings, "qualifier")
    run_agent(db, settings, "outreach")
    assert db.scalar("SELECT COUNT(*) FROM outbox WHERE kind = 'outreach' AND status = 'sent'") >= 4


def test_handoff_keyword_switches_to_human(db, settings):
    conv = get_or_create_conversation(db, "webchat", "web:tok1234567890abcd", name="Ann")
    add_inbound(db, conv, "I was in an accident with the Jeep, need help")
    conv = db.one("SELECT * FROM conversations WHERE id = ?", (conv["id"],))
    text = handle_conversation(make_context(db, settings), conv)
    assert "team member" in text
    assert db.one("SELECT mode FROM conversations WHERE id = ?", (conv["id"],))["mode"] == "human"


def test_rule_replies_use_conversation_context(db, settings):
    ctx = make_context(db, settings)
    conv = get_or_create_conversation(db, "webchat", "web:ctx1234567890abcd")
    text, _ = rule_reply(ctx, conv, "How much for Friday to Sunday?", "Need a pickup truck for moving")
    assert "F-150" in text and "$" in text
    text, _ = rule_reply(ctx, conv, "Do you take debit cards?")
    assert "debit" in text.lower()
    text, _ = rule_reply(ctx, conv, "I want to sell my 2018 Civic")
    assert "/sell" in text


def test_webchat_stop(db, settings):
    ctx = make_context(db, settings)
    webchat_message(ctx, "web:stop1234567890abcd", "hi")
    out = webchat_message(ctx, "web:stop1234567890abcd", "STOP")
    assert "won't message" in out["reply"]


# -- Claude path, with a fake client that behaves like the SDK -----------------------------------
class FakeMessages:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)


def block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def fake_claude(settings, script):
    msgs = FakeMessages(script)
    client = SimpleNamespace(beta=SimpleNamespace(messages=msgs))
    return Claude(settings, client=client), msgs


def test_concierge_books_through_claude_tools(db, settings):
    start, end = (date.today() + timedelta(days=3)).isoformat(), (date.today() + timedelta(days=5)).isoformat()
    script = [
        SimpleNamespace(stop_reason="tool_use", content=[
            block("tool_use", id="t1", name="quote_rental",
                  input={"vehicle_slug": "2024-jeep-wrangler-rent", "start_date": start, "end_date": end})]),
        SimpleNamespace(stop_reason="tool_use", content=[
            block("tool_use", id="t2", name="create_booking_request",
                  input={"vehicle_slug": "2024-jeep-wrangler-rent", "start_date": start, "end_date": end,
                         "name": "Gabe R", "email": "gabe@example.com"})]),
        SimpleNamespace(stop_reason="end_turn", content=[block("text", text="Done! Your request is in.")]),
    ]
    claude, msgs = fake_claude(settings, script)
    ctx = make_context(db, settings, claude=claude)
    out = webchat_message(ctx, "web:claude1234567890ab", "Book the Jeep for those dates, Gabe R gabe@example.com")
    assert out["reply"].startswith("Done!")
    b = db.one("SELECT * FROM bookings WHERE email = 'gabe@example.com'")
    assert b and b["status"] == "pending" and b["source"] == "chat:webchat"
    first = msgs.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    results = [b for m in msgs.calls[-1]["messages"] if isinstance(m["content"], list) for b in m["content"]
               if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert json.loads(results[0]["content"])["available"] is True
    assert json.loads(results[1]["content"])["ref"] == b["ref"]


def test_qualifier_uses_claude_structured_output(db, settings):
    run_agent(db, settings, "scout")
    ids = [r["id"] for r in db.all("SELECT id FROM leads WHERE status = 'new'")]
    payload = {"leads": [{"id": i, "intent": "rent", "score": 90, "reason": "r", "vehicle": "suv", "when": "",
                          "budget": 0} for i in ids]}
    claude, msgs = fake_claude(settings, [SimpleNamespace(stop_reason="end_turn",
                                                          content=[block("text", text=json.dumps(payload))])])
    ctx = make_context(db, settings, claude=claude)
    from carlos.agents import AGENTS
    AGENTS["qualifier"].execute(ctx)
    assert msgs.calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert db.scalar("SELECT COUNT(*) FROM leads WHERE score = 90 AND status = 'qualified'") == len(ids)


# -- live mode with fake Meta/Threads clients ------------------------------------------------------
class FakeThreads:
    def __init__(self):
        self.replies = []

    def keyword_search(self, q):
        return [{"platform": "threads", "source_id": "th:555", "media_id": "555", "handle": "realperson",
                 "text": "Need a rental car this weekend in Miami, anyone?", "posted_at": "", "post_url": "",
                 "reply_mode": "threads_reply", "found_by": f"threads search {q}"}]

    def reply(self, reply_to_id, text):
        self.replies.append((reply_to_id, text))
        return SendResult(True, "reply-1")

    def replies_to(self, media_id):
        return [{"id": "777", "text": "How much for Sat-Mon?", "username": "realperson"}]


def test_live_threads_flow(db, tmp_path):
    s = Settings(threads_user_id="1", threads_token="tok", site_url="https://x.example", upload_dir=str(tmp_path))
    fake = FakeThreads()
    db.set_setting("scout.keywords", ["need a rental car"])
    db.set_setting("outreach.require_approval", False)
    for name in ("scout", "qualifier", "outreach"):
        r = run_agent(db, s, name, threads=fake)
        assert r["status"] == "ok", r
    assert fake.replies and fake.replies[0][0] == "555" and "@realperson" in fake.replies[0][1]
    run_agent(db, s, "concierge", threads=fake)  # pulls the reply and answers it publicly
    assert fake.replies[-1][0] == "777"
    assert db.scalar("SELECT COUNT(*) FROM leads WHERE status = 'engaged'") == 1
