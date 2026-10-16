import html as htmllib
import re
import stat

import pytest
import yaml

from vv88.config import Settings, load_settings
from vv88.store import Store
from vv88.web.pages import settings as page


def _flashes(text: str) -> str:
    return htmllib.unescape(" ".join(re.findall(r'<div class="flash [a-z]+" role="status">(.*?)</div>', text, re.S)))


def _form(demo_root, token, **changes) -> dict:
    v = page.values_from_settings(load_settings(demo_root / "config.yaml", env_path=demo_root / ".env"))
    v.update(changes)
    data: dict = {"csrf": token}
    for k, val in v.items():
        if isinstance(val, bool):
            if val:
                data[k] = "on"
        else:
            data[k] = val
    return data


def _cfg(demo_root) -> dict:
    return yaml.safe_load((demo_root / "config.yaml").read_text())


def test_page_renders_form_system_check_and_benchmark(client):
    r = client.get("/settings")
    assert r.status_code == 200
    html = r.text
    assert 'id="settings-form"' in html and 'form="settings-form"' in html
    assert html.count('method="post"') == html.count('name="csrf"')
    assert 'action="/settings/benchmark"' in html
    for name in ("backend", "slots_utc", "tiktok_daily_cap", "x_max_hashtags", "min_clip_seconds", "whisper_model",
                 "force_style", "gameplay_dir", "llm_effort", "half_life_days", "campaigns_per_cycle",
                 "min_budget_remaining_usd", "region_code"):
        assert f'name="{name}"' in html, name
    for style in ("karaoke_pop", "dynamic_minimal", "soft_lowercase", "brainrot_stack"):
        assert f'value="{style}"' in html
    assert '<option value="dry_run" selected>' in html
    assert "System check" in html and "FFmpeg" in html and "Data folder writable" in html
    assert "No benchmark yet" in html
    assert "<script>" not in html and " onclick" not in html


