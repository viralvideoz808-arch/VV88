import asyncio

from fastapi.testclient import TestClient

from pumpscan.config import Settings
from pumpscan.engine import Engine
from pumpscan.server import create_app

from .helpers import NOW, runner


def demo_settings(tmp_path):
    s = Settings()
    s.demo = True
    s.demo_speed = 30
    s.data_dir = str(tmp_path)
    return s


def test_council_pass_emits_and_persists_signal(tmp_path):
    s = Settings()
    s.data_dir = str(tmp_path)
    e = Engine(s)
    e.clock = lambda: NOW
    tok = runner()
    e.add_token(tok)
    e.ctx.sol_usd = 150
    e.ctx.hot_words = {}
    new = e.council_pass()
    assert len(new) == 1 and new[0]["symbol"] == "KIMCHI"
    assert e.council_pass() == []  # cooldown: no duplicate signal
    assert (tmp_path / "signals.json").exists()
    assert Engine(s).signals[0]["mint"] == tok.mint
    st = e.state()
    assert st["active_signal"]["mint"] == tok.mint and st["active_signal"]["remaining_s"] == 600


def test_signal_voided_when_price_runs(tmp_path):
    s = Settings()
    s.data_dir = str(tmp_path)
    e = Engine(s)
    now = [NOW]
    e.clock = lambda: now[0]
    tok = runner()
    e.add_token(tok)
    e.ctx.sol_usd = 150
    e.council_pass()
    snap = tok.snap
    from pumpscan.models import Snapshot

    tok.add_snapshot(Snapshot(t=NOW + 20, price_usd=1, mcap_usd=snap.mcap_usd * 1.5, buys_m5=200, sells_m5=50,
                              vol_m5=20000, vol_h1=50000, change_m5=60))
    now[0] = NOW + 20
    e.council_pass()
    assert e.signals[-1]["state"] == "void"


def test_demo_engine_start_stop(tmp_path):
    async def go():
        e = Engine(demo_settings(tmp_path))
        await e.start()
        await asyncio.sleep(2.5)
        st = e.state()
        await e.stop()
        return st, e

    st, e = asyncio.run(go())
    assert st["running"] and st["mode"] == "demo"
    assert st["stats"]["watched"] >= 60 and st["stats"]["passes"] >= 1
    assert st["candidates"] and len(st["agents"]) == 25
    assert not e.running and e._tasks == []


def test_http_api(tmp_path):
    app = create_app(demo_settings(tmp_path))
    with TestClient(app) as c:
        assert "START" in c.get("/").text
        assert c.get("/api/state").json()["running"] is False
        assert c.post("/api/start").json() == {"running": True}
        assert c.get("/api/state").json()["running"] is True
        assert c.post("/api/rules", json={"min_buy_votes": 25}).json()["rules"]["min_buy_votes"] == 25
        assert c.post("/api/rules", json={"min_buy_votes": 30}).status_code == 422
        assert c.post("/api/stop").json() == {"running": False}


def test_password_protects_everything_but_healthz(tmp_path):
    s = demo_settings(tmp_path)
    s.password = "hunter2"
    with TestClient(create_app(s)) as c:
        assert c.get("/healthz").status_code == 200
        r = c.get("/")
        assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
        assert c.post("/api/start").status_code == 401
        assert c.get("/api/state", auth=("x", "wrong")).status_code == 401
        assert c.get("/api/state", auth=("anyone", "hunter2")).json()["running"] is False


def test_public_host_requires_password(monkeypatch):
    import pytest

    from pumpscan.__main__ import main

    monkeypatch.delenv("PUMPSCAN_PASSWORD", raising=False)
    with pytest.raises(SystemExit, match="without a password"):
        main(["--host", "0.0.0.0", "--no-browser"])


def test_autostart(tmp_path):
    s = demo_settings(tmp_path)
    s.autostart = True
    with TestClient(create_app(s)) as c:
        assert c.get("/api/state").json()["running"] is True
