"""Postiz backend: one integration that posts to TikTok, YouTube, Instagram and X.

Postiz is open source (self-host for free) and handles each platform's OAuth and app
review for you, which is the fastest way to get public (non-private) TikTok posting.
Provider `settings` keys below follow Postiz's documented schemas; run
`postiz integrations:settings <id>` once to confirm them for your instance, because
Postiz silently drops settings that don't apply.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import requests

from vv88.config import PostizConfig
from vv88.models import PostContent, PostResult
from vv88.publishing.base import PublishError, check

PROVIDER_TYPE = {"tiktok": "tiktok", "youtube": "youtube", "instagram": "instagram", "x": "x"}


def provider_settings(platform: str, content: PostContent) -> dict:
    if platform == "tiktok":
        return {
            "__type": "tiktok",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "duet": True,
            "stitch": True,
            "comment": True,
            # DIRECT_POST publishes; UPLOAD only drops the video in the TikTok inbox.
            "content_posting_method": "DIRECT_POST",
            "brand_content_toggle": content.paid_partnership,
            "brand_organic_toggle": False,
        }
    if platform == "youtube":
        return {
            "__type": "youtube",
            "title": content.title,
            "type": "public",
            "tags": [{"value": t, "label": t} for t in content.tags[:15]],
        }
    if platform == "instagram":
        return {"__type": "instagram", "post_type": "post"}
    if platform == "x":
        return {"__type": "x", "who_can_reply_post": "everyone"}
    raise PublishError(f"no Postiz mapping for {platform}")


class PostizPublisher:
    def __init__(self, platform: str, cfg: PostizConfig, session: requests.Session | None = None):
        self.platform = platform
        self.cfg = cfg
        self.http = session or requests.Session()
        self.integration_id = cfg.integrations.get(platform, "")
        if not cfg.api_key:
            raise PublishError("POSTIZ_API_KEY is not set")
        if not self.integration_id:
            raise PublishError(f"publishing.postiz.integrations.{platform} is not set (see GET /integrations)")
        self.base = cfg.api_url.rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": self.cfg.api_key}

    def upload(self, video_path: str) -> dict:
        with open(video_path, "rb") as f:
            body = check(
                self.http.post(
                    f"{self.base}/upload",
                    headers=self._headers(),
                    files={"file": (Path(video_path).name, f, "video/mp4")},
                    timeout=1800,
                ),
                "postiz upload",
            )
        if not body.get("path"):
            raise PublishError(f"postiz upload returned no path: {body}")
        return {"id": body.get("id", ""), "path": body["path"]}

    def publish(self, video_path: str, content: PostContent) -> PostResult:
        media = self.upload(video_path)
        payload = {
            "type": "now",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "shortLink": False,
            "tags": [],
            "posts": [
                {
                    "integration": {"id": self.integration_id},
                    "value": [{"content": content.caption, "image": [media]}],
                    "settings": provider_settings(self.platform, content),
                }
            ],
        }
        body = check(
            self.http.post(f"{self.base}/posts", headers={**self._headers(), "Content-Type": "application/json"},
                           json=payload, timeout=120),
            "postiz create post",
        )
        first = body[0] if isinstance(body, list) and body else body if isinstance(body, dict) else {}
        post_id = str(first.get("postId") or first.get("id") or "")
        return PostResult(self.platform, True, remote_id=f"postiz:{post_id}", status="queued_in_postiz",
                          raw={"response": body})

    def fetch_metrics(self, remote_id: str) -> dict | None:
        # Postiz's public API doesn't document per-post analytics; use direct API read
        # tokens (or `postiz analytics:post`) to feed views back into the optimizer.
        return None
