"""Background jobs for the web UI: one worker thread (rendering is CPU-bound) + an autopilot timer.

Every job loads settings fresh, so edits made on the Settings page apply to the next job.
Logs from the worker thread are captured per job (redacted) and shown live on the Jobs page.
"""

from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from vv88.config import Settings, load_settings
from vv88.publishing.base import redact
from vv88.store import Store

log = logging.getLogger(__name__)

AUTOPILOT_KEY = "web:autopilot"
PUBLISH_EVERY_S = 300
AUTOPILOT_MIN, AUTOPILOT_MAX, AUTOPILOT_DEFAULT = 10.0, 7 * 24 * 60.0, 30.0


def autopilot_interval(value) -> float:
    """Minutes between autopilot cycles, clamped to 10 min - 1 week (junk or non-finite -> 30)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return AUTOPILOT_DEFAULT
    if not math.isfinite(v):
        return AUTOPILOT_DEFAULT
    return min(max(v, AUTOPILOT_MIN), AUTOPILOT_MAX)


def redact_obj(obj):
    """redact() every string inside a job result (publish errors can quote URLs with access tokens)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


# ---- job implementations ----------------------------------------------------------------
def _engine(settings: Settings):
    from vv88.pipeline import Engine

    return Engine(settings)


def job_run_cycle(settings: Settings, params: dict) -> dict:
    return _engine(settings).run_cycle()


def job_discover(settings: Settings, params: dict) -> dict:
    engine = _engine(settings)
    campaigns = engine.discover()
    ranked = engine.rank()
    return {"campaigns": len(campaigns), "ranked": len(ranked),
            "top": [{"id": r.campaign.id, "title": r.campaign.title,
                     "usd_per_clip": round(r.expected_usd_per_clip, 2)} for r in ranked[:5]]}


def job_rank(settings: Settings, params: dict) -> dict:
    ranked = _engine(settings).rank()
    return {"ranked": len(ranked)}


def job_clip(settings: Settings, params: dict) -> dict:
    from vv88.sourcing import fetch_source

    engine = _engine(settings)
    campaign = engine.store.campaign(params["campaign_id"]) if params.get("campaign_id") else None
    if params.get("i_have_rights"):
        settings.rights_allowlist.append(params["source"])
    src = fetch_source(params["source"], campaign, settings, engine.store)
    clips = engine.produce_clips(src, campaign, n=params.get("n") or None, style=params.get("style") or None)
    scheduled = 0
    if params.get("post"):
        scheduled = engine.schedule(clips, campaign, engine.platforms_for(campaign))
    return {"clips": [{"id": c.id, "style": c.style, "duration": round(c.duration, 1), "hook": c.hook_text}
                      for c in clips], "scheduled": scheduled}


def job_schedule_clip(settings: Settings, params: dict) -> dict:
    engine = _engine(settings)
    clip = engine.store.clip(params["clip_id"])
    if clip is None:
        raise ValueError("clip not found")
    campaign = engine.store.campaign(clip.campaign_id)
    allowed = engine.platforms_for(campaign)  # never post a campaign's clip where the campaign doesn't pay
    platforms = [p for p in params.get("platforms") or allowed if p in allowed]
    return {"scheduled": engine.schedule([clip], campaign, platforms)}


def job_publish_due(settings: Settings, params: dict) -> dict:
    results = _engine(settings).publish_due()
    return {"published": sum(1 for r in results if r["ok"]), "results": results}


def job_metrics(settings: Settings, params: dict) -> dict:
    return {"updated": _engine(settings).collect_metrics()}


def job_brand_apply(settings: Settings, params: dict) -> dict:
    from vv88 import brand as b

    store = Store(settings.data_path / "vv88.db")
    kit = b.load_kit(params.get("dir") or "brand")
    token, scopes = None, None
    if settings.publishing.direct.youtube_refresh_token:
        from vv88.publishing.youtube import YouTubePublisher

        yt = YouTubePublisher(settings.publishing.direct)
        try:
            token, scopes = yt._access_token(), yt.scopes
        except Exception as e:  # noqa: BLE001 - YouTube trouble must not block the checklist
            log.warning("YouTube sign-in failed, skipping the automatic update: %s", e)
    return b.apply(kit, store, youtube_access_token=token, youtube_scopes=scopes)


