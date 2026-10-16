"""Publisher request flows against a scripted fake HTTP session (no network)."""

import json

import pytest

from vv88.config import DirectAPIConfig, PostizConfig
from vv88.models import PostContent
from vv88.publishing.base import PublishError
from vv88.publishing.instagram import InstagramPublisher
from vv88.publishing.postiz import PostizPublisher
from vv88.publishing.tiktok import TikTokPublisher
from vv88.publishing.x import XPublisher
from vv88.publishing.youtube import YouTubePublisher
from vv88.store import Store


class FakeResp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, routes):
        self.routes = routes  # list of (method, substring, FakeResp | callable)
        self.calls = []

    def _handle(self, method, url, **kw):
        if "data" in kw and hasattr(kw["data"], "read"):
            kw["data"] = kw["data"].read()
        self.calls.append((method, url, kw))
        for m, sub, resp in self.routes:
            if m == method and sub in url:
                return resp(url, kw) if callable(resp) else resp
        raise AssertionError(f"unexpected {method} {url}")

    def get(self, url, **kw):
        return self._handle("GET", url, **kw)

    def post(self, url, **kw):
        return self._handle("POST", url, **kw)

    def put(self, url, **kw):
        return self._handle("PUT", url, **kw)


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00" * 1000)
    return str(p)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


CONTENT = PostContent("x", "caption #tag", title="Title #Shorts", tags=["tag"], paid_partnership=True, cover_ms=1200)


def test_youtube_resumable_upload_sets_paid_placement(video):
    s = FakeSession([
        ("POST", "oauth2.googleapis.com/token", FakeResp(body={"access_token": "tok"})),
        ("POST", "upload/youtube/v3/videos", FakeResp(headers={"Location": "https://upload.example/session"})),
        ("PUT", "upload.example/session", FakeResp(body={"id": "vid123", "status": {"uploadStatus": "uploaded"}})),
    ])
    cfg = DirectAPIConfig(youtube_client_id="id", youtube_client_secret="sec", youtube_refresh_token="rt")
    res = YouTubePublisher(cfg, session=s).publish(video, CONTENT)
    assert res.remote_id == "vid123" and res.url.endswith("/shorts/vid123")
    init = s.calls[1][2]
    assert "paidProductPlacementDetails" in init["params"]["part"]
    assert init["json"]["status"]["selfDeclaredMadeForKids"] is False


def test_instagram_reel_flow(video):
    s = FakeSession([
        ("GET", "content_publishing_limit", FakeResp(body={"data": [{"quota_usage": 3, "config": {"quota_total": 100}}]})),
        ("POST", "/123/media_publish", FakeResp(body={"id": "media9"})),
        ("POST", "/123/media", FakeResp(body={"id": "cont1"})),
        ("POST", "rupload.facebook.com", FakeResp(body={"success": True})),
        ("GET", "/cont1", FakeResp(body={"status_code": "FINISHED"})),
        ("GET", "/media9", FakeResp(body={"permalink": "https://instagram.com/reel/abc"})),
    ])
    cfg = DirectAPIConfig(instagram_user_id="123", instagram_access_token="tok")
    res = InstagramPublisher(cfg, session=s, poll_every=0).publish(video, CONTENT)
    assert res.remote_id == "media9" and "instagram.com/reel" in res.url
    create = next(c for c in s.calls if c[1].endswith("/123/media"))
    assert create[2]["data"]["media_type"] == "REELS" and create[2]["data"]["thumb_offset"] == "1200"
    upload = next(c for c in s.calls if "rupload" in c[1])
    assert upload[2]["headers"]["file_size"] == "1000"


def test_instagram_quota_exhausted(video):
    s = FakeSession([
        ("GET", "content_publishing_limit", FakeResp(body={"data": [{"quota_usage": 100, "config": {"quota_total": 100}}]})),
    ])
    with pytest.raises(PublishError, match="quota"):
        InstagramPublisher(DirectAPIConfig(instagram_user_id="123", instagram_access_token="t"), session=s).publish(
            video, CONTENT)


