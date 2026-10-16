"""Configuration: YAML file + environment variables (secrets never live in YAML)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand_env(value):
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines); existing env vars win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if val[:1] in ("'", '"') and val[:1] in val[1:]:
            val = val[1 : val.index(val[0], 1)]
        else:
            # Unquoted values may carry a trailing "# comment" (as in .env.example).
            val = "" if val.startswith("#") else re.split(r"\s+#", val, maxsplit=1)[0].strip()
        os.environ.setdefault(key.strip(), val)


class _Model(BaseModel):
    """Expands ${ENV_VAR} / ${ENV_VAR:-default} in every field, defaults included."""

    model_config = ConfigDict(validate_default=True)

    @field_validator("*", mode="before")
    @classmethod
    def _env(cls, v):
        return _expand_env(v)


class WhopApifyConfig(_Model):
    enabled: bool = False
    token: str = "${APIFY_TOKEN}"
    # Third-party Apify actor that scrapes Whop's public Content Rewards listings.
    actor: str = "automation-lab~whop-content-rewards-scraper"
    max_items: int = 100
    min_reward_per_1k_usd: float = 0.0
    min_budget_remaining_usd: float = 50.0
    campaign_type: Literal["all", "clipping", "ugc", "both"] = "clipping"


class DiscoveryConfig(_Model):
    manual_file: str = "campaigns.yaml"
    whop_apify: WhopApifyConfig = Field(default_factory=WhopApifyConfig)
    refresh_hours: float = 6.0


class NicheSignalConfig(_Model):
    """Measures how much traffic each niche is getting *this week* on YouTube Shorts."""

    youtube_api_key: str = "${YOUTUBE_API_KEY}"
    region_code: str = "US"
    lookback_days: int = 7
    sample_size: int = 25
    cache_hours: float = 24.0
    # Extra search terms per niche (category name is always used).
    keywords: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "finance": ["money", "investing", "stocks"],
            "entertainment": ["funny", "celebrity"],
            "gaming": ["gaming", "streamer"],
            "podcast": ["podcast clips"],
            "sports": ["sports highlights"],
            "business": ["entrepreneur", "business advice"],
            "music": ["music", "rapper"],
            "tech": ["tech", "ai"],
        }
    )


class RankingConfig(_Model):
    # Prior expected views per clip per platform before the system has its own data.
    prior_views_per_clip: float = 1500.0
    # How many of our own posts in a niche before we fully trust our own numbers.
    shrinkage_posts: int = 20
    campaigns_per_cycle: int = 2


class AnalysisConfig(_Model):
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    language: str | None = None
    min_clip_seconds: float = 20.0
    max_clip_seconds: float = 60.0
    # Viral-tier median is ~41 s (Opus Clip 2026 dataset); aim slightly under.
    target_clip_seconds: float = 38.0
    clips_per_source: int = 5
    max_overlap: float = 0.15
    use_scene_detection: bool = True


class LLMConfig(_Model):
    enabled: bool = False
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_candidates: int = 15


class EditingConfig(_Model):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 20
    preset: str = "veryfast"
    threads: int = 0
    styles: list[str] = Field(
        default_factory=lambda: ["karaoke_pop", "dynamic_minimal", "soft_lowercase", "brainrot_stack"]
    )
    # Force a single style instead of letting the optimizer choose.
    force_style: str | None = None
    font: str = "Inter Black"
    fonts_dir: str | None = None
    # Folder of royalty-free / self-owned gameplay loops for the split-screen style.
    gameplay_dir: str | None = None
    jump_cut_gap: float = 0.35
    loudness_lufs: float = -14.0
    hook_seconds: float = 2.6
    progress_bar: bool = True
    punch_zoom: float = 0.08
    reframe: Literal["auto", "face", "center", "blur"] = "auto"
    face_model: str | None = None  # optional YuNet .onnx path


class PlatformConfig(_Model):
    enabled: bool = False
    daily_cap: int = 6
    min_gap_minutes: int = 90
    max_hashtags: int = 10
    generic_hashtags: list[str] = Field(default_factory=list)


def _platform(**kw) -> PlatformConfig:
    return PlatformConfig(**kw)


class PostizConfig(_Model):
    api_url: str = "${POSTIZ_API_URL:-https://api.postiz.com/public/v1}"
    api_key: str = "${POSTIZ_API_KEY}"
    # Postiz integration ids per platform; `vv88 connect postiz` fills these in .env automatically.
    integrations: dict[str, str] = Field(
        default_factory=lambda: {
            "tiktok": "${POSTIZ_INTEGRATION_TIKTOK}",
            "youtube": "${POSTIZ_INTEGRATION_YOUTUBE}",
            "instagram": "${POSTIZ_INTEGRATION_INSTAGRAM}",
            "x": "${POSTIZ_INTEGRATION_X}",
        }
    )


class DirectAPIConfig(_Model):
    youtube_client_id: str = "${YOUTUBE_CLIENT_ID}"
    youtube_client_secret: str = "${YOUTUBE_CLIENT_SECRET}"
    youtube_refresh_token: str = "${YOUTUBE_REFRESH_TOKEN}"
    youtube_category_id: str = "24"
    youtube_privacy: Literal["public", "unlisted", "private"] = "public"

    facebook_app_id: str = "${FACEBOOK_APP_ID}"
    facebook_app_secret: str = "${FACEBOOK_APP_SECRET}"
    # Optional: an https redirect registered on the Meta app (used with `vv88 connect instagram --paste`)
    facebook_redirect_uri: str = "${FACEBOOK_REDIRECT_URI}"
    # Optional: Facebook Login for Business configuration id (replaces the scope list)
    facebook_login_config_id: str = "${FACEBOOK_LOGIN_CONFIG_ID}"
    instagram_user_id: str = "${INSTAGRAM_USER_ID}"
    instagram_access_token: str = "${INSTAGRAM_ACCESS_TOKEN}"
    instagram_graph_version: str = "v23.0"
    instagram_share_to_feed: bool = True

    tiktok_access_token: str = "${TIKTOK_ACCESS_TOKEN}"
    tiktok_refresh_token: str = "${TIKTOK_REFRESH_TOKEN}"
    tiktok_client_key: str = "${TIKTOK_CLIENT_KEY}"
    tiktok_client_secret: str = "${TIKTOK_CLIENT_SECRET}"
    tiktok_privacy: str = "PUBLIC_TO_EVERYONE"

    x_access_token: str = "${X_ACCESS_TOKEN}"
    x_refresh_token: str = "${X_REFRESH_TOKEN}"
    x_client_id: str = "${X_CLIENT_ID}"
    x_client_secret: str = "${X_CLIENT_SECRET}"


class PublishingConfig(_Model):
    backend: Literal["dry_run", "postiz", "direct"] = "dry_run"
    disclose_paid_partnership: bool = True
    disclosure_tag: str = "#ad"
    # Posting slots in UTC. Defaults lean on the top median-view slots in the Opus Clip
    # 2026 TikTok dataset (Sat 12:00, Sat 02:00, Thu 12:00, Mon 06:00, Wed 21:00 UTC).
    slots_utc: list[str] = Field(default_factory=lambda: ["02:00", "06:00", "12:00", "16:00", "21:00"])
    jitter_minutes: int = 7
    platforms: dict[str, PlatformConfig] = Field(
        default_factory=lambda: {
            "tiktok": _platform(daily_cap=5, generic_hashtags=["#fyp", "#viral", "#foryou"], max_hashtags=10),
            "youtube": _platform(daily_cap=8, generic_hashtags=["#shorts"], max_hashtags=5),
            "instagram": _platform(daily_cap=8, generic_hashtags=["#reels", "#viral"], max_hashtags=5),
            "x": _platform(daily_cap=8, generic_hashtags=[], max_hashtags=2),
        }
    )
    postiz: PostizConfig = Field(default_factory=PostizConfig)
    direct: DirectAPIConfig = Field(default_factory=DirectAPIConfig)

    def enabled_platforms(self) -> list[str]:
        return [p for p, c in self.platforms.items() if c.enabled]


class OptimizerConfig(_Model):
    # Results older than this count half as much: keeps the style choice tuned to *this week*.
    half_life_days: float = 7.0
    metric_age_hours: float = 48.0
    explore_floor: float = 0.1


class Settings(_Model):
    data_dir: str = "data"
    rights_mode: Literal["campaign_only", "allowlist"] = "campaign_only"
    rights_allowlist: list[str] = Field(default_factory=list)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    niche: NicheSignalConfig = Field(default_factory=NicheSignalConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    editing: EditingConfig = Field(default_factory=EditingConfig)
    publishing: PublishingConfig = Field(default_factory=PublishingConfig)
    optimizer: OptimizerConfig = Field(default_factory=OptimizerConfig)

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings(path: str | Path | None = None, overrides: dict | None = None,
                  env_path: str | Path | None = None) -> Settings:
    load_dotenv(env_path or os.environ.get("VV88_ENV", ".env"))
    raw: dict = {}
    if path is None:
        path = os.environ.get("VV88_CONFIG", "config.yaml")
    p = Path(path)
    if p.is_file():
        raw = yaml.safe_load(p.read_text()) or {}
    # Merge onto the full defaults so a partial `platforms: {tiktok: {enabled: true}}`
    # keeps TikTok's default caps and hashtags instead of replacing the whole map.
    merged = deep_merge(Settings().model_dump(), raw)
    if overrides:
        merged = deep_merge(merged, overrides)
    return Settings.model_validate(merged)
