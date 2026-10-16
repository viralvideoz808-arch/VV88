"""Performance: measured views, estimated earnings, what's winning this week, top posts and payout links."""

from __future__ import annotations

import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request

from vv88.editing.styles import STYLES
from vv88.optimizer import DIMENSIONS, Optimizer
from vv88.web.core import context, flash, redirect, render, require_user, verify_csrf

router = APIRouter()

DEFAULT_DAYS = 7
MAX_DAYS = 365
DAY_CHOICES = (1, 3, 7, 14, 30, 90)
PAYOUT_LIMIT = 5000
DIM_TITLES = {"style": "Caption style", "length_bucket": "Clip length", "hook_type": "Hook type"}
_VIEWS = "(SELECT views FROM metrics m WHERE m.post_id = p.id ORDER BY fetched_at DESC LIMIT 1)"


def _link(url) -> str:
    """Only plain http(s) links reach the page (no javascript:, no embedded whitespace)."""
    u = str(url or "").strip()
    if len(u) > 2000 or any(ch.isspace() for ch in u) or not u.lower().startswith(("https://", "http://")):
        return ""
    return u if urlparse(u).netloc else ""


def _days(raw: str) -> tuple[int, str]:
    s = (raw or "").strip()
    if not s:
        return DEFAULT_DAYS, ""
    if s.isascii() and s.isdigit() and len(s) <= 4 and 1 <= int(s) <= MAX_DAYS:
        return int(s), ""
    return DEFAULT_DAYS, f"Days must be a whole number from 1 to {MAX_DAYS}; showing the last {DEFAULT_DAYS} days."


def _label(dim: str, name) -> str:
    if not name or name == "unknown":
        return "Unknown"
    name = str(name)
    if dim == "length_bucket":
        return name.replace("-", "–") + " s"
    return name.replace("_", " ").capitalize()


def _num(x: float) -> str:
    return f"{x:g}"


def _winning(rows, cfg, now: float) -> tuple[list[dict], bool]:
    try:
        report = Optimizer(rows, cfg, now=now).report()
    except (ArithmeticError, ValueError, TypeError):  # e.g. a half-life of 0 in config.yaml
        return [], False
    peak = max((abs(u) for d in DIMENSIONS for _, _, u in report.get(d, [])), default=0.0)
    out = []
    for d in DIMENSIONS:
        items = []
        for name, n, uplift in report.get(d, []):
            style = STYLES.get(name) if d == "style" else None
            items.append({"name": _label(d, name), "hint": style.description if style else "", "n": n,
                          "uplift": uplift, "uplift_txt": f"{uplift:+.1f}%", "neg": uplift < 0,
                          "width": round(abs(uplift) / peak * 100, 1) if peak else 0.0})
        out.append({"dim": d, "title": DIM_TITLES.get(d, d), "entries": items})
    return out, any(w["entries"] for w in out)


