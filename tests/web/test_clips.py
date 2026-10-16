import dataclasses
import json
import re
import shutil

import yaml

from vv88.config import load_settings
from vv88.store import Store


def _store(demo_root):
    return Store(demo_root / "data" / "vv88.db")


def _fresh_clip(demo_root, clip_id="fresh0aaaaaaaaaa"):
    """A clip with no posts yet (every seeded clip is already queued on all four platforms)."""
    store = _store(demo_root)
    base = store.clip("demo00aaaaaaaaaa")
    path = base.path.replace("demo0.mp4", f"{clip_id}.mp4")
    shutil.copy(base.path, path)
    clip = dataclasses.replace(base, id=clip_id, path=path, hook_text="A fresh hook nobody posted",
                               title="A fresh hook nobody posted")
    store.add_clip(clip)
    return clip


def _flash(text):
    return " ".join(re.findall(r'class="flash [a-z]+" role="status">([^<]*)<', text))


def _jobs(demo_root):
    return _store(demo_root).jobs(limit=50)


# ---- list -------------------------------------------------------------------------------
def test_clips_page_renders_grid_with_post_status(client):
    r = client.get("/clips")
    assert r.status_code == 200
    assert 'class="clips cliplist"' in r.text
    assert r.text.count("<video ") == 6
    assert "/media?path=" in r.text and 'preload="metadata"' in r.text and "playsinline" in r.text
    assert "What would you do with a million?" in r.text and 'href="/clips/demo00aaaaaaaaaa"' in r.text
    assert "Streamer highlights" in r.text and "Money Talks Podcast clips" in r.text
    # per-platform post status badges from the posts table
    assert 'badge info">scheduled' in r.text and 'badge ok">posted' in r.text and 'badge err">failed' in r.text


def test_clips_filters(client):
    r = client.get("/clips", params={"campaign": "streamer-highlights"})
    assert r.status_code == 200
    assert "The clutch nobody saw coming" in r.text and "Why do most people never get rich?" not in r.text
    r = client.get("/clips", params={"style": "soft_lowercase"})
    assert "He lost $2 million in one night" in r.text and "The clutch nobody saw coming" not in r.text
    r = client.get("/clips", params={"campaign": "late-night"})
    assert r.status_code == 200 and "No clips match these filters" in r.text


def test_clips_unknown_filter_is_a_friendly_warning(client):
    r = client.get("/clips", params={"campaign": "<script>nope</script>", "style": "../../etc"})
    assert r.status_code == 200
    assert "<script>nope" not in r.text
    assert "isn&#39;t known" in r.text
    assert r.text.count("<video ") == 6


# ---- detail -----------------------------------------------------------------------------
def test_clip_detail_renders(client):
    r = client.get("/clips/demo03aaaaaaaaaa")
    assert r.status_code == 200
    t = r.text
    assert "He lost $2 million in one night" in t
    assert "#finance" in t and "#podcast" in t                      # hashtags
    assert "Money Talks Podcast clips" in t and "soft_lowercase" in t
    assert 'download="demo03aaaaaaaaaa.mp4"' in t and "/media?path=" in t
    assert "https://example.com/tiktok/demo3" in t                  # post url
    assert "x media finalize failed" in t and "/queue/" in t and "Retry" in t
    assert "#moneytalks #fyp" in t and "data-copy-target=" in t      # captions with copy buttons
    assert "already in the queue or posted on every enabled platform" in t


