"""Threads API: keyword search over public posts, public replies, and posts on our own profile.

Threads has no DM API, so the first touch is always a short, helpful public reply that points the
person to the website or to our Instagram DMs.
"""

from __future__ import annotations

import time

import requests

from carlos.channels.base import ChannelError, SendResult, call
from carlos.config import Settings

THREADS = "https://graph.threads.net/v1.0"


class ThreadsClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None, publish_wait: float = 2.0):
        if not settings.threads_enabled:
            raise ChannelError("Threads not connected (THREADS_USER_ID / THREADS_ACCESS_TOKEN)")
        self.s = settings
        self.http = session or requests.Session()
        self.publish_wait = publish_wait

    def _get(self, path: str, **params) -> dict:
        params["access_token"] = self.s.threads_token
        return call(self.http.get, f"threads GET {path}", f"{THREADS}/{path}", params=params, timeout=30)

    def _post(self, path: str, **params) -> dict:
        params["access_token"] = self.s.threads_token
        return call(self.http.post, f"threads POST {path}", f"{THREADS}/{path}", params=params, timeout=30)

    def keyword_search(self, query: str, limit: int = 25) -> list[dict]:
        body = self._get("keyword_search", q=query, search_type="RECENT", limit=limit,
                         fields="id,text,media_type,permalink,timestamp,username")
        return [{
            "platform": "threads", "source_id": f"th:{p['id']}", "media_id": p["id"],
            "handle": p.get("username", ""), "text": p.get("text") or "", "posted_at": p.get("timestamp", ""),
            "post_url": p.get("permalink", ""), "profile_url": f"https://www.threads.net/@{p.get('username', '')}",
            "reply_mode": "threads_reply", "found_by": f"threads search “{query}”",
        } for p in body.get("data", []) if p.get("username") != self.s.extra.get("threads_username")]

    def _publish(self, **container) -> SendResult:
        created = self._post(f"{self.s.threads_user_id}/threads", media_type="TEXT", **container)
        creation_id = created.get("id")
        if not creation_id:
            raise ChannelError("threads: no container id returned")
        last_error = None
        for attempt in range(3):
            time.sleep(self.publish_wait * (attempt + 1))
            try:
                body = self._post(f"{self.s.threads_user_id}/threads_publish", creation_id=creation_id)
                return SendResult(True, body.get("id", ""))
            except ChannelError as e:  # container not ready yet
                last_error = e
        raise last_error or ChannelError("threads publish failed")

    def reply(self, reply_to_id: str, text: str) -> SendResult:
        return self._publish(text=text[:500], reply_to_id=reply_to_id)

    def post(self, text: str) -> SendResult:
        return self._publish(text=text[:500])

    def replies_to(self, media_id: str) -> list[dict]:
        body = self._get(f"{media_id}/replies", fields="id,text,username,timestamp,permalink")
        return body.get("data", [])
