import json
import re

from vv88 import brand as b
from vv88.store import Store


def _store(demo_root):
    return Store(demo_root / "data" / "vv88.db")


def _flash(text):
    return " ".join(re.findall(r'class="flash [a-z]+" role="status">([^<]*)<', text))


def _fingerprint(text):
    m = re.search(r'name="fingerprint" value="([0-9a-f]+)"', text)
    assert m, "no approve form"
    return m.group(1)


def _edit(brand_dir, **changes):
    data = json.loads((brand_dir / "brand.json").read_text())
    data.update(changes)
    (brand_dir / "brand.json").write_text(json.dumps(data))


def _approve(client, csrf, fingerprint=None, reviewed="yes"):
    token = csrf("/brand")
    data = {"csrf": token, "fingerprint": fingerprint or _fingerprint(client.get("/brand").text)}
    if reviewed:
        data["reviewed"] = reviewed
    return client.post("/brand/approve", data=data, follow_redirects=False)


# ---- page -------------------------------------------------------------------------------
def test_brand_renders_kit(client, brand_dir):
    r = client.get("/brand")
    assert r.status_code == 200
    t = r.text
    kit = b.load_kit(brand_dir)
    assert "Test kit" in t and "@allviralvideozz" in t
    assert f"<code>{kit.fingerprint()}</code>" in t and _fingerprint(t) == kit.fingerprint()
    assert 'class="avatar-xl"' in t and t.count('class="banner"') == 2
    assert "/media?path=" in t and "avatar_1024.png" in t
    assert "Waiting for your approval" in t and "No problems" in t
    # counts against BIO_LIMITS / NAME_LIMITS
    assert '<span class="counter">22 / 80</span>' in t          # "Moments worth a replay" on TikTok
    assert '<span class="counter">17 / 30</span>' in t          # "All Viral Videozz"
    assert '<span class="counter">17 / 100</span>' in t         # YouTube channel name
    assert "/ 1000</span>" in t and "/ 500</span>" in t
    # every text has a copy button bound to an element holding exactly that text
    for key in ("tiktok", "instagram", "x", "youtube_description"):
        assert f'data-copy-target="#bk-bio-{key}"' in t and f'id="bk-bio-{key}"' in t
    for key in ("tiktok", "instagram", "x", "youtube"):
        assert f'data-copy-target="#bk-name-{key}"' in t
    assert 'id="bk-bio-instagram">Moments worth a replay\nNew clips daily</div>' in t
    assert 'data-copy-target="#bk-keywords"' in t and '>clips &#34;podcast clips&#34;</div>' in t
    assert 'data-copy-target="#bk-handle"' in t
    # approve form + the note about how profiles are applied
    assert 'action="/brand/approve"' in t and 'name="reviewed" value="yes" required' in t
    assert "I reviewed the picture, banners and every word above" in t
    assert 'action="/brand/apply"' not in t
    assert "docs/LAPTOP_SETUP.md" in t and "applied automatically when YouTube is linked" in t


def test_brand_missing_kit_explains(client, brand_dir):
    (brand_dir / "brand.json").unlink()
    r = client.get("/brand")
    assert r.status_code == 200
    assert "No brand kit yet" in r.text and "brand/brand.json" in r.text and "brand design" in r.text
    assert 'action="/brand/approve"' not in r.text


def test_brand_broken_json_is_friendly(client, brand_dir):
    (brand_dir / "brand.json").write_text("{not json")
    r = client.get("/brand")
    assert r.status_code == 200 and "can't be read" in r.text
    (brand_dir / "brand.json").write_text(json.dumps(["a", "list"]))
    r = client.get("/brand")
    assert r.status_code == 200 and "can't be read" in r.text


def test_brand_odd_field_types_do_not_crash(client, brand_dir):
    _edit(brand_dir, palette="#fff", alternates={"x": 1}, youtube_keywords=[1, "ok"], palette_roles=[1])
    assert client.get("/brand").status_code == 200