def job_benchmark(settings: Settings, params: dict) -> dict:
    import tempfile

    from vv88.editing.render import RenderJob, render_clip
    from vv88.editing.styles import get_style
    from vv88.media import probe, run_ffmpeg
    from vv88.models import Transcript, Word

    secs = float(params.get("seconds") or 20)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.mp4"
        run_ffmpeg(["-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30:d={secs + 4}", "-f", "lavfi", "-i",
                    f"sine=f=220:d={secs + 4}", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", str(src)], desc="benchmark source")
        words, t = [], 0.3
        while t < secs + 2:
            words.append(Word("benchmark", t, t + 0.28))
            t += 0.36 if len(words) % 12 else 1.0
        job = RenderJob(str(src), probe(src), Transcript(words=words), 1.0, 1.0 + secs, get_style("karaoke_pop"),
                        "How fast is this machine?", Path(tmp) / "out.mp4")
        t0 = time.time()
        out = render_clip(job, settings.editing, Path(tmp) / "work")
        elapsed = time.time() - t0
    return {"clip_seconds": round(out.duration, 1), "render_seconds": round(elapsed, 1),
            "clips_per_day": int(86400 / elapsed)}


JOB_KINDS: dict[str, tuple[str, Callable[[Settings, dict], dict]]] = {
    "run_cycle": ("Full cycle (discover → clip → schedule → publish → metrics)", job_run_cycle),
    "discover": ("Discover + rank campaigns", job_discover),
    "rank": ("Re-rank campaigns", job_rank),
    "clip": ("Clip a source", job_clip),
    "schedule_clip": ("Schedule a clip", job_schedule_clip),
    "publish_due": ("Publish posts whose slot has arrived", job_publish_due),
    "metrics": ("Pull view counts", job_metrics),
    "brand_apply": ("Apply approved brand kit", job_brand_apply),
    "benchmark": ("Benchmark rendering speed", job_benchmark),
}


# ---- runner -----------------------------------------------------------------------------
class _JobLogHandler(logging.Handler):
    def __init__(self, manager: "JobManager"):
        super().__init__(level=logging.INFO)
        self.manager = manager
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.manager.worker_ident or self.manager.current is None:
            return
        try:
            line = redact(self.format(record))
        except Exception:  # noqa: BLE001
            return
        buf = self.manager.live.setdefault(self.manager.current, [])
        buf.append(line)
        if len(buf) > 2000:
            del buf[:500]


class JobManager:
    def __init__(self, config_path: str | Path | None, env_path: str | Path | None):
        self.config_path = config_path
        self.env_path = env_path
        self.q: queue.Queue[int] = queue.Queue()
        self.current: int | None = None
        self.live: dict[int, list[str]] = {}
        self.worker_ident: int | None = None
        self._stop = threading.Event()
        self._handler = _JobLogHandler(self)
        self._last_publish = 0.0
        self._db_path: Path | None = None
        self._started = False
        self._worker_thread: threading.Thread | None = None
        self._worker_lock = threading.Lock()

    # settings / store are loaded per use so the Settings page takes effect immediately
    def settings(self) -> Settings:
        return load_settings(self.config_path, env_path=self.env_path)

    def store(self) -> Store:
        try:
            path = self.settings().data_path / "vv88.db"
        except Exception:
            # config.yaml is mid-edit or broken: keep recording job state in the last good database
            if self._db_path is None:
                raise
            path = self._db_path
        else:
            self._db_path = path
        return Store(path)

    def start(self) -> None:
        root = logging.getLogger()
        root.addHandler(self._handler)
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)  # job logs show INFO progress lines
        for noisy in ("httpx", "httpcore", "urllib3", "huggingface_hub", "faster_whisper"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        store = self.store()
        for row in store.jobs(limit=500, status="running"):
            store.update_job(row["id"], status="failed", finished_at=time.time(),
                             log=(row["log"] or "") + "\ninterrupted: the server restarted")
        for row in reversed(store.jobs(limit=500, status="queued")):
            self.q.put(row["id"])
        self._started = True
        self._ensure_worker()
        threading.Thread(target=self._autopilot, name="vv88-autopilot", daemon=True).start()

    def _ensure_worker(self) -> None:
        """Start the worker thread, or a new one if it ever died, so queued jobs can't sit forever."""
        with self._worker_lock:
            if not self._started or self._stop.is_set():
                return
            if self._worker_thread is None or not self._worker_thread.is_alive():
                if self._worker_thread is not None:
                    log.error("job worker had stopped; starting a new one")
                self._worker_thread = threading.Thread(target=self._worker, name="vv88-worker", daemon=True)
                self._worker_thread.start()

    def stop(self) -> None:
        self._stop.set()
        logging.getLogger().removeHandler(self._handler)

    def submit(self, kind: str, params: dict | None = None) -> int:
        if kind not in JOB_KINDS:
            raise ValueError(f"unknown job {kind!r}")
        job_id = self.store().add_job(kind, params or {})
        self.q.put(job_id)
        self._ensure_worker()
        return job_id

    def busy_with(self, kind: str) -> bool:
        store = self.store()
        return any(r["kind"] == kind for r in store.jobs(limit=50, status="queued")) or any(
            r["kind"] == kind for r in store.jobs(limit=5, status="running"))

    def log_text(self, row) -> str:
        if row["status"] == "running" and row["id"] in self.live:
            return "\n".join(self.live[row["id"]])
        return row["log"] or ""

    def _worker(self) -> None:
        self.worker_ident = threading.get_ident()
        while not self._stop.is_set():
            try:
                job_id = self.q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._run(job_id)
            except Exception:  # noqa: BLE001 - nothing may end this loop (e.g. config.yaml mid-edit)
                log.exception("job #%s could not be run", job_id)
                self._fail_stuck(job_id)
            finally:
                self.current = None

    def _run(self, job_id: int) -> None:
        store = self.store()  # job state is written through this one connection, even if config breaks meanwhile
        row = store.job(job_id)
        if row is None or row["status"] != "queued":
            return
        self.current = job_id
        self.live[job_id] = []
        store.update_job(job_id, status="running", started_at=time.time())
        status, result = "done", None
        try:
            _, fn = JOB_KINDS[row["kind"]]
            result = fn(self.settings(), json.loads(row["params"]))
        except Exception as e:  # noqa: BLE001 - a failed job must not kill the worker
            status = "failed"
            result = {"error": redact(str(e))[:1000]}
            self.live[job_id].append(redact(traceback.format_exc())[-4000:])
        finally:
            self.current = None
            store.update_job(job_id, status=status, finished_at=time.time(), result=redact_obj(result),
                             log="\n".join(self.live.pop(job_id, []))[-200_000:])

    def _fail_stuck(self, job_id: int) -> None:
        try:
            store = self.store()
            row = store.job(job_id)
            if row is not None and row["status"] in ("queued", "running"):
                store.update_job(job_id, status="failed", finished_at=time.time(),
                                 log=(row["log"] or "") + "\ncould not run: see the server log",
                                 result={"error": "the job could not start (is config.yaml valid?)"})
        except Exception:  # noqa: BLE001
            log.exception("could not mark job #%s failed", job_id)
        self.live.pop(job_id, None)

    def autopilot(self) -> dict:
        cfg = self.store().kv_get(AUTOPILOT_KEY)
        cfg = cfg if isinstance(cfg, dict) else {}
        last = cfg.get("last_run")
        return {"enabled": bool(cfg.get("enabled")), "interval_min": autopilot_interval(cfg.get("interval_min", 30)),
                "last_run": float(last) if isinstance(last, (int, float)) and math.isfinite(last) else 0.0}

    def set_autopilot(self, enabled: bool, interval_min) -> dict:
        cfg = {**self.autopilot(), "enabled": bool(enabled), "interval_min": autopilot_interval(interval_min)}
        self.store().kv_set(AUTOPILOT_KEY, cfg)
        return cfg

    def _autopilot(self) -> None:
        while not self._stop.wait(30):
            try:
                self._ensure_worker()
                cfg = self.autopilot()
                if not cfg.get("enabled"):
                    continue
                now = time.time()
                if now - float(cfg.get("last_run") or 0) >= float(cfg.get("interval_min", 30)) * 60 \
                        and not self.busy_with("run_cycle"):
                    self.submit("run_cycle", {"source": "autopilot"})
                    self.store().kv_set(AUTOPILOT_KEY, {**cfg, "last_run": now})
                elif now - self._last_publish >= PUBLISH_EVERY_S and not self.busy_with("publish_due"):
                    self._last_publish = now
                    if self.store().due_posts():
                        self.submit("publish_due", {"source": "autopilot"})
            except Exception:  # noqa: BLE001 - keep the timer alive
                log.exception("autopilot tick failed")
