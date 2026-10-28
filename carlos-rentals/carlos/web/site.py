"""The public CAR-LOS Rentals website."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from carlos.agents import make_context
from carlos.agents.concierge import webchat_message
from carlos.config import Settings
from carlos.db import Database, now_iso
from carlos.inventory import (BODY_TYPES, QuoteError, create_booking, create_inquiry, get_booking, get_vehicle,
                              list_vehicles, quote)
from carlos.web.common import (WEB_DIR, RateLimiter, client_ip, first, html, make_env, read_form, security_headers,
                               to_float, to_int)
from carlos.web.webhooks import handle_meta_event, valid_signature

log = logging.getLogger(__name__)

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
INQUIRY_KINDS = {"test_drive": "Test drive", "buy": "Purchase question", "financing": "Financing request",
                 "sell": "Sell my car", "trade_in": "Trade-in", "contact": "General question"}


def create_site_app(db: Database, settings: Settings) -> FastAPI:
    app = FastAPI(title=f"{settings.business_name} website", docs_url=None, redoc_url=None, openapi_url=None)
    env = make_env()
    app.state.db, app.state.settings = db, settings
    forms_limit = RateLimiter(8, 600)
    chat_limit = RateLimiter(30, 600)
    quote_limit = RateLimiter(120, 600)

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    uploads = Path(settings.upload_dir)
    uploads.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=uploads), name="uploads")

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        return security_headers(response, CSP)

    def page(request: Request, name: str, status: int = 200, **ctx):
        if request.method == "GET" and status == 200:
            db.count_view(request.url.path)
        return html(env, name, status, s=settings, settings=db.all_settings(), path=request.url.path,
                    extras_catalog=db.get_setting("rental.extras") or {}, **ctx)

    def lead_id_from(request: Request) -> int | None:
        code = request.cookies.get("carlos_ref", "")
        if not re.fullmatch(r"[0-9a-f]{6}", code):
            return None
        row = db.one("SELECT id FROM leads WHERE ref_code = ?", (code,))
        return row["id"] if row else None

    # -- pages -------------------------------------------------------------------------------
    @app.get("/")
    def home(request: Request):
        rentals = [v for v in list_vehicles(db, listing="rent") if v["featured"]][:6] or \
            list_vehicles(db, listing="rent", limit=6)
        sales = [v for v in list_vehicles(db, listing="sale") if v["featured"]][:3] or \
            list_vehicles(db, listing="sale", limit=3)
        cheapest = min((v["daily_rate"] for v in list_vehicles(db, listing="rent") if v["daily_rate"]), default=None)
        counts = {b: db.scalar("SELECT COUNT(*) FROM vehicles WHERE body = ? AND status = 'available'", (b,))
                  for b in BODY_TYPES}
        return page(request, "site/home.html", rentals=rentals, sales=sales, cheapest=cheapest, counts=counts,
                    today=date.today(), tomorrow=date.today() + timedelta(days=1),
                    in3=date.today() + timedelta(days=4))

    def browse(request: Request, listing: str):
        qp = request.query_params
        body = qp.get("body") if qp.get("body") in BODY_TYPES else None
        cars = list_vehicles(db, listing=listing, body=body, q=qp.get("q", "")[:60],
                             max_price=to_float(qp.get("max_price")), min_seats=to_int(qp.get("seats")),
                             sort=qp.get("sort", "featured"))
        start, end = qp.get("start", ""), qp.get("end", "")
        quotes, date_error = {}, ""
        if listing == "rent" and start and end:
            for c in cars:
                try:
                    quotes[c["id"]] = quote(db, c, start, end)
                except QuoteError as e:
                    date_error = str(e)
                    quotes = {}
                    break
            if quotes and qp.get("available") == "1":
                cars = [c for c in cars if quotes[c["id"]]["available"]]
        return page(request, "site/browse.html", listing=listing, cars=cars, body=body, qp=qp, quotes=quotes,
                    start=start, end=end, date_error=date_error, body_types=BODY_TYPES)

    @app.get("/rentals")
    def rentals(request: Request):
        return browse(request, "rent")

    @app.get("/for-sale")
    def for_sale(request: Request):
        return browse(request, "sale")

    @app.get("/cars/{slug}")
    def car(request: Request, slug: str):
        v = get_vehicle(db, slug) if not slug.isdigit() else None
        if not v or v["status"] in ("hidden",):
            return page(request, "site/notfound.html", 404)
        db.run("UPDATE vehicles SET views = views + 1 WHERE id = ?", (v["id"],))
        similar = [c for c in list_vehicles(db, listing=v["listing"], body=v["body"]) if c["id"] != v["id"]][:3]
        if len(similar) < 3:
            similar += [c for c in list_vehicles(db, listing=v["listing"])
                        if c["id"] != v["id"] and c not in similar][:3 - len(similar)]
        qp = request.query_params
        return page(request, "site/car.html", v=v, similar=similar, start=qp.get("start", ""), end=qp.get("end", ""),
                    error=qp.get("error", ""), sent=qp.get("sent", ""),
                    today=date.today(), min_age=db.get_setting("rental.min_age"))

    @app.post("/cars/{slug}/book")
    async def book(request: Request, slug: str):
        v = get_vehicle(db, slug)
        if not v:
            return page(request, "site/notfound.html", 404)
        form = await read_form(request)
        back = f"/cars/{v['slug']}"
        if first(form, "website"):  # honeypot
            return RedirectResponse(back, 303)
        if not forms_limit.allow(client_ip(request)):
            return RedirectResponse(f"{back}?error=Too+many+requests,+please+try+again+in+a+few+minutes", 303)
        name, email = first(form, "name", limit=120), first(form, "email", limit=200)
        start, end = first(form, "start"), first(form, "end")
        err = ""
        if not name or not EMAIL_RE.match(email):
            err = "Please enter your name and a valid email."
        elif first(form, "agree") != "on":
            err = "Please accept the rental terms."
        if not err:
            try:
                b = create_booking(db, v, start, end, name, email, first(form, "phone", limit=40),
                                   extras=form.get("extras", []), driver_age=to_int(first(form, "age")),
                                   pickup=first(form, "pickup", limit=200), notes=first(form, "notes"),
                                   source="website", lead_id=lead_id_from(request))
            except QuoteError as e:
                err = str(e)
        if err:
            from urllib.parse import urlencode
            return RedirectResponse(f"{back}?{urlencode({'error': err, 'start': start, 'end': end})}#book", 303)
        from carlos.agents.concierge import notify_owner
        notify_owner(make_context(db, settings), f"New booking request {b['ref']}",
                     f"{name} ({email}) — {v['title']} {b['start_date']} → {b['end_date']}, ${b['total']:.2f}")
        return RedirectResponse(f"/booking/{b['ref']}?new=1", 303)

    @app.get("/api/quote")
    def api_quote(request: Request):
        if not quote_limit.allow(client_ip(request)):
            return JSONResponse({"error": "Too many requests"}, 429)
        qp = request.query_params
        v = get_vehicle(db, qp.get("car", ""))
        if not v:
            return JSONResponse({"error": "Unknown car"}, 404)
        try:
            q = quote(db, v, qp.get("start", ""), qp.get("end", ""), qp.getlist("extras"), to_int(qp.get("age")))
        except QuoteError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse(q)

    @app.get("/booking/{ref}")
    def booking(request: Request, ref: str):
        b = get_booking(db, ref) if re.fullmatch(r"CL-[A-Z0-9]{6}", ref.upper()) else None
        if not b:
            return page(request, "site/notfound.html", 404, what="booking")
        return page(request, "site/booking.html", b=b, new=request.query_params.get("new") == "1",
                    msg=request.query_params.get("msg", ""))

    @app.post("/booking/{ref}/cancel")
    async def booking_cancel(request: Request, ref: str):
        b = get_booking(db, ref) if re.fullmatch(r"CL-[A-Z0-9]{6}", ref.upper()) else None
        if not b:
            return page(request, "site/notfound.html", 404, what="booking")
        form = await read_form(request)
        if first(form, "email").lower() != b["email"].lower():
            msg = "That email doesn't match this booking."
        elif b["status"] not in ("pending", "confirmed"):
            msg = "This booking can't be cancelled online."
        elif date.fromisoformat(b["start_date"]) <= date.today() + timedelta(days=1):
            msg = "Pickup is less than 24 hours away — please call us to cancel."
        else:
            db.run("UPDATE bookings SET status = 'cancelled', updated_at = ?, notes = notes || ' [cancelled online]' "
                   "WHERE id = ?", (now_iso(), b["id"]))
            db.log("website", "booking.cancelled", b["ref"])
            msg = "Your booking is cancelled. Your deposit, if any, will be released."
        from urllib.parse import quote as q
        return RedirectResponse(f"/booking/{b['ref']}?msg={q(msg)}", 303)

    @app.get("/manage")
    def manage(request: Request):
        return page(request, "site/manage.html", error=request.query_params.get("error", ""))

    @app.post("/manage")
    async def manage_post(request: Request):
        form = await read_form(request)
        if not forms_limit.allow(client_ip(request)):
            return RedirectResponse("/manage?error=Too+many+attempts", 303)
        b = get_booking(db, first(form, "ref")) if re.fullmatch(r"CL-[A-Z0-9]{6}", first(form, "ref").upper()) else None
        if not b or b["email"].lower() != first(form, "email").lower():
            return RedirectResponse("/manage?error=We+couldn%27t+find+a+booking+with+that+reference+and+email", 303)
        return RedirectResponse(f"/booking/{b['ref']}", 303)

    def inquiry_page(request: Request, kind: str, template: str):
        qp = request.query_params
        car = get_vehicle(db, qp.get("car", "")) if qp.get("car") else None
        return page(request, template, kind=kind, car=car, sent=qp.get("sent") == "1", error=qp.get("error", ""),
                    sale_cars=list_vehicles(db, listing="sale"))

    @app.get("/sell")
    def sell(request: Request):
        return inquiry_page(request, "sell", "site/sell.html")

    @app.get("/financing")
    def financing(request: Request):
        return inquiry_page(request, "financing", "site/financing.html")

    @app.get("/contact")
    def contact(request: Request):
        return inquiry_page(request, "contact", "site/contact.html")

    @app.post("/inquiry")
    async def inquiry(request: Request):
        form = await read_form(request)
        kind = first(form, "kind")
        back = first(form, "back", "/contact")
        if not back.startswith("/") or back.startswith("//"):
            back = "/contact"
        sep = "&" if "?" in back else "?"
        if kind not in INQUIRY_KINDS:
            return RedirectResponse("/contact", 303)
        if first(form, "website"):
            return RedirectResponse(f"{back}{sep}sent=1", 303)
        if not forms_limit.allow(client_ip(request)):
            return RedirectResponse(f"{back}{sep}error=Too+many+requests,+try+again+soon", 303)
        name, email, phone = first(form, "name", limit=120), first(form, "email", limit=200), first(form, "phone", limit=40)
        if not name or not (EMAIL_RE.match(email) or len(re.sub(r"\D", "", phone)) >= 10):
            return RedirectResponse(f"{back}{sep}error=Please+add+your+name+and+an+email+or+phone+number", 303)
        details = {k: first(form, k, limit=200) for k in ("year", "make", "model", "mileage", "condition", "vin",
                                                          "down_payment", "credit", "employment", "preferred_time",
                                                          "budget") if first(form, k)}
        car = get_vehicle(db, first(form, "car")) if first(form, "car") else None
        iid = create_inquiry(db, kind, name, email, phone, first(form, "message", limit=4000),
                             vehicle_id=car["id"] if car else None, details=details, lead_id=lead_id_from(request))
        from carlos.agents.concierge import notify_owner
        notify_owner(make_context(db, settings), f"New {INQUIRY_KINDS[kind].lower()} (#{iid})",
                     f"{name} {email} {phone}" + (f" — {car['title']}" if car else ""))
        return RedirectResponse(f"{back}{sep}sent=1#form", 303)

    @app.get("/about")
    def about(request: Request):
        return page(request, "site/about.html")

    @app.get("/faq")
    def faq(request: Request):
        return page(request, "site/faq.html")

    @app.get("/terms")
    def terms(request: Request):
        return page(request, "site/terms.html")

    @app.get("/privacy")
    def privacy(request: Request):
        return page(request, "site/privacy.html")

    @app.get("/r/{code}")
    def referral(code: str):
        lead = db.one("SELECT * FROM leads WHERE ref_code = ?", (code,)) if re.fullmatch(r"[0-9a-f]{6}", code) else None
        if not lead:
            return RedirectResponse("/", 302)
        needs = json.loads(lead["needs"] or "{}")
        dest = {"buy": "/for-sale", "sell": "/sell"}.get(lead["intent"], "/rentals")
        if lead["intent"] == "rent" and needs.get("vehicle") in BODY_TYPES:
            dest += f"?body={needs['vehicle']}"
        db.log("website", "referral.click", f"lead #{lead['id']} ({lead['platform']} @{lead['handle']})")
        db.run("UPDATE leads SET status = CASE WHEN status IN ('contacted', 'qualified') THEN 'engaged' ELSE status END, "
               "updated_at = ? WHERE id = ?", (now_iso(), lead["id"]))
        resp = RedirectResponse(dest, 302)
        resp.set_cookie("carlos_ref", code, max_age=30 * 86400, httponly=True, samesite="lax",
                        secure=settings.https_cookies)
        return resp

    # -- website chat ----------------------------------------------------------------------------
    @app.post("/api/chat")
    async def chat(request: Request):
        try:
            data = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError:
            return JSONResponse({"error": "bad request"}, 400)
        token, text = str(data.get("token", "")), str(data.get("message", "")).strip()[:1500]
        if not TOKEN_RE.match(token) or not text:
            return JSONResponse({"error": "bad request"}, 400)
        if not chat_limit.allow(client_ip(request)):
            return JSONResponse({"reply": "You're sending messages quickly — give me a moment and try again."}, 429)
        import asyncio
        result = await asyncio.to_thread(webchat_message, make_context(db, settings), "web:" + token, text)
        return JSONResponse({"reply": result.get("reply"), "messages": chat_messages(token)})

    def chat_messages(token: str, after: int = 0) -> list[dict]:
        conv = db.one("SELECT id FROM conversations WHERE channel = 'webchat' AND external_id = ?", ("web:" + token,))
        if not conv:
            return []
        rows = db.all("SELECT id, direction, author, body, created_at FROM messages WHERE conversation_id = ? AND id > ? "
                      "ORDER BY id LIMIT 100", (conv["id"], after))
        return [{"id": r["id"], "from": "you" if r["direction"] == "in" else
                 ("team" if r["author"] == "staff" else "assistant"), "text": r["body"]} for r in rows]

    @app.get("/api/chat")
    def chat_poll(token: str = "", after: int = 0):
        if not TOKEN_RE.match(token):
            return JSONResponse({"error": "bad request"}, 400)
        return JSONResponse({"messages": chat_messages(token, after)})

    # -- webhooks & machine-readable -------------------------------------------------------------
    @app.get("/webhooks/meta")
    def meta_verify(request: Request):
        qp = request.query_params
        if qp.get("hub.mode") == "subscribe" and settings.meta_verify_token and \
                qp.get("hub.verify_token") == settings.meta_verify_token:
            return PlainTextResponse(qp.get("hub.challenge", ""))
        return PlainTextResponse("forbidden", 403)

    @app.post("/webhooks/meta")
    async def meta_event(request: Request):
        body = await request.body()
        if not valid_signature(settings.meta_app_secret, body, request.headers.get("x-hub-signature-256")):
            return PlainTextResponse("bad signature", 403)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return PlainTextResponse("bad json", 400)
        import asyncio
        await asyncio.to_thread(handle_meta_event, db, settings, payload)
        return PlainTextResponse("ok")

    @app.get("/robots.txt")
    def robots():
        return PlainTextResponse(f"User-agent: *\nDisallow: /booking/\nDisallow: /api/\nDisallow: /r/\n"
                                 f"Sitemap: {settings.site_url}/sitemap.xml\n")

    @app.get("/sitemap.xml")
    def sitemap():
        urls = ["/", "/rentals", "/for-sale", "/sell", "/financing", "/about", "/faq", "/contact"]
        urls += [f"/cars/{v['slug']}" for v in list_vehicles(db)]
        body = "".join(f"<url><loc>{settings.site_url}{u}</loc></url>" for u in urls)
        return Response(f'<?xml version="1.0" encoding="UTF-8"?><urlset '
                        f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
                        media_type="application/xml")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return page(request, "site/notfound.html", 404)

    return app