def test_brand_images_only_from_kit_folder(client, brand_dir):
    (brand_dir.parent / "secret.png").write_bytes(b"\x89PNG outside")
    (brand_dir / "alt_one.png").write_bytes(b"\x89PNG alt")
    (brand_dir / "notes.txt").write_text("hi")
    _edit(brand_dir, alternates=["../secret.png", "alt_one.png", "/etc/passwd", "notes.txt", "missing.png"])
    t = client.get("/brand").text
    assert "alt_one.png" in t and "secret.png" not in t and "passwd" not in t and "notes.txt" not in t
    assert t.count("bk-alt") >= 1 and "Alternate picture 2" not in t


def test_brand_palette_only_valid_colours(client, brand_dir):
    _edit(brand_dir, palette=["#FFE600", "red;background:url(x)", "#0F0F0F"], palette_roles={"#FFE600": "Signal"})
    t = client.get("/brand").text
    assert 'style="background: #FFE600"' in t and 'style="background: #0F0F0F"' in t
    assert "url(x)" not in t and "Signal" in t


def test_brand_over_limit_shows_red_and_blocks_approval(client, csrf, brand_dir, demo_root):
    _edit(brand_dir, bios={"tiktok": "x" * 81, "instagram": "ok", "x": "ok", "youtube_description": "ok"})
    t = client.get("/brand").text
    assert '<span class="counter over">81 / 80</span>' in t
    assert "tiktok bio is 81 chars (limit 80)" in t and "1 problem" in t
    assert 'action="/brand/approve"' not in t and "Approval is locked" in t
    fp = b.load_kit(brand_dir).fingerprint()
    r = _approve(client, csrf, fingerprint=fp)
    assert r.status_code == 303
    assert "Fix these" in _flash(client.get("/brand").text)
    assert _store(demo_root).kv_get("brand:approved") is None


# ---- approve ----------------------------------------------------------------------------
def test_brand_approve_happy_path(client, csrf, brand_dir, demo_root):
    r = _approve(client, csrf)
    assert r.status_code == 303 and r.headers["location"] == "/brand"
    rec = _store(demo_root).kv_get("brand:approved")
    assert rec["fingerprint"] == b.load_kit(brand_dir).fingerprint() and rec["note"] == "web"
    t = client.get("/brand").text
    assert "Brand kit approved" in _flash(t)
    assert "Approved" in t and 'action="/brand/approve"' not in t
    assert t.count('action="/brand/apply"') == 2          # header + approval card


def test_brand_approve_rejects_wrong_fingerprint(client, csrf, demo_root):
    r = _approve(client, csrf, fingerprint="0" * 20)
    assert r.status_code == 303
    assert "changed after you opened this page" in _flash(client.get("/brand").text)
    assert _store(demo_root).kv_get("brand:approved") is None


def test_brand_approve_rejects_old_fingerprint(client, csrf, brand_dir, demo_root):
    old = _fingerprint(client.get("/brand").text)
    _edit(brand_dir, bios={"tiktok": "A different bio", "instagram": "ok", "x": "ok", "youtube_description": "ok"})
    r = _approve(client, csrf, fingerprint=old)
    assert r.status_code == 303
    t = client.get("/brand").text
    assert "changed after you opened this page" in _flash(t)
    assert _store(demo_root).kv_get("brand:approved") is None
    assert _fingerprint(t) != old                       # the page now offers the new version


def test_brand_image_change_needs_new_approval(client, csrf, brand_dir, demo_root):
    assert _approve(client, csrf).status_code == 303
    (brand_dir / "avatar_1024.png").write_bytes(b"\x89PNG a new picture")
    t = client.get("/brand").text
    assert "changed since it was last approved" in t and 'action="/brand/approve"' in t
    assert 'action="/brand/apply"' not in t


def test_brand_approve_requires_checkbox(client, csrf, demo_root):
    for reviewed in (None, "on", "no"):
        r = _approve(client, csrf, reviewed=reviewed)
        assert r.status_code == 303
        assert "Tick" in _flash(client.get("/brand").text)
    assert _store(demo_root).kv_get("brand:approved") is None


