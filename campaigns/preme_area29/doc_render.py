"""Render a clip in the cinematic 'Tate documentary' style of the reference reel.

Look (matched to the reference): full-frame picture, dark contrasty grade with cool
shadows, vignette and fine grain; small bold-italic uppercase phrase subtitles at the
bottom centre; a glowing neon title over the opening hook; fast cuts with a slow push-in
and alternating punch zoom; a black end card with tall condensed white type.

Usage: python doc_render.py clip.json {16x9|9x16}
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from tate_render import censor, ts  # noqa: E402
from track import track, x_expr  # noqa: E402

END = 2.2  # end-card length (s)


def chunks_from(words, keep, max_chars):
    ws, acc = [], 0.0
    for a, b in keep:
        for w in words:
            mid = (w["s"] + min(w["e"], w["s"] + 0.6)) / 2
            if a <= mid <= b:
                txt = censor(w["w"].strip()).upper()
                if txt:
                    ws.append({"w": txt, "s": acc + max(0.0, w["s"] - a),
                               "e": acc + min(b - a, min(w["e"], w["s"] + 1.2) - a), "seg": (a, b)})
        acc += b - a
    out, cur = [], []
    for w in ws:
        if cur and (
            len(" ".join(x["w"] for x in cur + [w])) > max_chars
            or w["s"] - cur[-1]["e"] > 0.35
            or cur[-1]["w"][-1:] in ".?!"
            or w["seg"] != cur[-1]["seg"]
        ):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def build_ass(cfg, words, keep, total, W, H, vertical, path):
    sub_size = 60 if vertical else 46
    sub_y = int(H * (0.735 if vertical else 0.895))
    title_size = 150 if vertical else 132
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Inter ExtraBold,{sub_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,0,-1,0,0,100,100,0.5,0,1,1.4,2.5,5,60,60,0,1
Style: Glow,Inter Display Black,{title_size},&H0040C8FF,&H00FFFFFF,&H0000A5FF,&H00000000,0,0,0,0,100,100,4,0,1,10,0,5,40,40,0,1
Style: Core,Inter Display Black,{title_size},&H00C8F4FF,&H00FFFFFF,&H0020B4FF,&H00000000,0,0,0,0,100,100,4,0,1,2,0,5,40,40,0,1
Style: End,Inter Display Black,{170 if vertical else 150},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,50,128,3,0,1,0,0,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    for ch in chunks_from(words, keep, 22 if vertical else 30):
        s, e = ch[0]["s"], max(ch[-1]["e"], ch[0]["s"] + 0.45)
        txt = " ".join(x["w"] for x in ch)
        ev.append([s, e, txt])
    for i in range(len(ev) - 1):  # no overlaps, and bridge tiny gaps so text doesn't flicker
        if ev[i][1] > ev[i + 1][0] or ev[i + 1][0] - ev[i][1] < 0.25:
            ev[i][1] = ev[i + 1][0]
    body = ""
    for s, e, txt in ev:
        if s >= total:
            continue
        body += f"Dialogue: 1,{ts(s)},{ts(min(e, total))},Sub,,0,0,0,,{{\\an5\\pos({W // 2},{sub_y})\\blur0.6\\fad(40,40)}}{txt}\n"

    # neon title over the hook: flicker on, hold, fade
    title = cfg.get("title", "PREME")
    ty = int(H * (0.30 if vertical else 0.38))
    t0, t1 = 0.25, 2.6
    flick = "\\alpha&HFF&\\t(0,60,\\alpha&H00&)\\t(110,150,\\alpha&HC0&)\\t(150,200,\\alpha&H00&)\\t(330,360,\\alpha&H90&)\\t(360,400,\\alpha&H00&)"
    body += f"Dialogue: 3,{ts(t0)},{ts(t1)},Glow,,0,0,0,,{{\\an5\\pos({W // 2},{ty})\\blur14{flick}\\fad(0,300)}}{title}\n"
    body += f"Dialogue: 4,{ts(t0)},{ts(t1)},Core,,0,0,0,,{{\\an5\\pos({W // 2},{ty})\\blur1.2{flick}\\fad(0,300)}}{title}\n"

    # end card
    l1, l2 = cfg.get("end", ["WATCH THE FULL STREAM", "KICK.COM/PREME"])
    gap = 112 if vertical else 92
    body += f"Dialogue: 5,{ts(total + 0.05)},{ts(total + END)},End,,0,0,0,,{{\\an5\\pos({W // 2},{H // 2 - gap})\\fad(150,0)}}{l1}\n"
    body += f"Dialogue: 5,{ts(total + 0.05)},{ts(total + END)},End,,0,0,0,,{{\\an5\\pos({W // 2},{H // 2 + gap})\\fad(150,0)}}{l2}\n"
    Path(path).write_text(hdr + body)


def zoom_expr(keep, total):
    terms, acc = [], 0.0
    for i, (a, b) in enumerate(keep):
        z = 1.0 if i % 2 == 0 else 1.08
        terms.append(f"between(t,{acc:.3f},{acc + b - a:.3f})*{z}")
        acc += b - a
    return f"({'+'.join(terms)})*(1+0.05*t/{total:.3f})"


def main():
    cfg = json.loads(Path(sys.argv[1]).read_text())
    fmt = sys.argv[2] if len(sys.argv) > 2 else "16x9"
    vertical = fmt == "9x16"
    W, H = (1080, 1920) if vertical else (1920, 1080)
    keep = cfg["keep"]
    total = sum(b - a for a, b in keep)
    words = json.loads(Path(cfg["words"]).read_text())
    for t0, t1, old, new in cfg.get("fix", []):
        for w in words:
            if t0 <= w["s"] <= t1 and re.sub(r"[^\w'-]", "", w["w"]).lower() == old.lower():
                w["w"] = new
    words = [w for w in words if w["w"].strip()]

    stem = Path(cfg["out"]).stem
    out = Path(cfg["out"]).parent / fmt / f"{stem}_{fmt}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    ass = out.with_suffix(".ass")
    build_ass(cfg, words, keep, total, W, H, vertical, ass)

    fc, n = [], len(keep)
    for i, (a, b) in enumerate(keep):
        fc.append(f"[{i}:v]trim=0:{b - a:.3f},setpts=PTS-STARTPTS,fps=30[v{i}]")
        fc.append(f"[{i}:a]atrim=0:{b - a:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.02,"
                  f"afade=t=out:st={b - a - 0.03:.3f}:d=0.03[a{i}]")
    fc.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")

    z = zoom_expr(keep, total)
    if vertical:
        cw = 608
        xe = x_expr(track(cfg["src"], keep, cw))
        pre = f"crop={cw}:1080:x='{xe}':y=0,"
    else:
        pre = ""
    grade = (
        "eq=contrast=1.12:brightness=0.03:saturation=0.85:gamma=1.32,"
        "colorbalance=rs=-0.05:gs=0.01:bs=0.06:rh=0.04:gh=0.01:bh=-0.03,"
        "curves=master='0/0.02 0.25/0.24 0.5/0.55 0.78/0.84 1/0.97',"
        "unsharp=5:5:0.7,vignette=PI/5,noise=alls=4:allf=t"
    )
    fc.append(
        f"[vc]delogo=x=46:y=22:w=302:h=182,{pre}"
        f"scale=w='trunc({W}*({z})/2)*2':h='trunc({H}*({z})/2)*2':eval=frame:flags=lanczos,"
        f"crop={W}:{H},{grade},setsar=1,format=yuv420p[vg]"
    )
    lw = 150 if vertical else 170
    fc.append(f"[{n}:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa=0.85[logo]")
    fc.append(f"[vg][logo]overlay={32 if not vertical else 36}:{28 if not vertical else 150}:shortest=1[vl]")
    fc.append(f"[vl]tpad=stop_mode=add:stop_duration={END}:color=black,ass='{ass}':fontsdir=/home/user/VV88/fonts[vout]")
    boom = "0.85*sin(2*PI*(36+70*exp(-9*t))*t)*exp(-3.0*t)"
    fc.append(f"aevalsrc='{boom}|{boom}':s=48000:d=1.4[b1]")
    fc.append(f"aevalsrc='{boom}|{boom}':s=48000:d=1.4,adelay={int(total * 1000)}|{int(total * 1000)}[b2]")
    fc.append(f"[ac]aresample=48000,aformat=channel_layouts=stereo,afade=t=out:st={total - 0.4:.3f}:d=0.4,apad=pad_dur={END}[a2]")
    fc.append("[a2][b1][b2]amix=inputs=3:duration=first:normalize=0,loudnorm=I=-14:TP=-1.0:LRA=9[aout]")

    inputs = []
    for a, b in keep:
        inputs += ["-ss", f"{a:.3f}", "-t", f"{b - a + 0.2:.3f}", "-i", cfg["src"]]
    inputs += ["-loop", "1", "-i", str(HERE / "logo_rgba.png")]
    cmd = ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(fc),
           "-map", "[vout]", "-map", "[aout]", "-t", f"{total + END:.3f}",
           "-c:v", "libx264", "-preset", cfg.get("preset", "medium"), "-crf", "18",
           "-profile:v", "high", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           "-max_muxing_queue_size", "4096", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)]
    subprocess.run(cmd, check=True)
    print("wrote", out)


if __name__ == "__main__":
    main()
