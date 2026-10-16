"""Settings: edit the everyday part of config.yaml, plus a system check and a render benchmark.

The form only covers non-secret settings. Saving reads the current YAML, deep-merges the edited
fields into it, validates the result exactly the way `load_settings` would, and only then replaces
the file atomically (the previous version is kept as `<name>.bak`). Secrets stay in .env
(Connections page) and are never written here.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from starlette.datastructures import FormData

from vv88.config import Settings, deep_merge
from vv88.editing.styles import STYLES
from vv88.models import PLATFORMS
from vv88.publishing.base import redact
from vv88.web.core import context, flash, form_data, redirect, render, require_user, verify_csrf

router = APIRouter()

BACKENDS = ("dry_run", "postiz", "direct")
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")
REFRAMES = ("auto", "face", "center", "blur")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
PLATFORM_LABELS = {"tiktok": "TikTok", "youtube": "YouTube", "instagram": "Instagram", "x": "X"}

SLOT_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,99}$")
FONT_RE = re.compile(r"^[\w][\w .,'&+-]{0,99}$")
LANG_RE = re.compile(r"^[a-z]{2}$")
REGION_RE = re.compile(r"^[A-Z]{2}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

CHECKBOXES = ["disclose_paid_partnership", "llm_enabled", "whop_enabled", *(f"{p}_enabled" for p in PLATFORMS)]
_write_lock = threading.Lock()


class ConfigFileError(ValueError):
    """config.yaml can't be read or written safely (message is shown to the user)."""


def config_path(request: Request) -> Path:
    raw = getattr(request.app.state.jobs, "config_path", None) or os.environ.get("VV88_CONFIG") or "config.yaml"
    return Path(raw).expanduser()


# ---- settings <-> flat form values -------------------------------------------------------
def _num(x) -> str:
    x = float(x)
    return str(int(x)) if x.is_integer() else f"{x:g}"


def values_from_settings(s: Settings) -> dict:
    pub, an, ed = s.publishing, s.analysis, s.editing
    v: dict = {
        "backend": pub.backend,
        "slots_utc": ", ".join(pub.slots_utc),
        "disclose_paid_partnership": pub.disclose_paid_partnership,
        "disclosure_tag": pub.disclosure_tag,
        "min_clip_seconds": _num(an.min_clip_seconds),
        "max_clip_seconds": _num(an.max_clip_seconds),
        "target_clip_seconds": _num(an.target_clip_seconds),
        "clips_per_source": str(an.clips_per_source),
        "whisper_model": an.whisper_model,
        "language": an.language or "",
        "styles": [st for st in ed.styles if st in STYLES],
        "force_style": ed.force_style or "",
        "font": ed.font,
        "gameplay_dir": ed.gameplay_dir or "",
        "reframe": ed.reframe,
        "llm_enabled": s.llm.enabled,
        "llm_model": s.llm.model,
        "llm_effort": s.llm.effort,
        "half_life_days": _num(s.optimizer.half_life_days),
        "campaigns_per_cycle": str(s.ranking.campaigns_per_cycle),
        "prior_views_per_clip": _num(s.ranking.prior_views_per_clip),
        "whop_enabled": s.discovery.whop_apify.enabled,
        "min_budget_remaining_usd": _num(s.discovery.whop_apify.min_budget_remaining_usd),
        "region_code": s.niche.region_code,
    }
    for p in PLATFORMS:
        pc = pub.platforms.get(p)
        v[f"{p}_enabled"] = bool(pc and pc.enabled)
        v[f"{p}_daily_cap"] = str(pc.daily_cap) if pc else "6"
        v[f"{p}_min_gap_minutes"] = str(pc.min_gap_minutes) if pc else "90"
        v[f"{p}_max_hashtags"] = str(pc.max_hashtags) if pc else "10"
    return v


def values_from_form(form) -> dict:
    v: dict = {}
    for key in values_from_settings(Settings()):
        if key in CHECKBOXES:
            v[key] = form.get(key) in ("on", "true", "1", "yes")
        elif key == "styles":
            v[key] = [x for x in form.getlist("styles") if isinstance(x, str)][:20]
        else:
            raw = form.get(key)
            v[key] = raw.strip()[:600] if isinstance(raw, str) else ""
    return v


