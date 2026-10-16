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

If a platform only accepts an https redirect (e.g. a Meta app in Live mode), pass `--paste` and
register any https URL you control: after approving, copy the address-bar URL back into the terminal.

API-key tools (apify, youtube-key, postiz, anthropic) are pasted once, checked live and saved to .env.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import html
import http.server
import json
import logging
import os
import re
import secrets
import socket
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import requests

from vv88.config import Settings
from vv88.publishing.base import PublishError, TokenVault, check, redact
from vv88.store import Store

log = logging.getLogger(__name__)

DEFAULT_PORT = 8788
CALLBACK_PATH = "/callback/"
INSTAGRAM_RENEW_DAYS = 10

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPE_MANAGE = "https://www.googleapis.com/auth/youtube"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    # needed for channels.update (channel description/keywords from the brand kit)
    YOUTUBE_SCOPE_MANAGE,
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

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


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


def clean_text(s: str, limit: int = 300) -> str:
    """Strip control characters (incl. ANSI escapes) from text that came from a URL."""
    return _CONTROL.sub("", s)[:limit]


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class CallbackServer:
    """Loopback server that waits for the one redirect carrying our `state`.

    Bound *before* the browser is sent to the consent page, so another local process can't grab
    the port first. Requests without the right state get a 400 and are ignored (a random page
    can't end or spoof the flow), handler threads drop idle sockets after 10 s, and the error
    page never reflects request text.
    """

    def __init__(self, port: int, expected_state: str, also_ipv6: bool = False):
        self.result = CallbackResult()
        self.done = threading.Event()
        self.servers: list[http.server.HTTPServer] = []
        handler = self._handler(expected_state)
        try:
            self.servers.append(_Server(("127.0.0.1", port), handler))
        except OSError as e:
            raise PublishError(f"port {port} is busy ({e.strerror}); pass --port with a free port and register "
                               "that redirect URI instead") from e
        if also_ipv6 and socket.has_ipv6:
            class _Server6(_Server):
                address_family = socket.AF_INET6

            try:
                self.servers.append(_Server6(("::1", port), handler))
            except OSError:
                pass  # no IPv6 loopback; 'localhost' will resolve to 127.0.0.1
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in self.servers]
        for t in self.threads:
            t.start()

    def _handler(self, expected_state: str):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            timeout = 10

            def do_GET(self):  # noqa: N802 - stdlib naming
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path.rstrip("/") != CALLBACK_PATH.rstrip("/"):
                    self._reply(404, "Not found")
                    return
                q = urllib.parse.parse_qs(parsed.query)
                state = (q.get("state") or [""])[0]
                if not secrets.compare_digest(state.encode(), expected_state.encode()):
                    self._reply(400, "This link does not belong to the sign-in VV88 started. Ignored.")
                    return
                if outer.done.is_set():
                    self._reply(200, "Already received. You can close this tab.")
                    return
                outer.result.code = (q.get("code") or [""])[0]
                outer.result.state = state
                outer.result.error = clean_text((q.get("error_description") or q.get("error") or [""])[0])
                if outer.result.code and not outer.result.error:
                    self._reply(200, "VV88: account connected. You can close this tab.")
                else:
                    self._reply(200, "VV88: sign-in was not completed: "
                                + (outer.result.error or "no authorization code returned"))
                outer.done.set()

            def _reply(self, status: int, message: str) -> None:
                body = f"<!doctype html><meta charset='utf-8'><h2>{html.escape(message)}</h2>".encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # keep codes out of stderr
                pass

        return Handler

    def wait(self, timeout: float = 300.0) -> CallbackResult:
        try:
            if not self.done.wait(timeout):
                raise TimeoutError(f"no browser callback within {timeout:.0f}s")
            return self.result
        finally:
            self.close()

    def close(self) -> None:
        for s in self.servers:
            s.shutdown()
            s.server_close()
        self.servers = []


def wait_for_callback(port: int, timeout: float = 300.0, state: str = "") -> CallbackResult:
    """Convenience wrapper (used by tests): bind, wait, close."""
    return CallbackServer(port, state).wait(timeout)


