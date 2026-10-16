"""Shared plumbing for the VV88 web UI: auth, CSRF, flash messages, rendering, safe file serving.

Page modules (vv88/web/pages/*.py) follow one contract:

    router = APIRouter()

    @router.get("/thing", dependencies=[Depends(require_user)])
    def page(request: Request):
        ctx = context(request)                       # settings, store, jobs manager, csrf token
        return render(request, "thing.html", title="Thing", rows=...)

    @router.post("/thing/do", dependencies=[Depends(require_user), Depends(verify_csrf)])
    def do(request: Request, form: FormData = Depends(form_data)):   # plain def: runs off the event loop
        job_id = ctx.jobs.submit("clip", {...})      # long work always goes through a job
        return redirect(request, "/jobs/{id}", f"Started job #{id}")

Templates extend base.html and must render every POST form with {{ csrf_field() }}.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.datastructures import FormData

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
def password_tag(request: Request) -> str:
    """Ties a session to the current password: changing VV88_WEB_PASSWORD signs every browser out."""
    state = request.app.state
    password = os.environ.get("VV88_WEB_PASSWORD") or getattr(state, "web_password", "")
    key = str(getattr(state, "session_secret", "")).encode()
    return hmac.new(key, password.encode(), hashlib.sha256).hexdigest()[:24]


def logged_in(request: Request) -> bool:
    return request.session.get("user") == "owner" and hmac.compare_digest(
        str(request.session.get("pw") or ""), password_tag(request))


def require_user(request: Request) -> None:
    if not logged_in(request):
        raise LoginRequired()


async def read_form(request: Request, **limits) -> FormData:
    """request.form(), but a malformed body is a 400 instead of a 500 (python-multipart raises ValueErrors)."""
    try:
        return await request.form(**limits)
    except ValueError:
        raise HTTPException(status_code=400, detail="That form couldn't be read. Reload the page and retry.") from None


async def form_data(request: Request) -> FormData:
    """The parsed form (shared with verify_csrf), so POST handlers can be plain `def`s run in the threadpool."""
    return await read_form(request)


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session["csrf"] = tok
    return tok


async def verify_csrf(request: Request) -> None:
    form = await read_form(request)
    sent = str(form.get("csrf") or request.headers.get("x-csrf-token") or "")
    expected = request.session.get("csrf") or ""
    if not expected or not secrets.compare_digest(sent.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Form expired or invalid (CSRF). Reload the page and retry.")


class LoginThrottle:
    """Wrong-password limits per 10 minutes: 5 per client address, and 50 across all addresses.

    The global cap stops guessing from many addresses (or a spoofed X-Forwarded-For behind a proxy);
    the per-client cap keeps one noisy client from using up the global budget alone.
    """

    def __init__(self, limit: int = 5, window_s: float = 600, global_limit: int = 50, max_keys: int = 5000):
        self.limit, self.window, self.global_limit, self.max_keys = limit, window_s, global_limit, max_keys
        self.fails: dict[str, list[float]] = {}
        self.all_fails: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self.all_fails and now - self.all_fails[0] >= self.window:
            self.all_fails.popleft()
        if len(self.fails) > self.max_keys:
            for k in [k for k, v in self.fails.items() if not v or now - v[-1] >= self.window]:
                del self.fails[k]
            while len(self.fails) > self.max_keys:  # still too many: drop the oldest entries
                del self.fails[next(iter(self.fails))]

    def blocked(self, key: str) -> bool:
        now = time.time()
        self._prune(now)
        hits = [t for t in self.fails.get(key, []) if now - t < self.window]
        if hits:
            self.fails[key] = hits
        else:
            self.fails.pop(key, None)
        return len(hits) >= self.limit or len(self.all_fails) >= self.global_limit

    def fail(self, key: str) -> None:
        now = time.time()
        self.fails.setdefault(key, []).append(now)
        self.all_fails.append(now)
        self._prune(now)

    def clear(self, key: str) -> None:
        self.fails.pop(key, None)


class BodyLimit:
    """ASGI middleware: cap request bodies (declared or streamed) except on the upload route(s).

    Stops anonymous clients from spooling huge multipart bodies to disk via /login or /logout before
    any check runs. Upload routes are exempt; they require login before reading the body.
    """

    def __init__(self, app, limit: int = 1024 * 1024, exempt: tuple[str, ...] = ()):
        self.app, self.limit, self.exempt = app, limit, set(exempt)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self.exempt:
            return await self.app(scope, receive, send)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.limit):
            return await self._reject(scope, receive, send)
        seen = 0
        started = False

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BaseException as exc:  # may arrive wrapped in an ExceptionGroup from an inner task group
            if not _only_too_large(exc):
                raise
            if not started:
                await self._reject(scope, receive, send)

    async def _reject(self, scope, receive, send):
        response = PlainTextResponse("Request body too large.", status_code=413, headers={"Connection": "close"})
        await response(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


def _only_too_large(exc: BaseException) -> bool:
    inner = getattr(exc, "exceptions", None)  # (Base)ExceptionGroup, incl. the 3.10 backport
    if inner is not None:
        return bool(inner) and all(_only_too_large(e) for e in inner)
    return isinstance(exc, _BodyTooLarge)


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
        "user": request.session.get("user") if logged_in(request) else None,
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
