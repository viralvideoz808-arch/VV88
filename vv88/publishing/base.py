"""Publisher interface + shared HTTP helpers."""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Protocol

import requests

from vv88.models import PostContent, PostResult
from vv88.store import Store

log = logging.getLogger(__name__)


class Publisher(Protocol):
    platform: str

    def publish(self, video_path: str, content: PostContent) -> PostResult: ...

    def fetch_metrics(self, remote_id: str) -> dict | None: ...


class PublishError(RuntimeError):
    pass


_SECRET_PARAM = re.compile(
    r"(?i)((?:access_token|refresh_token|client_secret|fb_exchange_token|token|key|code|code_verifier|"
    r"api_key|apikey|password)=)[^&\s'\"]+"
)
_BEARER = re.compile(r"(?i)\b(bearer|oauth)\s+[A-Za-z0-9._~+/=-]{8,}")


def redact(text: str) -> str:
    """Mask secrets in URLs / headers before anything is printed or logged."""
    return _BEARER.sub(r"\1 ***", _SECRET_PARAM.sub(r"\1***", text))


class RedactingFilter(logging.Filter):
    """Logging filter that masks secrets in every record (installed by the CLI)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        record.msg, record.args = redact(msg), ()
        return True


def check(resp: requests.Response, what: str) -> dict:
    try:
        body = resp.json()
    except ValueError:
        body = {"text": resp.text[:500]}
    if resp.status_code >= 400:
        raise PublishError(redact(f"{what} failed: HTTP {resp.status_code} {json.dumps(body)[:800]}"))
    return body


def poll(fn, *, every: float, timeout: float, done) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        result = fn()
        if done(result):
            return result
        if time.monotonic() > deadline:
            raise PublishError(f"timed out waiting for processing: {json.dumps(result)[:500]}")
        time.sleep(every)


class TokenVault:
    """Keeps rotating OAuth refresh tokens (TikTok, X) across runs."""

    def __init__(self, store: Store, platform: str, initial_refresh: str, initial_access: str = ""):
        self.store = store
        self.key = f"oauth:{platform}"
        saved = store.kv_get(self.key) or {}
        self.refresh_token = saved.get("refresh_token") or initial_refresh
        self.access_token = saved.get("access_token") or initial_access
        self.expires_at = float(saved.get("expires_at") or 0)

    def valid(self) -> bool:
        return bool(self.access_token) and (self.expires_at == 0 or time.time() < self.expires_at - 120)

    def save(self, access: str, refresh: str | None, expires_in: float | None) -> None:
        self.access_token = access
        if refresh:
            self.refresh_token = refresh
        self.expires_at = time.time() + float(expires_in) if expires_in else 0
        self.store.kv_set(
            self.key,
            {"access_token": self.access_token, "refresh_token": self.refresh_token, "expires_at": self.expires_at},
        )


class DryRunPublisher:
    """Writes the video + post copy to data/outbox/<platform>/ (also handy for manual uploading)."""

    def __init__(self, platform: str, outbox: Path):
        self.platform = platform
        self.outbox = outbox / platform

    def publish(self, video_path: str, content: PostContent) -> PostResult:
        self.outbox.mkdir(parents=True, exist_ok=True)
        dest = self.outbox / Path(video_path).name
        if not dest.exists():
            shutil.copy2(video_path, dest)
        dest.with_suffix(".json").write_text(
            json.dumps(
                {
                    "platform": content.platform,
                    "title": content.title,
                    "caption": content.caption,
                    "tags": content.tags,
                    "paid_partnership": content.paid_partnership,
                    "cover_ms": content.cover_ms,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        log.info("[dry-run] %s -> %s", self.platform, dest)
        return PostResult(self.platform, True, remote_id=f"dry:{dest.stem}", url=str(dest), status="dry_run")

    def fetch_metrics(self, remote_id: str) -> dict | None:
        return None
