import re
import time

from vv88.store import Store

# Demo seed: clips 0-3 (money-talks, $1.50/1K) posted on 4 platforms (X failed for clip 3) with these views.
SEED_VIEWS = {(i, p): int(base * mult)
              for i, base in enumerate([18000, 4200, 9100, 2600])
              for p, mult in zip(("tiktok", "youtube", "instagram", "x"), (1.6, 0.9, 1.1, 0.4))
              if not (i == 3 and p == "x")}
TOTAL = sum(SEED_VIEWS.values())


def _store(demo_root):
    return Store(demo_root / "data" / "vv88.db")


def _flash(text):
    return " ".join(re.findall(r'class="flash [a-z]+" role="status">([^<]*)<', text))


def _kpi(text, label):
    m = re.search(rf'<div class="kpi-label">{re.escape(label)}</div><div class="kpi-value">([^<]*)<', text)
    assert m, f"no KPI {label}"
    return m.group(1)


# ---- page -------------------------------------------------------------------------------
def test_performance_renders_kpis(client):
    r = client.get("/performance")
    assert r.status_code == 200
    t = r.text
    assert TOTAL == 134560
    assert _kpi(t, "Measured views") == "134,560"
    assert _kpi(t, "Posts with metrics") == "15"
    assert _kpi(t, "Est. earnings") == "$201.84"          # 134,560 views x $1.50 / 1K
    assert 'action="/performance/pull-metrics"' in t and "Pull view counts" in t
    assert "of 15 posted" in t and "views last pulled" in t


def test_performance_whats_winning(client):
    t = client.get("/performance").text
    assert "What's winning this week" in t
    assert "7-day half-life" in t
    for title in ("Caption style", "Clip length", "Hook type"):
        assert f"<h3>{title}</h3>" in t
    # style labels, uplift percentages and bars scaled to the biggest |uplift| (which gets 100%)
    assert "Karaoke pop" in t and "Dynamic minimal" in t and "Soft lowercase" in t
    assert "15–30 s" in t or "30–45 s" in t
    assert re.search(r'class="num nowrap pf-(up|down)">[+-]\d+\.\d%<', t)
    widths = [float(w) for w in re.findall(r'<div class="bar(?: neg)?" role="img"[^>]*><span style="width: ([\d.]+)%">', t)]
    assert widths and max(widths) == 100.0 and min(widths) >= 0
    assert "(from 15 posts at least 48h old)" in t


def test_performance_half_life_comes_from_settings(client, demo_root):
    cfg = demo_root / "config.yaml"
    cfg.write_text(cfg.read_text() + "optimizer:\n  half_life_days: 3.5\n")
    t = client.get("/performance").text
    assert "3.5-day half-life" in t


def test_performance_platform_and_campaign_tables(client):
    t = client.get("/performance").text
    tiktok = sum(v for (i, p), v in SEED_VIEWS.items() if p == "tiktok")
    assert tiktok == 54240 and "54,240" in t
    assert "Views by platform" in t and "Earnings by campaign" in t
    assert "Money Talks Podcast clips" in t and "$1.50/1K" in t and "$201.84" in t


def test_performance_top_posts(client):
    t = client.get("/performance").text
    section = t.split("Top 10 posts by views", 1)[1].split('id="payouts"', 1)[0]
    rows = re.findall(r"<tr>.*?</tr>", section, flags=re.S)[1:]   # skip header row
    assert len(rows) == 10
    assert "28,800" in rows[0] and "https://example.com/tiktok/demo0" in rows[0]
    assert "Why do most people never get rich?" in rows[0] and 'href="/clips/demo00aaaaaaaaaa"' in rows[0]
    views = [int(re.search(r"<strong>([\d,]+)</strong>", r).group(1).replace(",", "")) for r in rows]
    assert views == sorted(views, reverse=True)


