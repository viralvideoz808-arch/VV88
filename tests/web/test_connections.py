import html as htmllib
import re

import requests

from vv88 import connect
from vv88.publishing.base import PublishError

SECRET = "GOCSPX-very-secret-value-123"


def _env(demo_root) -> str:
    p = demo_root / ".env"
    return p.read_text() if p.exists() else ""


def _flashes(html: str) -> str:
    return htmllib.unescape(" ".join(re.findall(r'<div class="flash [a-z]+" role="status">(.*?)</div>', html, re.S)))


def test_page_renders_status_buttons_and_redirect_uri(client):
    r = client.get("/connections")
    assert r.status_code == 200
    html = r.text
    for p in ("youtube", "tiktok", "instagram", "x"):
        assert f'action="/oauth/start/{p}"' in html
    # every POST form carries the CSRF field
    assert html.count('method="post"') == html.count('name="csrf"')
    assert 'value="http://testserver/oauth/callback"' in html and 'data-copy-target="#redirect-uri"' in html
    # missing developer-app credentials are named, and Connect is disabled until they're set
    assert "YOUTUBE_CLIENT_ID" in html and "FACEBOOK_APP_SECRET" in html and "disabled" in html
    for tool in ("apify", "youtube-key", "postiz", "anthropic"):
        assert f'action="/connections/key/{tool}"' in html
    assert "Postiz" in html and "docs/CONNECT.md" in html and "Apify (Whop discovery)" in html
    assert 'action="/connections/app-credentials"' in html and 'type="password"' in html
    assert "<script>" not in html and " onclick" not in html


def test_redirect_uri_prefers_configured_base_url(client, monkeypatch):
    monkeypatch.setenv("VV88_WEB_BASE_URL", "https://vv88.example.com/")
    assert 'value="https://vv88.example.com/oauth/callback"' in client.get("/connections").text


