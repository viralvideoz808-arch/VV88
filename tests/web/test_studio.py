import json

from vv88.store import Store

MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 256
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 256


def _store(demo_root):
    return Store(demo_root / "data" / "vv88.db")


def _last_job(demo_root):
    row = _store(demo_root).jobs(limit=1)[0]
    return row["kind"], json.loads(row["params"])


def _job_count(demo_root):
    return len(_store(demo_root).jobs(limit=1000))


def _uploads(demo_root):
    d = demo_root / "data" / "uploads"
    return sorted(d.iterdir()) if d.is_dir() else []


def test_page_renders(client):
    r = client.get("/studio")
    assert r.status_code == 200
    html = r.text
    assert 'enctype="multipart/form-data"' in html and 'name="upload"' in html
    assert "Auto — optimizer picks" in html and "Karaoke pop" in html
    # brainrot_stack needs a gameplay folder, which the demo doesn't have
    assert 'value="brainrot_stack"  disabled' in html or 'value="brainrot_stack" disabled' in html
    assert "Needs a gameplay folder" in html
    assert 'value="5"' in html  # default clips_per_source
    assert "Recent clip jobs" in html and "yt-dlp: Video unavailable" in html
    assert 'data-copy="https://www.youtube.com/watch?v=demo1"' in html
    assert "<script>" not in html


def test_campaign_is_preselected_with_its_sources(client):
    r = client.get("/studio", params={"campaign": "money-talks"})
    assert r.status_code == 200
    assert '<option value="money-talks" selected>' in r.text
    assert 'data-copy="https://www.youtube.com/watch?v=demo1"' in r.text
    r = client.get("/studio", params={"campaign": "money-talks", "source": "https://www.youtube.com/watch?v=demo1"})
    assert 'value="https://www.youtube.com/watch?v=demo1"' in r.text
    assert client.get("/studio", params={"campaign": "nope"}).status_code == 200


