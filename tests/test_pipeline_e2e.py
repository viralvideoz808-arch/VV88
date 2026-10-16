"""End-to-end dry run: campaign file -> local source -> highlights -> render -> schedule -> outbox."""

import json

import pytest
import yaml

from tests.conftest import make_transcript
from vv88.config import load_settings
from vv88.media import probe
from vv88.pipeline import Engine
from vv88.sourcing import RightsError, fetch_source


@pytest.fixture
def engine(tmp_path, sample_video, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Sidecar transcript stands in for Whisper so the test is fast and offline.
    long_script = " ".join([
        "Why do most people never get rich? Because nobody tells you this one simple secret about money.",
        "I was broke at 25 and it changed everything. You have to stop trading time for cash.",
        "What would you do with a million dollars? Most people would waste it in two years.",
        "The truth is the biggest mistake is spending before you invest. Never forget that.",
    ] * 2)
    t = make_transcript(long_script, rate=0.34, pause=0.6)
    (sample_video.parent / (sample_video.name + ".transcript.json")).write_text(json.dumps(t.to_dict()))
    (tmp_path / "campaigns.yaml").write_text(yaml.safe_dump({"campaigns": [{
        "id": "money-pod", "title": "Money Pod", "brand": "Money Pod", "category": "finance",
        "reward_per_1k_usd": 2.0, "budget_remaining_usd": 5000, "platforms": ["tiktok", "youtube", "x"],
        "source_urls": [str(sample_video)], "required_hashtags": ["#moneypod"], "required_mentions": ["@moneypod"],
    }]}))
    settings = load_settings(tmp_path / "missing.yaml", overrides={
        "data_dir": str(tmp_path / "data"),
        "niche": {"youtube_api_key": ""},
        "analysis": {"min_clip_seconds": 10, "max_clip_seconds": 25, "target_clip_seconds": 16,
                     "clips_per_source": 2, "use_scene_detection": False},
        "editing": {"preset": "ultrafast", "reframe": "center"},
    })
    return Engine(settings)


@pytest.mark.ffmpeg
def test_run_cycle_dry_run(engine):
    summary = engine.run_cycle()
    assert summary["campaigns"][0]["id"] == "money-pod"
    assert summary["rendered"] == 2
    # 2 clips x 3 campaign platforms
    assert summary["scheduled"] == 6 and summary["published"] == 6
    outbox = engine.s.data_path / "outbox"
    tiktok_posts = list((outbox / "tiktok").glob("*.json"))
    assert len(tiktok_posts) == 2
    post = json.loads(tiktok_posts[0].read_text())
    assert "#moneypod" in post["caption"] and "@moneypod" in post["caption"] and post["paid_partnership"]
    assert not (outbox / "instagram").exists()  # campaign doesn't pay for Instagram
    clip_files = list((engine.s.data_path / "clips").rglob("*.mp4"))
    info = probe(clip_files[0])
    assert (info.width, info.height) == (1080, 1920)
    assert 8 <= info.duration <= 26

    # A second cycle must not re-clip the same moments.
    again = engine.run_cycle()
    ranges = engine.store.clip_ranges(engine.store.campaigns()[0].source_urls and
                                      engine.store.source_by_url(engine.store.campaigns()[0].source_urls[0]).id)
    assert again["rendered"] >= 0 and len(ranges) == len(set(ranges))


def test_rights_guardrail(engine):
    with pytest.raises(RightsError):
        fetch_source("https://www.youtube.com/watch?v=someone-elses-video", None, engine.s, engine.store)
