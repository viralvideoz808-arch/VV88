"""Queue: every post, split into what needs you (Approve, Share) and what runs by itself.

Approve: posts waiting for your OK (publishing.require_approval); approving keeps or re-slots them.
Share: platforms set to the "share" backend; one tap hands the video to the phone's app (Web Share), then
"Mark posted". Scheduled / posted / failed / skipped: the automatic queue, with publish-now, cancel, retry.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import FormData

from vv88.models import PLATFORMS
from vv88.publishing.base import redact
from vv88.publishing.compose import LIMITS
from vv88.publishing.schedule import approve_post
from vv88.web.core import context, flash, form_data, media_url, redirect, render, require_user, safe_next, verify_csrf

router = APIRouter()

STATUSES = ["scheduled", "posted", "failed", "skipped"]
LANES = ["approve", "share"]
TABS = [*LANES, *STATUSES, "all"]
LIMIT = 300
SHARE_MAX_BYTES = 50 * 1024 * 1024  # Chrome on Android refuses to share bigger files
NAMES = {"tiktok": "TikTok", "youtube": "YouTube", "instagram": "Instagram", "x": "X"}
_BACK_RE = re.compile(r"^/(queue(\?status=[a-z]+)?|clips/[A-Za-z0-9_-]{1,64})$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # tabs and newlines are fine in a caption
_URL_RE = re.compile(r"^https://[^\s<>\"'\\]{3,490}$")
# A pasted post link -> the platform's video id, so view counts can come back when the platform is linked.
_REMOTE_ID = {
    "youtube": re.compile(r"(?:youtube\.com/(?:shorts/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{6,20})"),
    "tiktok": re.compile(r"tiktok\.com/@[^/]+/video/(\d{6,25})"),
    "x": re.compile(r"(?:x|twitter)\.com/[^/]+/status/(\d{6,25})"),
}


def _post_id(raw: str) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 12 else None


def _back(value, default: str) -> str:
    v = str(value or "")
    return safe_next(v) if _BACK_RE.match(v) else default


def _submit_publish(ctx) -> tuple[int, bool]:
    """Reuse a publish job that hasn't started yet (it will pick up this post too); else queue a new one."""
    for row in ctx.store.jobs(limit=50, status="queued"):
        if row["kind"] == "publish_due":
            return int(row["id"]), True
    return ctx.jobs.submit("publish_due", {"source": "queue"}), False


def share_platforms(settings) -> list[str]:
    return [p for p in PLATFORMS if settings.publishing.backend_for(p) == "share"]


def lane_counts(ctx) -> dict[str, int]:
    """Posts per tab: pending → Approve, pending/scheduled on a Share platform → Share, the rest by status."""
    shared = set(share_platforms(ctx.settings))
    counts = {t: 0 for t in TABS}
    for r in ctx.store.status_counts():
        st, n = r["status"], r["n"]
        if st in ("pending", "scheduled") and r["platform"] in shared:
            counts["share"] += n
        elif st == "pending":
            counts["approve"] += n
        elif st in ("scheduled", "publishing"):
            counts["scheduled"] += n  # uploading now: still on the Scheduled tab
        elif st in counts:
            counts[st] += n
        counts["all"] += n
    return counts


def _caption_problem(platform: str, caption: str, title: str | None) -> str:
    limit = LIMITS.get(platform, 2200)
    if len(caption) > limit:
        return f"The {platform} caption is {len(caption)} characters; the limit is {limit}."
    if _CONTROL.search(caption) or (title and _CONTROL.search(title)):
        return "The caption contains control characters. Retype it and try again."
    if title is not None and not 1 <= len(title) <= 100:
        return "The YouTube title must be 1-100 characters."
    return ""


