"""TikTok via the official Content Posting API (Direct Post, FILE_UPLOAD).

Until your developer app passes TikTok's audit, every API post is forced to private
(SELF_ONLY) and only private accounts can be posted to. Apply for the audit as soon as
your integration works; the publisher detects the restriction and reports it.
"""

from __future__ import annotations

import logging
import os

import requests

from vv88.config import DirectAPIConfig
from vv88.models import PostContent, PostResult
from vv88.publishing.base import PublishError, TokenVault, check, poll
from vv88.store import Store

log = logging.getLogger(__name__)

API = "https://open.tiktokapis.com/v2"
MIN_CHUNK = 5 * 1024 * 1024
DEFAULT_CHUNK = 10 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024


def chunk_plan(size: int, chunk_size: int = DEFAULT_CHUNK) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per TikTok's media transfer rules.

    Files under 5 MB go up whole. Otherwise the count is size // chunk_size and the last
    chunk absorbs the remainder (it may be up to 128 MB).
    """
    if size < MIN_CHUNK:
        return size, 1
    chunk_size = max(MIN_CHUNK, min(chunk_size, MAX_CHUNK, size))
    return chunk_size, max(1, size // chunk_size)


class TikTokPublisher:
    platform = "tiktok"

    def __init__(self, cfg: DirectAPIConfig, store: Store, session: requests.Session | None = None,
                 poll_every: float = 10):
        self.cfg = cfg
        self.http = session or requests.Session()
        self.poll_every = poll_every
        self.vault = TokenVault(store, "tiktok", cfg.tiktok_refresh_token, cfg.tiktok_access_token)

    def _token(self) -> str:
        if self.vault.valid():
            return self.vault.access_token
        if not (self.vault.refresh_token and self.cfg.tiktok_client_key):
            if self.vault.access_token:
                return self.vault.access_token
            raise PublishError("TikTok credentials missing (TIKTOK_ACCESS_TOKEN or refresh token + client key)")
        body = check(
            self.http.post(
                f"{API}/oauth/token/",
                data={
                    "client_key": self.cfg.tiktok_client_key,
                    "client_secret": self.cfg.tiktok_client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": self.vault.refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            ),
            "tiktok token refresh",
        )
        self.vault.save(body["access_token"], body.get("refresh_token"), body.get("expires_in"))
        return self.vault.access_token

    def _post(self, path: str, payload: dict) -> dict:
        body = check(
            self.http.post(
                f"{API}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json; charset=UTF-8"},
                timeout=60,
            ),
            f"tiktok {path}",
        )
        err = body.get("error", {})
        if err.get("code") not in (None, "ok"):
            raise PublishError(f"tiktok {path}: {err.get('code')} {err.get('message')}")
        return body.get("data", {})

    def publish(self, video_path: str, content: PostContent) -> PostResult:
        creator = self._post("/post/publish/creator_info/query/", {})
        options = creator.get("privacy_level_options") or []
        privacy = self.cfg.tiktok_privacy
        if options and privacy not in options:
            log.warning("TikTok privacy %s not allowed for this account/app (allowed: %s); "
                        "unaudited apps can only post SELF_ONLY", privacy, options)
            privacy = "SELF_ONLY" if "SELF_ONLY" in options else options[0]
        branded = content.paid_partnership and privacy != "SELF_ONLY"  # branded content can't be private
        size = os.path.getsize(video_path)
        chunk_size, chunks = chunk_plan(size)
        post_info = {
            "title": content.caption[:2200],
            "privacy_level": privacy,
            "disable_duet": bool(creator.get("duet_disabled", False)),
            "disable_stitch": bool(creator.get("stitch_disabled", False)),
            "disable_comment": bool(creator.get("comment_disabled", False)),
            "brand_content_toggle": branded,
            "brand_organic_toggle": False,
            "is_aigc": False,
        }
        if content.cover_ms:
            post_info["video_cover_timestamp_ms"] = content.cover_ms
        init = self._post(
            "/post/publish/video/init/",
            {
                "post_info": post_info,
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": chunks,
                },
            },
        )
        publish_id, upload_url = init["publish_id"], init["upload_url"]
        with open(video_path, "rb") as f:
            for i in range(chunks):
                first = i * chunk_size
                last = size - 1 if i == chunks - 1 else first + chunk_size - 1
                f.seek(first)
                blob = f.read(last - first + 1)
                resp = self.http.put(
                    upload_url,
                    data=blob,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(blob)),
                        "Content-Range": f"bytes {first}-{last}/{size}",
                    },
                    timeout=600,
                )
                if resp.status_code not in (200, 201, 206):
                    raise PublishError(f"tiktok chunk {i} upload failed: HTTP {resp.status_code} {resp.text[:300]}")
        status = poll(
            lambda: self._post("/post/publish/status/fetch/", {"publish_id": publish_id}),
            every=self.poll_every,
            timeout=900,
            done=lambda r: r.get("status") in ("PUBLISH_COMPLETE", "FAILED", "SEND_TO_USER_INBOX"),
        )
        if status.get("status") == "FAILED":
            raise PublishError(f"tiktok publish failed: {status.get('fail_reason')}")
        ids = status.get("publicaly_available_post_id") or status.get("publicly_available_post_id") or []
        post_id = str(ids[0]) if ids else ""
        return PostResult(
            self.platform,
            True,
            remote_id=post_id or publish_id,
            url=f"https://www.tiktok.com/video/{post_id}" if post_id else "",
            status=f"{status.get('status')}:{privacy}",
            raw=status,
        )

    def fetch_metrics(self, remote_id: str) -> dict | None:
        if not remote_id.isdigit():
            return None  # still a publish_id; private posts have no public id
        body = check(
            self.http.post(
                f"{API}/video/query/",
                params={"fields": "id,view_count,like_count,comment_count,share_count"},
                json={"filters": {"video_ids": [remote_id]}},
                headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"},
                timeout=30,
            ),
            "tiktok video query",
        )
        videos = body.get("data", {}).get("videos") or []
        if not videos:
            return None
        v = videos[0]
        return {
            "views": int(v.get("view_count", 0)),
            "likes": int(v.get("like_count", 0)),
            "comments": int(v.get("comment_count", 0)),
            "shares": int(v.get("share_count", 0)),
        }