def _int(v: dict, key: str, label: str, lo: int, hi: int, errors: list[str]) -> int | None:
    raw = v.get(key, "")
    try:
        n = float(raw)
    except ValueError:
        errors.append(f"{label} must be a whole number between {lo} and {hi}.")
        return None
    if not math.isfinite(n) or not n.is_integer() or not lo <= n <= hi:
        errors.append(f"{label} must be a whole number between {lo} and {hi}.")
        return None
    return int(n)


def _float(v: dict, key: str, label: str, lo: float, hi: float, errors: list[str]) -> float | None:
    raw = v.get(key, "").replace(",", "").replace("$", "")
    try:
        n = float(raw)
    except ValueError:
        errors.append(f"{label} must be a number between {_num(lo)} and {_num(hi)}.")
        return None
    if not math.isfinite(n) or not lo <= n <= hi:
        errors.append(f"{label} must be a number between {_num(lo)} and {_num(hi)}.")
        return None
    return int(n) if n.is_integer() else n


def _choice(v: dict, key: str, label: str, allowed, errors: list[str]) -> str | None:
    val = v.get(key, "")
    if val not in allowed:
        errors.append(f"{label}: pick one of {', '.join(allowed)}.")
        return None
    return val


def parse_values(v: dict) -> tuple[dict, list[str]]:
    """Flat form values -> (nested config overrides, errors). Nothing secret is ever produced."""
    errors: list[str] = []
    platforms: dict = {}
    for p in PLATFORMS:
        name = PLATFORM_LABELS.get(p, p)
        platforms[p] = {
            "enabled": bool(v.get(f"{p}_enabled")),
            "daily_cap": _int(v, f"{p}_daily_cap", f"{name} posts per day", 1, 50, errors),
            "min_gap_minutes": _int(v, f"{p}_min_gap_minutes", f"{name} minutes between posts", 0, 1440, errors),
            "max_hashtags": _int(v, f"{p}_max_hashtags", f"{name} max hashtags", 0, 30, errors),
        }

    slots: list[str] = []
    for part in re.split(r"[\s,]+", v.get("slots_utc", "")):
        if not part:
            continue
        m = SLOT_RE.match(part)
        if not m:
            errors.append(f"Posting slots must be times like 06:00 or 21:30 (UTC); {part[:12]!r} isn't.")
            break
        slot = f"{int(m.group(1)):02d}:{m.group(2)}"
        if slot not in slots:
            slots.append(slot)
    else:
        if not slots:
            errors.append("Add at least one posting slot (e.g. 12:00).")
        elif len(slots) > 48:
            errors.append("At most 48 posting slots a day.")
    slots.sort()

    disclose = bool(v.get("disclose_paid_partnership"))
    tag = v.get("disclosure_tag", "")
    if len(tag) > 40 or _CONTROL.search(tag) or "$" in tag:
        errors.append("Disclosure tag should be short text like #ad (40 characters max).")
    elif disclose and not tag:
        errors.append("Disclosure tag can't be empty while paid-partnership disclosure is on (use #ad).")

    lo = _float(v, "min_clip_seconds", "Shortest clip (seconds)", 5, 180, errors)
    hi = _float(v, "max_clip_seconds", "Longest clip (seconds)", 5, 180, errors)
    target = _float(v, "target_clip_seconds", "Target clip length (seconds)", 5, 180, errors)
    if None not in (lo, hi, target) and not lo <= target <= hi:
        errors.append("Clip lengths must satisfy shortest ≤ target ≤ longest.")
    clips_per_source = _int(v, "clips_per_source", "Clips per source", 1, 20, errors)
    whisper = _choice(v, "whisper_model", "Transcription model", WHISPER_MODELS, errors)
    language = v.get("language", "").lower()
    if language and not LANG_RE.match(language):
        errors.append("Language must be blank (auto-detect) or a 2-letter code like en or es.")

    styles = [st for st in STYLES if st in v.get("styles", [])]
    if any(st not in STYLES for st in v.get("styles", [])):
        errors.append("Unknown caption style.")
    elif not styles:
        errors.append("Pick at least one caption style.")
    force = v.get("force_style", "")
    if force and force not in STYLES:
        errors.append("Unknown style to force.")
        force = ""
    font = v.get("font", "")
    if not font or not FONT_RE.match(font):
        errors.append("Caption font must be a font name like 'Inter Black' (letters, digits, spaces).")
    gameplay = v.get("gameplay_dir", "")
    if gameplay:
        if _CONTROL.search(gameplay) or "$" in gameplay or len(gameplay) > 500:
            errors.append("Gameplay folder must be a plain folder path on this server.")
        elif not Path(gameplay).expanduser().is_dir():
            errors.append("Gameplay folder doesn't exist on this server (create it and add your loops first).")
    elif force and STYLES[force].split_screen:
        errors.append(f"Forcing {force} needs a gameplay folder.")
    reframe = _choice(v, "reframe", "Reframing", REFRAMES, errors)

    model = v.get("llm_model", "")
    if not MODEL_RE.match(model):
        errors.append("Claude model must be a model id like claude-opus-5-5.")
    effort = _choice(v, "llm_effort", "Claude effort", EFFORTS, errors)
    half_life = _float(v, "half_life_days", "Optimizer half-life (days)", 1, 60, errors)
    per_cycle = _int(v, "campaigns_per_cycle", "Campaigns per cycle", 1, 10, errors)
    prior = _float(v, "prior_views_per_clip", "Starting views guess", 1, 10_000_000, errors)
    min_budget = _float(v, "min_budget_remaining_usd", "Minimum budget left ($)", 0, 10_000_000, errors)
    region = v.get("region_code", "").upper()
    if not REGION_RE.match(region):
        errors.append("Region must be a 2-letter country code like US or GB.")
    backend = _choice(v, "backend", "Publishing backend", BACKENDS, errors)

    edited = {
        "publishing": {"backend": backend, "slots_utc": slots, "disclose_paid_partnership": disclose,
                       "disclosure_tag": tag, "platforms": platforms},
        "analysis": {"min_clip_seconds": lo, "max_clip_seconds": hi, "target_clip_seconds": target,
                     "clips_per_source": clips_per_source, "whisper_model": whisper, "language": language or None},
        "editing": {"styles": styles, "force_style": force or None, "font": font, "gameplay_dir": gameplay or None,
                    "reframe": reframe},
        "llm": {"enabled": bool(v.get("llm_enabled")), "model": model, "effort": effort},
        "optimizer": {"half_life_days": half_life},
        "ranking": {"campaigns_per_cycle": per_cycle, "prior_views_per_clip": prior},
        "discovery": {"whop_apify": {"enabled": bool(v.get("whop_enabled")), "min_budget_remaining_usd": min_budget}},
        "niche": {"region_code": region},
    }
    return edited, errors


