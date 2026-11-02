"""Queue: To approve / To share tabs, approve (+ approve all), mark posted, and the settings/connections around them."""

import json
import re
import time

import yaml

from vv88 import connect
from vv88.store import Store


def _store(demo_root):
    return Store(demo_root / "data" / "vv88.db")


def _flash(text):
    return " ".join(re.findall(r'class="flash [a-z]+" role="status">([^<]*)<', text))


def _set_config(demo_root, **publishing):
    path = demo_root / "config.yaml"
    doc = yaml.safe_load(path.read_text()) or {}
    pub = doc.setdefault("publishing", {})
    for k, v in publishing.items():
        if k == "platforms":
            for p, pc in v.items():
                pub.setdefault("platforms", {}).setdefault(p, {}).update(pc)
        else:
            pub[k] = v
    path.write_text(yaml.safe_dump(doc))


def _pending(demo_root, platform="tiktok", when=None, caption="Draft caption #fyp", clip=None):
    store = _store(demo_root)
    clip = clip or store.db.execute("SELECT id FROM clips ORDER BY id LIMIT 1").fetchone()["id"]
    store.db.execute("DELETE FROM posts WHERE clip_id=? AND platform=?", (clip, platform))
    store.db.commit()
    return store.schedule_post(clip, platform, when or time.time() + 7200,
                               {"platform": platform, "caption": caption, "title": "Title"}, status="pending")


def test_queue_opens_on_to_approve_when_posts_wait(client, demo_root):
    pid = _pending(demo_root)
    t = client.get("/queue").text
    assert 'aria-current="page">To approve' in t
    assert f'id="post-{pid}"' in t and f'action="/queue/{pid}/approve"' in t
    assert "<video" in t and "Draft caption #fyp</textarea>" in t
    assert 'data-maxlen="2200"' in t and "Approve all 1" in t


def test_approve_saves_the_edited_caption(client, csrf, demo_root):
    pid = _pending(demo_root)
    r = client.post(f"/queue/{pid}/approve", data={"csrf": csrf("/queue"), "caption": "Edited\r\ncaption"},
                    follow_redirects=True)
    assert "Approved the tiktok post" in _flash(r.text)
    row = _store(demo_root).post(pid)
    content = json.loads(row["content"])
    assert row["status"] == "scheduled" and content["caption"] == "Edited\ncaption" and content["approved"] is True


def test_approve_rejects_a_too_long_caption_and_keeps_the_post_waiting(client, csrf, demo_root):
    pid = _pending(demo_root, platform="x")
    r = client.post(f"/queue/{pid}/approve", data={"csrf": csrf("/queue"), "caption": "x" * 300},
                    follow_redirects=True)
    assert "Not approved" in _flash(r.text) and "limit is 280" in _flash(r.text)
    assert _store(demo_root).post(pid)["status"] == "pending"


def test_approve_needs_csrf(client, demo_root):
    pid = _pending(demo_root)
    r = client.post(f"/queue/{pid}/approve", data={"caption": "x"}, follow_redirects=False)
    assert r.status_code in (400, 403)
    assert _store(demo_root).post(pid)["status"] == "pending"


def test_approve_all(client, csrf, demo_root):
    a = _pending(demo_root, platform="tiktok")
    b = _pending(demo_root, platform="instagram", when=time.time() - 86400)   # slot passed while waiting
    r = client.post("/queue/approve-all", data={"csrf": csrf("/queue")}, follow_redirects=True)
    assert "Approved 2 posts" in _flash(r.text)
    store = _store(demo_root)
    assert {store.post(a)["status"], store.post(b)["status"]} == {"scheduled"}
    assert store.post(b)["scheduled_at"] > time.time()                       # re-slotted, not posted at once


def test_share_tab_offers_share_and_mark_posted(client, csrf, demo_root):
    _set_config(demo_root, platforms={"youtube": {"backend": "share"}})
    pid = _pending(demo_root, platform="youtube")
    t = client.get("/queue").text                       # nothing else waits for approval -> opens on To share
    assert 'aria-current="page">To share' in t and "YouTube is set to Share" in t
    assert re.search(r'data-share-src="/media\?path=[^"]+\.mp4"', t) and f'data-share-caption="#cap-{pid}"' in t
    assert "Share to YouTube" in t and f'action="/queue/{pid}/mark-posted"' in t
    assert f'action="/queue/{pid}/approve"' not in t    # sharing by hand is the approval
    r = client.post(f"/queue/{pid}/mark-posted", data={"csrf": csrf("/queue"), "back": "/queue?status=share",
                                                       "url": "https://youtube.com/shorts/AbCdEf12345"},
                    follow_redirects=True)
    assert "Marked the youtube post as posted" in _flash(r.text)
    row = _store(demo_root).post(pid)
    assert row["status"] == "posted" and row["remote_id"] == "AbCdEf12345"


