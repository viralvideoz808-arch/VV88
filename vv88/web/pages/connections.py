"""Connections: link the social accounts (OAuth in the browser), developer-app credentials, API keys.

Secrets never leave the server: the page only says whether each credential is set, inputs are
password fields that start empty (blank = keep the current value), and every error message is
passed through `redact()` (and scrubbed of the submitted value) before it is shown.
"""

from __future__ import annotations

import os
import re

import requests
from fastapi import APIRouter, Depends, Request
from starlette.datastructures import FormData

from vv88 import connect
from vv88.publishing.base import PublishError, redact
from vv88.web.core import context, form_data, redirect, render, require_user, verify_csrf

router = APIRouter()

SOCIAL = ("youtube", "tiktok", "instagram", "x")
LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "x": "X",
          "apify": "Apify (Whop discovery)", "youtube-key": "YouTube API key (niche signal)",
          "postiz": "Postiz", "anthropic": "Anthropic (Claude re-ranker)"}

# Developer-app credentials, grouped by the platform whose sign-in needs them.
# (env name, label, required for the browser sign-in?)
APP_GROUPS: list[tuple[str, str, str, list[tuple[str, str, bool]]]] = [
    ("youtube", "YouTube", "console.cloud.google.com → Credentials → OAuth client ID", [
        ("YOUTUBE_CLIENT_ID", "Client ID", True),
        ("YOUTUBE_CLIENT_SECRET", "Client secret", True),
    ]),
    ("tiktok", "TikTok", "developers.tiktok.com → Manage apps (Login Kit + Content Posting API)", [
        ("TIKTOK_CLIENT_KEY", "Client key", True),
        ("TIKTOK_CLIENT_SECRET", "Client secret", True),
    ]),
    ("x", "X", "developer.x.com → Projects & Apps → User authentication (OAuth 2.0)", [
        ("X_CLIENT_ID", "Client ID", True),
        ("X_CLIENT_SECRET", "Client secret (confidential apps only)", False),
    ]),
    ("instagram", "Instagram (Meta app)", "developers.facebook.com → Facebook Login for Business + Instagram Graph API", [
        ("FACEBOOK_APP_ID", "App ID", True),
        ("FACEBOOK_APP_SECRET", "App secret", True),
        ("FACEBOOK_LOGIN_CONFIG_ID", "Login for Business configuration ID (optional)", False),
        ("FACEBOOK_REDIRECT_URI", "HTTPS redirect for the CLI --paste flow (optional)", False),
    ]),
]
APP_CREDENTIALS = [name for _, _, _, fields in APP_GROUPS for name, _, _ in fields]
REQUIRED = {platform: [name for name, _, required in fields if required] for platform, _, _, fields in APP_GROUPS}

MAX_VALUE = 1024
MAX_KEY = 512
# Printable ASCII without whitespace, quotes or backslashes, not starting with "#" (a comment in .env):
# survives the .env round-trip unchanged.
_VALUE_RE = re.compile(r"^[!$-&(-\[\]-~][!#-&(-\[\]-~]*$")


def _credential_set(settings, env_name: str) -> bool:
    return bool(getattr(settings.publishing.direct, env_name.lower(), ""))


def _tool_set(settings, tool: str, rows: dict) -> bool:
    if tool in rows:
        return bool(rows[tool].linked)
    env_name, _ = connect.KEY_TOOLS[tool]
    return bool(os.environ.get(env_name))


def oauth_redirect_uri(request: Request) -> str:
    base = (os.environ.get("VV88_WEB_BASE_URL") or str(request.base_url)).rstrip("/")
    return f"{base}/oauth/callback"


def _scrub(message: str, *secrets: str) -> str:
    """Redact, then remove any submitted value that might have been echoed back."""
    text = redact(message)
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "***")
    return text[:300]