# ---- config.yaml read / validate / write ------------------------------------------------------
def read_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigFileError(f"Couldn't read {path.name}: {redact(str(e))[:160]}") from None
    except yaml.YAMLError:
        raise ConfigFileError(f"{path.name} isn't valid YAML, so it wasn't changed. Fix it by hand and retry.") from None
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        raise ConfigFileError(f"{path.name} should be a mapping of settings; it wasn't changed.")
    return doc


def validate(merged: dict) -> Settings:
    try:
        return Settings.model_validate(deep_merge(Settings().model_dump(), merged))
    except ValidationError as e:
        parts = []
        for err in e.errors(include_url=False, include_input=False, include_context=False)[:3]:
            loc = ".".join(str(x) for x in err.get("loc", ()))
            parts.append(f"{loc}: {err.get('msg', 'invalid')}")
        raise ConfigFileError("Settings not saved — " + "; ".join(parts)) from None


def write_config(path: Path, doc: dict) -> None:
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=1000)
    directory = path.parent if str(path.parent) else Path(".")
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(directory))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ---- system check (subprocess / import results cached for 60 s) ---------------------------
_CACHE_S = 60.0
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, fn):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_S:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _ffmpeg_info() -> dict:
    ff = shutil.which("ffmpeg")
    info = {"ffmpeg": ff or "", "ffprobe": shutil.which("ffprobe") or "", "version": "", "filters": set()}
    if ff:
        first = _run([ff, "-hide_banner", "-version"]).splitlines()
        info["version"] = first[0].split(" Copyright")[0][:80] if first else ""
        names = set()
        for line in _run([ff, "-hide_banner", "-filters"]).splitlines():
            parts = line.split()
            if len(parts) >= 3 and "->" in parts[2]:
                names.add(parts[1])
        info["filters"] = names
    return info


