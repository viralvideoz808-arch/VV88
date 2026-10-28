"""Side-profile car illustrations (SVG) in each car's paint colour, used until real photos are uploaded."""

from __future__ import annotations

import re
from functools import lru_cache

# Body silhouettes on a 400x160 canvas; wheels sit at x=100 and x=300 on y=120.
BODIES = {
    "sedan": "M22 118 C20 100 28 92 52 88 L120 82 C140 62 168 50 205 50 L250 50 C282 50 304 64 326 82 "
             "L366 88 C382 92 386 104 384 118 Z",
    "hatchback": "M24 118 C22 100 30 92 54 88 L118 82 C140 58 168 46 210 46 L262 48 C300 52 330 70 352 90 "
                 "L370 94 C380 100 382 108 380 118 Z",
    "coupe": "M20 118 C18 104 26 96 50 92 L130 84 C156 62 186 54 222 54 L252 56 C286 60 312 74 336 88 "
             "L370 92 C384 98 386 108 384 118 Z",
    "convertible": "M20 118 C18 104 26 96 50 92 L150 86 L170 70 L178 70 L170 86 L330 86 L370 92 "
                   "C384 98 386 108 384 118 Z",
    "suv": "M22 118 C20 96 26 84 46 80 L96 76 C108 50 122 38 156 38 L300 38 C326 38 342 52 352 76 "
           "L370 80 C384 86 386 100 384 118 Z",
    "truck": "M18 118 C16 98 22 86 42 82 L90 78 C100 52 112 42 140 42 L208 42 C220 42 226 50 226 62 "
             "L226 78 L384 78 L386 118 Z",
    "van": "M20 118 C18 92 22 70 40 60 C62 42 90 34 130 34 L340 34 C364 34 376 48 380 70 L386 118 Z",
}
WINDOWS = {
    "sedan": "M132 84 C150 66 172 58 204 58 L244 58 C270 58 288 68 306 84 Z",
    "hatchback": "M130 84 C150 62 174 54 210 54 L258 56 C290 60 314 74 330 88 Z",
    "coupe": "M144 86 C166 68 190 62 222 62 L248 64 C274 68 294 78 310 88 Z",
    "convertible": "",
    "suv": "M108 78 C118 56 130 46 158 46 L296 46 C316 46 330 58 338 78 Z",
    "truck": "M102 78 C110 58 120 50 142 50 L206 50 C214 50 218 56 218 64 L218 78 Z",
    "van": "M64 66 C80 52 100 44 132 44 L332 44 C352 44 364 54 368 72 L368 76 L64 76 Z",
}
PILLARS = {
    "sedan": [(214, 58, 214, 84)], "hatchback": [(220, 54, 220, 86)], "coupe": [],
    "convertible": [], "suv": [(220, 46, 220, 78), (290, 46, 290, 78)], "truck": [(160, 50, 160, 78)],
    "van": [(150, 44, 150, 76), (250, 44, 250, 76)],
}

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _shade(hex_color: str, factor: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    if factor < 1:
        r, g, b = (int(c * factor) for c in (r, g, b))
    else:
        r, g, b = (int(c + (255 - c) * (factor - 1)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


@lru_cache(maxsize=256)
def car_svg(body: str = "sedan", color: str = "#9aa3ad", uid: str = "c") -> str:
    body = body if body in BODIES else "sedan"
    color = color if HEX.match(color or "") else "#9aa3ad"
    uid = re.sub(r"[^a-zA-Z0-9]", "", uid)[:24] or "c"
    light, dark = _shade(color, 1.35), _shade(color, 0.55)
    window = WINDOWS[body]
    pillars = "".join(f'<line x1="{a}" y1="{b}" x2="{c}" y2="{d}" stroke="{dark}" stroke-width="5"/>'
                      for a, b, c, d in PILLARS[body])
    wheels = "".join(
        f'<g transform="translate({x} 120)"><circle r="25" fill="#111"/><circle r="15" fill="#c9ced3"/>'
        f'<circle r="15" fill="url(#rim{uid})"/><circle r="4" fill="#555"/></g>' for x in (100, 300))
    arches = "".join(f'<path d="M{x - 31} 119 A31 31 0 0 1 {x + 31} 119 Z" fill="#14161a"/>' for x in (100, 300))
    return (
        f'<svg viewBox="0 0 400 160" xmlns="http://www.w3.org/2000/svg" role="img" aria-hidden="true">'
        f'<defs><linearGradient id="paint{uid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{light}"/><stop offset=".55" stop-color="{color}"/>'
        f'<stop offset="1" stop-color="{dark}"/></linearGradient>'
        f'<radialGradient id="rim{uid}"><stop offset="0" stop-color="#eef1f4"/>'
        f'<stop offset="1" stop-color="#7d848b"/></radialGradient>'
        f'<linearGradient id="glass{uid}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="#cfe3f3"/><stop offset="1" stop-color="#3b4d5e"/></linearGradient></defs>'
        f'<ellipse cx="200" cy="146" rx="180" ry="8" fill="#000" opacity=".22"/>'
        f'<path d="{BODIES[body]}" fill="url(#paint{uid})" stroke="{dark}" stroke-width="2"/>'
        + (f'<path d="{window}" fill="url(#glass{uid})" opacity=".92"/>' if window else "")
        + pillars + arches + wheels
        + f'<rect x="368" y="96" width="14" height="6" rx="3" fill="#ffe7a3"/>'
          f'<rect x="22" y="98" width="12" height="6" rx="3" fill="#ff5252"/>'
          f'<path d="M60 104 L340 104" stroke="{light}" stroke-width="2" opacity=".55"/>'
          f'</svg>'
    )
