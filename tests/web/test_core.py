def test_requires_login(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_dashboard_and_jobs_render(client):
    r = client.get("/")
    assert r.status_code == 200 and "Dashboard" in r.text and "Content-Security-Policy" in r.headers
    assert client.get("/jobs").status_code == 200


def test_post_without_csrf_is_rejected(client):
    assert client.post("/actions/discover", data={}).status_code == 403


def test_quick_action_queues_a_job(client, csrf):
    r = client.post("/actions/metrics", data={"csrf": csrf()}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")


def test_media_is_confined_to_allowed_folders(client, demo_root):
    assert client.get("/media", params={"path": "/etc/passwd"}).status_code == 404
    assert client.get("/media", params={"path": str(demo_root / "config.yaml")}).status_code == 404
    clip = next((demo_root / "data" / "clips").rglob("*.mp4"))
    assert client.get("/media", params={"path": str(clip)}).status_code == 200


def test_login_redirect_is_not_open(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        r = c.post("/login", data={"password": "test-password", "next": "https://evil.example"},
                   follow_redirects=False)
        assert r.headers["location"] == "/"
