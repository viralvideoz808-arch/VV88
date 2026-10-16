"""Clips: browse every rendered clip, see where each one was posted, and queue it for more platforms."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import FormData

from vv88.editing.styles import STYLES
from vv88.models import PLATFORMS, RenderedClip
from vv88.publishing.base import redact
from vv88.web.core import context, flash, form_data, redirect, render, require_user, verify_csrf

router = APIRouter()

CLIP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LIST_LIMIT = 120
ADHOC = "adhoc"


def _valid_clip_id(clip_id: str) -> bool:
    return bool(CLIP_ID_RE.match(clip_id or ""))


def _campaign_titles(store) -> dict[str, str]:
    titles = {c.id: c.title for c in store.campaigns(active_only=False)}
    titles.setdefault(ADHOC, "Ad-hoc (no campaign)")
    return titles


def _posts_by_clip(store, clip_ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {cid: [] for cid in clip_ids}
    if not clip_ids:
        return out
    order = {p: i for i, p in enumerate(PLATFORMS)}
    marks = ",".join("?" * len(clip_ids))
    rows = store.db.execute(f"SELECT clip_id, platform, status FROM posts WHERE clip_id IN ({marks})",
                            clip_ids).fetchall()
    for r in rows:
        out.setdefault(r["clip_id"], []).append({"platform": r["platform"], "status": r["status"]})
    for posts in out.values():
        posts.sort(key=lambda p: order.get(p["platform"], 99))
    return out


def _target_platforms(settings, campaign=None) -> list[str]:
    """Enabled platforms, narrowed to the ones the clip's campaign pays for (same rule as the clip job)."""
    from vv88.pipeline import Engine

    return Engine(settings).platforms_for(campaign)


@router.get("/clips", dependencies=[Depends(require_user)])
def clips_page(request: Request, campaign: str = "", style: str = ""):
    ctx = context(request)
    store = ctx.store
    titles = _campaign_titles(store)
    used = store.db.execute("SELECT DISTINCT campaign_id, style FROM clips").fetchall()
    campaign_ids = sorted({r["campaign_id"] for r in used} | set(titles), key=lambda c: titles.get(c, c).lower())
    styles = sorted(set(STYLES) | {r["style"] for r in used})

    campaign = campaign.strip()
    style = style.strip()
    if campaign and campaign not in campaign_ids:
        flash(request, "That campaign filter isn't known, so all campaigns are shown.", "warning")
        campaign = ""
    if style and style not in styles:
        flash(request, "That style filter isn't known, so all styles are shown.", "warning")
        style = ""

    where, args = [], []
    if campaign:
        where.append("campaign_id=?")
        args.append(campaign)
    if style:
        where.append("style=?")
        args.append(style)
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    total = store.db.execute("SELECT COUNT(*) FROM clips" + cond, args).fetchone()[0]
    rows = store.db.execute("SELECT data, created_at FROM clips" + cond + " ORDER BY created_at DESC LIMIT ?",
                            (*args, LIST_LIMIT)).fetchall()

    clips = []
    for r in rows:
        try:
            clip = RenderedClip(**json.loads(r["data"]))
        except (TypeError, ValueError):
            continue
        clips.append({"clip": clip, "created_at": r["created_at"], "exists": Path(clip.path).is_file()})
    posts = _posts_by_clip(store, [c["clip"].id for c in clips])
    return render(request, "clips.html", title="Clips", clips=clips, posts=posts, titles=titles,
                  campaign_ids=campaign_ids, styles=styles, campaign=campaign, style=style, total=total,
                  limit=LIST_LIMIT, filtered=bool(campaign or style))


