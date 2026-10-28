from __future__ import annotations

import re
from dataclasses import dataclass

import requests

TOKEN_RE = re.compile(r"(access_token=)[^&\s\"']+|(EAA[A-Za-z0-9]{10,})|(TH[A-Za-z0-9]{20,})")


class ChannelError(Exception):
    pass


@dataclass
class SendResult:
    ok: bool
    external_id: str = ""
    error: str = ""


def redact(text: str) -> str:
    """Strip access tokens from anything that might end up in logs or the console."""
    return TOKEN_RE.sub(lambda m: (m.group(1) or "") + "…redacted…", str(text))


def check(resp: requests.Response, what: str) -> dict:
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:500]}
    if resp.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
        err = body.get("error") if isinstance(body, dict) else body
        msg = err.get("message") if isinstance(err, dict) else err
        raise ChannelError(redact(f"{what}: HTTP {resp.status_code}: {msg}"))
    return body


def call(fn, what: str, *args, **kwargs) -> dict:
    try:
        resp = fn(*args, **kwargs)
    except requests.RequestException as e:
        raise ChannelError(redact(f"{what}: {e}")) from e
    return check(resp, what)
