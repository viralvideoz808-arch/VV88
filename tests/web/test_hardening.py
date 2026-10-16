"""Regression tests for the web review findings (worker, publish race, limits, sessions, redaction)."""

import importlib
import inspect
import json
import os
import threading
import time

import pytest

from vv88.config import load_settings
from vv88.store import Store
from vv88.web.core import LoginThrottle
from vv88.web.jobs import JOB_KINDS, JobManager

TOKEN = "EAABsecretTOKEN1234567890abcdefXYZ"


def _store(demo_root):
    return Store(load_settings(demo_root / "config.yaml", env_path=demo_root / ".env").data_path / "vv88.db")


def _wait(pred, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


# ---- job worker ---------------------------------------------------------------------------
def test_worker_survives_a_broken_config_and_keeps_running_jobs(demo_root, monkeypatch):
    cfg = demo_root / "config.yaml"
    good = cfg.read_text()

    def corrupt(settings, params):
        cfg.write_text("publishing: [unclosed\n")
        return {"ok": True}

    monkeypatch.setitem(JOB_KINDS, "corrupt", ("Corrupt config", corrupt))
    monkeypatch.setitem(JOB_KINDS, "noop", ("No-op", lambda settings, params: {"ok": True}))
    jm = JobManager(cfg, demo_root / ".env")
    jm.start()
    try:
        store = _store(demo_root)
        first = jm.submit("corrupt")
        assert _wait(lambda: store.job(first)["status"] not in ("queued", "running")), "job stuck after config broke"
        assert store.job(first)["status"] == "done"
        cfg.write_text(good)
        second = jm.submit("noop")
        assert _wait(lambda: store.job(second)["status"] == "done"), "worker did not survive"
    finally:
        cfg.write_text(good)
        jm.stop()


def test_dead_worker_is_restarted_on_submit(demo_root, monkeypatch):
    monkeypatch.setitem(JOB_KINDS, "noop", ("No-op", lambda settings, params: {"ok": True}))
    jm = JobManager(demo_root / "config.yaml", demo_root / ".env")
    jm.start()
    try:
        jm._worker_thread = threading.Thread(target=lambda: None)  # simulate a worker that has exited
        jm._worker_thread.start()
        jm._worker_thread.join()
        job = jm.submit("noop")
        store = _store(demo_root)
        assert _wait(lambda: store.job(job)["status"] == "done")
    finally:
        jm.stop()


def test_job_results_are_redacted_when_stored_and_shown(demo_root, monkeypatch, client):
    leaky = {"publish_errors": [{"error": f"GET https://graph.facebook.com/x?fields=a&access_token={TOKEN} failed"}]}
    monkeypatch.setitem(JOB_KINDS, "leaky", ("Leaky", lambda settings, params: leaky))
    jm = JobManager(demo_root / "config.yaml", demo_root / ".env")
    store = _store(demo_root)
    job = store.add_job("leaky", {})
    jm._run(job)
    assert TOKEN not in (store.job(job)["result"] or "")
    # rows written before this fix are redacted on display too
    old = store.add_job("leaky", {})
    store.update_job(old, status="done", result=leaky)
    assert TOKEN not in client.get(f"/jobs/{old}").text


# ---- publishing race ----------------------------------------------------------------------
def test_post_cancelled_while_publish_job_runs_is_not_published(demo_root, monkeypatch):
    from vv88.models import PostResult
    from vv88.pipeline import Engine

    settings = load_settings(demo_root / "config.yaml", env_path=demo_root / ".env")
    engine = Engine(settings)
    due = engine.store.due_posts(now=1e18)
    assert len(due) >= 2
    victim = due[1]["id"]
    published = []

    class Fake:
        def publish(self, path, content):
            if not published:  # while the first upload is "in progress", the owner cancels another post
                assert _store(demo_root).cancel_post(victim)
            published.append(path)
            return PostResult("tiktok", True, remote_id=f"fake:{len(published)}", status="posted")

    monkeypatch.setattr("vv88.pipeline.make_publisher", lambda *a, **k: Fake())
    engine.publish_due()
    row = engine.store.post(victim)
    assert row["status"] == "skipped"
    assert len(published) == len(due) - 1


def test_claim_is_exclusive_and_stale_claims_are_released(demo_root):
    store = _store(demo_root)
    pid = store.due_posts(now=1e18)[0]["id"]
    assert store.claim_post(pid) is True
    assert store.claim_post(pid) is False  # a second publisher (CLI + web) can't take it
    assert store.cancel_post(pid) is False  # too late to cancel
    assert store.release_stale_claims() == 0  # fresh claim stays
    store.db.execute("UPDATE posts SET posted_at=? WHERE id=?", (time.time() - 3 * 3600, pid))
    assert store.release_stale_claims() == 1
    assert store.post(pid)["status"] == "failed"


def test_publish_errors_are_redacted(demo_root, monkeypatch):
    from vv88.pipeline import Engine

    class Boom:
        def publish(self, path, content):
            raise OSError(f"connection refused: https://graph.facebook.com/v1?access_token={TOKEN}")

    monkeypatch.setattr("vv88.pipeline.make_publisher", lambda *a, **k: Boom())
    engine = Engine(load_settings(demo_root / "config.yaml", env_path=demo_root / ".env"))
    results = engine.publish_due()
    assert results and all(TOKEN not in json.dumps(r) for r in results)
    rows = engine.store.db.execute("SELECT error FROM posts WHERE status='failed'").fetchall()
    assert rows and all(TOKEN not in (r["error"] or "") for r in rows)


# ---- campaign platforms on the clip page --------------------------------------------------
def test_clip_page_only_offers_platforms_the_campaign_pays_for(client, csrf, demo_root):
    store = _store(demo_root)
    clip_id = store.db.execute("SELECT id FROM clips WHERE campaign_id='streamer-highlights' LIMIT 1").fetchone()["id"]
    page = client.get(f"/clips/{clip_id}").text
    assert 'name="platforms" value="instagram"' not in page
    assert 'name="platforms" value="x"' not in page
    before = len(store.jobs(limit=500))
    r = client.post(f"/clips/{clip_id}/schedule", data={"csrf": csrf(f"/clips/{clip_id}"), "platforms": "instagram"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/clips/{clip_id}"
    assert len(store.jobs(limit=500)) == before


# ---- config -------------------------------------------------------------------------------
def test_empty_postiz_ids_in_yaml_fall_back_to_env(tmp_path):
    (tmp_path / "config.yaml").write_text('publishing:\n  postiz:\n    integrations: {tiktok: "", youtube: "yt9"}\n')
    (tmp_path / ".env").write_text("POSTIZ_INTEGRATION_TIKTOK=abc123\n")
    saved = dict(os.environ)
    try:
        s = load_settings(tmp_path / "config.yaml", env_path=tmp_path / ".env")
        assert s.publishing.postiz.integrations["tiktok"] == "abc123"
        assert s.publishing.postiz.integrations["youtube"] == "yt9"
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ---- sign-in ------------------------------------------------------------------------------
def test_wrong_password_does_not_block_the_event_loop(app, monkeypatch):
    from fastapi.testclient import TestClient

    def no_blocking_sleep(_):
        raise AssertionError("time.sleep in an async handler stalls every request")

    monkeypatch.setattr("vv88.web.app.time.sleep", no_blocking_sleep)
    with TestClient(app) as c:
        r = c.post("/login", data={"password": "nope", "next": "/"})
    assert "Wrong password" in r.text


def test_global_login_limit_and_bounded_memory():
    t = LoginThrottle(limit=5, window_s=600, global_limit=50, max_keys=100)
    for i in range(50):  # one guess each from 50 different (or spoofed) addresses
        assert not t.blocked(f"10.0.0.{i}")
        t.fail(f"10.0.0.{i}")
    assert t.blocked("203.0.113.9")  # a fresh address is refused too
    t2 = LoginThrottle(max_keys=100, global_limit=10**9)
    for i in range(1000):
        t2.fail(f"k{i}")
    assert len(t2.fails) <= 100


def test_password_change_signs_out_existing_sessions(client, monkeypatch):
    assert client.get("/", follow_redirects=False).status_code == 200
    monkeypatch.setenv("VV88_WEB_PASSWORD", "a-brand-new-long-password")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


# ---- request body limits ------------------------------------------------------------------
def test_anonymous_large_bodies_are_refused(app):
    from fastapi.testclient import TestClient

    big = b"x" * (2 * 1024 * 1024)
    with TestClient(app) as c:
        assert c.post("/login", content=big, headers={"content-type": "application/x-www-form-urlencoded"}
                      ).status_code == 413
        r = c.post("/logout", files={"f": ("a.bin", big)})
        assert r.status_code == 413

        def chunks():  # no Content-Length: a multipart body the form parser would spool, counted while streaming
            yield b'--zz\r\nContent-Disposition: form-data; name="f"; filename="a.bin"\r\n\r\n'
            for _ in range(4):
                yield b"y" * (512 * 1024)
            yield b"\r\n--zz--\r\n"

        assert c.post("/logout", content=chunks(), headers={"content-type": "multipart/form-data; boundary=zz"}
                      ).status_code == 413
        # the upload route is exempt from the 1 MB cap but still demands a login before reading anything
        r = c.post("/studio/clip", content=big, headers={"content-type": "application/octet-stream"},
                   follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_login_form_rejects_file_parts(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        r = c.post("/login", data={"password": "x"}, files={"f": ("a.bin", b"123")})
    assert r.status_code == 400


# ---- odd inputs ---------------------------------------------------------------------------
def test_autopilot_interval_is_clamped_and_dashboard_survives(client, csrf, app):
    for raw, want in (("inf", 30.0), ("1e999", 30.0), ("nan", 30.0), ("5", 10.0), ("999999", 10080.0)):
        client.post("/autopilot", data={"csrf": csrf("/"), "interval_min": raw})
        assert app.state.jobs.autopilot()["interval_min"] == want
        assert client.get("/").status_code == 200
    app.state.jobs.store().kv_set("web:autopilot", {"enabled": True, "interval_min": float("inf"), "last_run": "x"})
    assert client.get("/").status_code == 200


@pytest.mark.parametrize("method,path", [
    ("post", "/queue/%C2%B2/cancel"), ("post", "/queue/%C2%B2/publish-now"), ("post", "/queue/%C2%B2/retry"),
    ("get", "/performance?days=%C2%B2"), ("get", "/jobs/99999999999999999999"),
    ("get", "/jobs/99999999999999999999/log.json"), ("post", "/jobs/99999999999999999999/cancel"),
])
def test_odd_ids_never_500(client, csrf, method, path):
    data = {"csrf": csrf("/queue")} if method == "post" else None
    r = getattr(client, method)(path, **({"data": data} if data else {}), follow_redirects=False)
    assert r.status_code < 500


def test_form_handlers_run_off_the_event_loop():
    """Blocking work (key checks over the network, subprocess checks, DB/file writes) must not run on the loop."""
    allowed_async = {"/studio/clip"}  # offloads its file copy with run_in_threadpool
    found = 0
    for name in ("brand", "campaigns", "clips", "connections", "dashboard", "queue", "settings", "jobs"):
        module = importlib.import_module(f"vv88.web.pages.{name}")
        for route in module.router.routes:
            if "POST" in getattr(route, "methods", set()) and route.path not in allowed_async:
                found += 1
                assert not inspect.iscoroutinefunction(route.endpoint), f"{route.path} is async def"
    assert found >= 15


def test_malformed_multipart_is_a_400_not_a_500(app):
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/logout", content=b"\0" * 100, headers={"content-type": "multipart/form-data; boundary=zz"})
        assert r.status_code == 400
        r = c.post("/login", content=b"\0" * 100, headers={"content-type": "multipart/form-data; boundary=zz"})
        assert r.status_code == 400
