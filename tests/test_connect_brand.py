import base64
import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request

import pytest

from tests.test_publishers import FakeResp, FakeSession
from vv88 import brand as b
from vv88 import connect as c
from vv88.config import Settings, load_dotenv
from vv88.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


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


def test_local_callback_captures_code_and_state():
    port = 18788
    out = {}

    def run():
        out["r"] = c.wait_for_callback(port, timeout=10)

    t = threading.Thread(target=run)
    t.start()
    for _ in range(50):
        try:
            body = urllib.request.urlopen(f"http://127.0.0.1:{port}/callback/?code=abc123&state=s1", timeout=2).read()
            break
        except OSError:
            time.sleep(0.05)
    t.join(5)
    assert out["r"].code == "abc123" and out["r"].state == "s1"
    assert b"connected" in body


def test_state_mismatch_is_rejected(monkeypatch):
    monkeypatch.setattr(c, "wait_for_callback", lambda port: c.CallbackResult(code="x", state="evil"))
    with pytest.raises(c.PublishError, match="state mismatch"):
        c._authorize("https://example.com", 1, "good", open_browser=False)


def test_update_env_file_replaces_and_appends(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# keep me\nAPIFY_TOKEN=   # comment\nOTHER=1\n")
    c.update_env_file({"APIFY_TOKEN": "tok", "NEW_KEY": "v"}, env)
    text = env.read_text()
    assert "# keep me" in text and "APIFY_TOKEN=tok" in text and "OTHER=1" in text and "NEW_KEY=v" in text
    assert oct(env.stat().st_mode & 0o777) == "0o600"


def test_dotenv_ignores_inline_comments(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('EMPTY_ONE=   # just a note\nSET_ONE=abc  # note\nQUOTED="a # b"\n')
    for k in ("EMPTY_ONE", "SET_ONE", "QUOTED"):
        monkeypatch.delenv(k, raising=False)
    load_dotenv(env)
    assert os.environ["EMPTY_ONE"] == "" and os.environ["SET_ONE"] == "abc" and os.environ["QUOTED"] == "a # b"


def test_connect_x_saves_rotating_tokens(monkeypatch, store, tmp_path):
    monkeypatch.setattr(c, "_authorize", lambda url, port, state, open_browser: "code1")
    http = FakeSession([
        ("POST", "oauth2/token", FakeResp(body={"access_token": "at", "refresh_token": "rt", "expires_in": 7200})),
        ("GET", "users/me", FakeResp(body={"data": {"username": "artkdclips"}})),
    ])
    s = Settings()
    s.publishing.direct.x_client_id = "cid"
    res = c.connect_platform("x", s, store, open_browser=False, env_path=tmp_path / ".env", http=http)
    assert res["account"] == "@artkdclips"
    assert store.kv_get("oauth:x")["refresh_token"] == "rt"
    assert "X_REFRESH_TOKEN=rt" in (tmp_path / ".env").read_text()
    token_call = http.calls[0][2]["data"]
    assert token_call["grant_type"] == "authorization_code" and token_call["code_verifier"]


def test_connect_instagram_finds_professional_account(monkeypatch, store, tmp_path):
    monkeypatch.setattr(c, "_authorize", lambda url, port, state, open_browser: "code1")
    http = FakeSession([
        ("GET", "oauth/access_token", lambda url, kw: FakeResp(body={"access_token": "long" if kw["params"].get(
            "grant_type") else "short", "expires_in": 5_184_000})),
        ("GET", "me/accounts", FakeResp(body={"data": [{"name": "Page", "instagram_business_account": {
            "id": "1784", "username": "art0kd"}}]})),
    ])
    s = Settings()
    s.publishing.direct.facebook_app_id, s.publishing.direct.facebook_app_secret = "app", "sec"
    res = c.connect_platform("instagram", s, store, open_browser=False, env_path=tmp_path / ".env", http=http)
    assert res["account"] == "@art0kd" and res["ig_id"] == "1784"
    env = (tmp_path / ".env").read_text()
    assert "INSTAGRAM_ACCESS_TOKEN=long" in env and "INSTAGRAM_USER_ID=1784" in env
    status = dict((n, (ok, d)) for n, ok, d in c.link_status(load_settings_with(env, tmp_path), store))
    assert "expires in 60d" in status["instagram"][1]


def load_settings_with(env_text, tmp_path):
    s = Settings()
    for line in env_text.splitlines():
        k, _, v = line.partition("=")
        if k == "INSTAGRAM_ACCESS_TOKEN":
            s.publishing.direct.instagram_access_token = v
    return s


def test_connect_postiz_maps_integrations(tmp_path):
    http = FakeSession([("GET", "/integrations", FakeResp(body=[
        {"id": "i-tt", "identifier": "tiktok", "name": "artkd"},
        {"id": "i-ig", "identifier": "instagram-standalone", "name": "art0kd"},
        {"id": "i-x", "identifier": "x", "name": "x", "disabled": True},
    ]))])
    res = c.connect_key("postiz", Settings(), env_path=tmp_path / ".env", key="pz", http=http)
    env = (tmp_path / ".env").read_text()
    assert "POSTIZ_INTEGRATION_TIKTOK=i-tt" in env and "POSTIZ_INTEGRATION_INSTAGRAM=i-ig" in env
    assert "POSTIZ_INTEGRATION_X" not in env  # disabled integrations are skipped
    assert res["integrations"] == {"tiktok": "i-tt", "instagram": "i-ig"}


# ---- brand kit ---------------------------------------------------------------------------
def make_kit(tmp_path, **overrides):
    root = tmp_path / "brand"
    root.mkdir(exist_ok=True)
    (root / "avatar_1024.png").write_bytes(b"png-bytes")
    data = {
        "concept_name": "test", "unified_handle": "artkdclips", "handle_fallbacks": ["artkd.clips"],
        "display_names": {"tiktok": "artkd clips", "instagram": "artkd clips", "x": "artkd clips",
                          "youtube": "artkd signal"},
        "bios": {"tiktok": "Best moments, clipped daily.", "instagram": "Best moments from long videos.",
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
            "channel": {"title": "artkd signal", "country": "KE", "description": "old", "unsubscribedTrailer": "v9"},
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
    assert "Best moments, clipped daily." in text and "@artkdclips" in text
    assert res["youtube"]["ok"]
