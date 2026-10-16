"""Speech-to-text with word timestamps, run locally (free, no per-minute fees)."""

from __future__ import annotations

import json
import logging
import subprocess
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
    except ImportError as e:  # pragma: no cover - optional dependency
        raise RuntimeError("pip install 'vv88[transcribe]' for local Whisper transcription") from e

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
