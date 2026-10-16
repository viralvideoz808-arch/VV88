"""Fixtures for web UI tests: a seeded demo folder and a logged-in TestClient.

    def test_something(client, csrf):
        r = client.get("/campaigns")
        token = csrf("/campaigns")            # CSRF token from any rendered page
        client.post("/campaigns/add", data={"csrf": token, ...})
"""

import os
import re
import shutil

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from vv88.web.app import create_app  # noqa: E402
from vv88.web.demo import seed  # noqa: E402


@pytest.fixture
def demo_root(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed to seed demo clips")
    saved = dict(os.environ)
    root = tmp_path / "demo"
    seed(root, chdir=False)
    monkeypatch.chdir(root)
    monkeypatch.setenv("VV88_WEB_PASSWORD", "test-password")
    yield root
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def brand_dir(tmp_path):
    """A minimal valid brand kit (tests that need one can write their own instead)."""
    import json

    d = tmp_path / "brandkit"
    d.mkdir()
    for name in ("avatar_1024.png", "banner_youtube_2560x1440.png", "header_x_1500x500.png"):
        (d / name).write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
    (d / "brand.json").write_text(json.dumps({
        "concept_name": "Test kit", "unified_handle": "allviralvideozz", "handle_fallbacks": [],
        "display_names": {"tiktok": "All Viral Videozz", "instagram": "All Viral Videozz", "x": "All Viral Videozz",
                          "youtube": "All Viral Videozz"},
        "bios": {"tiktok": "Moments worth a replay", "instagram": "Moments worth a replay\nNew clips daily",
                 "x": "Moments worth a replay.", "youtube_description": "Independent clip channel."},
        "youtube_keywords": ["clips", "podcast clips"],
    }))
    return d


@pytest.fixture
def app(demo_root, brand_dir):
    return create_app(demo_root / "config.yaml", env_path=demo_root / ".env", brand_dir=brand_dir, start_jobs=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        r = c.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
        assert r.status_code == 303
        yield c


@pytest.fixture
def csrf(client):
    def get(path="/"):
        m = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
        assert m, f"no csrf field on {path}"
        return m.group(1)

    return get
