"""Brand: review the brand kit (picture, banners, names, bios), approve its exact fingerprint, then apply it.

Nothing is changed on any account until the owner approves. Approval is tied to the kit's fingerprint
(images + text), so an edit after approving needs a fresh review.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import FormData

from vv88 import brand as b
from vv88.connect import link_status
from vv88.publishing.base import redact
from vv88.web.core import context, form_data, redirect, render, require_user, verify_csrf

router = APIRouter()

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
HEX_RE = re.compile(r"#(?:[0-9A-Fa-f]{3,4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})")
FP_RE = re.compile(r"[0-9a-f]{8,64}")
LABELS = {"tiktok": "TikTok", "instagram": "Instagram", "x": "X", "youtube": "YouTube",
          "youtube_description": "YouTube description"}
CHECKLIST_MAX = 200_000
MAX_ALTERNATES = 12


def _brand_dir(request: Request) -> Path:
    return Path(request.app.state.brand_dir)


def _load(request: Request) -> tuple[b.BrandKit | None, str, str]:
    """(kit, fingerprint, error). error is "missing" when there's no brand.json, else a readable reason."""
    try:
        kit = b.load_kit(_brand_dir(request))
        if not isinstance(kit.data, dict):
            raise ValueError("brand.json must hold a JSON object")
        return kit, kit.fingerprint(), ""
    except FileNotFoundError:
        return None, "", "missing"
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as e:
        return None, "", redact(str(e))[:300] or "brand.json could not be read"


def _text(v) -> str:
    return v if isinstance(v, str) else ""


def _list(v) -> list:
    return v if isinstance(v, list) else []


def _image(root: Path, name) -> dict | None:
    """A kit image as {url, name}, only if it is a real image file inside the kit folder."""
    if not isinstance(name, str) or not name.strip() or "\x00" in name:
        return None
    try:
        base = root.resolve(strict=True)
        p = (root / name).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not p.is_file() or p.suffix.lower() not in IMAGE_EXT or not p.is_relative_to(base):
        return None
    return {"path": str(p), "name": p.name}


def _checklist(root: Path) -> tuple[str, float | None]:
    path = root / "APPLY_CHECKLIST.md"
    try:
        p = path.resolve(strict=True)
        if not p.is_file() or not p.is_relative_to(root.resolve(strict=True)):
            return "", None
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(CHECKLIST_MAX), p.stat().st_mtime
    except (OSError, RuntimeError, ValueError):
        return "", None


def _view(kit: b.BrandKit) -> dict:
    d = kit.data
    names = d.get("display_names") if isinstance(d.get("display_names"), dict) else {}
    bios = d.get("bios") if isinstance(d.get("bios"), dict) else {}
    roles = d.get("palette_roles") if isinstance(d.get("palette_roles"), dict) else {}
    palette = [{"hex": c, "role": _text(roles.get(c))}
               for c in _list(d.get("palette")) if isinstance(c, str) and HEX_RE.fullmatch(c)]
    keywords = [k for k in _list(d.get("youtube_keywords")) if isinstance(k, str) and k.strip()]
    kw_text = " ".join(b._quote_kw(k) for k in keywords)  # exactly what `brand apply` sends to YouTube
    alternates = [img for img in (_image(kit.root, n) for n in _list(d.get("alternates"))[:MAX_ALTERNATES]) if img]
    fallbacks = [h for h in _list(d.get("handle_fallbacks")) if isinstance(h, str) and h]
    return {
        "concept": _text(d.get("concept_name")) or "Brand kit",
        "rationale": _text(d.get("rationale")),
        "handle": _text(d.get("unified_handle")),
        "fallbacks": fallbacks,
        "palette": palette,
        "avatar": _image(kit.root, d.get("avatar") or "avatar_1024.png"),
        "alternates": alternates,
        "youtube_banner": _image(kit.root, d.get("youtube_banner") or "banner_youtube_2560x1440.png"),
        "x_header": _image(kit.root, d.get("x_header") or "header_x_1500x500.png"),
        "names": [{"key": k, "label": LABELS.get(k, k), "text": _text(names.get(k)),
                   "n": len(_text(names.get(k))), "limit": lim, "over": len(_text(names.get(k))) > lim}
                  for k, lim in b.NAME_LIMITS.items()],
        "bios": [{"key": k, "label": LABELS.get(k, k), "text": _text(bios.get(k)),
                  "n": len(_text(bios.get(k))), "limit": lim, "over": len(_text(bios.get(k))) > lim}
                 for k, lim in b.BIO_LIMITS.items()],
        "keywords": keywords,
        "kw_text": kw_text,
        "kw_n": len(kw_text),
        "kw_limit": b.YOUTUBE_KEYWORDS_MAX,
        "link_hint": _text(d.get("link_in_bio_suggestion")),
    }


