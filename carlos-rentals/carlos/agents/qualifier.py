"""Qualifier: decides what each new lead wants (rent / buy / sell) and how likely they are to become a customer."""

from __future__ import annotations

import json
import re

from carlos.agents.base import Agent, Context
from carlos.db import now_iso
from carlos.llm import LLMUnavailable

INTENTS = ("rent", "buy", "sell", "existing_customer", "not_relevant")

RENT = re.compile(r"\b(rent(al|ing)?|rent a|need a (car|ride|truck|van|suv|minivan)|car for the (weekend|week|day)|"
                  r"pick ?up truck for|convertible for|hire a car|turo|airport car)\b", re.I)
BUY = re.compile(r"\b(buy(ing)?|purchase|in the market for|looking for a (used |new )?(car|suv|truck)|"
                 r"is the .+ (still )?available|financ(e|ing)|test drive|interested in the)\b", re.I)
SELL = re.compile(r"\b(sell(ing)? my|trade[\s-]?in|what('?s| is) my car worth|cash for my car|offer for my)\b", re.I)
ASK = re.compile(r"\?|\b(anyone|recommend|recommendations|where can|who has|need|looking|how much)\b", re.I)
PRICE = re.compile(r"\$\s?(\d[\d,]*)\s?(k)?|\b(\d{2})k\b", re.I)
DEALER_SPAM = re.compile(r"\b(dm us|we ship|financing for everyone|buy here pay here|link in bio|wholesale|"
                         r"🔥 ?sale|dealer)\b", re.I)
THANKS = re.compile(r"\b(thank(s| you)|great service|love you guys|appreciate)\b", re.I)
VEHICLE_WORDS = re.compile(r"\b(car|suv|truck|pickup|van|minivan|jeep|sedan|convertible|tesla|camry|civic|"
                           r"rav4|f-?150|mustang|ride)\b", re.I)
DATES = re.compile(r"\b(today|tomorrow|tonight|this weekend|next (week|weekend|friday|saturday|month)|"
                   r"(mon|tues|wednes|thurs|fri|satur|sun)day|thanksgiving|christmas|the \d{1,2}(st|nd|rd|th))\b", re.I)


def heuristic(text: str, platform: str, locations: list[str]) -> dict:
    t = text or ""
    needs: dict = {}
    if DEALER_SPAM.search(t):
        return {"intent": "not_relevant", "score": 5, "reason": "looks like another dealer's ad", "needs": needs}
    rent, buy, sell = bool(RENT.search(t)), bool(BUY.search(t)), bool(SELL.search(t))
    if sell:
        intent = "sell"
    elif rent:
        intent = "rent"
    elif buy:
        intent = "buy"
    elif THANKS.search(t):
        return {"intent": "existing_customer", "score": 20, "reason": "thank-you / existing customer", "needs": needs}
    else:
        return {"intent": "not_relevant", "score": 8, "reason": "no rental or purchase intent", "needs": needs}

    score = 45
    reasons = [f"{intent} intent"]
    if ASK.search(t):
        score += 15
        reasons.append("asking for help")
    if VEHICLE_WORDS.search(t):
        score += 10
    m = DATES.search(t)
    if m:
        score += 12
        needs["when"] = m.group(0)
        reasons.append(f"has timing ({m.group(0)})")
    p = PRICE.search(t)
    if p:
        raw = (p.group(1) or p.group(3) or "").replace(",", "")
        if raw.isdigit():
            amount = int(raw) * (1000 if (p.group(2) or p.group(3)) else 1)
            needs["budget"] = amount
            score += 5
    for kind in ("suv", "truck", "pickup", "minivan", "van", "convertible", "sedan", "jeep"):
        if re.search(rf"\b{kind}\b", t, re.I):
            needs["vehicle"] = {"pickup": "truck", "minivan": "van", "jeep": "suv"}.get(kind, kind)
            break
    if re.search(r"\b(family of|\d+ (people|of us)|kids)\b", t, re.I):
        needs["group"] = True
    if locations and any(loc.lower() in t.lower() for loc in locations):
        score += 10
        reasons.append("mentions our area")
    if platform in ("instagram", "facebook") and re.search(r"\b(you guys|your|the (jeep|rav4|f-?150|car))\b", t, re.I):
        score += 15
        reasons.append("talking to us directly")
    return {"intent": intent, "score": min(score, 98), "reason": ", ".join(reasons), "needs": needs}


