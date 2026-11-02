"""Zernio publisher against a fake HTTP session shaped like docs.zernio.com (presign -> PUT -> POST /posts)."""

import json

import pytest

from vv88 import connect
from vv88.config import ZernioConfig, load_settings
from vv88.models import PostContent
from vv88.publishing.base import PublishError
from vv88.publishing.zernio import ZernioPublisher, list_accounts

KEY = "sk_" + "b" * 64
BASE = "https://zernio.com/api/v1"
UPLOAD = "https://storage.example.com/upload/abc?X-Amz-Signature=secretsig"
PUBLIC = "https://media.zernio.com/temp/abc.mp4"


class Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, post_status=201, post_body=None, put_status=200):
        self.calls = []
        self.post_status, self.put_status = post_status, put_status
        self.post_body = post_body or {"post": {"_id": "p1", "status": "published", "platforms": [
            {"platform": "tiktok", "status": "published", "platformPostUrl": "https://www.tiktok.com/@me/video/123"}]}}

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url, json, headers))
        if url.endswith("/media/presign"):
            return Resp(200, {"uploadUrl": UPLOAD, "publicUrl": PUBLIC, "key": "k", "expiresIn": 3600})
        return Resp(self.post_status, self.post_body)

    def put(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("PUT", url, data.read() if hasattr(data, "read") else data, headers))
        return Resp(self.put_status)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, None, headers))
        return Resp(200, {"accounts": [
            {"_id": "acc_tt", "platform": "tiktok", "username": "allviralvideozz", "isActive": True},
            {"_id": "acc_ig", "platform": "instagram", "username": "allviralvideozz", "isActive": True},
            {"_id": "acc_old", "platform": "instagram", "username": "old", "isActive": False},
            {"_id": "acc_x", "platform": "twitter", "username": "allviral", "isActive": True},
            {"_id": "acc_li", "platform": "linkedin", "username": "co", "isActive": True},
        ]})


def _cfg(**accounts):
    return ZernioConfig(api_url=BASE, api_key=KEY, accounts={"tiktok": "acc_tt", "instagram": "acc_ig",
                                                              "youtube": "acc_yt", "x": "acc_x", **accounts})


def _video(tmp_path):
    p = tmp_path / "clip1.mp4"
    p.write_bytes(b"video-bytes")
    return str(p)


def _content(platform, **kw):
    return PostContent(platform, kw.pop("caption", "Watch this #fyp"), approved=kw.pop("approved", True), **kw)


def test_tiktok_publish_flow(tmp_path):
    http = FakeSession()
    res = ZernioPublisher("tiktok", _cfg(), http).publish(_video(tmp_path), _content("tiktok", paid_partnership=True))
    presign, put, create = http.calls
    assert presign[1] == f"{BASE}/media/presign" and presign[2]["contentType"] == "video/mp4"
    assert presign[2]["size"] == len(b"video-bytes") and presign[3]["Authorization"] == f"Bearer {KEY}"
    assert put[0] == "PUT" and put[1] == UPLOAD and put[2] == b"video-bytes"
    assert "Authorization" not in put[3] and put[3]["Content-Type"] == "video/mp4"
    body, headers = create[2], create[3]
    assert create[1] == f"{BASE}/posts" and body["publishNow"] is True
    assert body["mediaItems"] == [{"type": "video", "url": PUBLIC}]
    assert body["platforms"] == [{"platform": "tiktok", "accountId": "acc_tt"}]
    tt = body["tiktokSettings"]
    assert tt["privacy_level"] == "PUBLIC_TO_EVERYONE" and tt["content_preview_confirmed"] is True
    assert tt["express_consent_given"] is True and tt["commercialContentType"] == "brand_content"
    assert headers["Idempotency-Key"]
    assert res.ok and res.remote_id == "zernio:p1" and res.url == "https://www.tiktok.com/@me/video/123"


def test_same_post_retried_sends_the_same_idempotency_key(tmp_path):
    a, b = FakeSession(), FakeSession()
    video = _video(tmp_path)
    ZernioPublisher("tiktok", _cfg(), a).publish(video, _content("tiktok"))
    ZernioPublisher("tiktok", _cfg(), b).publish(video, _content("tiktok"))
    ZernioPublisher("tiktok", _cfg(), b).publish(video, _content("tiktok", caption="different"))
    keys = [c[3]["Idempotency-Key"] for c in a.calls + b.calls if c[1].endswith("/posts")]
    assert keys[0] == keys[1] != keys[2]


def test_tiktok_needs_a_person_to_have_approved_the_post(tmp_path):
    http = FakeSession()
    with pytest.raises(PublishError, match="approve"):
        ZernioPublisher("tiktok", _cfg(), http).publish(_video(tmp_path), _content("tiktok", approved=False))
    assert http.calls == []                                   # nothing uploaded


