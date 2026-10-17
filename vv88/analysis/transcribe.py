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
import unicodedata
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
# Run with -ojf (per-token JSON) and, when the model has a DTW preset, -dtw: DTW aligns each token to the audio,
# so words keep their real position even across silent intros and pauses (whisper.cpp's own timestamp
# heuristic smears the first words over them). -sns suppresses non-speech tokens like faster-whisper does.
NO_SPACE_LANGUAGES = {"zh", "ja", "th", "lo", "my", "km", "bo", "yue"}  # one caption word per character/token
_DTW_PRESETS = {"tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
                "large.v1", "large.v2", "large.v3", "large.v3.turbo"}
_MIN_WORD, _MAX_WORD = 0.3, 1.2  # seconds a word is shown for when the next word is far away (a pause)


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


def dtw_preset(model: Path) -> str | None:
    """whisper.cpp's -dtw preset for a ggml model file (ggml-base.en.bin -> base.en, ggml-large-v3-q5_0.bin ->
    large.v3), or None when there is no matching preset."""
    m = re.fullmatch(r"ggml-(.+)\.bin", model.name)
    if not m:
        return None
    preset = re.sub(r"-q\d+(_\d+|_k)?$", "", m.group(1)).replace("-", ".")
    return preset if preset in _DTW_PRESETS else None


def _text(parts: list[str]) -> str:
    """Join token texts; whisper.cpp can split one UTF-8 character across tokens (kept as surrogate escapes)."""
    return "".join(parts).encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _complete(parts: list[str]) -> bool:
    try:
        "".join(parts).encode("utf-8", "surrogateescape").decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _is_punct(text: str) -> bool:
    return bool(text.strip()) and all(unicodedata.category(ch)[0] in "PZS" for ch in text.strip())


def parse_whisper_cpp_json(data: dict, duration: float | None = None) -> list[Word]:
    """Words from whisper-cli -ojf output: tokens grouped into words, timed by DTW (t_dtw) when present."""
    language = str((data.get("result") or {}).get("language") or "")
    no_space = language in NO_SPACE_LANGUAGES
    groups: list[tuple[list[str], float, list[float]]] = []
    for seg in data.get("transcription") or []:
        for tok in seg.get("tokens") or []:
            raw = str(tok.get("text") or "")
            if raw.startswith("[_"):  # [_BEG_], [_TT_123], [_SOT_]...: special tokens, not speech
                continue
            try:
                t_dtw = float(tok.get("t_dtw", -1))
                start = t_dtw / 100.0 if t_dtw >= 0 else float((tok.get("offsets") or {})["from"]) / 1000.0
            except (KeyError, TypeError, ValueError):
                continue
            prob = float(tok.get("p", 1.0) or 0.0)
            if groups:
                parts = groups[-1][0]
                if no_space:  # continue a split character, or attach punctuation; otherwise one word per token
                    joins = not _complete(parts) or (_complete([raw]) and _is_punct(raw))
                else:
                    joins = not raw.startswith(" ")
                if joins:
                    parts.append(raw)
                    groups[-1][2].append(prob)
                    continue
            groups.append(([raw], start, [prob]))
    raw_words = [(_text(parts).strip(), start, sum(ps) / len(ps)) for parts, start, ps in groups]
    return _finish_words(_drop_noise([w for w in raw_words if w[0]]), duration)


def _drop_noise(words: list[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    """Remove [MUSIC] / (upbeat music) spans (even split over several words), and ♪ or punctuation-only words."""
    out, i = [], 0
    while i < len(words):
        text = words[i][0]
        if text[:1] in "[(":
            close = next((j for j in range(i, min(i + 6, len(words))) if words[j][0][-1:] in ")]"), None)
            if close is not None:
                i = close + 1
                continue
        if any(ch.isalnum() for ch in text):
            out.append(words[i])
        i += 1
    return out


def _finish_words(raw: list[tuple[str, float, float]], duration: float | None) -> list[Word]:
    """Monotonic starts, stacked starts spread out, ends at the next word (or a short hold before a pause)."""
    limit = duration if duration and duration > 0 else float("inf")
    starts: list[float] = []
    for _, s, _ in raw:
        starts.append(min(max(s, starts[-1] if starts else 0.0), limit))
    i, n = 0, len(starts)
    while i < n:  # several words on one timestamp: share the time up to the next distinct word
        j = i + 1
        while j < n and starts[j] - starts[i] < 0.05:
            j += 1
        if j - i > 1:
            nxt = min(starts[j] if j < n else starts[i] + _MIN_WORD * (j - i), limit)
            step = max(0.0, nxt - starts[i]) / (j - i)
            for k in range(i, j):
                starts[k] = starts[i] + (k - i) * step
        i = j
    words = []
    for k, (text, _, prob) in enumerate(raw):
        hold = starts[k] + min(_MAX_WORD, max(_MIN_WORD, 0.25 + 0.075 * len(text)))
        end = min(starts[k + 1] if k + 1 < n else hold, hold, limit)
        words.append(Word(text=text, start=starts[k], end=max(end, starts[k]), prob=round(prob, 3)))
    return words


def transcribe_whisper_cpp(video_path: str | Path, cfg: AnalysisConfig, binary: str, model: Path) -> Transcript:
    preset = dtw_preset(model)
    log.info("transcribing with whisper.cpp (%s%s)", model.name, ", DTW timing" if preset else "")
    with tempfile.TemporaryDirectory(prefix="vv88-wcpp-") as tmp:
        wav = Path(tmp) / "audio.wav"
        decoded = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path),
                                  "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                                 capture_output=True, text=True)
        if decoded.returncode != 0 or not wav.is_file():
            if "does not contain any stream" in (decoded.stderr or ""):  # a video with no sound: nothing to say
                return Transcript(words=[], language=cfg.language or "en", duration=0.0)
            raise RuntimeError("couldn't read the audio: " + (decoded.stderr or "").strip()[-300:])
        duration = max(0.0, (wav.stat().st_size - 44) / (2 * 16000))
        if duration < 0.1:
            return Transcript(words=[], language=cfg.language or "en", duration=duration)
        out = Path(tmp) / "out"
        threads = max(1, min(8, os.cpu_count() or 4))
        cmd = [binary, "-m", str(model), "-f", str(wav), "-l", cfg.language or "auto", "-t", str(threads),
               "-ojf", "-of", str(out), "-np", "-sns"]
        if preset:
            cmd += ["-dtw", preset, "-nfa"]  # DTW needs flash attention off
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        result = out.with_suffix(".json")
        if proc.returncode != 0 or not result.is_file():
            tail = " | ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-3:])[:300]
            hint = (f" The model file {model} may be incomplete: delete it and run scripts/termux_setup.sh again."
                    if "failed to initialize" in tail or "failed to load" in tail else "")
            raise RuntimeError(f"whisper.cpp failed: {tail}.{hint}")
        data = json.loads(result.read_bytes().decode("utf-8", "surrogateescape"))
    language = str((data.get("result") or {}).get("language") or cfg.language or "en")
    return Transcript(words=parse_whisper_cpp_json(data, duration), language=language, duration=duration)