def test_save_merges_into_yaml_and_applies(client, csrf, demo_root, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "apify-secret-token-xyz")
    before = _cfg(demo_root)
    data = _form(demo_root, csrf("/settings"), backend="postiz", slots_utc="21:30, 6:00,06:00", x_enabled=False,
                 tiktok_daily_cap="3", tiktok_min_gap_minutes="120", youtube_max_hashtags="4",
                 min_clip_seconds="15", target_clip_seconds="30", max_clip_seconds="45", clips_per_source="7",
                 whisper_model="base", language="ES", styles=["karaoke_pop", "soft_lowercase"],
                 force_style="soft_lowercase", reframe="blur", llm_enabled=True, llm_effort="high",
                 half_life_days="3.5", campaigns_per_cycle="4", prior_views_per_clip="2,000", whop_enabled=True,
                 min_budget_remaining_usd="100", region_code="gb", disclosure_tag="#sponsored")
    r = client.post("/settings", data=data, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    cfg = _cfg(demo_root)
    # untouched keys survive the merge
    assert cfg["data_dir"] == before["data_dir"] and cfg["discovery"]["manual_file"] == before["discovery"]["manual_file"]
    pub = cfg["publishing"]
    assert pub["backend"] == "postiz" and pub["slots_utc"] == ["06:00", "21:30"]
    assert pub["platforms"]["x"]["enabled"] is False and pub["platforms"]["tiktok"]["enabled"] is True
    assert pub["platforms"]["tiktok"]["daily_cap"] == 3 and pub["platforms"]["tiktok"]["min_gap_minutes"] == 120
    assert pub["disclosure_tag"] == "#sponsored"
    assert cfg["analysis"]["language"] == "es" and cfg["analysis"]["clips_per_source"] == 7
    assert cfg["editing"]["styles"] == ["karaoke_pop", "soft_lowercase"] and cfg["editing"]["gameplay_dir"] is None
    assert cfg["discovery"]["whop_apify"] == {"enabled": True, "min_budget_remaining_usd": 100}
    assert cfg["niche"]["region_code"] == "GB" and cfg["ranking"]["prior_views_per_clip"] == 2000
    # nothing secret (or expanded from the environment) lands in config.yaml
    text = (demo_root / "config.yaml").read_text()
    assert "apify-secret-token-xyz" not in text and "token" not in text and "api_key" not in text
    assert (demo_root / "config.yaml.bak").is_file()
    s = load_settings(demo_root / "config.yaml", env_path=demo_root / ".env")
    assert s.publishing.backend == "postiz" and s.publishing.enabled_platforms() == ["tiktok", "youtube", "instagram"]
    assert s.analysis.target_clip_seconds == 30 and s.llm.enabled and s.optimizer.half_life_days == 3.5
    assert s.editing.force_style == "soft_lowercase" and s.editing.reframe == "blur"
    html = client.get("/settings").text
    assert "Saved config.yaml" in _flashes(html) and '<option value="postiz" selected>' in html


def test_gameplay_dir_must_exist(client, csrf, demo_root, tmp_path):
    token = csrf("/settings")
    r = client.post("/settings", data=_form(demo_root, token, gameplay_dir=str(tmp_path / "nope")))
    assert r.status_code == 400 and "doesn't exist" in _flashes(r.text)
    loops = tmp_path / "loops"
    loops.mkdir()
    r = client.post("/settings", data=_form(demo_root, token, gameplay_dir=str(loops), force_style="brainrot_stack"),
                    follow_redirects=False)
    assert r.status_code == 303
    assert _cfg(demo_root)["editing"]["gameplay_dir"] == str(loops)


@pytest.mark.parametrize("changes, expect", [
    ({"min_clip_seconds": "40", "target_clip_seconds": "30", "max_clip_seconds": "60"}, "shortest ≤ target ≤ longest"),
    ({"slots_utc": "25:00"}, "Posting slots must be times"),
    ({"slots_utc": " , "}, "at least one posting slot"),
    ({"tiktok_daily_cap": "0", "x_min_gap_minutes": "abc", "youtube_max_hashtags": "31"}, "TikTok posts per day"),
    ({"backend": "evil", "whisper_model": "huge", "llm_effort": "ultra", "reframe": "zoom"}, "Publishing backend"),
    ({"region_code": "USA", "language": "english"}, "Region must be a 2-letter"),
    ({"styles": [], "force_style": "nope"}, "Pick at least one caption style"),
    ({"font": "${APIFY_TOKEN}", "llm_model": "bad model!"}, "Caption font must be"),
    ({"disclosure_tag": ""}, "Disclosure tag can't be empty"),
    ({"half_life_days": "nan", "campaigns_per_cycle": "11", "prior_views_per_clip": "-1"}, "half-life"),
])
def test_invalid_input_flashes_and_keeps_file(client, csrf, demo_root, changes, expect):
    before = (demo_root / "config.yaml").read_text()
    r = client.post("/settings", data=_form(demo_root, csrf("/settings"), **changes))
    assert r.status_code == 400
    assert "Settings not saved." in _flashes(r.text) and expect in _flashes(r.text)
    assert (demo_root / "config.yaml").read_text() == before
    assert not (demo_root / "config.yaml.bak").exists()
    # what was typed into text/number inputs is echoed back so nothing is lost
    selects = {"backend", "whisper_model", "llm_effort", "reframe", "force_style"}
    for k, v in changes.items():
        if isinstance(v, str) and k not in selects:
            assert f'name="{k}"' in r.text
            assert f'value="{htmllib.escape(v.strip(), quote=True)}"' in r.text, k


def test_benchmark_submits_one_job(client, csrf, demo_root):
    token = csrf("/settings")
    r = client.post("/settings/benchmark", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303
    job_id = int(r.headers["location"].rsplit("/", 1)[1])
    job = Store(demo_root / "data" / "vv88.db").job(job_id)
    assert job["kind"] == "benchmark" and job["status"] == "queued"
    # a second press while it's queued shows the same job instead of stacking another render
    r = client.post("/settings/benchmark", data={"csrf": token}, follow_redirects=False)
    assert r.headers["location"] == f"/jobs/{job_id}"
    assert "Benchmark job #" in client.get("/settings").text


def test_last_benchmark_result_is_shown(client, demo_root):
    store = Store(demo_root / "data" / "vv88.db")
    jid = store.add_job("benchmark", {"seconds": 20})
    store.update_job(jid, status="done", finished_at=1.0,
                     result={"clip_seconds": 20.0, "render_seconds": 12.5, "clips_per_day": 6912})
    html = client.get("/settings").text
    assert "12.5 s" in html and "6,912" in html and f'href="/jobs/{jid}"' in html


def test_posts_require_csrf(client, demo_root):
    before = (demo_root / "config.yaml").read_text()
    assert client.post("/settings", data={"backend": "postiz"}).status_code == 403
    assert client.post("/settings/benchmark", data={}).status_code == 403
    assert client.post("/settings", data={"backend": "postiz", "csrf": "forged"}).status_code == 403
    assert (demo_root / "config.yaml").read_text() == before
    assert not any(j["kind"] == "benchmark" for j in Store(demo_root / "data" / "vv88.db").jobs(limit=50))


# ---- units (no demo server needed) ------------------------------------------------------------
def test_validate_reports_field_without_echoing_input():
    with pytest.raises(page.ConfigFileError) as e:
        page.validate({"publishing": {"backend": "s3cr3t-value"}})
    assert "publishing.backend" in str(e.value) and "s3cr3t-value" not in str(e.value)
    assert isinstance(page.validate({"publishing": {"backend": "direct"}}), Settings)


def test_read_and_write_config(tmp_path):
    path = tmp_path / "sub" / "config.yaml"
    assert page.read_config(path) == {}
    page.write_config(path, {"publishing": {"backend": "postiz"}})
    assert page.read_config(path) == {"publishing": {"backend": "postiz"}}
    assert stat.S_IMODE(path.stat().st_mode) == 0o644 and not path.with_name("config.yaml.bak").exists()
    path.chmod(0o600)
    page.write_config(path, {"publishing": {"backend": "direct"}})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert yaml.safe_load(path.with_name("config.yaml.bak").read_text()) == {"publishing": {"backend": "postiz"}}
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []
    path.write_text("publishing: [unclosed")
    with pytest.raises(page.ConfigFileError, match="isn't valid YAML"):
        page.read_config(path)
    path.write_text("- a\n- b\n")
    with pytest.raises(page.ConfigFileError, match="mapping"):
        page.read_config(path)


def test_system_checks_are_cached(tmp_path, monkeypatch):
    calls = []
    real_run = page.subprocess.run

    def counting_run(cmd, *a, **kw):
        calls.append(cmd[0])
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(page.subprocess, "run", counting_run)
    monkeypatch.setattr(page, "_cache", {})
    s = Settings(data_dir=str(tmp_path / "data"))
    rows = page.system_checks(s)
    labels = {r["label"]: r for r in rows}
    assert labels["Data folder writable"]["ok"] is True
    assert {"FFmpeg", "FFprobe", "yt-dlp (source downloads)"} <= set(labels)
    assert any(lbl.startswith("Caption font") for lbl in labels)
    first = len(calls)
    page.system_checks(s)
    assert len(calls) == first  # second call within 60 s runs no subprocesses