def parse_pasted_redirect(pasted: str, expected_state: str) -> str:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted.strip()).query)
    if not secrets.compare_digest((q.get("state") or [""])[0].encode(), expected_state.encode()):
        raise PublishError("the pasted URL doesn't belong to this sign-in (state mismatch); start again")
    err = clean_text((q.get("error_description") or q.get("error") or [""])[0])
    if err:
        raise PublishError(f"authorization refused: {err}")
    code = (q.get("code") or [""])[0]
    if not code:
        raise PublishError("no ?code= in the pasted URL")
    return code


def redirect_uri(port: int, host: str = "127.0.0.1") -> str:
    return f"http://{host}:{port}{CALLBACK_PATH}"


# ---- .env persistence --------------------------------------------------------------------
def update_env_file(values: dict[str, str], path: str | Path = ".env") -> Path:
    """Set KEY=VALUE lines in .env (replacing existing keys, keeping everything else).

    Written atomically with 0600 permissions, and mirrored into os.environ so the running process
    (and a following settings reload) sees the new values even if older ones were already exported.
    """
    for k, v in values.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", k):
            raise ValueError(f"bad env key {k!r}")
        if any(ch in v for ch in "\r\n\0"):
            raise ValueError(f"refusing to write {k}: value contains a line break")
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
    directory = p.parent if str(p.parent) else Path(".")
    fd, tmp = tempfile.mkstemp(prefix=".env.", dir=directory)  # mkstemp creates the file 0600
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    if os.name == "posix" and (p.stat().st_mode & 0o077):
        log.warning("%s is readable by other users; run: chmod 600 %s", p, p)
    os.environ.update(values)
    return p


def _safe_id(value: str, what: str) -> str:
    if not _SAFE_ID.match(value or ""):
        raise PublishError(f"unexpected {what} returned by the platform: {clean_text(str(value), 60)!r}")
    return value


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


def facebook_authorize_url(app_id: str, redirect: str, state: str, version: str, config_id: str = "") -> str:
    params = {"client_id": app_id, "redirect_uri": redirect, "state": state, "response_type": "code"}
    if config_id:
        # Facebook Login for Business apps define permissions in a configuration instead of `scope`.
        params["config_id"] = config_id
    else:
        params["scope"] = ",".join(FB_SCOPES)
    return f"https://www.facebook.com/{version}/dialog/oauth?" + urllib.parse.urlencode(params)


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
    # These two calls are documented as GET with query parameters; errors are redacted before display.
    short = check(http.get(f"{graph}/oauth/access_token", params={
        "client_id": app_id, "client_secret": app_secret, "redirect_uri": redirect, "code": code,
    }, timeout=30), "facebook code exchange")
    long = check(http.get(f"{graph}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
        "fb_exchange_token": short["access_token"],
    }, timeout=30), "facebook long-lived token")
    accounts = []
    url: str | None = f"{graph}/me/accounts"
    params: dict | None = {"fields": "name,instagram_business_account{id,username}", "limit": 100}
    headers = {"Authorization": f"Bearer {long['access_token']}"}
    while url:
        page = check(http.get(url, params=params, headers=headers, timeout=30), "facebook pages")
        for p in page.get("data", []):
            if p.get("instagram_business_account"):
                acct = p["instagram_business_account"]
                accounts.append({"page": p.get("name", ""), "ig_id": acct["id"], "username": acct.get("username", "")})
        url, params = (page.get("paging") or {}).get("next"), None  # `next` already carries the query
    return {"access_token": long["access_token"], "expires_in": long.get("expires_in"), "accounts": accounts}


# ---- interactive flows -------------------------------------------------------------------
def _authorize(url: str, port: int, state: str, open_browser: bool, paste: bool = False,
               also_ipv6: bool = False) -> str:
    if paste:
        print(f"\nOpen this link, sign in, approve, then copy the FULL address-bar URL you land on:\n\n  {url}\n")
        if open_browser:
            webbrowser.open(url)
        return parse_pasted_redirect(input("Paste the URL here: "), state)
    server = CallbackServer(port, state, also_ipv6=also_ipv6)  # bound before the browser leaves
    print(f"\nOpen this link, sign in to the account you want linked, and approve:\n\n  {url}\n")
    if open_browser:
        webbrowser.open(url)
    cb = server.wait()
    if cb.error:
        raise PublishError(f"authorization refused: {cb.error}")
    if not cb.code:
        raise PublishError("the platform returned no authorization code")
    return cb.code


