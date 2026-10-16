"""The VV88 control website: `vv88 web` (FastAPI + server-rendered pages, no build step)."""

from __future__ import annotations

import importlib
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from vv88.config import load_settings
from vv88.connect import AuthStart, clean_text, complete_authorization, start_authorization, update_env_file
from vv88.publishing.base import PublishError, redact
from vv88.web.core import (
    WEB_DIR,
    LoginRequired,
    LoginThrottle,
    context,
    redirect,
    render,
    require_user,
    resolve_media,
    safe_next,
    verify_csrf,
)
from vv88.web.jobs import JobManager

log = logging.getLogger(__name__)

PAGES = ["dashboard", "jobs", "campaigns", "studio", "clips", "queue", "performance", "brand", "connections",
         "settings"]
OAUTH_TTL_S = 900
OAUTH_PLATFORMS = ("youtube", "tiktok", "instagram", "x")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def ensure_web_secrets(env_path: str | Path) -> tuple[str, str, bool]:
    """Admin password + session-signing key, generated once and kept in .env (0600)."""
    password = os.environ.get("VV88_WEB_PASSWORD", "")
    secret = os.environ.get("VV88_WEB_SECRET", "")
    created = False
    values = {}
    if not password:
        password = secrets.token_urlsafe(12)
        values["VV88_WEB_PASSWORD"] = password
        created = True
    if len(secret) < 32:
        secret = secrets.token_urlsafe(48)
        values["VV88_WEB_SECRET"] = secret
    if values:
        update_env_file(values, env_path)
    return password, secret, created


def create_app(config_path: str | Path | None = None, env_path: str | Path = ".env",
               brand_dir: str | Path = "brand", https_cookies: bool = False, start_jobs: bool = True) -> FastAPI:
    load_settings(config_path, env_path=env_path)  # loads .env so existing web secrets are picked up
    password, secret, created = ensure_web_secrets(env_path)
    if created:
        print(f"\n  VV88 web password (saved to {env_path} as VV88_WEB_PASSWORD): {password}\n")

    jobs = JobManager(config_path, env_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_jobs:
            jobs.start()
        yield
        jobs.stop()

    app = FastAPI(title="VV88", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.jobs = jobs
    app.state.env_path = str(env_path)
    app.state.brand_dir = str(brand_dir)
    app.state.throttle = LoginThrottle()
    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="vv88_session",
                       same_site="lax", https_only=https_cookies, max_age=7 * 86400)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path not in ("/static/app.css", "/static/app.js"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(LoginRequired)
    async def to_login(request: Request, exc: LoginRequired):
        nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={safe_next(nxt)}", status_code=303)

    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    # ---- login ------------------------------------------------------------------------
    @app.get("/login")
    def login_page(request: Request, next: str = "/"):
        return render(request, "login.html", title="Sign in", next=safe_next(next))

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        client = request.client.host if request.client else "?"
        throttle: LoginThrottle = app.state.throttle
        nxt = safe_next(str(form.get("next") or "/"))
        if throttle.blocked(client):
            return render(request, "login.html", title="Sign in", next=nxt,
                          error="Too many attempts. Wait 10 minutes and try again.")
        current = os.environ.get("VV88_WEB_PASSWORD", password)
        if not secrets.compare_digest(str(form.get("password") or "").encode(), current.encode()):
            throttle.fail(client)
            time.sleep(0.5)
            return render(request, "login.html", title="Sign in", next=nxt, error="Wrong password.")
        throttle.clear(client)
        request.session.clear()  # new session on login (no fixation)
        request.session["user"] = "owner"
        request.session["csrf"] = secrets.token_urlsafe(32)
        return RedirectResponse(nxt, status_code=303)

    @app.post("/logout", dependencies=[Depends(verify_csrf)])
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return RedirectResponse("/static/icon.png", status_code=301)

    # ---- media (clips, outbox, brand kit) ----------------------------------------------
    @app.get("/media", dependencies=[Depends(require_user)])
    def media(request: Request, path: str):
        ctx = context(request)
        f = resolve_media(path, ctx.settings, app.state.brand_dir)
        return FileResponse(f)

    # ---- OAuth linking from the browser -----------------------------------------------
    def oauth_base(request: Request) -> str:
        return (os.environ.get("VV88_WEB_BASE_URL") or str(request.base_url)).rstrip("/")

    @app.post("/oauth/start/{platform}", dependencies=[Depends(require_user), Depends(verify_csrf)])
    def oauth_start(request: Request, platform: str):
        if platform not in OAUTH_PLATFORMS:
            return redirect(request, "/connections", "Unknown platform.", "error")
        ctx = context(request)
        try:
            start = start_authorization(platform, ctx.settings, f"{oauth_base(request)}/oauth/callback")
        except (PublishError, ValueError) as e:
            return redirect(request, "/connections", redact(str(e)), "error")
        pending = {k: v for k, v in (ctx.store.kv_get("web:oauth") or {}).items()
                   if time.time() - v.get("at", 0) < OAUTH_TTL_S}
        pending[start.state] = {**start.to_dict(), "at": time.time()}
        ctx.store.kv_set("web:oauth", pending)  # verifier stays server-side, never in the cookie
        return RedirectResponse(start.url, status_code=303)

    @app.get("/oauth/callback", dependencies=[Depends(require_user)])
    def oauth_callback(request: Request, state: str = "", code: str = "", error: str = "",
                       error_description: str = ""):
        ctx = context(request)
        pending = ctx.store.kv_get("web:oauth") or {}
        entry = pending.pop(state, None) if state else None
        ctx.store.kv_set("web:oauth", pending)
        if not entry or time.time() - entry.get("at", 0) > OAUTH_TTL_S:
            return redirect(request, "/connections", "That sign-in link expired or wasn't started here. Try again.",
                            "error")
        if error or error_description or not code:
            return redirect(request, "/connections",
                            f"Sign-in not completed: {clean_text(error_description or error or 'no code')}", "error")
        try:
            res = complete_authorization(AuthStart.from_dict(entry), code, ctx.settings, ctx.store,
                                         env_path=app.state.env_path)
        except Exception as e:  # noqa: BLE001 - show any failure as a message, never a stack trace
            return redirect(request, "/connections", f"Linking failed: {redact(str(e))[:300]}", "error")
        msg = f"{res['platform'].title()} linked {res.get('account') or ''}".strip()
        if res.get("warning"):
            msg += f" — {res['warning']}"
        return redirect(request, "/connections", msg, "success" if not res.get("warning") else "warning")

    for name in PAGES:
        try:
            module = importlib.import_module(f"vv88.web.pages.{name}")
        except ModuleNotFoundError as e:
            if e.name == f"vv88.web.pages.{name}":
                log.warning("page module %s not available yet", name)
                continue
            raise
        app.include_router(module.router)

    return app


def serve(host: str = "127.0.0.1", port: int = 8088, config_path: str | None = None, env_path: str = ".env",
          brand_dir: str = "brand", https_cookies: bool = False) -> None:
    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1") and not https_cookies:
        print("  ! Listening beyond this computer without HTTPS. Put it behind an HTTPS reverse proxy "
              "(and pass --https-cookies) before exposing it to the internet.")
    app = create_app(config_path, env_path, brand_dir, https_cookies)
    print(f"  VV88 control panel: http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning", proxy_headers=https_cookies)