def _font_families() -> set[str] | None:
    if not shutil.which("fc-list"):
        return None
    fams = set()
    for line in _run(["fc-list", ":", "family"]).splitlines():
        fams.update(f.strip().lower() for f in line.split(",") if f.strip())
    return fams


def _importable(module: str) -> tuple[bool, str]:
    try:
        importlib.import_module(module)
        return True, ""
    except Exception as e:  # noqa: BLE001 - any failure means "not usable"
        return False, redact(f"{type(e).__name__}: {e}")[:120]


def system_checks(settings: Settings) -> list[dict]:
    rows: list[dict] = []

    def add(label: str, ok: bool, detail: str = "", hint: str = "", optional: bool = False) -> None:
        rows.append({"label": label, "ok": ok, "detail": detail, "hint": "" if ok else hint, "optional": optional})

    ff = _cached("ffmpeg", _ffmpeg_info)
    add("FFmpeg", bool(ff["ffmpeg"]), ff["version"] or ff["ffmpeg"], "Install FFmpeg (apt install ffmpeg).")
    add("FFprobe", bool(ff["ffprobe"]), ff["ffprobe"], "Comes with FFmpeg; install the full package.")
    if ff["ffmpeg"]:
        add("libass captions filter", "ass" in ff["filters"], "animated burned-in captions",
            "Your FFmpeg was built without libass; install a full build.")
        add("loudnorm audio filter", "loudnorm" in ff["filters"], "-14 LUFS loudness",
            "Your FFmpeg lacks loudnorm; install a full build.")
    fw_ok, fw_err = _cached("import:faster_whisper", lambda: _importable("faster_whisper"))
    if fw_ok:
        add("Transcription", True, "faster-whisper")
    else:
        from vv88.analysis.transcribe import whisper_cpp_engine

        engine = whisper_cpp_engine(settings.analysis)
        add("Transcription", bool(engine), f"whisper.cpp ({engine[1].name})" if engine else fw_err,
            "pip install 'vv88[transcribe]', or build whisper.cpp (scripts/termux_setup.sh on Android).")
    for module, label, extra in (("cv2", "OpenCV (face-tracked reframing)", "vision"),
                                 ("yt_dlp", "yt-dlp (source downloads)", "download")):
        ok, err = _cached(f"import:{module}", lambda m=module: _importable(m))
        add(label, ok, "" if ok else err, f"pip install 'vv88[{extra}]'")

    font = settings.editing.font
    if settings.editing.fonts_dir:
        add(f"Caption font '{font}'", Path(settings.editing.fonts_dir).is_dir(), f"from {settings.editing.fonts_dir}",
            "editing.fonts_dir doesn't exist.")
    else:
        fams = _cached("fonts", _font_families)
        if fams is None:
            add(f"Caption font '{font}'", False, "fc-list not found", "Install fontconfig and the font.")
        else:
            exact = font.lower() in fams
            near = font.split()[0].lower() in fams if font.split() else False
            add(f"Caption font '{font}'", exact or near, "installed" if exact else ("family installed" if near else ""),
                "Install the font (fc-list must show it) or set editing.fonts_dir.")

    data = settings.data_path
    try:
        with tempfile.TemporaryFile(dir=data):
            pass
        add("Data folder writable", True, str(data.resolve()))
    except OSError as e:
        add("Data folder writable", False, str(data), f"Fix permissions: {redact(str(e))[:120]}")
    gp = settings.editing.gameplay_dir
    if gp:
        add("Gameplay folder", Path(gp).expanduser().is_dir(), gp, "Folder is missing.", optional=True)
    return rows