def test_campaign_source_needs_no_rights_box(client, csrf, demo_root):
    r = client.post("/studio/clip", data={"csrf": csrf("/studio"), "source": "https://www.youtube.com/watch?v=demo1",
                                          "campaign_id": "money-talks", "n": "3", "style": "karaoke_pop",
                                          "post": "on"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    kind, params = _last_job(demo_root)
    assert kind == "clip"
    assert params == {"source": "https://www.youtube.com/watch?v=demo1", "campaign_id": "money-talks", "n": 3,
                      "style": "karaoke_pop", "i_have_rights": False, "post": True}


def test_other_link_needs_rights_box(client, csrf, demo_root):
    token = csrf("/studio")
    before = _job_count(demo_root)
    r = client.post("/studio/clip", data={"csrf": token, "source": "https://www.youtube.com/watch?v=other",
                                          "campaign_id": "money-talks", "n": "2"})
    assert r.status_code == 400 and "isn&#39;t one of the campaign&#39;s source videos" in r.text
    assert 'value="https://www.youtube.com/watch?v=other"' in r.text  # form keeps the input
    r = client.post("/studio/clip", data={"csrf": token, "source": "https://www.youtube.com/watch?v=other",
                                          "n": "2"})
    assert r.status_code == 400 and "permission" in r.text
    assert _job_count(demo_root) == before
    r = client.post("/studio/clip", data={"csrf": token, "source": "https://www.youtube.com/watch?v=other",
                                          "n": "2", "style": "", "i_have_rights": "on"}, follow_redirects=False)
    assert r.status_code == 303
    _, params = _last_job(demo_root)
    assert params["i_have_rights"] is True and params["campaign_id"] == "" and params["style"] == ""
    assert params["n"] == 2 and params["post"] is False


def test_local_path_is_resolved(client, csrf, demo_root):
    clip = next((demo_root / "data" / "clips").rglob("*.mp4"))
    weird = str(clip.parent) + "/../" + clip.parent.name + "/" + clip.name
    r = client.post("/studio/clip", data={"csrf": csrf("/studio"), "source": weird, "n": "1",
                                          "i_have_rights": "on"}, follow_redirects=False)
    assert r.status_code == 303
    _, params = _last_job(demo_root)
    assert params["source"] == str(clip.resolve())


def test_upload_is_saved_under_a_random_name(client, csrf, demo_root):
    r = client.post("/studio/clip", data={"csrf": csrf("/studio"), "n": "4", "i_have_rights": "on",
                                          "campaign_id": "late-night"},
                    files={"upload": ("../../evil name.MP4", MP4, "video/mp4")}, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    files = _uploads(demo_root)
    assert len(files) == 1 and files[0].suffix == ".mp4" and "evil" not in files[0].name
    assert files[0].read_bytes() == MP4
    _, params = _last_job(demo_root)
    assert params["source"] == str(files[0].resolve()) and params["campaign_id"] == "late-night"
    assert params["n"] == 4 and params["i_have_rights"] is True


def test_webm_upload(client, csrf, demo_root):
    r = client.post("/studio/clip", data={"csrf": csrf("/studio"), "n": "1", "i_have_rights": "on"},
                    files={"upload": ("clip.webm", WEBM, "video/webm")}, follow_redirects=False)
    assert r.status_code == 303 and _uploads(demo_root)[0].suffix == ".webm"


def test_bad_uploads_are_rejected(client, csrf, demo_root):
    token = csrf("/studio")
    before = _job_count(demo_root)
    cases = [
        ({"n": "1"}, ("a.mp4", MP4, "video/mp4"), "Uploads need the box"),
        ({"n": "1", "i_have_rights": "on"}, ("notes.txt", b"hello", "text/plain"), "Uploads must be videos"),
        ({"n": "1", "i_have_rights": "on"}, ("fake.mp4", b"#!/bin/sh\nrm -rf /\n" * 4, "video/mp4"),
         "doesn&#39;t look like a video"),
        ({"n": "1", "i_have_rights": "on"}, ("pic.mp4", MP4, "image/png"), "doesn&#39;t look like a video"),
        ({"n": "1", "i_have_rights": "on"}, ("empty.mp4", b"", "video/mp4"), "empty"),
        ({"n": "1", "i_have_rights": "on", "source": "https://x.com/v"}, ("a.mp4", MP4, "video/mp4"), "not both"),
    ]
    for data, upload, message in cases:
        r = client.post("/studio/clip", data={"csrf": token, **data}, files={"upload": upload})
        assert r.status_code == 400, (data, upload[0])
        assert message in r.text, (data, upload[0], message)
    assert _uploads(demo_root) == []  # nothing saved, no .part leftovers
    assert _job_count(demo_root) == before


def test_invalid_fields_are_friendly(client, csrf, demo_root):
    token = csrf("/studio")
    before = _job_count(demo_root)
    ok = {"csrf": token, "source": "https://www.youtube.com/watch?v=demo1", "campaign_id": "money-talks"}
    cases = [
        ({"n": "0"}, "from 1 to 10"),
        ({"n": "11"}, "from 1 to 10"),
        ({"n": "three"}, "from 1 to 10"),
        ({"style": "sparkles"}, "Unknown style"),
        ({"style": "brainrot_stack"}, "needs a gameplay folder"),
        ({"campaign_id": "nope"}, "doesn&#39;t exist"),
        ({"source": ""}, "Paste a video link"),
        ({"source": "javascript:alert(1)"}, "http:// or https://"),
        ({"source": "file:///etc/passwd"}, "http:// or https://"),
        ({"source": "relative/video.mp4"}, "full path"),
        ({"source": "/etc/passwd"}, "Local files must be videos"),
        ({"source": "/definitely/not/here.mp4"}, "no file at"),
    ]
    for override, message in cases:
        r = client.post("/studio/clip", data={**ok, "n": "3", **override})
        assert r.status_code == 400, override
        assert message in r.text, (override, message)
    assert _job_count(demo_root) == before


def test_archived_campaign_cannot_be_used(client, csrf, demo_root):
    store = _store(demo_root)
    c = store.campaign("money-talks")
    c.status = "inactive"
    store.upsert_campaigns([c])
    r = client.post("/studio/clip", data={"csrf": csrf("/studio"), "source": "https://www.youtube.com/watch?v=demo1",
                                          "campaign_id": "money-talks", "n": "1"})
    assert r.status_code == 400 and "archived" in r.text


def test_post_requires_csrf(client, demo_root):
    before = _job_count(demo_root)
    r = client.post("/studio/clip", data={"source": "https://www.youtube.com/watch?v=demo1",
                                          "campaign_id": "money-talks", "n": "1"})
    assert r.status_code == 403
    r = client.post("/studio/clip", data={"csrf": "nope", "n": "1", "i_have_rights": "on"},
                    files={"upload": ("a.mp4", MP4, "video/mp4")})
    assert r.status_code == 403
    assert _job_count(demo_root) == before and _uploads(demo_root) == []


def test_oversized_upload_is_refused_before_parsing(client, csrf):
    r = client.post("/studio/clip", content=b"x", headers={"content-length": str(5 * 1024 ** 3),
                                                            "content-type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413


def test_save_upload_enforces_limit(tmp_path):
    import io

    import pytest

    from vv88.web.pages.studio import UploadRejected, save_upload

    with pytest.raises(UploadRejected):
        save_upload(io.BytesIO(MP4 * 10), ".mp4", tmp_path, limit=len(MP4) * 2)
    assert list(tmp_path.iterdir()) == []
    out = save_upload(io.BytesIO(MP4), ".mp4", tmp_path)
    assert out.read_bytes() == MP4 and out.parent == tmp_path