def test_clip_detail_unknown_clip_redirects_with_message(client):
    r = client.get("/clips/doesnotexist", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/clips"
    r = client.get("/clips/doesnotexist")
    assert r.status_code == 200 and "doesn&#39;t exist" in r.text


def test_clip_detail_shows_schedule_form_for_unposted_clip(client, demo_root):
    _fresh_clip(demo_root)
    r = client.get("/clips/fresh0aaaaaaaaaa")
    assert r.status_code == 200
    assert 'action="/clips/fresh0aaaaaaaaaa/schedule"' in r.text
    for p in ("tiktok", "youtube", "instagram", "x"):
        assert f'name="platforms" value="{p}"' in r.text
    assert "hasn&#39;t been scheduled yet" in r.text


# ---- schedule ---------------------------------------------------------------------------
def test_schedule_submits_job_with_params(client, csrf, demo_root):
    _fresh_clip(demo_root)
    r = client.post("/clips/fresh0aaaaaaaaaa/schedule",
                    data={"csrf": csrf("/clips"), "platforms": ["tiktok", "youtube", "tiktok"]},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    job = _jobs(demo_root)[0]
    assert job["kind"] == "schedule_clip" and job["status"] == "queued"
    params = json.loads(job["params"])
    assert params["clip_id"] == "fresh0aaaaaaaaaa" and params["platforms"] == ["tiktok", "youtube"]

    # the job accepts exactly these params and queues the posts
    from vv88.web.jobs import JOB_KINDS

    settings = load_settings(demo_root / "config.yaml", env_path=demo_root / ".env")
    assert JOB_KINDS["schedule_clip"][1](settings, params) == {"scheduled": 2}
    rows = _store(demo_root).db.execute("SELECT platform, status FROM posts WHERE clip_id='fresh0aaaaaaaaaa'")
    assert sorted((r["platform"], r["status"]) for r in rows) == [("tiktok", "scheduled"), ("youtube", "scheduled")]


def test_schedule_requires_csrf(client, demo_root):
    _fresh_clip(demo_root)
    r = client.post("/clips/fresh0aaaaaaaaaa/schedule", data={"platforms": "tiktok"})
    assert r.status_code == 403


def test_schedule_rejects_bad_input_with_flash(client, csrf, demo_root):
    _fresh_clip(demo_root)
    before = len(_jobs(demo_root))
    token = csrf("/clips")
    cases = [
        ({"platforms": "myspace"}, "Unknown platform"),
        ({}, "Pick at least one platform"),
        ({"platforms": "../../etc/passwd"}, "Unknown platform"),
    ]
    for data, msg in cases:
        r = client.post("/clips/fresh0aaaaaaaaaa/schedule", data={"csrf": token, **data})
        assert r.status_code == 200, data
        assert msg in _flash(r.text), data
    r = client.post("/clips/nope-nope/schedule", data={"csrf": token, "platforms": "tiktok"})
    assert r.status_code == 200 and "doesn&#39;t exist" in _flash(r.text)
    r = client.post("/clips/bad%20id!/schedule", data={"csrf": token, "platforms": "tiktok"})
    assert r.status_code == 200 and "doesn&#39;t exist" in _flash(r.text)
    assert len(_jobs(demo_root)) == before


def test_schedule_refuses_platforms_already_queued(client, csrf, demo_root):
    before = len(_jobs(demo_root))
    r = client.post("/clips/demo04aaaaaaaaaa/schedule", data={"csrf": csrf("/clips"), "platforms": "tiktok"})
    assert r.status_code == 200 and "Already in the queue for tiktok (scheduled)" in _flash(r.text)
    assert len(_jobs(demo_root)) == before


def test_schedule_refuses_disabled_platform(client, csrf, demo_root):
    _fresh_clip(demo_root)
    cfg_path = demo_root / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["publishing"]["platforms"]["x"]["enabled"] = False
    cfg_path.write_text(yaml.safe_dump(cfg))
    page = client.get("/clips/fresh0aaaaaaaaaa").text
    assert 'value="x"' not in page and 'value="tiktok"' in page
    r = client.post("/clips/fresh0aaaaaaaaaa/schedule", data={"csrf": csrf("/clips"), "platforms": "x"})
    assert r.status_code == 200 and "isn&#39;t enabled" in _flash(r.text)


def test_schedule_requeues_a_cancelled_platform(client, csrf, demo_root):
    _fresh_clip(demo_root)
    store = _store(demo_root)
    pid = store.schedule_post("fresh0aaaaaaaaaa", "tiktok", 0, {"platform": "tiktok", "caption": "c"})
    store.cancel_post(pid)
    assert "cancelled before" in client.get("/clips/fresh0aaaaaaaaaa").text
    r = client.post("/clips/fresh0aaaaaaaaaa/schedule", data={"csrf": csrf("/clips"), "platforms": "tiktok"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    assert _store(demo_root).db.execute(
        "SELECT COUNT(*) FROM posts WHERE clip_id='fresh0aaaaaaaaaa'").fetchone()[0] == 0


def test_schedule_refuses_clip_with_missing_file(client, csrf, demo_root):
    clip = _fresh_clip(demo_root)
    from pathlib import Path

    Path(clip.path).unlink()
    assert "missing on disk" in client.get("/clips/fresh0aaaaaaaaaa").text
    r = client.post("/clips/fresh0aaaaaaaaaa/schedule", data={"csrf": csrf("/clips"), "platforms": "tiktok"})
    assert r.status_code == 200 and "missing" in _flash(r.text)