def _applied(store, fingerprint: str) -> dict | None:
    rec = store.kv_get("brand:applied")
    if not isinstance(rec, dict):
        return None
    result = rec.get("result") if isinstance(rec.get("result"), dict) else {}
    yt = result.get("youtube") if isinstance(result.get("youtube"), dict) else {}
    if yt.get("ok"):
        yt_text, yt_kind = "YouTube description and keywords updated", "ok"
    else:
        yt_text, yt_kind = redact(str(yt.get("error") or yt.get("skipped") or "YouTube not updated"))[:400], "warn"
    return {"at": rec.get("at"), "current": rec.get("fingerprint") == fingerprint, "yt_text": yt_text,
            "yt_kind": yt_kind}


@router.get("/brand", dependencies=[Depends(require_user)])
def brand_page(request: Request):
    kit, fingerprint, error = _load(request)
    brand_dir = _brand_dir(request)
    if kit is None:
        return render(request, "brand.html", title="Brand", kit=None, load_error=error, brand_dir=str(brand_dir))
    ctx = context(request)
    approved = b.approval(kit, ctx.store) if not kit.problems else None
    previous = ctx.store.kv_get("brand:approved")
    checklist, checklist_at = _checklist(kit.root)
    youtube = next((r for r in link_status(ctx.settings, ctx.store) if r.name == "youtube"), None)
    return render(
        request,
        "brand.html",
        title="Brand",
        kit=_view(kit),
        load_error="",
        brand_dir=str(brand_dir),
        fingerprint=fingerprint,
        problems=[redact(p) for p in kit.problems],
        approved=approved,
        stale_approval=bool(previous) and not approved,
        applied=_applied(ctx.store, fingerprint),
        checklist=checklist,
        checklist_at=checklist_at,
        youtube_linked=bool(youtube and youtube.linked),
        youtube_warning=youtube.warning if youtube else "",
    )


@router.post("/brand/approve", dependencies=[Depends(require_user), Depends(verify_csrf)])
def approve(request: Request, form: FormData = Depends(form_data)):
    sent = str(form.get("fingerprint") or "").strip().lower()
    if str(form.get("reviewed") or "") != "yes":
        return redirect(request, "/brand#approve", "Tick “I reviewed the picture, banners and every word above” "
                        "to approve.", "error")
    if not FP_RE.fullmatch(sent):
        return redirect(request, "/brand", "That approval form is out of date. Review the kit below and approve "
                        "again.", "error")
    kit, fingerprint, error = _load(request)
    if kit is None:
        return redirect(request, "/brand", "There is no readable brand kit to approve.", "error")
    if fingerprint != sent:
        return redirect(request, "/brand", "The brand kit changed after you opened this page. Review the new "
                        "version below and approve again.", "error")
    if kit.problems:
        return redirect(request, "/brand", "Fix these in brand.json first: " + "; ".join(kit.problems), "error")
    ctx = context(request)
    try:
        b.approve(kit, ctx.store, note="web")
    except ValueError as e:
        return redirect(request, "/brand", redact(str(e)), "error")
    return redirect(request, "/brand", "Brand kit approved. Press “Apply to profiles” when you're ready.", "success")


@router.post("/brand/apply", dependencies=[Depends(require_user), Depends(verify_csrf)])
def apply(request: Request):
    kit, _fp, _err = _load(request)
    if kit is None:
        return redirect(request, "/brand", "There is no readable brand kit to apply.", "error")
    ctx = context(request)
    if kit.problems or not b.approval(kit, ctx.store):
        return redirect(request, "/brand", "Approve the current version of the brand kit before applying it.",
                        "error")
    for status in ("queued", "running"):
        for row in ctx.store.jobs(limit=50, status=status):
            if row["kind"] == "brand_apply":
                return redirect(request, f"/jobs/{row['id']}", "The brand kit is already being applied.", "info")
    job_id = ctx.jobs.submit("brand_apply", {"dir": str(_brand_dir(request).resolve())})
    return redirect(request, f"/jobs/{job_id}", "Applying the brand kit: writing the checklist and updating YouTube.",
                    "success")
