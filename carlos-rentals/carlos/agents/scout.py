"""Scout: finds people who are publicly asking for a rental, a car to buy, or someone to buy their car."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from carlos.agents.base import Agent, Context
from carlos.channels import demo
from carlos.channels.base import ChannelError
from carlos.db import now_iso, parse_iso
from carlos.messaging import add_inbound, get_or_create_conversation


def store_lead(db, item: dict) -> int:
    stamp = now_iso()
    return db.insert("leads", {
        "platform": item["platform"], "source_id": item["source_id"], "handle": item.get("handle", "")[:120],
        "name": item.get("name", item.get("handle", ""))[:120], "profile_url": item.get("profile_url", ""),
        "post_url": item.get("post_url", ""), "text": (item.get("text") or "")[:3000],
        "posted_at": item.get("posted_at", ""), "found_by": item.get("found_by", ""),
        "reply_mode": item.get("reply_mode", "manual"),
        "needs": json.dumps({k: item[k] for k in ("comment_id", "media_id", "user_id", "context")
                                           if item.get(k)}),
        "status": "new", "created_at": stamp, "updated_at": stamp,
    }, ignore=True)


class Scout(Agent):
    name = "scout"
    title = "Lead Scout"
    description = ("Searches Threads for public posts asking for rentals or cars, reads comments on our Instagram "
                   "and Facebook posts, and checks Instagram hashtags. New finds go to the Qualifier.")

    def run(self, ctx: Context) -> str:
        db, s = ctx.db, ctx.settings
        max_age = timedelta(days=int(db.get_setting("scout.max_age_days") or 7))
        cutoff = datetime.now(timezone.utc) - max_age
        found: list[dict] = []
        errors: list[str] = []

        if s.demo_mode:
            found += demo.sample_posts(db, limit=5)
            for channel, ext, handle, text in demo.seed_sample_dms(db):
                conv = get_or_create_conversation(db, channel, ext, name=handle, handle=handle)
                add_inbound(db, conv, text, external_id=f"{ext}:1")
                ctx.note(f"sample DM from {handle}")
        else:
            if s.threads_enabled:
                threads = ctx.threads or _threads(s)
                for kw in (db.get_setting("scout.keywords") or [])[:20]:
                    try:
                        found += threads.keyword_search(kw)
                    except ChannelError as e:
                        errors.append(str(e))
                        break
            if s.meta_enabled:
                meta = ctx.meta or _meta(s)
                for fetch in (meta.ig_comments_on_our_posts, meta.fb_comments_on_our_posts):
                    try:
                        found += fetch()
                    except ChannelError as e:
                        errors.append(str(e))
                if s.ig_user_id:
                    # Meta allows 30 unique hashtags per 7 days; keep the list short.
                    for tag in (db.get_setting("scout.hashtags") or [])[:5]:
                        try:
                            found += meta.ig_hashtag_posts(tag)
                        except ChannelError as e:
                            errors.append(str(e))
                            break

        new = 0
        for item in found:
            posted = parse_iso(item.get("posted_at"))
            if posted and posted < cutoff:
                continue
            if not (item.get("text") or "").strip():
                continue
            if store_lead(db, item):
                new += 1
        parts = [f"{len(found)} posts checked, {new} new leads"]
        if ctx.notes:
            parts.append(", ".join(ctx.notes))
        if errors:
            parts.append("errors: " + "; ".join(errors[:3]))
        return " · ".join(parts)


def _threads(settings):
    from carlos.channels.threads import ThreadsClient
    return ThreadsClient(settings)


def _meta(settings):
    from carlos.channels.meta import MetaClient
    return MetaClient(settings)