@router.get("/connections", dependencies=[Depends(require_user)])
def connections_page(request: Request):
    ctx = context(request)
    s = ctx.settings
    rows = connect.link_status(s, ctx.store)
    by_name = {r.name: r for r in rows}
    social = []
    for p in SOCIAL:
        r = by_name.get(p) or connect.LinkRow(p, False)
        missing = [n for n in REQUIRED[p] if not _credential_set(s, n)]
        social.append({"platform": p, "label": LABELS[p], "row": r, "missing": missing})
    groups = [{"platform": p, "title": title, "where": where,
               "fields": [{"name": n, "label": label, "required": req, "set": _credential_set(s, n)}
                          for n, label, req in fields]}
              for p, title, where, fields in APP_GROUPS]
    tools = []
    for tool, (env_name, where) in connect.KEY_TOOLS.items():
        r = by_name.get(tool)
        tools.append({"tool": tool, "label": LABELS.get(tool, tool), "env": env_name, "where": where,
                      "set": _tool_set(s, tool, by_name), "detail": r.detail if r else ""})
    linked_social = sum(1 for x in social if x["row"].linked)
    return render(
        request, "connections.html", title="Connections",
        rows=rows, labels=LABELS, social=social, groups=groups, tools=tools,
        redirect_uri=oauth_redirect_uri(request), backend=s.publishing.backend,
        linked_social=linked_social, postiz_set=_tool_set(s, "postiz", by_name),
    )


@router.post("/connections/app-credentials", dependencies=[Depends(require_user), Depends(verify_csrf)])
def save_app_credentials(request: Request, form: FormData = Depends(form_data)):
    values: dict[str, str] = {}
    problems: list[str] = []
    for name in APP_CREDENTIALS:
        raw = form.get(name)
        v = raw.strip() if isinstance(raw, str) else ""
        if not v:
            continue  # blank = keep what is there
        if len(v) > MAX_VALUE:
            problems.append(f"{name} is too long.")
        elif not _VALUE_RE.match(v):
            problems.append(f"{name} doesn't look right (no spaces, quotes or line breaks).")
        elif name == "FACEBOOK_REDIRECT_URI" and not v.lower().startswith("https://"):
            problems.append("FACEBOOK_REDIRECT_URI must be an https:// address.")
        else:
            values[name] = v
    if problems:
        return redirect(request, "/connections", "Nothing saved. " + " ".join(problems[:4]), "error")
    if not values:
        return redirect(request, "/connections", "Nothing to save: every field was blank (blank keeps the current "
                        "value).", "info")
    try:
        connect.update_env_file(values, request.app.state.env_path)
    except (ValueError, OSError) as e:
        return redirect(request, "/connections", "Couldn't save: " + _scrub(str(e), *values.values()), "error")
    return redirect(request, "/connections", "Saved " + ", ".join(sorted(values)) + ". Now press Connect for that "
                    "platform.", "success")


@router.post("/connections/key/{tool}", dependencies=[Depends(require_user), Depends(verify_csrf)])
def save_key(request: Request, tool: str, form: FormData = Depends(form_data)):
    if tool not in connect.KEY_TOOLS:
        return redirect(request, "/connections", "Unknown tool.", "error")
    raw = form.get("key")
    key = raw.strip() if isinstance(raw, str) else ""
    label = LABELS.get(tool, tool)
    if len(key) > MAX_KEY:
        return redirect(request, "/connections", f"{label}: that key is too long to be real.", "error")
    ctx = context(request)
    try:
        info = connect.connect_key(tool, ctx.settings, env_path=request.app.state.env_path, key=key)
    except connect.Skipped:
        return redirect(request, "/connections", f"{label}: nothing saved — paste a key first.", "info")
    except (PublishError, ValueError, OSError, requests.RequestException) as e:
        return redirect(request, "/connections", f"{label}: key not saved. {_scrub(str(e), key)}", "error")
    msg = f"{label} key checked and saved."
    kind = "success"
    if tool == "postiz":
        found = info.get("integrations") or {}
        if found:
            msg += " Channels found: " + ", ".join(f"{LABELS.get(p, p)} ({i})" for p, i in sorted(found.items())) + "."
        else:
            msg += (" No TikTok/YouTube/Instagram/X channels found yet — add them in Postiz (Add channel), then save "
                    "the key again.")
            kind = "warning"
        if ctx.settings.publishing.backend != "postiz":
            msg += " To post through Postiz, set the publishing backend to “postiz” on the Settings page."
    elif info.get("account"):
        msg += f" Account: {str(info['account'])[:60]}."
    return redirect(request, "/connections", msg, kind)
