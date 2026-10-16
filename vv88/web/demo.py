"""Seed a throw-away demo data folder and serve the control website (for screenshots / UI work).

    python -m vv88.web.demo --dir /tmp/vv88demo --port 8091 [--brand-dir brand]

Prints the login password. Never point --dir at real data: it is wiped first.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from vv88.config import load_settings
from vv88.models import RenderedClip, SourceVideo
from vv88.store import Store

CAMPAIGNS = [
    {"id": "money-talks", "title": "Money Talks Podcast clips", "brand": "Money Talks", "category": "finance",
     "reward_per_1k_usd": 1.5, "budget_remaining_usd": 6200, "platforms": ["tiktok", "youtube", "instagram", "x"],
     "source_urls": ["https://www.youtube.com/watch?v=demo1"], "required_hashtags": ["#moneytalks"],
     "campaign_url": "https://whop.com/example"},
    {"id": "streamer-highlights", "title": "Streamer highlights", "brand": "LiveDuo", "category": "gaming",
     "reward_per_1k_usd": 2.5, "budget_remaining_usd": 900, "platforms": ["tiktok", "youtube"],
     "source_urls": ["https://www.twitch.tv/videos/demo"]},
    {"id": "late-night", "title": "Late night interview cuts", "brand": "Night Show", "category": "entertainment",
     "reward_per_1k_usd": 1.0, "budget_remaining_usd": 15000, "platforms": ["tiktok", "instagram"],
     "source_urls": []},
]
HOOKS = [("Why do most people never get rich?", "question", "karaoke_pop", 34.0),
         ("I was broke at 25 and this changed it", "story", "dynamic_minimal", 41.5),
         ("Nobody tells you this about saving", "bold_claim", "karaoke_pop", 28.2),
         ("He lost $2 million in one night", "number", "soft_lowercase", 47.9),
         ("The clutch nobody saw coming", "emotion", "karaoke_pop", 22.4),
         ("What would you do with a million?", "question", "dynamic_minimal", 36.0)]


def make_video(path: Path, colour: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={colour}:s=540x960:r=30:d=3", "-f", "lavfi", "-i", "sine=f=330:d=3",
                    "-vf", "drawtext=text='VV88 demo':fontcolor=yellow:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


def seed(root: Path, chdir: bool = True) -> Path:
    """Create a demo folder (config.yaml, campaigns.yaml, data/ with clips, posts, metrics, jobs)."""
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / "campaigns.yaml").write_text(yaml.safe_dump({"campaigns": CAMPAIGNS}, sort_keys=False))
    (root / "config.yaml").write_text(yaml.safe_dump({
        "data_dir": str(root / "data"),
        "discovery": {"manual_file": str(root / "campaigns.yaml")},
        "publishing": {"backend": "dry_run", "platforms": {p: {"enabled": True} for p in
                                                           ("tiktok", "youtube", "instagram", "x")}},
    }))
    if chdir:
        os.chdir(root)
    settings = load_settings(root / "config.yaml", env_path=root / ".env")
    store = Store(settings.data_path / "vv88.db")
    from vv88.discovery.manual import ManualCampaignSource

    store.upsert_campaigns(ManualCampaignSource(root / "campaigns.yaml").fetch())
    for cid, score in (("money-talks", 9.0), ("streamer-highlights", 3.75), ("late-night", 3.0)):
        store.set_campaign_score(cid, score)
    store.add_source(SourceVideo("src1", "money-talks", "https://www.youtube.com/watch?v=demo1", "/nonexistent.mp4",
                                 3600, 1920, 1080, 30, "Money Talks ep. 112"))
    now = time.time()
    colours = ["0x1a1a1a", "0x2b2b10", "0x101a2b", "0x2b1010", "0x102b1a", "0x241030"]
    for i, (hook, htype, style, dur) in enumerate(HOOKS):
        camp = "money-talks" if i < 4 else "streamer-highlights"
        path = settings.data_path / "clips" / camp / f"demo{i}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        make_video(path, colours[i])
        clip = RenderedClip(id=f"demo{i:02d}aaaaaaaaaa", source_id="src1", campaign_id=camp, path=str(path),
                            start=60.0 * i, end=60.0 * i + dur, duration=dur, style=style,
                            length_bucket="30-45" if 30 <= dur < 45 else ("15-30" if dur < 30 else "45-90"),
                            hook_type=htype, hook_text=hook, title=hook, text=hook,
                            hashtags=["#finance", "#money", "#podcast"], cover_ms=1300, score=8.0 - i * 0.6)
        store.add_clip(clip)
        pays = next(c["platforms"] for c in CAMPAIGNS if c["id"] == camp)
        for j, platform in enumerate(p for p in ("tiktok", "youtube", "instagram", "x") if p in pays):
            when = now - (6 - i) * 86400 + j * 3600 if i < 4 else now + (i - 3) * 5400 + j * 1800
            pid = store.schedule_post(clip.id, platform, when, {
                "platform": platform, "caption": f"{hook}\n\n#moneytalks #fyp", "title": hook if platform == "youtube" else "",
                "tags": ["moneytalks"], "paid_partnership": True, "cover_ms": 1300})
            if i < 4 and pid:
                status = "failed" if (i == 3 and platform == "x") else "posted"
                store.mark_post(pid, status, f"dry:demo{i}{platform}" if status == "posted" else "",
                                f"https://example.com/{platform}/demo{i}" if status == "posted" else "",
                                "x media finalize failed: HTTP 403 {}" if status == "failed" else "")
                if status == "posted":
                    store.db.execute("UPDATE posts SET posted_at=? WHERE id=?", (when, pid))
                    views = int([18000, 4200, 9100, 2600][i] * [1.6, 0.9, 1.1, 0.4][j])
                    store.add_metric(pid, views, views // 20, views // 150, views // 300)
    store.db.commit()
    for kind, status, took in (("run_cycle", "done", 95), ("discover", "done", 4), ("clip", "failed", 12),
                               ("publish_due", "done", 2)):
        jid = store.add_job(kind, {"source": "demo"})
        store.update_job(jid, status=status, started_at=now - 3600, finished_at=now - 3600 + took,
                         log=f"12:00:00 INFO vv88.pipeline: demo {kind} log line\n12:00:01 INFO done",
                         result={"error": "yt-dlp: Video unavailable"} if status == "failed" else {"ok": True})
    return root / "config.yaml"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/tmp/vv88demo")
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--brand-dir", default="brand")
    ap.add_argument("--seed-only", action="store_true")
    args = ap.parse_args()
    root = Path(args.dir).resolve()
    seed(root)
    print(f"seeded {root}")
    if args.seed_only:
        return
    os.environ["VV88_WEB_PASSWORD"] = "demo-password"
    from vv88.web.app import serve

    print("login password: demo-password")
    serve(host="127.0.0.1", port=args.port, config_path=str(root / "config.yaml"), env_path=str(root / ".env"),
          brand_dir=args.brand_dir)


if __name__ == "__main__":
    main()
