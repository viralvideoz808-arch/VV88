"""Outreach: writes the first, helpful reply to each qualified lead and queues it for approval (or sends it)."""

from __future__ import annotations

import json

from carlos.agents.base import Agent, Context
from carlos.compliance import opted_out, platform_of
from carlos.db import now_iso
from carlos.inventory import list_vehicles, money
from carlos.llm import LLMUnavailable
from carlos.messaging import queue, ref_code

LIMITS = {"threads_reply": 480, "ig_private_reply": 900, "fb_private_reply": 900}

SYSTEM = """You write the first reply from CAR-LOS Rentals (a US car rental and used-car business) to a person who \
posted publicly or commented on our post. Rules:
- Sound like a friendly local business owner, not an ad. 1-3 short sentences. At most one emoji.
- Answer what they actually asked, using only the inventory facts given. Never invent prices, availability or policies.
- Include the link exactly once. No hashtags. No pressure, no "limited time" language.
- If it's a public reply, start with @handle. Stay under {limit} characters.
Return only the message text."""


def best_matches(ctx: Context, intent: str, needs: dict, limit: int = 2) -> list[dict]:
    listing = "sale" if intent == "buy" else "rent"
    cars = list_vehicles(ctx.db, listing=listing, body=needs.get("vehicle") or None, sort="featured")
    if not cars:
        cars = list_vehicles(ctx.db, listing=listing, sort="featured")
    if intent == "buy" and needs.get("budget"):
        within = [c for c in cars if (c["sale_price"] or 0) <= needs["budget"] * 1.05]
        cars = within or cars
    if needs.get("group"):
        cars = sorted(cars, key=lambda c: -(c["seats"] or 0))
    return [c for c in cars if c["status"] == "available"][:limit]


def link_for(ctx: Context, lead: dict) -> str:
    return f"{ctx.settings.site_url}/r/{ref_code(ctx.db, lead['id'])}"


def template_message(ctx: Context, lead: dict, needs: dict, cars: list[dict], link: str) -> str:
    if lead["reply_mode"] == "threads_reply" and lead["handle"]:
        greet = f"@{lead['handle']} "
    else:
        greet = f"Hi {lead['handle'].split(' ')[0]}! " if lead["handle"] else "Hi! "
    city = f" in {ctx.settings.city}" if ctx.settings.city else ""
    biz = ctx.settings.business_name
    if lead["intent"] == "rent":
        if cars:
            car = cars[0]
            offer = f"we have a {car['title']} from {money(car['daily_rate'])}/day{city}"
            if len(cars) > 1:
                offer += f" (or a {cars[1]['title']})"
        else:
            offer = f"we rent cars, SUVs and trucks{city}"
        body = f"{greet}{biz} here — {offer}. You can see prices and book in a minute: {link}"
    elif lead["intent"] == "buy":
        if cars:
            car = cars[0]
            offer = f"we have a {car['full_title']} for {money(car['sale_price'])} ({car['mileage']:,} mi)"
        else:
            offer = "we have quality used cars with clean titles"
        body = f"{greet}{biz} here — {offer}. Photos and details: {link} Happy to set up a test drive."
    else:  # sell
        body = (f"{greet}{biz} buys cars and takes trade-ins{city}. "
                f"Send the year, make and mileage here for a no-obligation offer: {link}")
    return body


class Outreach(Agent):
    name = "outreach"
    title = "Outreach Writer"
    description = ("Drafts a short, useful first reply for each qualified lead — a public reply on Threads, or the "
                   "one private reply Meta allows to a comment on our posts — then queues it for approval.")

    def run(self, ctx: Context) -> str:
        db = ctx.db
        leads = db.all("SELECT * FROM leads WHERE status = 'qualified' AND id NOT IN "
                       "(SELECT lead_id FROM outbox WHERE lead_id IS NOT NULL AND kind = 'outreach') "
                       "ORDER BY score DESC LIMIT 25")
        approval = bool(db.get_setting("outreach.require_approval"))
        drafted = manual = 0
        for lead in leads:
            if opted_out(db, platform_of(lead["reply_mode"]) if lead["reply_mode"] != "manual" else lead["platform"],
                         lead["handle"]):
                db.run("UPDATE leads SET status = 'opted_out' WHERE id = ?", (lead["id"],))
                continue
            needs = json.loads(lead["needs"] or "{}")
            if lead["reply_mode"] == "manual":
                # e.g. hashtag posts: Meta doesn't give us the author, so a person follows up by hand.
                db.run("UPDATE leads SET status = 'manual', updated_at = ? WHERE id = ?", (now_iso(), lead["id"]))
                manual += 1
                continue
            target = needs.get("media_id") if lead["reply_mode"] == "threads_reply" else needs.get("comment_id")
            if not target:
                db.run("UPDATE leads SET status = 'manual', updated_at = ? WHERE id = ?", (now_iso(), lead["id"]))
                manual += 1
                continue
            cars = best_matches(ctx, lead["intent"], needs)
            link = link_for(ctx, lead)
            body = self.write(ctx, lead, needs, cars, link)
            queue(db, kind="outreach", channel=lead["reply_mode"], target=target, lead_id=lead["id"], body=body,
                  agent=self.name, approved=not approval,
                  reason=f"{lead['intent']} lead, score {lead['score']}: {lead['reason']}")
            drafted += 1
        sent = ctx.dispatcher.flush()
        out = f"{drafted} replies drafted" + (" (waiting for your approval)" if approval and drafted else "")
        if manual:
            out += f", {manual} leads to follow up by hand"
        if sent:
            out += ", outbox: " + ", ".join(f"{v} {k}" for k, v in sent.items())
        return out + (" · " + "; ".join(ctx.notes) if ctx.notes else "")

    def write(self, ctx: Context, lead: dict, needs: dict, cars: list[dict], link: str) -> str:
        limit = LIMITS.get(lead["reply_mode"], 480)
        fallback = template_message(ctx, lead, needs, cars, link)[:limit]
        if not ctx.claude.available:
            return fallback
        facts = [{"car": c["full_title"], "type": c["body"], "seats": c["seats"],
                  "price": (f"{money(c['daily_rate'])}/day" if c["listing"] == "rent" else money(c["sale_price"])),
                  "mileage": c["mileage"]} for c in cars]
        prompt = json.dumps({
            "reply_type": "public reply" if lead["reply_mode"] == "threads_reply" else "private message",
            "platform": lead["platform"], "handle": lead["handle"], "their_post": lead["text"][:800],
            "context_of_our_post": needs.get("context", ""), "intent": lead["intent"], "what_they_need": needs,
            "matching_inventory": facts, "link": link, "business_city": ctx.settings.city,
        }, indent=1)
        try:
            text = ctx.claude.text(SYSTEM.format(limit=limit), prompt, effort="low").strip().strip('"')
        except LLMUnavailable as e:
            ctx.note(f"Claude unavailable ({e}); used template")
            return fallback
        if link not in text:
            text = f"{text} {link}"
        return text[:limit] if len(text) <= limit else fallback
