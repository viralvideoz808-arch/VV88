"""The operations console: run the agents, approve messages, work the inbox, manage bookings and the fleet."""

from __future__ import annotations

import hmac
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote as urlquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from markupsafe import Markup
from starlette.middleware.sessions import SessionMiddleware

from carlos import compliance
from carlos.agents import AGENTS, PIPELINE, Scheduler, make_context, run_due
from carlos.agents.concierge import compose_reply, reply_target
from carlos.config import Settings
from carlos.db import DEFAULTS, Database, loads, now_iso, parse_iso
from carlos.inventory import (BODY_TYPES, BOOKING_STATUSES, VEHICLE_STATUSES, get_booking, get_vehicle, list_vehicles,
                              money, save_vehicle)
from carlos.messaging import Dispatcher, history, queue
from carlos.web.common import WEB_DIR, RateLimiter, client_ip, html, make_env, security_headers, to_float, to_int

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
PUBLIC = ("/login", "/static", "/healthz")
MAX_UPLOAD = 12 * 1024 * 1024
IMAGE_MAGIC = {b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png", b"RIFF": ".webp"}
LEAD_STATUSES = ("new", "qualified", "manual", "contacted", "engaged", "converted", "low", "review", "no_response",
                 "opted_out", "do_not_contact")


def create_ops_app(db: Database, settings: Settings, run_scheduler: bool = True) -> FastAPI:
    scheduler = Scheduler(db, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if run_scheduler:
            scheduler.start()
        yield
        await scheduler.stop()

    app = FastAPI(title="CAR-LOS console", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    env = make_env()
    app.state.db, app.state.settings, app.state.scheduler = db, settings, scheduler
    login_limit = RateLimiter(10, 900)

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    uploads = Path(settings.upload_dir)
    uploads.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=uploads), name="uploads")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if not any(path == p or path.startswith(p + "/") for p in PUBLIC) and not request.session.get("user"):
            nxt = urlquote(path if path.startswith("/") and not path.startswith("//") else "/")
            return security_headers(RedirectResponse(f"/login?next={nxt}", 303), CSP)
        length = int(request.headers.get("content-length") or 0)
        if length > MAX_UPLOAD:
            return security_headers(html(env, "ops/error.html", 413, msg="Upload too large (12 MB max)."), CSP)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return security_headers(response, CSP)

    # Added after the guard so it runs first (Starlette wraps middleware outside-in).
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key or secrets.token_urlsafe(32),
                       session_cookie="carlos_console", max_age=14 * 86400, same_site="strict",
                       https_only=settings.https_cookies)

    def csrf(request: Request) -> str:
        tok = request.session.get("csrf")
        if not tok:
            tok = request.session["csrf"] = secrets.token_urlsafe(24)
        return tok

    async def form(request: Request):
        data = await request.form()
        if not hmac.compare_digest(str(data.get("csrf", "")), request.session.get("csrf", "") or "-"):
            raise HTTPException(403, "Session expired — reload the page and try again.")
        return data

    def counts() -> dict:
        return {
            "approvals": db.scalar("SELECT COUNT(*) FROM outbox WHERE status = 'pending_approval'"),
            "inbox": db.scalar("SELECT COUNT(*) FROM conversations WHERE needs_reply = 1 AND status = 'open'"),
            "human": db.scalar("SELECT COUNT(*) FROM conversations WHERE mode = 'human' AND needs_reply = 1"),
            "bookings": db.scalar("SELECT COUNT(*) FROM bookings WHERE status = 'pending'"),
            "inquiries": db.scalar("SELECT COUNT(*) FROM inquiries WHERE status = 'new'"),
            "leads": db.scalar("SELECT COUNT(*) FROM leads WHERE status IN ('qualified', 'manual')"),
        }

    def page(request: Request, name: str, code: int = 200, **ctx):
        tok = csrf(request)
        field = Markup(f'<input type="hidden" name="csrf" value="{tok}">')
        return html(env, name, code, s=settings, path=request.url.path, csrf=tok, csrf_field=lambda: field,
                    counts=counts(),
                    flash=request.query_params.get("msg", ""), agents=AGENTS, compliance=compliance, **ctx)

    def back(to: str, msg: str = "") -> RedirectResponse:
        if not to.startswith("/") or to.startswith("//"):
            to = "/"
        sep = "&" if "?" in to else "?"
        return RedirectResponse(f"{to}{sep}msg={urlquote(msg)}" if msg else to, 303)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return html(env, "ops/error.html", exc.status_code, msg=exc.detail, s=settings)

    # -- auth ----------------------------------------------------------------------------------
    @app.get("/login")
    def login_page(request: Request):
        return html(env, "ops/login.html", s=settings, error=request.query_params.get("error", ""),
                    next=request.query_params.get("next", "/"))

    @app.post("/login")
    async def login(request: Request):
        data = await request.form()
        nxt = str(data.get("next", "/"))
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        if not login_limit.allow(client_ip(request)):
            return RedirectResponse("/login?error=Too+many+attempts.+Wait+15+minutes.", 303)
        if not hmac.compare_digest(str(data.get("password", "")).encode(), settings.ops_password.encode()):
            db.log("console", "login.failed", client_ip(request))
            return RedirectResponse(f"/login?error=Wrong+password&next={urlquote(nxt)}", 303)
        request.session.clear()
        request.session["user"] = "owner"
        request.session["csrf"] = secrets.token_urlsafe(24)
        db.log("console", "login", client_ip(request))
        return RedirectResponse(nxt, 303)

    @app.post("/logout")
    async def logout(request: Request):
        await form(request)
        request.session.clear()
        return RedirectResponse("/login", 303)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # -- dashboard -------------------------------------------------------------------------------
    @app.get("/")
    def dashboard(request: Request):
        today = date.today().isoformat()
        since7 = (datetime.now(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat()
        since30 = (date.today() - timedelta(days=30)).isoformat()
        kpi = {
            "leads_7d": db.scalar("SELECT COUNT(*) FROM leads WHERE created_at >= ?", (since7,)),
            "qualified_7d": db.scalar("SELECT COUNT(*) FROM leads WHERE created_at >= ? AND intent IN "
                                      "('rent', 'buy', 'sell') AND score >= ?",
                                      (since7, db.get_setting("outreach.min_score"))),
            "contacted_7d": db.scalar("SELECT COUNT(*) FROM outbox WHERE kind = 'outreach' AND status = 'sent' "
                                      "AND sent_at >= ?", (since7,)),
            "converted": db.scalar("SELECT COUNT(*) FROM leads WHERE status = 'converted'"),
            "bookings_30d": db.scalar("SELECT COUNT(*) FROM bookings WHERE created_at >= ? AND status != 'cancelled'",
                                      (since30,)),
            "revenue_30d": db.scalar("SELECT COALESCE(SUM(total), 0) FROM bookings WHERE status IN "
                                     "('confirmed', 'active', 'completed') AND start_date >= ?", (since30,)),
            "on_rent": db.scalar("SELECT COUNT(*) FROM bookings WHERE status = 'active'"),
            "fleet": db.scalar("SELECT COUNT(*) FROM vehicles WHERE listing = 'rent' AND status = 'available'"),
            "for_sale": db.scalar("SELECT COUNT(*) FROM vehicles WHERE listing = 'sale' AND status IN "
                                  "('available', 'pending_sale')"),
            "views_7d": db.scalar("SELECT COALESCE(SUM(count), 0) FROM pageviews WHERE day >= ?",
                                  ((date.today() - timedelta(days=7)).isoformat(),)),
        }
        runs = {n: db.one("SELECT * FROM agent_runs WHERE agent = ? ORDER BY id DESC LIMIT 1", (n,)) for n in PIPELINE}
        pickups = db.all("SELECT b.*, v.make || ' ' || v.model AS car FROM bookings b JOIN vehicles v ON v.id = "
                         "b.vehicle_id WHERE b.status IN ('confirmed', 'pending') AND b.start_date >= ? "
                         "ORDER BY b.start_date LIMIT 6", (today,))
        returns = db.all("SELECT b.*, v.make || ' ' || v.model AS car FROM bookings b JOIN vehicles v ON v.id = "
                         "b.vehicle_id WHERE b.status = 'active' ORDER BY b.end_date LIMIT 6")
        hot = db.all("SELECT * FROM leads WHERE status IN ('qualified', 'engaged', 'manual') ORDER BY score DESC, id "
                     "DESC LIMIT 6")
        events = db.all("SELECT * FROM events ORDER BY id DESC LIMIT 12")
        funnel = [(label, db.scalar(sql)) for label, sql in (
            ("Found", "SELECT COUNT(*) FROM leads"),
            ("Qualified", "SELECT COUNT(*) FROM leads WHERE status NOT IN ('new', 'low', 'review')"),
            ("Contacted", "SELECT COUNT(*) FROM leads WHERE status IN ('contacted', 'engaged', 'converted', "
                          "'no_response', 'opted_out')"),
            ("Engaged", "SELECT COUNT(*) FROM leads WHERE status IN ('engaged', 'converted')"),
            ("Booked / bought", "SELECT COUNT(*) FROM leads WHERE status = 'converted'"))]
        return page(request, "ops/dashboard.html", kpi=kpi, runs=runs, pickups=pickups, returns=returns, hot=hot,
                    events=events, issues=db.get_setting("fleet.issues") or [], funnel=funnel,
                    paused=db.get_setting("agents_paused"))

    # -- agents ----------------------------------------------------------------------------------
    @app.get("/agents")
    def agents_page(request: Request):
        info = []
        for name in PIPELINE:
            runs = db.all("SELECT * FROM agent_runs WHERE agent = ? ORDER BY id DESC LIMIT 8", (name,))
            info.append({"agent": AGENTS[name], "enabled": db.get_setting(f"agent.{name}.enabled"),
                         "interval": db.get_setting(f"agent.{name}.interval"), "runs": runs})
        return page(request, "ops/agents.html", info=info, paused=db.get_setting("agents_paused"),
                    llm=settings.llm_enabled)

    @app.post("/agents/run-all")
    async def run_all(request: Request):
        await form(request)
        done = [await scheduler.run(n, "manual") for n in PIPELINE if db.get_setting(f"agent.{n}.enabled")]
        errors = [d["agent"] for d in done if d["status"] == "error"]
        return back("/agents", f"Ran {len(done)} agents" + (f" — errors in {', '.join(errors)}" if errors else ""))

    @app.post("/agents/pause")
    async def pause(request: Request):
        data = await form(request)
        new = not db.get_setting("agents_paused")
        db.set_setting("agents_paused", new)
        db.log("console", "agents.paused" if new else "agents.resumed")
        return back(str(data.get("back", "/agents")), "All agents paused" if new else "Agents running")

    @app.post("/agents/tick")
    async def tick(request: Request):
        """Hosts without a background loop: the open console calls this every minute to run due agents."""
        await form(request)
        import asyncio
        done = await asyncio.to_thread(run_due, db, settings, "console", 50)
        return JSONResponse({"ran": [d["agent"] for d in done]})

    @app.post("/agents/{name}/run")
    async def run_one(request: Request, name: str):
        await form(request)
        if name not in AGENTS:
            raise HTTPException(404, "No such agent")
        r = await scheduler.run(name, "manual")
        return back("/agents", f"{AGENTS[name].title}: {r['summary']}")

    @app.post("/agents/{name}/settings")
    async def agent_settings(request: Request, name: str):
        data = await form(request)
        if name not in AGENTS:
            raise HTTPException(404, "No such agent")
        db.set_setting(f"agent.{name}.enabled", data.get("enabled") == "on")
        db.set_setting(f"agent.{name}.interval", max(1, min(1440, to_int(data.get("interval"), 30))))
        return back("/agents", f"Saved {AGENTS[name].title}")

    # -- approvals ---------------------------------------------------------------------------------
    @app.get("/approvals")
    def approvals(request: Request):
        pending = db.all("SELECT o.*, l.handle, l.platform, l.text AS lead_text, l.post_url, l.score, l.intent, "
                         "c.contact_name, c.contact_handle FROM outbox o LEFT JOIN leads l ON l.id = o.lead_id "
                         "LEFT JOIN conversations c ON c.id = o.conversation_id WHERE o.status = 'pending_approval' "
                         "ORDER BY o.id")
        recent = db.all("SELECT * FROM outbox WHERE status != 'pending_approval' ORDER BY id DESC LIMIT 40")
        return page(request, "ops/approvals.html", pending=pending, recent=recent)

    @app.post("/approvals/{oid}")
    async def decide(request: Request, oid: int):
        data = await form(request)
        item = db.one("SELECT * FROM outbox WHERE id = ?", (oid,))
        if not item or item["status"] not in ("pending_approval", "failed", "approved"):
            return back("/approvals", "Already handled")
        action = data.get("action")
        body = str(data.get("body", item["body"])).strip()[:2000] or item["body"]
        if action == "reject":
            db.run("UPDATE outbox SET status = 'rejected', decided_at = ? WHERE id = ?", (now_iso(), oid))
            if item["lead_id"] and item["kind"] == "outreach":
                db.run("UPDATE leads SET status = 'low', updated_at = ? WHERE id = ?", (now_iso(), item["lead_id"]))
            return back("/approvals", "Rejected")
        db.run("UPDATE outbox SET status = 'approved', body = ?, decided_at = ?, error = '' WHERE id = ?",
               (body, now_iso(), oid))
        status = Dispatcher(db, settings).send_item(db.one("SELECT * FROM outbox WHERE id = ?", (oid,)))
        err = db.scalar("SELECT error FROM outbox WHERE id = ?", (oid,))
        msg = {"sent": "Sent ✓", "waiting": f"Approved — will send when allowed ({err})",
               "blocked": f"Blocked: {err}", "failed": f"Send failed: {err}"}[status]
        return back("/approvals", msg)

    @app.post("/approvals-all")
    async def approve_all(request: Request):
        await form(request)
        db.run("UPDATE outbox SET status = 'approved', decided_at = ? WHERE status = 'pending_approval'", (now_iso(),))
        result = Dispatcher(db, settings).flush(limit=200)
        return back("/approvals", "Approved all · " + (", ".join(f"{v} {k}" for k, v in result.items()) or "none"))

    # -- leads -------------------------------------------------------------------------------------
    @app.get("/leads")
    def leads(request: Request):
        st = request.query_params.get("status", "")
        plat = request.query_params.get("platform", "")
        where, args = [], []
        if st in LEAD_STATUSES:
            where.append("status = ?")
            args.append(st)
        if plat in ("threads", "instagram", "facebook", "manual"):
            where.append("platform = ?")
            args.append(plat)
        rows = db.all("SELECT * FROM leads" + (" WHERE " + " AND ".join(where) if where else "") +
                      " ORDER BY CASE status WHEN 'qualified' THEN 0 WHEN 'manual' THEN 1 WHEN 'engaged' THEN 2 "
                      "ELSE 3 END, score DESC, id DESC LIMIT 300", args)
        by_status = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) AS n FROM leads GROUP BY status")}
        return page(request, "ops/leads.html", rows=rows, status=st, platform=plat, by_status=by_status,
                    statuses=LEAD_STATUSES)

    @app.get("/leads/{lid}")
    def lead(request: Request, lid: int):
        row = db.one("SELECT * FROM leads WHERE id = ?", (lid,))
        if not row:
            raise HTTPException(404, "Lead not found")
        outbox = db.all("SELECT * FROM outbox WHERE lead_id = ? ORDER BY id", (lid,))
        conv = db.one("SELECT * FROM conversations WHERE lead_id = ? ORDER BY id DESC LIMIT 1", (lid,))
        bookings = db.all("SELECT * FROM bookings WHERE lead_id = ?", (lid,))
        inquiries = db.all("SELECT * FROM inquiries WHERE lead_id = ?", (lid,))
        return page(request, "ops/lead.html", l=row, needs=loads(row["needs"], {}), outbox=outbox, conv=conv,
                    bookings=bookings, inquiries=inquiries, statuses=LEAD_STATUSES)

    @app.post("/leads/{lid}")
    async def lead_update(request: Request, lid: int):
        data = await form(request)
        row = db.one("SELECT * FROM leads WHERE id = ?", (lid,))
        if not row:
            raise HTTPException(404, "Lead not found")
        action = data.get("action")
        if action == "status" and data.get("status") in LEAD_STATUSES:
            db.run("UPDATE leads SET status = ?, updated_at = ? WHERE id = ?", (data["status"], now_iso(), lid))
            if data["status"] in ("do_not_contact", "opted_out"):
                compliance.record_opt_out(db, row["platform"], row["handle"])
                db.run("UPDATE outbox SET status = 'rejected' WHERE lead_id = ? AND status IN ('pending_approval', "
                       "'approved')", (lid,))
            return back(f"/leads/{lid}", "Updated")
        if action == "requalify":
            db.run("UPDATE leads SET status = 'new', updated_at = ? WHERE id = ?", (now_iso(), lid))
            await scheduler.run("qualifier", "manual")
            await scheduler.run("outreach", "manual")
            return back(f"/leads/{lid}", "Re-qualified and drafted")
        return back(f"/leads/{lid}")

    @app.post("/leads-add")
    async def lead_add(request: Request):
        data = await form(request)
        text = str(data.get("text", "")).strip()
        if not text:
            return back("/leads", "Paste the post text")
        stamp = now_iso()
        lid = db.insert("leads", {
            "platform": "manual", "source_id": f"manual:{uuid.uuid4().hex[:12]}",
            "handle": str(data.get("handle", "")).strip()[:120], "name": str(data.get("handle", "")).strip()[:120],
            "post_url": str(data.get("url", "")).strip()[:500], "text": text[:3000], "posted_at": stamp,
            "found_by": "added by hand", "reply_mode": "manual", "status": "new", "created_at": stamp,
            "updated_at": stamp,
        })
        await scheduler.run("qualifier", "manual")
        return back(f"/leads/{lid}", "Lead added and scored")

    # -- inbox --------------------------------------------------------------------------------------
    @app.get("/inbox")
    def inbox(request: Request):
        f = request.query_params.get("f", "open")
        where = {"open": "status = 'open'", "waiting": "needs_reply = 1 AND status = 'open'",
                 "human": "mode = 'human'", "closed": "status != 'open'"}.get(f, "status = 'open'")
        convs = db.all(f"SELECT c.*, (SELECT body FROM messages m WHERE m.conversation_id = c.id ORDER BY id DESC "
                       f"LIMIT 1) AS last_body FROM conversations c WHERE {where} ORDER BY needs_reply DESC, "
                       f"updated_at DESC LIMIT 200")
        return page(request, "ops/inbox.html", convs=convs, f=f)

    @app.get("/inbox/{cid}")
    def conversation(request: Request, cid: int):
        conv = db.one("SELECT * FROM conversations WHERE id = ?", (cid,))
        if not conv:
            raise HTTPException(404, "Conversation not found")
        lead = db.one("SELECT * FROM leads WHERE id = ?", (conv["lead_id"],)) if conv["lead_id"] else None
        pending = db.all("SELECT * FROM outbox WHERE conversation_id = ? AND status IN ('pending_approval', "
                         "'approved', 'failed') ORDER BY id", (cid,))
        channel, target = reply_target(make_context(db, settings), conv)
        window_ok = True
        if channel in compliance.WINDOWED:
            last = parse_iso(conv["last_inbound_at"])
            window_ok = bool(last and datetime.now(timezone.utc) - last <= compliance.WINDOWED[channel])
        return page(request, "ops/conversation.html", c=conv, msgs=history(db, cid, 200), lead=lead,
                    pending=pending, reply_channel=channel, window_ok=window_ok,
                    draft=request.query_params.get("draft", ""))

    @app.post("/inbox/{cid}")
    async def conversation_action(request: Request, cid: int):
        data = await form(request)
        conv = db.one("SELECT * FROM conversations WHERE id = ?", (cid,))
        if not conv:
            raise HTTPException(404, "Conversation not found")
        action = data.get("action")
        if action == "reply":
            text = str(data.get("body", "")).strip()[:2000]
            if not text:
                return back(f"/inbox/{cid}")
            channel, target = reply_target(make_context(db, settings), conv)
            oid = queue(db, kind="reply", channel=channel, target=target, conversation_id=cid,
                        lead_id=conv["lead_id"], body=text, agent="staff", approved=True, reason="sent by you")
            item = db.one("SELECT * FROM outbox WHERE id = ?", (oid,))
            status = Dispatcher(db, settings).send_item(item)
            err = db.scalar("SELECT error FROM outbox WHERE id = ?", (oid,))
            return back(f"/inbox/{cid}", "Sent ✓" if status == "sent" else f"Not sent: {err}")
        if action == "draft":
            text, handoff, _ = compose_reply(make_context(db, settings), conv)
            return back(f"/inbox/{cid}?draft={urlquote(text[:1500])}",
                        "Draft ready — edit and send" + (f" (assistant suggests handoff: {handoff})" if handoff else ""))
        if action == "mode":
            mode = "human" if conv["mode"] == "auto" else "auto"
            db.update("conversations", cid, {"mode": mode})
            return back(f"/inbox/{cid}", "You've taken over this conversation" if mode == "human"
                        else "Assistant is handling this conversation again")
        if action == "close":
            db.update("conversations", cid, {"status": "closed", "needs_reply": 0})
            return back("/inbox", "Closed")
        if action == "reopen":
            db.update("conversations", cid, {"status": "open"})
            return back(f"/inbox/{cid}", "Reopened")
        if action == "note":
            db.update("conversations", cid, {"summary": str(data.get("summary", ""))[:2000]})
            return back(f"/inbox/{cid}", "Notes saved")
        return back(f"/inbox/{cid}")

    # -- bookings ------------------------------------------------------------------------------------
    @app.get("/bookings")
    def bookings(request: Request):
        st = request.query_params.get("status", "")
        where = "WHERE b.status = ?" if st in BOOKING_STATUSES else ""
        rows = db.all("SELECT b.*, v.year || ' ' || v.make || ' ' || v.model AS car FROM bookings b JOIN vehicles v "
                      f"ON v.id = b.vehicle_id {where} ORDER BY CASE b.status WHEN 'pending' THEN 0 WHEN 'confirmed' "
                      "THEN 1 WHEN 'active' THEN 2 ELSE 3 END, b.start_date LIMIT 300", (st,) if where else ())
        return page(request, "ops/bookings.html", rows=rows, status=st, statuses=BOOKING_STATUSES)

    @app.get("/bookings/{ref}")
    def booking_page(request: Request, ref: str):
        b = get_booking(db, ref)
        if not b:
            raise HTTPException(404, "Booking not found")
        lead = db.one("SELECT * FROM leads WHERE id = ?", (b["lead_id"],)) if b["lead_id"] else None
        return page(request, "ops/booking.html", b=b, lead=lead, statuses=BOOKING_STATUSES,
                    extras=db.get_setting("rental.extras") or {})

    @app.post("/bookings/{ref}")
    async def booking_update(request: Request, ref: str):
        data = await form(request)
        b = get_booking(db, ref)
        if not b:
            raise HTTPException(404, "Booking not found")
        new = data.get("status")
        notes = str(data.get("notes", b["notes"]))[:2000]
        if new not in BOOKING_STATUSES:
            db.run("UPDATE bookings SET notes = ?, updated_at = ? WHERE id = ?", (notes, now_iso(), b["id"]))
            return back(f"/bookings/{b['ref']}", "Notes saved")
        db.run("UPDATE bookings SET status = ?, notes = ?, updated_at = ? WHERE id = ?", (new, notes, now_iso(), b["id"]))
        db.log("console", f"booking.{new}", b["ref"])
        msg = f"Booking {new}"
        if new in ("confirmed", "cancelled") and b["status"] != new and settings.email_enabled:
            car = b["vehicle"]["title"]
            if new == "confirmed":
                body = (f"Subject: Confirmed: your {car} — {b['ref']}\n\nHi {b['name'].split(' ')[0]},\n\nYou're all "
                        f"set! Your {car} is confirmed for {b['start_date']} → {b['end_date']}. Total "
                        f"{money(b['total'])}, refundable deposit {money(b['deposit'])} at pickup.\n\nBring your "
                        f"driver's license and a card in your name.\n{settings.site_url}/booking/{b['ref']}\n\n— "
                        f"{settings.business_name}")
            else:
                body = (f"Subject: Booking {b['ref']} cancelled\n\nHi {b['name'].split(' ')[0]},\n\nYour booking for "
                        f"the {car} ({b['start_date']} → {b['end_date']}) has been cancelled. Questions? Just reply."
                        f"\n\n— {settings.business_name}")
            oid = queue(db, kind="followup", channel="email", target=b["email"], body=body, agent="staff",
                        approved=True, reason=f"booking {new} {b['ref']}")
            st = Dispatcher(db, settings).send_item(db.one("SELECT * FROM outbox WHERE id = ?", (oid,)))
            msg += " · email " + ("sent" if st == "sent" else "not sent")
        elif new == "confirmed" and not settings.email_enabled:
            msg += " · connect email (SMTP) to notify customers automatically — call or text them for now"
        return back(f"/bookings/{b['ref']}", msg)

    # -- inquiries -------------------------------------------------------------------------------------
    @app.get("/inquiries")
    def inquiries(request: Request):
        rows = db.all("SELECT i.*, v.year || ' ' || v.make || ' ' || v.model AS car, v.slug FROM inquiries i LEFT "
                      "JOIN vehicles v ON v.id = i.vehicle_id ORDER BY CASE i.status WHEN 'new' THEN 0 ELSE 1 END, "
                      "i.id DESC LIMIT 300")
        for r in rows:
            r["details"] = loads(r["details"], {})
        return page(request, "ops/inquiries.html", rows=rows)

    @app.post("/inquiries/{iid}")
    async def inquiry_update(request: Request, iid: int):
        data = await form(request)
        if data.get("status") in ("new", "in_progress", "won", "lost", "closed"):
            db.run("UPDATE inquiries SET status = ? WHERE id = ?", (data["status"], iid))
        return back("/inquiries", "Updated")

    # -- inventory ---------------------------------------------------------------------------------------
    @app.get("/inventory")
    def inventory(request: Request):
        cars = list_vehicles(db, public=False, sort="recent", listing=request.query_params.get("listing") or None)
        upcoming = {r["vehicle_id"]: r["n"] for r in db.all(
            "SELECT vehicle_id, COUNT(*) AS n FROM bookings WHERE status IN ('pending', 'confirmed', 'active') "
            "GROUP BY vehicle_id")}
        return page(request, "ops/inventory.html", cars=cars, upcoming=upcoming,
                    listing=request.query_params.get("listing", ""))

    @app.get("/inventory/new")
    def vehicle_new(request: Request):
        listing = request.query_params.get("listing", "rent")
        return page(request, "ops/vehicle.html", v=None, listing=listing if listing in ("rent", "sale") else "rent",
                    body_types=BODY_TYPES, statuses=VEHICLE_STATUSES)

    @app.get("/inventory/{vid}")
    def vehicle_edit(request: Request, vid: int):
        v = get_vehicle(db, vid)
        if not v:
            raise HTTPException(404, "Vehicle not found")
        bookings_ = db.all("SELECT * FROM bookings WHERE vehicle_id = ? AND status IN ('pending', 'confirmed', "
                           "'active') ORDER BY start_date", (vid,))
        return page(request, "ops/vehicle.html", v=v, listing=v["listing"], body_types=BODY_TYPES,
                    statuses=VEHICLE_STATUSES, bookings=bookings_)

    def vehicle_fields(data) -> dict:
        out = {k: str(data.get(k, "")).strip()[:500] for k in (
            "make", "model", "trim", "body", "listing", "transmission", "fuel", "mpg", "color", "color_hex", "vin",
            "location", "status")}
        out["description"] = str(data.get("description", "")).strip()[:4000]
        out["year"] = to_int(data.get("year"), date.today().year)
        for k in ("daily_rate", "weekly_rate", "deposit", "sale_price"):
            out[k] = to_float(data.get(k))
        for k in ("mileage", "seats", "doors"):
            out[k] = to_int(data.get(k))
        out["featured"] = int(data.get("featured") == "on")
        out["features"] = [f.strip() for f in str(data.get("features", "")).splitlines() if f.strip()][:30]
        if out["body"] not in BODY_TYPES:
            out["body"] = "sedan"
        if out["listing"] not in ("rent", "sale"):
            out["listing"] = "rent"
        if out["status"] not in VEHICLE_STATUSES:
            out["status"] = "available"
        if not out["color_hex"].startswith("#") or len(out["color_hex"]) != 7:
            out["color_hex"] = "#9aa3ad"
        return out

    @app.post("/inventory/new")
    async def vehicle_create(request: Request):
        data = await form(request)
        fields = vehicle_fields(data)
        if not fields["make"] or not fields["model"]:
            return back(f"/inventory/new?listing={fields['listing']}", "Make and model are required")
        vid = save_vehicle(db, {**fields, "sample": 0})
        db.log("console", "vehicle.created", f"#{vid} {fields['year']} {fields['make']} {fields['model']}")
        return back(f"/inventory/{vid}", "Saved — now add photos")

    @app.post("/inventory/{vid}")
    async def vehicle_save(request: Request, vid: int):
        data = await form(request)
        v = get_vehicle(db, vid)
        if not v:
            raise HTTPException(404, "Vehicle not found")
        action = data.get("action", "save")
        if action == "delete":
            if db.scalar("SELECT COUNT(*) FROM bookings WHERE vehicle_id = ?", (vid,)):
                db.update("vehicles", vid, {"status": "hidden", "updated_at": now_iso()})
                return back("/inventory", "This car has bookings, so it was hidden instead of deleted")
            db.run("DELETE FROM vehicles WHERE id = ?", (vid,))
            return back("/inventory", "Deleted")
        if action == "upload":
            photos = list(v["photos"])
            added = 0
            for up in data.getlist("photos"):
                if not getattr(up, "filename", ""):
                    continue
                raw = await up.read(MAX_UPLOAD + 1)
                ext = next((e for magic, e in IMAGE_MAGIC.items() if raw.startswith(magic)), None)
                if not ext or len(raw) > MAX_UPLOAD or (ext == ".webp" and raw[8:12] != b"WEBP"):
                    continue
                try:
                    photos.append(store_photo(settings, raw, ext))
                except Exception as e:  # storage outage: keep the other photos
                    db.log("console", "upload.failed", str(e)[:300])
                    continue
                added += 1
            save_vehicle(db, {"photos": photos[:20], "sample": 0}, vid)
            return back(f"/inventory/{vid}", f"{added} photo(s) added" if added else "No valid JPG/PNG/WebP images")
        if action == "photo_remove":
            photos = [p for p in v["photos"] if p != data.get("photo")]
            save_vehicle(db, {"photos": photos}, vid)
            return back(f"/inventory/{vid}", "Photo removed")
        if action == "photo_first":
            p = data.get("photo")
            photos = [p] + [x for x in v["photos"] if x != p] if p in v["photos"] else v["photos"]
            save_vehicle(db, {"photos": photos}, vid)
            return back(f"/inventory/{vid}", "Cover photo set")
        fields = vehicle_fields(data)
        save_vehicle(db, {**fields, "sample": 0}, vid)
        return back(f"/inventory/{vid}", "Saved")

    # -- settings ---------------------------------------------------------------------------------------
    @app.get("/settings")
    def settings_page(request: Request):
        return page(request, "ops/settings.html", cfg=db.all_settings(), defaults=DEFAULTS,
                    connections=connections(settings))

    @app.post("/settings")
    async def settings_save(request: Request):
        data = await form(request)
        lines = lambda k: [x.strip() for x in str(data.get(k, "")).splitlines() if x.strip()][:40]  # noqa: E731
        db.set_setting("scout.keywords", lines("keywords"))
        db.set_setting("scout.hashtags", [h.lstrip("#") for h in lines("hashtags")][:10])
        db.set_setting("scout.locations", lines("locations"))
        db.set_setting("scout.max_age_days", max(1, min(30, to_int(data.get("max_age_days"), 7))))
        db.set_setting("outreach.require_approval", data.get("outreach_approval") == "on")
        db.set_setting("outreach.min_score", max(0, min(100, to_int(data.get("min_score"), 55))))
        caps = {ch: max(0, min(500, to_int(data.get(f"cap_{ch}"), 10))) for ch in (
            "threads_reply", "ig_private_reply", "fb_private_reply", "ig_comment_reply", "fb_comment_reply")}
        db.set_setting("outreach.daily_caps", caps)
        db.set_setting("outreach.quiet_hours", [max(0, min(23, to_int(data.get("quiet_start"), 21))),
                                                max(0, min(23, to_int(data.get("quiet_end"), 8)))])
        db.set_setting("concierge.auto_reply", data.get("auto_reply") == "on")
        db.set_setting("concierge.require_approval", data.get("concierge_approval") == "on")
        db.set_setting("concierge.handoff_keywords", [w.lower() for w in lines("handoff")])
        db.set_setting("site.tagline", str(data.get("tagline", "")).strip()[:120] or DEFAULTS["site.tagline"])
        db.set_setting("site.hours", str(data.get("hours", "")).strip()[:120] or DEFAULTS["site.hours"])
        db.set_setting("site.announcement", str(data.get("announcement", "")).strip()[:200])
        db.set_setting("rental.tax_rate", max(0.0, min(0.3, (to_float(data.get("tax_rate"), 7.0) or 0) / 100)))
        db.set_setting("rental.min_age", max(18, min(30, to_int(data.get("min_age"), 21))))
        db.set_setting("rental.young_driver_age", max(18, min(30, to_int(data.get("young_age"), 25))))
        db.set_setting("rental.young_driver_fee", max(0.0, to_float(data.get("young_fee"), 25.0) or 0))
        db.set_setting("rental.max_days", max(1, min(365, to_int(data.get("max_days"), 60))))
        extras = db.get_setting("rental.extras") or {}
        for key, x in extras.items():
            price = to_float(data.get(f"extra_{key}"))
            if price is not None:
                x["flat" if "flat" in x else "per_day"] = max(0.0, price)
        db.set_setting("rental.extras", extras)
        db.set_setting("fleet.auto_feature", data.get("auto_feature") == "on")
        db.set_setting("fleet.daily_post", data.get("daily_post") == "on")
        db.log("console", "settings.saved")
        return back("/settings", "Settings saved")

    # -- activity ----------------------------------------------------------------------------------------
    @app.get("/activity")
    def activity(request: Request):
        events = db.all("SELECT * FROM events ORDER BY id DESC LIMIT 300")
        runs = db.all("SELECT * FROM agent_runs ORDER BY id DESC LIMIT 100")
        return page(request, "ops/activity.html", events=events, runs=runs)

    return app


def store_photo(settings: Settings, raw: bytes, ext: str) -> str:
    """Save an uploaded car photo; returns the URL the website uses for it."""
    name = f"{uuid.uuid4().hex}{ext}"
    if settings.blob_token:
        from vercel.blob.sync import put_blob
        mime = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[ext]
        return put_blob(f"cars/{name}", raw, access="public", content_type=mime).url
    (Path(settings.upload_dir) / name).write_bytes(raw)
    return f"/uploads/{name}"


def connections(s: Settings) -> list[dict]:
    return [
        {"name": "Claude (AI writing & conversations)", "on": s.llm_enabled, "env": "ANTHROPIC_API_KEY",
         "off_note": "Agents use built-in rules and templates."},
        {"name": "Facebook Page + Messenger", "on": s.meta_enabled, "env": "FB_PAGE_ID, FB_PAGE_TOKEN, META_APP_SECRET",
         "off_note": "Comments, DMs and page posts run in the sandbox."},
        {"name": "Instagram professional account", "on": s.instagram_enabled, "env": "IG_USER_ID (+ the Page above)",
         "off_note": "Instagram comments, DMs and hashtag search run in the sandbox."},
        {"name": "Threads", "on": s.threads_enabled, "env": "THREADS_USER_ID, THREADS_ACCESS_TOKEN",
         "off_note": "Threads search and replies run in the sandbox."},
        {"name": "Email (SMTP)", "on": s.email_enabled, "env": "SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM",
         "off_note": "Booking confirmations and reminders aren't emailed."},
        {"name": "Database (Postgres)", "on": bool(s.database_url), "env": "DATABASE_URL (Neon via Vercel Storage)",
         "off_note": "Using SQLite on this server's disk." if s.persistent else
         "Nothing is saved for long — connect a database."},
        {"name": "Photo storage (Vercel Blob)", "on": bool(s.blob_token), "env": "BLOB_READ_WRITE_TOKEN",
         "off_note": "Photos are saved on this server's disk." if not s.serverless else "Uploaded photos won't last."},
        {"name": "Owner alerts", "on": bool(s.owner_email and s.email_enabled), "env": "OWNER_EMAIL",
         "off_note": "Alerts show here in the console only."},
    ]

