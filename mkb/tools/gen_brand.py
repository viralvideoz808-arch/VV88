"""Generate the MKB logo SVGs with the wordmark outlined to paths.

Outlined text means the logos look identical everywhere, even where the
brand fonts are not installed.

    pip install fonttools
    python tools/gen_brand.py --syne Syne-800.ttf --manrope Manrope-800.ttf --manrope600 Manrope-600.ttf

Fonts: Syne and Manrope from Google Fonts (SIL Open Font License).
Writes into public/assets/brand/.
"""
import argparse
import os

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

OUT = os.path.join(os.path.dirname(__file__), "..", "public", "assets", "brand")

BLACK = "#040605"
DEEP = "#0A2A1F"
FOREST = "#0F3D2E"
JUNGLE = "#29AB87"
MINT = "#6FE3BF"
PAPER = "#ECF5F1"

# The mark: an M whose outer stems rise into bull horns. 120 x 120 grid.
M_PATH = "M26 92V48h13l21 23 21-23h13v44H81V70L60 92 39 70v22z"
HORN_L = "M26 48c-6-3-11-9-12-18-1-6 1-12 4-16 0 9 4 15 9 19 4 3 8 6 12 15z"
HORN_R = "M94 48c6-3 11-9 12-18 1-6-1-12-4-16 0 9-4 15-9 19-4 3-8 6-12 15z"


def outline(font_path, text, size, track=0.0):
    font = TTFont(font_path)
    gs = font.getGlyphSet()
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    upm = font["head"].unitsPerEm
    scale = size / upm
    asc = font["hhea"].ascent
    pen = SVGPathPen(gs)
    x = 0.0
    for ch in text:
        g = cmap.get(ord(ch))
        if g is None:
            continue
        gs[g].draw(TransformPen(pen, (scale, 0, 0, -scale, x, asc * scale)))
        x += hmtx[g][0] * scale + track * size
    x -= track * size
    cap = font["OS/2"].sCapHeight * scale
    return {"d": pen.getCommands(), "w": x, "asc": asc * scale, "cap": cap}


def defs(uid):
    return f"""<defs>
  <linearGradient id="{uid}bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{FOREST}"/><stop offset="1" stop-color="#030504"/></linearGradient>
  <linearGradient id="{uid}m" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#1F8A68"/><stop offset="1" stop-color="#4FD1A8"/></linearGradient>
  <linearGradient id="{uid}h" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="{JUNGLE}"/><stop offset="1" stop-color="#C9F5E6"/></linearGradient>
</defs>"""


def mark_group(uid, badge=True, mono=None):
    parts = []
    if badge:
        parts.append(f'<rect width="120" height="120" rx="28" fill="url(#{uid}bg)"/>')
        parts.append(f'<rect x=".75" y=".75" width="118.5" height="118.5" rx="27.3" fill="none" stroke="{JUNGLE}" stroke-opacity=".35" stroke-width="1.5"/>')
    mf = mono or f"url(#{uid}m)"
    hf = mono or f"url(#{uid}h)"
    parts.append(f'<path d="{M_PATH}" fill="{mf}"/>')
    parts.append(f'<path d="{HORN_L}" fill="{hf}"/>')
    parts.append(f'<path d="{HORN_R}" fill="{hf}"/>')
    return "\n  ".join(parts)


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}" role="img" aria-label="{title}">\n'
            f"<title>{title}</title>\n{body}\n</svg>\n")


def text_path(t, x, baseline, scale, fill):
    # outline() puts the baseline at y = asc; shift so it sits on `baseline`.
    ty = baseline - t["asc"] * scale
    return f'<path transform="translate({x:.2f} {ty:.2f}) scale({scale:.4f})" d="{t["d"]}" fill="{fill}"/>'


