"""Connect accounts once, stay linked: OAuth sign-in on your own computer, tokens kept and refreshed.

Run `vv88 connect <platform>` on the machine where you're signed in to the account. It opens
the platform's own consent page in your browser; you approve; the platform redirects back to a
tiny local web server (127.0.0.1) that captures the authorization code and swaps it for tokens.
No passwords are ever seen or stored by VV88, only revocable tokens:

* youtube   – Google OAuth (desktop app, PKCE). Refresh tokens don't expire once the Google Cloud
              OAuth consent screen is "In production" (in "Testing" Google expires them after 7 days).
* tiktok    – TikTok Login Kit for Desktop (PKCE with a hex-encoded challenge, per TikTok's docs).
              Refresh tokens rotate; the newest is always saved.
* x         – X OAuth 2.0 with PKCE + offline.access. Refresh tokens rotate; the newest is saved.
* instagram – Facebook Login for Business -> long-lived (~60 day) token + your Instagram
              professional account id. `vv88 doctor` warns 10 days before it lapses.

API-key tools (apify, youtube-key, postiz, anthropic) are pasted once, checked live and saved to .env.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import http.server
import json
import logging
import re
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import requests

from vv88.config import Settings
from vv88.publishing.base import PublishError, TokenVault, check
from vv88.store import Store

log = logging.getLogger(__name__)

DEFAULT_PORT = 8788
CALLBACK_PATH = "/callback/"

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    # needed for channels.update (channel description/keywords from the brand kit)
    "https://www.googleapis.com/auth/youtube",
]
TIKTOK_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_SCOPES = ["user.info.basic", "video.publish", "video.list"]
X_AUTH = "https://x.com/i/oauth2/authorize"
X_TOKEN = "https://api.x.com/2/oauth2/token"
X_SCOPES = ["tweet.read", "tweet.write", "users.read", "media.write", "offline.access"]
FB_SCOPES = [
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_insights",
    "pages_show_list",
    "pages_read_engagement",
    "business_management",
]


# ---- PKCE & local callback ---------------------------------------------------------------
def pkce_pair(hex_challenge: bool = False) -> tuple[str, str]:
    """(code_verifier, code_challenge). TikTok desktop wants hex(SHA256); RFC 7636 wants base64url."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    if hex_challenge:
        return verifier, digest.hex()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@dataclass
class CallbackResult:
    code: str = ""
    state: str = ""
    error: str = ""