SCHEMA = {
    "type": "object",
    "properties": {
        "leads": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "intent": {"type": "string", "enum": list(INTENTS)},
                    "score": {"type": "integer"},
                    "reason": {"type": "string"},
                    "vehicle": {"type": "string"},
                    "when": {"type": "string"},
                    "budget": {"type": "integer"},
                },
                "required": ["id", "intent", "score", "reason", "vehicle", "when", "budget"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["leads"],
    "additionalProperties": False,
}

SYSTEM = """You qualify social-media leads for CAR-LOS Rentals, a US business that rents cars and sells used cars.
For each post decide the author's intent:
- rent: wants to rent a car/truck/van/SUV soon
- buy: wants to buy a car (or is asking about one of our cars for sale)
- sell: wants to sell or trade in their car
- existing_customer: thanks, praise, or a question about an existing booking
- not_relevant: anything else, including other dealers' ads, jokes, news, or spam
score is 0-100: how likely a friendly, helpful reply from us turns this person into a customer in the next 30 days.
Posts asking for recommendations with dates or budgets score high; vague or old posts score low; ads from other
dealers score under 10. vehicle is the type they want (sedan, suv, truck, van, convertible, or empty); when is any
timing they mention (empty if none); budget is a dollar figure (0 if none). reason is one short sentence."""


class Qualifier(Agent):
    name = "qualifier"
    title = "Lead Qualifier"
    description = "Reads each new lead, works out whether they want to rent, buy or sell, and scores them 0–100."

    def run(self, ctx: Context) -> str:
        db = ctx.db
        leads = db.all("SELECT * FROM leads WHERE status = 'new' ORDER BY id LIMIT 40")
        if not leads:
            return "no new leads"
        locations = db.get_setting("scout.locations") or []
        min_score = int(db.get_setting("outreach.min_score") or 55)
        results = {}
        used = "rules"
        if ctx.claude.available:
            try:
                prompt = "Areas we serve: " + (", ".join(locations) or "not specified") + "\n\n" + "\n".join(
                    json.dumps({"id": l["id"], "platform": l["platform"], "posted_at": l["posted_at"],
                                "text": l["text"][:800]}) for l in leads)
                out = ctx.claude.structured(SYSTEM, prompt, SCHEMA, effort="low")
                for r in out.get("leads", []):
                    needs = {k: r[k] for k in ("vehicle", "when", "budget") if r.get(k)}
                    results[r["id"]] = {"intent": r["intent"], "score": max(0, min(100, r["score"])),
                                        "reason": r["reason"], "needs": needs}
                used = "Claude"
            except LLMUnavailable as e:
                ctx.note(f"Claude unavailable ({e}); used rules")
        counts = {"qualified": 0, "low": 0}
        for lead in leads:
            r = results.get(lead["id"]) or heuristic(lead["text"], lead["platform"], locations)
            prior = json.loads(lead["needs"] or "{}")
            needs = {**prior, **r["needs"]}
            relevant = r["intent"] in ("rent", "buy", "sell")
            status = "qualified" if relevant and r["score"] >= min_score else "low"
            if r["intent"] == "existing_customer":
                status = "review"
            counts[status if status != "review" else "low"] += 1
            db.run("UPDATE leads SET intent = ?, score = ?, reason = ?, needs = ?, status = ?, updated_at = ? "
                   "WHERE id = ?", (r["intent"], r["score"], r["reason"][:300], json.dumps(needs), status, now_iso(),
                                    lead["id"]))
        summary = f"{len(leads)} leads scored with {used}: {counts['qualified']} qualified, {counts['low']} low/other"
        return summary + (" · " + "; ".join(ctx.notes) if ctx.notes else "")
