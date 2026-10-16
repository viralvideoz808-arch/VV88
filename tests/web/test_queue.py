import json
import re
import time

from vv88.store import Store


def _store(demo_root):
    return Store(demo_root / "data" / "vv88.db")


def _flash(text):
    return " ".join(re.findall(r'class="flash [a-z]+" role="status">([^<]*)<', text))


def _post_id(demo_root, status, platform=None):
    sql, args = "SELECT id FROM posts WHERE status=?", [status]
    if platform:
        sql += " AND platform=?"
        args.append(platform)
    return _store(demo_root).db.execute(sql + " ORDER BY id LIMIT 1", args).fetchone()["id"]


def _jobs(demo_root):
    return _store(demo_root).jobs(limit=50)


# ---- page -------------------------------------------------------------------------------
def test_queue_renders_scheduled_by_default(client):
    r = client.get("/queue")
    assert r.status_code == 200
    t = r.text
    assert 'action="/queue/publish-due"' in t and "Publish due now" in t
    assert 'aria-current="page">Scheduled' in t
    # KPI counts: 8 scheduled, 15 posted, 1 failed, 0 skipped in the demo seed
    assert re.search(r"Scheduled</div><div class=\"kpi-value\">8<", t)
    assert re.search(r"Posted</div><div class=\"kpi-value\">15<", t)
    assert re.search(r"Failed</div><div class=\"kpi-value\">1<", t)
    assert t.count("/publish-now") == 8 and t.count("/cancel") == 8
    assert 'href="/clips/demo04aaaaaaaaaa"' in t and "The clutch nobody saw coming" in t
    assert "<details>" in t and "#moneytalks #fyp" in t      # caption preview
    assert "in 1h" in t or "in 2h" in t                      # fmt_ago on upcoming posts


def test_queue_tabs(client):
    posted = client.get("/queue", params={"status": "posted"}).text
    assert "Open post" in posted and 'href="https://example.com/tiktok/demo0"' in posted
    assert "28,800" in posted and "/publish-now" not in posted
    failed = client.get("/queue", params={"status": "failed"}).text
    assert "x media finalize failed: HTTP 403" in failed and "/retry" in failed
    skipped = client.get("/queue", params={"status": "skipped"}).text
    assert "No cancelled posts" in skipped
    everything = client.get("/queue", params={"status": "all"}).text
    assert everything.count('<td class="c-plat">') == 24


def test_queue_unknown_status_falls_back_with_warning(client):
    r = client.get("/queue", params={"status": "'; DROP TABLE posts;--"})
    assert r.status_code == 200 and "Unknown filter" in _flash(r.text)
    assert 'aria-current="page">Scheduled' in r.text


def test_queue_redacts_errors(client, demo_root):
    pid = _post_id(demo_root, "failed")
    store = _store(demo_root)
    store.db.execute("UPDATE posts SET error=? WHERE id=?",
                     ("upload failed https://api.example/x?access_token=SUPERSECRET123&a=1", pid))
    store.db.commit()
    t = client.get("/queue", params={"status": "failed"}).text
    assert "SUPERSECRET123" not in t and "access_token=***" in t


def test_queue_ignores_non_http_urls(client, demo_root):
    pid = _post_id(demo_root, "posted")
    store = _store(demo_root)
    store.db.execute("UPDATE posts SET url='javascript:alert(1)', remote_id='rid-123' WHERE id=?", (pid,))
    store.db.commit()
    t = client.get("/queue", params={"status": "posted"}).text
    assert "javascript:alert" not in t and "rid-123" in t


