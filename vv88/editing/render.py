"""Render one finished vertical clip with a single FFmpeg pass.

Pipeline (all free, local, no watermark, no daily cap):
  seek -> 30 fps -> 9:16 reframe (face-tracked / blur fill / split-screen) -> jump cuts
  -> alternating punch-in zoom on cuts -> word-by-word captions + hook -> progress bar
  -> loudness normalised to -14 LUFS -> H.264/AAC, faststart MP4 (accepted by all four platforms)
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path

from vv88.config import EditingConfig
from vv88.editing.captions import build_ass, remap_words
from vv88.editing.reframe import CropPlan, detect_face_track, plan_crop
from vv88.editing.styles import Style
from vv88.editing.timeline import Timeline, keep_segments
from vv88.media import ProbeInfo, run_ffmpeg
from vv88.models import Transcript

log = logging.getLogger(__name__)

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm")


@dataclass
class RenderJob:
    source_path: str
    source_info: ProbeInfo
    transcript: Transcript
    start: float
    end: float
    style: Style
    hook_text: str
    out_path: Path


@dataclass
class RenderOutput:
    path: Path
    duration: float
    crop_mode: str
    segments: int


def _filter_path(p: Path) -> str:
    # Escape for use inside an FFmpeg filter argument.
    return str(p.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _zoom_windows(timeline: Timeline, sentence_starts: list[float], min_gap: float = 2.5) -> list[tuple[float, float]]:
    """Alternate between 100% and punched-in framing at jump cuts / sentence starts (hides the cut)."""
    marks = sorted(set([round(b, 3) for b in timeline.boundaries()] + [round(s, 3) for s in sentence_starts]))
    toggles, last = [], 0.0
    for m in marks:
        if m - last >= min_gap and m < timeline.duration - 1.0:
            toggles.append(m)
            last = m
    windows = []
    for i in range(0, len(toggles), 2):
        a = toggles[i]
        b = toggles[i + 1] if i + 1 < len(toggles) else timeline.duration
        windows.append((a, b))
    return windows


def _pick_gameplay(cfg: EditingConfig) -> Path | None:
    if not cfg.gameplay_dir:
        return None
    files = [p for p in Path(cfg.gameplay_dir).glob("*") if p.suffix.lower() in VIDEO_EXTS]
    return random.choice(files) if files else None


def _reframe_chain(plan: CropPlan, out_w: int, out_h: int) -> str:
    if plan.mode == "native":
        return (f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={out_w}:{out_h},setsar=1")
    if plan.mode == "blur":
        return (
            f"split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={out_w // 4}:{out_h // 4}:force_original_aspect_ratio=increase,"
            f"crop={out_w // 4}:{out_h // 4},boxblur=luma_radius=12:luma_power=2,"
            f"scale={out_w}:{out_h},setsar=1[bg];"
            f"[fgsrc]scale={out_w}:-2:flags=lanczos,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
    return (f"crop=w={plan.crop_w}:h={plan.crop_h}:x='{plan.x_expr()}':y=0,"
            f"scale={out_w}:{out_h}:flags=lanczos,setsar=1")


def render_clip(job: RenderJob, cfg: EditingConfig, work_dir: Path) -> RenderOutput:
    work_dir.mkdir(parents=True, exist_ok=True)
    job.out_path.parent.mkdir(parents=True, exist_ok=True)
    style = job.style
    W, H, fps = cfg.width, cfg.height, cfg.fps
    window = job.end - job.start

    words = job.transcript.words_between(job.start, job.end)
    segments = keep_segments(words, job.start, job.end, max_gap=cfg.jump_cut_gap)
    timeline = Timeline(segments)
    duration = timeline.duration

    # Reframe the speaker panel (full frame, or the top half for split-screen).
    gameplay = _pick_gameplay(cfg) if style.split_screen else None
    split = style.split_screen and gameplay is not None
    if style.split_screen and not split:
        log.warning("brainrot_stack needs editing.gameplay_dir with video loops; rendering full-frame")
    panel_h = H // 2 if split else H
    aspect = W / panel_h
    info = job.source_info
    track = []
    if cfg.reframe in ("auto", "face") and info.width / info.height > aspect * 1.05:
        track = detect_face_track(job.source_path, job.start, job.end, face_model=cfg.face_model)
    plan = plan_crop(info.width, info.height, track, window, aspect=aspect, mode=cfg.reframe)

    # Captions on the edited timeline.
    ass_path = work_dir / f"{job.out_path.stem}.ass"
    ass_path.write_text(
        build_ass(
            remap_words(words, timeline),
            style,
            font=cfg.font,
            width=W,
            height=H,
            duration=duration,
            hook_text=job.hook_text,
            hook_seconds=cfg.hook_seconds,
        ),
        encoding="utf-8",
    )

    inputs = ["-ss", f"{job.start:.3f}", "-t", f"{window + 0.3:.3f}", "-i", job.source_path]
    audio_in = "[0:a]"
    next_input = 1
    if not info.has_audio:
        inputs += ["-f", "lavfi", "-t", f"{window + 0.3:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        audio_in = f"[{next_input}:a]"
        next_input += 1
    game_idx = None
    if split:
        offset = random.uniform(0, 30)
        inputs += ["-ss", f"{offset:.2f}", "-stream_loop", "-1", "-i", str(gameplay)]
        game_idx = next_input
        next_input += 1

    rel = timeline.relative(job.start)
    k = len(rel)
    fc: list[str] = [f"[0:v]fps={fps},{_reframe_chain(plan, W, panel_h)}[vr]"]
    if k == 1:
        a, b = rel[0]
        fc.append(f"[vr]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[vc]")
        fc.append(f"{audio_in}atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[ac]")
    else:
        fc.append(f"[vr]split={k}" + "".join(f"[vs{i}]" for i in range(k)))
        fc.append(f"{audio_in}asplit={k}" + "".join(f"[as{i}]" for i in range(k)))
        pairs = []
        for i, (a, b) in enumerate(rel):
            fc.append(f"[vs{i}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[vt{i}]")
            fc.append(f"[as{i}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[at{i}]")
            pairs.append(f"[vt{i}][at{i}]")
        fc.append("".join(pairs) + f"concat=n={k}:v=1:a=1[vc][ac]")

    current = "[vc]"
    if split:
        fc.append(f"[{game_idx}:v]fps={fps},scale={W}:{H - panel_h}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H - panel_h},setsar=1[gp]")
        fc.append(f"{current}[gp]vstack=inputs=2:shortest=1[vst]")
        current = "[vst]"

    if style.punch_zoom and cfg.punch_zoom > 0 and not split:
        sentence_starts = [timeline.map(w.start) for i, w in enumerate(words)
                           if i == 0 or words[i - 1].text.rstrip().endswith((".", "?", "!"))]
        windows = _zoom_windows(timeline, sentence_starts)
        if windows:
            on = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in windows)
            z = cfg.punch_zoom
            fc.append(
                f"{current}scale=w='trunc(iw*(1+{z}*({on}))/2)*2':h='trunc(ih*(1+{z}*({on}))/2)*2':"
                f"eval=frame:flags=bicubic,crop={W}:{H}[vz]"
            )
            current = "[vz]"

    fonts = f":fontsdir='{_filter_path(Path(cfg.fonts_dir))}'" if cfg.fonts_dir else ""
    fc.append(f"{current}ass=filename='{_filter_path(ass_path)}'{fonts}[vsub]")
    current = "[vsub]"

    if cfg.progress_bar:
        bar_h = max(6, H // 192)
        fc.append(f"color=c=white@0.85:s={W}x{bar_h}:r={fps},format=rgba[bar]")
        fc.append(f"{current}[bar]overlay=x='-w+W*t/{max(duration, 0.1):.3f}':y=H-h:shortest=1,format=yuv420p[vout]")
    else:
        fc.append(f"{current}format=yuv420p[vout]")

    fc.append(f"[ac]loudnorm=I={cfg.loudness_lufs}:TP=-1.5:LRA=11,aresample=48000[aout]")

    args = [
        *inputs,
        "-filter_complex", ";".join(fc),
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps * 2),
        "-maxrate", "12M", "-bufsize", "24M",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
    ]
    if cfg.threads:
        args += ["-threads", str(cfg.threads)]
    args.append(str(job.out_path))
    run_ffmpeg(args, desc=f"render {job.out_path.name}")
    return RenderOutput(path=job.out_path, duration=duration, crop_mode=plan.mode, segments=k)
