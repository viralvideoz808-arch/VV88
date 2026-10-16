import numpy as np

from vv88.analysis.highlights import AnalysisSignals, HighlightFinder, hook_score, make_hook_text, split_sentences
from vv88.config import AnalysisConfig


def test_split_sentences_on_punctuation_and_gaps(transcript):
    sentences = split_sentences(transcript)
    assert sentences[0].text == "Why do most people never get rich?"
    assert all(s.end > s.start for s in sentences)
    assert sentences[-1].word_end_idx == len(transcript.words)


def test_hook_score_prefers_questions_numbers_and_bold_claims():
    q, q_type = hook_score("Why do most people never get rich?")
    weak, _ = hook_score("So um anyway that is basically it.")
    num, num_type = hook_score("I made $50,000 in 30 days.")
    assert q > weak and num > weak
    assert q_type == "question"
    assert num_type == "number"


def test_make_hook_text_drops_filler_and_caps_length():
    assert make_hook_text("so the truth is nobody tells you this about money at all") == "The truth is nobody tells you this…"
    assert make_hook_text("What would you do?") == "What would you do?"


def test_candidates_respect_length_bounds_and_rank_hooks_first(transcript):
    cfg = AnalysisConfig(min_clip_seconds=8, max_clip_seconds=25, target_clip_seconds=15)
    cands = HighlightFinder(cfg).candidates(transcript)
    assert cands
    assert all(cfg.min_clip_seconds <= c.duration <= cfg.max_clip_seconds for c in cands)
    assert cands[0].hook_type in {"question", "number", "bold_claim"}
    assert not cands[0].text.lower().startswith("so um")


def test_select_applies_non_max_suppression(transcript):
    cfg = AnalysisConfig(min_clip_seconds=6, max_clip_seconds=20, target_clip_seconds=12, max_overlap=0.1)
    finder = HighlightFinder(cfg)
    chosen = finder.select(finder.candidates(transcript), n=3)
    for i, a in enumerate(chosen):
        for b in chosen[i + 1 :]:
            inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
            assert inter / min(a.duration, b.duration) <= 0.1 + 1e-9


def test_select_skips_ranges_already_clipped(transcript):
    cfg = AnalysisConfig(min_clip_seconds=6, max_clip_seconds=20, target_clip_seconds=12)
    finder = HighlightFinder(cfg)
    cands = finder.candidates(transcript)
    best = cands[0]
    again = finder.select(cands, n=1, taken=[(best.start, best.end)])
    assert again and (again[0].start, again[0].end) != (best.start, best.end)


def test_energy_and_keywords_shift_ranking(transcript):
    cfg = AnalysisConfig(min_clip_seconds=6, max_clip_seconds=14, target_clip_seconds=10)
    hop = 0.25
    loud = np.full(int(transcript.duration / hop) + 1, -30.0, dtype=np.float32)
    # A loud reaction late in the video should pull a window there up the ranking.
    late = int(25 / hop)
    loud[late : late + 40] = -8.0
    quiet = HighlightFinder(cfg).candidates(transcript, AnalysisSignals(loudness_db=np.full_like(loud, -30.0)))
    loud_c = HighlightFinder(cfg).candidates(transcript, AnalysisSignals(loudness_db=loud, hop=hop))
    best_loud = max(loud_c, key=lambda c: c.features["energy"])
    assert best_loud.end > 25
    kw = HighlightFinder(cfg, keywords=["million dollars"]).candidates(transcript)
    assert any(c.features["keywords"] > 0 for c in kw)
    assert quiet


def test_hook_prefers_clause_break_and_hashtags_from_text():
    from vv88.analysis.highlights import suggest_hashtags

    assert make_hook_text("Here is the crazy part, your salary means nothing at all") == "Here is the crazy part"
    assert make_hook_text("I was broke and had three credit cards maxed out") == "I was broke and had three credit…"
    tags = suggest_hashtags("Savings beat income. Your savings rate decides when you retire.", "finance", ["money pod"])
    assert tags[:2] == ["#finance", "#moneypod"] and "#savings" in tags
