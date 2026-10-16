"""Caption / layout presets for the short-form looks that dominate feeds in 2026.

Burned-in animated captions show up on ~79-80% of viral-tier clips (Opus Clip, 13.5M-clip
study), and word-by-word "karaoke" timing is now the baseline rather than a trend. The
presets below cover the four looks that matter; the optimizer A/B-tests them and shifts
traffic toward whichever is winning on your accounts this week.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> ASS '&HAABBGGRR' (alpha 0 = opaque)."""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


@dataclass(frozen=True)
class Style:
    name: str
    description: str
    font: str | None = None  # None = settings.editing.font
    font_size: int = 88
    bold: bool = True
    case: str = "upper"  # upper | lower | sentence
    words_per_chunk: int = 3
    max_chunk_chars: int = 18
    text_color: str = "#FFFFFF"
    inactive_alpha: int = 0  # 0 = opaque; higher dims words that aren't being spoken
    highlight_colors: tuple[str, ...] = ("#FFE600",)
    outline_color: str = "#000000"
    outline: float = 6.0
    shadow: float = 0.0
    pop_scale: int = 112  # % scale of the active word; 100 = off
    position_y: float = 0.70  # caption line centre, fraction of frame height
    hook_box: bool = True
    hook_font_size: int = 70
    hook_text_color: str = "#000000"
    hook_box_color: str = "#FFFFFF"
    hook_position_y: float = 0.16
    progress_color: str = "#FFFFFF"
    split_screen: bool = False
    punch_zoom: bool = True
    tags: tuple[str, ...] = field(default_factory=tuple)


STYLES: dict[str, Style] = {
    "karaoke_pop": Style(
        name="karaoke_pop",
        description="Bold uppercase, 2-3 words at a time, yellow active word that pops. The default high-retention look.",
        font_size=92,
        case="upper",
        words_per_chunk=3,
        highlight_colors=("#FFE600",),
        outline=7.0,
        pop_scale=114,
        position_y=0.69,
    ),
    "dynamic_minimal": Style(
        name="dynamic_minimal",
        description="Word-by-word timing without the loud colours: white text, unspoken words dimmed, no pop.",
        font_size=74,
        case="sentence",
        words_per_chunk=4,
        max_chunk_chars=24,
        inactive_alpha=0x70,
        highlight_colors=("#FFFFFF",),
        outline=3.0,
        shadow=2.0,
        pop_scale=100,
        position_y=0.72,
        hook_box=False,
        hook_text_color="#FFFFFF",
        hook_font_size=64,
    ),
    "soft_lowercase": Style(
        name="soft_lowercase",
        description="Single-line lowercase captions in soft type ('quiet luxury' / aesthetic niches).",
        font_size=64,
        bold=False,
        case="lower",
        words_per_chunk=5,
        max_chunk_chars=30,
        highlight_colors=("#F5E6C8",),
        outline=1.5,
        outline_color="#333333",
        shadow=3.0,
        pop_scale=100,
        position_y=0.78,
        hook_box=False,
        hook_text_color="#FFFFFF",
        hook_font_size=56,
        punch_zoom=False,
    ),
    "brainrot_stack": Style(
        name="brainrot_stack",
        description="Speaker on top, gameplay underneath, kinetic 3-colour captions on the seam.",
        font_size=88,
        case="upper",
        words_per_chunk=2,
        max_chunk_chars=14,
        highlight_colors=("#FFE600", "#39FF14", "#00E5FF"),
        outline=8.0,
        pop_scale=118,
        position_y=0.50,
        hook_position_y=0.08,
        split_screen=True,
        punch_zoom=False,
    ),
}


def get_style(name: str) -> Style:
    try:
        return STYLES[name]
    except KeyError:
        raise ValueError(f"unknown style {name!r}; choose from {', '.join(STYLES)}") from None
