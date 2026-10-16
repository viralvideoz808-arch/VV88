"""Campaigns you joined by hand (Whop, ClipAffiliates, Vyro, direct creator deals...)."""

from __future__ import annotations

from pathlib import Path

import yaml

from vv88.models import Campaign, normalize_platform, stable_id


class ManualCampaignSource:
    name = "manual"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fetch(self) -> list[Campaign]:
        if not self.path.is_file():
            return []
        doc = yaml.safe_load(self.path.read_text()) or {}
        items = doc.get("campaigns", doc if isinstance(doc, list) else [])
        out: list[Campaign] = []
        for item in items:
            platforms = [p for p in (normalize_platform(x) for x in item.get("platforms", [])) if p]
            cid = str(item.get("id") or stable_id(item.get("title", ""), item.get("campaign_url", "")))
            out.append(
                Campaign(
                    id=cid,
                    title=item.get("title", cid),
                    brand=item.get("brand", ""),
                    category=str(item.get("category", "general")).lower(),
                    reward_per_1k_usd=float(item.get("reward_per_1k_usd", 0) or 0),
                    budget_remaining_usd=_opt_float(item.get("budget_remaining_usd")),
                    total_budget_usd=_opt_float(item.get("total_budget_usd")),
                    platforms=platforms,
                    source_urls=list(item.get("source_urls", [])),
                    campaign_url=item.get("campaign_url", ""),
                    requirements=item.get("requirements", ""),
                    required_hashtags=list(item.get("required_hashtags", [])),
                    required_mentions=list(item.get("required_mentions", [])),
                    keywords=list(item.get("keywords", [])),
                    min_views_for_payout=int(item.get("min_views_for_payout", 0) or 0),
                    paid_partnership=bool(item.get("paid_partnership", True)),
                    status=item.get("status", "active"),
                    origin=self.name,
                )
            )
        return out


def _opt_float(v) -> float | None:
    return None if v in (None, "") else float(v)
