"""Find the most clippable moments in a long video.

Every window that starts and ends on a sentence boundary and fits the target length is
scored on signals that predict short-form retention:

* hook      – the first sentence: questions, numbers/money, bold claims, emotion, story openers
              (viewers decide in the first ~1–3 s); penalises starting mid-thought ("so", "and", "um")
* energy    – loudness relative to the whole video, plus spikes (laughs, shouting, reactions)
* pace      – words per second near the 2.5–3.5 w/s sweet spot for talking-head clips
* payoff    – ends on a complete sentence
* density   – little dead air (dead air gets jump-cut anyway, but heavy silence means a weak moment)
* length    – close to the target length (viral-tier median ≈ 41 s)
* relevance – mentions the campaign's brand / keywords (what the campaign is paying for)
* visual    – scene changes (from the source edit) keep the eye moving

The top windows are picked with non-maximum suppression so clips don't repeat each other.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from vv88.config import AnalysisConfig
from vv88.models import ClipCandidate, Sentence, Transcript

_END_PUNCT = re.compile(r"[.!?…]['\")\]]*$")

HOOK_RULES: list[tuple[re.Pattern, float, str]] = [
    (re.compile(r"\?\s*$"), 1.0, "question"),
    (re.compile(r"^(why|how|what|who|when|where|is|are|do|does|did|can|could|should|would|have you)\b", re.I),
     0.7, "question"),
    (re.compile(r"(\$\s?\d|\b\d+(\.\d+)?\s?(k|m|million|billion|thousand|percent|%|x)\b|\b\d{2,}\b)", re.I),
     0.8, "number"),
    (re.compile(r"\b(never|nobody|no one|everyone|secret|truth|mistake|wrong|lie|lied|biggest|worst|best|"
                r"only|stop|don't|dont|nobody's|the reason)\b", re.I), 0.6, "bold_claim"),
    (re.compile(r"\b(crazy|insane|unbelievable|shocking|wild|terrifying|hilarious|scary|ridiculous|"
                r"disgusting|obsessed|furious)\b", re.I), 0.6, "emotion"),
    (re.compile(r"\b(i was|when i|the day|one time|this guy|my (dad|mom|wife|husband|friend)|story)\b", re.I),
     0.45, "story"),
    (re.compile(r"\b(you|your|you're)\b", re.I), 0.25, "direct"),
]
WEAK_START = re.compile(r"^(so|and|but|um+|uh+|like|yeah|i mean|okay|ok|anyway|right)\b[, ]", re.I)
DANGLING_REF = re.compile(r"^(it|that|this|he|she|they|them|those|these)\b", re.I)


@dataclass
class AnalysisSignals:
    loudness_db: np.ndarray | None = None  # per-hop dBFS over the whole source
    hop: float = 0.25
    scene_cuts: list[float] | None = None


def split_sentences(t: Transcript, max_gap: float = 0.7, max_words: int = 35) -> list[Sentence]:
    sentences: list[Sentence] = []
    if not t.words:
        return sentences
    start_idx = 0
    for i, w in enumerate(t.words):
        last = i == len(t.words) - 1
        gap = (t.words[i + 1].start - w.end) if not last else 0.0
        n = i - start_idx + 1
        if last or _END_PUNCT.search(w.text) or gap > max_gap or n >= max_words:
            ws = t.words[start_idx : i + 1]
            sentences.append(
                Sentence(
                    start=ws[0].start,
                    end=ws[-1].end,
                    text=" ".join(x.text for x in ws),
                    word_start_idx=start_idx,
                    word_end_idx=i + 1,
                )
            )
            start_idx = i + 1
    return sentences


def hook_score(text: str) -> tuple[float, str]:
    score = 0.0
    best_type, best_weight = "statement", 0.0
    for pattern, weight, kind in HOOK_RULES:
        if pattern.search(text):
            score += weight
            if weight > best_weight:
                best_type, best_weight = kind, weight
    if WEAK_START.search(text + " "):
        score -= 0.6
    elif DANGLING_REF.search(text):
        score -= 0.25
    return min(score, 2.0), best_type


def _gauss(x: float, mu: float, sigma: float) -> float:
    return math.exp(-(((x - mu) / sigma) ** 2) / 2)


def make_hook_text(text: str, max_words: int = 7) -> str:
    words = re.sub(r"\s+", " ", text).strip().split(" ")
    words = [w for w in words if w]
    while words and WEAK_START.match(words[0] + " "):
        words = words[1:]
    if not words:
        return ""
    if len(words) <= max_words:
        out = " ".join(words)
    else:
        # Prefer ending on a clause break ("The truth is, ...") over chopping mid-phrase.
        cut = max_words
        for i in range(max_words, 3, -1):
            if re.search(r"[,;:.!?]$", words[i - 1]):
                cut = i
                break
        out = " ".join(words[:cut]).rstrip(",;:")
        if cut == max_words and not re.search(r"[.!?]$", out):
            out += "…"
    return out[0].upper() + out[1:]


_STOP = set(
    "about above after again against because before being below between could doing during every going "
    "gonna having into itself just know like little maybe might more most much never only other ourselves "
    "really right said same should since some something still such than that their them then there these "
    "they thing things think this those through today told under until very want wanna what when where "
    "which while will with would your yours yourself actually basically literally people okay yeah".split()
)


def suggest_hashtags(text: str, category: str = "", keywords: list[str] | None = None, limit: int = 6) -> list[str]:
    """Niche hashtags without an LLM: campaign niche + brand terms + the clip's most repeated content words."""
    tags: list[str] = []
    for k in [category, *(keywords or [])]:
        k = re.sub(r"[^\w]", "", (k or "").lower())
        if k and k != "general" and f"#{k}" not in tags:
            tags.append(f"#{k}")
    counts: dict[str, int] = {}
    for w in re.findall(r"[a-zA-Z]{5,}", text.lower()):
        if w not in _STOP:
            counts[w] = counts.get(w, 0) + 1
    for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]))):
        if len(tags) >= limit:
            break
        if f"#{w}" not in tags:
            tags.append(f"#{w}")
    return tags[:limit]


