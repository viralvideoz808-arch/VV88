# Preme AREA 29: Tate-style clip pack

Six vertical clips cut from https://kick.com/preme/videos/01a18590-5880-7162-a96c-dd934dfc6689 for the Content Rewards "Preme Clipping Campaign".

- `tate_render.py`: first-pass look, before the reference reel was available. It uses a black 9:16 canvas, a bold headline with a yellow keyword, and a cinematic graded panel. Captions pop word by word with censoring. Jump-cut punch-ins hide the edits, alongside a slow push-in and an impact hit. It adds a progress bar and normalizes audio to −14 LUFS.
- `doc_render.py`: **the final look, matched to the Andrew Tate reference reel.** Full-frame picture, dark cinematic grade, small bold-italic uppercase phrase subtitles, neon title over the hook, fast cuts with push-ins, and a condensed end card. Run `python doc_render.py configs/king.json 16x9` (or `9x16` for full-screen vertical with face tracking).
- `track.py`: face-aware horizontal crop tracking (OpenCV Haar, smoothed).
- `configs/*.json`: cut lists, headline, highlight words and transcript fixes per clip.
- `POST_COPY.md`: titles, captions and hashtags.

Render: `python tate_render.py configs/king.json`. Paths inside the configs are relative to the working dir: `hi/<clip>.mp4` (1080p60 source sections from yt-dlp `--download-sections`) and `work/<clip>.words.json` (faster-whisper medium.en word timestamps).
