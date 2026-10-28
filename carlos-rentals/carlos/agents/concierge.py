"""Concierge: answers every customer conversation — website chat, Instagram and Messenger DMs, replies on Threads.

With Claude it can look up the fleet, quote exact prices, file booking requests and test-drive/trade-in inquiries,
and hand off to a person. Without Claude it answers the common questions from rules and points to the right page.
Bookings made in chat are requests: the team confirms them (and collects payment/ID) in the console.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from carlos import compliance
from carlos.agents.base import Agent, Context
from carlos.channels import demo
from carlos.channels.base import ChannelError
from carlos.inventory import (BODY_TYPES, QuoteError, create_booking, create_inquiry, get_vehicle, list_vehicles,
                              money, next_free_dates, quote)
from carlos.llm import LLMUnavailable
from carlos.messaging import add_inbound, get_or_create_conversation, history, queue

REPLY_CHANNEL = {"ig_private_reply": "instagram_dm", "fb_private_reply": "messenger",
                 "ig_comment_reply": "ig_comment_reply", "fb_comment_reply": "fb_comment_reply"}

TOOLS = [
    {"name": "search_inventory", "description": "Search the CAR-LOS fleet. listing is 'rent' or 'sale'.",
     "input_schema": {"type": "object", "properties": {
         "listing": {"type": "string", "enum": ["rent", "sale"]},
         "body": {"type": "string", "enum": list(BODY_TYPES)},
         "max_price": {"type": "number", "description": "max daily rate (rent) or price (sale) in USD"},
         "min_seats": {"type": "integer"}, "query": {"type": "string", "description": "make/model words"}},
         "required": ["listing"]}},
    {"name": "quote_rental", "description": "Exact price and availability for renting one car for given dates. "
     "Dates are YYYY-MM-DD; end_date is the return day.",
     "input_schema": {"type": "object", "properties": {
         "vehicle_slug": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"},
         "extras": {"type": "array", "items": {"type": "string"}}, "driver_age": {"type": "integer"}},
         "required": ["vehicle_slug", "start_date", "end_date"]}},
    {"name": "create_booking_request", "description": "File a rental booking request after the customer confirmed "
     "the car, dates and price, and gave their full name and email. The team confirms it and contacts them.",
     "input_schema": {"type": "object", "properties": {
         "vehicle_slug": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"},
         "name": {"type": "string"}, "email": {"type": "string"}, "phone": {"type": "string"},
         "driver_age": {"type": "integer"}, "extras": {"type": "array", "items": {"type": "string"}}},
         "required": ["vehicle_slug", "start_date", "end_date", "name", "email"]}},
    {"name": "create_inquiry", "description": "Record a test-drive request, financing question, trade-in/sell-my-car "
     "offer request, or purchase interest so the sales team follows up.",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["test_drive", "financing", "trade_in", "sell", "buy", "question"]},
         "vehicle_slug": {"type": "string"}, "name": {"type": "string"}, "email": {"type": "string"},
         "phone": {"type": "string"}, "message": {"type": "string"}},
         "required": ["kind", "name", "message"]}},
    {"name": "business_info", "description": "Hours, address, phone, rental requirements and policies.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "handoff_to_human", "description": "Pass the conversation to a team member: complaints, accidents, "
     "damage, refunds, legal or payment problems, anything you can't answer from the tools, or if they ask for a person.",
     "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
]

SYSTEM = """You are the virtual assistant for {biz}, a US business that rents cars and sells used cars{city}.
Today is {today}. You're chatting with a customer on {channel}.

How to help:
- Be warm, brief and concrete, like a great front-desk person texting. 1-4 short sentences, plain text, no markdown.
- Use the tools for every fact about cars, prices, availability and policies. Never guess or invent them.
- To book: find the car, quote the exact dates, confirm car + dates + total with the customer, get their full name and
  email, then call create_booking_request. Tell them it's a request: the team confirms it and handles payment, ID
  and insurance at pickup. Give them their reference number and the link.