@dataclass
class AuthStart:
    """Everything needed to finish a sign-in after the browser comes back (kept server-side)."""

    platform: str
    url: str
    state: str
    verifier: str
    redirect: str

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("platform", "url", "state", "verifier", "redirect")}

    @classmethod
    def from_dict(cls, d: dict) -> "AuthStart":
        return cls(**{k: d[k] for k in ("platform", "url", "state", "verifier", "redirect")})


def start_authorization(platform: str, settings: Settings, redirect: str) -> AuthStart:
    """Check prerequisites and build the consent URL for `platform` with a fresh state + PKCE pair."""
    d = settings.publishing.direct
    state = secrets.token_urlsafe(24)
    if platform == "youtube":
        if not (d.youtube_client_id and d.youtube_client_secret):
            raise PublishError("set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET (Google Cloud > Credentials > "
                               "OAuth client ID > Desktop app) first")
        verifier, challenge = pkce_pair()
        url = youtube_authorize_url(d.youtube_client_id, redirect, state, challenge)
    elif platform == "tiktok":
        if not (d.tiktok_client_key and d.tiktok_client_secret):
            raise PublishError("set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET (TikTok developer app with "
                               "Login Kit for Desktop + Content Posting API) first")
        verifier, challenge = pkce_pair(hex_challenge=True)
        url = tiktok_authorize_url(d.tiktok_client_key, redirect, state, challenge)
    elif platform == "x":
        if not d.x_client_id:
            raise PublishError("set X_CLIENT_ID (and X_CLIENT_SECRET for a confidential app) first, and register "
                               f"{redirect} as a callback URL in the X developer portal")
        verifier, challenge = pkce_pair()
        url = x_authorize_url(d.x_client_id, redirect, state, challenge)
    elif platform == "instagram":
        if not (d.facebook_app_id and d.facebook_app_secret):
            raise PublishError("set FACEBOOK_APP_ID and FACEBOOK_APP_SECRET (Meta app with Facebook Login for "
                               "Business + Instagram Graph API) first. Meta allows an http://localhost redirect "
                               "only while the app is in Development mode and you have a role on it; otherwise "
                               "register an https URL")
        verifier = ""
        url = facebook_authorize_url(d.facebook_app_id, redirect, state, d.instagram_graph_version,
                                     d.facebook_login_config_id)
    else:
        raise ValueError(f"unknown platform {platform!r}")
    return AuthStart(platform, url, state, verifier, redirect)


