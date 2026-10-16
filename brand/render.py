#!/usr/bin/env python3
"""All Viral Videozz - "Loop 88" brand kit renderer.

Re-run from anywhere:  python3 brand/render.py   (deterministic, no network)
Writes every image plus brand.json into the folder that holds this script
(the repo's brand/ folder). Everything is drawn with code (no downloaded
assets): Pillow + numpy, Inter fonts from the system.

The mark: a single continuous figure-eight stroke (two circles joined by
crossing tangents). One diagonal passes over the other, so the 8 reads as a
replay loop / infinity. A play triangle sits in the lower counter.
The banner / header wordmark spells the display name: ALL VIRAL VIDEOZZ.
"""
import json
import math
import os
import re

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))  # every output is written here
FONT_DIR = "/usr/share/fonts/opentype/inter/"

# ---------------------------------------------------------------- palette
YELLOW = (255, 230, 0)      # #FFE600  caption active word / mark
INK = (15, 15, 15)          # #0F0F0F  background
INK_LIFT = (23, 23, 23)     # #171717  centre of background glow
GHOST = (27, 27, 27)        # #1B1B1B  ghost loop texture
WHITE = (246, 246, 242)     # #F6F6F2  off-white type
STEEL = (138, 138, 133)     # #8A8A85  secondary type


def hexc(c):
    return "#%02X%02X%02X" % c


def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), int(round(size)))


