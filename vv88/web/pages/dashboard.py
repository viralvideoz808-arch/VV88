from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import FormData

from vv88.connect import link_status
from vv88.web.core import context, form_data, redirect, render, require_user, verify_csrf
from vv88.web.jobs import JOB_KINDS

router = APIRouter()

QUICK_ACTIONS = ["run_cycle", "discover", "publish_due", "metrics"]


@router.get("/", dependencies=[Depends(require_user)])
def dashboard(request: Request):
    ctx = context(request)
    store = ctx.store
    counts = store.counts()
    upcoming = [p for p in store.posts_list(status="scheduled", limit=200) if p["scheduled_at"]]
    upcoming.sort(key=lambda p: p["scheduled_at"])
    campaigns = sorted(store.campaigns(), key=lambda c: store.campaign_scores().get(c.id, 0.0), reverse=True)
    scores = store.campaign_scores()
    from vv88.pipeline import Engine

    earnings = Engine(ctx.settings).earnings_estimate()
    jobs = store.jobs(limit=8)
    running = any(j["status"] in ("running", "queued") for j in jobs)
    return render(
        request,
        "dashboard.html",
        title="Dashboard",
        counts=counts,
        upcoming=upcoming[:6],
        campaigns=campaigns[:5],
        scores=scores,
        clips=store.clips_list(limit=4),
        jobs=jobs,
        running=running,
        links=link_status(ctx.settings, store),
        autopilot=ctx.jobs.autopilot(),
        earnings=earnings,
        backend=ctx.settings.publishing.backend,
        quick=[(k, JOB_KINDS[k][0]) for k in QUICK_ACTIONS],
        now=time.time(),
    )


@router.post("/actions/{kind}", dependencies=[Depends(require_user), Depends(verify_csrf)])
def quick_action(request: Request, kind: str):
    if kind not in QUICK_ACTIONS:
        return redirect(request, "/", "Unknown action.", "error")
    ctx = context(request)
    job_id = ctx.jobs.submit(kind, {"source": "dashboard"})
    return redirect(request, f"/jobs/{job_id}", f"Started: {JOB_KINDS[kind][0]}", "success")


@router.post("/autopilot", dependencies=[Depends(require_user), Depends(verify_csrf)])
def autopilot(request: Request, form: FormData = Depends(form_data)):
    ctx = context(request)
    cfg = ctx.jobs.set_autopilot(form.get("enabled") == "on", form.get("interval_min") or 30)  # clamped there
    state = "on" if cfg["enabled"] else "off"
    return redirect(request, "/", f"Autopilot {state} (every {cfg['interval_min']:.0f} min).", "success")
