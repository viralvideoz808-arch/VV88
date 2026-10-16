"""Speech-to-text with word timestamps, run locally (free, no per-minute fees).

Two engines, tried in order:
* faster-whisper (pip install 'vv88[transcribe]'), the default on PCs and servers.
* whisper.cpp's `whisper-cli` binary, for phones (Termux on Android) where faster-whisper can't be
  installed. Found via VV88_WHISPER_CPP, the PATH, or ~/whisper.cpp/build/bin/whisper-cli; the ggml model
  via VV88_WHISPER_CPP_MODEL or ~/whisper.cpp/models/ggml-<analysis.whisper_model>.bin.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from vv88.config import AnalysisConfig
from vv88.models import Transcript, Word

log = logging.getLogger(__name__)

_MODEL_CACHE: dict[tuple, object] = {}


def transcript_cache_path(data_dir: Path, source_id: str) -> Path:
    p = data_dir / "transcripts"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{source_id}.json"


def load_or_transcribe(video_path: str | Path, source_id: str, data_dir: Path, cfg: AnalysisConfig) -> Transcript:
    # A sidecar next to the video (e.g. creator-provided captions converted to JSON) wins.
    sidecar = Path(str(video_path) + ".transcript.json")
    if sidecar.is_file():
        return Transcript.from_dict(json.loads(sidecar.read_text()))
    cache = transcript_cache_path(data_dir, source_id)
    if cache.is_file():
        return Transcript.from_dict(json.loads(cache.read_text()))
    transcript = transcribe(video_path, cfg)
    cache.write_text(json.dumps(transcript.to_dict()))
    return transcript


def decode_audio(video_path: str | Path, sample_rate: int = 16000):
    """16 kHz mono float32 via the ffmpeg binary (avoids PyAV version breakage inside faster-whisper)."""
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video_path), "-vn", "-ac", "1",
         "-ar", str(sample_rate), "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(video_path: str | Path, cfg: AnalysisConfig) -> Transcript:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        engine = whisper_cpp_engine(cfg)
        if engine:
            return transcribe_whisper_cpp(video_path, cfg, *engine)
        raise RuntimeError(
            "No transcription engine. Install faster-whisper (pip install 'vv88[transcribe]'), or on a phone build "
            "whisper.cpp (scripts/termux_setup.sh does it) so `whisper-cli` and a ggml model are available."
        ) from e

    key = (cfg.whisper_model, cfg.whisper_device, cfg.whisper_compute_type)
    model = _MODEL_CACHE.get(key)
    if model is None:
        log.info("loading whisper model %s (%s/%s)", *key)
        model = WhisperModel(cfg.whisper_model, device=cfg.whisper_device, compute_type=cfg.whisper_compute_type)
        _MODEL_CACHE[key] = model

    segments, info = model.transcribe(
        decode_audio(video_path),
        language=cfg.language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
    )
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append(Word(text=text, start=float(w.start), end=float(w.end), prob=float(w.probability)))
    return Transcript(words=words, language=info.language, duration=float(info.duration))


# ---- whisper.cpp (phones / Termux) ----------------------------------------------------------
_NOISE = re.compile(r"^\s*[\[(].*[\])]\s*$")  # [BLANK_AUDIO], (music), [Applause] ...


def whisper_cpp_engine(cfg: AnalysisConfig) -> tuple[str, Path] | None:
    """(whisper-cli path, ggml model path) if both are available, else None."""
    home_build = Path.home() / "whisper.cpp"
    binary = None
    for c in (os.environ.get("VV88_WHISPER_CPP"), shutil.which("whisper-cli"),
              str(home_build / "build" / "bin" / "whisper-cli")):
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            binary = c
            break
    if not binary:
        return None
    env_model = os.environ.get("VV88_WHISPER_CPP_MODEL")
    if env_model:
        return (binary, Path(env_model)) if Path(env_model).is_file() else None
    dirs = [home_build / "models", Path(binary).resolve().parents[2] / "models"]
    for d in dirs:
        for name in (f"ggml-{cfg.whisper_model}.bin", f"ggml-{cfg.whisper_model}.en.bin"):
            if (d / name).is_file():
                return binary, d / name
    for d in dirs:  # any other downloaded model, smallest first (phones are slow)
        found = sorted((p for p in d.glob("ggml-*.bin") if not re.search(r"silero|vad", p.name)),
                       key=lambda p: p.stat().st_size) if d.is_dir() else []
        if found:
            return binary, found[0]
    return None


def parse_whisper_cpp_json(data: dict) -> list[Word]:
    """Words from whisper-cli -oj output run with -ml 1 -sow (one word per segment, offsets in ms)."""
    words: list[Word] = []
    for seg in data.get("transcription") or []:
        text = str(seg.get("text") or "").strip()
        if not text or _NOISE.match(text):
            continue
        off = seg.get("offsets") or {}
        try:
            start, end = float(off["from"]) / 1000.0, float(off["to"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            continue
        words.append(Word(text=text, start=start, end=max(end, start)))
    return words


def transcribe_whisper_cpp(video_path: str | Path, cfg: AnalysisConfig, binary: str, model: Path) -> Transcript:
    log.info("transcribing with whisper.cpp (%s)", model.name)
    with tempfile.TemporaryDirectory(prefix="vv88-wcpp-") as tmp:
        wav = Path(tmp) / "audio.wav"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path), "-vn",
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], check=True, capture_output=True)
        out = Path(tmp) / "out"
        threads = max(1, min(8, os.cpu_count() or 4))
        cmd = [binary, "-m", str(model), "-f", str(wav), "-l", cfg.language or "auto", "-t", str(threads),
               "-ml", "1", "-sow", "-oj", "-of", str(out), "-np"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        result = out.with_suffix(".json")
        if proc.returncode != 0 or not result.is_file():
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            raise RuntimeError("whisper.cpp failed: " + " | ".join(tail)[:400])
        data = json.loads(result.read_text(encoding="utf-8", errors="replace"))
        duration = max(0.0, (wav.stat().st_size - 44) / (2 * 16000))
    language = str((data.get("result") or {}).get("language") or cfg.language or "en")
    return Transcript(words=parse_whisper_cpp_json(data), language=language, duration=duration)
