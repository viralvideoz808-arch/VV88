"""Brand kit: one profile picture + coordinated names and bios across TikTok, Instagram, YouTube and X.

Nothing is changed on any account until the owner approves the exact kit. Approval is tied to a
fingerprint of the images and text, so editing the kit after approving it requires approving again.

What can be applied automatically is limited by the platforms themselves:
* YouTube – channel description + keywords via channels.update (needs the `youtube` OAuth scope).
  Channel name, profile picture and banner have to be set in YouTube Studio.
* TikTok, Instagram, X – their APIs don't offer profile editing to apps like this, so `vv88 brand
  apply` writes a click-by-click checklist with the exact text and image files to use.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from vv88.publishing.base import PublishError, check
from vv88.store import Store

BIO_LIMITS = {"tiktok": 80, "instagram": 150, "x": 160, "youtube_description": 1000}
NAME_LIMITS = {"tiktok": 30, "instagram": 30, "x": 50, "youtube": 100}
HANDLE_MAX = 15  # X is the tightest
YOUTUBE_KEYWORDS_MAX = 500
YOUTUBE_WRITABLE = ("country", "description", "defaultLanguage", "keywords", "trackingAnalyticsAccountId",
                    "unsubscribedTrailer")

EDIT_PAGES = {
    "tiktok": "TikTok app or tiktok.com > Profile > Edit profile",
    "instagram": "instagram.com/accounts/edit (or app > Profile > Edit profile)",
    "x": "x.com/settings/profile (or Profile > Edit profile)",
    "youtube": "studio.youtube.com > Customization > Profile / Branding / Basic info",
}


@dataclass
class BrandKit:
    root: Path
    data: dict
    problems: list[str] = field(default_factory=list)

    @property
    def avatar(self) -> Path:
        return self.root / self.data.get("avatar", "avatar_1024.png")

    @property
    def youtube_banner(self) -> Path:
        return self.root / self.data.get("youtube_banner", "banner_youtube_2560x1440.png")

    @property
    def x_header(self) -> Path:
        return self.root / self.data.get("x_header", "header_x_1500x500.png")

    def fingerprint(self) -> str:
        h = hashlib.sha256(json.dumps(self.data, sort_keys=True, ensure_ascii=False).encode())
        for p in (self.avatar, self.youtube_banner, self.x_header):
            if p.exists():
                h.update(p.read_bytes())
        return h.hexdigest()[:20]


def load_kit(root: str | Path) -> BrandKit:
    root = Path(root)
    path = root / "brand.json"
    if not path.is_file():
        raise FileNotFoundError(f"no brand.json in {root}")
    kit = BrandKit(root=root, data=json.loads(path.read_text()))
    kit.problems = validate(kit)
    return kit


def validate(kit: BrandKit) -> list[str]:
    d = kit.data
    problems = []
    bios = d.get("bios", {})
    for k, limit in BIO_LIMITS.items():
        text = bios.get(k, "")
        if not text:
            problems.append(f"missing bio for {k}")
        elif len(text) > limit:
            problems.append(f"{k} bio is {len(text)} chars (limit {limit})")
    names = d.get("display_names", {})
    for k, limit in NAME_LIMITS.items():
        if len(names.get(k, "")) > limit:
            problems.append(f"{k} display name is {len(names[k])} chars (limit {limit})")
    handle = d.get("unified_handle", "")
    if handle and (len(handle) > HANDLE_MAX or not all(c.isalnum() or c in "._" for c in handle)):
        problems.append(f"handle {handle!r} must be <= {HANDLE_MAX} chars of letters, digits, '.' or '_'")
    kw = " ".join(_quote_kw(k) for k in d.get("youtube_keywords", []))
    if len(kw) > YOUTUBE_KEYWORDS_MAX:
        problems.append(f"youtube keywords are {len(kw)} chars (limit {YOUTUBE_KEYWORDS_MAX})")
    if not kit.avatar.exists():
        problems.append(f"avatar image missing: {kit.avatar}")
    return problems


def _quote_kw(k: str) -> str:
    k = k.strip().replace('"', "")
    return f'"{k}"' if " " in k else k


# ---- approval gate ----------------------------------------------------------------------
def approve(kit: BrandKit, store: Store, note: str = "") -> dict:
    if kit.problems:
        raise ValueError("fix these before approving: " + "; ".join(kit.problems))
    record = {"fingerprint": kit.fingerprint(), "root": str(kit.root), "approved_at": time.time(), "note": note}
    store.kv_set("brand:approved", record)
    return record


def approval(kit: BrandKit, store: Store) -> dict | None:
    rec = store.kv_get("brand:approved")
    if rec and rec.get("fingerprint") == kit.fingerprint():
        return rec
    return None


# ---- applying ---------------------------------------------------------------------------
def youtube_apply(kit: BrandKit, access_token: str, http: requests.Session | None = None) -> dict:
    """Update the channel description + keywords, keeping every other branding setting intact.

    channels.update replaces the whole brandingSettings part and deletes omitted properties, so the
    current settings are read first and only description/keywords are changed.
    """
    http = http or requests.Session()
    auth = {"Authorization": f"Bearer {access_token}"}
    current = check(
        http.get("https://www.googleapis.com/youtube/v3/channels",
                 params={"part": "brandingSettings", "mine": "true"}, headers=auth, timeout=30),
        "youtube channels.list",
    )
    items = current.get("items") or []
    if not items:
        raise PublishError("no YouTube channel found for this account")
    channel = items[0]
    existing = channel.get("brandingSettings", {}).get("channel", {})
    # Only these are writable (YouTube Data API channels.update); carry the current values forward.
    ch = {k: existing[k] for k in YOUTUBE_WRITABLE if k in existing}
    ch["description"] = kit.data["bios"]["youtube_description"]
    if kit.data.get("youtube_keywords"):
        ch["keywords"] = " ".join(_quote_kw(k) for k in kit.data["youtube_keywords"])
    body = {"id": channel["id"], "brandingSettings": {"channel": ch}}
    updated = check(
        http.put("https://www.googleapis.com/youtube/v3/channels", params={"part": "brandingSettings"},
                 headers={**auth, "Content-Type": "application/json"}, json=body, timeout=30),
        "youtube channels.update",
    )
    return {"channel_id": channel["id"], "description_chars": len(ch["description"]),
            "keywords": ch.get("keywords", ""), "ok": bool(updated.get("id"))}


def checklist(kit: BrandKit) -> str:
    d = kit.data
    names = d.get("display_names", {})
    bios = d.get("bios", {})
    handle = d.get("unified_handle", "")
    lines = [
        f"# Profile update checklist — {d.get('concept_name', 'brand kit')}",
        "",
        f"Profile picture (same on every platform): `{kit.avatar}`",
        f"Unified handle to try: `@{handle}`"
        + (f" (fallbacks: {', '.join('@' + h for h in d.get('handle_fallbacks', []))})" if handle else ""),
        "Handles can't be checked from here; claim the same one everywhere if it's free. Changing a handle "
        "breaks old links to your profile, and TikTok/Instagram limit how often you can change it.",
        "",
    ]
    sections = [
        ("tiktok", "TikTok", [("Name", names.get("tiktok", "")), ("Bio", bios.get("tiktok", ""))], []),
        ("instagram", "Instagram", [("Name", names.get("instagram", "")), ("Bio", bios.get("instagram", ""))], []),
        ("x", "X", [("Name", names.get("x", "")), ("Bio", bios.get("x", ""))],
         [f"Header image: `{kit.x_header}`"] if kit.x_header.exists() else []),
        ("youtube", "YouTube", [("Channel name", names.get("youtube", ""))],
         ([f"Banner image: `{kit.youtube_banner}`"] if kit.youtube_banner.exists() else [])
         + ["Description + keywords: applied automatically by `vv88 brand apply` when YouTube is connected; "
            "otherwise paste the description below."]),
    ]
    for key, title, fields, extra in sections:
        lines.append(f"## {title}  ({EDIT_PAGES[key]})")
        lines.append("- [ ] Upload the profile picture")
        for label, value in fields:
            lines.append(f"- [ ] {label} ({len(value)} chars):")
            lines.append("")
            lines.append("  ```")
            lines.append(f"  {value}")
            lines.append("  ```")
        for e in extra:
            lines.append(f"- [ ] {e}")
        if d.get("link_in_bio_suggestion") and key != "youtube":
            lines.append(f"- [ ] Link: {d['link_in_bio_suggestion']}")
        lines.append("")
    lines.append("## YouTube description")
    lines.append("```")
    lines.append(bios.get("youtube_description", ""))
    lines.append("```")
    return "\n".join(lines) + "\n"


def apply(kit: BrandKit, store: Store, youtube_access_token: str | None = None,
          http: requests.Session | None = None) -> dict:
    if not approval(kit, store):
        raise PermissionError("this brand kit hasn't been approved (or changed since approval); "
                              "review it and run `vv88 brand approve` first")
    result: dict = {}
    if youtube_access_token:
        result["youtube"] = youtube_apply(kit, youtube_access_token, http)
    path = kit.root / "APPLY_CHECKLIST.md"
    path.write_text(checklist(kit))
    result["checklist"] = str(path)
    store.kv_set("brand:applied", {"fingerprint": kit.fingerprint(), "at": time.time(), "result": result})
    return result
