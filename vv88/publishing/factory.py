from __future__ import annotations

from vv88.config import Settings
from vv88.publishing.base import DryRunPublisher, Publisher
from vv88.store import Store


def make_publisher(platform: str, settings: Settings, store: Store) -> Publisher:
    pub = settings.publishing
    if pub.backend == "dry_run":
        return DryRunPublisher(platform, settings.data_path / "outbox")
    if pub.backend == "postiz":
        from vv88.publishing.postiz import PostizPublisher

        return PostizPublisher(platform, pub.postiz)
    d = pub.direct
    if platform == "youtube":
        from vv88.publishing.youtube import YouTubePublisher

        return YouTubePublisher(d)
    if platform == "instagram":
        from vv88.publishing.instagram import InstagramPublisher

        return InstagramPublisher(d)
    if platform == "tiktok":
        from vv88.publishing.tiktok import TikTokPublisher

        return TikTokPublisher(d, store)
    if platform == "x":
        from vv88.publishing.x import XPublisher

        return XPublisher(d, store)
    raise ValueError(f"unknown platform {platform}")


def make_metrics_reader(platform: str, settings: Settings, store: Store) -> Publisher | None:
    """Direct API clients double as metrics readers even when posting goes through Postiz."""
    d = settings.publishing.direct
    try:
        if platform == "youtube" and d.youtube_refresh_token:
            from vv88.publishing.youtube import YouTubePublisher

            return YouTubePublisher(d)
        if platform == "instagram" and d.instagram_access_token:
            from vv88.publishing.instagram import InstagramPublisher

            return InstagramPublisher(d)
        if platform == "tiktok" and (d.tiktok_access_token or d.tiktok_refresh_token):
            from vv88.publishing.tiktok import TikTokPublisher

            return TikTokPublisher(d, store)
        if platform == "x" and (d.x_access_token or d.x_refresh_token):
            from vv88.publishing.x import XPublisher

            return XPublisher(d, store)
    except Exception:  # noqa: BLE001 - missing creds just means no metrics for that platform
        return None
    return None