def complete_authorization(start: AuthStart, code: str, settings: Settings, store: Store,
                           env_path: str | Path = ".env", http: requests.Session | None = None) -> dict:
    """Swap the authorization code for tokens and save them so the account stays linked."""
    http = http or requests.Session()
    d = settings.publishing.direct
    platform, redirect = start.platform, start.redirect

    if platform == "youtube":
        tok = exchange_youtube(http, d.youtube_client_id, d.youtube_client_secret, code, redirect, start.verifier)
        if not tok.get("refresh_token"):
            raise PublishError("Google returned no refresh token; remove VV88 at "
                               "https://myaccount.google.com/permissions and connect again")
        me = check(http.get("https://www.googleapis.com/youtube/v3/channels",
                            params={"part": "snippet", "mine": "true"},
                            headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=30),
                   "youtube channel lookup")
        items = me.get("items") or []
        if not items:
            raise PublishError("this Google account has no YouTube channel; create the channel first, or sign in "
                               "with the Google account (or brand account) that owns it")
        channel = items[0]
        scopes = tok.get("scope", "").split()
        update_env_file({"YOUTUBE_REFRESH_TOKEN": tok["refresh_token"]}, env_path)
        store.kv_set("oauth:youtube", {"channel_id": _safe_id(channel.get("id", ""), "channel id"),
                                        "title": channel.get("snippet", {}).get("title", ""),
                                        "scopes": scopes, "connected_at": time.time()})
        out = {"platform": "youtube", "account": channel.get("snippet", {}).get("title", ""),
               "channel_id": channel["id"]}
        if scopes and YOUTUBE_SCOPE_MANAGE not in scopes:
            out["warning"] = "channel-edit permission wasn't granted; `brand apply` can't update the description"
        return out

    if platform == "tiktok":
        tok = exchange_tiktok(http, d.tiktok_client_key, d.tiktok_client_secret, code, redirect, start.verifier)
        TokenVault(store, "tiktok", "", "").save(tok["access_token"], tok.get("refresh_token"), tok.get("expires_in"))
        update_env_file({"TIKTOK_REFRESH_TOKEN": tok.get("refresh_token", ""),
                         "TIKTOK_ACCESS_TOKEN": tok["access_token"]}, env_path)
        return {"platform": "tiktok", "open_id": tok.get("open_id", ""), "scope": tok.get("scope", "")}

    if platform == "x":
        tok = exchange_x(http, d.x_client_id, d.x_client_secret, code, redirect, start.verifier)
        TokenVault(store, "x", "", "").save(tok["access_token"], tok.get("refresh_token"), tok.get("expires_in"))
        update_env_file({"X_REFRESH_TOKEN": tok.get("refresh_token", ""), "X_ACCESS_TOKEN": tok["access_token"]},
                        env_path)
        out = {"platform": "x", "account": ""}
        try:  # tokens are already valid and saved; the name lookup is only cosmetic
            me = check(http.get("https://api.x.com/2/users/me",
                                headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=30), "x users/me")
            out["account"] = "@" + me.get("data", {}).get("username", "")
        except (PublishError, requests.RequestException) as e:
            out["warning"] = f"linked, but couldn't read the account name ({redact(str(e))[:120]})"
        return out

    if platform == "instagram":
        tok = exchange_facebook(http, d.facebook_app_id, d.facebook_app_secret, code, redirect,
                                d.instagram_graph_version)
        if not tok["accounts"]:
            raise PublishError("no Instagram professional account is linked to a Facebook Page you manage; "
                               "switch the IG account to Creator/Business and link it to a Page")
        acct = tok["accounts"][0]
        ig_id = _safe_id(acct["ig_id"], "Instagram account id")
        expires_at = time.time() + float(tok["expires_in"]) if tok.get("expires_in") else 0
        update_env_file({"INSTAGRAM_ACCESS_TOKEN": tok["access_token"], "INSTAGRAM_USER_ID": ig_id}, env_path)
        store.kv_set("oauth:instagram", {"username": acct["username"], "ig_id": ig_id,
                                          "expires_at": expires_at, "connected_at": time.time()})
        return {"platform": "instagram", "account": "@" + acct["username"], "ig_id": ig_id,
                "other_accounts": [a["username"] for a in tok["accounts"][1:]]}

    raise ValueError(f"unknown platform {platform!r}")


def connect_platform(platform: str, settings: Settings, store: Store, port: int = DEFAULT_PORT,
                     open_browser: bool = True, env_path: str | Path = ".env",
                     http: requests.Session | None = None, paste: bool = False,
                     redirect: str | None = None) -> dict:
    """CLI flow: start, wait for the local callback (or a pasted URL), complete."""
    d = settings.publishing.direct
    if platform == "instagram":
        redirect = redirect or d.facebook_redirect_uri or redirect_uri(port, host="localhost")
        local = redirect.startswith((f"http://localhost:{port}", f"http://127.0.0.1:{port}"))
        paste = paste or not local
    else:
        redirect = redirect or redirect_uri(port)
    start = start_authorization(platform, settings, redirect)
    code = _authorize(start.url, port, start.state, open_browser, paste, also_ipv6=platform == "instagram")
    return complete_authorization(start, code, settings, store, env_path, http)


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
    """Live-check a key. Keys travel in headers, never in URLs, so they can't leak via error text or logs."""
    http = http or requests.Session()
    if tool == "apify":
        body = check(http.get("https://api.apify.com/v2/users/me", headers={"Authorization": f"Bearer {key}"},
                              timeout=30), "apify")
        return {"account": body.get("data", {}).get("username", "")}
    if tool == "youtube-key":
        check(http.get("https://www.googleapis.com/youtube/v3/videoCategories",
                       params={"part": "snippet", "regionCode": "US"}, headers={"X-goog-api-key": key},
                       timeout=30), "youtube api key")
        return {"ok": True}
    if tool == "postiz":
        body = check(http.get(settings.publishing.postiz.api_url.rstrip("/") + "/integrations",
                              headers={"Authorization": key}, timeout=30), "postiz integrations")
        items = body if isinstance(body, list) else body.get("integrations", [])
        mapping: dict[str, str] = {}
        for it in items:
            platform = POSTIZ_IDENTIFIERS.get(str(it.get("identifier") or it.get("providerIdentifier") or "").lower())
            iid = str(it.get("id") or "")
            if platform and not it.get("disabled") and platform not in mapping and _SAFE_ID.match(iid):
                mapping[platform] = iid
        return {"integrations": mapping, "names": [it.get("name") for it in items]}
    if tool == "anthropic":
        check(http.get("https://api.anthropic.com/v1/models", headers={"x-api-key": key,
                                                                       "anthropic-version": "2023-06-01"},
                       timeout=30), "anthropic key")
        return {"ok": True}
    raise ValueError(f"unknown tool {tool!r}")