def test_platform_specific_fields(tmp_path):
    video = _video(tmp_path)
    ig = FakeSession(post_body={"post": {"_id": "p", "status": "published", "platforms": [
        {"platform": "instagram", "status": "published", "platformPostUrl": "https://www.instagram.com/reel/Cx/"}]}})
    ZernioPublisher("instagram", _cfg(), ig).publish(video, _content("instagram"))
    entry = ig.calls[-1][2]["platforms"][0]
    assert entry == {"platform": "instagram", "accountId": "acc_ig", "platformSpecificData": {"shareToFeed": True}}
    assert "tiktokSettings" not in ig.calls[-1][2]
    yt = FakeSession()
    ZernioPublisher("youtube", _cfg(), yt).publish(video, _content("youtube", title="My Short #Shorts",
                                                                   tags=["clips", "pod"]))
    body = yt.calls[-1][2]
    assert body["platforms"][0]["platformSpecificData"]["title"] == "My Short #Shorts"
    assert body["platforms"][0]["platformSpecificData"]["visibility"] == "public" and body["tags"] == ["clips", "pod"]
    x = FakeSession()
    ZernioPublisher("x", _cfg(), x).publish(video, _content("x", paid_partnership=True))
    entry = x.calls[-1][2]["platforms"][0]
    assert entry["platform"] == "twitter" and entry["platformSpecificData"] == {"paidPartnership": True}


def test_rejected_post_raises_with_the_platform_reason(tmp_path):
    body = {"message": "Post created but publishing failed", "post": {"_id": "p", "status": "failed", "platforms": [
        {"platform": "tiktok", "status": "failed", "errorMessage": "Video too short", "errorCategory": "user_content"}]}}
    with pytest.raises(PublishError, match="Video too short") as e:
        ZernioPublisher("tiktok", _cfg(), FakeSession(207, body)).publish(_video(tmp_path), _content("tiktok"))
    assert "rate limit" not in str(e.value).lower()


def test_throttled_post_says_rate_limit_so_it_is_retried_later(tmp_path):
    body = {"post": {"_id": "p", "status": "failed", "platforms": [
        {"platform": "tiktok", "status": "failed", "errorMessage": "Too many posts", "errorCategory": "quota_exhausted"}]}}
    with pytest.raises(PublishError, match="rate limit"):
        ZernioPublisher("tiktok", _cfg(), FakeSession(207, body)).publish(_video(tmp_path), _content("tiktok"))


def test_http_errors_and_failed_upload_never_leak_the_signed_url(tmp_path):
    with pytest.raises(PublishError) as e:
        ZernioPublisher("tiktok", _cfg(), FakeSession(put_status=403)).publish(_video(tmp_path), _content("tiktok"))
    assert "secretsig" not in str(e.value) and "HTTP 403" in str(e.value)
    with pytest.raises(PublishError, match="HTTP 409"):
        ZernioPublisher("tiktok", _cfg(), FakeSession(409, {"error": "This exact content is already scheduled"})
                        ).publish(_video(tmp_path), _content("tiktok"))


def test_missing_key_or_account(tmp_path):
    with pytest.raises(PublishError, match="ZERNIO_API_KEY"):
        ZernioPublisher("tiktok", ZernioConfig(api_url=BASE, api_key="", accounts={"tiktok": "a"}))
    with pytest.raises(PublishError, match="no Zernio instagram account"):
        ZernioPublisher("instagram", _cfg(instagram=""))


def test_list_accounts_and_connect_key_save_the_account_ids(tmp_path, monkeypatch):
    for k in ("ZERNIO_API_KEY", *(f"ZERNIO_ACCOUNT_{p}" for p in ("TIKTOK", "INSTAGRAM", "YOUTUBE", "X"))):
        monkeypatch.delenv(k, raising=False)
    http = FakeSession()
    assert len(list_accounts(BASE, KEY, http)) == 5
    settings = load_settings(tmp_path / "missing.yaml", overrides={"data_dir": str(tmp_path / "data")})
    env = tmp_path / ".env"
    info = connect.connect_key("zernio", settings, env_path=env, key=KEY, http=http)
    assert info["accounts"] == {"tiktok": "acc_tt", "instagram": "acc_ig", "x": "acc_x"}  # inactive + linkedin skipped
    text = env.read_text()
    assert f"ZERNIO_API_KEY={KEY}" in text and "ZERNIO_ACCOUNT_TIKTOK=acc_tt" in text
    assert "ZERNIO_ACCOUNT_INSTAGRAM=acc_ig" in text and "acc_old" not in text
    assert http.calls[0][3]["Authorization"] == f"Bearer {KEY}"
