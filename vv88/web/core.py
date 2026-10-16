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
import re
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
def current_password(request: Request) -> str:
    """VV88_WEB_PASSWORD (it can change while running), else the one the app started with; never ''."""
    return os.environ.get("VV88_WEB_PASSWORD") or getattr(request.app.state, "web_password", "")


def password_tag(request: Request) -> str:
    """Ties a session to the current password: changing VV88_WEB_PASSWORD signs every browser out."""
    key = str(getattr(request.app.state, "session_secret", "")).encode()
    return hmac.new(key, current_password(request).encode(), hashlib.sha256).hexdigest()[:24]


DEVICE_COOKIE = "vv88_device"
DEVICE_COOKIE_HTTPS = "__Host-vv88_device"  # __Host-: no other host or subdomain can set or override it
DEVICE_MAX_AGE = 365 * 86400
_DEVICE_RE = re.compile(r"([A-Za-z0-9_-]{16,64})\.([0-9a-f]{32})")


def device_cookie_name(request: Request) -> str:
    return getattr(request.app.state, "device_cookie", DEVICE_COOKIE)


def _device_sig(request: Request, device_id: str) -> str:
    # Bound to the password: changing VV88_WEB_PASSWORD withdraws every browser's trusted status too.
    key = str(getattr(request.app.state, "session_secret", "")).encode()
    msg = f"device:{device_id}:{password_tag(request)}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def new_device_token(request: Request) -> str:
    """Signed id for a browser that has signed in successfully (survives logout; not a credential)."""
    device_id = secrets.token_urlsafe(16)
    return f"{device_id}.{_device_sig(request, device_id)}"


def known_device(request: Request) -> str:
    """The device id from any valid device cookie in the request, else ''.

    Reads the raw Cookie header(s) so a second cookie of the same name (planted from another port or a
    sibling domain) can't hide the real one; anything not in the exact token format is ignored.
    """
    name = device_cookie_name(request)
    for header in request.headers.getlist("cookie"):
        for part in header.split(";"):
            key, sep, value = part.strip().partition("=")
            m = _DEVICE_RE.fullmatch(value.strip().strip('"')) if sep and key.strip() == name else None
            if m and hmac.compare_digest(m.group(2).encode(), _device_sig(request, m.group(1)).encode()):
                return m.group(1)
    return ""


def _origin_key(url: str) -> tuple[str, int | None] | None:
    """(host, port) with case, a trailing dot and default ports (80/443) normalised away; None if unparseable."""
    try:
        parts = urlparse(url)
        host = (parts.hostname or "").rstrip(".").lower()
        port = parts.port
    except ValueError:
        return None
    return (host, None if port in (None, 80, 443) else port) if host else None


def same_origin(request: Request) -> bool:
    """False when a browser says the form was posted from another origin (another port, host or subdomain).

    Sec-Fetch-Site is set by the browser itself (pages can't forge it) and survives a strict Referrer-Policy,
    which turns Origin into "null"; Origin/Referer are only the fallback for browsers without it. Requests with
    none of these (scripts, curl) can't carry a victim's cookies, so they pass.
    """
    site = request.headers.get("sec-fetch-site")
    if site:
        return site in ("same-origin", "none")
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return True
    key = None if source == "null" else _origin_key(source)
    if key is None:
        return False
    allowed = {_origin_key("//" + request.url.netloc), _origin_key(os.environ.get("VV88_WEB_BASE_URL", ""))}
    return key in allowed - {None}


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
    """Wrong-password limits per 10 minutes: 5 per client, and 50 across all clients.

    The global cap stops guessing from many addresses (or a spoofed X-Forwarded-For behind a proxy). It does not
    apply to browsers that have signed in before (trusted=True, keyed by their device cookie instead of their
    address), so outsiders can't use it to lock the owner out.
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

    def blocked(self, key: str, trusted: bool = False) -> bool:
        now = time.time()
        self._prune(now)
        hits = [t for t in self.fails.get(key, []) if now - t < self.window]
        if hits:
            self.fails[key] = hits
        else:
            self.fails.pop(key, None)
        return len(hits) >= self.limit or (not trusted and len(self.all_fails) >= self.global_limit)

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
        # len() first: int() on thousands of digits raises (Python's digit limit) and would be a 500
        if declared is not None and (not declared.isdigit() or len(declared) > 15 or int(declared) > self.limit):
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


# Return URL kept through sign-in, measured once form-encoded (UTF-8 %XX: non-ASCII grows up to 9x); the login
# form allows 8 KB per field, so stay well under it.
MAX_NEXT_ENCODED = 6000


def _fits_login_form(url: str) -> bool:
    return len(quote(url, safe="")) <= MAX_NEXT_ENCODED


def safe_next(url: str) -> str:
    """Only allow same-site relative paths (no open redirects); anything odd, even unparseable, becomes '/'."""
    url = url or "/"
    if not url.startswith("/") or url.startswith(("//", "/\\")):
        return "/"
    try:
        parsed = urlparse(url)
    except ValueError:
        return "/"
    if parsed.scheme or parsed.netloc:
        return "/"
    if not _fits_login_form(url):
        # Too big to carry through the login form once the browser encodes it: keep just the page.
        return parsed.path if _fits_login_form(parsed.path) else "/"
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