class Skipped(Exception):
    """The owner pressed Enter without a key: keep whatever is configured."""


def connect_key(tool: str, settings: Settings, env_path: str | Path = ".env", key: str | None = None,
                http: requests.Session | None = None) -> dict:
    env_name, where = KEY_TOOLS[tool]
    if key is None:
        print(f"Paste your {tool} key ({where}). Input is hidden; press Enter to skip.")
        key = getpass.getpass(f"{env_name}: ").strip()
    if not key:
        raise Skipped(f"{tool}: skipped (kept existing setting)" if os.environ.get(env_name) else f"{tool}: skipped")
    if any(ch in key for ch in "\r\n\0 "):
        raise PublishError("that doesn't look like a key (it contains spaces or line breaks)")
    info = validate_key(tool, key, settings, http)
    values = {env_name: key}
    if tool == "postiz":
        for platform, iid in info.get("integrations", {}).items():
            values[f"POSTIZ_INTEGRATION_{platform.upper()}"] = iid
    update_env_file(values, env_path)
    return {"tool": tool, "saved": sorted(values), **{k: v for k, v in info.items() if k != "ok"}}


@dataclass
class LinkRow:
    name: str
    linked: bool
    detail: str = ""
    warning: str = ""


def link_status(settings: Settings, store: Store, now: float | None = None) -> list[LinkRow]:
    """Every account and tool, for `vv88 doctor` / `vv88 connect status`."""
    now = time.time() if now is None else now
    d = settings.publishing.direct
    rows: list[LinkRow] = []
    yt = store.kv_get("oauth:youtube") or {}
    yt_warn = ""
    if yt.get("scopes") and YOUTUBE_SCOPE_MANAGE not in yt["scopes"]:
        yt_warn = "no channel-edit permission; run `vv88 connect youtube` again for `brand apply`"
    rows.append(LinkRow("youtube", bool(d.youtube_refresh_token), yt.get("title", ""), yt_warn))

    ig = store.kv_get("oauth:instagram") or {}
    exp = float(ig.get("expires_at") or 0)
    detail = "@" + ig["username"] if ig.get("username") else ""
    warning = ""
    linked = bool(d.instagram_access_token)
    if exp:
        days = (exp - now) / 86400
        if days <= 0:
            linked, warning = False, "token expired; run `vv88 connect instagram`"
        elif days < INSTAGRAM_RENEW_DAYS:
            warning = f"token expires in {days:.0f}d; run `vv88 connect instagram` to renew"
        detail += f" (token expires in {days:.0f}d)" if days > 0 else ""
    rows.append(LinkRow("instagram", linked, detail.strip(), warning))

    for name in ("tiktok", "x"):
        saved = store.kv_get(f"oauth:{name}") or {}
        env_tok = d.tiktok_refresh_token if name == "tiktok" else d.x_refresh_token
        rows.append(LinkRow(name, bool(saved.get("refresh_token") or env_tok),
                            "refresh token stored" if saved.get("refresh_token") else ""))
    rows.append(LinkRow("apify", bool(settings.discovery.whop_apify.token)))
    rows.append(LinkRow("youtube-key", bool(settings.niche.youtube_api_key)))
    pz = settings.publishing.postiz
    channels = sorted(p for p, v in pz.integrations.items() if v)
    rows.append(LinkRow("postiz", bool(pz.api_key), ", ".join(channels)))
    return rows


def dump(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)
