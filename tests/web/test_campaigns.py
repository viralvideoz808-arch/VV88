import yaml

from vv88.store import Store


def _store(demo_root):
    return Store(demo_root / "data" / "vv88.db")


def _yaml(demo_root):
    return yaml.safe_load((demo_root / "campaigns.yaml").read_text())["campaigns"]


def _form(token, **kw):
    data = {"csrf": token, "title": "Crypto Daily clips", "brand": "Crypto Daily", "category": "Finance",
            "reward_per_1k_usd": "2.25", "budget_remaining_usd": "1500", "platforms": ["tiktok", "youtube"],
            "source_urls": "https://www.youtube.com/watch?v=abc\n\nhttps://example.com/ep2.mp4",
            "required_hashtags": "cryptodaily, #ad", "required_mentions": "@cryptodaily",
            "keywords": "bitcoin, side hustle", "campaign_url": "https://whop.com/crypto",
            "requirements": "Min 15s", "paid_partnership": "on"}
    data.update(kw)
    return data


def test_page_renders_leaderboard_and_table(client):
    r = client.get("/campaigns")
    assert r.status_code == 200
    html = r.text
    assert "Niche leaderboard" in html and "finance" in html and "$9.00" in html
    # sorted by stored score: money-talks (9.0) before streamer (3.75) before late-night (3.0)
    assert html.index("Money Talks Podcast clips") < html.index("Streamer highlights") < html.index("Late night")
    assert 'href="/studio?campaign=money-talks"' in html
    assert 'action="/campaigns/money-talks/archive"' in html and "data-confirm=" in html
    assert 'action="/campaigns/add"' in html and "<script>" not in html