def test_tiktok_unaudited_app_falls_back_to_private_without_branding(video, store):
    s = FakeSession([
        ("POST", "creator_info/query", FakeResp(body={"data": {"privacy_level_options": ["SELF_ONLY"]},
                                                       "error": {"code": "ok"}})),
        ("POST", "video/init", FakeResp(body={"data": {"publish_id": "p1", "upload_url": "https://up.tiktok/x"},
                                              "error": {"code": "ok"}})),
        ("PUT", "up.tiktok", FakeResp(status=201)),
        ("POST", "status/fetch", FakeResp(body={"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}})),
    ])
    cfg = DirectAPIConfig(tiktok_access_token="tok")
    res = TikTokPublisher(cfg, store, session=s, poll_every=0).publish(video, CONTENT)
    assert res.ok and res.status.endswith("SELF_ONLY")
    init = next(c for c in s.calls if "video/init" in c[1])[2]["json"]
    assert init["post_info"]["privacy_level"] == "SELF_ONLY"
    assert init["post_info"]["brand_content_toggle"] is False
    assert init["source_info"] == {"source": "FILE_UPLOAD", "video_size": 1000, "chunk_size": 1000,
                                   "total_chunk_count": 1}
    put = next(c for c in s.calls if c[0] == "PUT")[2]
    assert put["headers"]["Content-Range"] == "bytes 0-999/1000"


def test_tiktok_error_code_raises(video, store):
    s = FakeSession([
        ("POST", "creator_info/query", FakeResp(body={"data": {}, "error": {"code": "spam_risk_too_many_posts",
                                                                             "message": "cap"}})),
    ])
    with pytest.raises(PublishError, match="spam_risk_too_many_posts"):
        TikTokPublisher(DirectAPIConfig(tiktok_access_token="t"), store, session=s).publish(video, CONTENT)


def test_x_chunked_upload_and_post(video, store):
    s = FakeSession([
        ("POST", "media/upload/initialize", FakeResp(body={"data": {"id": "m1"}})),
        ("POST", "media/upload/m1/append", FakeResp(status=204, body={})),
        ("POST", "media/upload/m1/finalize", FakeResp(body={"data": {"id": "m1", "processing_info": {"state": "pending"}}})),
        ("GET", "media/upload", FakeResp(body={"data": {"processing_info": {"state": "succeeded"}}})),
        ("POST", "/2/tweets", FakeResp(body={"data": {"id": "t1"}})),
    ])
    res = XPublisher(DirectAPIConfig(x_access_token="tok"), store, session=s, poll_every=0).publish(video, CONTENT)
    assert res.remote_id == "t1"
    tweet = next(c for c in s.calls if c[1].endswith("/2/tweets"))[2]["json"]
    assert tweet["media"]["media_ids"] == ["m1"]


def test_x_refresh_token_rotation_is_persisted(store):
    s = FakeSession([("POST", "oauth2/token", FakeResp(body={"access_token": "new", "refresh_token": "rt2",
                                                             "expires_in": 7200}))])
    pub = XPublisher(DirectAPIConfig(x_refresh_token="rt1", x_client_id="cid"), store, session=s)
    assert pub._token() == "new"
    assert store.kv_get("oauth:x")["refresh_token"] == "rt2"


def test_postiz_upload_then_post_with_direct_post_tiktok(video):
    s = FakeSession([
        ("POST", "/upload", FakeResp(body={"id": "u1", "path": "https://uploads.postiz.com/u1.mp4"})),
        ("POST", "/posts", FakeResp(body=[{"postId": "p9", "integration": "i1"}])),
    ])
    cfg = PostizConfig(api_url="https://api.postiz.com/public/v1", api_key="key", integrations={"tiktok": "i1"})
    res = PostizPublisher("tiktok", cfg, session=s).publish(video, CONTENT)
    assert res.remote_id == "postiz:p9"
    body = s.calls[1][2]["json"]
    assert s.calls[1][2]["headers"]["Authorization"] == "key"
    post = body["posts"][0]
    assert post["value"][0]["image"] == [{"id": "u1", "path": "https://uploads.postiz.com/u1.mp4"}]
    assert post["settings"]["content_posting_method"] == "DIRECT_POST"
    assert post["settings"]["brand_content_toggle"] is True


def test_youtube_retries_without_paid_placement_if_rejected(video):
    def init(url, kw):
        if "paidProductPlacementDetails" in kw["params"]["part"]:
            return FakeResp(status=400, body={"error": {"message": "paidProductPlacementDetails not writable"}})
        return FakeResp(headers={"Location": "https://upload.example/s2"})

    s = FakeSession([
        ("POST", "oauth2.googleapis.com/token", FakeResp(body={"access_token": "tok"})),
        ("POST", "upload/youtube/v3/videos", init),
        ("PUT", "upload.example/s2", FakeResp(body={"id": "v2"})),
    ])
    cfg = DirectAPIConfig(youtube_client_id="id", youtube_client_secret="sec", youtube_refresh_token="rt")
    res = YouTubePublisher(cfg, session=s).publish(video, CONTENT)
    assert res.remote_id == "v2"
    assert [c for c in s.calls if "upload/youtube" in c[1]][-1][2]["params"]["part"] == "snippet,status"
