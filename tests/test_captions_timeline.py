from vv88.editing.captions import build_ass, chunk_words, remap_words
from vv88.editing.reframe import crop_size, plan_crop
from vv88.editing.styles import STYLES, ass_color
from vv88.editing.timeline import Timeline, keep_segments
from vv88.models import Word


def words_with_gap():
    return [Word("hello", 1.0, 1.3), Word("there", 1.35, 1.7), Word("big", 3.5, 3.8), Word("gap.", 3.85, 4.2)]


def test_keep_segments_removes_long_silence():
    segs = keep_segments(words_with_gap(), 0.8, 4.6, max_gap=0.35)
    assert len(segs) == 2
    assert segs[0][1] < 3.5 and segs[1][0] > 1.7
    tl = Timeline(segs)
    assert tl.duration < 4.6 - 0.8 - 1.0  # most of the 1.8 s pause is gone


def test_timeline_maps_monotonically():
    tl = Timeline(keep_segments(words_with_gap(), 0.8, 4.6))
    mapped = [tl.map(t) for t in (1.0, 1.35, 3.5, 3.85, 4.2)]
    assert mapped == sorted(mapped)
    assert mapped[2] - mapped[1] < 1.0


def test_no_words_keeps_whole_window():
    assert keep_segments([], 2.0, 9.0) == [(2.0, 9.0)]


def test_ass_color_is_bgr():
    assert ass_color("#FFE600") == "&H0000E6FF"


def test_chunking_respects_word_limit_and_sentence_breaks():
    style = STYLES["karaoke_pop"]
    tl = Timeline([(0.0, 10.0)])
    words = remap_words(
        [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate("one two three four five. six seven".split())], tl
    )
    chunks = chunk_words(words, style)
    assert all(len(c) <= style.words_per_chunk for c in chunks)
    assert any(c[-1].text == "five." for c in chunks)


def test_build_ass_contains_hook_and_highlighted_words():
    style = STYLES["karaoke_pop"]
    tl = Timeline([(0.0, 5.0)])
    ass = build_ass(remap_words(words_with_gap(), tl), style, "Inter Black", 1080, 1920, 5.0, "Watch {this}")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Style: Hook" in ass
    assert "WATCH (THIS)" in ass  # uppercased, braces escaped so libass doesn't eat them
    assert ass.count("Dialogue: 1,") == 4  # one event per spoken word
    assert "\\1c&H0000E6FF" in ass


def test_crop_size_and_native_vertical():
    assert crop_size(1920, 1080, 9 / 16) == (606, 1080)
    plan = plan_crop(1080, 1920, [], 30.0)
    assert plan.mode == "native"


def test_face_plan_holds_and_switches():
    # Speaker on the left for 5 s, then on the right.
    track = [(i * 0.4, 0.25 if i * 0.4 < 5 else 0.75) for i in range(25)]
    plan = plan_crop(1920, 1080, track, 10.0)
    assert plan.mode == "face"
    assert len(plan.holds) == 2
    assert plan.holds[0][2] < plan.holds[1][2]
    assert plan.x_expr().startswith("if(lt(t,")


def test_no_faces_falls_back_to_blur():
    plan = plan_crop(1920, 1080, [(i * 0.4, None) for i in range(20)], 8.0)
    assert plan.mode == "blur"