def test_add_campaign_appends_yaml_and_store(client, csrf, demo_root):
    r = client.post("/campaigns/add", data=_form(csrf("/campaigns"), id="crypto-daily"), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/campaigns"
    item = _yaml(demo_root)[-1]
    assert item["id"] == "crypto-daily" and item["category"] == "finance"
    assert item["reward_per_1k_usd"] == 2.25 and item["budget_remaining_usd"] == 1500
    assert item["platforms"] == ["tiktok", "youtube"]
    assert item["source_urls"] == ["https://www.youtube.com/watch?v=abc", "https://example.com/ep2.mp4"]
    assert item["required_hashtags"] == ["#cryptodaily", "#ad"] and item["required_mentions"] == ["@cryptodaily"]
    assert item["keywords"] == ["bitcoin", "side hustle"] and item["paid_partnership"] is True
    c = _store(demo_root).campaign("crypto-daily")
    assert c is not None and c.status == "active" and c.title == "Crypto Daily clips"
    assert (demo_root / "campaigns.yaml.bak").is_file()
    # a ranking job was queued so the new campaign gets its $/clip
    jobs = _store(demo_root).jobs(limit=1)
    assert jobs[0]["kind"] == "rank"
    page = client.get("/campaigns").text
    assert "Crypto Daily clips" in page and "Added" in page


def test_add_campaign_derives_unique_id(client, csrf, demo_root):
    token = csrf("/campaigns")
    for _ in range(2):
        r = client.post("/campaigns/add", data=_form(token, title="Money Talks!", paid_partnership=""),
                        follow_redirects=False)
        assert r.status_code == 303
    ids = [i["id"] for i in _yaml(demo_root)]
    assert ids[-2:] == ["money-talks-2", "money-talks-3"]  # "money-talks" is already taken
    assert _yaml(demo_root)[-1]["paid_partnership"] is False


def test_add_campaign_creates_missing_file(client, csrf, demo_root):
    (demo_root / "campaigns.yaml").unlink()
    r = client.post("/campaigns/add", data=_form(csrf("/campaigns"), platforms=[]), follow_redirects=False)
    assert r.status_code == 303
    doc = yaml.safe_load((demo_root / "campaigns.yaml").read_text())
    assert list(doc) == ["campaigns"] and doc["campaigns"][0]["id"] == "crypto-daily-clips"
    assert doc["campaigns"][0]["platforms"] == []


def test_add_campaign_rejects_duplicate_id(client, csrf, demo_root):
    before = (demo_root / "campaigns.yaml").read_text()
    r = client.post("/campaigns/add", data=_form(csrf("/campaigns"), id="money-talks"))
    assert r.status_code == 400 and "already exists" in r.text
    assert (demo_root / "campaigns.yaml").read_text() == before
    assert 'value="Crypto Daily clips"' in r.text  # the form keeps what was typed


def test_add_campaign_invalid_input_is_friendly(client, csrf, demo_root):
    before = (demo_root / "campaigns.yaml").read_text()
    token = csrf("/campaigns")
    cases = [
        ({"title": ""}, "Title is required"),
        ({"reward_per_1k_usd": "-1"}, "0 or more"),
        ({"reward_per_1k_usd": "lots"}, "must be a number"),
        ({"reward_per_1k_usd": "nan"}, "0 or more"),
        ({"budget_remaining_usd": "-5"}, "0 or more"),
        ({"source_urls": "javascript:alert(1)"}, "http(s) links"),
        ({"campaign_url": "ftp://x"}, "Campaign page"),
        ({"id": "Bad ID!"}, "ID may only use"),
        ({"platforms": ["myspace"]}, "Unknown platform"),
        ({"required_mentions": "@bad<handle>"}, "valid @handle"),
    ]
    for override, message in cases:
        r = client.post("/campaigns/add", data=_form(token, **override))
        assert r.status_code == 400, override
        assert message in r.text, override
    assert (demo_root / "campaigns.yaml").read_text() == before


def test_add_campaign_refuses_to_clobber_broken_yaml(client, csrf, demo_root):
    (demo_root / "campaigns.yaml").write_text("campaigns: [\n  - {title: oops\n")
    r = client.post("/campaigns/add", data=_form(csrf("/campaigns")))
    assert r.status_code == 400 and "isn&#39;t valid YAML" in r.text
    assert (demo_root / "campaigns.yaml").read_text() == "campaigns: [\n  - {title: oops\n"


def test_archive_and_restore(client, csrf, demo_root):
    token = csrf("/campaigns")
    r = client.post("/campaigns/late-night/archive", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303
    assert next(i for i in _yaml(demo_root) if i["id"] == "late-night")["status"] == "inactive"
    assert _store(demo_root).campaign("late-night").status == "inactive"
    page = client.get("/campaigns").text
    assert "Archived" in page and 'action="/campaigns/late-night/restore"' in page
    assert 'href="/studio?campaign=late-night"' not in page

    r = client.post("/campaigns/late-night/restore", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303
    assert next(i for i in _yaml(demo_root) if i["id"] == "late-night")["status"] == "active"
    assert _store(demo_root).campaign("late-night").status == "active"


def test_archive_discovered_campaign_updates_store_only(client, csrf, demo_root):
    from vv88.models import Campaign

    store = _store(demo_root)
    store.upsert_campaigns([Campaign(id="whop:abc", title="Whop thing", reward_per_1k_usd=1.0, origin="whop")])
    before = (demo_root / "campaigns.yaml").read_text()
    r = client.post("/campaigns/whop%3Aabc/archive", data={"csrf": csrf("/campaigns")})
    assert r.status_code == 200 and "may come back" in r.text
    assert _store(demo_root).campaign("whop:abc").status == "inactive"
    assert (demo_root / "campaigns.yaml").read_text() == before


def test_archive_unknown_campaign(client, csrf):
    r = client.post("/campaigns/nope/archive", data={"csrf": csrf("/campaigns")})
    assert r.status_code == 200 and "doesn&#39;t exist" in r.text


def test_discover_and_rank_start_jobs(client, csrf, demo_root):
    token = csrf("/campaigns")
    r = client.post("/campaigns/discover", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    job_id = int(r.headers["location"].rsplit("/", 1)[1])
    assert _store(demo_root).job(job_id)["kind"] == "discover"
    # a second press while it is still queued goes to the same job
    r2 = client.post("/campaigns/discover", data={"csrf": token}, follow_redirects=False)
    assert r2.headers["location"] == f"/jobs/{job_id}"
    r = client.post("/campaigns/rank", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303 and _store(demo_root).job(int(r.headers["location"].rsplit("/", 1)[1]))["kind"] == "rank"
    assert "Ranking is queued" in client.get("/campaigns").text or "Discovery is queued" in client.get("/campaigns").text


def test_posts_require_csrf(client, demo_root):
    before = (demo_root / "campaigns.yaml").read_text()
    assert client.post("/campaigns/add", data={"title": "x", "reward_per_1k_usd": "1"}).status_code == 403
    assert client.post("/campaigns/money-talks/archive", data={}).status_code == 403
    assert client.post("/campaigns/money-talks/restore", data={}).status_code == 403
    assert client.post("/campaigns/discover", data={"csrf": "wrong"}).status_code == 403
    assert client.post("/campaigns/rank", data={}).status_code == 403
    assert (demo_root / "campaigns.yaml").read_text() == before


def test_requires_login(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        assert c.get("/campaigns", follow_redirects=False).status_code == 303
