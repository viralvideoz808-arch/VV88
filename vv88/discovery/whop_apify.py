"""Whop "Content Rewards" campaigns via an Apify actor.

Whop does not publish an official endpoint for listing Content Rewards campaigns, so this
uses a community Apify actor that reads the public listings (reward per 1K views, budget
left, platforms, resource links). Costs a fraction of a cent per run on Apify's free tier.
"""

from __future__ import annotations

import logging
import re

import requests

from vv88.config import WhopApifyConfig
from vv88.models import Campaign, normalize_platform

log = logging.getLogger(__name__)

APIFY_RUN_SYNC = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"

# Resource links that usually point at the raw footage a campaign wants clipped.
_VIDEO_HOSTS = re.compile(
    r"(youtube\.com|youtu\.be|drive\.google\.com|dropbox\.com|vimeo\.com|kick\.com|twitch\.tv|"
    r"streamable\.com|\.mp4($|\?)|\.mov($|\?))",
    re.I,
)
_HASHTAG = re.compile(r"#\w+")
_MENTION = re.compile(r"(?<!\w)@[\w.]+")


class WhopApifySource:
    name = "whop"

    def __init__(self, cfg: WhopApifyConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.http = session or requests.Session()

    def fetch(self) -> list[Campaign]:
        if not self.cfg.enabled:
            return []
        if not self.cfg.token:
            log.warning("whop_apify enabled but APIFY_TOKEN is not set; skipping")
            return []
        payload = {
            "maxItems": self.cfg.max_items,
            "campaignType": self.cfg.campaign_type,
            "status": "active",
            "sortBy": "reward",
            "enrichDetails": True,
        }
        if self.cfg.min_reward_per_1k_usd:
            payload["minRewardPer1kUsd"] = self.cfg.min_reward_per_1k_usd
        if self.cfg.min_budget_remaining_usd:
            payload["minBudgetRemainingUsd"] = self.cfg.min_budget_remaining_usd
        resp = self.http.post(
            APIFY_RUN_SYNC.format(actor=self.cfg.actor),
            params={"token": self.cfg.token},
            json=payload,
            timeout=300,
        )
        resp.raise_for_status()
        return [c for c in (self.parse_item(i) for i in resp.json()) if c]

    @classmethod
    def parse_item(cls, item: dict) -> Campaign | None:
        cid = item.get("campaignId") or item.get("id")
        if not cid:
            return None
        resources = [u for u in (item.get("resourceUrls") or []) if isinstance(u, str)]
        requirements = item.get("requirements") or ""
        if isinstance(requirements, list):
            requirements = "\n".join(map(str, requirements))
        text = f"{item.get('description') or ''}\n{requirements}"
        platforms = [p for p in (normalize_platform(str(x)) for x in item.get("platforms") or []) if p]
        return Campaign(
            id=f"whop:{cid}",
            title=item.get("title") or str(cid),
            brand=item.get("brand") or "",
            category=str(item.get("category") or "general").lower(),
            reward_per_1k_usd=float(item.get("rewardPer1kUsd") or 0),
            budget_remaining_usd=_f(item.get("budgetRemainingUsd")),
            total_budget_usd=_f(item.get("totalBudgetUsd")),
            platforms=platforms,
            source_urls=[u for u in resources if _VIDEO_HOSTS.search(u)],
            campaign_url=item.get("campaignUrl") or "",
            requirements=requirements,
            required_hashtags=sorted(set(_HASHTAG.findall(text))),
            required_mentions=sorted(set(_MENTION.findall(text))),
            status=str(item.get("status") or "active").lower(),
            origin=cls.name,
        )


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