@router.get("/queue", dependencies=[Depends(require_user)])
def queue_page(request: Request, status: str = ""):
    ctx = context(request)
    db = ctx.store.db
    counts = lane_counts(ctx)
    status = (status or "").strip().lower()
    if not status:  # open on whatever needs you
        status = "approve" if counts["approve"] else "share" if counts["share"] else "scheduled"
    elif status not in TABS:
        flash(request, "Unknown filter, showing scheduled posts.", "warning")
        status = "scheduled"
    shared = share_platforms(ctx.settings)
    marks = ",".join("?" * len(shared)) or "''"
    now = time.time()
    due = db.execute(f"SELECT COUNT(*) FROM posts WHERE status='scheduled' AND scheduled_at<=? "
                     f"AND platform NOT IN ({marks})", (now, *shared)).fetchone()[0]

    sql = ("SELECT p.*, (SELECT views FROM metrics m WHERE m.post_id=p.id ORDER BY fetched_at DESC LIMIT 1) AS views, "
           "json_extract(c.data, '$.hook_text') AS hook, json_extract(c.data, '$.path') AS clip_path, "
           "c.id AS clip_exists FROM posts p LEFT JOIN clips c ON c.id = p.clip_id")
    if status == "approve":
        rows = db.execute(sql + f" WHERE p.status='pending' AND p.platform NOT IN ({marks}) "
                          "ORDER BY p.scheduled_at ASC LIMIT ?", (*shared, LIMIT))
    elif status == "share":
        rows = db.execute(sql + f" WHERE p.status IN ('pending','scheduled') AND p.platform IN ({marks}) "
                          "ORDER BY p.scheduled_at ASC LIMIT ?", (*shared, LIMIT))
    elif status == "scheduled":
        rows = db.execute(sql + f" WHERE p.status IN ('scheduled','publishing') AND p.platform NOT IN ({marks}) "
                          "ORDER BY p.status='scheduled', p.scheduled_at ASC LIMIT ?", (*shared, LIMIT))
    elif status == "all":
        # Upcoming posts first (soonest at the top), then everything else newest first.
        rows = db.execute(sql + " ORDER BY CASE WHEN p.status IN ('pending','scheduled') THEN 0 ELSE 1 END, "
                          "CASE WHEN p.status IN ('pending','scheduled') THEN p.scheduled_at END ASC, "
                          "COALESCE(p.posted_at, p.scheduled_at) DESC LIMIT ?", (LIMIT,))
    else:
        rows = db.execute(sql + " WHERE p.status=? ORDER BY COALESCE(p.posted_at, p.scheduled_at) DESC LIMIT ?",
                          (status, LIMIT))
    posts = []
    for r in rows.fetchall():
        try:
            content = json.loads(r["content"] or "{}") or {}
        except ValueError:
            content = {}
        url = r["url"] or ""
        lane = ("share" if r["platform"] in shared and r["status"] in ("pending", "scheduled")
                else "approve" if r["status"] == "pending" else "")
        path = Path(r["clip_path"]) if r["clip_exists"] and r["clip_path"] else None
        size = path.stat().st_size if path is not None and path.is_file() else None
        posts.append({**dict(r), "caption": str(content.get("caption") or ""),
                      "post_title": str(content.get("title") or ""),
                      "error": redact(r["error"] or "")[:600],
                      "link": url if url.startswith(("https://", "http://")) else "",
                      "when": r["posted_at"] or r["scheduled_at"], "lane": lane,
                      "media": media_url(path) if size is not None else "", "size": size or 0,
                      "shareable": size is not None and size <= SHARE_MAX_BYTES,
                      "limit": LIMITS.get(r["platform"], 2200),
                      "due": r["status"] in ("pending", "scheduled") and (r["scheduled_at"] or 0) <= now})
    return render(request, "queue.html", title="Queue", posts=posts, status=status, tabs=TABS, counts=counts,
                  due=due, backend=ctx.settings.publishing.backend, shared=shared,
                  shared_names=", ".join(NAMES.get(p, p) for p in shared),
                  backends={p: ctx.settings.publishing.backend_for(p) for p in PLATFORMS},
                  require_approval=ctx.settings.publishing.require_approval,
                  more=max(0, counts.get(status, 0) - len(posts)))


