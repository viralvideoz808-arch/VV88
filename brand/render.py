#!/usr/bin/env python3
"""All Viral Videozz - "VV Network" brand kit renderer (the main kit).

Re-run from anywhere:  python3 brand/render.py   (deterministic, no network)
Writes every image, brand.json and APPLY_CHECKLIST.md into the folder that holds
this script (the repo's brand/ folder). Everything is drawn with code (no
downloaded assets): Pillow + numpy, Inter fonts from the system.

The mark and the banner lockup are drawn by brand/options/render_options.py
(option 1, "VV Network"), so the main kit and the option sheet can never drift
apart. The previous main kit, "Loop 88", is archived in brand/options/loop_88/.
"""
import json
import os
import re
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))  # every output is written here
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "options"))
import render_options as ro  # noqa: E402

YELLOW, INK, WHITE, STEEL, LIFT = ro.YELLOW, ro.INK, ro.WHITE, ro.STEEL, ro.AV_LIFT


def hexc(c):
    return "#%02X%02X%02X" % c


# ---------------------------------------------------------------- 1. avatar + alternates
def render_avatar():
    main = ro.avatar("vv_network")
    main.save(os.path.join(HERE, "avatar_1024.png"), optimize=True)
    # alternates: same geometry and placement, only the colours change
    inverted = ro.avatar("vv_network", bg=YELLOW, colors=(INK, INK))
    inverted.save(os.path.join(HERE, "avatar_alt_inverted_1024.png"), optimize=True)
    white = ro.avatar("vv_network", bg=WHITE, colors=(INK, INK))
    white.save(os.path.join(HERE, "avatar_alt_white_1024.png"), optimize=True)
    return main, inverted, white


def render_previews(main, inverted, white):
    """preview_circle_400.png (main avatar) and preview_small.png: every avatar as a circle at
    32/48/96/180 px (Lanczos) plus 32/48 px with bilinear downscaling (closer to what browsers
    do), on white, #0F0F0F and pure #000000 (X lights-out)."""
    ro.circle(main, 400).save(os.path.join(HERE, "preview_circle_400.png"), optimize=True)

    def circle_crop(img, size, resample):
        small = img.resize((size, size), resample).convert("RGBA")
        m = Image.new("L", (size * 8, size * 8), 0)
        ImageDraw.Draw(m).ellipse([0, 0, size * 8 - 1, size * 8 - 1], fill=255)
        small.putalpha(m.resize((size, size), Image.LANCZOS))
        return small

    variants = (("main", main), ("inverted", inverted), ("white", white))
    cols = [(32, Image.LANCZOS), (48, Image.LANCZOS), (96, Image.LANCZOS), (180, Image.LANCZOS),
            (32, Image.BILINEAR), (48, Image.BILINEAR)]
    rows = (("white  #FFFFFF", (255, 255, 255), (110, 110, 110)),
            ("#0F0F0F", INK, (150, 150, 150)),
            ("#000000  X lights-out", (0, 0, 0), (150, 150, 150)))
    pad, gap, split, group_gap, label_w = 28, 22, 44, 64, 190
    head_h, row_h = 74, 180 + 2 * 24 + 26
    group_w = sum(sz for sz, _ in cols) + gap * (len(cols) - 2) + split
    W = pad + label_w + len(variants) * group_w + (len(variants) - 1) * group_gap + pad
    H = head_h + row_h * len(rows)
    sheet = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    title = ro.font("Inter-SemiBold.otf", 20)
    small = ro.font("Inter-Medium.otf", 12)
    rowlab = ro.font("Inter-SemiBold.otf", 14)

    def col_x(g):
        x = pad + label_w + g * (group_w + group_gap)
        out = []
        for i, (sz, _) in enumerate(cols):
            out.append(x)
            x += sz + (split if i == 3 else gap)
        return out

    for g, (name, _) in enumerate(variants):
        xs = col_x(g)
        d.text((xs[0], 22), name, font=title, fill=(20, 20, 20), anchor="ls")
        d.text((xs[0], 52), "LANCZOS", font=small, fill=(120, 120, 120), anchor="ls")
        d.text((xs[4], 52), "BILINEAR", font=small, fill=(120, 120, 120), anchor="ls")
    for r, (label, bg, fg) in enumerate(rows):
        y0 = head_h + r * row_h
        d.rectangle([0, y0, W, y0 + row_h - 1], fill=bg)
        d.text((pad, y0 + 24 + 90), label, font=rowlab, fill=fg, anchor="lm")
        for g, (_, av) in enumerate(variants):
            for (sz, rs), x in zip(cols, col_x(g)):
                c = circle_crop(av, sz, rs)
                y = y0 + 24 + (180 - sz) // 2
                sheet.paste(c, (x, y), c)
                d.text((x + sz / 2, y0 + 24 + 180 + 14), f"{sz}", font=small, fill=fg, anchor="mm")
    sheet.save(os.path.join(HERE, "preview_small.png"), optimize=True)


