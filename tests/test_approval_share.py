"""Approval gate, per-platform backends and the Share lane (store + pipeline + factory, no web)."""

import json
import time

import pytest

from vv88.config import load_settings
from vv88.pipeline import Engine
from vv88.publishing.base import DryRunPublisher, PublishError
from vv88.publishing.factory import make_publisher
from vv88.publishing.schedule import approve_post
from vv88.publishing.zernio import ZernioPublisher


def _engine(tmp_path, **publishing):
    settings = load_settings(tmp_path / "missing.yaml", overrides={
        "data_dir": str(tmp_path / "data"), "publishing": publishing})
    return Engine(settings)


def _clip(engine, tmp_path, cid="c1"):
    from vv88.models import RenderedClip

    path = tmp_path / f"{cid}.mp4"
    path.write_bytes(b"\x00" * 64)
    clip = RenderedClip(id=cid, source_id="s", campaign_id="adhoc", path=str(path), start=0, end=10,
                        duration=10, style="karaoke_pop", length_bucket="short", hook_type="question",
                        hook_text="Hook", title="Title", text="text", captions={}, hashtags=["#a"],
                        cover_ms=0, score=1.0)
    engine.store.add_clip(clip)
    return clip


def _rows(engine, status=None):
    sql = "SELECT * FROM posts" + (" WHERE status=?" if status else "") + " ORDER BY id"
    return engine.store.db.execute(sql, (status,) if status else ()).fetchall()


def test_new_posts_wait_for_approval_by_default(tmp_path):
    e = _engine(tmp_path)
    assert e.s.publishing.require_approval is True
    n = e.schedule([_clip(e, tmp_path)], None, ["tiktok", "youtube"])
    assert n == 2 and len(_rows(e, "pending")) == 2
    assert e.publish_due() == []                          # dry run would write everything, but nothing is approved
    assert not (e.s.data_path / "outbox").exists()
    content = json.loads(_rows(e, "pending")[0]["content"])
    assert content["approved"] is False


def test_hand_scheduled_and_approval_off_go_straight_to_scheduled(tmp_path):
    e = _engine(tmp_path)
    e.schedule([_clip(e, tmp_path)], None, ["tiktok"], approved=True)
    row = _rows(e, "scheduled")[0]
    assert json.loads(row["content"])["approved"] is True
    off = _engine(tmp_path / "off", require_approval=False)
    off.schedule([_clip(off, tmp_path, "c2")], None, ["tiktok"])
    assert len(_rows(off, "scheduled")) == 1
    assert json.loads(_rows(off, "scheduled")[0]["content"])["approved"] is False  # no person saw it


def test_approve_keeps_a_future_slot_and_saves_edits(tmp_path):
    e = _engine(tmp_path)
    e.schedule([_clip(e, tmp_path)], None, ["tiktok"])
    row = _rows(e, "pending")[0]
    assert e.approve(row["id"], caption="New caption", title=None)
    after = e.store.post(row["id"])
    assert after["status"] == "scheduled" and after["scheduled_at"] == row["scheduled_at"]
    content = json.loads(after["content"])
    assert content["caption"] == "New caption" and content["approved"] is True
    assert not e.approve(row["id"])                       # only pending posts can be approved


def test_approving_a_backlog_never_posts_it_in_a_burst(tmp_path):
    e = _engine(tmp_path)
    past = time.time() - 3 * 86400
    ids = [e.store.schedule_post(f"c{i}", "tiktok", past + i, {"platform": "tiktok", "caption": "x"},
                                 status="pending") for i in range(4)]
    for i in ids:
        assert approve_post(e.store, e.s.publishing, i)
    times = sorted(e.store.post(i)["scheduled_at"] for i in ids)
    assert times[0] > time.time() + 60                      # moved to upcoming slots, not "now"
    gap = e.s.publishing.platforms["tiktok"].min_gap_minutes * 60
    assert all(b - a >= gap - 1 for a, b in zip(times, times[1:]))


def test_pending_posts_count_for_caps_and_back_pressure(tmp_path):
    e = _engine(tmp_path)
    now = time.time()
    e.store.schedule_post("old", "tiktok", now - 86400 * 2, {"caption": "x"}, status="pending")
    e.store.schedule_post("new", "tiktok", now + 3600, {"caption": "x"}, status="pending")
    assert e.queue_depth("tiktok") == 2                      # a stale pending post still holds rendering back
    assert len(e.store.post_times("tiktok", since=now - 60)) == 1


def test_share_platforms_are_never_auto_published(tmp_path):
    e = _engine(tmp_path, backend="dry_run", platforms={"x": {"backend": "share"}})
    assert e.s.publishing.backend_for("x") == "share" and e.s.publishing.backend_for("tiktok") == "dry_run"
    e.schedule([_clip(e, tmp_path)], None, ["tiktok", "x"], approved=True)
    results = e.publish_due()
    assert [r["platform"] for r in results] == ["tiktok"]
    x_post = [r for r in _rows(e) if r["platform"] == "x"][0]
    assert x_post["status"] == "scheduled"
    with pytest.raises(PublishError, match="Share"):
        make_publisher("x", e.s, e.store)


def test_real_backends_wait_for_their_slot_even_when_another_platform_dry_runs(tmp_path, monkeypatch):
    e = _engine(tmp_path, backend="dry_run", platforms={"tiktok": {"backend": "zernio"}})
    e.schedule([_clip(e, tmp_path)], None, ["tiktok", "youtube"], approved=True)
    called = []
    monkeypatch.setattr("vv88.pipeline.make_publisher",
                        lambda p, s, st: called.append(p) or DryRunPublisher(p, s.data_path / "outbox"))
    e.publish_due()
    assert called == ["youtube"]                            # tiktok's slot is in the future
    tiktok = [r for r in _rows(e) if r["platform"] == "tiktok"][0]
    assert tiktok["status"] == "scheduled"


def test_mark_shared_and_cancel(tmp_path):
    e = _engine(tmp_path)
    pid = e.store.schedule_post("c", "youtube", time.time(), {"caption": "x"}, status="pending")
    assert e.store.mark_shared(pid, url="https://youtube.com/shorts/abcdef123", remote_id="abcdef123")
    row = e.store.post(pid)
    assert row["status"] == "posted" and row["posted_at"] and row["remote_id"] == "abcdef123"
    assert not e.store.mark_shared(pid)
    other = e.store.schedule_post("d", "x", time.time(), {"caption": "x"}, status="pending")
    assert e.store.cancel_post(other) and e.store.post(other)["status"] == "skipped"
    claimed = e.store.schedule_post("f", "x", time.time(), {"caption": "x"})
    assert e.store.claim_post(claimed)
    assert not e.store.mark_shared(claimed) and not e.store.cancel_post(claimed)  # uploading: hands off


def test_factory_routes_each_platform_to_its_own_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_" + "a" * 64)
    monkeypatch.setenv("ZERNIO_ACCOUNT_TIKTOK", "66b2e19d8c3f5a7e9d0b1c2d")
    e = _engine(tmp_path, backend="dry_run", platforms={"tiktok": {"backend": "zernio"}})
    assert isinstance(make_publisher("tiktok", e.s, e.store), ZernioPublisher)
    assert isinstance(make_publisher("youtube", e.s, e.store), DryRunPublisher)


def test_store_rejects_a_bad_initial_status(tmp_path):
    e = _engine(tmp_path)
    with pytest.raises(ValueError):
        e.store.schedule_post("c", "x", time.time(), {}, status="posted")