@router.post("/queue/{post_id}/approve", dependencies=[Depends(require_user), Depends(verify_csrf)])
def approve(request: Request, post_id: str, form: FormData = Depends(form_data)):
    ctx = context(request)
    pid = _post_id(post_id)
    post = ctx.store.post(pid) if pid is not None else None
    if post is None:
        return redirect(request, "/queue?status=approve", "That post doesn't exist any more.", "error")
    if post["status"] != "pending":
        return redirect(request, "/queue?status=approve",
                        f"This {post['platform']} post isn't waiting for approval any more ({post['status']}).", "info")
    raw_caption, raw_title = form.get("caption"), form.get("title")
    caption = raw_caption.replace("\r\n", "\n").strip() if isinstance(raw_caption, str) else None
    title = raw_title.strip() if isinstance(raw_title, str) and post["platform"] == "youtube" else None
    problem = _caption_problem(post["platform"], caption or "", title)
    if problem:
        return redirect(request, f"/queue?status=approve#post-{pid}", "Not approved. " + problem, "error")
    if not approve_post(ctx.store, ctx.settings.publishing, pid, caption=caption, title=title):
        return redirect(request, "/queue?status=approve", "Someone changed this post a moment ago; reload.", "info")
    row = ctx.store.post(pid)
    when = float(row["scheduled_at"]) if row else time.time()
    backend = ctx.settings.publishing.backend_for(post["platform"])
    msg = f"Approved the {post['platform']} post."
    msg += (" Dry-run mode: it is written to data/outbox on the next publish run." if backend == "dry_run"
            else f" It goes out {_when_text(when)}.")
    return redirect(request, "/queue?status=approve", msg, "success")


def _when_text(ts: float) -> str:
    mins = (ts - time.time()) / 60
    if mins <= 5:
        return "within a few minutes"
    if mins < 90:
        return f"in about {mins:.0f} minutes"
    return f"in about {mins / 60:.0f} hours" if mins < 48 * 60 else f"in about {mins / 1440:.0f} days"


@router.post("/queue/approve-all", dependencies=[Depends(require_user), Depends(verify_csrf)])
def approve_all(request: Request):
    ctx = context(request)
    shared = share_platforms(ctx.settings)
    marks = ",".join("?" * len(shared)) or "''"
    ids = [r["id"] for r in ctx.store.db.execute(
        f"SELECT id FROM posts WHERE status='pending' AND platform NOT IN ({marks}) ORDER BY scheduled_at",
        shared).fetchall()]
    done = sum(1 for i in ids if approve_post(ctx.store, ctx.settings.publishing, i))
    if not done:
        return redirect(request, "/queue?status=approve", "Nothing was waiting for approval.", "info")
    return redirect(request, "/queue?status=scheduled",
                    f"Approved {done} post{'s' if done != 1 else ''}. They go out at their posting slots.", "success")


@router.post("/queue/{post_id}/mark-posted", dependencies=[Depends(require_user), Depends(verify_csrf)])
def mark_posted(request: Request, post_id: str, form: FormData = Depends(form_data)):
    ctx = context(request)
    pid = _post_id(post_id)
    post = ctx.store.post(pid) if pid is not None else None
    back = _back(form.get("back"), "/queue?status=share")
    if post is None:
        return redirect(request, back, "That post doesn't exist any more.", "error")
    raw = form.get("url")
    url = raw.strip() if isinstance(raw, str) else ""
    if url and not _URL_RE.match(url):
        return redirect(request, back, "That link doesn't look right: paste the https:// address of the post, or "
                        "leave it empty.", "error")
    m = _REMOTE_ID.get(post["platform"])
    found = m.search(url) if m and url else None
    if not ctx.store.mark_shared(pid, url=url, remote_id=found.group(1) if found else ""):
        return redirect(request, back, f"This {post['platform']} post is already {post['status']}.", "info")
    return redirect(request, back, f"Marked the {post['platform']} post as posted.", "success")