@router.get("/clips/{clip_id}", dependencies=[Depends(require_user)])
def clip_page(request: Request, clip_id: str):
    ctx = context(request)
    store = ctx.store
    clip = store.clip(clip_id) if _valid_clip_id(clip_id) else None
    if clip is None:
        return redirect(request, "/clips", "That clip doesn't exist (it may have been removed).", "error")
    row = store.db.execute("SELECT created_at FROM clips WHERE id=?", (clip.id,)).fetchone()
    titles = _campaign_titles(store)
    campaign = store.campaign(clip.campaign_id)

    posts = []
    for p in store.db.execute(
            "SELECT p.*, (SELECT views FROM metrics m WHERE m.post_id=p.id ORDER BY fetched_at DESC LIMIT 1) "
            "AS views FROM posts p WHERE p.clip_id=? ORDER BY p.scheduled_at", (clip.id,)).fetchall():
        try:
            content = json.loads(p["content"] or "{}")
        except ValueError:
            content = {}
        posts.append({**dict(p), "caption": (content or {}).get("caption", ""),
                      "post_title": (content or {}).get("title", ""),
                      "error": redact(p["error"] or ""),
                      "link": (p["url"] or "") if (p["url"] or "").startswith(("https://", "http://")) else ""})
    order = {p: i for i, p in enumerate(PLATFORMS)}
    posts.sort(key=lambda p: order.get(p["platform"], 99))
    existing = {p["platform"]: p["status"] for p in posts}

    # What each platform will (or did) show: the post's own caption, else the clip's draft caption.
    captions = [{"platform": p["platform"], "text": p["caption"], "title": p["post_title"], "draft": False}
                for p in posts if p["caption"]]
    covered = {c["platform"] for c in captions}
    captions += [{"platform": k, "text": v, "title": "", "draft": True}
                 for k, v in (clip.captions or {}).items() if v and k not in covered]

    targets = _target_platforms(ctx.settings, campaign)
    options = []
    for p in targets:
        status = existing.get(p)
        options.append({"platform": p, "status": status, "available": status in (None, "skipped")})

    src = store.source(clip.source_id)
    source = None
    if src is not None:
        source = {"title": src.title or src.url, "url": src.url if src.url.startswith(("https://", "http://")) else ""}

    hook = (clip.hook_text or clip.title or "Clip").strip()
    return render(request, "clip.html", title=hook if len(hook) <= 70 else hook[:69] + "…", clip=clip,
                  created_at=row["created_at"] if row else None,
                  campaign=campaign, campaign_title=titles.get(clip.campaign_id, clip.campaign_id),
                  posts=posts, captions=captions, options=options, any_available=any(o["available"] for o in options),
                  exists=Path(clip.path).is_file(), source=source,
                  backend=ctx.settings.publishing.backend, now=time.time())


@router.post("/clips/{clip_id}/schedule", dependencies=[Depends(require_user), Depends(verify_csrf)])
def clip_schedule(request: Request, clip_id: str, form: FormData = Depends(form_data)):
    ctx = context(request)
    store = ctx.store
    clip = store.clip(clip_id) if _valid_clip_id(clip_id) else None
    if clip is None:
        return redirect(request, "/clips", "That clip doesn't exist (it may have been removed).", "error")
    back = f"/clips/{clip.id}"
    if not Path(clip.path).is_file():
        return redirect(request, back, "The video file for this clip is missing, so it can't be scheduled.", "error")

    campaign = store.campaign(clip.campaign_id)
    enabled = _target_platforms(ctx.settings)
    targets = _target_platforms(ctx.settings, campaign)
    picked: list[str] = []
    for raw in form.getlist("platforms"):
        p = str(raw).strip().lower()
        if p not in PLATFORMS:
            return redirect(request, back, "Unknown platform in the form. Reload the page and try again.", "error")
        if p not in enabled:
            return redirect(request, back, f"{p.title()} isn't enabled. Turn it on in Settings first.", "error")
        if p not in targets:
            return redirect(request, back, f"This clip's campaign doesn't pay for {p.title()} posts, so it isn't "
                            "offered there.", "error")
        if p not in picked:
            picked.append(p)
    if not picked:
        return redirect(request, back, "Pick at least one platform to schedule.", "warning")

    existing = {r["platform"]: r["status"] for r in
                store.db.execute("SELECT platform, status FROM posts WHERE clip_id=?", (clip.id,)).fetchall()}
    blocked = [p for p in picked if existing.get(p) not in (None, "skipped")]
    if blocked:
        names = ", ".join(f"{p} ({existing[p]})" for p in blocked)
        return redirect(request, back, f"Already in the queue for {names}. Untick those and try again.", "warning")
    # A cancelled (skipped) post blocks a new one for the same platform: clear it so the job can re-queue.
    for p in picked:
        if existing.get(p) == "skipped":
            store.db.execute("DELETE FROM posts WHERE clip_id=? AND platform=? AND status='skipped'", (clip.id, p))
    store.db.commit()

    job_id = ctx.jobs.submit("schedule_clip", {"clip_id": clip.id, "platforms": picked, "source": "clips"})
    return redirect(request, f"/jobs/{job_id}", f"Scheduling on {', '.join(picked)}.", "success")
