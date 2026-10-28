"""Settings from command-line flags and PUMPSCAN_* environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .council import Rules


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"PUMPSCAN_{name}", default)


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8088
    demo: bool = False
    demo_speed: float = 10.0
    # Optional. Enables live per-trade data (dev sells, bundles, unique buyers) for the top 40 candidates.
    # PumpPortal meters this: 0.01 SOL per 10,000 trade messages, billed to the wallet linked to the key.
    pumpportal_api_key: str = ""
    watch_cap: int = 1500
    data_dir: str = "~/.pumpscan"
    # Required whenever the dashboard is reachable from other machines: anyone with the URL could press Stop.
    password: str = ""
    # Press Start automatically when the server boots (for hosted installs, so the track record keeps growing).
    autostart: bool = False
    # Free hosts (Render's free plan) put a service to sleep after ~15 idle minutes, which would stop the scan.
    # When set, the server requests this URL every 10 minutes so it never counts as idle.
    keepalive_url: str = ""
    rules: Rules = field(default_factory=Rules)

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.host = _env("HOST", s.host)
        s.port = int(_env("PORT", os.environ.get("PORT", str(s.port))))  # hosts like Render set PORT
        s.demo = _env("DEMO", "0") in ("1", "true", "yes")
        s.pumpportal_api_key = _env("PUMPPORTAL_API_KEY", os.environ.get("PUMPPORTAL_API_KEY", ""))
        s.watch_cap = int(_env("WATCH_CAP", str(s.watch_cap)))
        s.data_dir = _env("DATA_DIR", s.data_dir)
        s.password = _env("PASSWORD")
        s.autostart = _env("AUTOSTART", "0") in ("1", "true", "yes")
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "")  # set by Render on every web service
        s.keepalive_url = _env("KEEPALIVE_URL", render_url.rstrip("/") + "/healthz" if render_url else "")
        s.rules.min_buy_votes = int(_env("MIN_BUY_VOTES", str(s.rules.min_buy_votes)))
        s.rules.window_minutes = float(_env("WINDOW_MINUTES", str(s.rules.window_minutes)))
        return s

    def is_local_only(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")