def test_save_app_credentials_writes_env_without_echoing(client, csrf, demo_root):
    (demo_root / ".env").write_text(_env(demo_root) + "TIKTOK_CLIENT_KEY=keep-me\n")
    r = client.post("/connections/app-credentials", data={
        "csrf": csrf("/connections"), "YOUTUBE_CLIENT_ID": " 123-abc.apps.googleusercontent.com ",
        "YOUTUBE_CLIENT_SECRET": SECRET, "TIKTOK_CLIENT_KEY": "", "NOT_ALLOWED": "x"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/connections"
    env = _env(demo_root)
    assert "YOUTUBE_CLIENT_ID=123-abc.apps.googleusercontent.com\n" in env
    assert f"YOUTUBE_CLIENT_SECRET={SECRET}\n" in env
    assert "TIKTOK_CLIENT_KEY=keep-me" in env and "NOT_ALLOWED" not in env  # blank keeps, unknown ignored
    html = client.get("/connections").text
    assert "Saved YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET" in _flashes(html)
    assert SECRET not in html and "123-abc" not in html
    # YouTube no longer lists missing credentials, so its Connect button is live and starts Google sign-in
    youtube_row = html[html.index('action="/oauth/start/youtube"') - 600:html.index('action="/oauth/start/youtube"')]
    assert "Needs" not in youtube_row
    token = csrf("/connections")
    r = client.post("/oauth/start/youtube", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://accounts.google.com/")
    assert "redirect_uri=http%3A%2F%2Ftestserver%2Foauth%2Fcallback" in r.headers["location"]


def test_app_credentials_reject_bad_values(client, csrf, demo_root):
    before = _env(demo_root)
    token = csrf("/connections")
    for data, expect in (
        ({"YOUTUBE_CLIENT_ID": "has space"}, "YOUTUBE_CLIENT_ID doesn't look right"),
        ({"X_CLIENT_ID": "line\nbreak"}, "X_CLIENT_ID doesn't look right"),
        ({"FACEBOOK_REDIRECT_URI": "http://insecure.example/cb"}, "must be an https://"),
        ({"TIKTOK_CLIENT_SECRET": "x" * 2000}, "TIKTOK_CLIENT_SECRET is too long"),
        ({"YOUTUBE_CLIENT_ID": "", "X_CLIENT_ID": "   "}, "Nothing to save"),
    ):
        r = client.post("/connections/app-credentials", data={"csrf": token, **data})
        assert r.status_code == 200, data
        assert expect in _flashes(r.text), data
        assert "has space" not in r.text and "insecure.example" not in r.text
    assert _env(demo_root) == before


def test_save_postiz_key_shows_channels(client, csrf, demo_root, monkeypatch):
    seen = {}

    def fake_validate(tool, key, settings, http=None):
        seen["call"] = (tool, key)
        return {"integrations": {"tiktok": "tt-123", "youtube": "yt-456"}, "names": ["a", "b"]}

    monkeypatch.setattr(connect, "validate_key", fake_validate)
    r = client.post("/connections/key/postiz", data={"csrf": csrf("/connections"), "key": " pz_live_key_987 "})
    assert r.status_code == 200
    assert seen["call"] == ("postiz", "pz_live_key_987")
    msg = _flashes(r.text)
    assert "Postiz key checked and saved" in msg and "TikTok (tt-123)" in msg and "YouTube (yt-456)" in msg
    assert "Settings page" in msg  # backend is still dry_run
    assert "pz_live_key_987" not in r.text
    env = _env(demo_root)
    assert "POSTIZ_API_KEY=pz_live_key_987" in env and "POSTIZ_INTEGRATION_TIKTOK=tt-123" in env
    assert 'action="/connections/key/postiz"' in r.text and "Replace" in r.text


def test_save_key_errors_are_redacted(client, csrf, demo_root, monkeypatch):
    before = _env(demo_root)

    def failing(tool, key, settings, http=None):
        raise PublishError(f"apify failed: HTTP 401 token={key} body mentions {key}")

    monkeypatch.setattr(connect, "validate_key", failing)
    token = csrf("/connections")
    r = client.post("/connections/key/apify", data={"csrf": token, "key": "apify_api_SECRETSECRET"})
    assert r.status_code == 200
    msg = _flashes(r.text)
    assert "key not saved" in msg and "HTTP 401" in msg and "apify_api_SECRETSECRET" not in r.text

    def offline(tool, key, settings, http=None):
        raise requests.ConnectionError("Max retries exceeded with url: /v2/users/me")

    monkeypatch.setattr(connect, "validate_key", offline)
    r = client.post("/connections/key/anthropic", data={"csrf": token, "key": "sk-ant-abcdef"})
    assert "Anthropic (Claude re-ranker): key not saved" in _flashes(r.text) and "sk-ant-abcdef" not in r.text
    assert _env(demo_root) == before


def test_save_key_blank_spaces_and_unknown_tool(client, csrf, demo_root):
    before = _env(demo_root)
    token = csrf("/connections")
    r = client.post("/connections/key/youtube-key", data={"csrf": token, "key": "  "})
    assert "nothing saved" in _flashes(r.text)
    r = client.post("/connections/key/postiz", data={"csrf": token, "key": "two words"})
    assert "doesn't look like a key" in _flashes(r.text) and "two words" not in r.text
    r = client.post("/connections/key/postiz", data={"csrf": token, "key": "k" * 600})
    assert "too long" in _flashes(r.text)
    r = client.post("/connections/key/evil", data={"csrf": token, "key": "abc"})
    assert r.status_code == 200 and "Unknown tool" in _flashes(r.text)
    assert _env(demo_root) == before


def test_posts_require_csrf(client, demo_root):
    before = _env(demo_root)
    assert client.post("/connections/app-credentials", data={"YOUTUBE_CLIENT_ID": "abc"}).status_code == 403
    assert client.post("/connections/key/apify", data={"key": "abc"}).status_code == 403
    assert client.post("/connections/key/apify", data={"key": "abc", "csrf": "forged"}).status_code == 403
    assert _env(demo_root) == before
