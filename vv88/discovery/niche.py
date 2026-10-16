"""Niche traffic signal: how many views short-form videos in each niche are pulling *this week*.

Uses the official YouTube Data API (free 10,000 units/day): one `search.list` (100 units)
per niche for Shorts-length videos published in the lookback window, ordered by views,
then one `videos.list` (1 unit) for their stats. The score is the median views-per-hour
of the top results, which tracks real-time demand far better than all-time totals.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone

import requests

from vv88.config import NicheSignalConfig
from vv88.store import Store

log = logging.getLogger(__name__)

YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
YT_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"


class NicheTrafficSignal:
    def __init__(self, cfg: NicheSignalConfig, store: Store, session: requests.Session | None = None):
        self.cfg = cfg
        self.store = store
        self.http = session or requests.Session()

    def views_per_hour(self, niche: str) -> float | None:
        key = f"niche_vph:{self.cfg.region_code}:{niche}"
        cached = self.store.kv_get(key, max_age_s=self.cfg.cache_hours * 3600)
        if cached is not None:
            return cached
        if not self.cfg.youtube_api_key:
            return None
        try:
            value = self._measure(niche)
        except requests.RequestException as e:
            log.warning("niche signal for %s failed: %s", niche, e)
            return None
        self.store.kv_set(key, value)
        return value

    def _measure(self, niche: str) -> float:
        terms = [niche, *self.cfg.keywords.get(niche, [])]
        now = datetime.now(timezone.utc)
        published_after = (now - timedelta(days=self.cfg.lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        search = self.http.get(
            YT_SEARCH,
            params={
                "key": self.cfg.youtube_api_key,
                "part": "id",
                "type": "video",
                "videoDuration": "short",
                "order": "viewCount",
                "publishedAfter": published_after,
                "regionCode": self.cfg.region_code,
                "maxResults": self.cfg.sample_size,
                "q": " | ".join(terms),
            },
            timeout=30,
        )
        search.raise_for_status()
        ids = [it["id"]["videoId"] for it in search.json().get("items", []) if it.get("id", {}).get("videoId")]
        if not ids:
            return 0.0
        videos = self.http.get(
            YT_VIDEOS,
            params={"key": self.cfg.youtube_api_key, "part": "statistics,snippet", "id": ",".join(ids)},
            timeout=30,
        )
        videos.raise_for_status()
        rates = []
        for v in videos.json().get("items", []):
            views = int(v.get("statistics", {}).get("viewCount", 0))
            published = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
            hours = max(1.0, (now - published).total_seconds() / 3600)
            rates.append(views / hours)
        return float(statistics.median(rates)) if rates else 0.0

    def multipliers(self, niches: list[str]) -> dict[str, float]:
        """Relative traffic per niche, normalised so the median niche is 1.0."""
        raw = {n: self.views_per_hour(n) for n in set(niches)}
        known = [v for v in raw.values() if v]
        if not known:
            return {n: 1.0 for n in raw}
        mid = statistics.median(known)
        # Square-root damping: a niche with 4x the traffic is not 4x easier to win in.
        return {n: ((v / mid) ** 0.5 if v else 1.0) for n, v in raw.items()}
