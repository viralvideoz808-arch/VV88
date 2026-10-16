from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse

from vv88.web.core import context, redirect, render, require_user, verify_csrf
from vv88.web.jobs import JOB_KINDS, redact_obj

router = APIRouter()
JobId = Annotated[int, Path(ge=1, le=2**63 - 1)]  # SQLite INTEGER range; anything else: 422, not 500


def _row(job, jobs_manager) -> dict:
    d = dict(job)
    d["label"] = JOB_KINDS.get(d["kind"], (d["kind"],))[0]
    d["params"] = json.loads(d["params"] or "{}")
    d["result"] = redact_obj(json.loads(d["result"])) if d.get("result") else None  # older rows too
    d["log"] = jobs_manager.log_text(job)
    return d


@router.get("/jobs", dependencies=[Depends(require_user)])
def jobs_page(request: Request):
    ctx = context(request)
    rows = [_row(j, ctx.jobs) for j in ctx.store.jobs(limit=100)]
    running = any(r["status"] in ("running", "queued") for r in rows)
    return render(request, "jobs.html", title="Jobs", jobs=rows, running=running)


@router.get("/jobs/{job_id}", dependencies=[Depends(require_user)])
def job_page(request: Request, job_id: JobId):
    ctx = context(request)
    job = ctx.store.job(job_id)
    if job is None:
        raise HTTPException(status_code=404)
    return render(request, "job.html", title=f"Job #{job_id}", job=_row(job, ctx.jobs),
                  result_json=json.dumps(_row(job, ctx.jobs)["result"], indent=2, default=str))


@router.get("/jobs/{job_id}/log.json", dependencies=[Depends(require_user)])
def job_log(request: Request, job_id: JobId):
    ctx = context(request)
    job = ctx.store.job(job_id)
    if job is None:
        raise HTTPException(status_code=404)
    return JSONResponse({"status": job["status"], "log": ctx.jobs.log_text(job)[-60_000:]})


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_user), Depends(verify_csrf)])
def job_cancel(request: Request, job_id: JobId):
    ctx = context(request)
    job = ctx.store.job(job_id)
    if job is None or job["status"] != "queued":
        return redirect(request, f"/jobs/{job_id}", "Only queued jobs can be cancelled.", "warning")
    ctx.store.update_job(job_id, status="cancelled")
    return redirect(request, "/jobs", f"Job #{job_id} cancelled.", "success")
