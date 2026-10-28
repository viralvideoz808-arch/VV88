import hashlib
import hmac
import json
from datetime import date, timedelta


def d(n):
    return (date.today() + timedelta(days=n)).isoformat()


def test_public_pages(site):
    for path in ["/", "/rentals", "/for-sale", "/rentals?body=suv", f"/rentals?start={d(1)}&end={d(3)}&available=1",
                 "/cars/2025-toyota-corolla-rent", "/cars/2021-honda-accord-sale", "/sell", "/financing", "/contact",
                 "/about", "/faq", "/terms", "/privacy", "/manage", "/sitemap.xml", "/robots.txt", "/healthz"]:
        r = site.get(path)
        assert r.status_code == 200, path
    assert site.get("/cars/nope").status_code == 404
    home = site.get("/")
    assert "CAR" in home.text and "Content-Security-Policy" in home.headers
    assert "Sample listing" in home.text


def test_booking_flow_and_cancel(site, db):
    r = site.post("/cars/2025-toyota-corolla-rent/book", data={
        "start": d(5), "end": d(8), "name": "Ana Ruiz", "email": "ana@example.com", "age": "30", "agree": "on"},
        follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/booking/CL-")
    ref = r.headers["location"].split("/")[2].split("?")[0]
    page = site.get(f"/booking/{ref}")
    assert "pending" in page.text and "$157.29" in page.text  # 3 × $49 + 7% tax
    bad = site.post(f"/booking/{ref}/cancel", data={"email": "wrong@example.com"}, follow_redirects=False)
    assert "match" in bad.headers["location"]
    site.post(f"/booking/{ref}/cancel", data={"email": "ANA@example.com"})
    assert db.one("SELECT status FROM bookings WHERE ref = ?", (ref,))["status"] == "cancelled"


def test_booking_validation(site):
    r = site.post("/cars/2025-toyota-corolla-rent/book", data={"start": d(5), "end": d(8), "name": "A",
                                                                "email": "not-an-email", "agree": "on"},
                  follow_redirects=False)
    assert "error=" in r.headers["location"]
    honeypot = site.post("/cars/2025-toyota-corolla-rent/book", data={"website": "spam"}, follow_redirects=False)
    assert honeypot.status_code == 303


def test_quote_api(site):
    r = site.get("/api/quote", params={"car": "2025-toyota-corolla-rent", "start": d(1), "end": d(8)})
    assert r.json()["base"] == 294
    assert site.get("/api/quote", params={"car": "2025-toyota-corolla-rent", "start": d(3), "end": d(1)}).status_code == 400


def test_inquiry_and_referral(site, db):
    db.insert("leads", {"platform": "threads", "source_id": "th:9", "handle": "x", "intent": "sell",
                        "status": "contacted", "ref_code": "abc123", "created_at": "2026-01-01",
                        "updated_at": "2026-01-01"})
    r = site.get("/r/abc123", follow_redirects=False)
    assert r.headers["location"] == "/sell" and "carlos_ref" in r.headers["set-cookie"]
    site.cookies.set("carlos_ref", "abc123")
    r = site.post("/inquiry", data={"kind": "sell", "name": "X", "email": "x@example.com", "year": "2019",
                                    "make": "Honda", "model": "Civic", "back": "/sell"}, follow_redirects=False)
    assert r.headers["location"].startswith("/sell?sent=1")
    q = db.one("SELECT * FROM inquiries")
    assert q["lead_id"] and json.loads(q["details"])["make"] == "Honda"
    assert site.post("/inquiry", data={"kind": "sell", "name": "X", "back": "//evil.example"},
                     follow_redirects=False).headers["location"].startswith("/contact")


def test_chat_api(site):
    r = site.post("/api/chat", json={"token": "tok_abcdefghijklmnop", "message": "What are your hours?"})
    body = r.json()
    assert "open" in body["reply"].lower() and body["messages"][0]["from"] == "you"
    assert site.post("/api/chat", json={"token": "short", "message": "x"}).status_code == 400
    poll = site.get("/api/chat", params={"token": "tok_abcdefghijklmnop", "after": 0}).json()
    assert len(poll["messages"]) == 2


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()


def test_meta_webhook(site, db):
    assert site.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me",
                                              "hub.challenge": "42"}).text == "42"
    assert site.get("/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "nope"}).status_code == 403
    payload = {"object": "instagram", "entry": [{
        "messaging": [{"sender": {"id": "IGSID1"}, "message": {"mid": "m1", "text": "Is the Jeep free Saturday?"}}],
        "changes": [{"field": "comments", "value": {"id": "C1", "text": "How much to rent the Tahoe?",
                                                     "from": {"id": "U9", "username": "kim"}}}]}]}
    body = json.dumps(payload).encode()
    assert site.post("/webhooks/meta", content=body, headers={"x-hub-signature-256": "sha256=bad"}).status_code == 403
    assert site.post("/webhooks/meta", content=body, headers={"x-hub-signature-256": sign(body)}).status_code == 200
    site.post("/webhooks/meta", content=body, headers={"x-hub-signature-256": sign(body)})  # redelivery
    assert db.scalar("SELECT COUNT(*) FROM messages WHERE external_id = 'm1'") == 1
    lead = db.one("SELECT * FROM leads WHERE source_id = 'igc:C1'")
    assert lead["reply_mode"] == "ig_private_reply" and lead["handle"] == "kim"


def test_console_requires_login_and_csrf(db, settings):
    from fastapi.testclient import TestClient

    from carlos.web.ops import create_ops_app
    c = TestClient(create_ops_app(db, settings, run_scheduler=False))
    assert c.get("/", follow_redirects=False).headers["location"].startswith("/login")
    r = c.post("/login", data={"password": "wrong", "next": "/"}, follow_redirects=False)
    assert "error" in r.headers["location"]
    r = c.post("/login", data={"password": "test-password-123", "next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/"
    assert c.post("/agents/scout/run", data={"csrf": "forged"}).status_code == 403


def test_console_pages_and_actions(ops, db):
    ops.post("/agents/run-all", data={"csrf": ops.csrf})
    for path in ["/", "/agents", "/approvals", "/leads", "/leads/1", "/inbox", "/inbox/1", "/bookings",
                 "/inquiries", "/inventory", "/inventory/1", "/inventory/new", "/settings", "/activity"]:
        assert ops.get(path).status_code == 200, path
    pending = db.one("SELECT id FROM outbox WHERE status = 'pending_approval' AND kind = 'outreach'")
    r = ops.post(f"/approvals/{pending['id']}", data={"csrf": ops.csrf, "action": "approve", "body": "Edited!"},
                 follow_redirects=False)
    assert "Sent" in r.headers["location"] or "Sent" in ops.get(r.headers["location"]).text
    assert db.one("SELECT body, status FROM outbox WHERE id = ?", (pending["id"],)) == {"body": "Edited!",
                                                                                       "status": "sent"}


def test_console_booking_confirm(ops, db):
    from carlos.inventory import create_booking, get_vehicle
    b = create_booking(db, get_vehicle(db, "2025-honda-cr-v-rent"), d(2), d(4), "Lee", "lee@example.com")
    ops.post(f"/bookings/{b['ref']}", data={"csrf": ops.csrf, "status": "confirmed", "notes": "ID checked"})
    row = db.one("SELECT status, notes FROM bookings WHERE id = ?", (b["id"],))
    assert row == {"status": "confirmed", "notes": "ID checked"}


def test_console_vehicle_crud_and_upload(ops, db):
    r = ops.post("/inventory/new", data={"csrf": ops.csrf, "listing": "sale", "year": "2020", "make": "Mazda",
                                         "model": "CX-5", "body": "suv", "sale_price": "21,500", "mileage": "40000",
                                         "status": "available"}, follow_redirects=False)
    vid = int(r.headers["location"].split("/")[2].split("?")[0])
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    ops.post(f"/inventory/{vid}", data={"csrf": ops.csrf, "action": "upload"},
             files=[("photos", ("a.png", png, "image/png")), ("photos", ("evil.html", b"<script>", "text/html"))])
    v = db.one("SELECT * FROM vehicles WHERE id = ?", (vid,))
    assert v["sale_price"] == 21500 and len(json.loads(v["photos"])) == 1
    ops.post(f"/inventory/{vid}", data={"csrf": ops.csrf, "action": "delete"})
    assert db.one("SELECT 1 FROM vehicles WHERE id = ?", (vid,)) is None
