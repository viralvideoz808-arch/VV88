"""X (Twitter) via API v2: chunked media upload + create post.

X has no free write tier for new developers in 2026: posting is pay-per-use (roughly a
cent or two per post at the time of writing), so the X daily cap is also a cost cap.
"""

from __future__ import annotations

import os

import requests

from vv88.config import DirectAPIConfig
from vv88.models import PostContent, PostResult
from vv88.publishing.base import PublishError, TokenVault, check, poll
from vv88.store import Store

API = "https://api.x.com/2"
CHUNK = 4 * 1024 * 1024


class XPublisher:
    platform = "x"

    def __init__(self, cfg: DirectAPIConfig, store: Store, session: requests.Session | None = None,
                 poll_every: float = 5):
        self.cfg = cfg
        self.http = session or requests.Session()
        self.poll_every = poll_every
        self.vault = TokenVault(store, "x", cfg.x_refresh_token, cfg.x_access_token)

    def _token(self) -> str:
        if self.vault.valid():
            return self.vault.access_token
        if not (self.vault.refresh_token and self.cfg.x_client_id):
            if self.vault.access_token:
                return self.vault.access_token
            raise PublishError("X credentials missing (X_ACCESS_TOKEN or X_REFRESH_TOKEN + X_CLIENT_ID)")
        auth = (self.cfg.x_client_id, self.cfg.x_client_secret) if self.cfg.x_client_secret else None
        body = check(
            self.http.post(
                f"{API}/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.vault.refresh_token,
                    "client_id": self.cfg.x_client_id,
                },
                auth=auth,
                timeout=30,
            ),
            "x token refresh",
        )
        self.vault.save(body["access_token"], body.get("refresh_token"), body.get("expires_in"))
        return self.vault.access_token

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}"}

    def publish(self, video_path: str, content: PostContent) -> PostResult:
        size = os.path.getsize(video_path)
        init = check(
            self.http.post(
                f"{API}/media/upload/initialize",
                json={"media_type": "video/mp4", "total_bytes": size, "media_category": "tweet_video"},
                headers=self._auth(),
                timeout=60,
            ),
            "x media init",
        )["data"]
        media_id = init["id"]
        with open(video_path, "rb") as f:
            index = 0
            while blob := f.read(CHUNK):
                check(
                    self.http.post(
                        f"{API}/media/upload/{media_id}/append",
                        data={"segment_index": str(index)},
                        files={"media": ("chunk.mp4", blob, "application/octet-stream")},
                        headers=self._auth(),
                        timeout=300,
                    ),
                    f"x media append {index}",
                )
                index += 1
        fin = check(self.http.post(f"{API}/media/upload/{media_id}/finalize", headers=self._auth(), timeout=60),
                    "x media finalize").get("data", {})
        if fin.get("processing_info"):
            status = poll(
                lambda: check(
                    self.http.get(f"{API}/media/upload", params={"command": "STATUS", "media_id": media_id},
                                  headers=self._auth(), timeout=30),
                    "x media status",
                ).get("data", {}),
                every=self.poll_every,
                timeout=600,
                done=lambda r: (r.get("processing_info") or {}).get("state") in ("succeeded", "failed", None),
            )
            if (status.get("processing_info") or {}).get("state") == "failed":
                raise PublishError(f"x media processing failed: {status}")
        tweet = check(
            self.http.post(
                f"{API}/tweets",
                json={"text": content.caption, "media": {"media_ids": [str(media_id)]}},
                headers=self._auth(),
                timeout=60,
            ),
            "x create post",
        )["data"]
        return PostResult(self.platform, True, remote_id=tweet["id"],
                          url=f"https://x.com/i/status/{tweet['id']}", status="published")

    def fetch_metrics(self, remote_id: str) -> dict | None:
        body = check(
            self.http.get(f"{API}/tweets/{remote_id}", params={"tweet.fields": "public_metrics"},
                          headers=self._auth(), timeout=30),
            "x metrics",
        )
        m = (body.get("data") or {}).get("public_metrics")
        if not m:
            return None
        return {
            "views": int(m.get("impression_count", 0)),
            "likes": int(m.get("like_count", 0)),
            "comments": int(m.get("reply_count", 0)),
            "shares": int(m.get("retweet_count", 0)) + int(m.get("quote_count", 0)),
        }
