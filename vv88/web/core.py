"""Shared plumbing for the VV88 web UI: auth, CSRF, flash messages, rendering, safe file serving.

Page modules (vv88/web/pages/*.py) follow one contract:

    router = APIRouter()

    @router.get("/thing", dependencies=[Depends(require_user)])
    def page(request: Request):
        ctx = context(request)                       # settings, store, jobs manager, csrf token
        return render(request, "thing.html", title="Thing", rows=...)

    @router.post("/thing/do", dependencies=[Depends(require_user), Depends(verify_csrf)])
    async def do(request: Request):
        form = await request.form()
        job_id = ctx.jobs.submit("clip", {...})      # long work always goes through a job
        return redirect(request, "/jobs/{id}", f"Started job #{id}")

Templates extend base.html and must render every POST form with {{ csrf_field() }}.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from vv88.config import Settings
from vv88.store import Store

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

NAV = [
    ("/", "Dashboard"),
    ("/campaigns", "Campaigns"),
    ("/studio", "Clip studio"),
    ("/clips", "Clips"),
    ("/queue", "Queue"),
    ("/performance", "Performance"),
    ("/brand", "Brand"),
    ("/connections", "Connections"),
    ("/settings", "Settings"),
    ("/jobs", "Jobs"),
]


class LoginRequired(Exception):
    pass


@dataclass
class Ctx:
    settings: Settings
    store: Store
    jobs: object  # vv88.web.jobs.JobManager


def context(request: Request) -> Ctx:
    jobs = request.app.state.jobs
    settings = jobs.settings()
    return Ctx(settings=settings, store=Store(settings.data_path / "vv88.db"), jobs=jobs)


# ---- auth -------------------------------------------------------------------------------
def require_user(request: Request) -> None:
    if not request.session.get("user"):
        raise LoginRequired()


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session["csrf"] = tok
    return tok


async def verify_csrf(request: Request) -> None:
    form = await request.form()
    sent = str(form.get("csrf") or request.headers.get("x-csrf-token") or "")
    expected = request.session.get("csrf") or ""
    if not expected or not secrets.compare_digest(sent.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Form expired or invalid (CSRF). Reload the page and retry.")


class LoginThrottle:
    """5 wrong passwords per client within 10 minutes -> locked out for the rest of the window."""

    def __init__(self, limit: int = 5, window_s: float = 600):
        self.limit, self.window = limit, window_s
        self.fails: dict[str, list[float]] = {}

    def blocked(self, key: str) -> bool:
        now = time.time()
        hits = [t for t in self.fails.get(key, []) if now - t < self.window]
        self.fails[key] = hits
        return len(hits) >= self.limit

    def fail(self, key: str) -> None:
        self.fails.setdefault(key, []).append(time.time())

    def clear(self, key: str) -> None:
        self.fails.pop(key, None)


# ---- flash + redirects ------------------------------------------------------------------
def flash(request: Request, message: str, kind: str = "info") -> None:
    request.session.setdefault("flash", []).append({"kind": kind, "message": message[:500]})


def redirect(request: Request, url: str, message: str = "", kind: str = "info") -> RedirectResponse:
    if message:
        flash(request, message, kind)
    return RedirectResponse(safe_next(url), status_code=303)


def safe_next(url: str) -> str:
    """Only allow same-site relative paths (no open redirects)."""
    parsed = urlparse(url or "/")
    if parsed.scheme or parsed.netloc or not (url or "/").startswith("/") or url.startswith("//"):
        return "/"
    return url


# ---- rendering --------------------------------------------------------------------------
def fmt_time(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fmt_ago(ts: float | None) -> str:
    if not ts:
        return "—"
    d = time.time() - float(ts)
    future = d < 0
    d = abs(d)
    for unit, secs in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= secs:
            v = f"{int(d // secs)}{unit}"
            break
    else:
        v = f"{int(d)}s"
    return f"in {v}" if future else f"{v} ago"


def media_url(path: str | Path | None) -> str:
    return "/media?path=" + quote(str(Path(path))) if path else ""


def _brand_avatar(request: Request) -> str:
    p = Path(getattr(request.app.state, "brand_dir", "brand")) / "avatar_1024.png"
    return str(p.resolve()) if p.is_file() else ""


def render(request: Request, template: str, **ctx):
    flashes = request.session.pop("flash", [])
    token = csrf_token(request)
    base = {
        "request": request,
        "nav": NAV,
        "path": request.url.path,
        "flashes": flashes,
        "csrf": token,
        "csrf_field": lambda: Markup(f'<input type="hidden" name="csrf" value="{token}">'),
        "fmt_time": fmt_time,
        "fmt_ago": fmt_ago,
        "media_url": media_url,
        "user": request.session.get("user"),
        "brand_avatar": _brand_avatar(request),
    }
    base.update(ctx)
    return templates.TemplateResponse(request, template, base)


# ---- safe file serving ------------------------------------------------------------------
def allowed_roots(settings: Settings, brand_dir: str | Path = "brand") -> list[Path]:
    data = settings.data_path.resolve()
    return [data / "clips", data / "outbox", Path(brand_dir).resolve()]


def resolve_media(path: str, settings: Settings, brand_dir: str | Path = "brand") -> Path:
    """Map a requested path to a real file under an allowed folder, or raise 404 (no traversal)."""
    try:
        p = Path(path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(status_code=404) from None
    if not p.is_file() or p.suffix.lower() not in {".mp4", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".md"}:
        raise HTTPException(status_code=404)
    for root in allowed_roots(settings, brand_dir):
        if p.is_relative_to(root):
            return p
    raise HTTPException(status_code=404)
