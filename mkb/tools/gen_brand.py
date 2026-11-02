"""Generate the MKB logo SVGs with the wordmark outlined to paths.

Logo: "Candle M", an M drawn as five candlesticks. Theme: "Trading Terminal".
Outlined text means the logos look identical everywhere, even where the
brand fonts are not installed.

    pip install fonttools
    python tools/gen_brand.py --grotesk SpaceGrotesk-700.ttf --mono JetBrainsMono-700.ttf

Fonts: Space Grotesk and JetBrains Mono from Google Fonts (SIL Open Font License).
Writes into public/assets/brand/.
"""
import argparse
import os

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

OUT = os.path.join(os.path.dirname(__file__), "..", "public", "assets", "brand")

BLACK = "#000000"
DARK = "#07130E"       # dark green
JUNGLE = "#2BD69F"     # electric jungle green (primary)
JUNGLE_2 = "#29AB87"   # classic jungle green
MINT = "#D9FBEA"       # light text tint
MUTED = "#7FA594"
LIGHT_A = "#14936B"    # jungle tuned for light backgrounds
LIGHT_B = "#07130E"
PAPER = "#F2FBF7"

# The mark: five candlesticks in an M silhouette on a 120 x 120 grid.
# (centre x, body top, body height, wick top, wick bottom, role)
# role a = outer bullish candles, b = inner candles, o = hollow (bearish) centre candle
CANDLES = [
    (21.0, 36, 62, 26, 104, "a"),
    (40.5, 42, 24, 33, 74, "b"),
    (60.0, 56, 24, 47, 90, "o"),
    (79.5, 42, 24, 33, 74, "b"),
    (99.0, 36, 62, 26, 104, "a"),
]
BODY_W = 14


def mark_shapes(a, b, hollow_stroke=None):
    """SVG for the five candles. Wicks stop at the body, so the hollow candle stays see-through."""
    out = []
    hs = hollow_stroke or a
    for cx, y, h, wt, wb, role in CANDLES:
        x = cx - BODY_W / 2
        wick = a if role != "b" else a
        out.append(f'<path d="M{cx} {wt}V{y}M{cx} {y + h}V{wb}" stroke="{wick}" stroke-width="3" stroke-linecap="round" fill="none"/>')
        if role == "o":
            out.append(f'<rect x="{x + 1.5}" y="{y + 1.5}" width="{BODY_W - 3}" height="{h - 3}" rx="2" fill="none" stroke="{hs}" stroke-width="3"/>')
        else:
            out.append(f'<rect x="{x}" y="{y}" width="{BODY_W}" height="{h}" rx="3" fill="{a if role == "a" else b}"/>')
    return "".join(out)


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


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}" role="img" aria-label="{title}">\n'
            f"<title>{title}</title>\n{body}\n</svg>\n")


def text_path(t, x, baseline, scale, fill):
    ty = baseline - t["asc"] * scale
    return f'<path transform="translate({x:.2f} {ty:.2f}) scale({scale:.4f})" d="{t["d"]}" fill="{fill}"/>'


def in_badge(shapes):
    return f'<g transform="translate(60 60) scale(.84) translate(-60 -60)">{shapes}</g>'


def badge(uid):
    return (f'<defs><linearGradient id="{uid}bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0B2219"/><stop offset=".55" stop-color="{BLACK}"/></linearGradient></defs>'
            f'<rect width="120" height="120" rx="16" fill="url(#{uid}bg)"/>'
            f'<rect x=".75" y=".75" width="118.5" height="118.5" rx="15.3" fill="none" stroke="{JUNGLE}" stroke-opacity=".4" stroke-width="1.5"/>')