def test_performance_payout_links_default_week(client):
    t = client.get("/performance").text
    m = re.search(r'<textarea id="payout-1" hidden readonly aria-hidden="true">([^<]*)</textarea>', t)
    assert m
    urls = m.group(1).split("\n")
    assert len(urls) == 15 and all(u.startswith("https://example.com/") for u in urls)
    assert 'data-copy-target="#payout-1"' in t and "Copy all 15" in t
    assert 'href="https://whop.com/example"' in t          # the campaign's page
    assert '<option value="7" selected>' in t
    assert "1 post without a link" not in t                # the failed X post isn't "posted"


def test_performance_payout_window(client):
    t = client.get("/performance", params={"days": "1"}).text
    assert "Nothing posted in the last 1 day." in t and "payout-1" not in t
    assert '<option value="1" selected>' in t
    t = client.get("/performance", params={"days": "5"}).text   # clips 1-3 were posted 5, 4 and 3 days ago (+0-3h)
    m = re.search(r'<textarea id="payout-1"[^>]*>([^<]*)</textarea>', t)
    assert m and 8 <= len(m.group(1).split("\n")) <= 11
    assert '<option value="5" selected>' in t


def test_performance_invalid_days_flash_not_500(client):
    for bad in ("abc", "0", "-3", "100000", "7.5", "1e3", "<script>"):
        r = client.get("/performance", params={"days": bad})
        assert r.status_code == 200, bad
        assert "Days must be a whole number" in _flash(r.text), bad
        assert '<option value="7" selected>' in r.text
        assert "<script>" not in r.text.split("</style>", 1)[1]


def test_performance_ignores_unsafe_urls(client, demo_root):
    store = _store(demo_root)
    pid = store.db.execute("SELECT id FROM posts WHERE url='https://example.com/tiktok/demo0'").fetchone()["id"]
    store.db.execute("UPDATE posts SET url='javascript:alert(1)' WHERE id=?", (pid,))
    store.db.commit()
    t = client.get("/performance").text
    assert "javascript:" not in t
    assert "1 post without a link" in t and "Copy all 14" in t


def test_performance_empty_states(client, demo_root):
    store = _store(demo_root)
    store.db.execute("DELETE FROM metrics")
    store.db.execute("DELETE FROM posts")
    store.db.commit()
    r = client.get("/performance")
    assert r.status_code == 200
    t = r.text
    assert _kpi(t, "Measured views") == "0" and _kpi(t, "Est. earnings") == "$0.00"
    assert "Nothing to compare yet" in t and "Pull view counts" in t
    assert "No posts published yet" in t and "No measured posts yet" in t
    assert "Nothing posted in the last 7 days" in t


def test_performance_recent_posts_not_in_winning_yet(client, demo_root):
    store = _store(demo_root)
    store.db.execute("UPDATE posts SET posted_at=? WHERE status='posted'", (time.time() - 3600,))
    store.db.commit()
    t = client.get("/performance").text
    assert "Nothing to compare yet" in t                    # all posts are younger than metric_age_hours
    assert _kpi(t, "Measured views") == "134,560"


# ---- pull view counts -------------------------------------------------------------------
def test_pull_metrics_starts_job(client, csrf, demo_root):
    token = csrf("/performance")
    r = client.post("/performance/pull-metrics", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303
    m = re.fullmatch(r"/jobs/(\d+)", r.headers["location"])
    assert m
    job = _store(demo_root).job(int(m.group(1)))
    assert job["kind"] == "metrics" and job["status"] == "queued"
    assert "Pulling the latest view counts" in _flash(client.get(r.headers["location"]).text)
    # a second press reuses the waiting job instead of queueing another
    r2 = client.post("/performance/pull-metrics", data={"csrf": token}, follow_redirects=False)
    assert r2.headers["location"] == r.headers["location"]
    assert "already waiting" in _flash(client.get("/performance").text)


def test_pull_metrics_requires_csrf(client, demo_root):
    before = len(_store(demo_root).jobs(limit=100))
    assert client.post("/performance/pull-metrics", data={}).status_code == 403
    assert client.post("/performance/pull-metrics", data={"csrf": "wrong"}).status_code == 403
    assert len(_store(demo_root).jobs(limit=100)) == before


def test_performance_requires_login(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as anon:
        r = anon.get("/performance", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
