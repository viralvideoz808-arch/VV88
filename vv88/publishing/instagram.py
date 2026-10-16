"""Instagram Reels via the Instagram Graph API (Facebook Login for Business token).

Flow: create a REELS container with upload_type=resumable -> push the file to
rupload.facebook.com -> wait for status_code FINISHED -> media_publish.
Meta caps API publishing per account per rolling 24 h; we read the live quota first.
"""

from __future__ import annotations

import os

import requests

from vv88.config import DirectAPIConfig
from vv88.models import PostContent, PostResult
from vv88.publishing.base import PublishError, check, poll

GRAPH = "https://graph.facebook.com"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload"


class InstagramPublisher:
    platform = "instagram"

    def __init__(self, cfg: DirectAPIConfig, session: requests.Session | None = None, poll_every: float = 20):
        self.cfg = cfg
        self.http = session or requests.Session()
        self.poll_every = poll_every
        if not (cfg.instagram_user_id and cfg.instagram_access_token):
            raise PublishError("Instagram credentials missing (INSTAGRAM_USER_ID / INSTAGRAM_ACCESS_TOKEN)")
        self.base = f"{GRAPH}/{cfg.instagram_graph_version}"

    def _get(self, path: str, **params) -> dict:
        params["access_token"] = self.cfg.instagram_access_token
        return check(self.http.get(f"{self.base}/{path}", params=params, timeout=30), f"instagram GET {path}")

    def quota_left(self) -> int | None:
        try:
            body = self._get(f"{self.cfg.instagram_user_id}/content_publishing_limit", fields="quota_usage,config")
        except PublishError:
            return None
        data = (body.get("data") or [{}])[0]
        total = (data.get("config") or {}).get("quota_total")
        used = data.get("quota_usage")
        return None if total is None or used is None else int(total) - int(used)

    def publish(self, video_path: str, content: PostContent) -> PostResult:
        left = self.quota_left()
        if left is not None and left <= 0:
            raise PublishError("Instagram content publishing quota exhausted for the rolling 24 h window")
        ig = self.cfg.instagram_user_id
        token = self.cfg.instagram_access_token
        data = {
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": content.caption,
            "share_to_feed": "true" if self.cfg.instagram_share_to_feed else "false",
            "access_token": token,
        }
        if content.cover_ms:
            data["thumb_offset"] = str(content.cover_ms)
        container = check(self.http.post(f"{self.base}/{ig}/media", data=data, timeout=60),
                          "instagram create container")["id"]
        size = os.path.getsize(video_path)
        with open(video_path, "rb") as f:
            up = check(
                self.http.post(
                    f"{RUPLOAD}/{self.cfg.instagram_graph_version}/{container}",
                    headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
                    data=f,
                    timeout=1800,
                ),
                "instagram upload",
            )
        if not up.get("success", True):
            raise PublishError(f"instagram upload rejected: {up}")
        status = poll(
            lambda: self._get(container, fields="status_code,status"),
            every=self.poll_every,
            timeout=900,
            done=lambda r: r.get("status_code") in ("FINISHED", "ERROR", "EXPIRED"),
        )
        if status.get("status_code") != "FINISHED":
            raise PublishError(f"instagram processing failed: {status}")
        media_id = check(
            self.http.post(f"{self.base}/{ig}/media_publish", data={"creation_id": container, "access_token": token},
                           timeout=60),
            "instagram publish",
        )["id"]
        try:
            permalink = self._get(media_id, fields="permalink").get("permalink", "")
        except PublishError:
            permalink = ""
        return PostResult(self.platform, True, remote_id=media_id, url=permalink, status="published")

    def fetch_metrics(self, remote_id: str) -> dict | None:
        body = self._get(f"{remote_id}/insights", metric="views,likes,comments,shares")
        values = {m["name"]: (m.get("values") or [{}])[0].get("value", 0) for m in body.get("data", [])}
        if not values:
            return None
        return {k: int(values.get(k, 0)) for k in ("views", "likes", "comments", "shares")}