- For buying: share the car's link and offer a test drive (create_inquiry). For selling/trade-ins: ask year, make,
  model and mileage, then create_inquiry kind=trade_in or sell.
- Never ask for card numbers, SSNs or license numbers in chat.
- Hand off to a person (handoff_to_human) for complaints, accidents, damage, refunds, legal or payment problems, or
  when they ask for a human.
- If they say stop or not interested, thank them and stop.
Website: {site}"""


def business_info(ctx: Context) -> dict:
    db, s = ctx.db, ctx.settings
    return {
        "name": s.business_name, "phone": s.phone, "email": s.email, "address": s.address, "city": s.city,
        "hours": db.get_setting("site.hours"), "website": s.site_url,
        "requirements": [
            f"Minimum driver age {db.get_setting('rental.min_age')}; drivers under "
            f"{db.get_setting('rental.young_driver_age')} pay {money(db.get_setting('rental.young_driver_fee'))}/day",
            "Valid driver's license (international visitors: home license + passport)",
            "Credit card or debit card in the driver's name for the refundable security deposit",
            "Proof of insurance, or add our collision damage waiver",
        ],
        "extras": {k: v for k, v in (db.get_setting("rental.extras") or {}).items()},
        "tax_rate": db.get_setting("rental.tax_rate"),
        "booking_note": "Bookings are confirmed by the team; payment and ID checks happen at pickup.",
        "sales": "Used cars for sale with test drives; trade-ins accepted; customers can send a financing request "
                 "online and the team replies with options (no approval is promised).",
    }


def car_summary(ctx: Context, c: dict) -> dict:
    out = {"slug": c["slug"], "car": c["full_title"], "type": c["body"], "seats": c["seats"], "fuel": c["fuel"],
           "url": f"{ctx.settings.site_url}/cars/{c['slug']}", "status": c["status"]}
    if c["listing"] == "rent":
        out.update(daily=c["daily_rate"], weekly=c["weekly_rate"], deposit=c["deposit"])
    else:
        out.update(price=c["sale_price"], mileage=c["mileage"])
    return out


class Toolbox:
    def __init__(self, ctx: Context, conv: dict):
        self.ctx, self.conv = ctx, conv
        self.handoff_reason = ""
        self.actions: list[str] = []

    def __call__(self, name: str, args: dict):
        db = self.ctx.db
        if name == "search_inventory":
            cars = list_vehicles(db, listing=args.get("listing"), body=args.get("body"), q=args.get("query", ""),
                                 max_price=args.get("max_price"), min_seats=args.get("min_seats"))
            return [car_summary(self.ctx, c) for c in cars if c["status"] == "available"][:8]
        if name == "quote_rental":
            car = get_vehicle(db, args["vehicle_slug"])
            if not car:
                return {"error": "no car with that slug"}
            try:
                q = quote(db, car, args["start_date"], args["end_date"], args.get("extras"), args.get("driver_age"))
            except QuoteError as e:
                return {"error": str(e)}
            if not q["available"]:
                alt = next_free_dates(db, car, date.fromisoformat(q["start"]), q["days"])
                q["next_available"] = [d.isoformat() for d in alt] if alt else None
            q["car"] = car["full_title"]
            return q
        if name == "create_booking_request":
            car = get_vehicle(db, args["vehicle_slug"])
            if not car:
                return {"error": "no car with that slug"}
            if "@" not in args.get("email", ""):
                return {"error": "a valid email is required"}
            try:
                b = create_booking(db, car, args["start_date"], args["end_date"], args["name"], args["email"],
                                   args.get("phone", ""), args.get("extras"), args.get("driver_age"),
                                   source=f"chat:{self.conv['channel']}", lead_id=self.conv.get("lead_id"))
            except QuoteError as e:
                return {"error": str(e)}
            db.update("conversations", self.conv["id"], {"email": args["email"], "contact_name": args["name"]})
            self.actions.append(f"booking {b['ref']}")
            notify_owner(self.ctx, f"New booking request {b['ref']}",
                         f"{args['name']} ({args['email']}) requested the {car['title']} "
                         f"{b['start_date']} → {b['end_date']}, total {money(b['total'])}.")
            return {"ref": b["ref"], "total": b["total"], "status": "pending confirmation",
                    "manage_url": f"{self.ctx.settings.site_url}/booking/{b['ref']}"}
        if name == "create_inquiry":
            car = get_vehicle(db, args["vehicle_slug"]) if args.get("vehicle_slug") else None
            iid = create_inquiry(db, args["kind"], args["name"], args.get("email", ""), args.get("phone", ""),
                                 args.get("message", ""), vehicle_id=car["id"] if car else None,
                                 source=f"chat:{self.conv['channel']}", lead_id=self.conv.get("lead_id"))
            self.actions.append(f"inquiry #{iid}")
            return {"inquiry_id": iid, "status": "the sales team will follow up"}
        if name == "business_info":
            return business_info(self.ctx)
        if name == "handoff_to_human":
            self.handoff_reason = args.get("reason", "customer needs a person")
            return {"ok": True, "tell_customer": "A team member will take it from here shortly."}
        raise ValueError(f"unknown tool {name}")


# -- rule-based replies ---------------------------------------------------------------------------
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def find_dates(text: str, today: date) -> tuple[date, date] | None:
    t = text.lower()
    iso = re.findall(r"\b(20\d\d-\d\d-\d\d)\b", t)
    if len(iso) >= 2:
        try:
            return date.fromisoformat(iso[0]), date.fromisoformat(iso[1])
        except ValueError:
            pass
    md = re.findall(r"\b(\d{1,2})/(\d{1,2})\b", t)
    if len(md) >= 2:
        try:
            ds = []
            for m, d in md[:2]:
                x = date(today.year, int(m), int(d))
                ds.append(x if x >= today else date(today.year + 1, int(m), int(d)))
            return ds[0], ds[1]
        except ValueError:
            pass
    days = [w for w in re.findall(r"\b(" + "|".join(WEEKDAYS) + r")\b", t)]
    if len(days) >= 2:
        start = today + timedelta(days=(WEEKDAYS.index(days[0]) - today.weekday()) % 7 or 7)
        end = start + timedelta(days=(WEEKDAYS.index(days[1]) - WEEKDAYS.index(days[0])) % 7 or 7)
        return start, end
    if "this weekend" in t or "the weekend" in t:
        start = today + timedelta(days=(4 - today.weekday()) % 7)
        return start, start + timedelta(days=2)
    return None


def rule_reply(ctx: Context, conv: dict, text: str, earlier: str = "") -> tuple[str, str]:
    """Returns (reply, handoff_reason). `text` is the latest message; `earlier` is what they said before."""
    db, s = ctx.db, ctx.settings
    t = text.lower()
    lead = db.one("SELECT intent, needs FROM leads WHERE id = ?", (conv.get("lead_id"),)) if conv.get("lead_id") else None
    intent = (lead or {}).get("intent", "")
    needs = json.loads((lead or {}).get("needs") or "{}")
    both = f"{earlier} {text}".lower()
    site = s.site_url
    for word in db.get_setting("concierge.handoff_keywords") or []:
        if word in t:
            return ("I'm sorry to hear that — I'm passing this to a team member right now so they can help you "
                    "personally." + (f" You can also call us at {s.phone}." if s.phone else ""), f"mentioned “{word}”")
    if re.search(r"\b(human|real person|agent|someone|call me)\b", t):
        return ("Of course — a team member will reply here shortly." +
                (f" Or call us at {s.phone}." if s.phone else ""), "asked for a person")

    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    if email:
        db.update("conversations", conv["id"], {"email": email.group(0)})
        create_inquiry(db, "question", conv.get("contact_name") or conv.get("contact_handle") or "Chat customer",
                       email.group(0), message=f"From {conv['channel']} chat:\n{earlier}\n{text}"[-3000:],
                       source=f"chat:{conv['channel']}", lead_id=conv.get("lead_id"))
        notify_owner(ctx, "Customer left their email in chat", f"{email.group(0)}: {text[:300]}")
        return (f"Thanks! I've passed this to the team with your email ({email.group(0)}) — they'll confirm the "
                "details shortly.", "")

    info = business_info(ctx)
    if re.search(r"\b(what do i need|requirements?|bring|debit|credit card|deposit|license|licence|age|under 25|"
                 r"insurance)\b", t):
        reqs = "; ".join(info["requirements"][:4])
        return f"Here's what you need to rent: {reqs}. Full details: {site}/faq", ""
    if re.search(r"\b(hours|open|close|address|where are you|location)\b", t):
        where = f" We're at {s.address}." if s.address else ""
        return f"We're open {info['hours']}.{where} Delivery to your address is available too.", ""
    if re.search(r"\b(sell\w*|trade[\s-]?in|offer for my|what('?s| is) my car worth)\b", t) or \
            (intent == "sell" and re.search(r"\b(offer|info|price|worth|how)\b", t)):
        return (f"We buy cars and take trade-ins! Tell me the year, make, model and mileage, or fill in the quick form "
                f"for a no-obligation offer: {site}/sell", "")
    if re.search(r"\b(financ\w*|loans?|credit score|monthly payments?|down payment|negotiable)\b", t):
        return (f"Happy to talk numbers on any of our cars. For financing, send a quick request here and we'll get back "
                f"to you with options: {site}/financing — which car are you looking at?", "")

    wants_sale = intent == "buy" or bool(re.search(r"\b(buy|purchase|for sale|test drive|still available|miles|"
                                                    r"mileage)\b", both))
    listing = "sale" if wants_sale else "rent"
    body = next((b for b in BODY_TYPES if re.search(rf"\b{b}s?\b", both)), None) or needs.get("vehicle")
    body = body or ("truck" if "pickup" in both else "van" if "minivan" in both else None)
    named = [c for c in list_vehicles(db, public=True) if c["model"].lower() in t or c["make"].lower() in t]
    if named:
        listing = named[0]["listing"]
    seats = 6 if needs.get("group") or re.search(r"\b(family of|[6-9] (people|of us))\b", both) else None
    cars = named or list_vehicles(db, listing=listing, body=body, min_seats=seats) \
        or list_vehicles(db, listing=listing, min_seats=seats) or list_vehicles(db, listing=listing)
    cars = [c for c in cars if c["status"] == "available"][:3]

    dates = find_dates(text, date.today()) or find_dates(earlier, date.today())
    if listing == "rent" and cars and dates:
        lines = []
        for c in cars:
            try:
                q = quote(db, c, dates[0], dates[1])
            except QuoteError:
                continue
            if q["available"]:
                lines.append(f"{c['title']} — {money(q['total'])} total for {q['days']} day(s) incl. tax")
        if lines:
            return (f"For {dates[0]:%a %b} {dates[0].day} → {dates[1]:%a %b} {dates[1].day} I have: " + "; ".join(lines) +
                    f". Book here: {site}/cars/{cars[0]['slug']} — or reply with your name and email and a team "
                    f"member will lock it in for you.", "")
    if cars:
        if listing == "rent":
            opts = "; ".join(f"{c['title']} from {money(c['daily_rate'])}/day" for c in cars)
            return f"Here's what's available: {opts}. What dates do you need? {site}/rentals" + \
                   (f"?body={body}" if body else ""), ""
        opts = "; ".join(f"{c['full_title']} — {money(c['sale_price'])}, {c['mileage']:,} mi" for c in cars)
        return f"Yes! {opts}. Want to come see it or schedule a test drive? {site}/for-sale", ""
    if re.search(r"\b(hi|hello|hey|good (morning|afternoon|evening))\b", t):
        return (f"Hi! Thanks for reaching out to {s.business_name}. Are you looking to rent a car, buy one, or sell "
                "yours?", "")
    return (f"Thanks for your message! I can help with rentals, cars for sale and trade-ins. Browse everything at "
            f"{site} — or tell me what you need and when.", "")


def notify_owner(ctx: Context, subject: str, body: str) -> None:
    ctx.db.log("concierge", "notify", f"{subject}: {body}")
    if ctx.settings.email_enabled and ctx.settings.owner_email:
        from carlos.channels.email import send_email
        try:
            send_email(ctx.settings, ctx.settings.owner_email, f"[CAR-LOS] {subject}",
                       f"{body}\n\nOpen the console to respond.")
        except ChannelError:
            pass


def reply_target(ctx: Context, conv: dict) -> tuple[str, str]:
    channel = REPLY_CHANNEL.get(conv["channel"], conv["channel"])
    if conv["channel"] == "threads_reply":
        last = ctx.db.one("SELECT external_id FROM messages WHERE conversation_id = ? AND direction = 'in' "
                          "AND external_id LIKE 'th:%' ORDER BY id DESC LIMIT 1", (conv["id"],))
        if last:
            return channel, last["external_id"][3:]
        lead = ctx.db.one("SELECT needs FROM leads WHERE id = ?", (conv.get("lead_id"),)) or {}
        return channel, json.loads(lead.get("needs") or "{}").get("media_id", conv["external_id"])
    if conv["channel"] == "webchat" or conv["external_id"].startswith("demo"):
        return channel, conv["external_id"]
    if conv["channel"] in ("instagram_dm", "messenger"):
        return channel, conv["external_id"].split(":", 1)[-1]
    if conv["channel"] == "email":
        return channel, conv["email"]
    return channel, conv["external_id"].split(":", 1)[-1]


def compose_reply(ctx: Context, conv: dict) -> tuple[str, str, list[str]]:
    """Returns (reply text, handoff reason, actions taken)."""
    msgs = history(ctx.db, conv["id"], limit=30)
    inbound = [m["body"] for m in msgs if m["direction"] == "in"]
    last_in = inbound[-1] if inbound else ""
    if ctx.claude.available:
        box = Toolbox(ctx, conv)
        convo = []
        for m in msgs:
            role = "user" if m["direction"] == "in" else "assistant"
            if convo and convo[-1]["role"] == role:
                convo[-1]["content"] += "\n" + m["body"]
            else:
                convo.append({"role": role, "content": m["body"]})
        while convo and convo[0]["role"] != "user":
            convo.pop(0)
        if convo and convo[-1]["role"] == "user":
            s = ctx.settings
            system = SYSTEM.format(biz=s.business_name, city=f" in {s.city}" if s.city else "",
                                   today=f"{date.today():%A, %B} {date.today().day}, {date.today().year}",
                                   channel=compliance.CHANNELS.get(conv["channel"], conv["channel"]), site=s.site_url)
            try:
                text = ctx.claude.tool_loop(system, convo, TOOLS, box, effort="low")
                if text:
                    return text, box.handoff_reason, box.actions
            except LLMUnavailable as e:
                ctx.note(f"Claude unavailable ({e}); used rules")
    text, handoff = rule_reply(ctx, conv, last_in, " ".join(inbound[:-1]))
    return text, handoff, []


def handle_conversation(ctx: Context, conv: dict, approval: bool = False) -> str | None:
    """Compose and send (or queue) the reply to one conversation. Returns the text, or None when nothing was sent."""
    db = ctx.db
    if conv["status"] == "opted_out":
        return None
    text, handoff, actions = compose_reply(ctx, conv)
    if not text:
        return None
    first = not db.scalar("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND direction = 'out' "
                          "AND author = 'concierge'", (conv["id"],))
    text = compliance.with_disclosure(text, first and conv["channel"] != "threads_reply")
    channel, target = reply_target(ctx, conv)
    oid = queue(db, kind="reply", channel=channel, target=target, conversation_id=conv["id"], lead_id=conv["lead_id"],
                body=text, agent="concierge", approved=not approval or conv["channel"] == "webchat",
                reason=("; ".join(actions) or "reply") + (f"; handoff: {handoff}" if handoff else ""))
    db.run("UPDATE conversations SET needs_reply = 0 WHERE id = ?", (conv["id"],))
    if handoff:
        db.run("UPDATE conversations SET mode = 'human', needs_reply = 1 WHERE id = ?", (conv["id"],))
        notify_owner(ctx, "Conversation needs you", f"{conv['contact_name'] or conv['contact_handle']} on "
                     f"{conv['channel']}: {handoff}")
    item = db.one("SELECT * FROM outbox WHERE id = ?", (oid,))
    if item["status"] == "approved":
        ctx.dispatcher.send_item(item)
    return text


def webchat_message(ctx: Context, token: str, text: str, name: str = "", email: str = "") -> dict:
    conv = get_or_create_conversation(ctx.db, "webchat", token, name=name or "Website visitor", email=email)
    add_inbound(ctx.db, conv, text)
    conv = ctx.db.one("SELECT * FROM conversations WHERE id = ?", (conv["id"],))
    if conv["status"] == "opted_out":
        return {"reply": "Got it — we won't message you again. Thanks!"}
    if conv["mode"] == "human":
        notify_owner(ctx, "Website chat waiting", f"{conv['contact_name']}: {text[:300]}")
        return {"reply": None}
    return {"reply": handle_conversation(ctx, conv)}


class Concierge(Agent):
    name = "concierge"
    title = "Conversation Concierge"
    description = ("Answers website chat, Instagram/Messenger DMs and Threads replies: prices, availability, "
                   "booking requests, test drives and trade-ins. Hands off to you for anything sensitive.")

    def run(self, ctx: Context) -> str:
        db = ctx.db
        pulled = 0
        if ctx.settings.demo_mode:
            for r in demo.pending_demo_replies(db):
                conv = db.one("SELECT * FROM conversations WHERE id = ?", (r["conversation_id"],))
                add_inbound(db, conv, r["text"], external_id=f"demo-reply:{conv['id']}")
                pulled += 1
        elif ctx.settings.threads_enabled:
            pulled += self.pull_threads_replies(ctx)
        if not db.get_setting("concierge.auto_reply"):
            waiting = db.scalar("SELECT COUNT(*) FROM conversations WHERE needs_reply = 1")
            return f"auto-reply is off; {waiting} conversations waiting for you"
        approval = bool(db.get_setting("concierge.require_approval"))
        convs = db.all("SELECT * FROM conversations WHERE needs_reply = 1 AND mode = 'auto' AND status = 'open' "
                       "AND channel != 'webchat' ORDER BY last_inbound_at LIMIT 20")
        replied = 0
        for conv in convs:
            if handle_conversation(ctx, conv, approval=approval):
                replied += 1
        flushed = ctx.dispatcher.flush()
        out = f"{replied} conversations answered"
        if pulled:
            out = f"{pulled} new replies pulled · " + out
        if flushed:
            out += ", outbox: " + ", ".join(f"{v} {k}" for k, v in flushed.items())
        return out + (" · " + "; ".join(ctx.notes) if ctx.notes else "")

    def pull_threads_replies(self, ctx: Context) -> int:
        """Threads has no DM API: when someone answers our public reply, bring that answer into the inbox."""
        db = ctx.db
        threads = ctx.threads or _threads(ctx.settings)
        n = 0
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat()
        rows = db.all("SELECT * FROM outbox WHERE channel = 'threads_reply' AND status = 'sent' AND external_id != '' "
                      "AND conversation_id IS NOT NULL AND sent_at >= ?", (week_ago,))
        for item in rows:
            try:
                replies = threads.replies_to(item["external_id"])
            except ChannelError as e:
                ctx.note(str(e))
                break
            conv = db.one("SELECT * FROM conversations WHERE id = ?", (item["conversation_id"],))
            for r in replies:
                if r.get("username") and conv["contact_handle"] and r["username"] != conv["contact_handle"]:
                    continue
                if add_inbound(db, conv, r.get("text", ""), external_id=f"th:{r['id']}"):
                    n += 1
        return n


def _threads(settings):
    from carlos.channels.threads import ThreadsClient
    return ThreadsClient(settings)

