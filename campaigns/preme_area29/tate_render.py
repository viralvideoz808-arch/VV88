"""Render a 'Tate-page' style vertical clip.

Usage: python tate_render.py clip.json

clip.json keys:
  src        hi-res source segment (mp4)
  words      word-level transcript json (list of {w,s,e}) in src time
  keep       [[start,end], ...] ranges of src to keep (jump cuts)
  headline   headline text; wrap words in *stars* to colour them yellow
  crop_w     width of the source crop (height = full 1080). default 1440
  center_x   horizontal centre of the crop as a fraction (0..1). default .5
  punch      list of output-time seconds where a hard punch-in should hit
  out        output path
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path

W, H = 1080, 1920
FONT_DIR = "/home/user/VV88/fonts"
FONT = "Inter Black"
YELLOW = "&H0000D7FF"  # ASS BGR -> #FFD700
WHITE = "&H00FFFFFF"
BLACK = "&H00000000"

CENSOR = {
    r"\bn[i1]gg?[ae]s?\b": lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1),
    r"f+u+c+k": lambda m: "f*ck",
    r"\bsh[i1]t": lambda m: "sh*t",
    r"\bpuss(y|ies)": lambda m: "p*ss" + m.group(1),
    r"\bdick": lambda m: "d*ck",
    r"\bbitch": lambda m: "b*tch",
}


def censor(word: str) -> str:
    for pat, fn in CENSOR.items():
        word = re.sub(pat, fn, word, flags=re.I)
    return word


def ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def remap(t: float, keep: list[list[float]]) -> float | None:
    """Source time -> output time after jump cuts (None if cut out)."""
    acc = 0.0
    for a, b in keep:
        if a <= t <= b:
            return acc + (t - a)
        acc += b - a
    return None


def headline_ass(text: str, video_top: int) -> str:
    # *word* -> yellow
    parts = re.split(r"(\*[^*]+\*)", text.upper())
    out = ""
    for p in parts:
        if p.startswith("*"):
            out += "{\\c" + YELLOW + "}" + p.strip("*") + "{\\c" + WHITE + "}"
        else:
            out += p
    y = video_top - 40
    # Slam-in: starts at 130% scale, settles to 100% in 0.25 s
    anim = "{\\an2\\pos(540," + str(y) + ")\\fscx130\\fscy130\\t(0,250,\\fscx100\\fscy100)}"
    return f"Dialogue: 2,{ts(0)},{ts(9999)},Head,,0,0,0,,{anim}{out}\n"


def captions_ass(words, keep, cap_y: int, highlight: set[str]) -> str:
    # remap & censor
    ws = []
    acc = 0.0
    for a, b in keep:  # per range, so a source moment reused (teaser) is captioned both times
        for w in words:
            mid = (w["s"] + min(w["e"], w["s"] + 0.6)) / 2
            if not (a <= mid <= b):
                continue
            s = acc + max(0.0, w["s"] - a)
            e = acc + min(b - a, min(w["e"], w["s"] + 1.2) - a)
            txt = censor(w["w"].strip()).upper()
            if txt:
                ws.append({"w": txt, "s": s, "e": max(e, s + 0.08)})
        acc += b - a
    # chunks must not span a jump cut boundary visually; fine for fast cuts
    # chunk: up to 3 words / 16 chars, break on pauses and punctuation
    chunks, cur = [], []
    for w in ws:
        if cur and (
            len(cur) >= 3
            or len(" ".join(x["w"] for x in cur + [w])) > 16
            or w["s"] - cur[-1]["e"] > 0.45
            or cur[-1]["w"][-1:] in ".?!,"
        ):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    lines = ""
    for ci, ch in enumerate(chunks):
        end_chunk = ch[-1]["e"]
        if ci + 1 < len(chunks):
            end_chunk = min(max(end_chunk, chunks[ci + 1][0]["s"]), chunks[ci + 1][0]["s"])
        for i, w in enumerate(ch):
            s = w["s"]
            e = ch[i + 1]["s"] if i + 1 < len(ch) else end_chunk
            if e <= s:
                continue
            txt = ""
            for j, x in enumerate(ch):
                clean = re.sub(r"[^A-Z0-9*']", "", x["w"])
                word = x["w"].rstrip(",.")
                if j == i:
                    col = YELLOW if True else WHITE
                    txt += "{\\c" + col + "\\fscx112\\fscy112}" + word + "{\\c" + WHITE + "\\fscx100\\fscy100}"
                elif clean in highlight:
                    txt += "{\\c" + YELLOW + "}" + word + "{\\c" + WHITE + "}"
                else:
                    txt += word
                if j + 1 < len(ch):
                    txt += " "
            pop = "{\\an5\\pos(540," + str(cap_y) + ")"
            if i == 0:  # chunk entrance pop
                pop += "\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)"
            pop += "}"
            lines += f"Dialogue: 1,{ts(s)},{ts(e)},Cap,,0,0,0,,{pop}{txt}\n"
    return lines


def build_ass(cfg, words, keep, video_top, video_h, path):
    cap_y = video_top + video_h + 150 if cfg.get("caption_below", True) else video_top + video_h - 120
    hl = {w.upper() for w in cfg.get("highlight", [])}
    head_size = cfg.get("headline_size", 82)
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Head,{FONT},{head_size},{WHITE},{WHITE},{BLACK},&H80000000,-1,0,0,0,100,100,0,0,1,4,3,2,70,70,0,1
Style: Cap,{FONT},{cfg.get("caption_size", 94)},{WHITE},{WHITE},{BLACK},&H90000000,-1,0,0,0,100,100,0,0,1,7,4,5,60,60,0,1
Style: Tag,{FONT},34,&H00D0D0D0,{WHITE},{BLACK},&H00000000,-1,0,0,0,100,100,2,0,1,2,0,2,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = headline_ass(cfg["headline"], video_top)
    body += captions_ass(words, keep, cap_y, hl)
    tag = cfg.get("tag", "KICK.COM/PREME")
    body += f"Dialogue: 0,{ts(0)},{ts(9999)},Tag,,0,0,0,,{{\\an2\\pos(540,{H-120})}}{tag}\n"
    Path(path).write_text(hdr + body)


def zoom_expr(keep, punch, total):
    """Piecewise zoom: alternate 1.00 / 1.10 on every jump cut, 1.18 punches,
    slow 4 % push-in across the clip, and a 0.3 s settle at the very start."""
    terms = []
    acc = 0.0
    for i, (a, b) in enumerate(keep):
        d = b - a
        z = 1.0 if i % 2 == 0 else 1.10
        terms.append(f"between(t,{acc:.3f},{acc + d:.3f})*{z}")
        acc += d
    base = "(" + "+".join(terms) + ")"
    push = f"(1+0.04*t/{total:.3f})"
    intro = "(1+0.22*max(0,1-t/0.3))"
    pz = "+".join(f"0.16*between(t,{p:.3f},{p + 0.9:.3f})" for p in punch) or "0"
    return f"{base}*{push}*{intro}+({pz})"


def main():
    cfg = json.loads(Path(sys.argv[1]).read_text())
    keep = cfg["keep"]
    total = sum(b - a for a, b in keep)
    words = json.loads(Path(cfg["words"]).read_text())
    # transcript corrections: [t0, t1, old, new]; new == "" drops the word
    for t0, t1, old, new in cfg.get("fix", []):
        for w in words:
            if t0 <= w["s"] <= t1 and re.sub(r"[^\w'-]", "", w["w"]).lower() == old.lower():
                w["w"] = new
    words = [w for w in words if w["w"].strip()]
    crop_w = cfg.get("crop_w", 1260)
    cx = cfg.get("center_x", 0.5)
    vid_w = W
    vid_h = int(round(1080 * W / crop_w / 2) * 2)
    video_top = cfg.get("video_top", 540)
    ass = Path(cfg["out"]).with_suffix(".ass")
    build_ass(cfg, words, keep, video_top, vid_h, ass)

    # concat jump-cut segments
    fc = []
    for i, (a, b) in enumerate(keep):
        fc.append(f"[{i}:v]trim=0:{b - a:.3f},setpts=PTS-STARTPTS,fps=30[v{i}]")
        fc.append(f"[{i}:a]atrim=0:{b - a:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.02,afade=t=out:st={b - a - 0.03:.3f}:d=0.03[a{i}]")
    n = len(keep)
    fc.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")

    z = zoom_expr(keep, cfg.get("punch", []), total)
    sys.path.insert(0, str(Path(__file__).parent))
    from track import track, x_expr
    if cfg.get("track", True):
        xe = x_expr(track(cfg["src"], keep, crop_w))
    else:
        xe = str(max(0, min(1920 - crop_w, int(cx * 1920 - crop_w / 2))))
    grade = cfg.get(
        "grade",
        "eq=contrast=1.12:brightness=0.02:saturation=1.15:gamma=1.16,"
        "unsharp=5:5:0.6,vignette=PI/5",
    )
    fc.append(
        f"[vc]delogo=x=46:y=22:w=302:h=182,crop={crop_w}:1080:x='{xe}':y=0,"
        f"scale=w='trunc({vid_w}*({z})/2)*2':h='trunc({vid_h}*({z})/2)*2':eval=frame:flags=lanczos,"
        f"crop={vid_w}:{vid_h},{grade},noise=alls=3:allf=t,setsar=1[vz]"
    )
    fc.append(f"color=c=black:s={W}x{H}:r=30:d={total:.3f}[bg]")
    fc.append(f"[{n}:v]scale=210:-1,format=rgba,colorchannelmixer=aa=0.92[logo]")
    fc.append(f"[bg][vz]overlay=0:{video_top}:shortest=1[comp0]")
    fc.append(f"[comp0][logo]overlay=22:{video_top + 18}:shortest=1[comp]")
    # thin accent lines framing the video
    fc.append(
        f"[comp]drawbox=x=0:y={video_top - 4}:w={W}:h=4:color=0xFFD700@0.9:t=fill,"
        f"drawbox=x=0:y={video_top + vid_h}:w={W}:h=4:color=0xFFD700@0.9:t=fill,"
        f"ass='{ass}':fontsdir={FONT_DIR},"
        f"drawbox=x=0:y={H - 14}:w='{W}*t/{total:.3f}':h=14:color=0xFFD700:t=fill[vout]"
    )
    # impact hit on the hook + loudness
    fc.append(
        "aevalsrc='0.9*sin(2*PI*(38+70*exp(-9*t))*t)*exp(-3.2*t)|0.9*sin(2*PI*(38+70*exp(-9*t))*t)*exp(-3.2*t)'"
        ":s=48000:d=1.2[boom]"
    )
    fc.append("[ac]aresample=48000,aformat=channel_layouts=stereo[a2]")
    fc.append("[a2][boom]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.0:LRA=9[aout]")

    inputs = []
    for a, b in keep:
        inputs += ["-ss", f"{a:.3f}", "-t", f"{b - a + 0.2:.3f}", "-i", cfg["src"]]
    inputs += ["-loop", "1", "-i", str(Path(__file__).parent / "logo_rgba.png")]
    cmd = [
        "ffmpeg", "-v", "error", "-stats", "-y", *inputs,
        "-filter_complex", ";".join(fc),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", cfg.get("preset", "slow"), "-crf", str(cfg.get("crf", 18)), "-max_muxing_queue_size", "4096",
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        cfg["out"],
    ]
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
