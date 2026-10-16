"""whisper.cpp fallback (phones / Termux): output parsing, engine discovery, and a real run when available."""

import os
import shutil
import stat
import subprocess

import pytest

from vv88.analysis import transcribe as tr
from vv88.config import AnalysisConfig


def test_parse_keeps_words_and_drops_noise_markers():
    data = {"result": {"language": "en"}, "transcription": [
        {"offsets": {"from": 320, "to": 330}, "text": " And"},
        {"offsets": {"from": 330, "to": 740}, "text": " so,"},
        {"offsets": {"from": 740, "to": 740}, "text": ""},
        {"offsets": {"from": 800, "to": 900}, "text": " [BLANK_AUDIO]"},
        {"offsets": {"from": 900, "to": 950}, "text": "(music)"},
        {"offsets": {"from": 950, "to": 940}, "text": " my"},
        {"text": " no-offsets"},
    ]}
    words = tr.parse_whisper_cpp_json(data)
    assert [(w.text, w.start, w.end) for w in words] == [("And", 0.32, 0.33), ("so,", 0.33, 0.74), ("my", 0.95, 0.95)]


def test_engine_discovery_prefers_env_then_home_build(tmp_path, monkeypatch):
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
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", None)
    monkeypatch.setattr(tr, "whisper_cpp_engine", lambda cfg: None)
    with pytest.raises(RuntimeError, match="faster-whisper.*whisper.cpp"):
        tr.transcribe(tmp_path / "x.mp4", AnalysisConfig())


@pytest.mark.skipif(not (os.environ.get("VV88_WHISPER_CPP") and os.environ.get("VV88_WHISPER_CPP_MODEL")
                         and shutil.which("ffmpeg")), reason="set VV88_WHISPER_CPP and VV88_WHISPER_CPP_MODEL")
def test_real_whisper_cpp_run(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", None)
    video = tmp_path / "tone.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-shortest", str(video)], check=True)
    result = tr.transcribe(video, AnalysisConfig(language="en"))
    assert result.duration == pytest.approx(2.0, abs=0.1)
