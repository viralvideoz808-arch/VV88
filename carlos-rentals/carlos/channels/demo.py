"""Demo sandbox: used while no social account is connected.

The scout reads this sample feed instead of Meta/Threads, sends land in the console's sandbox
instead of going out, and a few sample customers write back so the conversation agent can be
seen working. Everything created here is tagged "demo" in the console.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

from carlos.channels.base import SendResult
from carlos.db import Database, now_iso

_counter = itertools.count(1)

SAMPLE_POSTS = [
    ("threads", "mia.travels", "Flying in next Friday for a wedding, need a rental car for the weekend. Anyone know a "
     "place that isn't $90/day at the airport?", "threads_reply"),
    ("threads", "jaylen_moves", "Moving apartments on the 15th — need a pickup truck for a day. Recommendations?",
     "threads_reply"),
    ("threads", "chef.dani", "Looking to buy a used car under 25k, reliable SUV, kids + dog. Where do I even start",
     "threads_reply"),
    ("threads", "marcus.b", "Thinking about selling my 2019 Civic, anyone know who gives a fair trade-in price?",
     "threads_reply"),
    ("threads", "brianna_k", "Family of 7 coming to visit for Thanksgiving. Need a minivan or big SUV for a week 😩",
     "threads_reply"),
    ("threads", "nflhottakes", "That trade was a robbery, they need to rent a better quarterback lol", "threads_reply"),
    ("threads", "sam.codes", "Is it cheaper to rent a car for a week or just Uber everywhere? Visiting for 6 days",
     "threads_reply"),
    ("threads", "luxe.cars.deals", "🔥 SALE 🔥 2020 Camry only 18k!! DM us, financing for everyone, we ship nationwide",
     "threads_reply"),
    ("instagram", "taylor.shoots", "Anyone renting out a convertible for a photo shoot next Saturday? Need it for "
     "4-5 hours", "ig_private_reply"),
    ("instagram", "gabe_r", "How much to rent the Jeep for 3 days?", "ig_private_reply"),
    ("instagram", "nina.m", "Is the RAV4 still available? Can I come see it Thursday?", "ig_private_reply"),
    ("instagram", "", "#carrental #rentacar Road trip season! Tag someone you'd take with you", "manual"),
    ("facebook", "Rosa Delgado", "Do you guys do rentals for drivers under 25? My son is 22 and needs a car for a "
     "summer job", "fb_private_reply"),
    ("facebook", "Kevin Tran", "Interested in the F-150 for sale. Do you take trade-ins?", "fb_private_reply"),
    ("facebook", "Alicia Moore", "Great service last time, thank you!", "fb_private_reply"),
]

SAMPLE_DMS = [
    ("instagram_dm", "andre.v", "Hey! Do you have any SUVs available this weekend? Need it Saturday to Monday"),
    ("messenger", "Priya Shah", "Hi, what do I need to bring to pick up a rental? Can I pay with debit?"),
]

FOLLOW_UPS = {
    "rent": "Yes please! How much would it be for Friday to Sunday?",
    "buy": "Thanks! Is the price negotiable? And do you help with financing?",
    "sell": "Cool, what info do you need from me to give an offer?",
    "unknown": "Thanks for reaching out — what do you have available?",
}


def sample_posts(db: Database, limit: int = 6) -> list[dict]:
    """Return sample posts the scout hasn't seen yet, newest first."""
    out = []
    now = datetime.now(timezone.utc)
    for i, (platform, handle, text, mode) in enumerate(SAMPLE_POSTS):
        source_id = f"demo:{platform}:{i}"
        if db.one("SELECT 1 FROM leads WHERE platform = ? AND source_id = ?", (platform, source_id)):
            continue
        slug = handle.replace(" ", "").lower() or f"post{i}"
        out.append({
            "platform": platform, "source_id": source_id, "comment_id": f"demo-comment-{i}", "media_id": f"demo-{i}",
            "handle": handle, "text": text, "reply_mode": mode,
            "posted_at": (now - timedelta(minutes=17 * (i + 1))).replace(microsecond=0).isoformat(),
            "post_url": "", "profile_url": f"https://www.{ 'threads.net/@' if platform == 'threads' else platform + '.com/'}{slug}",
            "found_by": "demo feed",
        })
        if len(out) >= limit:
            break
    return out


def seed_sample_dms(db: Database) -> list[tuple[str, str, str]]:
    """Inbound DMs from sample customers, created once."""
    new = []
    for channel, handle, text in SAMPLE_DMS:
        ext = f"demo:{handle}"
        if not db.one("SELECT 1 FROM conversations WHERE channel = ? AND external_id = ?", (channel, ext)):
            new.append((channel, ext, handle, text))
    return new


def sandbox_send(channel: str, target: str, text: str) -> SendResult:
    return SendResult(True, f"sandbox-{channel}-{next(_counter)}-{int(datetime.now().timestamp())}")


def pending_demo_replies(db: Database) -> list[dict]:
    """Sample leads we messaged who haven't answered yet: they answer once, so the concierge has work."""
    rows = db.all(
        "SELECT c.id AS conversation_id, l.intent, l.handle FROM conversations c JOIN leads l ON l.id = c.lead_id "
        "WHERE c.external_id LIKE 'demo:%' AND c.last_outbound_at IS NOT NULL "
        "AND (c.last_inbound_at IS NULL) AND l.found_by = 'demo feed' AND l.status != 'opted_out'")
    return [dict(r, text=FOLLOW_UPS.get(r["intent"], FOLLOW_UPS["unknown"]), at=now_iso()) for r in rows]