# ---------------------------------------------------------------- 2. banner + header
def render_banners():
    ro.youtube_banner("vv_network").save(os.path.join(HERE, "banner_youtube_2560x1440.png"), optimize=True)
    ro.x_header("vv_network").save(os.path.join(HERE, "header_x_1500x500.png"), optimize=True)


# ---------------------------------------------------------------- 3. copy + brand.json
# Fixed identity (owner's decision): @allviralvideozz is already claimed on all four platforms.
HANDLE = "allviralvideozz"
FALLBACKS = []
NAME = "All Viral Videozz"
DISPLAY = {
    "tiktok": NAME,
    "instagram": NAME,
    "youtube": NAME,
    "x": NAME,
}
BIOS = {
    "tiktok": "Moments worth a replay \U0001F501 Captioned podcast, stream & finance clips, new daily",
    "instagram": ("Moments worth a replay \U0001F501\n"
                  "Podcasts · streams · finance · entertainment\n"
                  "Captioned clips daily. Independent page, clipped with permission"),
    "x": ("Moments worth a replay \U0001F501 Captioned clips from podcasts, streams, finance and "
          "entertainment, posted daily. Independent page, clipped with permission."),
    "youtube_description": (
        "All Viral Videozz: moments worth a replay.\n\n"
        "Short, captioned vertical clips from podcasts, livestreams, finance talks and "
        "entertainment, posted daily. Each clip is trimmed to the moment and carries bold "
        "word-by-word captions, so you can follow along with the sound off.\n\n"
        "What you'll find here:\n"
        "- Podcast highlights and big ideas in under a minute\n"
        "- Stream moments worth watching twice\n"
        "- Money and finance talk in plain language\n"
        "- Entertainment moments from shows and creators that allow clipping\n\n"
        "All Viral Videozz is an independent clip channel and is not run by the creators it "
        "features. Clips are made from footage we have permission to use, mostly through "
        "creators' clipping campaigns; paid campaign clips are marked #ad.\n\n"
        "Featured creator? For a correction or a removal, use the contact link on this channel "
        "and we will review it.\n\n"
        "Also on TikTok, Instagram and X as @allviralvideozz."
    ),
}
YOUTUBE_KEYWORDS = [
    "All Viral Videozz", "allviralvideozz", "podcast clips", "podcast highlights",
    "stream highlights", "livestream clips", "finance clips", "finance shorts",
    "entertainment clips", "captioned clips", "viral clips", "best moments", "shorts clips",
]
LIMITS = {
    "bio_tiktok": 80, "bio_instagram": 150, "bio_x": 160, "bio_youtube_description": 1000,
    "display_tiktok": 30, "display_instagram": 30, "display_x": 50, "display_youtube": 100,
    "youtube_keywords": 500,
}
EMOJI_RANGES = ((0x1F300, 0x1FAFF), (0x2600, 0x27BF))
LOGIN_EMAIL = "viralvideoz808@gmail.com"  # private: never in public copy
OLD_NAMES = ("vv88 clips", "vv88")        # the engine's name, not the brand
BAIT = ("follow for more", "like and share", "like & share", "subscribe for more", "tag a friend",
        "comment below", "turn on notifications", "hit subscribe", "smash that", "follow us")


def count_emoji(s):
    return sum(1 for ch in s if any(lo <= ord(ch) <= hi for lo, hi in EMOJI_RANGES))


def keywords_field(kws):
    """The string YouTube stores (same quoting as vv88.brand)."""
    return " ".join(f'"{k}"' if " " in k else k for k in kws)


