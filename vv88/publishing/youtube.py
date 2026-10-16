"""YouTube Shorts via the official YouTube Data API v3 (resumable upload).

Vertical videos up to 3 minutes are classified as Shorts automatically. Default project
quota is 10,000 units/day; Google's revision history says an upload now costs ~100 units
(down from ~1,600), but check the Quotas page in Google Cloud Console for your project.
"""

from __future__ import annotations

import os

import requests

from vv88.config import DirectAPIConfig
from vv88.models import PostContent, PostResult
from vv88.publishing.base import PublishError, check

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


class YouTubePublisher:
    platform = "youtube"

    def __init__(self, cfg: DirectAPIConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.http = session or requests.Session()
        self._token: str | None = None
        self.scopes: list[str] = []

    def _access_token(self) -> str:
        if self._token:
            return self._token
        if not (self.cfg.youtube_client_id and self.cfg.youtube_refresh_token):
            raise PublishError("YouTube credentials missing (YOUTUBE_CLIENT_ID / _SECRET / _REFRESH_TOKEN)")
        body = check(
            self.http.post(
                TOKEN_URL,
                data={
                    "client_id": self.cfg.youtube_client_id,
                    "client_secret": self.cfg.youtube_client_secret,
                    "refresh_token": self.cfg.youtube_refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=30,
            ),
            "youtube token refresh",
        )
        self._token = body["access_token"]
        self.scopes = body.get("scope", "").split()
        return self._token

    def publish(self, video_path: str, content: PostContent) -> PostResult:
        token = self._access_token()
        size = os.path.getsize(video_path)
        parts = ["snippet", "status"]
        meta: dict = {
            "snippet": {
                "title": content.title[:100],
                "description": content.caption,
                "tags": content.tags[:30],
                "categoryId": self.cfg.youtube_category_id,
            },
            "status": {"privacyStatus": self.cfg.youtube_privacy, "selfDeclaredMadeForKids": False},
        }
        if content.paid_partnership:
            parts.append("paidProductPlacementDetails")
            meta["paidProductPlacementDetails"] = {"hasPaidProductPlacement": True}
        init = self._init_upload(token, size, parts, meta)
        if init.status_code == 400 and "paidProductPlacement" in init.text:
            # Keep uploading if the API won't take the flag; the #ad in the description still discloses.
            parts.remove("paidProductPlacementDetails")
            meta.pop("paidProductPlacementDetails")
            init = self._init_upload(token, size, parts, meta)
        check(init, "youtube upload init")
        location = init.headers.get("Location")
        if not location:
            raise PublishError("youtube upload init returned no Location header")
        with open(video_path, "rb") as f:
            done = check(
                self.http.put(
                    location,
                    data=f,
                    headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
                    timeout=1800,
                ),
                "youtube upload",
            )
        vid = done["id"]
        return PostResult(self.platform, True, remote_id=vid, url=f"https://youtube.com/shorts/{vid}",
                          status=done.get("status", {}).get("uploadStatus", ""), raw=done)

    def _init_upload(self, token: str, size: int, parts: list[str], meta: dict) -> requests.Response:
        return self.http.post(
            UPLOAD_URL,
            params={"uploadType": "resumable", "part": ",".join(parts)},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(size),
            },
            json=meta,
            timeout=60,
        )

    def fetch_metrics(self, remote_id: str) -> dict | None:
        body = check(
            self.http.get(
                VIDEOS_URL,
                params={"part": "statistics", "id": remote_id},
                headers={"Authorization": f"Bearer {self._access_token()}"},
                timeout=30,
            ),
            "youtube stats",
        )
        items = body.get("items") or []
        if not items:
            return None
        s = items[0]["statistics"]
        return {
            "views": int(s.get("viewCount", 0)),
            "likes": int(s.get("likeCount", 0)),
            "comments": int(s.get("commentCount", 0)),
        }
