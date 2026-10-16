import base64
import hashlib
import json
import os
import socket
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from tests.test_publishers import FakeResp, FakeSession
from vv88 import brand as b
from vv88 import connect as c
from vv88.config import Settings, load_dotenv
from vv88.publishing.base import PublishError, redact
from vv88.store import Store


@pytest.fixture(autouse=True)
def _restore_environ():
    """update_env_file mirrors values into os.environ; keep tests from leaking into each other."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ---- OAuth plumbing ----------------------------------------------------------------------
def test_pkce_s256_base64url_and_tiktok_hex():
    v, ch = c.pkce_pair()
    assert 43 <= len(v) <= 128
    assert ch == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    v2, ch2 = c.pkce_pair(hex_challenge=True)
    assert ch2 == hashlib.sha256(v2.encode()).hexdigest()


def test_authorize_urls_carry_scopes_pkce_and_offline_access():
    yt = urllib.parse.parse_qs(urllib.parse.urlparse(c.youtube_authorize_url("cid", "http://127.0.0.1:8788/callback/",
                                                                             "st", "chal")).query)
    assert yt["access_type"] == ["offline"] and yt["prompt"] == ["consent"]
    assert "https://www.googleapis.com/auth/youtube" in yt["scope"][0].split()
    tt = urllib.parse.parse_qs(urllib.parse.urlparse(c.tiktok_authorize_url("ck", "r", "st", "ch")).query)
    assert tt["client_key"] == ["ck"] and "video.publish" in tt["scope"][0].split(",")
    xq = urllib.parse.parse_qs(urllib.parse.urlparse(c.x_authorize_url("cid", "r", "st", "ch")).query)
    assert "offline.access" in xq["scope"][0].split() and xq["code_challenge_method"] == ["S256"]
    fb = urllib.parse.parse_qs(urllib.parse.urlparse(c.facebook_authorize_url("app", "r", "st", "v23.0", "cfg1")).query)
    assert fb["config_id"] == ["cfg1"] and "scope" not in fb


def test_callback_server_ignores_foreign_requests_escapes_errors_and_survives_idle_sockets():
    port = _free_port()
    server = c.CallbackServer(port, "s1")  # bound before any browser is opened
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", server.wait(timeout=10)))
    t.start()
    # An idle connection that never sends a request must not block the real redirect.
    idle = socket.create_connection(("127.0.0.1", port))
    # A random page hitting /callback with the wrong state is refused and does not end the flow.
    status, body = _get(f"http://127.0.0.1:{port}/callback/?error=%3Cscript%3Ealert(1)%3C/script%3E&state=evil")
    assert status == 400 and b"<script>" not in body
    assert not server.done.is_set()
    status, body = _get(f"http://127.0.0.1:{port}/callback/?code=abc123&state=s1")
    assert status == 200 and b"connected" in body
    t0 = time.monotonic()
    t.join(5)
    idle.close()
    assert time.monotonic() - t0 < 5
    assert out["r"].code == "abc123" and out["r"].state == "s1"


def test_callback_error_text_is_escaped_and_control_chars_stripped():
    port = _free_port()
    server = c.CallbackServer(port, "s1")
    t = threading.Thread(target=lambda: server.wait(timeout=10))
    t.start()
    status, body = _get(f"http://127.0.0.1:{port}/callback/?state=s1&error_description="
                        + urllib.parse.quote("<b>no</b>\x1b[31mred"))
    t.join(5)
    assert b"<b>" not in body and b"&lt;b&gt;" in body
    assert "\x1b" not in server.result.error


def test_busy_port_fails_before_the_browser_opens():
    port = _free_port()
    first = c.CallbackServer(port, "a")
    try:
        with pytest.raises(PublishError, match="busy"):
            c.CallbackServer(port, "b")
    finally:
        first.close()


def test_pasted_redirect_checks_state():
    assert c.parse_pasted_redirect("https://example.com/cb?code=z9&state=good", "good") == "z9"
    with pytest.raises(PublishError, match="state mismatch"):
        c.parse_pasted_redirect("https://example.com/cb?code=z9&state=evil", "good")


# ---- secrets on disk ---------------------------------------------------------------------
def test_update_env_file_replaces_appends_is_private_and_mirrors_environ(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# keep me\nAPIFY_TOKEN=   # comment\nOTHER=1\n")
    env.chmod(0o644)
    c.update_env_file({"APIFY_TOKEN": "tok", "NEW_KEY": "v"}, env)
    text = env.read_text()
    assert "# keep me" in text and "APIFY_TOKEN=tok" in text and "OTHER=1" in text and "NEW_KEY=v" in text
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert os.environ["APIFY_TOKEN"] == "tok"  # a stale exported value can't shadow the fresh one


def test_update_env_file_rejects_line_breaks(tmp_path):
    with pytest.raises(ValueError):
        c.update_env_file({"X_ACCESS_TOKEN": "abc\nEVIL=1"}, tmp_path / ".env")
    assert not (tmp_path / ".env").exists()


def test_token_database_is_private(tmp_path):
    Store(tmp_path / "data" / "vv88.db")
    assert stat.S_IMODE((tmp_path / "data" / "vv88.db").stat().st_mode) == 0o600


def test_redact_masks_tokens_in_urls_and_headers():
    msg = "Max retries exceeded with url: /v2/users/me?token=apify_api_SECRET&x=1 Authorization: Bearer abcdefghijk"
    out = redact(msg)
    assert "SECRET" not in out and "abcdefghijk" not in out and "x=1" in out


def test_api_keys_travel_in_headers_not_urls():
    http = FakeSession([
        ("GET", "api.apify.com", FakeResp(body={"data": {"username": "u"}})),
        ("GET", "videoCategories", FakeResp(body={"items": []})),
    ])
    c.validate_key("apify", "apify_api_SECRET", Settings(), http)
    c.validate_key("youtube-key", "AIzaSECRET", Settings(), http)
    for _, url, kw in http.calls:
        assert "SECRET" not in url and "SECRET" not in json.dumps(kw.get("params") or {})
    assert http.calls[0][2]["headers"]["Authorization"] == "Bearer apify_api_SECRET"
    assert http.calls[1][2]["headers"]["X-goog-api-key"] == "AIzaSECRET"


def test_dotenv_ignores_inline_comments(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('EMPTY_ONE=   # just a note\nSET_ONE=abc  # note\nQUOTED="a # b"\n')
    for k in ("EMPTY_ONE", "SET_ONE", "QUOTED"):
        monkeypatch.delenv(k, raising=False)
    load_dotenv(env)
    assert os.environ["EMPTY_ONE"] == "" and os.environ["SET_ONE"] == "abc" and os.environ["QUOTED"] == "a # b"


# ---- platform flows ----------------------------------------------------------------------
def _no_browser(monkeypatch, code="code1"):
    monkeypatch.setattr(c, "_authorize", lambda *a, **k: code)


def test_connect_x_saves_rotating_tokens(monkeypatch, store, tmp_path):
    _no_browser(monkeypatch)
    http = FakeSession([
        ("POST", "oauth2/token", FakeResp(body={"access_token": "at", "refresh_token": "rt", "expires_in": 7200})),
        ("GET", "users/me", FakeResp(body={"data": {"username": "exampleclips"}})),
    ])
    s = Settings()
    s.publishing.direct.x_client_id = "cid"
    res = c.connect_platform("x", s, store, open_browser=False, env_path=tmp_path / ".env", http=http)
    assert res["account"] == "@exampleclips"
    assert store.kv_get("oauth:x")["refresh_token"] == "rt"
    assert "X_REFRESH_TOKEN=rt" in (tmp_path / ".env").read_text()
    token_call = http.calls[0][2]["data"]
    assert token_call["grant_type"] == "authorization_code" and token_call["code_verifier"]


def test_connect_x_name_lookup_failure_is_only_a_warning(monkeypatch, store, tmp_path):
    _no_browser(monkeypatch)
    http = FakeSession([
        ("POST", "oauth2/token", FakeResp(body={"access_token": "at", "refresh_token": "rt"})),
        ("GET", "users/me", FakeResp(status=429, body={"title": "Too Many Requests"})),
    ])
    s = Settings()
    s.publishing.direct.x_client_id = "cid"
    res = c.connect_platform("x", s, store, open_browser=False, env_path=tmp_path / ".env", http=http)
    assert "warning" in res and store.kv_get("oauth:x")["refresh_token"] == "rt"


def test_connect_youtube_without_channel_saves_nothing(monkeypatch, store, tmp_path):
    _no_browser(monkeypatch)
    http = FakeSession([
        ("POST", "oauth2.googleapis.com/token", FakeResp(body={"access_token": "a", "refresh_token": "r",
                                                               "scope": "https://www.googleapis.com/auth/youtube"})),
        ("GET", "youtube/v3/channels", FakeResp(body={"items": []})),
    ])
    s = Settings()
    s.publishing.direct.youtube_client_id, s.publishing.direct.youtube_client_secret = "id", "sec"
    with pytest.raises(PublishError, match="no YouTube channel"):
        c.connect_platform("youtube", s, store, open_browser=False, env_path=tmp_path / ".env", http=http)
    assert not (tmp_path / ".env").exists() and store.kv_get("oauth:youtube") is None


def test_connect_instagram_follows_page_pagination_and_warns_before_expiry(monkeypatch, store, tmp_path):
    _no_browser(monkeypatch)
    http = FakeSession([
        ("GET", "oauth/access_token", lambda url, kw: FakeResp(body={"access_token": "long" if kw["params"].get(
            "grant_type") else "short", "expires_in": 5_184_000})),
        ("GET", "me/accounts?after=p2", FakeResp(body={"data": [{"name": "Page2", "instagram_business_account": {
            "id": "1784", "username": "example_ig"}}]})),
        ("GET", "me/accounts", FakeResp(body={"data": [{"name": "Page without IG"}],
                                             "paging": {"next": "https://graph.facebook.com/v23.0/me/accounts?after=p2"}})),
    ])
    s = Settings()
    s.publishing.direct.facebook_app_id, s.publishing.direct.facebook_app_secret = "app", "sec"
    res = c.connect_platform("instagram", s, store, open_browser=False, env_path=tmp_path / ".env", http=http)
    assert res["account"] == "@example_ig" and res["ig_id"] == "1784"
    env = (tmp_path / ".env").read_text()
    assert "INSTAGRAM_ACCESS_TOKEN=long" in env and "INSTAGRAM_USER_ID=1784" in env
    accounts_call = next(call for call in http.calls if "me/accounts" in call[1])
    assert accounts_call[2]["headers"]["Authorization"] == "Bearer long"

    s.publishing.direct.instagram_access_token = "long"
    rows = {r.name: r for r in c.link_status(s, store)}
    assert rows["instagram"].linked and not rows["instagram"].warning and "60d" in rows["instagram"].detail
    soon = {r.name: r for r in c.link_status(s, store, now=time.time() + 55 * 86400)}
    assert "renew" in soon["instagram"].warning
    later = {r.name: r for r in c.link_status(s, store, now=time.time() + 61 * 86400)}
    assert not later["instagram"].linked and "expired" in later["instagram"].warning


def test_connect_postiz_maps_integrations(tmp_path):
    http = FakeSession([("GET", "/integrations", FakeResp(body=[
        {"id": "i-tt", "identifier": "tiktok", "name": "example"},
        {"id": "i-ig", "identifier": "instagram-standalone", "name": "example_ig"},
        {"id": "i-x", "identifier": "x", "name": "x", "disabled": True},
        {"id": "bad\nid", "identifier": "youtube", "name": "y"},
    ]))])
    res = c.connect_key("postiz", Settings(), env_path=tmp_path / ".env", key="pz", http=http)
    env = (tmp_path / ".env").read_text()
    assert "POSTIZ_INTEGRATION_TIKTOK=i-tt" in env and "POSTIZ_INTEGRATION_INSTAGRAM=i-ig" in env
    assert "POSTIZ_INTEGRATION_X" not in env  # disabled integrations are skipped
    assert "POSTIZ_INTEGRATION_YOUTUBE" not in env  # malformed ids are never written
    assert res["integrations"] == {"tiktok": "i-tt", "instagram": "i-ig"}


def test_empty_key_is_a_skip_not_a_failure(tmp_path):
    with pytest.raises(c.Skipped):
        c.connect_key("apify", Settings(), env_path=tmp_path / ".env", key="")


# ---- brand kit ---------------------------------------------------------------------------
def make_kit(tmp_path, **overrides):
    root = tmp_path / "brand"
    root.mkdir(exist_ok=True)
    (root / "avatar_1024.png").write_bytes(b"png-bytes")
    data = {
        "concept_name": "test", "unified_handle": "exampleclips", "handle_fallbacks": ["example_clips"],
        "display_names": {"tiktok": "Example Clips", "instagram": "Example Clips", "x": "Example Clips",
                          "youtube": "Example Channel"},
        "bios": {"tiktok": "Best moments, clipped daily.", "instagram": "Best moments\nfrom long videos.",
                 "x": "Clips worth your time.", "youtube_description": "Daily clips."},
        "youtube_keywords": ["clips", "podcast clips"],
    }
    data.update(overrides)
    (root / "brand.json").write_text(json.dumps(data))
    return b.load_kit(root)


def test_kit_validation_catches_overlong_bios(tmp_path):
    kit = make_kit(tmp_path, bios={"tiktok": "x" * 81, "instagram": "ok", "x": "ok", "youtube_description": "ok"})
    assert any("tiktok bio is 81" in p for p in kit.problems)
    with pytest.raises(ValueError):
        b.approve(kit, Store(tmp_path / "s.db"))


@pytest.mark.parametrize("handle", ["example.clips", "exämple", "a" * 16, "bad handle"])
def test_handles_must_satisfy_x_rules(tmp_path, handle):
    kit = make_kit(tmp_path, handle_fallbacks=[handle])
    assert any("fallback handle" in p for p in kit.problems)


def test_apply_requires_approval_of_exact_kit(tmp_path, store):
    kit = make_kit(tmp_path)
    with pytest.raises(PermissionError):
        b.apply(kit, store)
    b.approve(kit, store)
    assert b.approval(kit, store)
    (kit.root / "avatar_1024.png").write_bytes(b"different picture")
    kit2 = b.load_kit(kit.root)
    assert b.approval(kit2, store) is None  # changing the image after approval revokes it
    with pytest.raises(PermissionError):
        b.apply(kit2, store)


def test_apply_updates_youtube_writable_fields_only_and_writes_checklist(tmp_path, store):
    kit = make_kit(tmp_path)
    b.approve(kit, store)
    http = FakeSession([
        ("GET", "youtube/v3/channels", FakeResp(body={"items": [{"id": "UC1", "brandingSettings": {
            "channel": {"title": "Example Channel", "country": "KE", "description": "old", "unsubscribedTrailer": "v9"},
            "image": {"bannerExternalUrl": "https://banner"}}}]})),
        ("PUT", "youtube/v3/channels", FakeResp(body={"id": "UC1"})),
    ])
    res = b.apply(kit, store, youtube_access_token="tok", http=http)
    body = http.calls[1][2]["json"]
    ch = body["brandingSettings"]["channel"]
    assert ch["description"] == "Daily clips." and ch["country"] == "KE" and ch["unsubscribedTrailer"] == "v9"
    assert "title" not in ch and "image" not in body["brandingSettings"]
    assert ch["keywords"] == 'clips "podcast clips"'
    text = (kit.root / "APPLY_CHECKLIST.md").read_text()
    assert "Best moments, clipped daily." in text and "@exampleclips" in text
    assert "  Best moments\n  from long videos." in text  # multi-line bios stay inside the fence
    assert 'clips "podcast clips"' in text
    assert res["youtube"]["ok"]


def test_youtube_failure_still_writes_checklist(tmp_path, store):
    kit = make_kit(tmp_path)
    b.approve(kit, store)
    http = FakeSession([("GET", "youtube/v3/channels", FakeResp(status=403, body={"error": "insufficientPermissions"}))])
    res = b.apply(kit, store, youtube_access_token="tok", http=http)
    assert res["youtube"]["ok"] is False and "403" in res["youtube"]["error"]
    assert (kit.root / "APPLY_CHECKLIST.md").exists()


def test_missing_channel_edit_scope_skips_youtube(tmp_path, store):
    kit = make_kit(tmp_path)
    b.approve(kit, store)
    res = b.apply(kit, store, youtube_access_token="tok",
                  youtube_scopes=["https://www.googleapis.com/auth/youtube.upload"])
    assert "skipped" in res["youtube"] and (kit.root / "APPLY_CHECKLIST.md").exists()


def test_cli_approve_requires_the_shown_fingerprint(tmp_path, monkeypatch, capsys):
    from vv88.cli import main

    monkeypatch.chdir(tmp_path)
    kit = make_kit(tmp_path)
    (tmp_path / "config.yaml").write_text(f"data_dir: {tmp_path / 'data'}\n")
    assert main(["-c", "config.yaml", "brand", "approve", "--dir", str(kit.root), "--fingerprint", "wrong"]) == 1
    assert main(["-c", "config.yaml", "brand", "approve", "--dir", str(kit.root),
                 "--fingerprint", kit.fingerprint()]) == 0
    assert "approved fingerprint" in capsys.readouterr().out