class HighlightFinder:
    def __init__(self, cfg: AnalysisConfig, keywords: list[str] | None = None):
        self.cfg = cfg
        kw = [k.strip() for k in (keywords or []) if k and k.strip()]
        self.keyword_re = re.compile(r"\b(" + "|".join(map(re.escape, kw)) + r")\b", re.I) if kw else None

    def candidates(self, t: Transcript, signals: AnalysisSignals | None = None) -> list[ClipCandidate]:
        sentences = split_sentences(t)
        if not sentences:
            return []
        signals = signals or AnalysisSignals()
        loud = signals.loudness_db
        loud_cum = loud_z = peak_cum = None
        if loud is not None and loud.size:
            voiced = loud[loud > -60]
            mu = float(voiced.mean()) if voiced.size else float(loud.mean())
            sd = float(voiced.std()) if voiced.size else float(loud.std()) or 1.0
            loud_z = np.clip((loud - mu) / (sd or 1.0), -3, 3)
            loud_cum = np.concatenate([[0.0], np.cumsum(loud_z)])
            peak_cum = np.concatenate([[0.0], np.cumsum(loud_z > 1.5)])
        cuts = np.array(sorted(signals.scene_cuts or []), dtype=float)

        hooks = [hook_score(s.text) for s in sentences]
        kw_hits = [len(self.keyword_re.findall(s.text)) if self.keyword_re else 0 for s in sentences]
        word_cum = np.concatenate([[0], np.cumsum([s.word_end_idx - s.word_start_idx for s in sentences])])
        speech_cum = np.concatenate([[0.0], np.cumsum([s.end - s.start for s in sentences])])
        kw_cum = np.concatenate([[0], np.cumsum(kw_hits)])

        cfg = self.cfg
        out: list[ClipCandidate] = []
        for i, first in enumerate(sentences):
            for j in range(i, len(sentences)):
                last = sentences[j]
                dur = last.end - first.start
                if dur > cfg.max_clip_seconds:
                    break
                if dur < cfg.min_clip_seconds:
                    continue
                f: dict[str, float] = {}
                h, hook_type = hooks[i]
                # A very short opener ("Listen.") lets the next sentence carry the hook.
                if first.end - first.start < 1.5 and j > i:
                    h2, t2 = hooks[i + 1]
                    if h2 * 0.8 > h:
                        h, hook_type = h2 * 0.8, t2
                f["hook"] = h
                n_words = int(word_cum[j + 1] - word_cum[i])
                f["pace"] = _gauss(n_words / dur, 3.0, 0.9)
                f["density"] = float(min(1.0, (speech_cum[j + 1] - speech_cum[i]) / dur))
                f["payoff"] = 1.0 if _END_PUNCT.search(last.text) else 0.0
                f["length"] = _gauss(dur, cfg.target_clip_seconds, 12.0)
                f["keywords"] = min(1.0, float(kw_cum[j + 1] - kw_cum[i]) / 2.0)
                if loud_cum is not None:
                    a = int(first.start / signals.hop)
                    b = max(a + 1, int(last.end / signals.hop))
                    b = min(b, loud_cum.size - 1)
                    a = min(a, b - 1) if b > 0 else 0
                    span = max(1, b - a)
                    f["energy"] = float((loud_cum[b] - loud_cum[a]) / span)
                    f["peaks"] = float(min(1.0, (peak_cum[b] - peak_cum[a]) / span * 10))
                    # Energy in the opening 3 s matters most for the scroll-stop.
                    a3 = min(b, a + max(1, int(3.0 / signals.hop)))
                    f["open_energy"] = float((loud_cum[a3] - loud_cum[a]) / max(1, a3 - a))
                else:
                    f["energy"] = f["peaks"] = f["open_energy"] = 0.0
                if cuts.size:
                    n_cuts = int(np.searchsorted(cuts, last.end) - np.searchsorted(cuts, first.start))
                    f["visual"] = min(1.0, n_cuts / dur * 5)
                else:
                    f["visual"] = 0.0

                score = (
                    2.0 * f["hook"]
                    + 0.9 * f["energy"]
                    + 0.5 * f["open_energy"]
                    + 0.8 * f["peaks"]
                    + 0.8 * f["pace"]
                    + 1.0 * f["density"]
                    + 0.6 * f["payoff"]
                    + 1.0 * f["length"]
                    + 0.7 * f["keywords"]
                    + 0.3 * f["visual"]
                )
                text = " ".join(s.text for s in sentences[i : j + 1])
                out.append(
                    ClipCandidate(
                        start=first.start,
                        end=last.end,
                        score=score,
                        text=text,
                        hook_text=make_hook_text(first.text if hooks[i][0] >= h - 1e-9 else sentences[i + 1].text),
                        hook_type=hook_type,
                        features=f,
                    )
                )
        out.sort(key=lambda c: c.score, reverse=True)
        return out

    def select(
        self,
        candidates: list[ClipCandidate],
        n: int,
        taken: list[tuple[float, float]] | None = None,
    ) -> list[ClipCandidate]:
        """Greedy non-maximum suppression over time ranges."""
        chosen: list[ClipCandidate] = []
        blocked = list(taken or [])
        for c in candidates:
            if len(chosen) >= n:
                break
            if any(_overlap(c.start, c.end, a, b) > self.cfg.max_overlap for a, b in blocked):
                continue
            chosen.append(c)
            blocked.append((c.start, c.end))
        return chosen


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    return inter / max(1e-9, min(a1 - a0, b1 - b0))
