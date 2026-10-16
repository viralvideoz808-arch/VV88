"""Clip studio: pick footage (link, path on this computer, or upload), a campaign and a style; start a clip job.

Uploads are streamed in 1 MB chunks into <data_dir>/uploads/<random>.<ext>. The browser's file name is never
used except for its extension, which must be on the video allowlist, and the first bytes must look like a
video container. Rendering always happens in the background job (`clip`), never in the request.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from vv88.editing.styles import STYLES
from vv88.publishing.base import redact
from vv88.web.core import context, flash, read_form, redirect, render, require_user, verify_csrf

router = APIRouter()

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
MAX_UPLOAD_BYTES = 4 * 1024 ** 3
MAX_CLIPS = 10
CHUNK = 1024 * 1024
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_ISO_BOXES = (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot")
_EBML = b"\x1a\x45\xdf\xa3"


class UploadRejected(ValueError):
    pass


# ---- validation helpers -------------------------------------------------------------------
def _s(form, key: str, limit: int = 4096) -> str:
    v = form.get(key)
    return v.strip()[:limit] if isinstance(v, str) else ""


def check_source(raw: str) -> tuple[str, str]:
    """-> (normalised source, error). Links must be http(s); paths must be existing video files."""
    s = raw.strip()
    if not s:
        return "", ""
    if len(s) > 2048 or any(ord(c) < 32 for c in s):
        return "", "That link or path is too long or has invalid characters."
    if _SCHEME.match(s) and not re.match(r"^[A-Za-z]:[\\/]", s):
        try:
            p = urlparse(s)
        except ValueError:
            return "", "That link isn't valid."
        if p.scheme.lower() not in ("http", "https") or not p.hostname or any(c.isspace() for c in s):
            return "", "Links must start with http:// or https://."
        return s, ""
    path = Path(s).expanduser()
    if not path.is_absolute():
        return "", ("Paste a full link (https://…) or the full path to a video on this computer "
                    "(e.g. /home/you/Videos/talk.mp4).")
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return "", f"There's no file at {s[:200]} on this computer."
    if not real.is_file():
        return "", "That path is a folder, not a video file."
    if real.suffix.lower() not in VIDEO_EXTS:
        return "", "Local files must be videos: " + " ".join(VIDEO_EXTS) + "."
    return str(real), ""


def looks_like_video(head: bytes, ext: str) -> bool:
    if ext in (".mkv", ".webm"):
        return head[:4] == _EBML
    return head[4:8] in _ISO_BOXES


def save_upload(src: BinaryIO, ext: str, folder: Path, limit: int = MAX_UPLOAD_BYTES) -> Path:
    """Copy an uploaded file to folder/<random><ext> in chunks; sniff the header; enforce the size cap."""
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = folder / f"{secrets.token_hex(12)}{ext}"
    part = dest.with_name(dest.name + ".part")
    total = 0
    try:
        src.seek(0)
        with open(part, "xb") as out:
            while chunk := src.read(CHUNK):
                if total == 0 and not looks_like_video(chunk[:16], ext):
                    raise UploadRejected("That file doesn't look like a video. Upload an .mp4, .mov, .mkv, "
                                         ".webm or .m4v file.")
                total += len(chunk)
                if total > limit:
                    raise UploadRejected("That upload is over the 4 GB limit. Trim it first, or pass its path "
                                         "on this computer instead.")
                out.write(chunk)
        if total == 0:
            raise UploadRejected("The uploaded file is empty.")
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return dest


def upload_size_guard(request: Request) -> None:
    """Refuse oversized bodies before the form parser spools them to disk."""
    length = request.headers.get("content-length", "")
    if length.isascii() and length.isdigit() and (len(length) > 15 or int(length) > MAX_UPLOAD_BYTES + CHUNK):
        raise HTTPException(status_code=413, detail="That upload is over the 4 GB limit. Trim it first, or pass "
                                                    "its path on this computer instead.")


def _default_n(settings) -> int:
    return max(1, min(MAX_CLIPS, int(settings.analysis.clips_per_source or 5)))


def _pretty(name: str) -> str:
    return name.replace("_", " ").capitalize()


# ---- page -------------------------------------------------------------------------------
def _style_stats(ctx) -> dict[str, tuple[int, float]]:
    try:
        from vv88.optimizer import Optimizer

        report = Optimizer(ctx.store.performance_rows(), ctx.settings.optimizer).report()
        return {name: (n, pct) for name, n, pct in report.get("style", []) if n}
    except Exception:  # noqa: BLE001 - stats are a nice-to-have, never break the page
        return {}


def _recent_clip_jobs(ctx) -> list[dict]:
    uploads = (ctx.settings.data_path / "uploads").resolve()
    out = []
    rows = ctx.store.db.execute("SELECT id, status, params, result, created_at FROM jobs WHERE kind='clip' "
                                "ORDER BY id DESC LIMIT 5").fetchall()
    for r in rows:
        try:
            params = json.loads(r["params"] or "{}")
            result = json.loads(r["result"]) if r["result"] else {}
        except (TypeError, ValueError):
            params, result = {}, {}
        src = str(params.get("source") or "")
        if src and Path(src).parent == uploads:
            label = "uploaded video"
        elif src and not _SCHEME.match(src):
            label = Path(src).name
        else:
            label = re.sub(r"^https?://(www\.)?", "", src)
        detail = ""
        if isinstance(result, dict):
            if r["status"] == "done" and isinstance(result.get("clips"), list):
                n = len(result["clips"])
                detail = f"{n} clip{'s' if n != 1 else ''}"
                if result.get("scheduled"):
                    detail += f" · {result['scheduled']} posts scheduled"
            elif result.get("error"):
                detail = redact(str(result["error"]))[:140]
        out.append({"id": r["id"], "status": r["status"], "label": label or "(no source)",
                    "campaign": params.get("campaign_id") or "", "detail": detail, "created_at": r["created_at"]})
    return out


def _page(request: Request, ctx, values: dict, status_code: int = 200):
    settings, store = ctx.settings, ctx.store
    scores = store.campaign_scores()
    campaigns = sorted(store.campaigns(), key=lambda c: (-(scores.get(c.id) or 0.0), c.title.lower()))
    selected = next((c for c in campaigns if c.id == values.get("campaign_id")), None)
    gameplay = bool(settings.editing.gameplay_dir)
    stats = _style_stats(ctx)
    styles = []
    for name, st in STYLES.items():
        n, pct = stats.get(name, (0, 0.0))
        styles.append({
            "name": name, "label": _pretty(name), "description": st.description,
            "disabled": st.split_screen and not gameplay,
            "hint": "Needs a gameplay folder: set editing → gameplay_dir in Settings." if st.split_screen and not gameplay
                    else ("" if name in settings.editing.styles else "Not in the Auto rotation."),
            "stats": (f"{n} post{'s' if n != 1 else ''} · {pct:+.0f}% vs your average" if n else "not tested yet"),
        })
    from vv88.pipeline import Engine

    enabled = Engine(settings).platforms_for(selected)  # what "post after rendering" will really use
    footage = [c for c in campaigns if c.source_urls][:5] if selected is None else []
    response = render(
        request, "studio.html", title="Clip studio",
        v=values, campaigns=campaigns, scores=scores, selected=selected, footage=footage, styles=styles,
        max_clips=MAX_CLIPS, video_exts=VIDEO_EXTS, accept="video/*," + ",".join(VIDEO_EXTS),
        post_platforms=enabled, backend=settings.publishing.backend,
        jobs=_recent_clip_jobs(ctx), ffmpeg_missing=not shutil.which("ffmpeg"),
    )
    response.status_code = status_code
    return response


@router.get("/studio", dependencies=[Depends(require_user)])
def studio(request: Request, campaign: str = "", source: str = ""):
    ctx = context(request)
    values = {"source": source.strip()[:2048], "campaign_id": campaign[:200], "n": _default_n(ctx.settings),
              "style": "", "i_have_rights": False, "post": False}
    return _page(request, ctx, values)


@router.post("/studio/clip",
             dependencies=[Depends(require_user), Depends(upload_size_guard), Depends(verify_csrf)])
async def start_clip(request: Request):
    form = await read_form(request)
    upload = form.get("upload")
    try:
        # Validation, settings/DB access, the file copy and the job insert all block: keep them off the loop.
        return await run_in_threadpool(_start_clip, request, form, upload if isinstance(upload, UploadFile) else None)
    finally:
        for _, value in form.multi_items():
            if isinstance(value, UploadFile):
                await value.close()


def _start_clip(request: Request, form, upload: UploadFile | None):
    ctx = context(request)
    settings = ctx.settings
    values = {"source": _s(form, "source"), "campaign_id": _s(form, "campaign_id", 200), "n": _s(form, "n", 10),
              "style": _s(form, "style", 60), "i_have_rights": form.get("i_have_rights") == "on",
              "post": form.get("post") == "on"}
    errors: list[str] = []
    has_upload = upload is not None and bool(upload.filename)

    source, err = check_source(values["source"])
    if err:
        errors.append(err)
    ext = ""
    if has_upload:
        ext = Path(upload.filename or "").suffix.lower()
        ctype = (upload.content_type or "").split(";")[0].strip().lower()
        if values["source"]:
            errors.append("Use either a link/path or an upload, not both.")
        if ext not in VIDEO_EXTS:
            errors.append("Uploads must be videos: " + " ".join(VIDEO_EXTS) + ".")
        elif ctype and not (ctype.startswith("video/") or ctype == "application/octet-stream"):
            errors.append("That file doesn't look like a video.")
    elif not values["source"]:
        errors.append("Paste a video link or a path on this computer, or upload a video.")

    campaign = None
    if values["campaign_id"]:
        campaign = ctx.store.campaign(values["campaign_id"])
        if campaign is None or campaign.status != "active":
            errors.append("That campaign doesn't exist or is archived. Pick another one.")
            campaign = None

    try:
        n = int(values["n"]) if values["n"] else _default_n(settings)
        if not 1 <= n <= MAX_CLIPS:
            raise ValueError
    except ValueError:
        errors.append(f"Number of clips must be a whole number from 1 to {MAX_CLIPS}.")
        n = _default_n(settings)
    values["n"] = n

    style = "" if values["style"] in ("", "auto") else values["style"]
    if style and style not in STYLES:
        errors.append("Unknown style. Pick one from the list.")
        style = ""
    elif style and STYLES[style].split_screen and not settings.editing.gameplay_dir:
        errors.append(f"{_pretty(style)} needs a gameplay folder (editing → gameplay_dir in Settings).")
        style = ""
    values["style"] = style

    campaign_footage = bool(campaign and not has_upload and source and source in campaign.source_urls)
    if not values["i_have_rights"] and not campaign_footage and not errors:
        if has_upload:
            errors.append("Uploads need the box “I own this footage or have permission to clip it” ticked.")
        elif campaign:
            errors.append("That link isn't one of the campaign's source videos. Tick “I own this footage or have "
                          "permission to clip it” if you're allowed to use it.")
        else:
            errors.append("Without a campaign, tick “I own this footage or have permission to clip it” "
                          "(only clip content you're allowed to use).")

    if not errors and has_upload:
        try:
            saved = save_upload(upload.file, ext, settings.data_path / "uploads")
            source = str(saved.resolve())
        except UploadRejected as e:
            errors.append(str(e))
        except OSError as e:
            errors.append(f"Couldn't save the upload: {redact(str(e))[:200]}")

    if errors:
        flash(request, " ".join(errors[:4]), "error")
        if has_upload:
            values["upload_note"] = "Choose the file again — browsers don't keep it after an error."
        return _page(request, ctx, values, status_code=400)

    job_id = ctx.jobs.submit("clip", {"source": source, "campaign_id": campaign.id if campaign else "",
                                      "n": n, "style": style, "i_have_rights": values["i_have_rights"],
                                      "post": values["post"]})
    what = f"{n} clip{'s' if n != 1 else ''}"
    return redirect(request, f"/jobs/{job_id}", f"Clipping started: {what} (job #{job_id}).", "success")
