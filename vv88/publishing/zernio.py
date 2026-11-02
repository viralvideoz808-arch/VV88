"""Zernio backend: one API key that posts to TikTok, Instagram, YouTube and X.

Zernio's first 2 connected accounts are free without a card (X always needs one: X bills every API
call). New TikTok connections go through TikTok's Business app, so posts come out public without the
private-only limit and app audit a self-made TikTok developer app has. Instagram needs a Business or
Creator account but no Facebook Page.

Flow (docs.zernio.com, "Media uploads" and "Post lifecycle"): POST /media/presign, PUT the file to the
returned storage URL, then POST /posts with publishNow. publishNow is synchronous: the response carries
each platform's result and live URL (201 = published, 207 = created but a platform rejected it).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import requests

from vv88.config import ZernioConfig
from vv88.models import PostContent, PostResult
from vv88.publishing.base import PublishError, check, redact

PLATFORM = {"tiktok": "tiktok", "instagram": "instagram", "youtube": "youtube", "x": "twitter"}
FROM_ZERNIO = {"tiktok": "tiktok", "instagram": "instagram", "youtube": "youtube", "twitter": "x", "x": "x"}
# errorCategory values that clear up by waiting; the message carries "rate limit" so publish_due retries later.
_WAIT = {"platform_rate_limit", "quota_exhausted", "user_abuse", "platform_error", "system_error"}


def list_accounts(api_url: str, api_key: str, http: requests.Session | None = None) -> list[dict]:
    """Connected accounts as [{_id, platform, username, isActive}] (GET /accounts)."""
    http = http or requests.Session()
    body = check(http.get(api_url.rstrip("/") + "/accounts", headers={"Authorization": f"Bearer {api_key}"},
                          timeout=30), "zernio accounts")
    items = body.get("accounts") if isinstance(body, dict) else body
    return [a for a in items or [] if isinstance(a, dict)]


def platform_settings(platform: str, content: PostContent) -> tuple[dict, dict]:
    """(top-level fields, platformSpecificData) for one post."""
    if platform == "tiktok":
        tiktok = {
            "privacy_level": "PUBLIC_TO_EVERYONE",  # the Business-app lane publishes videos as public only
            "allow_comment": True,
            "allow_duet": True,
            "allow_stitch": True,
            # TikTok's rule: the person saw this exact post and agreed to publish it (VV88's Approve step).
            "content_preview_confirmed": True,
            "express_consent_given": True,
            "video_cover_timestamp_ms": max(0, int(content.cover_ms)),
        }
        if content.paid_partnership:
            tiktok["commercialContentType"] = "brand_content"
        return {"tiktokSettings": tiktok}, {}
    if platform == "instagram":
        return {}, {"shareToFeed": True}
    if platform == "youtube":
        top = {"tags": content.tags[:15]} if content.tags else {}
        return top, {"title": content.title[:100] or content.caption[:100], "visibility": "public",
                     "madeForKids": False}
    if platform == "x":
        return {}, ({"paidPartnership": True} if content.paid_partnership else {})
    raise PublishError(f"no Zernio mapping for {platform}")


class ZernioPublisher:
    def __init__(self, platform: str, cfg: ZernioConfig, session: requests.Session | None = None):
        if platform not in PLATFORM:
            raise PublishError(f"Zernio can't post to {platform}")
        self.platform = platform
        self.http = session or requests.Session()
        self.api_key = cfg.api_key
        self.account_id = cfg.accounts.get(platform, "")
        if not self.api_key:
            raise PublishError("ZERNIO_API_KEY is not set: add the key on the Connections page")
        if not self.account_id:
            raise PublishError(f"no Zernio {platform} account: connect it in Zernio, then save the key again on "
                               "the Connections page")
        self.base = cfg.api_url.rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def upload(self, video_path: str) -> str:
        size = os.path.getsize(video_path)
        body = check(self.http.post(f"{self.base}/media/presign", headers=self._headers(),
                                    json={"filename": Path(video_path).name, "contentType": "video/mp4",
                                          "size": size}, timeout=60),
                     "zernio presign")
        upload_url, public_url = body.get("uploadUrl"), body.get("publicUrl")
        if not upload_url or not public_url:
            raise PublishError("zernio presign returned no upload URL")
        with open(video_path, "rb") as f:
            # Straight to storage: no Authorization header. The URL is signed, so it never goes into errors.
            resp = self.http.put(upload_url, data=f, headers={"Content-Type": "video/mp4",
                                                               "Content-Length": str(size)}, timeout=1800)
        if resp.status_code >= 400:
            raise PublishError(f"zernio upload failed: HTTP {resp.status_code}")
        return public_url

    def _idempotency_key(self, video_path: str, content: PostContent) -> str:
        # Same clip + account + caption -> same key, so retrying after a timeout returns the post that may
        # already have gone out instead of posting it twice.
        digest = hashlib.sha256(f"{self.platform}|{self.account_id}|{Path(video_path).name}|{content.caption}"
                                .encode()).hexdigest()
        return str(uuid.UUID(digest[:32]))

    def publish(self, video_path: str, content: PostContent) -> PostResult:
        if self.platform == "tiktok" and not content.approved:
            raise PublishError("TikTok only accepts posts that you previewed and approved: turn on 'Approve posts "
                               "before they go out' in Settings, then approve this post on the Queue page")
        top, specific = platform_settings(self.platform, content)
        entry = {"platform": PLATFORM[self.platform], "accountId": self.account_id}
        if specific:
            entry["platformSpecificData"] = specific
        media_url = self.upload(video_path)
        payload = {"content": content.caption, "mediaItems": [{"type": "video", "url": media_url}],
                   "platforms": [entry], "publishNow": True, **top}
        resp = self.http.post(f"{self.base}/posts", json=payload, timeout=900,
                              headers={**self._headers(), "Content-Type": "application/json",
                                       "Idempotency-Key": self._idempotency_key(video_path, content)})
        body = check(resp, "zernio create post")
        post = body.get("post") if isinstance(body, dict) else None
        post = post if isinstance(post, dict) else {}
        entries = [e for e in post.get("platforms") or [] if isinstance(e, dict)]
        mine = next((e for e in entries if e.get("platform") == PLATFORM[self.platform]), entries[0] if entries else {})
        status = str(post.get("status") or "")
        if resp.status_code == 207 or status in ("failed", "partial") or mine.get("status") == "failed":
            category = str(mine.get("errorCategory") or "")
            reason = str(mine.get("errorMessage") or body.get("message") or status or "rejected")
            hint = " (rate limit or outage: retrying in 2 hours)" if category in _WAIT else ""
            raise PublishError(redact(f"zernio: {self.platform} rejected the post: {reason[:400]}"
                                      f"{f' [{category}]' if category else ''}{hint}"))
        url = str(mine.get("platformPostUrl") or "")
        return PostResult(self.platform, True, remote_id=f"zernio:{post.get('_id', '')}",
                          url=url if url.startswith("https://") else "", status=status or "published",
                          raw={"response": body})

    def fetch_metrics(self, remote_id: str) -> dict | None:
        return None  # views come back through the direct API readers when those are linked
