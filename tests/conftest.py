import shutil
import subprocess
from pathlib import Path

import pytest

from vv88.models import Transcript, Word

SCRIPT = (
    "Why do most people never get rich? Because nobody tells you this one simple secret about money. "
    "I was broke at 25 and it changed everything. You have to stop trading time for cash. "
    "So um anyway that is basically it. And then we talked about other stuff for a while. "
    "What would you do with a million dollars? Most people would waste it in two years. "
    "The truth is the biggest mistake is spending before you invest."
)


def make_transcript(text: str = SCRIPT, start: float = 0.5, rate: float = 0.36, pause: float = 0.9) -> Transcript:
    words, t = [], start
    for w in text.split():
        words.append(Word(w, round(t, 3), round(t + rate * 0.8, 3)))
        t += rate
        if w.endswith((".", "?", "!")):
            t += pause
    return Transcript(words=words, duration=t + 1)


@pytest.fixture
def transcript() -> Transcript:
    return make_transcript()


def has_ffmpeg() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return " ass " in out


@pytest.fixture
def sample_video(tmp_path: Path) -> Path:
    if not has_ffmpeg():
        pytest.skip("ffmpeg with libass not available")
    path = tmp_path / "talk.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=70",
         "-f", "lavfi", "-i", "sine=f=200:d=70", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-shortest", str(path)],
        check=True,
    )
    return path
