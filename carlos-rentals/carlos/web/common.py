"""Shared web plumbing: templates, filters, form parsing, rate limiting, security headers."""

from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from carlos.carart import car_svg
from carlos.db import parse_iso
from carlos.inventory import money

WEB_DIR = Path(__file__).parent
MAX_FORM = 64 * 1024


def make_env() -> Environment:
    env = Environment(loader=FileSystemLoader(WEB_DIR / "templates"), autoescape=select_autoescape(["html", "xml"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["money"] = money
    env.filters["miles"] = lambda v: f"{int(v):,} mi" if v is not None else ""
    env.filters["ago"] = ago
    env.filters["nicedate"] = nicedate
    env.globals["car_svg"] = lambda body, color, uid="c": Markup(car_svg(body, color, str(uid)))
    env.globals["now_year"] = date.today().year
    return env


def nicedate(value) -> str:
    if not value:
        return ""
    try:
        d = value if isinstance(value, date) else datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return str(value)
    return f"{d:%a, %b} {d.day}, {d.year}"


def ago(value) -> str:
    dt = parse_iso(value) if isinstance(value, str) else value
    if not dt:
        return ""
    secs = int(time.time() - dt.timestamp())
    if secs < 0:
        return "just now"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit} ago"
    return "just now"


async def read_form(request: Request) -> dict[str, list[str]]:
    """Parse a urlencoded form with a size limit (multipart uploads are handled separately)."""
    body = await request.body()
    if len(body) > MAX_FORM:
        raise ValueError("form too large")
    return parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)


def first(form: dict[str, list[str]], key: str, default: str = "", limit: int = 2000) -> str:
    vals = form.get(key)
    return (vals[0] if vals else default).strip()[:limit]


def to_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def to_float(value, default=None):
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return default


class RateLimiter:
    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self.hits) > 10000:  # bound memory
            for k in list(self.hits)[:5000]:
                del self.hits[k]
        return True


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def new_token() -> str:
    return secrets.token_urlsafe(24)


def same_token(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a, b)


def security_headers(response, csp: str):
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


def html(env: Environment, name: str, code: int = 200, **ctx) -> HTMLResponse:
    return HTMLResponse(env.get_template(name).render(**ctx), status_code=code)
