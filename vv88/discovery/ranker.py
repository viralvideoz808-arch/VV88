"""Rank campaigns by expected dollars per clip we post.

    EV(campaign) = reward_per_1k / 1000
                 * sum over usable platforms of E[views per clip | niche, platform]
                 * budget_factor

E[views] starts from a prior scaled by the live niche-traffic multiplier, then shifts toward
our own measured views in that niche as posts accumulate (Bayesian shrinkage).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from vv88.config import RankingConfig
from vv88.models import Campaign


@dataclass
class RankedCampaign:
    campaign: Campaign
    expected_usd_per_clip: float
    platforms: list[str]
    expected_views: dict[str, float] = field(default_factory=dict)
    budget_factor: float = 1.0
    niche_multiplier: float = 1.0


def observed_views_by_niche(performance_rows) -> dict[tuple[str, str], list[float]]:
    out: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in performance_rows:
        if r["views"] is None:
            continue
        out[(r["category"] or "general", r["platform"])].append(float(r["views"]))
    return out


def budget_factor(c: Campaign, expected_payout_per_clip: float, clips_planned: int = 10) -> float:
    """Discount campaigns that are nearly drained: views after the budget runs out pay nothing."""
    if c.budget_remaining_usd is None:
        return 1.0
    if c.budget_remaining_usd <= 0:
        return 0.0
    need = max(expected_payout_per_clip * clips_planned, 1e-9)
    return min(1.0, c.budget_remaining_usd / need)


def rank_campaigns(
    campaigns: list[Campaign],
    connected_platforms: list[str],
    niche_multipliers: dict[str, float],
    observed: dict[tuple[str, str], list[float]],
    cfg: RankingConfig,
) -> list[RankedCampaign]:
    ranked: list[RankedCampaign] = []
    for c in campaigns:
        if c.status != "active" or c.reward_per_1k_usd <= 0:
            continue
        usable = [p for p in (c.platforms or connected_platforms) if p in connected_platforms]
        if not usable:
            continue
        mult = niche_multipliers.get(c.category, 1.0)
        exp_views: dict[str, float] = {}
        for p in usable:
            prior = cfg.prior_views_per_clip * mult
            obs = observed.get((c.category, p), [])
            if obs:
                # Shrink the geometric mean of our own results toward the prior.
                n = len(obs)
                own = math.exp(sum(math.log1p(v) for v in obs) / n) - 1
                w = n / (n + cfg.shrinkage_posts)
                exp_views[p] = w * own + (1 - w) * prior
            else:
                exp_views[p] = prior
            if c.min_views_for_payout and exp_views[p] < c.min_views_for_payout:
                # Below the payout threshold most clips earn nothing on that platform.
                exp_views[p] *= 0.25
        total_views = sum(exp_views.values())
        payout = c.reward_per_1k_usd / 1000.0 * total_views
        bf = budget_factor(c, payout)
        ranked.append(
            RankedCampaign(
                campaign=c,
                expected_usd_per_clip=payout * bf,
                platforms=usable,
                expected_views=exp_views,
                budget_factor=bf,
                niche_multiplier=mult,
            )
        )
    ranked.sort(key=lambda r: r.expected_usd_per_clip, reverse=True)
    return ranked


def niche_leaderboard(ranked: list[RankedCampaign]) -> list[tuple[str, float, int]]:
    """(niche, best EV per clip, number of campaigns) — the 'highest traffic niche' answer."""
    best: dict[str, float] = {}
    count: dict[str, int] = defaultdict(int)
    for r in ranked:
        cat = r.campaign.category
        best[cat] = max(best.get(cat, 0.0), r.expected_usd_per_clip)
        count[cat] += 1
    return sorted(((n, v, count[n]) for n, v in best.items()), key=lambda x: x[1], reverse=True)
