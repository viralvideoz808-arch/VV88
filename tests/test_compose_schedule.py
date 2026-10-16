import random
from datetime import datetime, timezone

from vv88.config import PlatformConfig, PublishingConfig
from vv88.models import Campaign, RenderedClip
from vv88.publishing.compose import build_hashtags, compose_post
from vv88.publishing.schedule import next_slot
from vv88.publishing.tiktok import chunk_plan


def clip(**kw):
    base = dict(id="c1", source_id="s", campaign_id="k", path="/x.mp4", start=0, end=30, duration=30,
                style="karaoke_pop", length_bucket="30-45", hook_type="question", hook_text="Why nobody tells you this",
                title="Why nobody tells you this", text="...", hashtags=["#money", "#investing", "#finance"],
                captions={"default": "The one money rule nobody teaches."})
    base.update(kw)
    return RenderedClip(**base)


CAMPAIGN = Campaign(id="k", title="t", required_hashtags=["#brandclips"], required_mentions=["@brand"],
                    paid_partnership=True)


def test_required_hashtags_always_kept_and_capped():
    tags = build_hashtags(["#brand"], ["#a", "#b", "#c", "#d"], ["#fyp", "#viral"], max_tags=4)
    assert tags[0] == "#brand"
    assert "#fyp" in tags
    assert len(tags) == 4
    assert build_hashtags(["#r1", "#r2", "#r3"], ["#a"], [], max_tags=2)[:3] == ["#r1", "#r2", "#r3"]


def test_tiktok_caption_has_disclosure_mentions_and_tags():
    pub = PublishingConfig()
    post = compose_post(clip(), CAMPAIGN, "tiktok", pub.platforms["tiktok"], pub)
    assert post.caption.startswith("#ad The one money rule")
    assert "@brand" in post.caption and "#brandclips" in post.caption and "#fyp" in post.caption
    assert post.paid_partnership


def test_x_post_fits_280():
    pub = PublishingConfig()
    long = clip(captions={"default": "word " * 120})
    post = compose_post(long, CAMPAIGN, "x", pub.platforms["x"], pub)
    assert len(post.caption) <= 280


def test_youtube_title_gets_shorts_tag_and_limits():
    pub = PublishingConfig()
    post = compose_post(clip(title="x" * 150), None, "youtube", pub.platforms["youtube"], pub)
    assert len(post.title) <= 100
    p2 = compose_post(clip(), None, "youtube", pub.platforms["youtube"], pub)
    assert p2.title.endswith("#Shorts") and not p2.paid_partnership


def test_scheduler_respects_cap_and_gap():
    pcfg = PlatformConfig(enabled=True, daily_cap=2, min_gap_minutes=120)
    now = datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc).timestamp()
    rng = random.Random(0)
    taken: list[float] = []
    for _ in range(6):
        t = next_slot(taken, pcfg, ["02:00", "03:00", "12:00", "21:00"], now=now, jitter_minutes=0, rng=rng)
        assert t is not None
        taken.append(t)
    days = {}
    for t in taken:
        d = datetime.fromtimestamp(t, tz=timezone.utc).date()
        days[d] = days.get(d, 0) + 1
    assert max(days.values()) <= 2
    s = sorted(taken)
    assert all(b - a >= 120 * 60 for a, b in zip(s, s[1:]))


def test_tiktok_chunk_plan_follows_media_transfer_rules():
    assert chunk_plan(3_000_000) == (3_000_000, 1)
    size, chunks = chunk_plan(52_000_000)
    assert size == 10 * 1024 * 1024 and chunks == 52_000_000 // size
    assert 52_000_000 - size * (chunks - 1) <= 128 * 1024 * 1024