# ---- routes -------------------------------------------------------------------------------
def _active_benchmark(ctx):
    return ctx.store.db.execute(
        "SELECT id FROM jobs WHERE kind='benchmark' AND status IN ('queued','running') ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _last_benchmark(ctx):
    row = ctx.store.db.execute(
        "SELECT id, status, result, finished_at FROM jobs WHERE kind='benchmark' AND status IN ('done','failed') "
        "ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    try:
        result = json.loads(row["result"] or "{}")
    except (TypeError, ValueError):
        result = {}
    result = result if isinstance(result, dict) else {}
    nums = {}
    for key in ("render_seconds", "clip_seconds", "clips_per_day"):
        try:
            nums[key] = float(result[key])
        except (KeyError, TypeError, ValueError):
            nums = {}
            break
    ok = row["status"] == "done" and bool(nums)
    return {"id": row["id"], "status": row["status"], "finished_at": row["finished_at"], "ok": ok,
            "render_seconds": _num(nums["render_seconds"]) if ok else "",
            "clip_seconds": _num(nums["clip_seconds"]) if ok else "",
            "clips_per_day": f"{int(nums['clips_per_day']):,}" if ok else ""}


def _page(request: Request, ctx, values: dict | None = None, status_code: int = 200):
    s = ctx.settings
    checks = system_checks(s)
    response = render(
        request, "settings.html", title="Settings",
        v=values or values_from_settings(s), platforms=[(p, PLATFORM_LABELS.get(p, p)) for p in PLATFORMS],
        styles=[(name, st.description, st.split_screen) for name, st in STYLES.items()],
        backends=BACKENDS, whisper_models=WHISPER_MODELS, reframes=REFRAMES, efforts=EFFORTS,
        checks=checks, checks_ok=all(c["ok"] for c in checks if not c["optional"]),
        config_name=config_path(request).name, benchmark=_last_benchmark(ctx), running=_active_benchmark(ctx),
    )
    response.status_code = status_code
    return response


@router.get("/settings", dependencies=[Depends(require_user)])
def settings_page(request: Request):
    return _page(request, context(request))


@router.post("/settings", dependencies=[Depends(require_user), Depends(verify_csrf)])
def save_settings(request: Request, form: FormData = Depends(form_data)):
    ctx = context(request)
    values = values_from_form(form)
    edited, errors = parse_values(values)
    path = config_path(request)
    if not errors:
        try:
            with _write_lock:
                merged = deep_merge(read_config(path), edited)
                validate(merged)
                write_config(path, merged)
        except ConfigFileError as e:
            errors.append(str(e))
        except OSError as e:
            errors.append(f"Couldn't save {path.name}: {redact(str(e))[:200]}")
    if errors:
        flash(request, "Settings not saved. " + " ".join(errors[:5]), "error")
        return _page(request, ctx, values=values, status_code=400)
    msg = f"Saved {path.name}. The next job uses the new settings."
    if edited["publishing"]["backend"] != "dry_run" and ctx.settings.publishing.backend == "dry_run":
        msg += " Publishing is now live: check Connections first."
    return redirect(request, "/settings", msg, "success")


@router.post("/settings/benchmark", dependencies=[Depends(require_user), Depends(verify_csrf)])
def benchmark(request: Request):
    ctx = context(request)
    running = _active_benchmark(ctx)
    if running:
        return redirect(request, f"/jobs/{running['id']}", "A benchmark is already running — showing it.", "info")
    job_id = ctx.jobs.submit("benchmark", {"seconds": 20, "source": "settings"})
    return redirect(request, f"/jobs/{job_id}", f"Benchmark started (job #{job_id}): renders a 20 s test clip.",
                    "success")
