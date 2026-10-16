"""Regression tests for the web review findings (worker, publish race, limits, sessions, redaction)."""

import importlib
import inspect
import json
import os
import re
import sqlite3
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


# ---- second review round ------------------------------------------------------------------
def test_known_browser_can_sign_in_during_a_global_lockout(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as owner, TestClient(app) as stranger:
        assert owner.post("/login", data={"password": "test-password"}, follow_redirects=False).status_code == 303
        assert owner.cookies.get("vv88_device")
        owner.cookies.delete("vv88_session")  # signed out, but the browser is remembered
        throttle = app.state.throttle
        throttle.all_fails.extend([time.time()] * throttle.global_limit)  # 50 failures from everywhere
        r = stranger.post("/login", data={"password": "test-password"}, follow_redirects=False)
        assert "Too many attempts" in r.text  # an unknown browser is still capped
        stranger.cookies.set("vv88_device", "forged.0123456789abcdef0123456789abcdef")
        assert "Too many attempts" in stranger.post("/login", data={"password": "test-password"}).text
        r = owner.post("/login", data={"password": "test-password"}, follow_redirects=False)
        assert r.status_code == 303  # the owner's own browser gets in


def test_empty_password_env_never_allows_an_empty_password(app, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("VV88_WEB_PASSWORD", "")
    with TestClient(app) as c:
        assert "Wrong password" in c.post("/login", data={"password": ""}).text
        assert c.post("/login", data={"password": "test-password"}, follow_redirects=False).status_code == 303


def test_campaign_paying_only_on_unsupported_platforms_gets_none(tmp_path):
    from vv88.config import RankingConfig
    from vv88.discovery.manual import ManualCampaignSource
    from vv88.discovery.ranker import rank_campaigns

    (tmp_path / "c.yaml").write_text("campaigns:\n"
                                     "  - {id: fb, title: FB only, reward_per_1k_usd: 2, platforms: [facebook]}\n"
                                     "  - {id: any, title: Anywhere, reward_per_1k_usd: 2}\n")
    by_id = {c.id: c for c in ManualCampaignSource(tmp_path / "c.yaml").fetch()}
    assert by_id["fb"].platforms_on(["tiktok", "youtube"]) == []
    assert by_id["any"].platforms_on(["tiktok", "youtube"]) == ["tiktok", "youtube"]
    ranked = rank_campaigns(list(by_id.values()), ["tiktok", "youtube"], {}, {}, RankingConfig())
    assert [r.campaign.id for r in ranked] == ["any"]


def test_publish_now_never_unclaims_an_uploading_post(client, csrf, demo_root):
    store = _store(demo_root)
    pid = store.due_posts(now=1e18)[0]["id"]
    assert store.claim_post(pid)  # the publish job is uploading it
    assert store.reschedule(pid, time.time()) is False
    r = client.post(f"/queue/{pid}/publish-now", data={"csrf": csrf("/queue")}, follow_redirects=False)
    assert r.headers["location"] == "/queue?status=scheduled"  # 'publishing' isn't a tab of its own
    assert store.post(pid)["status"] == "publishing"


def test_error_loading_a_clip_after_the_claim_does_not_strand_the_post(demo_root, monkeypatch):
    from vv88.pipeline import Engine

    engine = Engine(load_settings(demo_root / "config.yaml", env_path=demo_root / ".env"))
    pid = engine.store.due_posts(now=1e18)[0]["id"]

    def broken(clip_id):
        raise TypeError("unexpected keyword in stored clip")

    monkeypatch.setattr(engine.store, "clip", broken)
    engine.publish_due()
    assert engine.store.post(pid)["status"] == "failed"


def test_dashboard_counts_posts_that_are_uploading(demo_root, client):
    store = _store(demo_root)
    before = store.counts()["scheduled"]
    pid = store.due_posts(now=1e18)[0]["id"]
    assert store.claim_post(pid)
    assert store.counts()["scheduled"] == before
    assert any(p["id"] == pid for p in store.posts_list(status="scheduled"))


def test_absurd_content_length_is_a_413_not_a_500():
    import asyncio

    from vv88.web.core import BodyLimit

    sent = []

    async def app(scope, receive, send):  # never reached
        raise AssertionError

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "path": "/login", "method": "POST", "headers": [(b"content-length", b"0" * 5000 + b"5")]}
    asyncio.run(BodyLimit(app)(scope, receive, send))
    assert sent[0]["status"] == 413


def test_studio_upload_work_runs_off_the_event_loop():
    from vv88.web.pages import studio

    assert not inspect.iscoroutinefunction(studio._start_clip)


# ---- third review round -------------------------------------------------------------------
def _sign_in(c, **headers):
    return c.post("/login", data={"password": "test-password"}, headers=headers, follow_redirects=False)


def test_password_change_withdraws_device_trust(app, monkeypatch):
    from fastapi.testclient import TestClient

    with TestClient(app) as owner:
        assert _sign_in(owner).status_code == 303
        old = owner.cookies.get("vv88_device")
        monkeypatch.setenv("VV88_WEB_PASSWORD", "rotated-long-password-123")
        app.state.throttle.all_fails.extend([time.time()] * app.state.throttle.global_limit)
        owner.cookies.delete("vv88_session")
        r = owner.post("/login", data={"password": "rotated-long-password-123"}, follow_redirects=False)
        assert "Too many attempts" in r.text  # the old device token no longer exempts this browser
        app.state.throttle.all_fails.clear()
        r = owner.post("/login", data={"password": "rotated-long-password-123"}, follow_redirects=False)
        assert r.status_code == 303 and owner.cookies.get("vv88_device") != old  # re-issued for the new password


def test_cross_origin_sign_in_is_refused_without_using_up_attempts(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        for _ in range(8):  # another port on the same host posting wrong passwords with this browser's cookies
            r = c.post("/login", data={"password": "nope"}, headers={"Origin": "http://testserver:9999"})
            assert r.status_code == 403
        assert c.post("/login", data={"password": "nope"}, headers={"Origin": "null"}).status_code == 403
        assert not app.state.throttle.all_fails  # none of those counted
        assert _sign_in(c, Origin="http://testserver").status_code == 303  # same origin is fine
        c.cookies.delete("vv88_session")
        assert _sign_in(c, Referer="http://testserver/login").status_code == 303


def test_malformed_or_planted_device_cookies_do_not_break_sign_in(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as owner:
        assert _sign_in(owner).status_code == 303
        token = owner.cookies.get("vv88_device")
    app.state.throttle.all_fails.extend([time.time()] * app.state.throttle.global_limit)
    with TestClient(app) as c:
        r = _sign_in(c, Cookie='vv88_device="x.\\351"')
        assert r.status_code == 200 and "Too many attempts" in r.text  # not a 500; just an unknown browser
        # a planted second cookie with the same name can't hide the real one
        assert _sign_in(c, Cookie=f"vv88_device={token}; vv88_device=planted.0000").status_code == 303


def test_https_mode_uses_a_host_only_device_cookie(demo_root, brand_dir):
    from fastapi.testclient import TestClient

    from vv88.web.app import create_app

    app = create_app(demo_root / "config.yaml", env_path=demo_root / ".env", brand_dir=brand_dir, start_jobs=False,
                     https_cookies=True)
    with TestClient(app, base_url="https://testserver") as c:
        r = _sign_in(c)
        assert r.status_code == 303
        set_cookie = " ".join(r.headers.get_list("set-cookie"))
        assert "__Host-vv88_device=" in set_cookie and "Secure" in set_cookie and "Domain" not in set_cookie


def test_campaigns_stored_by_older_versions_are_rebuilt(demo_root):
    import yaml

    from vv88.models import Campaign
    from vv88.pipeline import Engine

    path = demo_root / "campaigns.yaml"
    doc = yaml.safe_load(path.read_text())
    items = doc["campaigns"] if isinstance(doc, dict) else doc
    items.append({"id": "fb-only", "title": "FB only", "reward_per_1k_usd": 5, "platforms": ["facebook"]})
    path.write_text(yaml.safe_dump(doc))
    store = _store(demo_root)
    legacy = Campaign(id="fb-only", title="FB only", reward_per_1k_usd=5).to_dict()
    del legacy["other_platforms"]  # how a pre-upgrade row looks
    store.db.execute("INSERT OR REPLACE INTO campaigns(id, data, updated_at, score) VALUES(?,?,?,?)",
                     ("fb-only", json.dumps(legacy), time.time(), 47.09))
    store.db.commit()
    store.kv_set("upgrade:other_platforms", None)
    store.kv_set("last_discovery", time.time())

    engine = Engine(load_settings(demo_root / "config.yaml", env_path=demo_root / ".env"))
    assert engine.platforms_for(engine.store.campaign("fb-only")) == []
    assert engine.store.kv_get("last_discovery") == 0  # next cycle rediscovers the rest
    engine.rank()
    assert engine.store.campaign_scores()["fb-only"] == 0  # its old score doesn't linger


def test_studio_and_clip_page_explain_campaigns_without_usable_platforms(client, demo_root):
    from vv88.models import Campaign

    store = _store(demo_root)
    store.upsert_campaigns([Campaign(id="fb-only", title="FB only", reward_per_1k_usd=5,
                                     other_platforms=["facebook"])])
    page = client.get("/studio?campaign=fb-only").text
    assert "None of this campaign" in page and "facebook" in page
    assert "facebook (not supported)" in client.get("/campaigns").text


# ---- fourth review round ------------------------------------------------------------------
def test_strict_referrer_policy_null_origin_still_signs_in(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        # Referrer-Policy: no-referrer makes browsers send Origin: null even to their own site
        r = _sign_in(c, Origin="null", **{"Sec-Fetch-Site": "same-origin"})
        assert r.status_code == 303
    with TestClient(app) as c:  # same-site (another port) and cross-site posts are still refused
        assert _sign_in(c, **{"Sec-Fetch-Site": "same-site"}).status_code == 403
        assert _sign_in(c, **{"Sec-Fetch-Site": "cross-site"}).status_code == 403


@pytest.mark.parametrize("headers", [{"Origin": "http://["}, {"Origin": "http://[::1"},
                                     {"Referer": "http://[x]/login"}, {"Origin": "http://testserver:99999"}])
def test_malformed_origin_headers_are_refused_not_500(app, headers):
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as c:
        assert _sign_in(c, **headers).status_code == 403


def test_origin_check_normalises_ports_dots_and_bad_base_urls(app, monkeypatch):
    from fastapi.testclient import TestClient

    with TestClient(app, base_url="http://127.0.0.1:8088") as c:  # a proxy that rewrites Host
        for base in ("https://vv88.example.com:443/", "https://VV88.example.com./"):
            monkeypatch.setenv("VV88_WEB_BASE_URL", base)
            c.cookies.clear()
            assert _sign_in(c, Origin="https://vv88.example.com").status_code == 303
        monkeypatch.setenv("VV88_WEB_BASE_URL", "http://[vv88")  # a typo must not break sign-in
        c.cookies.clear()
        assert _sign_in(c, Origin="http://127.0.0.1:8088").status_code == 303
    with TestClient(app, base_url="https://vv88.example.com:443") as c:  # Host: $host:$server_port
        monkeypatch.delenv("VV88_WEB_BASE_URL")
        assert _sign_in(c, Origin="https://vv88.example.com").status_code == 303


def test_campaign_upgrade_handles_non_finite_numbers_and_never_blocks_startup(demo_root, monkeypatch):
    from vv88.models import Campaign
    from vv88.pipeline import Engine

    store = _store(demo_root)
    legacy = Campaign(id="uncapped", title="Uncapped", reward_per_1k_usd=1, budget_remaining_usd=float("inf")).to_dict()
    del legacy["other_platforms"]
    store.db.execute("INSERT OR REPLACE INTO campaigns(id, data, updated_at) VALUES(?,?,?)",
                     ("uncapped", json.dumps(legacy), time.time()))
    store.db.commit()
    assert "uncapped" in store.campaigns_missing("other_platforms")  # Python json reads Infinity fine
    store.kv_set("upgrade:other_platforms", None)

    def boom(self, key):
        raise sqlite3.OperationalError("malformed JSON")

    monkeypatch.setattr(Store, "campaigns_missing", boom)
    Engine(load_settings(demo_root / "config.yaml", env_path=demo_root / ".env"))  # must not raise


def test_studio_hint_carries_per_campaign_platforms_and_warns(client, csrf, demo_root):
    from vv88.models import Campaign

    _store(demo_root).upsert_campaigns([Campaign(id="fb-only", title="FB only", reward_per_1k_usd=5,
                                                 other_platforms=["facebook"])])
    page = client.get("/studio").text
    assert 'data-platform-hint="#post-hint"' in page
    assert re.search(r'value="fb-only"[^>]*data-platforms=""[^>]*data-named="facebook"', page, re.S)
    assert re.search(r'value="money-talks"[^>]*data-platforms="tiktok youtube instagram x"', page, re.S)
    src = demo_root / "own.mp4"
    src.write_bytes(b"\0\0\0\x18ftypmp42" + b"\0" * 64)
    r = client.post("/studio/clip", data={"csrf": csrf("/studio"), "source": str(src), "campaign_id": "fb-only",
                                          "n": "1", "i_have_rights": "on", "post": "on"}, follow_redirects=True)
    assert "Nothing will be scheduled" in r.text


# ---- fifth review round -------------------------------------------------------------------
@pytest.mark.parametrize("nxt", ["//[", "//[::1", "//[x]", "//＃", "/\\evil.example", "https://evil.example"])
def test_login_next_parameter_never_500s_or_redirects_off_site(app, nxt):
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/login", params={"next": nxt}).status_code == 200
        r = c.post("/login", data={"password": "test-password", "next": nxt}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"


def test_store_never_writes_nan_or_infinity_and_repairs_old_rows(tmp_path):
    from vv88.models import Campaign

    store = Store(tmp_path / "vv88.db")
    store.upsert_campaigns([Campaign(id="uncapped", title="U", reward_per_1k_usd=float("inf"),
                                     budget_remaining_usd=float("inf"), total_budget_usd=float("nan"))])
    raw = store.db.execute("SELECT data FROM campaigns WHERE id='uncapped'").fetchone()["data"]
    assert "Infinity" not in raw and "NaN" not in raw
    c = store.campaign("uncapped")
    assert c.reward_per_1k_usd == 0.0 and c.budget_remaining_usd is None and c.total_budget_usd is None
    # a row written by an older version with bare Infinity gets rewritten once on open
    store.db.execute("INSERT INTO campaigns(id, data, updated_at) VALUES('old', ?, 0)",
                     ('{"id": "old", "title": "Old", "reward_per_1k_usd": 1.5, "budget_remaining_usd": Infinity}',))
    store.db.commit()
    store.kv_set("repair:nonfinite_json", None)
    store = Store(tmp_path / "vv88.db")
    raw = store.db.execute("SELECT data FROM campaigns WHERE id='old'").fetchone()["data"]
    assert "Infinity" not in raw and store.campaign("old").budget_remaining_usd is None
    assert json.loads(raw)["reward_per_1k_usd"] == 1.5


def test_studio_warning_names_the_real_cause_without_a_campaign(client, csrf, demo_root):
    cfg = demo_root / "config.yaml"
    text = cfg.read_text().replace("backend: dry_run", "backend: direct").replace("enabled: true", "enabled: false")
    cfg.write_text(text)
    src = demo_root / "own.mp4"
    src.write_bytes(b"\0\0\0\x18ftypmp42" + b"\0" * 64)
    r = client.post("/studio/clip", data={"csrf": csrf("/studio"), "source": str(src), "campaign_id": "", "n": "1",
                                          "i_have_rights": "on", "post": "on"}, follow_redirects=True)
    assert "no platforms are enabled" in r.text and "this campaign" not in r.text


# ---- sixth review round -------------------------------------------------------------------
def test_sign_in_returns_to_the_full_original_url(app):
    from urllib.parse import parse_qs, urlparse

    from fastapi.testclient import TestClient

    target = "/studio?campaign=money-talks&source=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Ddemo1%26t%3D5"
    with TestClient(app) as c:
        r = c.get(target, follow_redirects=False)
        assert r.status_code == 303
        login_url = r.headers["location"]
        assert parse_qs(urlparse(login_url).query)["next"] == [target]
        page = c.get(login_url).text
        assert re.search(r'name="next" value="([^"]*)"', page).group(1).replace("&amp;", "&") == target
        r = c.post("/login", data={"password": "test-password", "next": target}, follow_redirects=False)
        assert r.headers["location"] == target
        assert 'value="https://www.youtube.com/watch?v=demo1&amp;t=5"' in c.get(r.headers["location"]).text
