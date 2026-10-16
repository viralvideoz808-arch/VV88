"""Thin wrappers around the ffmpeg / ffprobe binaries."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise FFmpegError(f"{tool} not found on PATH; install FFmpeg (free, open source)")


@dataclass
class ProbeInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def probe(path: str | Path) -> ProbeInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in data["streams"])
    if not video:
        raise FFmpegError(f"no video stream in {path}")
    num, _, den = (video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    width, height = int(video["width"]), int(video["height"])
    # Respect rotation metadata from phone footage.
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    if abs(rotation) in (90, 270):
        width, height = height, width
    duration = float(data["format"].get("duration") or video.get("duration") or 0)
    return ProbeInfo(duration=duration, width=width, height=height, fps=fps or 30.0, has_audio=has_audio)


def run_ffmpeg(args: list[str], desc: str = "ffmpeg") -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("%s: %s", desc, " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"{desc} failed ({proc.returncode}): {proc.stderr[-2000:]}")


def audio_rms_db(path: str | Path, start: float = 0.0, duration: float | None = None, hop: float = 0.25,
                 sample_rate: int = 8000) -> np.ndarray:
    """Loudness envelope in dBFS, one value per `hop` seconds."""
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}"]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    args += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"]
    raw = subprocess.run(args, capture_output=True, check=True).stdout
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    n = int(sample_rate * hop)
    if samples.size < n:
        return np.full(1, -90.0, dtype=np.float32)
    frames = samples[: samples.size // n * n].reshape(-1, n)
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    return (20 * np.log10(rms)).astype(np.float32)


def scene_cuts(path: str | Path, threshold: float = 0.3) -> list[float]:
    """Timestamps of hard cuts (downscaled analysis keeps this fast on long videos)."""
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an",
            "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    cuts = []
    for line in proc.stderr.splitlines():
        if "showinfo" in line and "pts_time:" in line:
            try:
                cuts.append(float(line.split("pts_time:")[1].split()[0]))
            except (IndexError, ValueError):
                continue
    return cuts


def extract_frame(path: str | Path, t: float, out: str | Path, width: int = 540) -> Path:
    run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf", f"scale={width}:-2", str(out)],
               desc="extract_frame")
    return Path(out)