def write_brand_json():
    # identity
    assert re.fullmatch(r"[A-Za-z0-9_]{1,15}", HANDLE), HANDLE  # X is the strictest platform
    for f in FALLBACKS:
        assert re.fullmatch(r"[A-Za-z0-9_]{1,15}", f), f
    assert len(set(DISPLAY.values())) == 1, "display names must match on every platform"
    assert ro.WORDMARK == HANDLE, "banner / header wordmark must spell the handle"
    assert NAME.replace(" ", "").lower() == HANDLE, "display name and handle must match"

    counts = {}
    public = [*DISPLAY.values(), *BIOS.values(), *YOUTUBE_KEYWORDS]
    for k, v in BIOS.items():
        key = "bio_" + k
        counts[key] = len(v)
        assert len(v) <= LIMITS[key], (key, len(v))
        assert count_emoji(v) <= 2, key
        assert "official" not in v.lower(), key
        assert not any(b in v.lower() for b in BAIT), key
        # the handle is only needed where a platform has to cross-link (YouTube)
        assert ("@" + HANDLE in v) == (k == "youtube_description"), key
    for k, v in DISPLAY.items():
        key = "display_" + k
        counts[key] = len(v)
        assert len(v) <= LIMITS[key], (key, len(v))
    for v in public:
        assert LOGIN_EMAIL not in v and "@gmail" not in v.lower(), v
        assert not any(o in v.lower() for o in OLD_NAMES), v
    assert 5 <= len(YOUTUBE_KEYWORDS) <= 15, len(YOUTUBE_KEYWORDS)
    counts["youtube_keywords"] = len(keywords_field(YOUTUBE_KEYWORDS))
    assert counts["youtube_keywords"] <= LIMITS["youtube_keywords"]

    brand = {
        "concept_name": "VV Network",
        "rationale": (
            "The mark is a double V for Viral Videozz: two heavy, forward-leaning Vs, the first in "
            "off-white and the second in signal yellow, so it reads as one bold monogram at 32-48 px "
            "and as a two-beat 'viral, videozz' at full size. The banner and header wordmark spell "
            "the handle, allviralvideozz, split the same way (allviral in white, videozz in yellow), "
            "over the niche line (podcasts, streams, finance, entertainment) with a yellow progress "
            "bar and 'new clips every day'. Signal yellow #FFE600 on near-black is the same colour "
            "pair as the karaoke captions on every clip, so the avatar, the banners and the videos "
            "look like one product. No creator names and no 'official' language: the copy says what "
            "the page posts, that it is independent, and how a featured creator can ask for a "
            "correction or removal."
        ),
        "palette": [hexc(YELLOW), hexc(INK), hexc(WHITE), hexc(STEEL), hexc(LIFT)],
        "palette_roles": {
            hexc(YELLOW): "Signal Yellow - second V, 'videozz', highlights, progress bar",
            hexc(INK): "Ink - backgrounds",
            hexc(WHITE): "Paper - first V, 'allviral', primary type on dark",
            hexc(STEEL): "Steel - secondary type, meta lines",
            hexc(LIFT): "Lift - soft background glow behind the avatar",
        },
        "fonts": {
            "wordmark": "Inter Black (tracking -1%), lowercase",
            "niche_line": "Inter Bold, uppercase",
            "meta": "Inter SemiBold, uppercase, tracking +20%",
            "body": "Inter Regular / Medium",
        },
        "unified_handle": HANDLE,
        "handle_fallbacks": FALLBACKS,
        "handle_note": (
            "Claimed by the owner on TikTok, Instagram, YouTube and X. 15/15 characters, valid "
            "under X's [A-Za-z0-9_]{1,15}."
        ),
        "display_names": DISPLAY,
        "bios": BIOS,
        "youtube_keywords": YOUTUBE_KEYWORDS,
        "link_in_bio_suggestion": (
            "One simple link hub (a free link-in-bio page, or a GitHub Pages site under the "
            "viralvideoz808-arch org) that lists the four All Viral Videozz profiles "
            "(@allviralvideozz on TikTok, Instagram, YouTube and X) plus a contact / takedown "
            "form. On YouTube, add that contact / takedown form as a channel link: it is the "
            "'contact link' the channel description points featured creators to. Keep the login "
            "email viralvideoz808@gmail.com private: leave YouTube's business-email field empty "
            "or use a separate contact alias."
        ),
        "avatar": "avatar_1024.png",
        "youtube_banner": "banner_youtube_2560x1440.png",
        "x_header": "header_x_1500x500.png",
        "alternates": ["avatar_alt_inverted_1024.png", "avatar_alt_white_1024.png"],
        "previews": ["preview_small.png", "preview_circle_400.png"],
        "char_counts": counts,
        "char_limits": LIMITS,
    }
    blob = json.dumps(brand, ensure_ascii=False)
    assert "vv88" not in blob.lower(), "old brand name left in brand.json"
    for name in ("avatar", "youtube_banner", "x_header"):
        assert os.path.isfile(os.path.join(HERE, brand[name])), brand[name]
    for name in brand["alternates"] + brand["previews"]:
        assert os.path.isfile(os.path.join(HERE, name)), name
    with open(os.path.join(HERE, "brand.json"), "w", encoding="utf-8") as fh:
        json.dump(brand, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    for k, v in counts.items():
        print(f"  {k}: {v}/{LIMITS[k]}")


def write_checklist():
    """APPLY_CHECKLIST.md, written by the app's own checklist() so it matches `vv88 brand apply`."""
    sys.path.insert(0, REPO)
    try:
        from vv88 import brand as b
    except ImportError as e:  # app dependencies not installed: the images and brand.json are still fine
        print(f"checklist skipped ({e})")
        return
    cwd = os.getcwd()
    os.chdir(REPO)  # so the checklist shows repo-relative paths like brand/avatar_1024.png
    try:
        kit = b.load_kit("brand")
        assert not kit.problems, kit.problems
        with open(os.path.join(HERE, "APPLY_CHECKLIST.md"), "w", encoding="utf-8") as fh:
            fh.write(b.checklist(kit))
        print(f"checklist written, kit fingerprint {kit.fingerprint()}")
    finally:
        os.chdir(cwd)


if __name__ == "__main__":
    av = render_avatar()
    render_previews(*av)
    render_banners()
    write_brand_json()
    write_checklist()
    print("done")