# ---- publish due ------------------------------------------------------------------------
def test_publish_due_queues_one_job(client, csrf, demo_root):
    token = csrf("/queue")
    r = client.post("/queue/publish-due", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303
    first = r.headers["location"]
    assert first.startswith("/jobs/")
    assert _jobs(demo_root)[0]["kind"] == "publish_due"
    # a second click while the first is still waiting reuses it instead of piling up jobs
    r = client.post("/queue/publish-due", data={"csrf": token}, follow_redirects=False)
    assert r.headers["location"] == first
    assert sum(1 for j in _jobs(demo_root) if j["kind"] == "publish_due" and j["status"] == "queued") == 1


def test_posts_require_csrf(client, demo_root):
    sid = _post_id(demo_root, "scheduled")
    fid = _post_id(demo_root, "failed")
    for url in ("/queue/publish-due", f"/queue/{sid}/publish-now", f"/queue/{sid}/cancel", f"/queue/{fid}/retry"):
        assert client.post(url, data={}).status_code == 403, url
    assert _store(demo_root).post(sid)["status"] == "scheduled"
    assert _store(demo_root).post(fid)["status"] == "failed"


# ---- publish now ------------------------------------------------------------------------
def test_publish_now_moves_slot_and_starts_job(client, csrf, demo_root):
    pid = _post_id(demo_root, "scheduled")
    assert _store(demo_root).post(pid)["scheduled_at"] > time.time()
    r = client.post(f"/queue/{pid}/publish-now", data={"csrf": csrf("/queue")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    post = _store(demo_root).post(pid)
    assert post["status"] == "scheduled" and post["scheduled_at"] <= time.time()
    assert _jobs(demo_root)[0]["kind"] == "publish_due"
    assert pid in [p["id"] for p in _store(demo_root).due_posts()]


def test_publish_now_rejects_wrong_state_and_bad_ids(client, csrf, demo_root):
    token = csrf("/queue")
    before = len(_jobs(demo_root))
    posted = _post_id(demo_root, "posted")
    r = client.post(f"/queue/{posted}/publish-now", data={"csrf": token})
    assert r.status_code == 200 and "Only scheduled posts" in _flash(r.text)
    for bad in ("999999", "abc", "-1", "1.5"):
        r = client.post(f"/queue/{bad}/publish-now", data={"csrf": token})
        assert r.status_code == 200 and "doesn&#39;t exist" in _flash(r.text), bad
    assert len(_jobs(demo_root)) == before


# ---- cancel -----------------------------------------------------------------------------
def test_cancel_marks_post_skipped(client, csrf, demo_root):
    pid = _post_id(demo_root, "scheduled")
    r = client.post(f"/queue/{pid}/cancel", data={"csrf": csrf("/queue")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/queue?status=scheduled"
    assert _store(demo_root).post(pid)["status"] == "skipped"
    page = client.get("/queue?status=skipped").text
    assert "Cancelled the" in _flash(page) or "skipped" in page
    assert re.search(r"Skipped</div><div class=\"kpi-value\">1<", page)


def test_cancel_back_link_is_restricted(client, csrf, demo_root):
    token = csrf("/queue")
    ids = [r["id"] for r in _store(demo_root).db.execute(
        "SELECT id FROM posts WHERE status='scheduled' ORDER BY id LIMIT 3").fetchall()]
    r = client.post(f"/queue/{ids[0]}/cancel", data={"csrf": token, "back": "/clips/demo04aaaaaaaaaa"},
                    follow_redirects=False)
    assert r.headers["location"] == "/clips/demo04aaaaaaaaaa"
    r = client.post(f"/queue/{ids[1]}/cancel", data={"csrf": token, "back": "https://evil.example/"},
                    follow_redirects=False)
    assert r.headers["location"] == "/queue?status=scheduled"
    r = client.post(f"/queue/{ids[2]}/cancel", data={"csrf": token, "back": "//evil.example/queue"},
                    follow_redirects=False)
    assert r.headers["location"] == "/queue?status=scheduled"


def test_cancel_rejects_non_scheduled(client, csrf, demo_root):
    pid = _post_id(demo_root, "posted")
    r = client.post(f"/queue/{pid}/cancel", data={"csrf": csrf("/queue")})
    assert r.status_code == 200 and "Only scheduled posts can be cancelled" in _flash(r.text)
    assert _store(demo_root).post(pid)["status"] == "posted"
    r = client.post("/queue/nope/cancel", data={"csrf": csrf("/queue")})
    assert r.status_code == 200 and "doesn&#39;t exist" in _flash(r.text)


# ---- retry ------------------------------------------------------------------------------
def test_retry_requeues_failed_post(client, csrf, demo_root):
    pid = _post_id(demo_root, "failed")
    r = client.post(f"/queue/{pid}/retry", data={"csrf": csrf("/queue")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    post = _store(demo_root).post(pid)
    assert post["status"] == "scheduled" and not post["error"] and post["scheduled_at"] <= time.time()
    job = _jobs(demo_root)[0]
    assert job["kind"] == "publish_due" and json.loads(job["params"])["source"] == "queue"


def test_retry_rejects_non_failed(client, csrf, demo_root):
    before = len(_jobs(demo_root))
    pid = _post_id(demo_root, "scheduled")
    r = client.post(f"/queue/{pid}/retry", data={"csrf": csrf("/queue")})
    assert r.status_code == 200 and "Only failed posts can be retried" in _flash(r.text)
    r = client.post("/queue/0/retry", data={"csrf": csrf("/queue")})
    assert r.status_code == 200 and "doesn&#39;t exist" in _flash(r.text)
    assert len(_jobs(demo_root)) == before


def test_queue_empty_states(client, demo_root):
    store = _store(demo_root)
    store.db.execute("DELETE FROM posts")
    store.db.commit()
    for status, msg in (("scheduled", "Nothing is waiting"), ("posted", "Nothing has been posted"),
                        ("failed", "No failed posts"), ("all", "The queue is empty")):
        r = client.get("/queue", params={"status": status})
        assert r.status_code == 200 and msg in r.text, status
