"""Facebook Page + Instagram professional account through Meta's Graph API and Messenger Platform.

Only endpoints Meta provides for businesses are used:
- read comments on the business's own posts, and reply to them (publicly, or one private reply);
- answer DMs people send the business (24-hour window);
- Instagram hashtag search (public posts; Meta does not return the author, so these leads are worked by hand);
- post to the business's own Page.
There is no scraping of profiles, groups or Marketplace, and no unsolicited DMs.
"""

from __future__ import annotations

import requests

from carlos.channels.base import ChannelError, SendResult, call
from carlos.config import Settings

GRAPH = "https://graph.facebook.com"


class MetaClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        if not settings.meta_enabled:
            raise ChannelError("Facebook Page not connected (FB_PAGE_ID / FB_PAGE_TOKEN)")
        self.s = settings
        self.http = session or requests.Session()
        self.base = f"{GRAPH}/{settings.graph_version}"

    def _get(self, path: str, **params) -> dict:
        params["access_token"] = self.s.fb_page_token
        return call(self.http.get, f"meta GET {path}", f"{self.base}/{path}", params=params, timeout=30)

    def _post(self, path: str, json_body: dict | None = None, **params) -> dict:
        params["access_token"] = self.s.fb_page_token
        return call(self.http.post, f"meta POST {path}", f"{self.base}/{path}", params=params, json=json_body,
                    timeout=30)

    # -- messaging ---------------------------------------------------------------------------
    def send_message(self, recipient_id: str, text: str) -> SendResult:
        """Reply in a Messenger or Instagram DM thread the customer opened (both go through the Page)."""
        body = self._post(f"{self.s.fb_page_id}/messages", {
            "recipient": {"id": recipient_id}, "messaging_type": "RESPONSE", "message": {"text": text[:1000]}})
        return SendResult(True, body.get("message_id", ""))

    def private_reply(self, comment_id: str, text: str) -> SendResult:
        """The one DM Meta allows in answer to a comment on our own post (within 7 days)."""
        body = self._post(f"{self.s.fb_page_id}/messages", {
            "recipient": {"comment_id": comment_id}, "message": {"text": text[:1000]}})
        return SendResult(True, body.get("message_id", ""))

    def reply_ig_comment(self, comment_id: str, text: str) -> SendResult:
        body = self._post(f"{comment_id}/replies", message=text[:2200])
        return SendResult(True, body.get("id", ""))

    def reply_fb_comment(self, comment_id: str, text: str) -> SendResult:
        body = self._post(f"{comment_id}/comments", message=text[:8000])
        return SendResult(True, body.get("id", ""))

    def page_post(self, text: str, link: str = "") -> SendResult:
        params = {"message": text}
        if link:
            params["link"] = link
        body = self._post(f"{self.s.fb_page_id}/feed", **params)
        return SendResult(True, body.get("id", ""))

    # -- discovery -----------------------------------------------------------------------------
    def ig_comments_on_our_posts(self, media_limit: int = 12) -> list[dict]:
        if not self.s.ig_user_id:
            return []
        body = self._get(f"{self.s.ig_user_id}/media", limit=media_limit,
                         fields="id,caption,permalink,timestamp,comments.limit(50){id,text,username,timestamp,from}")
        out = []
        for media in body.get("data", []):
            for c in (media.get("comments") or {}).get("data", []):
                frm = c.get("from") or {}
                out.append({
                    "platform": "instagram", "source_id": f"igc:{c['id']}", "comment_id": c["id"],
                    "handle": c.get("username") or frm.get("username", ""), "user_id": frm.get("id", ""),
                    "text": c.get("text", ""), "posted_at": c.get("timestamp", ""),
                    "post_url": media.get("permalink", ""), "context": (media.get("caption") or "")[:300],
                    "reply_mode": "ig_private_reply", "found_by": "instagram comments on our posts",
                })
        return out

    def fb_comments_on_our_posts(self, post_limit: int = 12) -> list[dict]:
        body = self._get(f"{self.s.fb_page_id}/posts", limit=post_limit,
                         fields="id,message,permalink_url,comments.limit(50){id,message,from,created_time}")
        out = []
        for post in body.get("data", []):
            for c in (post.get("comments") or {}).get("data", []):
                frm = c.get("from") or {}
                if frm.get("id") == self.s.fb_page_id:
                    continue  # our own replies
                out.append({
                    "platform": "facebook", "source_id": f"fbc:{c['id']}", "comment_id": c["id"],
                    "handle": frm.get("name", ""), "user_id": frm.get("id", ""), "text": c.get("message", ""),
                    "posted_at": c.get("created_time", ""), "post_url": post.get("permalink_url", ""),
                    "context": (post.get("message") or "")[:300], "reply_mode": "fb_private_reply",
                    "found_by": "facebook comments on our posts",
                })
        return out

    def ig_hashtag_posts(self, tag: str, limit: int = 25) -> list[dict]:
        if not self.s.ig_user_id:
            return []
        found = self._get("ig_hashtag_search", user_id=self.s.ig_user_id, q=tag.lstrip("#"))
        if not found.get("data"):
            return []
        tag_id = found["data"][0]["id"]
        body = self._get(f"{tag_id}/recent_media", user_id=self.s.ig_user_id, limit=limit,
                         fields="id,caption,permalink,timestamp,media_type")
        return [{
            "platform": "instagram", "source_id": f"igh:{m['id']}", "handle": "", "text": m.get("caption") or "",
            "posted_at": m.get("timestamp", ""), "post_url": m.get("permalink", ""), "reply_mode": "manual",
            "found_by": f"instagram #{tag.lstrip('#')}",
        } for m in body.get("data", [])]
