"""Queue: every post (scheduled, posted, failed, skipped) with publish-now, cancel and retry controls."""

from __future__ import annotations

import json
import re
import time

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import FormData

from vv88.publishing.base import redact
from vv88.web.core import context, flash, form_data, redirect, render, require_user, safe_next, verify_csrf

router = APIRouter()

STATUSES = ["scheduled", "posted", "failed", "skipped"]
TABS = [*STATUSES, "all"]
LIMIT = 300
_BACK_RE = re.compile(r"^/(queue(\?status=[a-z]+)?|clips/[A-Za-z0-9_-]{1,64})$")


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


@router.get("/queue", dependencies=[Depends(require_user)])
def queue_page(request: Request, status: str = "scheduled"):
    ctx = context(request)
    db = ctx.store.db
    status = (status or "scheduled").strip().lower()
    if status not in TABS:
        flash(request, "Unknown filter, showing scheduled posts.", "warning")
        status = "scheduled"

    counts = {s: 0 for s in STATUSES}
    for r in db.execute("SELECT status, COUNT(*) AS n FROM posts GROUP BY status").fetchall():
        key = "scheduled" if r["status"] == "publishing" else r["status"]  # uploading now: still on the Scheduled tab
        counts[key] = counts.get(key, 0) + r["n"]
    counts["all"] = sum(counts.values())
    now = time.time()
    due = db.execute("SELECT COUNT(*) FROM posts WHERE status='scheduled' AND scheduled_at<=?", (now,)).fetchone()[0]

    sql = ("SELECT p.*, (SELECT views FROM metrics m WHERE m.post_id=p.id ORDER BY fetched_at DESC LIMIT 1) AS views, "
           "json_extract(c.data, '$.hook_text') AS hook, c.id AS clip_exists FROM posts p "
           "LEFT JOIN clips c ON c.id = p.clip_id")
    if status == "scheduled":
        rows = db.execute(sql + " WHERE p.status IN ('scheduled','publishing') "
                          "ORDER BY p.status='scheduled', p.scheduled_at ASC LIMIT ?", (LIMIT,))
    elif status == "all":
        # Upcoming posts first (soonest at the top), then everything else newest first.
        rows = db.execute(sql + " ORDER BY CASE p.status WHEN 'scheduled' THEN 0 ELSE 1 END, "
                          "CASE WHEN p.status='scheduled' THEN p.scheduled_at END ASC, "
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
        posts.append({**dict(r), "caption": str(content.get("caption") or ""),
                      "post_title": str(content.get("title") or ""),
                      "error": redact(r["error"] or "")[:600],
                      "link": url if url.startswith(("https://", "http://")) else "",
                      "when": r["posted_at"] or r["scheduled_at"],
                      "due": r["status"] == "scheduled" and (r["scheduled_at"] or 0) <= now})
    shown_total = counts.get(status, 0)
    return render(request, "queue.html", title="Queue", posts=posts, status=status, tabs=TABS, counts=counts,
                  due=due, backend=ctx.settings.publishing.backend, more=max(0, shown_total - len(posts)))


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
    if post["status"] != "scheduled":
        return redirect(request, f"/queue?status={post['status']}",
                        f"Only scheduled posts can be published now (this one is {post['status']}).", "warning")
    ctx.store.reschedule(pid, time.time())
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
    if post["status"] != "scheduled":
        return redirect(request, back, f"Only scheduled posts can be cancelled (this one is {post['status']}).",
                        "warning")
    if not ctx.store.cancel_post(pid):
        return redirect(request, back, f"Too late: the {post['platform']} post started uploading a moment ago.",
                        "warning")
    return redirect(request, back, f"Cancelled the {post['platform']} post. It won't be published.", "success")


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
