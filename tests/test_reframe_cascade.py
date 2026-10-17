"""Face-detection data lookup for OpenCV builds without cv2.data (Termux, distro packages)."""

import types

from vv88.editing.reframe import haar_cascade_path

NAME = "haarcascade_frontalface_default.xml"


def test_pip_builds_use_cv2_data(tmp_path):
    (tmp_path / NAME).write_text("<xml/>")
    cv2 = types.SimpleNamespace(data=types.SimpleNamespace(haarcascades=str(tmp_path) + "/"))
    assert haar_cascade_path(cv2) == str(tmp_path / NAME)


def test_system_builds_fall_back_to_prefix_share(tmp_path, monkeypatch):
    share = tmp_path / "share" / "opencv4" / "haarcascades"
    share.mkdir(parents=True)
    (share / NAME).write_text("<xml/>")
    monkeypatch.setenv("PREFIX", str(tmp_path))
    assert haar_cascade_path(types.SimpleNamespace()) == str(share / NAME)  # no cv2.data at all


def test_missing_everywhere_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("PREFIX", str(tmp_path))
    monkeypatch.setattr("vv88.editing.reframe.os.path.isfile", lambda p: False)
    assert haar_cascade_path(types.SimpleNamespace()) == ""