# ---------------------------------------------------------------- geometry helpers
def band(draw, pts, w, fill=255):
    """Fill a constant-width band centred on a dense open polyline."""
    n = len(pts)
    left, right = [], []
    for i in range(n):
        a = pts[max(i - 1, 0)]
        b = pts[min(i + 1, n - 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln, dx / ln
        x, y = pts[i]
        left.append((x + nx * w / 2, y + ny * w / 2))
        right.append((x - nx * w / 2, y - ny * w / 2))
    draw.polygon(left + right[::-1], fill=fill)


def arc_pts(cx, cy, r, a0, a1, steps=900):
    return [(cx + r * math.cos(a0 + (a1 - a0) * t / steps),
             cy + r * math.sin(a0 + (a1 - a0) * t / steps)) for t in range(steps + 1)]


def line_pts(p, q, steps=300):
    return [(p[0] + (q[0] - p[0]) * t / steps, p[1] + (q[1] - p[1]) * t / steps)
            for t in range(steps + 1)]


def rounded_triangle_pts(cx, cy, R, rc, angle=0.0, steps=40):
    """Equilateral triangle with tip distance R from (cx,cy), corners rounded with radius rc."""
    pts = []
    rv = R - rc  # distance of corner-arc centres from the centroid
    for k in range(3):
        th = angle + k * 2 * math.pi / 3
        ccx, ccy = cx + rv * math.cos(th), cy + rv * math.sin(th)
        for s in range(steps + 1):
            a = th - math.pi / 3 + (2 * math.pi / 3) * s / steps
            pts.append((ccx + rc * math.cos(a), ccy + rc * math.sin(a)))
    return pts


# ---------------------------------------------------------------- the mark
# Proportions in abstract units (upper loop radius = 1). Tuned by eye at 48 px.
MARK = dict(
    r1=1.00,        # upper loop centre-line radius
    r2=1.38,        # lower loop centre-line radius (bigger, like a typographic 8)
    w=0.78,         # stroke width
    phi_deg=50.0,   # angle of the crossing diagonals from vertical
    gap=0.24,       # knock-out gap either side of the over-stroke at the crossing
    tri=0.67,       # play triangle tip distance as a fraction of the lower counter radius
    tri_round=0.17, # triangle corner radius as a fraction of its tip distance
    tri_dx=0.07,    # optical nudge of the triangle to the right (fraction of tip distance)
)


def mark_layout(height, params=None):
    """Return geometry (in px, origin = bbox top-left) for a mark `height` px tall."""
    m = dict(MARK, **(params or {}))
    r1, r2, w = m["r1"], m["r2"], m["w"]
    d = (r1 + r2) / math.sin(math.radians(m["phi_deg"]))
    h_units = d + r1 + r2 + w
    w_units = 2 * max(r1, r2) + w
    k = height / h_units
    r1, r2, w, d = r1 * k, r2 * k, w * k, d * k
    W = w_units * k
    ox = W / 2
    oy = r1 + w / 2 + d * r1 / (r1 + r2)  # crossing point P (internal homothety centre)
    return dict(r1=r1, r2=r2, w=w, d=d, W=W, H=height, ox=ox, oy=oy, k=k)


def draw_mark(height, with_play=True, ss=1, params=None):
    """Render the mark as an 'L' mask (255 = ink). Returns (mask, layout).
    `params` overrides MARK entries (used for the symmetric background loop)."""
    P = dict(MARK, **(params or {}))
    L = mark_layout(height * ss, P)
    r1, r2, w, d, ox, oy = L["r1"], L["r2"], L["w"], L["d"], L["ox"], L["oy"]
    pad = int(w)  # breathing room so nothing is clipped
    size = (int(math.ceil(L["W"])) + 2 * pad, int(math.ceil(L["H"])) + 2 * pad)
    ox += pad
    oy += pad
    C1 = (ox, oy - d * r1 / (r1 + r2))
    C2 = (ox, oy + d * r2 / (r1 + r2))
    al = math.acos(r1 / (d * r1 / (r1 + r2)))
    s, c = math.sin(al), math.cos(al)
    T_ur = (C1[0] + r1 * s, C1[1] + r1 * c)
    T_ul = (C1[0] - r1 * s, C1[1] + r1 * c)
    T_ll = (C2[0] - r2 * s, C2[1] - r2 * c)
    T_lr = (C2[0] + r2 * s, C2[1] - r2 * c)
    ov = 0.004  # tiny angular overlap so pieces fuse without seams
    a_ul, a_ur = math.atan2(c, -s), math.atan2(c, s) + 2 * math.pi
    a_lr, a_ll = math.atan2(-c, s), math.atan2(-c, -s) + 2 * math.pi
    upper = arc_pts(C1[0], C1[1], r1, a_ul - ov, a_ur + ov)
    lower = arc_pts(C2[0], C2[1], r2, a_lr - ov, a_ll + ov)
    over = line_pts(T_ul, T_lr)     # "\" passes over
    under = line_pts(T_ur, T_ll)    # "/" passes under

    g = P["gap"] * L["k"]  # L["k"] already includes the supersample factor
    # under-stroke minus a widened copy of the over-stroke = clean knot gap
    und = Image.new("L", size, 0)
    band(ImageDraw.Draw(und), under, w)
    cut = Image.new("L", size, 0)
    band(ImageDraw.Draw(cut), over, w + 2 * g)
    und = ImageChops.subtract(und, cut)
    mask = Image.new("L", size, 0)
    dm = ImageDraw.Draw(mask)
    for p in (upper, lower):
        band(dm, p, w)
    mask = ImageChops.lighter(mask, und)
    dm = ImageDraw.Draw(mask)
    band(dm, over, w)
    if with_play:
        counter = r2 - w / 2
        R = counter * P["tri"]
        dm.polygon(rounded_triangle_pts(C2[0] + R * P["tri_dx"], C2[1], R,
                                        R * P["tri_round"]), fill=255)
    if ss > 1:
        mask = mask.resize((size[0] // ss, size[1] // ss), Image.LANCZOS)
    L["pad"] = pad // ss
    L["C1"], L["C2"] = C1, C2
    return mask, L


def paste_mark(img, cx, cy, height, color, with_play=True, ss=4, rotate=0, params=None):
    """Composite the mark centred (by bbox) at (cx, cy)."""
    mask, _ = draw_mark(height, with_play=with_play, ss=ss, params=params)
    if rotate == 90:
        mask = mask.transpose(Image.Transpose.ROTATE_90)
    x = int(round(cx - mask.width / 2))
    y = int(round(cy - mask.height / 2))
    img.paste(color, (x, y), mask)


# ---------------------------------------------------------------- backgrounds
def glow_bg(w, h, cx=None, cy=None, radius=None, inner=INK_LIFT, outer=INK):
    """Near-black background with a very soft radial lift."""
    cx = w / 2 if cx is None else cx
    cy = h / 2 if cy is None else cy
    radius = max(w, h) * 0.6 if radius is None else radius
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / radius
    t = np.clip(t, 0, 1) ** 1.6
    out = np.empty((h, w, 3), np.float32)
    for i in range(3):
        out[..., i] = inner[i] * (1 - t) + outer[i] * t
    # faint noise-free dithering to avoid banding in the gradient
    out += (np.indices((h, w)).sum(0) % 2)[..., None] * 0.35
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


# ---------------------------------------------------------------- type helpers
def text_w(fnt, s, tracking=0.0):
    return fnt.getlength(s) + tracking * fnt.size * (len(s) - 1)


def draw_tracked(draw, x, y, s, fnt, fill, tracking=0.0, anchor="ls"):
    """Draw text on a baseline with optional letter-spacing (fraction of font size)."""
    if tracking == 0:
        draw.text((x, y), s, font=fnt, fill=fill, anchor=anchor)
        return
    for i, ch in enumerate(s):
        adv = fnt.getlength(s[:i]) + tracking * fnt.size * i
        draw.text((x + adv, y), ch, font=fnt, fill=fill, anchor=anchor)


def cap_height(fnt):
    return -fnt.getbbox("H", anchor="ls")[1]


def karaoke(draw, x, y, words, active, fnt, gap=0.28, tracking=0.0):
    """Caption line in the house style: white words, the active word in #FFE600."""
    sp = fnt.size * gap
    for i, wd in enumerate(words):
        draw_tracked(draw, x, y, wd, fnt, YELLOW if i == active else WHITE, tracking)
        x += text_w(fnt, wd, tracking) + sp
    return x - sp


def karaoke_w(words, fnt, gap=0.28, tracking=0.0):
    return sum(text_w(fnt, wd, tracking) for wd in words) + fnt.size * gap * (len(words) - 1)


# ---------------------------------------------------------------- 1. avatar
def render_avatar():
    S = 1024
    SS = 4
    img = glow_bg(S, S, radius=S * 0.62)
    mark_h = S * 0.745
    mask, _ = draw_mark(mark_h, ss=SS)
    # optical centring: the bigger lower bowl makes the 8 bottom-heavy, so lift the
    # mark by ~45% of the gap between its ink centroid and its bbox centre.
    a = np.array(mask).astype(np.float64)
    ys, xs = np.nonzero(a)
    cy_ink = (ys * a[ys, xs]).sum() / a[ys, xs].sum()
    lift = 0.45 * (cy_ink - mask.height / 2)
    cy = S / 2 - lift
    pos = (int(round(S / 2 - mask.width / 2)), int(round(cy - mask.height / 2)))
    img.paste(YELLOW, pos, mask)
    img.save(os.path.join(HERE, "avatar_1024.png"), optimize=True)

    # safety check: every inked pixel must sit inside the central 80% circle
    ys, xs = np.nonzero(a > 127)
    xs = xs + round(S / 2 - mask.width / 2)
    ys = ys + round(cy - mask.height / 2)
    rmax = np.sqrt((xs - S / 2) ** 2 + (ys - S / 2) ** 2).max()
    print(f"avatar: optical lift {lift:.1f}px, farthest ink at {rmax / (S / 2):.3f} "
          f"of radius (limit 0.80)")
    assert rmax / (S / 2) <= 0.80 + 1e-3

    # alternates: same geometry, same placement, only the colours change
    loop_mask, _ = draw_mark(mark_h, with_play=False, ss=SS)
    play_mask = ImageChops.subtract(mask, loop_mask)  # loop and play never touch
    inverted = Image.new("RGB", (S, S), YELLOW)
    inverted.paste(INK, pos, mask)
    inverted.save(os.path.join(HERE, "avatar_alt_inverted_1024.png"), optimize=True)
    white = glow_bg(S, S, radius=S * 0.62)
    white.paste(WHITE, pos, loop_mask)
    white.paste(YELLOW, pos, play_mask)
    white.save(os.path.join(HERE, "avatar_alt_white_1024.png"), optimize=True)
    return img, inverted, white


def circle_crop(img, size, resample=Image.LANCZOS):
    """Downscale like a platform would, then apply an anti-aliased circular mask."""
    small = img.resize((size, size), resample).convert("RGBA")
    m = Image.new("L", (size * 8, size * 8), 0)
    ImageDraw.Draw(m).ellipse([0, 0, size * 8 - 1, size * 8 - 1], fill=255)
    small.putalpha(m.resize((size, size), Image.LANCZOS))
    return small


def render_previews(main, inverted, white):
    """preview_circle_400.png (main avatar) and preview_small.png: every avatar as a circle at
    32/48/96/180 px (Lanczos) plus 32/48 px with bilinear downscaling (closer to what browsers
    do), on white, #0F0F0F and pure #000000 (X lights-out)."""
    circle_crop(main, 400).save(os.path.join(HERE, "preview_circle_400.png"), optimize=True)

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
    title = font("Inter-SemiBold.otf", 20)
    small = font("Inter-Medium.otf", 12)
    rowlab = font("Inter-SemiBold.otf", 14)

    def col_x(g):
        """x of each column inside group g"""
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


# ---------------------------------------------------------------- 2. YouTube banner
WORDMARK = "ALL VIRAL VIDEOZZ"  # = the display name, uppercased (asserted in write_brand_json)
WORD_GAP = 0.26                # space between wordmark words, in em
META_PX = 39                   # niche line size in design units (was 25: ~4pt on a phone)


def wordmark_w(fnt, text, gap=WORD_GAP):
    words = text.split()
    return sum(text_w(fnt, w, -0.02) for w in words) + gap * fnt.size * (len(words) - 1)


def lockup(draw, img, x, cy, scale, ss, text=WORDMARK, meta_line=True):
    """Mark + wordmark + karaoke tagline + niche line. Returns total width.
    x = left edge, cy = vertical centre, scale = px per design unit, text = wordmark (one line)."""
    mark_h = 300 * scale
    word = font("InterDisplay-Black.otf", 148 * scale)
    tag = font("Inter-ExtraBold.otf", 50 * scale)
    meta = font("Inter-SemiBold.otf", META_PX * scale)

    _, ML = draw_mark(mark_h, ss=1)
    mark_w = ML["W"]
    paste_mark(img, x + mark_w / 2, cy, mark_h, YELLOW, ss=ss)

    tx = x + mark_w + 78 * scale
    top = cy - mark_h / 2
    # wordmark: cap top aligned with mark top, words set one by one with a fixed em gap
    base1 = top + cap_height(word)
    wx = tx
    for w in text.split():
        draw_tracked(draw, wx, base1, w, word, WHITE, tracking=-0.02)
        wx += text_w(word, w, -0.02) + WORD_GAP * word.size
    word_w = wordmark_w(word, text)

    # karaoke tagline
    base2 = base1 + 96 * scale
    words = ["MOMENTS", "WORTH", "A", "REPLAY"]
    karaoke(draw, tx, base2, words, 3, tag, tracking=0.0)

    # niche line, bottom aligned with mark bottom
    meta_w = 0
    if meta_line:
        base3 = top + mark_h
        meta_s = "PODCASTS  ·  STREAMS  ·  FINANCE  ·  ENTERTAINMENT"
        draw_tracked(draw, tx + 2 * scale, base3, meta_s, meta, STEEL, tracking=0.12)
        meta_w = 2 * scale + text_w(meta, meta_s, 0.12)
    return (tx - x) + max(word_w, karaoke_w(words, tag), meta_w)


# background texture: the same knot, but with equal loops so it reads as a true infinity
GHOST_PARAMS = dict(r1=1.0, r2=1.0, w=0.62, phi_deg=48.0, gap=0.26)


def ghost_loop(img, cx, cy, height, color, rotate=90, ss=2):
    paste_mark(img, cx, cy, height, color, with_play=False, ss=ss, rotate=rotate,
               params=GHOST_PARAMS)


def render_youtube():
    SS = 2
    W, H = 2560 * SS, 1440 * SS
    img = glow_bg(W, H, radius=W * 0.55)
    # ghost infinity across the desktop band (behind everything, very low contrast)
    ghost_loop(img, W / 2, H / 2, 1500 * SS, GHOST, rotate=90, ss=1)
    draw = ImageDraw.Draw(img)
    # measure the lockup on a scratch canvas, then centre it in the safe area.
    # 0.87 fits the one-line ALL VIRAL VIDEOZZ wordmark (1.12 overflowed the safe area).
    scale = 0.87 * SS
    scratch = Image.new("RGB", (W, H))
    lw = lockup(ImageDraw.Draw(scratch), scratch, 0, H / 2, scale, ss=2)
    x0 = (W - lw) / 2
    lockup(draw, img, x0, H / 2, scale, ss=2)
    # safe-area check (1546 x 423 centred)
    sx0, sx1 = (2560 - 1546) / 2 * SS, (2560 + 1546) / 2 * SS
    sy0, sy1 = (1440 - 423) / 2 * SS, (1440 + 423) / 2 * SS
    my0, my1 = H / 2 - 150 * scale, H / 2 + 150 * scale  # mark (= lockup) top / bottom
    print(f"youtube: lockup {lw / SS:.0f}px wide, x {x0 / SS:.0f}-{(x0 + lw) / SS:.0f} "
          f"(safe {sx0 / SS:.0f}-{sx1 / SS:.0f}), y {my0 / SS:.0f}-{my1 / SS:.0f} "
          f"(safe {sy0 / SS:.1f}-{sy1 / SS:.1f})")
    assert x0 >= sx0 and x0 + lw <= sx1, "lockup wider than the YouTube safe area"
    assert my0 >= sy0 and my1 <= sy1, "lockup taller than the YouTube safe area"
    img = img.resize((2560, 1440), Image.LANCZOS)
    img.save(os.path.join(HERE, "banner_youtube_2560x1440.png"), optimize=True)


# ---------------------------------------------------------------- 3. X header
def render_x():
    SS = 2
    W, H = 1500 * SS, 500 * SS
    CX, CY = 920 * SS, (250 - 20) * SS  # right of the profile-picture overlap, a little high
    img = glow_bg(W, H, cx=CX, cy=H / 2, radius=W * 0.5)
    ghost_loop(img, CX, H / 2, 1000 * SS, GHOST, rotate=90, ss=1)
    draw = ImageDraw.Draw(img)
    scale = 0.59 * SS
    scratch = Image.new("RGB", (W, H))
    lw = lockup(ImageDraw.Draw(scratch), scratch, 0, CY, scale, ss=2)
    x0 = CX - lw / 2
    lockup(draw, img, x0, CY, scale, ss=2)
    y0, y1 = CY - 150 * scale, CY + 150 * scale
    print(f"x header: lockup x {x0 / SS:.0f}-{(x0 + lw) / SS:.0f}, y {y0 / SS:.0f}-{y1 / SS:.0f}")
    # desktop profile picture covers about x 40-375, y 333-500; keep the 1500 px edge clear too
    assert x0 >= 420 * SS and x0 + lw <= 1420 * SS, "X header lockup hits the avatar or the edge"
    assert y0 >= 70 * SS and y1 <= 330 * SS, "X header lockup outside the y 70-330 band"
    img = img.resize((1500, 500), Image.LANCZOS)
    img.save(os.path.join(HERE, "header_x_1500x500.png"), optimize=True)


# ---------------------------------------------------------------- 4. copy + brand.json
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
    assert WORDMARK == NAME.upper(), "banner / header wordmark must spell the display name"
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
        "concept_name": "Loop 88",
        "rationale": (
            "The mark is a figure-eight replay loop with a play button: one continuous stroke "
            "that crosses itself, so it reads as both a bold 8 and a replay / infinity loop, the "
            "thing a good clip makes people do. One diagonal passes over the other with a cut "
            "gap, which gives the loop its depth at large sizes and simply resolves to a bold 8 "
            "at 32-48 px. A play triangle sits in the larger lower counter to say 'video' without "
            "any text. Signal yellow #FFE600 on near-black #0F0F0F is the same colour pair as the "
            "karaoke captions on every clip, so the avatar, the banners and the videos look like "
            "one product. The banner and header wordmark spell the display name, ALL VIRAL "
            "VIDEOZZ, so profile name, handle and artwork match on every platform. No creator "
            "names and no 'official' language: the copy says what the page posts, that it is "
            "independent, and how a featured creator can ask for a correction or removal."
        ),
        "palette": [hexc(YELLOW), hexc(INK), hexc(WHITE), hexc(STEEL), hexc(INK_LIFT)],
        "palette_roles": {
            hexc(YELLOW): "Signal Yellow - mark, active caption word, highlights",
            hexc(INK): "Ink - backgrounds",
            hexc(WHITE): "Paper - primary type on dark",
            hexc(STEEL): "Steel - secondary type, meta lines",
            hexc(INK_LIFT): "Lift - soft background glow, panels",
        },
        "fonts": {
            "wordmark": "Inter Display Black (tracking -2%)",
            "captions_and_taglines": "Inter ExtraBold, uppercase",
            "meta": "Inter SemiBold, uppercase, tracking +12%",
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


if __name__ == "__main__":
    av = render_avatar()
    render_previews(*av)
    render_youtube()
    render_x()
    write_brand_json()
    print("done")
