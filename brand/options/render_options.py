#!/usr/bin/env python3
"""All Viral Videozz - logo options #1 "VV Network" and #4 "Pulse", renamed.

These are the two "viralvideoz" options from the earlier option sheet, rebuilt
with code at full resolution and with every "viralvideoz" changed to
"allviralvideozz" (wordmark and handle). The design is kept as it was: same
marks, colours, type and layout.

Re-run from anywhere:  python3 brand/options/render_options.py
(deterministic, no network; Pillow + numpy + the system Inter fonts)

Writes, next to this script:
  vv_network/avatar_1024.png              profile picture (square, circle-safe)
  vv_network/banner_youtube_2560x1440.png YouTube banner, lockup in the safe area
  vv_network/header_x_1500x500.png        X header, lockup clear of the avatar
  vv_network/page_banner_1932x410.png     the wide "page" banner, as on the sheet
  pulse/...                               the same four files for option 4
  preview_sheet.png                       both options side by side
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = "/usr/share/fonts/opentype/inter/"

HANDLE = "allviralvideozz"
WORD_WHITE, WORD_YELLOW = "allviral", "videozz"   # VV Network splits the wordmark in two colours
WORDMARK = WORD_WHITE + WORD_YELLOW
assert WORDMARK == HANDLE

# ---------------------------------------------------------------- palette
YELLOW = (255, 230, 0)          # #FFE600
WHITE = (246, 246, 242)         # #F6F6F2
STEEL = (138, 138, 133)         # #8A8A85
# VV Network
INK = (15, 15, 15)              # #0F0F0F
INK_LIFT = (30, 30, 30)         # #1E1E1E
AV_INK, AV_LIFT = (28, 28, 28), (42, 42, 41)
BAR_TRACK = (46, 46, 46)        # #2E2E2E
# Pulse
NIGHT = (19, 12, 43)            # #130C2B
VIOLET = (43, 22, 96)           # #2B1660
AV_NIGHT, AV_VIOLET = (27, 17, 62), (50, 29, 92)
LAVENDER = (182, 167, 226)      # #B6A7E2  tagline
LAVENDER_META = (194, 176, 236) # #C2B0EC  niche line
WAVE = (58, 45, 128)            # #3A2D80  side pulse lines


def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), max(1, int(round(size))))


def text_w(fnt, s, tracking=0.0):
    return fnt.getlength(s) + tracking * fnt.size * (len(s) - 1)


def draw_tracked(draw, x, y, s, fnt, fill, tracking=0.0):
    """Text on a baseline (anchor 'ls') with letter-spacing as a fraction of the size."""
    if not tracking:
        draw.text((x, y), s, font=fnt, fill=fill, anchor="ls")
        return
    for i, ch in enumerate(s):
        draw.text((x + fnt.getlength(s[:i]) + tracking * fnt.size * i, y), ch,
                  font=fnt, fill=fill, anchor="ls")


# ---------------------------------------------------------------- backgrounds
def radial_bg(w, h, inner, outer, cx=None, cy=None, radius=None, power=1.5):
    cx = w / 2 if cx is None else cx
    cy = h / 2 if cy is None else cy
    radius = max(w, h) * 0.6 if radius is None else radius
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = np.clip(np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / radius, 0, 1) ** power
    out = np.empty((h, w, 3), np.float32)
    for i in range(3):
        out[..., i] = inner[i] * (1 - t) + outer[i] * t
    out += (np.indices((h, w)).sum(0) % 2)[..., None] * 0.35  # anti-banding dither
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def diagonal_sheen(img, x_top, x_bottom, strength=5):
    """The faint diagonal panel on the right of the VV Network banner."""
    w, h = img.size
    a = np.asarray(img).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    edge = x_top + (x_bottom - x_top) * yy / max(h - 1, 1)
    feather = 0.004 * w
    t = np.clip((xx - edge) / feather, 0, 1)
    a += t[..., None] * strength
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


# ---------------------------------------------------------------- the marks
# Both marks are drawn in the frame of the original option sheet: a 890-unit
# square whose circle has its centre at (445, 445) and radius 420.
FRAME_C, FRAME_R = 445.0, 420.0

V_WHITE = [(152, 275), (265, 275), (272, 405), (360, 275), (432, 275), (300, 625), (232, 625)]
V_YELLOW = [(462, 275), (572, 275), (578, 405), (655, 275), (738, 275), (578, 625), (520, 625)]
VV_BOX = (152, 275, 742, 625)

PULSE_TRI = [(180, 378), (440, 506), (180, 636)]
PULSE_LINE = [(400, 483), (493, 483), (575, 138), (632, 483), (712, 483),
              (712, 530), (655, 530), (608, 670), (565, 410), (540, 530), (400, 530)]
PULSE_CAP = (712, 506.5, 23.5)  # round end of the baseline
PULSE_BOX = (180, 138, 736, 670)


def _poly(draw, pts, k, ox, oy, fill=255):
    draw.polygon([(ox + x * k, oy + y * k) for x, y in pts], fill=fill)


def _round(mask, r):
    """Round the corners of a mask: blur, then threshold back to a hard edge."""
    if r < 0.5:
        return mask
    return mask.filter(ImageFilter.GaussianBlur(r)).point(lambda v: 255 if v >= 128 else 0)


def vv_masks(height, ss=4):
    """(white_mask, yellow_mask) for the VV mark, `height` px tall (bbox)."""
    x0, y0, x1, y1 = VV_BOX
    k = height * ss / (y1 - y0)
    pad = int(8 * ss)
    size = (int(math.ceil((x1 - x0) * k)) + 2 * pad, int(math.ceil((y1 - y0) * k)) + 2 * pad)
    out = []
    for pts in (V_WHITE, V_YELLOW):
        m = Image.new("L", size, 0)
        _poly(ImageDraw.Draw(m), pts, k, pad - x0 * k, pad - y0 * k)
        m = _round(m, 2.5 * k)  # the sheet's corners are just softened, not round
        out.append(m.resize((size[0] // ss, size[1] // ss), Image.LANCZOS))
    return out


def pulse_mask(height, ss=4):
    """Mask of the Pulse mark (play triangle running into a heartbeat line)."""
    x0, y0, x1, y1 = PULSE_BOX
    k = height * ss / (y1 - y0)
    pad = int(8 * ss)
    size = (int(math.ceil((x1 - x0) * k)) + 2 * pad, int(math.ceil((y1 - y0) * k)) + 2 * pad)
    ox, oy = pad - x0 * k, pad - y0 * k
    tri = Image.new("L", size, 0)
    _poly(ImageDraw.Draw(tri), PULSE_TRI, k, ox, oy)
    tri = _round(tri, 16 * k)
    line = Image.new("L", size, 0)
    d = ImageDraw.Draw(line)
    _poly(d, PULSE_LINE, k, ox, oy)
    cx, cy, r = PULSE_CAP
    d.ellipse([ox + (cx - r) * k, oy + (cy - r) * k, ox + (cx + r) * k, oy + (cy + r) * k], fill=255)
    line = _round(line, 3 * k)
    m = Image.fromarray(np.maximum(np.asarray(tri), np.asarray(line)))
    return m.resize((size[0] // ss, size[1] // ss), Image.LANCZOS)


def mark_width(box, height):
    x0, y0, x1, y1 = box
    return (x1 - x0) * height / (y1 - y0)


def paste_box(img, mask, color, left, top, height, box):
    """Paste a mask so that its design bbox has its top-left at (left, top)."""
    x0, y0, x1, y1 = box
    pad = (mask.height - height) / 2  # masks carry equal padding on every side
    img.paste(color, (int(round(left - pad)), int(round(top - pad))), mask)


# ---------------------------------------------------------------- 1. avatars
def avatar(kind, S=1024, bg=None, colors=None):
    """Square profile picture; the mark sits where it sat in the sheet's circle.
    VV Network only: `bg` (an RGB colour) and `colors` ((left V, right V)) recolour it,
    which the main kit uses for its alternates."""
    k = (S / 2) / FRAME_R
    if kind == "vv_network":
        img = radial_bg(S, S, AV_LIFT, AV_INK, radius=S * 0.62) if bg is None else Image.new("RGB", (S, S), bg)
        box = VV_BOX
        wm, ym = vv_masks((box[3] - box[1]) * k)
        c_left, c_right = colors or (WHITE, YELLOW)
        masks = ((wm, c_left), (ym, c_right))
    else:
        img = radial_bg(S, S, AV_VIOLET, AV_NIGHT, cy=S * 0.45, radius=S * 0.62)
        box = PULSE_BOX
        masks = ((pulse_mask((box[3] - box[1]) * k), YELLOW),)
    left = S / 2 + (box[0] - FRAME_C) * k
    top = S / 2 + (box[1] - FRAME_C) * k
    ink = np.zeros((S, S), np.float32)
    for m, color in masks:
        paste_box(img, m, color, left, top, (box[3] - box[1]) * k, box)
        layer = Image.new("L", (S, S), 0)
        layer.paste(m, (int(round(left - (m.height - (box[3] - box[1]) * k) / 2)),
                        int(round(top - (m.height - (box[3] - box[1]) * k) / 2))))
        ink = np.maximum(ink, np.asarray(layer, np.float32))
    ys, xs = np.nonzero(ink > 127)
    rmax = np.sqrt((xs - S / 2) ** 2 + (ys - S / 2) ** 2).max() / (S / 2)
    print(f"{kind} avatar: farthest ink at {rmax:.3f} of the circle radius (limit 0.83)")
    assert rmax <= 0.83, "mark would be clipped by a circular crop"
    return img


# ---------------------------------------------------------------- 2. lockups
# Measured on the sheet's banners, in units of a 1932 x 410 band
# (the whole band = what the sheet shows; y is from the band's top).
BAND_W, BAND_H = 1932, 410


class Pen:
    """Maps band units to pixels: x is free, y = cy + (v - 205) * s."""

    def __init__(self, img, cx, cy, s):
        self.img, self.cx, self.cy, self.s = img, cx, cy, s
        self.draw = ImageDraw.Draw(img)

    def y(self, v):
        return self.cy + (v - BAND_H / 2) * self.s


def vv_network_lockup(pen, measure_only=False):
    """VV mark + 'allviral|videozz' + niche line + progress bar + 'NEW CLIPS EVERY DAY'.
    Returns (left, right, top, bottom) of the content in px."""
    s = pen.s
    mark_h = 125 * s
    mark_w = mark_width(VV_BOX, mark_h)
    word = font("Inter-Black.otf", 110 * s)
    ww_white = text_w(word, WORD_WHITE, -0.01)
    ww = ww_white + text_w(word, WORD_YELLOW, -0.01) + (-0.01 * word.size)
    gap = 30 * s
    row1 = mark_w + gap + ww
    niche = font("Inter-Bold.otf", 36 * s)
    words = ["PODCASTS", "STREAMS", "FINANCE", "ENTERTAINMENT"]
    sp = niche.getlength(" ")
    row2 = sum(niche.getlength(w) for w in words) + sp * (len(words) - 1)
    meta = font("Inter-SemiBold.otf", 27 * s)
    meta_s = "NEW CLIPS EVERY DAY"
    row3 = text_w(meta, meta_s, 0.2)
    left = pen.cx - max(row1, row2) / 2
    right = pen.cx + max(row1, row2) / 2
    box = (left, right, pen.y(70), pen.y(337))
    if measure_only:
        return box

    x = pen.cx - row1 / 2
    wm, ym = vv_masks(mark_h)
    paste_box(pen.img, wm, WHITE, x, pen.y(70), mark_h, VV_BOX)
    paste_box(pen.img, ym, YELLOW, x, pen.y(70), mark_h, VV_BOX)
    x += mark_w + gap
    base1 = pen.y(175)
    draw_tracked(pen.draw, x, base1, WORD_WHITE, word, WHITE, -0.01)
    draw_tracked(pen.draw, x + ww_white - 0.01 * word.size, base1, WORD_YELLOW, word, YELLOW, -0.01)

    x = pen.cx - row2 / 2
    for i, w in enumerate(words):
        pen.draw.text((x, pen.y(263)), w, font=niche, fill=YELLOW if i == 1 else WHITE, anchor="ls")
        x += niche.getlength(w) + sp

    # progress bar under the niche line: yellow for the first 37 %, then a dark track
    bx0, bx1 = pen.cx - row2 / 2, pen.cx + row2 / 2
    by, bh = pen.y(287), 10 * s
    pen.draw.rounded_rectangle([bx0, by - bh / 2, bx1, by + bh / 2], radius=bh / 2, fill=BAR_TRACK)
    pen.draw.rounded_rectangle([bx0, by - bh / 2, bx0 + 0.37 * (bx1 - bx0), by + bh / 2],
                               radius=bh / 2, fill=YELLOW)
    draw_tracked(pen.draw, pen.cx - row3 / 2, pen.y(337), meta_s, meta, STEEL, 0.2)
    return box


def wave_line(draw, x0, x1, y, s, color, bumps):
    """Thin flat line with a few small heartbeat squiggles at the given fractions."""
    if x1 - x0 < 20 * s:
        return
    pts, n = [], max(int((x1 - x0) / (2 * s)), 2)
    for i in range(n + 1):
        t = i / n
        x = x0 + (x1 - x0) * t
        dy = 0.0
        for c, amp, wid in bumps:
            u = (t - c) / wid
            if abs(u) < 1:
                dy += amp * math.sin(u * math.pi * 2) * (1 - abs(u)) * s
        pts.append((x, y + dy))
    draw.line(pts, fill=color, width=max(1, int(round(4 * s))), joint="curve")


def pulse_lockup(pen, measure_only=False, band_left=None, band_right=None):
    """Pulse mark + 'allviralvideozz' + tagline + niche line + side pulse lines."""
    s = pen.s
    mark_h = 238 * s
    mark_w = mark_width(PULSE_BOX, mark_h)
    gap = 62 * s
    word = font("Inter-Black.otf", 126 * s)
    ww = text_w(word, WORDMARK, -0.01)
    tag = font("Inter-Medium.otf", 35 * s)
    tag_s = "The moments worth watching. Clipped daily."
    tw = tag.getlength(tag_s)
    group = mark_w + gap + max(ww, tw)
    meta = font("Inter-Bold.otf", 22 * s)
    items = ["PODCASTS", "STREAMS", "FINANCE", "ENTERTAINMENT"]
    dot = "•"
    dot_gap = 0.35 * meta.size
    row3 = (sum(text_w(meta, it, 0.12) for it in items)
            + (len(items) - 1) * (2 * dot_gap + meta.getlength(dot)))
    left, right = pen.cx - group / 2, pen.cx + group / 2
    box = (min(left, pen.cx - row3 / 2), max(right, pen.cx + row3 / 2), pen.y(10), pen.y(325))
    if measure_only:
        return box

    # side pulse lines first, so nothing is drawn over the lockup
    band_left = 0 if band_left is None else band_left
    band_right = pen.img.width if band_right is None else band_right
    wy = pen.y(176)
    wave_line(pen.draw, band_left, left - 45 * s, wy, s, WAVE,
              [(0.38, -5, 0.05), (0.46, 5, 0.04), (0.55, -3, 0.05), (0.72, 3, 0.05)])
    wave_line(pen.draw, right + 50 * s, band_right, wy, s, WAVE,
              [(0.35, -3, 0.05), (0.52, 5, 0.04), (0.6, -4, 0.04), (0.8, 3, 0.05)])

    x = left
    paste_box(pen.img, pulse_mask(mark_h), YELLOW, x, pen.y(10), mark_h, PULSE_BOX)
    x += mark_w + gap
    draw_tracked(pen.draw, x, pen.y(207), WORDMARK, word, WHITE, -0.01)
    pen.draw.text((x + 2 * s, pen.y(257)), tag_s, font=tag, fill=LAVENDER, anchor="ls")

    x = pen.cx - row3 / 2
    for i, it in enumerate(items):
        draw_tracked(pen.draw, x, pen.y(325), it, meta, LAVENDER_META, 0.12)
        x += text_w(meta, it, 0.12)
        if i < len(items) - 1:
            x += dot_gap
            pen.draw.text((x, pen.y(325)), dot, font=meta, fill=YELLOW, anchor="ls")
            x += meta.getlength(dot) + dot_gap
    return box


LOCKUPS = {"vv_network": vv_network_lockup, "pulse": pulse_lockup}


def background(kind, w, h, cx=None):
    if kind == "vv_network":
        img = radial_bg(w, h, INK_LIFT, INK, cx=cx, radius=max(w, h) * 0.55)
        x_top = (cx if cx is not None else w / 2) + 0.42 * w
        return diagonal_sheen(img, x_top, x_top - 0.06 * w)
    return radial_bg(w, h, VIOLET, NIGHT, cx=cx, radius=max(w, h) * 0.55)


def fit_scale(kind, max_w, max_h):
    """Largest band-unit scale whose content fits in max_w x max_h px."""
    scratch = Pen(Image.new("RGB", (8, 8)), 0, 0, 1.0)
    l, r, t, b = LOCKUPS[kind](scratch, measure_only=True)
    return min(max_w / (r - l), max_h / (b - t))


def render_into(kind, img, cx, cy, s):
    pen = Pen(img, cx, cy, s)
    # centre the content box vertically on cy
    l, r, t, b = LOCKUPS[kind](Pen(Image.new("RGB", (8, 8)), cx, cy, s), measure_only=True)
    pen.cy += cy - (t + b) / 2
    return LOCKUPS[kind](pen)


def page_banner(kind):
    """The wide band exactly as the sheet showed it (1932 x 410)."""
    img = background(kind, BAND_W, BAND_H)
    LOCKUPS[kind](Pen(img, BAND_W / 2, BAND_H / 2, 1.0))
    return img


def youtube_banner(kind):
    SS = 2
    W, H = 2560 * SS, 1440 * SS
    img = background(kind, W, H)
    safe_w, safe_h = 1546 * SS, 423 * SS
    s = fit_scale(kind, safe_w * 0.94, safe_h * 0.90)
    l, r, t, b = render_into(kind, img, W / 2, H / 2, s)
    sx0, sx1 = (W - safe_w) / 2, (W + safe_w) / 2
    sy0, sy1 = (H - safe_h) / 2, (H + safe_h) / 2
    print(f"{kind} youtube: content x {l / SS:.0f}-{r / SS:.0f} (safe {sx0 / SS:.0f}-{sx1 / SS:.0f}), "
          f"y {t / SS:.0f}-{b / SS:.0f} (safe {sy0 / SS:.1f}-{sy1 / SS:.1f})")
    assert l >= sx0 and r <= sx1 and t >= sy0 and b <= sy1, "outside the YouTube safe area"
    return img.resize((2560, 1440), Image.LANCZOS)


def x_header(kind):
    SS = 2
    W, H = 1500 * SS, 500 * SS
    CX, CY = 920 * SS, 215 * SS  # right of the profile-picture overlap, a little high
    img = background(kind, W, H, cx=CX)
    s = fit_scale(kind, 960 * SS, 220 * SS)
    l, r, t, b = render_into(kind, img, CX, CY, s)
    print(f"{kind} x header: content x {l / SS:.0f}-{r / SS:.0f}, y {t / SS:.0f}-{b / SS:.0f}")
    # desktop profile picture covers about x 40-375, y 333-500
    assert l >= 420 * SS and r <= 1440 * SS and t >= 60 * SS and b <= 340 * SS
    return img.resize((1500, 500), Image.LANCZOS)


# ---------------------------------------------------------------- 3. preview sheet
def circle(img, size):
    small = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    m = Image.new("L", (size * 8, size * 8), 0)
    ImageDraw.Draw(m).ellipse([0, 0, size * 8 - 1, size * 8 - 1], fill=255)
    small.putalpha(m.resize((size, size), Image.LANCZOS))
    return small


def preview_sheet(avatars, bands):
    W, pad = 1600, 48
    names = {"vv_network": "1  VV Network", "pulse": "4  Pulse"}
    col_w = (W - 3 * pad) // 2
    band_h = int(col_w * BAND_H / BAND_W)
    H = pad + 300 + 60 + 60 + 2 * (W - 2 * pad) * BAND_H // BAND_W + 3 * 24 + pad
    sheet = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    title = font("Inter-Bold.otf", 30)
    handle = font("Inter-Medium.otf", 22)
    small = font("Inter-Medium.otf", 18)
    for i, kind in enumerate(("vv_network", "pulse")):
        x = pad + i * (col_w + pad)
        c = circle(avatars[kind], 260)
        sheet.paste(c, (x, pad), c)
        d.text((x + 290, pad + 110), names[kind], font=title, fill=(20, 20, 20), anchor="ls")
        d.text((x + 290, pad + 145), "@" + HANDLE, font=handle, fill=(110, 110, 110), anchor="ls")
        cx = x + 290
        for sz in (96, 48, 32):
            cc = circle(avatars[kind], sz)
            sheet.paste(cc, (cx, pad + 175 + (96 - sz) // 2), cc)
            cx += sz + 18
        d.text((cx + 6, pad + 175 + 54), "feed sizes", font=small, fill=(140, 140, 140), anchor="ls")
    y = pad + 300 + 40
    full_w = W - 2 * pad
    full_h = full_w * BAND_H // BAND_W
    for kind in ("vv_network", "pulse"):
        sheet.paste(bands[kind].resize((full_w, full_h), Image.LANCZOS), (pad, y))
        y += full_h + 24
    return sheet.crop((0, 0, W, y - 24 + pad))


if __name__ == "__main__":
    avatars, bands = {}, {}
    for kind in ("vv_network", "pulse"):
        out = os.path.join(HERE, kind)
        os.makedirs(out, exist_ok=True)
        avatars[kind] = avatar(kind)
        avatars[kind].save(os.path.join(out, "avatar_1024.png"), optimize=True)
        bands[kind] = page_banner(kind)
        bands[kind].save(os.path.join(out, "page_banner_1932x410.png"), optimize=True)
        youtube_banner(kind).save(os.path.join(out, "banner_youtube_2560x1440.png"), optimize=True)
        x_header(kind).save(os.path.join(out, "header_x_1500x500.png"), optimize=True)
    preview_sheet(avatars, bands).save(os.path.join(HERE, "preview_sheet.png"), optimize=True)
    print("done")
