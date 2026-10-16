import random
import time

from vv88.config import OptimizerConfig, RankingConfig
from vv88.discovery.ranker import budget_factor, niche_leaderboard, rank_campaigns
from vv88.discovery.whop_apify import WhopApifySource
from vv88.models import Campaign
from vv88.optimizer import Optimizer


def camp(cid, reward, cat="podcast", budget=None, platforms=("tiktok", "youtube")):
    return Campaign(id=cid, title=cid, category=cat, reward_per_1k_usd=reward, budget_remaining_usd=budget,
                    platforms=list(platforms))


def test_rank_prefers_payout_times_traffic():
    cfg = RankingConfig(prior_views_per_clip=1000)
    ranked = rank_campaigns(
        [camp("a", 1.0, "finance"), camp("b", 2.0, "podcast"), camp("c", 1.5, "gaming")],
        ["tiktok", "youtube"],
        {"finance": 3.0, "podcast": 1.0, "gaming": 1.0},
        {},
        cfg,
    )
    assert [r.campaign.id for r in ranked] == ["a", "b", "c"]  # 1.0*3 beats 2.0*1
    assert niche_leaderboard(ranked)[0][0] == "finance"


def test_rank_skips_campaigns_on_unconnected_platforms():
    ranked = rank_campaigns([camp("x-only", 5.0, platforms=("x",))], ["tiktok"], {}, {}, RankingConfig())
    assert ranked == []


def test_budget_factor_discounts_drained_campaigns():
    assert budget_factor(camp("a", 1, budget=None), 5.0) == 1.0
    assert budget_factor(camp("a", 1, budget=0), 5.0) == 0.0
    assert 0 < budget_factor(camp("a", 1, budget=10), 5.0) < 1.0


def test_own_results_shift_expected_views():
    cfg = RankingConfig(prior_views_per_clip=1000, shrinkage_posts=5)
    observed = {("podcast", "tiktok"): [20000.0] * 20}
    ranked = rank_campaigns([camp("p", 1.0)], ["tiktok", "youtube"], {}, observed, cfg)
    assert ranked[0].expected_views["tiktok"] > 10000
    assert ranked[0].expected_views["youtube"] == 1000


def test_whop_item_parsing():
    c = WhopApifySource.parse_item({
        "campaignId": "abc", "title": "Clip my podcast", "brand": "Pod", "category": "Podcast",
        "rewardPer1kUsd": 2.5, "budgetRemainingUsd": 900, "platforms": ["TikTok", "Instagram", "YouTube"],
        "resourceUrls": ["https://drive.google.com/drive/folders/xyz", "https://pod.com/brand-guidelines.pdf"],
        "requirements": "Use #podclips and tag @pod", "campaignUrl": "https://whop.com/x",
    })
    assert c.id == "whop:abc" and c.category == "podcast"
    assert c.platforms == ["tiktok", "instagram", "youtube"]
    assert c.source_urls == ["https://drive.google.com/drive/folders/xyz"]
    assert c.required_hashtags == ["#podclips"] and c.required_mentions == ["@pod"]


def _row(style, views, platform="tiktok", age_h=72, hook="question", bucket="30-45"):
    return {"style": style, "views": views, "platform": platform, "posted_at": time.time() - age_h * 3600,
            "hook_type": hook, "length_bucket": bucket}


def test_optimizer_converges_on_winning_style():
    rows = [_row("karaoke_pop", 20000) for _ in range(15)] + [_row("soft_lowercase", 800) for _ in range(15)]
    opt = Optimizer(rows, OptimizerConfig(explore_floor=0.0), rng=random.Random(1))
    picks = [opt.choose("style", ["karaoke_pop", "soft_lowercase"]) for _ in range(200)]
    assert picks.count("karaoke_pop") > 190
    report = opt.report()["style"]
    assert report[0][0] == "karaoke_pop" and report[0][2] > 0


def test_optimizer_ignores_too_fresh_posts_and_decays_old_ones():
    fresh = [_row("a", 1_000_000, age_h=2)]
    opt = Optimizer(fresh, OptimizerConfig())
    assert opt.report()["style"] == []
    old_win = [_row("a", 50000, age_h=24 * 60) for _ in range(5)] + [_row("b", 500, age_h=24 * 60) for _ in range(5)]
    new_win = [_row("b", 50000, age_h=60) for _ in range(5)] + [_row("a", 500, age_h=60) for _ in range(5)]
    opt = Optimizer(old_win + new_win, OptimizerConfig(half_life_days=7))
    ranking = [name for name, _, _ in opt.report()["style"]]
    assert ranking[0] == "b"  # this week's winner beats last quarter's
