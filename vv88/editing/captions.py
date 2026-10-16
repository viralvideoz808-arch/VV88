"""Word-by-word animated captions as an ASS subtitle file (rendered by libass inside FFmpeg)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from vv88.editing.styles import Style, ass_color
from vv88.editing.timeline import Timeline
from vv88.models import Word

_PUNCT_STRIP = re.compile(r"^[\"'“”‘’(\[]+|[\"'“”‘’)\],;:]+$")
_BREAK_AFTER = re.compile(r"[.!?,;:…]['\")\]]*$")


@dataclass
class TimedWord:
    text: str
    start: float
    end: float


def _fmt_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def _case(text: str, mode: str) -> str:
    if mode == "upper":
        return text.upper()
    if mode == "lower":
        return text.lower()
    return text


def _display_word(text: str, style: Style) -> str:
    t = text.strip()
    if style.case in ("upper", "lower"):
        # Trailing commas/quotes look noisy in big kinetic captions; keep ?, ! and . for tone.
        t = _PUNCT_STRIP.sub("", t)
    return _case(t, style.case)


def remap_words(words: list[Word], timeline: Timeline) -> list[TimedWord]:
    out = []
    for w in words:
        s, e = timeline.map(w.start), timeline.map(w.end)
        if e - s < 0.04:
            e = s + 0.04
        out.append(TimedWord(w.text, s, e))
    return out


def chunk_words(words: list[TimedWord], style: Style, max_gap: float = 0.6) -> list[list[TimedWord]]:
    chunks: list[list[TimedWord]] = []
    cur: list[TimedWord] = []
    chars = 0
    for w in words:
        wlen = len(w.text.strip())
        gap = w.start - cur[-1].end if cur else 0.0
        if cur and (len(cur) >= style.words_per_chunk or chars + wlen + 1 > style.max_chunk_chars or gap > max_gap):
            chunks.append(cur)
            cur, chars = [], 0
        cur.append(w)
        chars += wlen + (1 if chars else 0)
        if _BREAK_AFTER.search(w.text):
            chunks.append(cur)
            cur, chars = [], 0
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(
    words: list[TimedWord],
    style: Style,
    font: str,
    width: int,
    height: int,
    duration: float,
    hook_text: str = "",
    hook_seconds: float = 2.6,
) -> str:
    font_name = style.font or font
    primary = ass_color(style.text_color)
    outline = ass_color(style.outline_color)
    shadow_col = ass_color("#000000", 0x60)
    bold = -1 if style.bold else 0
    scale = width / 1080.0
    fs = int(style.font_size * scale)
    hook_fs = int(style.hook_font_size * scale)

    if style.hook_box:
        hook_style = (
            f"Style: Hook,{font_name},{hook_fs},{ass_color(style.hook_text_color)},{primary},"
            f"{ass_color(style.hook_box_color)},{ass_color(style.hook_box_color)},{bold},0,0,0,100,100,0,0,"
            f"3,{max(10, int(18 * scale))},0,8,60,60,0,1"
        )
    else:
        hook_style = (
            f"Style: Hook,{font_name},{hook_fs},{ass_color(style.hook_text_color)},{primary},"
            f"{outline},{shadow_col},{bold},0,0,0,100,100,0,0,1,{max(2.0, style.outline)},{style.shadow or 2},8,60,60,0,1"
        )

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{font_name},{fs},{primary},{primary},{outline},{shadow_col},{bold},0,0,0,100,100,0,0,"
        f"1,{style.outline * scale:.1f},{style.shadow * scale:.1f},5,70,70,0,1",
        hook_style,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    cx = width // 2
    cy = int(height * style.position_y)

    if hook_text:
        hy = int(height * style.hook_position_y)
        hook = _escape(_case(hook_text, "upper" if style.case == "upper" else "sentence"))
        lines.append(
            f"Dialogue: 2,{_fmt_time(0)},{_fmt_time(min(hook_seconds, duration))},Hook,,0,0,0,,"
            f"{{\\an8\\pos({cx},{hy})\\fad(0,180)\\fscx92\\fscy92\\t(0,120,\\fscx100\\fscy100)}}{hook}"
        )

    chunks = chunk_words(words, style)
    color_i = 0
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0].start if ci + 1 < len(chunks) else duration
        # Hold the chunk on screen across short pauses so captions don't flicker.
        chunk_end = next_start if next_start - chunk[-1].end < 0.5 else chunk[-1].end + 0.25
        chunk_end = min(chunk_end, duration)
        display = [_escape(_display_word(w.text, style)) for w in chunk]
        for ai, active in enumerate(chunk):
            seg_start = active.start if ai else chunk[0].start
            seg_end = chunk[ai + 1].start if ai + 1 < len(chunk) else chunk_end
            if seg_end - seg_start <= 0.01:
                continue
            hl = ass_color(style.highlight_colors[color_i % len(style.highlight_colors)])
            color_i += 1
            parts = []
            for wi, word in enumerate(display):
                if not word:
                    continue
                if wi == ai:
                    pop = f"\\fscx{style.pop_scale}\\fscy{style.pop_scale}" if style.pop_scale != 100 else ""
                    parts.append(f"{{\\1c{hl}\\1a&H00&{pop}}}{word}{{\\r}}")
                elif style.inactive_alpha:
                    parts.append(f"{{\\1a&H{style.inactive_alpha:02X}&}}{word}{{\\r}}")
                else:
                    parts.append(word)
            entrance = "\\fad(60,0)" if ai == 0 else ""
            # Heavy outlines eat into word gaps; a wider space keeps words readable.
            gap = "  " if style.outline >= 5 else " "
            lines.append(
                f"Dialogue: 1,{_fmt_time(seg_start)},{_fmt_time(seg_end)},Caption,,0,0,0,,"
                f"{{\\an5\\pos({cx},{cy}){entrance}}}" + gap.join(parts)
            )
    return "\n".join(lines) + "\n"
