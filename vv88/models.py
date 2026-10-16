"""Core data types shared across the pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

PLATFORMS = ("tiktok", "youtube", "instagram", "x")

_PLATFORM_ALIASES = {
    "tiktok": "tiktok",
    "tik tok": "tiktok",
    "youtube": "youtube",
    "youtube shorts": "youtube",
    "yt": "youtube",
    "shorts": "youtube",
    "instagram": "instagram",
    "instagram reels": "instagram",
    "ig": "instagram",
    "reels": "instagram",
    "x": "x",
    "twitter": "x",
    "x (twitter)": "x",
}


def normalize_platform(name: str) -> str | None:
    return _PLATFORM_ALIASES.get(name.strip().lower())


@dataclass
class Campaign:
    id: str
    title: str
    brand: str = ""
    category: str = "general"
    reward_per_1k_usd: float = 0.0
    budget_remaining_usd: float | None = None
    total_budget_usd: float | None = None
    platforms: list[str] = field(default_factory=list)
    # Platforms the campaign named that VV88 can't post to (e.g. facebook, snapchat). Kept so that a campaign
    # paying only there isn't mistaken for one that names no platforms (= pays anywhere).
    other_platforms: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    campaign_url: str = ""
    requirements: str = ""
    required_hashtags: list[str] = field(default_factory=list)
    required_mentions: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    min_views_for_payout: int = 0
    paid_partnership: bool = True
    status: str = "active"
    origin: str = "manual"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def platforms_on(self, available: list[str]) -> list[str]:
        """Which of `available` this campaign pays for: its supported platforms, none if it only names
        platforms VV88 can't post to, or all of them if it names no platforms at all."""
        if self.platforms:
            return [p for p in self.platforms if p in available]
        if self.other_platforms:
            return []
        return list(available)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Campaign":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Word:
    text: str
    start: float
    end: float
    prob: float = 1.0


@dataclass
class Transcript:
    words: list[Word]
    language: str = "en"
    duration: float = 0.0

    def words_between(self, t0: float, t1: float) -> list[Word]:
        return [w for w in self.words if w.start >= t0 - 1e-6 and w.end <= t1 + 1e-6]

    def text_between(self, t0: float, t1: float) -> str:
        return " ".join(w.text.strip() for w in self.words_between(t0, t1)).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "duration": self.duration,
            "words": [asdict(w) for w in self.words],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transcript":
        return cls(
            words=[Word(**w) for w in d.get("words", [])],
            language=d.get("language", "en"),
            duration=float(d.get("duration", 0.0)),
        )


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    word_start_idx: int
    word_end_idx: int  # exclusive


@dataclass
class ClipCandidate:
    start: float
    end: float
    score: float
    text: str
    hook_text: str = ""
    hook_type: str = "statement"
    title: str = ""
    captions: dict[str, str] = field(default_factory=dict)
    hashtags: list[str] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class SourceVideo:
    id: str
    campaign_id: str
    url: str
    path: str
    duration: float
    width: int
    height: int
    fps: float
    title: str = ""


@dataclass
class RenderedClip:
    id: str
    source_id: str
    campaign_id: str
    path: str
    start: float
    end: float
    duration: float
    style: str
    length_bucket: str
    hook_type: str
    hook_text: str
    title: str
    text: str
    captions: dict[str, str] = field(default_factory=dict)
    hashtags: list[str] = field(default_factory=list)
    cover_ms: int = 0
    score: float = 0.0


@dataclass
class PostContent:
    platform: str
    caption: str
    title: str = ""
    tags: list[str] = field(default_factory=list)
    paid_partnership: bool = False
    cover_ms: int = 0


@dataclass
class PostResult:
    platform: str
    ok: bool
    remote_id: str = ""
    url: str = ""
    status: str = ""
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def stable_id(*parts: Any, length: int = 16) -> str:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return h[:length]


def length_bucket(seconds: float) -> str:
    if seconds < 30:
        return "15-30"
    if seconds < 45:
        return "30-45"
    return "45-90"