@router.get("/performance", dependencies=[Depends(require_user)])
def performance(request: Request, days: str = ""):
    ctx = context(request)
    db = ctx.store.db
    days_n, warning = _days(days)
    if warning:
        flash(request, warning, "warning")
    now = time.time()
    rows = ctx.store.performance_rows()
    campaigns = {c.id: c for c in ctx.store.campaigns(active_only=False)}

    from vv88.pipeline import Engine

    earnings = Engine(ctx.settings).earnings_estimate()
    measured = [r for r in rows if r["views"] is not None]
    total_views = sum(int(r["views"]) for r in measured)
    last_pull = db.execute("SELECT MAX(fetched_at) FROM metrics").fetchone()[0]

    # what's winning (the same optimizer the clip studio uses to pick styles / lengths / hooks)
    cfg = ctx.settings.optimizer
    min_age = cfg.metric_age_hours * 3600
    usable = sum(1 for r in measured if r["posted_at"] and now - r["posted_at"] >= min_age)
    winning, has_winning = _winning(rows, cfg, now)

    # views by platform
    plat: dict[str, dict] = {}
    for r in rows:
        e = plat.setdefault(r["platform"], {"platform": r["platform"], "posts": 0, "measured": 0, "views": 0})
        e["posts"] += 1
        if r["views"] is not None:
            e["measured"] += 1
            e["views"] += int(r["views"])
    platforms = sorted(plat.values(), key=lambda e: (-e["views"], e["platform"]))
    top_platform_views = max((e["views"] for e in platforms), default=0)
    for e in platforms:
        e["avg"] = e["views"] // e["measured"] if e["measured"] else None
        e["share"] = round(e["views"] / total_views * 100, 1) if total_views else 0.0
        e["width"] = round(e["views"] / top_platform_views * 100, 1) if top_platform_views else 0.0

    # earnings by campaign (views x the campaign's $/1K, as Engine.earnings_estimate computes it)
    camp: dict[str, dict] = {}
    for r in rows:
        cid = r["campaign_id"] or ""
        c = campaigns.get(cid)
        e = camp.setdefault(cid, {"id": cid, "title": c.title if c else (cid or "Unknown campaign"),
                                  "rate": r["reward_per_1k"], "posts": 0, "views": 0,
                                  "usd": earnings["by_campaign"].get(cid, 0.0)})
        e["posts"] += 1
        e["views"] += int(r["views"] or 0)
    by_campaign = sorted(camp.values(), key=lambda e: (-e["usd"], -e["views"], e["title"]))

    # top 10 posts by views
    top = []
    for r in db.execute(
        f"SELECT * FROM (SELECT p.id, p.platform, p.url, p.posted_at, p.clip_id, c.id AS clip_exists, "
        f"json_extract(c.data, '$.hook_text') AS hook, {_VIEWS} AS views FROM posts p "
        f"LEFT JOIN clips c ON c.id = p.clip_id WHERE p.status = 'posted') "
        f"WHERE views IS NOT NULL ORDER BY views DESC, posted_at DESC LIMIT 10"
    ).fetchall():
        link = _link(r["url"])
        top.append({**dict(r), "link": link, "short": link.split("://", 1)[-1] if link else ""})

    # payout links: posted URLs per campaign for the last N days
    groups: dict[str, dict] = {}
    for r in db.execute(
        f"SELECT p.id, p.platform, p.url, p.posted_at, c.campaign_id, {_VIEWS} AS views FROM posts p "
        f"LEFT JOIN clips c ON c.id = p.clip_id WHERE p.status = 'posted' AND p.posted_at >= ? "
        f"ORDER BY c.campaign_id, p.posted_at DESC LIMIT ?", (now - days_n * 86400, PAYOUT_LIMIT)
    ).fetchall():
        cid = r["campaign_id"] or ""
        c = campaigns.get(cid)
        g = groups.setdefault(cid, {"title": c.title if c else (cid or "Unknown campaign"),
                                    "campaign_url": _link(c.campaign_url) if c else "",
                                    "links": [], "missing": 0, "views": 0})
        link = _link(r["url"])
        if not link:
            g["missing"] += 1
            continue
        g["links"].append({"platform": r["platform"], "url": link, "views": r["views"], "posted_at": r["posted_at"]})
        g["views"] += int(r["views"] or 0)
    payouts = sorted(groups.values(), key=lambda g: (-len(g["links"]), g["title"]))
    for g in payouts:
        g["text"] = "\n".join(x["url"] for x in g["links"])

    return render(
        request,
        "performance.html",
        title="Performance",
        total_views=total_views,
        measured=len(measured),
        posted=len(rows),
        earnings=earnings,
        last_pull=last_pull,
        winning=winning,
        has_winning=has_winning,
        usable=usable,
        half_life=_num(cfg.half_life_days),
        min_age_h=_num(cfg.metric_age_hours),
        platforms=platforms,
        by_campaign=by_campaign,
        top=top,
        payouts=payouts,
        payout_links=sum(len(g["links"]) for g in payouts),
        days=days_n,
        day_choices=sorted({*DAY_CHOICES, days_n}),
    )


@router.post("/performance/pull-metrics", dependencies=[Depends(require_user), Depends(verify_csrf)])
def pull_metrics(request: Request):
    ctx = context(request)
    for row in ctx.store.jobs(limit=50, status="queued"):
        if row["kind"] == "metrics":
            return redirect(request, f"/jobs/{row['id']}", "A view-count pull is already waiting to run.", "info")
    job_id = ctx.jobs.submit("metrics", {"source": "performance"})
    return redirect(request, f"/jobs/{job_id}", "Pulling the latest view counts for recent posts.", "success")
