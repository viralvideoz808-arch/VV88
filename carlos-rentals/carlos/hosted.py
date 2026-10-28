"""One ASGI app serving both sites, for hosts that run a single entry point (Vercel Functions).

The console answers on CARLOS_CONSOLE_HOST (or any hostname starting with "console." or ending in
"-console"); every other hostname gets the public website.
"""

from __future__ import annotations

from carlos.config import ensure_secrets, load_settings
from carlos.db import Database
from carlos.seed import seed_sample_inventory


class HostRouter:
    def __init__(self, site, ops, console_host: str = ""):
        self.site, self.ops, self.console_host = site, ops, console_host

    def is_console(self, host: str) -> bool:
        host = host.split(":")[0].lower()
        first = host.split(".")[0]
        return bool(host) and (host == self.console_host or first == "console" or first.endswith("-console"))

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        headers = dict(scope.get("headers") or [])
        host = (headers.get(b"x-forwarded-host") or headers.get(b"host") or b"").decode("latin-1")
        target = self.ops if self.is_console(host.split(",")[0].strip()) else self.site
        await target(scope, receive, send)


def build_app(env_path: str = ".env"):
    from carlos.web.ops import create_ops_app
    from carlos.web.site import create_site_app
    settings = load_settings(env_path)
    ensure_secrets(settings, env_path)
    db = Database(settings.db_path, url=settings.database_url)
    seed_sample_inventory(db)
    return HostRouter(create_site_app(db, settings),
                      create_ops_app(db, settings, run_scheduler=not settings.serverless),
                      settings.console_host)
