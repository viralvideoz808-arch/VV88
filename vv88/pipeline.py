"""The engine: discover -> rank -> fetch -> find highlights -> render -> schedule -> publish -> learn."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

from vv88.analysis.highlights import AnalysisSignals, HighlightFinder, suggest_hashtags
from vv88.analysis.transcribe import load_or_transcribe
from vv88.config import Settings
from vv88.discovery.manual import ManualCampaignSource
from vv88.discovery.niche import NicheTrafficSignal
from vv88.discovery.ranker import RankedCampaign, observed_views_by_niche, rank_campaigns
from vv88.discovery.whop_apify import WhopApifySource
from vv88.editing.render import RenderJob, render_clip
from vv88.editing.styles import STYLES, get_style
from vv88.media import audio_rms_db, probe, require_ffmpeg, scene_cuts
from vv88.models import PLATFORMS, Campaign, ClipCandidate, PostContent, RenderedClip, SourceVideo, length_bucket, stable_id
from vv88.optimizer import LENGTH_TARGETS, Optimizer
from vv88.publishing.base import PublishError
from vv88.publishing.compose import compose_post
from vv88.publishing.factory import make_metrics_reader, make_publisher
from vv88.publishing.schedule import next_slot
from vv88.sourcing import fetch_source
from vv88.store import Store

log = logging.getLogger(__name__)

_RETRYABLE = ("429", "quota", "rate limit", "spam_risk_too_many_posts", "timed out")


class Engine:
    def __init__(self, settings: Settings):
        self.s = settings
        self.store = Store(settings.data_path / "vv88.db")

    # ---- discovery & ranking ---------------------------------------------------------
    def target_platforms(self) -> list[str]:
        enabled = self.s.publishing.enabled_platforms()
        if not enabled and self.s.publishing.backend == "dry_run":
            return list(PLATFORMS)
        return enabled

    def discover(self) -> list[Campaign]:
        found: list[Campaign] = []
        for source in (ManualCampaignSource(self.s.discovery.manual_file), WhopApifySource(self.s.discovery.whop_apify)):
            try:
                got = source.fetch()
                log.info("discovered %d campaigns from %s", len(got), source.name)
                found.extend(got)
            except Exception as e:  # noqa: BLE001 - one bad source shouldn't stop the run
                log.warning("campaign source %s failed: %s", source.name, e)
        self.store.upsert_campaigns(found)
        self.store.kv_set("last_discovery", time.time())
        return self.store.campaigns()

    def rank(self) -> list[RankedCampaign]:
        campaigns = self.store.campaigns()
        signal = NicheTrafficSignal(self.s.niche, self.store)
        multipliers = signal.multipliers([c.category for c in campaigns])
        observed = observed_views_by_niche(self.store.performance_rows())
        ranked = rank_campaigns(campaigns, self.target_platforms(), multipliers, observed, self.s.ranking)
        for r in ranked:
            self.store.set_campaign_score(r.campaign.id, r.expected_usd_per_clip)
        return ranked

    # ---- analysis & rendering --------------------------------------------------------
    def optimizer(self) -> Optimizer:
        return Optimizer(self.store.performance_rows(), self.s.optimizer)

    def available_styles(self) -> list[str]:
        styles = [n for n in self.s.editing.styles if n in STYLES]
        if not self.s.editing.gameplay_dir:
            styles = [n for n in styles if not STYLES[n].split_screen]
        return styles or ["karaoke_pop"]

    def find_highlights(self, src: SourceVideo, campaign: Campaign | None, n: int, opt: Optimizer,
                        target_seconds: float | None = None) -> tuple[list[ClipCandidate], object]:
        transcript = load_or_transcribe(src.path, src.id, self.s.data_path, self.s.analysis)
        signals = AnalysisSignals(loudness_db=audio_rms_db(src.path))
        if self.s.analysis.use_scene_detection:
            signals.scene_cuts = scene_cuts(src.path)
        acfg = self.s.analysis.model_copy()
        if target_seconds:
            acfg.target_clip_seconds = target_seconds
        keywords: list[str] = []
        if campaign:
            keywords = [*campaign.keywords, *([campaign.brand] if campaign.brand else [])]
        finder = HighlightFinder(acfg, keywords=keywords)
        cands = finder.candidates(transcript, signals)
        bonus = opt.hook_bonus()
        if bonus:
            for c in cands:
                c.score += bonus.get(c.hook_type, 0.0)
            cands.sort(key=lambda c: c.score, reverse=True)
        taken = self.store.clip_ranges(src.id)
        if self.s.llm.enabled:
            from vv88.analysis.llm import rerank_with_claude

            shortlist = finder.select(cands, self.s.llm.max_candidates, taken)
            picks = rerank_with_claude(shortlist, campaign, self.s.llm, n)
        else:
            picks = finder.select(cands, n, taken)
        return picks, transcript

    def produce_clips(self, src: SourceVideo, campaign: Campaign | None, n: int | None = None,
                      style: str | None = None) -> list[RenderedClip]:
        require_ffmpeg()
        n = n or self.s.analysis.clips_per_source
        opt = self.optimizer()
        bucket = opt.choose("length_bucket", list(LENGTH_TARGETS))
        picks, transcript = self.find_highlights(src, campaign, n, opt, LENGTH_TARGETS[bucket])
        info = probe(src.path)
        styles = self.available_styles()
        out: list[RenderedClip] = []
        for cand in picks:
            style_name = style or self.s.editing.force_style or opt.choose("style", styles)
            st = get_style(style_name)
            cid = stable_id(src.id, round(cand.start, 2), round(cand.end, 2), style_name)
            campaign_dir = (campaign.id if campaign else "adhoc").replace(":", "_")
            path = self.s.data_path / "clips" / campaign_dir / f"{cid}.mp4"
            t0 = time.time()
            result = render_clip(
                RenderJob(src.path, info, transcript, cand.start, cand.end, st, cand.hook_text, path),
                self.s.editing,
                self.s.data_path / "work",
            )
            log.info("rendered %s (%s, %.1fs, %s crop) in %.1fs", path.name, style_name, result.duration,
                     result.crop_mode, time.time() - t0)
            clip = RenderedClip(
                id=cid,
                source_id=src.id,
                campaign_id=campaign.id if campaign else "adhoc",
                path=str(path),
                start=cand.start,
                end=cand.end,
                duration=result.duration,
                style=style_name,
                length_bucket=length_bucket(result.duration),
                hook_type=cand.hook_type,
                hook_text=cand.hook_text,
                title=cand.title or cand.hook_text,
                text=cand.text,
                captions=cand.captions,
                hashtags=cand.hashtags or suggest_hashtags(
                    cand.text,
                    campaign.category if campaign else "",
                    campaign.keywords if campaign else [],
                ),
                cover_ms=int(min(self.s.editing.hook_seconds, result.duration) * 500),
                score=cand.score,
            )
            self.store.add_clip(clip)
            out.append(clip)
        return out

    # ---- scheduling & publishing -----------------------------------------------------
    def schedule(self, clips: list[RenderedClip], campaign: Campaign | None, platforms: list[str]) -> int:
        pub = self.s.publishing
        n = 0
        for clip in clips:
            for p in platforms:
                pcfg = pub.platforms.get(p)
                if pcfg is None:
                    continue
                existing = self.store.post_times(p, since=time.time() - 86400)
                when = next_slot(existing, pcfg, pub.slots_utc, jitter_minutes=pub.jitter_minutes)
                if when is None:
                    log.info("%s queue is full for the next week; not scheduling %s", p, clip.id)
                    continue
                content = compose_post(clip, campaign, p, pcfg, pub)
                if self.store.schedule_post(clip.id, p, when, asdict(content)):
                    n += 1
        return n

    def queue_depth(self, platform: str) -> int:
        return len([t for t in self.store.post_times(platform, since=time.time()) if t >= time.time()])

    def publish_due(self) -> list[dict]:
        dry = self.s.publishing.backend == "dry_run"
        # Dry runs write straight to the outbox; real backends wait for each slot.
        rows = self.store.due_posts(now=1e18 if dry else None)
        publishers: dict = {}
        results = []
        for row in rows:
            clip = self.store.clip(row["clip_id"])
            if clip is None or not Path(clip.path).exists():
                self.store.mark_post(row["id"], "failed", error="clip file missing")
                continue
            platform = row["platform"]
            content = PostContent(**json.loads(row["content"]))
            try:
                pub = publishers.get(platform) or make_publisher(platform, self.s, self.store)
                publishers[platform] = pub
                res = pub.publish(clip.path, content)
                self.store.mark_post(row["id"], "posted", res.remote_id, res.url)
                results.append({"platform": platform, "clip": clip.id, "ok": True, "url": res.url,
                                "status": res.status})
            except (PublishError, OSError, ValueError) as e:
                msg = str(e)
                if any(k in msg.lower() for k in _RETRYABLE):
                    self.store.reschedule(row["id"], time.time() + 2 * 3600)
                    log.warning("%s rate-limited, retrying in 2h: %s", platform, msg)
                else:
                    self.store.mark_post(row["id"], "failed", error=msg[:1000])
                    log.error("%s publish failed: %s", platform, msg)
                results.append({"platform": platform, "clip": clip.id, "ok": False, "error": msg[:300]})
        return results

    # ---- learning --------------------------------------------------------------------
    def collect_metrics(self, min_age_hours: float = 6, max_age_days: float = 14) -> int:
        rows = self.store.posts_for_metrics(min_age_hours * 3600, max_age_days * 86400)
        readers: dict = {}
        n = 0
        for row in rows:
            remote = row["remote_id"] or ""
            if remote.startswith(("dry:", "postiz:")):
                continue
            platform = row["platform"]
            if platform not in readers:
                readers[platform] = make_metrics_reader(platform, self.s, self.store)
            reader = readers[platform]
            if reader is None:
                continue
            try:
                m = reader.fetch_metrics(remote)
            except Exception as e:  # noqa: BLE001
                log.warning("metrics for %s %s failed: %s", platform, remote, e)
                continue
            if m:
                self.store.add_metric(row["id"], m.get("views", 0), m.get("likes", 0), m.get("comments", 0),
                                      m.get("shares", 0))
                n += 1
        return n

    def earnings_estimate(self) -> dict:
        total, by_campaign = 0.0, {}
        for r in self.store.performance_rows():
            if r["views"] is None or r["reward_per_1k"] is None:
                continue
            usd = r["views"] / 1000.0 * float(r["reward_per_1k"])
            by_campaign[r["campaign_id"]] = by_campaign.get(r["campaign_id"], 0.0) + usd
            total += usd
        return {"total_usd": round(total, 2), "by_campaign": {k: round(v, 2) for k, v in by_campaign.items()}}

    # ---- the loop --------------------------------------------------------------------
    def run_cycle(self) -> dict:
        summary: dict = {"rendered": 0, "scheduled": 0, "published": 0, "metrics": 0, "campaigns": []}
        last = self.store.kv_get("last_discovery") or 0
        if time.time() - last > self.s.discovery.refresh_hours * 3600:
            self.discover()
        ranked = self.rank()
        platforms = self.target_platforms()
        caps = {p: self.s.publishing.platforms[p].daily_cap for p in platforms if p in self.s.publishing.platforms}
        # Back-pressure: don't render faster than the accounts can post.
        hungry = [p for p in platforms if self.queue_depth(p) < 2 * caps.get(p, 0)]
        if not hungry:
            log.info("all posting queues are full; skipping rendering this cycle")
        for r in ranked[: self.s.ranking.campaigns_per_cycle] if hungry else []:
            summary["campaigns"].append(
                {"id": r.campaign.id, "title": r.campaign.title, "niche": r.campaign.category,
                 "usd_per_clip": round(r.expected_usd_per_clip, 2)}
            )
            if not r.campaign.source_urls:
                log.info("campaign %s lists no downloadable footage; add source_urls in campaigns.yaml",
                         r.campaign.id)
            for url in r.campaign.source_urls:
                try:
                    src = fetch_source(url, r.campaign, self.s, self.store)
                    clips = self.produce_clips(src, r.campaign)
                except Exception as e:  # noqa: BLE001 - try the next source
                    log.warning("source %s failed: %s", url, e)
                    continue
                if not clips:
                    continue  # source exhausted; try the next one
                summary["rendered"] += len(clips)
                summary["scheduled"] += self.schedule(clips, r.campaign, [p for p in r.platforms if p in hungry])
                break
        results = self.publish_due()
        summary["published"] = sum(1 for x in results if x["ok"])
        summary["publish_errors"] = [x for x in results if not x["ok"]]
        summary["metrics"] = self.collect_metrics()
        return summary
