"""Settings come from environment variables (a local .env file is loaded first).

Everything the business owner changes day to day (search keywords, caps, auto-send switches,
business hours) lives in the database instead and is edited from the operations console.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def load_env_file(path: str | Path = ".env") -> None:
    """Minimal .env reader: KEY=VALUE lines, # comments, optional quotes. Never overrides real env vars."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def update_env_file(values: dict[str, str], path: str | Path = ".env") -> None:
    """Set keys in the .env file (keeping other lines) and in os.environ. The file is kept owner-only."""
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    os.environ.update(values)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    # Business identity (shown on the website and used by the agents)
    business_name: str = "CAR-LOS Rentals"
    phone: str = ""
    email: str = ""
    address: str = ""
    city: str = ""
    timezone: str = "America/New_York"
    site_url: str = "http://localhost:8080"

    # Storage and web
    db_path: str = "data/carlos.db"
    upload_dir: str = "data/uploads"
    ops_password: str = ""
    secret_key: str = ""
    https_cookies: bool = False

    # Claude (optional: without a key the agents use built-in rules and templates)
    anthropic_api_key: str = ""
    model: str = "claude-opus-5-5"

    # Meta: Facebook Page + Instagram professional account (Messenger Platform / Graph API)
    meta_app_secret: str = ""
    meta_verify_token: str = ""
    graph_version: str = "v23.0"
    fb_page_id: str = ""
    fb_page_token: str = ""
    ig_user_id: str = ""

    # Threads API
    threads_user_id: str = ""
    threads_token: str = ""

    # Email (booking confirmations, owner alerts, follow-ups to people who asked by email)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    owner_email: str = ""

    extra: dict = field(default_factory=dict)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def meta_enabled(self) -> bool:
        return bool(self.fb_page_id and self.fb_page_token)

    @property
    def instagram_enabled(self) -> bool:
        return bool(self.meta_enabled and self.ig_user_id)

    @property
    def threads_enabled(self) -> bool:
        return bool(self.threads_user_id and self.threads_token)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and (self.smtp_from or self.smtp_user))

    @property
    def demo_mode(self) -> bool:
        """No social account connected: the scout reads a built-in sample feed and sends go to a sandbox."""
        return not (self.meta_enabled or self.threads_enabled)


def load_settings(env_path: str | Path = ".env") -> Settings:
    load_env_file(env_path)
    return Settings(
        business_name=_env("CARLOS_BUSINESS_NAME", "CAR-LOS Rentals"),
        phone=_env("CARLOS_PHONE"),
        email=_env("CARLOS_EMAIL"),
        address=_env("CARLOS_ADDRESS"),
        city=_env("CARLOS_CITY"),
        timezone=_env("CARLOS_TIMEZONE", "America/New_York"),
        site_url=_env("CARLOS_SITE_URL", "http://localhost:8080").rstrip("/"),
        db_path=_env("CARLOS_DB", "data/carlos.db"),
        upload_dir=_env("CARLOS_UPLOAD_DIR", "data/uploads"),
        ops_password=_env("CARLOS_OPS_PASSWORD"),
        secret_key=_env("CARLOS_SECRET_KEY"),
        https_cookies=_env("CARLOS_HTTPS_COOKIES").lower() in ("1", "true", "yes"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        model=_env("CARLOS_MODEL", "claude-opus-5-5"),
        meta_app_secret=_env("META_APP_SECRET"),
        meta_verify_token=_env("META_VERIFY_TOKEN"),
        graph_version=_env("META_GRAPH_VERSION", "v23.0"),
        fb_page_id=_env("FB_PAGE_ID"),
        fb_page_token=_env("FB_PAGE_TOKEN"),
        ig_user_id=_env("IG_USER_ID"),
        threads_user_id=_env("THREADS_USER_ID"),
        threads_token=_env("THREADS_ACCESS_TOKEN"),
        smtp_host=_env("SMTP_HOST"),
        smtp_port=_env_int("SMTP_PORT", 587),
        smtp_user=_env("SMTP_USER"),
        smtp_password=_env("SMTP_PASSWORD"),
        smtp_from=_env("SMTP_FROM"),
        owner_email=_env("OWNER_EMAIL"),
    )


def ensure_secrets(settings: Settings, env_path: str | Path = ".env") -> bool:
    """Create the console password and session key on first run. Returns True when a password was generated."""
    values = {}
    created = False
    if not settings.ops_password:
        settings.ops_password = secrets.token_urlsafe(12)
        values["CARLOS_OPS_PASSWORD"] = settings.ops_password
        created = True
    if len(settings.secret_key) < 32:
        settings.secret_key = secrets.token_urlsafe(48)
        values["CARLOS_SECRET_KEY"] = settings.secret_key
    if not settings.meta_verify_token:
        settings.meta_verify_token = secrets.token_urlsafe(18)
        values["META_VERIFY_TOKEN"] = settings.meta_verify_token
    if values:
        update_env_file(values, env_path)
    return created