def write(name, content):
    with open(os.path.join(OUT, name), "w") as f:
        f.write(content)
    print("wrote", name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grotesk", required=True, help="Space Grotesk Bold TTF")
    ap.add_argument("--mono", required=True, help="JetBrains Mono Bold TTF")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    mkb = outline(a.grotesk, "MKB", 100, 0.0)
    name = outline(a.mono, "MOSES BRIGHT KANG’ARA", 20, 0.08)
    tag = outline(a.mono, "BUILD · TRADE · SCALE", 20, 0.16)

    # Marks
    write("mkb-mark.svg", svg(120, 120, badge("a") + in_badge(mark_shapes(JUNGLE, MINT)), "MKB mark"))
    write("mkb-mark-bare.svg", svg(120, 120, mark_shapes(JUNGLE, MINT), "MKB mark"))
    write("mkb-mark-white.svg", svg(120, 120, mark_shapes("#FFFFFF", "#FFFFFF"), "MKB mark (white)"))
    write("mkb-mark-black.svg", svg(120, 120, mark_shapes(BLACK, BLACK), "MKB mark (black)"))
    write("mkb-mark-jungle.svg", svg(120, 120, mark_shapes(JUNGLE, JUNGLE), "MKB mark (jungle green)"))
    write("mkb-mark-light.svg", svg(120, 120, mark_shapes(LIGHT_A, LIGHT_B), "MKB mark (for light backgrounds)"))

    # Horizontal lockup: mark | MKB over the full name
    def horizontal(mark_svg, word_fill, name_fill):
        s_word = 56 / mkb["cap"]
        word_w = mkb["w"] * s_word
        s_name = word_w / name["w"]
        x0 = 140
        body = mark_svg
        body += "\n" + text_path(mkb, x0, 72, s_word, word_fill)
        body += "\n" + text_path(name, x0 + 1, 100, s_name, name_fill)
        return svg(x0 + word_w + 6, 120, body, "MKB — Moses Bright Kang'ara")

    write("mkb-logo-horizontal.svg", horizontal(badge("f") + in_badge(mark_shapes(JUNGLE, MINT)), MINT, JUNGLE))
    write("mkb-logo-horizontal-light.svg", horizontal(mark_shapes(LIGHT_A, LIGHT_B), BLACK, LIGHT_A))
    write("mkb-logo-horizontal-white.svg", horizontal(mark_shapes("#FFFFFF", "#FFFFFF"), "#FFFFFF", "#FFFFFF"))
    write("mkb-logo-horizontal-black.svg", horizontal(mark_shapes(BLACK, BLACK), BLACK, BLACK))

    # Stacked lockup: mark above, MKB, name, tagline
    def stacked(mark_svg, word_fill, name_fill, tag_fill):
        W = 420
        s_word = 66 / mkb["cap"]
        word_w = mkb["w"] * s_word
        s_name = 0.9
        name_w = name["w"] * s_name
        s_tag = 0.66
        tag_w = tag["w"] * s_tag
        body = f'<g transform="translate({(W - 150) / 2:.2f} 10) scale(1.25)">{mark_svg}</g>'
        body += "\n" + text_path(mkb, (W - word_w) / 2, 240, s_word, word_fill)
        body += "\n" + text_path(name, (W - name_w) / 2, 276, s_name, name_fill)
        body += "\n" + text_path(tag, (W - tag_w) / 2, 304, s_tag, tag_fill)
        return svg(W, 320, body, "MKB — Moses Bright Kang'ara")

    write("mkb-logo-stacked.svg", stacked(badge("i") + in_badge(mark_shapes(JUNGLE, MINT)), MINT, JUNGLE, MUTED))
    write("mkb-logo-stacked-light.svg", stacked(mark_shapes(LIGHT_A, LIGHT_B), BLACK, LIGHT_A, "#4E6B5F"))

    # Favicon: badge with slightly heavier candles for small sizes
    write("favicon.svg", f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect width="120" height="120" rx="18" fill="{BLACK}"/>{mark_shapes(JUNGLE, MINT)}</svg>\n')


if __name__ == "__main__":
    main()
