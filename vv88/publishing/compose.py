"""Per-platform post copy: caption length limits, hashtag mix, required tags and disclosure."""

from __future__ import annotations

import re

from vv88.config import PlatformConfig, PublishingConfig
from vv88.models import Campaign, PostContent, RenderedClip

LIMITS = {"tiktok": 2200, "instagram": 2200, "youtube": 5000, "x": 280}
_TAG_OK = re.compile(r"^#\w+$")


def _norm_tag(t: str) -> str:
    t = t.strip()
    if not t:
        return ""
    t = "#" + re.sub(r"[^\w]", "", t.lstrip("#"))
    return t if _TAG_OK.match(t) else ""


def build_hashtags(required: list[str], niche: list[str], generic: list[str], max_tags: int) -> list[str]:
    """Required tags always survive; then niche tags; then generic ones (the 2026 data favours a mix)."""
    out: list[str] = []
    seen: set[str] = set()

    def add(tag: str, force: bool = False) -> None:
        t = _norm_tag(tag)
        if t and t.lower() not in seen and (force or len(out) < max_tags):
            out.append(t)
            seen.add(t.lower())

    for t in required:
        add(t, force=True)
    # Reserve room for generic discovery tags.
    niche_room = max(0, max_tags - len(out) - min(len(generic), max(1, max_tags // 4)))
    for t in niche[:niche_room]:
        add(t)
    for t in generic:
        add(t)
    for t in niche[niche_room:]:
        add(t)
    return out


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: max(0, limit - 1)].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def compose_post(
    clip: RenderedClip,
    campaign: Campaign | None,
    platform: str,
    pcfg: PlatformConfig,
    pub: PublishingConfig,
) -> PostContent:
    required = campaign.required_hashtags if campaign else []
    mentions = campaign.required_mentions if campaign else []
    tags = build_hashtags(required, clip.hashtags, pcfg.generic_hashtags, pcfg.max_hashtags)
    paid = bool(campaign and campaign.paid_partnership and pub.disclose_paid_partnership)
    disclosure = pub.disclosure_tag if paid and pub.disclosure_tag else ""

    base = clip.captions.get(platform) or clip.captions.get("default") or clip.title or clip.hook_text
    base = base.strip()
    tail_parts = [*mentions, *tags]
    tail = " ".join(tail_parts)

    if platform == "x":
        head = f"{disclosure} {base}".strip() if disclosure else base
        room = LIMITS["x"] - len(tail) - (1 if tail else 0)
        text = _truncate(head, max(40, room))
        caption = f"{text} {tail}".strip() if len(text) + len(tail) + 1 <= LIMITS["x"] else text
        return PostContent(platform, caption, paid_partnership=paid, cover_ms=clip.cover_ms)

    if platform == "youtube":
        title = clip.title or clip.hook_text or base
        title = _truncate(title, 90)
        if "#shorts" not in title.lower() and len(title) + 8 <= 100:
            title = f"{title} #Shorts"
        desc = "\n\n".join(p for p in [disclosure, base, tail] if p)
        return PostContent(
            platform,
            _truncate(desc, LIMITS["youtube"]),
            title=title,
            tags=[t.lstrip("#") for t in tags],
            paid_partnership=paid,
            cover_ms=clip.cover_ms,
        )

    head = f"{disclosure} {base}".strip() if disclosure else base
    caption = "\n\n".join(p for p in [head, tail] if p)
    return PostContent(
        platform,
        _truncate(caption, LIMITS.get(platform, 2200)),
        tags=[t.lstrip("#") for t in tags],
        paid_partnership=paid,
        cover_ms=clip.cover_ms,
    )
