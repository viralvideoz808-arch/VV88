"""Optional Claude pass: re-rank the heuristic shortlist and write hooks, titles and captions.

Off by default (the rest of the pipeline is free). When enabled it sends only the
shortlisted windows' transcript text — not the video — so a run costs a few cents.
"""

from __future__ import annotations

import json
import logging

from vv88.config import LLMConfig
from vv88.models import Campaign, ClipCandidate

log = logging.getLogger(__name__)

HOOK_TYPES = ["question", "number", "bold_claim", "story", "emotion", "statement"]

SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "integer"},
                    "virality": {"type": "integer"},
                    "hook_text": {"type": "string"},
                    "hook_type": {"type": "string", "enum": HOOK_TYPES},
                    "title": {"type": "string"},
                    "caption": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["candidate_id", "virality", "hook_text", "hook_type", "title", "caption", "hashtags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

SYSTEM = """You are the editor-in-chief of a short-form clipping team. You pick the moments from \
long videos that will hold a cold audience on TikTok, YouTube Shorts, Instagram Reels and X, and you \
write the on-screen hook and post copy.

What wins right now: the payoff or tension is clear inside the first 1-3 seconds; the clip stands on \
its own without context from the rest of the video; one idea per clip; it ends on a punchline, \
reveal or strong statement rather than trailing off.

Hook text is burned onto the first ~2.5 seconds of video: at most 7 words, curiosity-gap phrasing, \
no clickbait that the clip doesn't pay off, no emojis. Titles are under 70 characters. Captions are \
1-2 short sentences that add context or provoke a comment. Hashtags: 5-8 niche-specific tags \
(no generic ones like #fyp or #viral; those are added later), each starting with #.

Never invent claims that aren't in the transcript. Respect the campaign's requirements."""


def rerank_with_claude(
    candidates: list[ClipCandidate],
    campaign: Campaign | None,
    cfg: LLMConfig,
    n: int,
) -> list[ClipCandidate]:
    if not candidates:
        return []
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic SDK not installed (pip install 'vv88[llm]'); using heuristic ranking")
        return candidates[:n]

    shortlist = candidates[: cfg.max_candidates]
    lines = []
    for idx, c in enumerate(shortlist):
        lines.append(
            f'<candidate id="{idx}" start="{c.start:.1f}" end="{c.end:.1f}" '
            f'heuristic_score="{c.score:.2f}">\n{c.text}\n</candidate>'
        )
    brief = ""
    if campaign:
        brief = (
            f"<campaign>\nbrand: {campaign.brand}\ntitle: {campaign.title}\nniche: {campaign.category}\n"
            f"requirements: {campaign.requirements or 'none stated'}\n</campaign>\n\n"
        )
    prompt = (
        f"{brief}Here are {len(shortlist)} candidate clips cut from one source video.\n\n"
        + "\n\n".join(lines)
        + f"\n\nReturn the best {n} candidates, best first, each with a virality score from 0 to 100 "
        "and the hook text, title, caption and hashtags. Skip candidates that need outside context "
        "to make sense."
    )

    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=cfg.model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": cfg.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            # Server-side fallback: if a safety classifier declines, re-run on Anthropic's
            # recommended fallback model instead of returning a refusal.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError as e:
        log.warning("Claude rate limited (%s); using heuristic ranking", e.message)
        return candidates[:n]
    except anthropic.APIStatusError as e:
        log.warning("Claude API error %s: %s; using heuristic ranking", e.status_code, e.message)
        return candidates[:n]
    except anthropic.APIConnectionError:
        log.warning("Claude unreachable; using heuristic ranking")
        return candidates[:n]

    if response.stop_reason == "refusal":
        log.warning("Claude declined the request; using heuristic ranking")
        return candidates[:n]
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        picks = json.loads(text)["clips"]
    except (json.JSONDecodeError, KeyError, TypeError):
        log.warning("unparseable Claude response (stop_reason=%s); using heuristic ranking", response.stop_reason)
        return candidates[:n]

    out: list[ClipCandidate] = []
    seen: set[int] = set()
    for p in picks:
        idx = p.get("candidate_id")
        if not isinstance(idx, int) or not 0 <= idx < len(shortlist) or idx in seen:
            continue
        seen.add(idx)
        c = shortlist[idx]
        c.hook_text = (p.get("hook_text") or c.hook_text).strip()
        c.hook_type = p.get("hook_type") if p.get("hook_type") in HOOK_TYPES else c.hook_type
        c.title = (p.get("title") or "").strip()[:100]
        if p.get("caption"):
            c.captions["default"] = p["caption"].strip()
        c.hashtags = [h if h.startswith("#") else f"#{h}" for h in p.get("hashtags", []) if h.strip()]
        c.features["llm_virality"] = float(p.get("virality", 0))
        c.reasons.append("claude")
        out.append(c)
        if len(out) >= n:
            break
    return out or candidates[:n]
