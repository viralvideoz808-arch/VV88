import re

import pytest
from fastapi.testclient import TestClient

from carlos.config import Settings
from carlos.db import Database
from carlos.seed import seed_sample_inventory


@pytest.fixture
def settings(tmp_path):
    return Settings(city="Miami, FL", phone="(305) 555-0100", ops_password="test-password-123",
                    secret_key="s" * 48, upload_dir=str(tmp_path / "uploads"), meta_app_secret="appsecret",
                    meta_verify_token="verify-me", site_url="https://carlos.example")


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    seed_sample_inventory(d)
    d.set_setting("outreach.quiet_hours", [0, 0])  # never quiet during tests
    return d


@pytest.fixture
def site(db, settings):
    from carlos.web.site import create_site_app
    return TestClient(create_site_app(db, settings))


@pytest.fixture
def ops(db, settings):
    from carlos.web.ops import create_ops_app
    c = TestClient(create_ops_app(db, settings, run_scheduler=False))
    c.post("/login", data={"password": "test-password-123", "next": "/"})
    c.csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/settings").text).group(1)
    return c