def test_brand_approve_garbage_fingerprint(client, csrf, demo_root):
    for bad in ("<script>alert(1)</script>", "", "z" * 20, "a" * 5000):
        r = _approve(client, csrf, fingerprint=bad or " ")
        assert r.status_code == 303
        flashes = _flash(client.get("/brand").text)
        assert "out of date" in flashes or "changed" in flashes
    assert _store(demo_root).kv_get("brand:approved") is None


def test_brand_approve_without_kit(client, csrf, brand_dir):
    token = csrf("/brand")
    (brand_dir / "brand.json").unlink()
    r = client.post("/brand/approve", data={"csrf": token, "fingerprint": "a" * 20, "reviewed": "yes"},
                    follow_redirects=False)
    assert r.status_code == 303 and "no readable brand kit" in _flash(client.get("/brand").text)


# ---- apply ------------------------------------------------------------------------------
def test_brand_apply_requires_approval(client, csrf, demo_root):
    before = len(_store(demo_root).jobs(limit=100))
    r = client.post("/brand/apply", data={"csrf": csrf("/brand")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/brand"
    assert "Approve the current version" in _flash(client.get("/brand").text)
    assert len(_store(demo_root).jobs(limit=100)) == before


def test_brand_apply_starts_job(client, csrf, brand_dir, demo_root):
    assert _approve(client, csrf).status_code == 303
    r = client.post("/brand/apply", data={"csrf": csrf("/brand")}, follow_redirects=False)
    assert r.status_code == 303
    m = re.fullmatch(r"/jobs/(\d+)", r.headers["location"])
    assert m
    job = _store(demo_root).job(int(m.group(1)))
    assert job["kind"] == "brand_apply" and json.loads(job["params"]) == {"dir": str(brand_dir.resolve())}
    # pressing again while it waits reuses the job
    r2 = client.post("/brand/apply", data={"csrf": csrf("/brand")}, follow_redirects=False)
    assert r2.headers["location"] == r.headers["location"]


def test_brand_apply_rejected_after_kit_changes(client, csrf, brand_dir, demo_root):
    assert _approve(client, csrf).status_code == 303
    _edit(brand_dir, concept_name="Edited after approval")
    before = len(_store(demo_root).jobs(limit=100))
    r = client.post("/brand/apply", data={"csrf": csrf("/brand")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/brand"
    assert len(_store(demo_root).jobs(limit=100)) == before


def test_brand_checklist_shown_escaped(client, brand_dir):
    (brand_dir / "APPLY_CHECKLIST.md").write_text("# Profile update checklist\n- [ ] <b>bold</b> & more\n")
    t = client.get("/brand").text
    assert '<pre class="log" id="bk-checklist"># Profile update checklist\n- [ ] &lt;b&gt;bold&lt;/b&gt; &amp; more\n</pre>' in t
    assert 'data-copy-target="#bk-checklist"' in t


def test_brand_shows_last_apply_result_redacted(client, csrf, brand_dir, demo_root):
    assert _approve(client, csrf).status_code == 303
    kit = b.load_kit(brand_dir)
    _store(demo_root).kv_set("brand:applied", {"fingerprint": kit.fingerprint(), "at": 1.0, "result": {
        "youtube": {"ok": False, "error": "HTTP 401 https://x.example/?access_token=SECRET123abc"}}})
    t = client.get("/brand").text
    assert "SECRET123abc" not in t and "access_token=***" in t and "Last applied" in t


# ---- csrf -------------------------------------------------------------------------------
def test_brand_posts_require_csrf(client, brand_dir, demo_root):
    fp = b.load_kit(brand_dir).fingerprint()
    assert client.post("/brand/approve", data={"fingerprint": fp, "reviewed": "yes"}).status_code == 403
    assert client.post("/brand/approve", data={"csrf": "bad", "fingerprint": fp, "reviewed": "yes"}).status_code == 403
    assert client.post("/brand/apply", data={}).status_code == 403
    assert _store(demo_root).kv_get("brand:approved") is None
