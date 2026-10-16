"""Command-line entry point: `vv88 <command>`."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from vv88.config import Settings, load_settings


def _engine(settings: Settings):
    from vv88.pipeline import Engine

    return Engine(settings)


def cmd_doctor(settings: Settings, _args) -> int:
    ok = True

    def line(good: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'ok' if good else '!!'}] {label}{(' - ' + detail) if detail else ''}")

    print("Editing engine (free, local):")
    ff = shutil.which("ffmpeg")
    line(bool(ff), "ffmpeg", ff or "install FFmpeg")
    if ff:
        filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        line(" ass " in filters, "libass (animated captions)")
        line(" loudnorm " in filters, "loudnorm (-14 LUFS audio)")
    for mod, label, extra in [
        ("faster_whisper", "faster-whisper (transcription)", "transcribe"),
        ("cv2", "OpenCV (face-tracked reframing)", "vision"),
        ("yt_dlp", "yt-dlp (source downloads)", "download"),
    ]:
        try:
            __import__(mod)
            line(True, label)
        except ImportError:
            line(False, label, f"pip install 'vv88[{extra}]'")
    fonts = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True).stdout if shutil.which(
        "fc-list") else ""
    font_ok = settings.editing.font.split()[0].lower() in fonts.lower() or bool(settings.editing.fonts_dir)
    line(font_ok, f"caption font '{settings.editing.font}'", "" if font_ok else "install it or set editing.fonts_dir")

    print("Discovery:")
    line(Path(settings.discovery.manual_file).is_file(), f"manual campaigns file {settings.discovery.manual_file}")
    w = settings.discovery.whop_apify
    line(not w.enabled or bool(w.token), "Whop via Apify", "enabled" if w.enabled else "disabled")
    line(True, "YouTube niche signal", "on" if settings.niche.youtube_api_key else "off (set YOUTUBE_API_KEY)")

    print(f"Publishing (backend: {settings.publishing.backend}):")
    enabled = settings.publishing.enabled_platforms()
    line(bool(enabled) or settings.publishing.backend == "dry_run", "platforms enabled", ", ".join(enabled) or "none")
    if settings.publishing.backend == "postiz":
        pz = settings.publishing.postiz
        line(bool(pz.api_key), "POSTIZ_API_KEY")
        for p in enabled:
            line(bool(pz.integrations.get(p)), f"postiz integration id for {p}")
    if settings.publishing.backend == "direct":
        d = settings.publishing.direct
        need = {
            "youtube": d.youtube_refresh_token,
            "instagram": d.instagram_access_token,
            "tiktok": d.tiktok_access_token or d.tiktok_refresh_token,
            "x": d.x_access_token or d.x_refresh_token,
        }
        for p in enabled:
            line(bool(need.get(p)), f"{p} credentials")
    print(f"LLM re-ranking: {'on (' + settings.llm.model + ')' if settings.llm.enabled else 'off (free mode)'}")
    return 0 if ok else 1


def cmd_discover(settings: Settings, args) -> int:
    engine = _engine(settings)
    campaigns = engine.discover()
    for c in sorted(campaigns, key=lambda c: c.reward_per_1k_usd, reverse=True)[: args.limit]:
        budget = f"${c.budget_remaining_usd:,.0f} left" if c.budget_remaining_usd is not None else "budget ?"
        print(f"${c.reward_per_1k_usd:>5.2f}/1K  {budget:>14}  [{c.category}] {c.title}  ({', '.join(c.platforms)})"
              f"  sources={len(c.source_urls)}")
    print(f"{len(campaigns)} active campaigns")
    return 0


def cmd_rank(settings: Settings, args) -> int:
    from vv88.discovery.ranker import niche_leaderboard

    engine = _engine(settings)
    if not engine.store.campaigns():
        engine.discover()
    ranked = engine.rank()
    print("Niche leaderboard (best expected $ per clip):")
    for niche, usd, count in niche_leaderboard(ranked):
        print(f"  {niche:<16} ${usd:7.2f}/clip  ({count} campaigns)")
    print("\nTop campaigns:")
    for r in ranked[: args.limit]:
        views = ", ".join(f"{p}:{v:,.0f}" for p, v in r.expected_views.items())
        print(f"  ${r.expected_usd_per_clip:7.2f}/clip  {r.campaign.title} [{r.campaign.category}] "
              f"niche x{r.niche_multiplier:.2f} budget x{r.budget_factor:.2f}  views~ {views}")
    return 0


def cmd_clip(settings: Settings, args) -> int:
    from vv88.sourcing import fetch_source

    engine = _engine(settings)
    campaign = engine.store.campaign(args.campaign) if args.campaign else None
    if args.campaign and not campaign:
        engine.discover()
        campaign = engine.store.campaign(args.campaign)
        if not campaign:
            print(f"unknown campaign {args.campaign}", file=sys.stderr)
            return 2
    if args.i_have_rights:
        settings.rights_allowlist.append(args.source)
    src = fetch_source(args.source, campaign, settings, engine.store)
    clips = engine.produce_clips(src, campaign, n=args.n, style=args.style)
    for c in clips:
        print(f"{c.path}  {c.duration:5.1f}s  {c.style:<15} hook[{c.hook_type}]: {c.hook_text}")
    if args.post:
        platforms = campaign.platforms if campaign and campaign.platforms else engine.target_platforms()
        platforms = [p for p in platforms if p in engine.target_platforms()]
        n = engine.schedule(clips, campaign, platforms)
        print(f"scheduled {n} posts")
        for r in engine.publish_due():
            print(json.dumps(r))
    return 0


def cmd_publish(settings: Settings, _args) -> int:
    for r in _engine(settings).publish_due():
        print(json.dumps(r))
    return 0


def cmd_metrics(settings: Settings, _args) -> int:
    print(f"updated metrics for {_engine(settings).collect_metrics()} posts")
    return 0


def cmd_report(settings: Settings, _args) -> int:
    engine = _engine(settings)
    rep = engine.optimizer().report()
    for dim, rows in rep.items():
        print(f"{dim}:")
        for name, n, uplift in rows or [("(no data yet)", 0, 0.0)]:
            print(f"  {name:<18} posts={n:<4} vs platform avg: {uplift:+.1f}%")
    est = engine.earnings_estimate()
    print(f"\nEstimated campaign earnings from measured views: ${est['total_usd']:.2f}")
    for cid, usd in est["by_campaign"].items():
        print(f"  {cid}: ${usd:.2f}")
    return 0


def cmd_links(settings: Settings, args) -> int:
    """Posted URLs per campaign, ready to submit on the campaign page for payout."""
    engine = _engine(settings)
    rows = engine.store.db.execute(
        "SELECT c.campaign_id, p.platform, p.url, p.posted_at FROM posts p JOIN clips c ON c.id = p.clip_id "
        "WHERE p.status = 'posted' AND p.url != '' AND p.posted_at >= ? ORDER BY c.campaign_id, p.posted_at",
        (time.time() - args.days * 86400,),
    ).fetchall()
    current = None
    for r in rows:
        if r["campaign_id"] != current:
            current = r["campaign_id"]
            camp = engine.store.campaign(current)
            print(f"\n{camp.title if camp else current}  {camp.campaign_url if camp else ''}")
        print(f"  {r['platform']:<10} {r['url']}")
    return 0


def cmd_run(settings: Settings, args) -> int:
    engine = _engine(settings)
    while True:
        started = time.time()
        try:
            summary = engine.run_cycle()
            print(json.dumps(summary, default=str))
        except KeyboardInterrupt:
            return 0
        except Exception as e:  # noqa: BLE001 - a daemon should survive one bad cycle
            logging.exception("cycle failed: %s", e)
        if args.once:
            return 0
        time.sleep(max(60.0, args.interval * 60 - (time.time() - started)))


def cmd_styles(_settings: Settings, _args) -> int:
    from vv88.editing.styles import STYLES

    for s in STYLES.values():
        print(f"{s.name:<16} {s.description}")
    return 0


def cmd_benchmark(settings: Settings, args) -> int:
    """Render a synthetic talking-length clip to measure clips/day on this machine."""
    from vv88.editing.render import RenderJob, render_clip
    from vv88.editing.styles import get_style
    from vv88.media import probe, run_ffmpeg
    from vv88.models import Transcript, Word

    secs = args.seconds
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.mp4"
        run_ffmpeg(["-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30:d={secs + 4}", "-f", "lavfi", "-i",
                    f"sine=f=220:d={secs + 4}", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", str(src)], desc="benchmark source")
        words, t = [], 0.3
        while t < secs + 2:
            words.append(Word("benchmark", t, t + 0.28))
            t += 0.36 if len(words) % 12 else 1.0
        job = RenderJob(str(src), probe(src), Transcript(words=words), 1.0, 1.0 + secs, get_style(args.style),
                        "How fast is this machine?", Path(tmp) / "out.mp4")
        t0 = time.time()
        out = render_clip(job, settings.editing, Path(tmp) / "work")
        elapsed = time.time() - t0
    per_day = 86400 / elapsed
    print(f"rendered a {out.duration:.1f}s 1080x1920 clip in {elapsed:.1f}s "
          f"({out.duration / elapsed:.2f}x realtime) -> ~{per_day:,.0f} clips/day on this machine, $0")
    print("(transcription adds roughly 0.1-0.3x the source length per source video with whisper 'small' on CPU)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vv88", description="Automated clipping engine")
    parser.add_argument("-c", "--config", default=None, help="config YAML (default: config.yaml / $VV88_CONFIG)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check tools, credentials and config")
    p = sub.add_parser("discover", help="fetch campaigns from all sources")
    p.add_argument("--limit", type=int, default=30)
    p = sub.add_parser("rank", help="rank campaigns and niches by expected $ per clip")
    p.add_argument("--limit", type=int, default=15)
    p = sub.add_parser("clip", help="clip one source (URL or file)")
    p.add_argument("source")
    p.add_argument("--campaign", help="campaign id (its rules, tags and rights apply)")
    p.add_argument("-n", type=int, default=None, help="number of clips")
    p.add_argument("--style", default=None)
    p.add_argument("--post", action="store_true", help="schedule + publish to enabled platforms")
    p.add_argument("--i-have-rights", action="store_true",
                   help="confirm you own or have permission to clip this source")
    sub.add_parser("publish", help="publish posts whose slot has arrived")
    sub.add_parser("metrics", help="pull view counts for recent posts")
    sub.add_parser("report", help="what's working (style/length/hook) + earnings estimate")
    p = sub.add_parser("links", help="posted URLs per campaign, for payout submission")
    p.add_argument("--days", type=float, default=7)
    p = sub.add_parser("run", help="run the full loop")
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=float, default=30, help="minutes between cycles")
    sub.add_parser("styles", help="list caption/layout styles")
    p = sub.add_parser("benchmark", help="measure local rendering throughput")
    p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--style", default="karaoke_pop")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings(args.config)
    handlers = {
        "doctor": cmd_doctor,
        "discover": cmd_discover,
        "rank": cmd_rank,
        "clip": cmd_clip,
        "publish": cmd_publish,
        "metrics": cmd_metrics,
        "report": cmd_report,
        "links": cmd_links,
        "run": cmd_run,
        "styles": cmd_styles,
        "benchmark": cmd_benchmark,
    }
    return handlers[args.command](settings, args)