@router.post("/queue/publish-due", dependencies=[Depends(require_user), Depends(verify_csrf)])
def publish_due(request: Request):
    ctx = context(request)
    job_id, reused = _submit_publish(ctx)
    msg = "A publish run was already waiting; it will handle every due post." if reused else \
        "Publishing every post whose slot has arrived."
    return redirect(request, f"/jobs/{job_id}", msg, "info" if reused else "success")


@router.post("/queue/{post_id}/publish-now", dependencies=[Depends(require_user), Depends(verify_csrf)])
def publish_now(request: Request, post_id: str):
    ctx = context(request)
    pid = _post_id(post_id)
    post = ctx.store.post(pid) if pid is not None else None
    if post is None:
        return redirect(request, "/queue", "That post doesn't exist any more.", "error")
    if ctx.settings.publishing.backend_for(post["platform"]) == "share":
        return redirect(request, "/queue?status=share",
                        f"{post['platform'].title()} is set to Share: post it with the Share button.", "info")
    if post["status"] == "pending":
        return redirect(request, "/queue?status=approve", "Approve this post first.", "warning")
    if post["status"] != "scheduled":
        tab = post["status"] if post["status"] in TABS else "scheduled"  # 'publishing' rows live on Scheduled
        return redirect(request, f"/queue?status={tab}",
                        f"Only scheduled posts can be published now (this one is {post['status']}).", "warning")
    if not ctx.store.reschedule(pid, time.time()):  # conditional: never touches a post already uploading
        return redirect(request, "/queue?status=scheduled",
                        f"The {post['platform']} post started uploading a moment ago.", "info")
    job_id, _ = _submit_publish(ctx)
    return redirect(request, f"/jobs/{job_id}", f"Publishing the {post['platform']} post now.", "success")


@router.post("/queue/{post_id}/cancel", dependencies=[Depends(require_user), Depends(verify_csrf)])
def cancel(request: Request, post_id: str, form: FormData = Depends(form_data)):
    ctx = context(request)
    back = _back(form.get("back"), "/queue?status=scheduled")
    pid = _post_id(post_id)
    post = ctx.store.post(pid) if pid is not None else None
    if post is None:
        return redirect(request, back, "That post doesn't exist any more.", "error")
    if post["status"] not in ("pending", "scheduled"):
        return redirect(request, back, f"Only waiting or scheduled posts can be cancelled (this one is "
                        f"{post['status']}).", "warning")
    if not ctx.store.cancel_post(pid):
        return redirect(request, back, f"Too late: the {post['platform']} post started uploading a moment ago.",
                        "warning")
    return redirect(request, back, f"Skipped the {post['platform']} post. It won't be published.", "success")


@router.post("/queue/{post_id}/retry", dependencies=[Depends(require_user), Depends(verify_csrf)])
def retry(request: Request, post_id: str):
    ctx = context(request)
    pid = _post_id(post_id)
    post = ctx.store.post(pid) if pid is not None else None
    if post is None:
        return redirect(request, "/queue?status=failed", "That post doesn't exist any more.", "error")
    if post["status"] != "failed":
        return redirect(request, "/queue?status=failed",
                        f"Only failed posts can be retried (this one is {post['status']}).", "warning")
    ctx.store.db.execute("UPDATE posts SET status='scheduled', scheduled_at=?, error='', posted_at=NULL "
                         "WHERE id=? AND status='failed'", (time.time(), pid))
    ctx.store.db.commit()
    job_id, _ = _submit_publish(ctx)
    return redirect(request, f"/jobs/{job_id}", f"Retrying the {post['platform']} post.", "success")
