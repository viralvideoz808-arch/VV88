"""Fetch campaign source footage.

Rights guardrail: by default only URLs that a campaign itself lists as source material are
downloaded. Clipping creators who have not opted in gets channels struck and campaigns
rejected, so anything else requires an explicit allowlist entry (e.g. your own channel).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests

from vv88.config import Settings
from vv88.media import probe
from vv88.models import Campaign, SourceVideo, stable_id
from vv88.store import Store

log = logging.getLogger(__name__)


class RightsError(PermissionError):
    pass


def check_rights(url: str, campaign: Campaign | None, settings: Settings) -> None:
    if Path(url).exists():
        return  # local files are the operator's responsibility
    if campaign and url in campaign.source_urls:
        return
    host = urlparse(url).netloc.lower()
    for allowed in settings.rights_allowlist:
        if allowed.lower() in url.lower() or allowed.lower() == host:
            return
    raise RightsError(
        f"{url} is not listed as source material by the campaign and is not in rights_allowlist. "
        "Only clip content you have permission to use."
    )


def fetch_source(url: str, campaign: Campaign | None, settings: Settings, store: Store) -> SourceVideo:
    check_rights(url, campaign, settings)
    existing = store.source_by_url(url)
    if existing and Path(existing.path).exists():
        return existing

    campaign_id = campaign.id if campaign else "adhoc"
    sid = stable_id(url)
    out_dir = settings.data_path / "sources" / _safe(campaign_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    title = ""
    if Path(url).exists():
        path = out_dir / f"{sid}{Path(url).suffix or '.mp4'}"
        if Path(url).resolve() != path.resolve():
            shutil.copy2(url, path)
            sidecar = Path(url + ".transcript.json")
            if sidecar.is_file():
                shutil.copy2(sidecar, str(path) + ".transcript.json")
        title = Path(url).stem
    elif _is_direct_media(url):
        path = out_dir / f"{sid}{Path(urlparse(url).path).suffix}"
        _http_download(url, path)
    else:
        path, title = _ytdlp_download(url, out_dir, sid)

    info = probe(path)
    src = SourceVideo(
        id=sid,
        campaign_id=campaign_id,
        url=url,
        path=str(path),
        duration=info.duration,
        width=info.width,
        height=info.height,
        fps=info.fps,
        title=title,
    )
    store.add_source(src)
    return src


def _safe(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in s)


def _is_direct_media(url: str) -> bool:
    return urlparse(url).path.lower().endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv"))


def _http_download(url: str, path: Path) -> None:
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        tmp = path.with_suffix(path.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        tmp.rename(path)


def _ytdlp_download(url: str, out_dir: Path, sid: str) -> tuple[Path, str]:
    try:
        import yt_dlp
    except ImportError as e:  # pragma: no cover - optional dependency
        raise RuntimeError("pip install 'vv88[download]' to fetch from video sites") from e
    opts = {
        "outtmpl": str(out_dir / f"{sid}.%(ext)s"),
        # 1080p is plenty for a 1080x1920 crop; avoids multi-GB 4K downloads.
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if info.get("entries"):
            # Drive folder / playlist: take the first video. List individual video links in
            # campaigns.yaml to have every file in a folder clipped.
            first = next((e for e in info["entries"] if e), None)
            if not first:
                raise RuntimeError(f"no videos found at {url}")
            url = first.get("webpage_url") or first.get("url")
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
        if not path.exists():
            candidates = sorted(out_dir.glob(f"{sid}.*"))
            if not candidates:
                raise RuntimeError(f"yt-dlp produced no file for {url}")
            path = candidates[0]
        return path, info.get("title", "")