def _handler_factory(result: CallbackResult, done: threading.Event):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            parsed = urllib.parse.urlparse(self.path)
            if not parsed.path.rstrip("/").endswith(CALLBACK_PATH.strip("/")):
                self.send_response(404)
                self.end_headers()
                return
            q = urllib.parse.parse_qs(parsed.query)
            result.code = (q.get("code") or [""])[0]
            result.state = (q.get("state") or [""])[0]
            result.error = (q.get("error_description") or q.get("error") or [""])[0]
            ok = bool(result.code) and not result.error
            body = (
                "<h2>VV88: account connected. You can close this tab.</h2>"
                if ok
                else f"<h2>VV88: connection failed</h2><p>{result.error or 'no code returned'}</p>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())
            done.set()

        def log_message(self, *args):  # silence default stderr logging
            pass

    return Handler


def wait_for_callback(port: int, timeout: float = 300.0) -> CallbackResult:
    result, done = CallbackResult(), threading.Event()
    server = http.server.HTTPServer(("127.0.0.1", port), _handler_factory(result, done))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(timeout):
            raise TimeoutError(f"no browser callback on port {port} within {timeout:.0f}s")
    finally:
        server.shutdown()
        server.server_close()
    return result


def redirect_uri(port: int, host: str = "127.0.0.1") -> str:
    return f"http://{host}:{port}{CALLBACK_PATH}"


# ---- .env persistence --------------------------------------------------------------------
def update_env_file(values: dict[str, str], path: str | Path = ".env") -> Path:
    """Set KEY=VALUE lines in .env (replacing existing keys, keeping everything else)."""
    p = Path(path)
    lines = p.read_text().splitlines() if p.exists() else []
    pending = dict(values)
    out = []
    for line in lines:
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in pending:
            out.append(f"{m.group(1)}={pending.pop(m.group(1))}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in pending.items())
    p.write_text("\n".join(out) + "\n")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p


# ---- authorize URLs (pure functions, unit-tested) ----------------------------------------
def youtube_authorize_url(client_id: str, redirect: str, state: str, challenge: str) -> str:
    return GOOGLE_AUTH + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # always return a refresh token
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def tiktok_authorize_url(client_key: str, redirect: str, state: str, challenge: str) -> str:
    return TIKTOK_AUTH + "?" + urllib.parse.urlencode({
        "client_key": client_key,
        "response_type": "code",
        "scope": ",".join(TIKTOK_SCOPES),
        "redirect_uri": redirect,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def x_authorize_url(client_id: str, redirect: str, state: str, challenge: str) -> str:
    return X_AUTH + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": " ".join(X_SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def facebook_authorize_url(app_id: str, redirect: str, state: str, version: str) -> str:
    return f"https://www.facebook.com/{version}/dialog/oauth?" + urllib.parse.urlencode({
        "client_id": app_id,
        "redirect_uri": redirect,
        "state": state,
        "response_type": "code",
        "scope": ",".join(FB_SCOPES),
    })


# ---- token exchanges ---------------------------------------------------------------------
def exchange_youtube(http: requests.Session, client_id: str, client_secret: str, code: str, redirect: str,
                     verifier: str) -> dict:
    return check(http.post(GOOGLE_TOKEN, data={
        "code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect,
        "grant_type": "authorization_code", "code_verifier": verifier,
    }, timeout=30), "google token exchange")


def exchange_tiktok(http: requests.Session, client_key: str, client_secret: str, code: str, redirect: str,
                    verifier: str) -> dict:
    body = check(http.post(TIKTOK_TOKEN, data={
        "client_key": client_key, "client_secret": client_secret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": redirect, "code_verifier": verifier,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30), "tiktok token exchange")
    if body.get("error") and body.get("error") not in ("ok", ""):
        raise PublishError(f"tiktok token exchange: {body.get('error')} {body.get('error_description', '')}")
    return body


def exchange_x(http: requests.Session, client_id: str, client_secret: str, code: str, redirect: str,
               verifier: str) -> dict:
    auth = (client_id, client_secret) if client_secret else None
    return check(http.post(X_TOKEN, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
        "code_verifier": verifier, "client_id": client_id,
    }, auth=auth, timeout=30), "x token exchange")


def exchange_facebook(http: requests.Session, app_id: str, app_secret: str, code: str, redirect: str,
                      version: str) -> dict:
    graph = f"https://graph.facebook.com/{version}"
    short = check(http.get(f"{graph}/oauth/access_token", params={
        "client_id": app_id, "client_secret": app_secret, "redirect_uri": redirect, "code": code,
    }, timeout=30), "facebook code exchange")
    long = check(http.get(f"{graph}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
        "fb_exchange_token": short["access_token"],
    }, timeout=30), "facebook long-lived token")
    pages = check(http.get(f"{graph}/me/accounts", params={
        "fields": "name,instagram_business_account{id,username}", "access_token": long["access_token"],
    }, timeout=30), "facebook pages")
    accounts = [
        {"page": p.get("name", ""), "ig_id": p["instagram_business_account"]["id"],
         "username": p["instagram_business_account"].get("username", "")}
        for p in pages.get("data", []) if p.get("instagram_business_account")
    ]
    return {"access_token": long["access_token"], "expires_in": long.get("expires_in"), "accounts": accounts}


# ---- interactive flows -------------------------------------------------------------------
def _authorize(url: str, port: int, state: str, open_browser: bool) -> str:
    print(f"\nOpen this link, sign in to the account you want linked, and approve:\n\n  {url}\n")
    if open_browser:
        webbrowser.open(url)
    cb = wait_for_callback(port)
    if cb.error:
        raise PublishError(f"authorization refused: {cb.error}")
    if cb.state != state:
        raise PublishError("state mismatch on OAuth callback (possible CSRF); try again")
    return cb.code


def connect_platform(platform: str, settings: Settings, store: Store, port: int = DEFAULT_PORT,
                     open_browser: bool = True, env_path: str | Path = ".env",
                     http: requests.Session | None = None) -> dict:
    http = http or requests.Session()
    d = settings.publishing.direct
    state = secrets.token_urlsafe(24)

    if platform == "youtube":
        if not (d.youtube_client_id and d.youtube_client_secret):
            raise PublishError("set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET (Google Cloud > Credentials > "
                               "OAuth client ID > Desktop app) in .env first")
        verifier, challenge = pkce_pair()
        redirect = redirect_uri(port)
        code = _authorize(youtube_authorize_url(d.youtube_client_id, redirect, state, challenge), port, state,
                          open_browser)
        tok = exchange_youtube(http, d.youtube_client_id, d.youtube_client_secret, code, redirect, verifier)
        if not tok.get("refresh_token"):
            raise PublishError("Google returned no refresh token; remove VV88 at "
                               "https://myaccount.google.com/permissions and connect again")
        me = check(http.get("https://www.googleapis.com/youtube/v3/channels",
                            params={"part": "snippet", "mine": "true"},
                            headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=30),
                   "youtube channel lookup")
        channel = (me.get("items") or [{}])[0]
        update_env_file({"YOUTUBE_REFRESH_TOKEN": tok["refresh_token"]}, env_path)
        store.kv_set("oauth:youtube", {"channel_id": channel.get("id", ""),
                                        "title": channel.get("snippet", {}).get("title", ""),
                                        "connected_at": time.time()})
        return {"platform": "youtube", "account": channel.get("snippet", {}).get("title", ""),
                "channel_id": channel.get("id", "")}

    if platform == "tiktok":
        if not (d.tiktok_client_key and d.tiktok_client_secret):
            raise PublishError("set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET (TikTok developer app with "
                               "Login Kit for Desktop + Content Posting API) in .env first")
        verifier, challenge = pkce_pair(hex_challenge=True)
        redirect = redirect_uri(port)
        code = _authorize(tiktok_authorize_url(d.tiktok_client_key, redirect, state, challenge), port, state,
                          open_browser)
        tok = exchange_tiktok(http, d.tiktok_client_key, d.tiktok_client_secret, code, redirect, verifier)
        TokenVault(store, "tiktok", "", "").save(tok["access_token"], tok.get("refresh_token"), tok.get("expires_in"))
        update_env_file({"TIKTOK_REFRESH_TOKEN": tok.get("refresh_token", ""),
                         "TIKTOK_ACCESS_TOKEN": tok["access_token"]}, env_path)
        return {"platform": "tiktok", "open_id": tok.get("open_id", ""), "scope": tok.get("scope", "")}

    if platform == "x":
        if not d.x_client_id:
            raise PublishError("set X_CLIENT_ID (and X_CLIENT_SECRET for a confidential app) in .env first; "
                               f"register {redirect_uri(port)} as a callback URL in the X developer portal")
        verifier, challenge = pkce_pair()
        redirect = redirect_uri(port)
        code = _authorize(x_authorize_url(d.x_client_id, redirect, state, challenge), port, state, open_browser)
        tok = exchange_x(http, d.x_client_id, d.x_client_secret, code, redirect, verifier)
        TokenVault(store, "x", "", "").save(tok["access_token"], tok.get("refresh_token"), tok.get("expires_in"))
        update_env_file({"X_REFRESH_TOKEN": tok.get("refresh_token", ""), "X_ACCESS_TOKEN": tok["access_token"]},
                        env_path)
        me = check(http.get("https://api.x.com/2/users/me",
                            headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=30), "x users/me")
        return {"platform": "x", "account": "@" + me.get("data", {}).get("username", "")}

    if platform == "instagram":
        fb_app_id, fb_app_secret = d.facebook_app_id, d.facebook_app_secret
        if not (fb_app_id and fb_app_secret):
            raise PublishError("set FACEBOOK_APP_ID and FACEBOOK_APP_SECRET (Meta app with Facebook Login for "
                               "Business + Instagram Graph API) in .env first")
        version = d.instagram_graph_version
        redirect = redirect_uri(port, host="localhost")
        code = _authorize(facebook_authorize_url(fb_app_id, redirect, state, version), port, state, open_browser)
        tok = exchange_facebook(http, fb_app_id, fb_app_secret, code, redirect, version)
        if not tok["accounts"]:
            raise PublishError("no Instagram professional account is linked to a Facebook Page you manage; "
                               "switch the IG account to Creator/Business and link it to a Page")
        acct = tok["accounts"][0]
        expires_at = time.time() + float(tok["expires_in"]) if tok.get("expires_in") else 0
        update_env_file({"INSTAGRAM_ACCESS_TOKEN": tok["access_token"], "INSTAGRAM_USER_ID": acct["ig_id"]},
                        env_path)
        store.kv_set("oauth:instagram", {"username": acct["username"], "ig_id": acct["ig_id"],
                                          "expires_at": expires_at, "connected_at": time.time()})
        return {"platform": "instagram", "account": "@" + acct["username"], "ig_id": acct["ig_id"],
                "other_accounts": [a["username"] for a in tok["accounts"][1:]]}

    raise ValueError(f"unknown platform {platform!r}")


# ---- API-key tools -----------------------------------------------------------------------
KEY_TOOLS = {
    "apify": ("APIFY_TOKEN", "Apify > Settings > API & Integrations > Personal API token"),
    "youtube-key": ("YOUTUBE_API_KEY", "Google Cloud > APIs & Services > Credentials > API key "
                                       "(enable YouTube Data API v3)"),
    "postiz": ("POSTIZ_API_KEY", "Postiz > Settings > Public API"),
    "anthropic": ("ANTHROPIC_API_KEY", "console.anthropic.com > API keys"),
}

POSTIZ_IDENTIFIERS = {
    "tiktok": "tiktok",
    "youtube": "youtube",
    "instagram": "instagram",
    "instagram-standalone": "instagram",
    "x": "x",
    "twitter": "x",
}


def validate_key(tool: str, key: str, settings: Settings, http: requests.Session | None = None) -> dict:
    http = http or requests.Session()
    if tool == "apify":
        body = check(http.get("https://api.apify.com/v2/users/me", params={"token": key}, timeout=30), "apify")
        return {"account": body.get("data", {}).get("username", "")}
    if tool == "youtube-key":
        check(http.get("https://www.googleapis.com/youtube/v3/videoCategories",
                       params={"part": "snippet", "regionCode": "US", "key": key}, timeout=30), "youtube api key")
        return {"ok": True}
    if tool == "postiz":
        body = check(http.get(settings.publishing.postiz.api_url.rstrip("/") + "/integrations",
                              headers={"Authorization": key}, timeout=30), "postiz integrations")
        items = body if isinstance(body, list) else body.get("integrations", [])
        mapping: dict[str, str] = {}
        for it in items:
            platform = POSTIZ_IDENTIFIERS.get(str(it.get("identifier") or it.get("providerIdentifier") or "").lower())
            if platform and not it.get("disabled") and platform not in mapping:
                mapping[platform] = str(it.get("id"))
        return {"integrations": mapping, "names": [it.get("name") for it in items]}
    if tool == "anthropic":
        r = http.get("https://api.anthropic.com/v1/models", headers={"x-api-key": key,
                                                                     "anthropic-version": "2023-06-01"}, timeout=30)
        check(r, "anthropic key")
        return {"ok": True}
    raise ValueError(f"unknown tool {tool!r}")


def connect_key(tool: str, settings: Settings, env_path: str | Path = ".env", key: str | None = None,
                http: requests.Session | None = None) -> dict:
    env_name, where = KEY_TOOLS[tool]
    if key is None:
        print(f"Paste your {tool} key ({where}). Input is hidden.")
        key = getpass.getpass(f"{env_name}: ").strip()
    if not key:
        raise PublishError("no key entered")
    info = validate_key(tool, key, settings, http)
    values = {env_name: key}
    if tool == "postiz":
        for platform, iid in info.get("integrations", {}).items():
            values[f"POSTIZ_INTEGRATION_{platform.upper()}"] = iid
    update_env_file(values, env_path)
    return {"tool": tool, "saved": sorted(values), **{k: v for k, v in info.items() if k != "ok"}}


def link_status(settings: Settings, store: Store) -> list[tuple[str, bool, str]]:
    """(name, linked?, detail) for every account and tool, used by `vv88 doctor` / `vv88 connect --status`."""
    d = settings.publishing.direct
    rows: list[tuple[str, bool, str]] = []
    yt = store.kv_get("oauth:youtube") or {}
    rows.append(("youtube", bool(d.youtube_refresh_token), yt.get("title", "")))
    ig = store.kv_get("oauth:instagram") or {}
    exp = float(ig.get("expires_at") or 0)
    detail = "@" + ig["username"] if ig.get("username") else ""
    if exp:
        days = (exp - time.time()) / 86400
        detail += f" (token {'expired' if days <= 0 else f'expires in {days:.0f}d'})"
    rows.append(("instagram", bool(d.instagram_access_token) and (exp == 0 or exp > time.time()), detail))
    for name in ("tiktok", "x"):
        saved = store.kv_get(f"oauth:{name}") or {}
        env_tok = d.tiktok_refresh_token if name == "tiktok" else d.x_refresh_token
        rows.append((name, bool(saved.get("refresh_token") or env_tok), "refresh token stored" if saved else ""))
    rows.append(("apify", bool(settings.discovery.whop_apify.token), ""))
    rows.append(("youtube-key", bool(settings.niche.youtube_api_key), ""))
    pz = settings.publishing.postiz
    linked = {p for p, v in pz.integrations.items() if v}
    rows.append(("postiz", bool(pz.api_key), ", ".join(sorted(linked)) if linked else ""))
    return rows


def dump(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)
