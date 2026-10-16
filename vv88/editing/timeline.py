"""Jump cuts: drop dead air between words and remap timestamps onto the edited timeline."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from vv88.models import Word


def keep_segments(
    words: list[Word],
    start: float,
    end: float,
    max_gap: float = 0.35,
    pad: float = 0.08,
    min_segment: float = 0.3,
) -> list[tuple[float, float]]:
    """Absolute-time ranges to keep. Gaps between words longer than `max_gap` are removed."""
    ws = [w for w in words if w.end > start and w.start < end]
    if not ws or max_gap <= 0:
        return [(start, end)]
    segs: list[list[float]] = []
    seg_start = max(start, ws[0].start - pad)
    prev_end = ws[0].end
    for w in ws[1:]:
        if w.start - prev_end > max_gap:
            cut_at = min(prev_end + pad, w.start - pad)
            segs.append([seg_start, cut_at])
            seg_start = max(cut_at, w.start - pad)
        prev_end = max(prev_end, w.end)
    segs.append([seg_start, min(end, prev_end + pad + 0.12)])

    # Fold very short fragments into their neighbour so the edit doesn't stutter.
    merged: list[list[float]] = []
    for s in segs:
        if merged and (s[1] - s[0] < min_segment or merged[-1][1] - merged[-1][0] < min_segment):
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    return [(round(a, 3), round(b, 3)) for a, b in merged if b - a > 0.05]


@dataclass
class Timeline:
    """Maps source-absolute times to the output timeline produced by `segments`."""

    segments: list[tuple[float, float]]

    def __post_init__(self) -> None:
        self._starts = [a for a, _ in self.segments]
        self._offsets = []
        acc = 0.0
        for a, b in self.segments:
            self._offsets.append(acc)
            acc += b - a
        self.duration = acc

    def map(self, t: float) -> float:
        i = bisect_right(self._starts, t) - 1
        if i < 0:
            return 0.0
        a, b = self.segments[i]
        return self._offsets[i] + min(max(t - a, 0.0), b - a)

    def boundaries(self) -> list[float]:
        """Output-time positions of each jump cut."""
        return self._offsets[1:]

    def relative(self, origin: float) -> list[tuple[float, float]]:
        return [(a - origin, b - origin) for a, b in self.segments]
