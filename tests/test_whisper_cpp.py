"""whisper.cpp fallback (phones / Termux): token parsing, engine discovery, and a real run when available."""

import os
import shutil
import stat
import subprocess
import sys

import pytest

from vv88.analysis import transcribe as tr
from vv88.config import AnalysisConfig


def tok(text, t_dtw=-1, frm=0, p=0.9):
    return {"text": text, "t_dtw": t_dtw, "offsets": {"from": frm, "to": frm}, "p": p}


def doc(tokens, language="en"):
    return {"result": {"language": language}, "transcription": [{"tokens": tokens}]}


def test_tokens_become_words_timed_by_dtw_with_pauses_kept():
    words = tr.parse_whisper_cpp_json(doc([
        tok("[_BEG_]"), tok(" And", 424, 0, 0.5), tok(" so", 506), tok(" Americ", 628), tok("ans", 640), tok(",", 650),
        tok("[_TT_500]"), tok(" ask", 784), tok(" not", 1100),
    ]), duration=30)
    assert [w.text for w in words] == ["And", "so", "Americans,", "ask", "not"]
    assert [round(w.start, 2) for w in words] == [4.24, 5.06, 6.28, 7.84, 11.0]
    assert words[0].end == pytest.approx(4.715) and words[0].prob == 0.5  # held 0.475 s, then a short gap
    assert words[3].end < words[4].start  # 3 s between "ask" and "not": a real pause, not one long word
    assert words[-1].end <= 30


def test_offsets_are_used_when_there_is_no_dtw():
    words = tr.parse_whisper_cpp_json(doc([tok(" Hi", -1, 1500), tok(" there", -1, 1900)]))
    assert [(w.text, w.start) for w in words] == [("Hi", 1.5), ("there", 1.9)]


def test_noise_spans_and_symbols_are_dropped_even_across_words():
    words = tr.parse_whisper_cpp_json(doc([
        tok(" [SUSP", 0), tok("ENSEFUL", 10), tok(" MUSIC]", 50), tok(" ♪", 90), tok(" (upbeat", 100),
        tok(" music)", 120), tok(" Hello", 200), tok(" -", 230), tok(" world", 250),
    ]))
    assert [w.text for w in words] == ["Hello", "world"]


def test_unclosed_bracket_keeps_the_rest():
    words = tr.parse_whisper_cpp_json(doc([tok(" (and", 0)] + [tok(f" w{i}", 10 * (i + 1)) for i in range(8)]))
    assert words[0].text == "(and" and len(words) == 9


def test_stacked_and_backwards_times_are_repaired():
    words = tr.parse_whisper_cpp_json(doc([tok(" a", 100), tok(" b", 100), tok(" c", 100), tok(" d", 400),
                                           tok(" e", 350)]), duration=4.5)
    starts = [w.start for w in words]
    assert starts == sorted(starts) and starts[:3] == pytest.approx([1.0, 2.0, 3.0])
    assert all(w.end - w.start >= 0.3 for w in words[:3])  # each spread word gets a visible slot
    assert words[-1].end <= 4.5


def test_languages_without_spaces_get_one_word_per_character_and_split_utf8_is_joined():
    zhong = "中".encode()
    first, rest = zhong[:1].decode("utf-8", "surrogateescape"), zhong[1:].decode("utf-8", "surrogateescape")
    words = tr.parse_whisper_cpp_json(doc([tok("我们", 100), tok(first, 200), tok(rest, 210), tok("国", 300),
                                           tok("。", 310)], language="zh"))
    assert [w.text for w in words] == ["我们", "中", "国。"]


def test_dtw_preset_names():
    p = tr.Path
    assert tr.dtw_preset(p("ggml-base.bin")) == "base"
    assert tr.dtw_preset(p("ggml-base.en.bin")) == "base.en"
    assert tr.dtw_preset(p("ggml-large-v3-turbo-q5_0.bin")) == "large.v3.turbo"
    assert tr.dtw_preset(p("ggml-small-q8_0.bin")) == "small"
    assert tr.dtw_preset(p("my-model.bin")) is None


def test_engine_discovery_prefers_configured_model(tmp_path, monkeypatch):
    home = tmp_path / "home"
    binary = home / "whisper.cpp" / "build" / "bin" / "whisper-cli"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    models = home / "whisper.cpp" / "models"
    models.mkdir()
    monkeypatch.setattr(tr.Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("VV88_WHISPER_CPP", raising=False)
    monkeypatch.delenv("VV88_WHISPER_CPP_MODEL", raising=False)
    monkeypatch.setattr(tr.shutil, "which", lambda name: None)
    cfg = AnalysisConfig(whisper_model="base")
    assert tr.whisper_cpp_engine(cfg) is None  # binary but no model
    (models / "ggml-silero-v5.1.2.bin").write_bytes(b"x")  # a VAD model is never picked
    (models / "for-tests-ggml-tiny.bin").write_bytes(b"x")
    assert tr.whisper_cpp_engine(cfg) is None
    (models / "ggml-small.bin").write_bytes(b"x" * 10)
    (models / "ggml-tiny.bin").write_bytes(b"x" * 5)
    assert tr.whisper_cpp_engine(cfg)[1].name == "ggml-tiny.bin"  # no ggml-base: smallest real model
    (models / "ggml-base.bin").write_bytes(b"x" * 7)
    assert tr.whisper_cpp_engine(cfg) == (str(binary), models / "ggml-base.bin")  # the configured size wins
    monkeypatch.setenv("VV88_WHISPER_CPP_MODEL", str(tmp_path / "missing.bin"))
    assert tr.whisper_cpp_engine(cfg) is None  # an explicit model path must exist


def test_no_engine_error_names_both_options(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setattr(tr, "whisper_cpp_engine", lambda cfg: None)
    with pytest.raises(RuntimeError, match="faster-whisper.*whisper.cpp"):
        tr.transcribe(tmp_path / "x.mp4", AnalysisConfig())


needs_engine = pytest.mark.skipif(
    not (os.environ.get("VV88_WHISPER_CPP") and os.environ.get("VV88_WHISPER_CPP_MODEL") and shutil.which("ffmpeg")),
    reason="set VV88_WHISPER_CPP and VV88_WHISPER_CPP_MODEL")


@needs_engine
def test_real_run_on_video_without_sound_or_speech(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    mute = tmp_path / "mute.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", str(mute)],
                   check=True)
    assert tr.transcribe(mute, AnalysisConfig()).words == []
    tone = tmp_path / "tone.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-shortest", str(tone)], check=True)
    result = tr.transcribe(tone, AnalysisConfig(language="en"))
    assert result.duration == pytest.approx(2.0, abs=0.1)
    assert all(w.end <= result.duration + 1e-6 for w in result.words)