def test_mark_posted_rejects_a_bad_link(client, csrf, demo_root):
    _set_config(demo_root, platforms={"x": {"backend": "share"}})
    pid = _pending(demo_root, platform="x")
    r = client.post(f"/queue/{pid}/mark-posted", data={"csrf": csrf("/queue"), "url": "javascript:alert(1)"},
                    follow_redirects=True)
    assert "look right" in _flash(r.text)
    assert _store(demo_root).post(pid)["status"] == "pending"


def test_publish_now_points_share_and_pending_posts_elsewhere(client, csrf, demo_root):
    pid = _pending(demo_root)
    r = client.post(f"/queue/{pid}/publish-now", data={"csrf": csrf("/queue")}, follow_redirects=True)
    assert "Approve this post first" in _flash(r.text)
    _set_config(demo_root, platforms={"tiktok": {"backend": "share"}})
    r = client.post(f"/queue/{pid}/publish-now", data={"csrf": csrf("/queue")}, follow_redirects=True)
    assert "set to Share" in _flash(r.text)
    assert _store(demo_root).post(pid)["status"] == "pending"


def test_skip_a_waiting_post(client, csrf, demo_root):
    pid = _pending(demo_root)
    r = client.post(f"/queue/{pid}/cancel", data={"csrf": csrf("/queue"), "back": "/queue?status=approve"},
                    follow_redirects=True)
    assert "Skipped the tiktok post" in _flash(r.text)
    assert _store(demo_root).post(pid)["status"] == "skipped"


def test_dashboard_and_clip_page_point_to_waiting_posts(client, demo_root):
    pid = _pending(demo_root)
    t = client.get("/").text
    assert "Waiting for you" in t and 'href="/queue?status=approve"' in t
    clip = _store(demo_root).post(pid)["clip_id"]
    page = client.get(f"/clips/{clip}").text
    assert f'href="/queue?status=approve#post-{pid}"' in page


def test_settings_save_per_platform_backend_and_approval(client, csrf, demo_root):
    form = client.get("/settings").text
    assert 'name="tiktok_backend"' in form and 'name="require_approval"' in form and "zernio" in form
    from vv88.config import load_settings
    from vv88.web.pages.settings import values_from_settings

    values = values_from_settings(load_settings(demo_root / "config.yaml", env_path=demo_root / ".env"))
    data = {k: v for k, v in values.items() if not isinstance(v, (bool, list))}
    data.update({k: "on" for k, v in values.items() if v is True})
    data["styles"] = values["styles"]
    data.pop("require_approval", None)                       # unticked = off
    data.update({"csrf": csrf("/settings"), "tiktok_backend": "zernio", "x_backend": "share"})
    r = client.post("/settings", data=data, follow_redirects=True)
    assert "Saved" in _flash(r.text), _flash(r.text)
    doc = yaml.safe_load((demo_root / "config.yaml").read_text())
    assert doc["publishing"]["platforms"]["tiktok"]["backend"] == "zernio"
    assert doc["publishing"]["platforms"]["x"]["backend"] == "share"
    assert doc["publishing"]["platforms"]["youtube"]["backend"] is None
    assert doc["publishing"]["require_approval"] is False
    data.update({"csrf": csrf("/settings"), "tiktok_backend": "nonsense"})
    r = client.post("/settings", data=data, follow_redirects=True)
    assert "posting method" in _flash(r.text)


def test_save_zernio_key_lists_accounts(client, csrf, demo_root, monkeypatch):
    def fake_validate(tool, key, settings, http=None):
        assert tool == "zernio"
        return {"accounts": {"tiktok": "acc_tt", "instagram": "acc_ig"},
                "usernames": {"tiktok": "allviralvideozz", "instagram": "allviralvideozz"}}

    monkeypatch.setattr(connect, "validate_key", fake_validate)
    r = client.post("/connections/key/zernio", data={"csrf": csrf("/connections"), "key": "sk_" + "c" * 64},
                    follow_redirects=True)
    msg = _flash(r.text)
    assert "Zernio" in msg and "TikTok @allviralvideozz" in msg and "TikTok to “zernio”" in msg
    env = (demo_root / ".env").read_text()
    assert "ZERNIO_ACCOUNT_TIKTOK=acc_tt" in env and "ZERNIO_ACCOUNT_INSTAGRAM=acc_ig" in env
    page = client.get("/connections").text
    assert "Free and simplest: Zernio + Share" in page and "sk_ccc" not in page