def write(name, content):
    with open(os.path.join(OUT, name), "w") as f:
        f.write(content)
    print("wrote", name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--syne", required=True)
    ap.add_argument("--manrope", required=True)
    ap.add_argument("--manrope600", required=True)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    mkb = outline(a.syne, "MKB", 100, 0.02)
    name = outline(a.manrope, "MOSES BRIGHT KANG’ARA", 20, 0.18)
    tag = outline(a.manrope600, "BUILD · TRADE · SCALE", 20, 0.3)

    # Mark only
    write("mkb-mark.svg", svg(120, 120, defs("a") + "\n  " + mark_group("a"), "MKB mark"))
    write("mkb-mark-bare.svg", svg(120, 120, defs("b") + "\n  " + mark_group("b", badge=False), "MKB mark"))
    write("mkb-mark-white.svg", svg(120, 120, mark_group("c", badge=False, mono=PAPER), "MKB mark (white)"))
    write("mkb-mark-black.svg", svg(120, 120, mark_group("d", badge=False, mono=BLACK), "MKB mark (black)"))
    write("mkb-mark-jungle.svg", svg(120, 120, mark_group("e", badge=False, mono=JUNGLE), "MKB mark (jungle green)"))

    # Horizontal lockup: mark | MKB over the full name
    def horizontal(uid, word_fill, name_fill, badge=True, mono=None):
        s_word = 56 / mkb["cap"]
        word_w = mkb["w"] * s_word
        s_name = word_w / name["w"]
        x0 = 146
        body = (defs(uid) if not mono else "") + "\n  " + mark_group(uid, badge=badge, mono=mono)
        body += "\n  " + text_path(mkb, x0, 70, s_word, word_fill)
        body += "\n  " + text_path(name, x0 + 2, 101, s_name, name_fill)
        return svg(x0 + word_w + 8, 120, body, "MKB — Moses Bright Kang'ara")

    write("mkb-logo-horizontal.svg", horizontal("f", PAPER, JUNGLE))
    write("mkb-logo-horizontal-light.svg", horizontal("g", BLACK, FOREST))
    write("mkb-logo-horizontal-white.svg", horizontal("h", PAPER, PAPER, badge=False, mono=PAPER))

    # Stacked lockup: mark above, MKB, name, tagline
    def stacked(uid, word_fill, name_fill, tag_fill):
        W = 420
        s_word = 64 / mkb["cap"]
        word_w = mkb["w"] * s_word
        s_name = 0.95
        name_w = name["w"] * s_name
        s_tag = 0.72
        tag_w = tag["w"] * s_tag
        body = defs(uid)
        body += f'\n  <g transform="translate({(W - 150) / 2:.2f} 10) scale(1.25)">' + mark_group(uid) + "</g>"
        body += "\n  " + text_path(mkb, (W - word_w) / 2, 238, s_word, word_fill)
        body += "\n  " + text_path(name, (W - name_w) / 2, 276, s_name, name_fill)
        body += "\n  " + text_path(tag, (W - tag_w) / 2, 306, s_tag, tag_fill)
        return svg(W, 320, body, "MKB — Moses Bright Kang'ara")

    write("mkb-logo-stacked.svg", stacked("i", PAPER, JUNGLE, "#8FA89D"))
    write("mkb-logo-stacked-light.svg", stacked("j", BLACK, FOREST, "#4A5C54"))

    # Favicon: the badge mark, tuned for small sizes (thicker border, no gradient noise)
    fav = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">'
           f'<rect width="120" height="120" rx="28" fill="{DEEP}"/>'
           f'<path d="{M_PATH}" fill="{JUNGLE}"/><path d="{HORN_L}" fill="{MINT}"/><path d="{HORN_R}" fill="{MINT}"/></svg>\n')
    write("favicon.svg", fav)

    # Path data for the site's inline use
    write("wordmark-paths.txt", f"MKB {mkb['w']:.2f} {mkb['asc']:.2f} {mkb['cap']:.2f}\n{mkb['d']}\n")


if __name__ == "__main__":
    main()
