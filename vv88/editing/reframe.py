"""Horizontal -> vertical reframing that follows the speaker's face.

Faces are sampled every ~0.4 s, smoothed, and turned into a few *held* crop positions
(hard switches like a camera operator, never a jittery pan). If no face is found reliably
the frame is shown whole over a blurred fill, which is always safe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class CropPlan:
    mode: str  # face | center | blur | native
    src_w: int
    src_h: int
    crop_w: int
    crop_h: int
    # Piecewise-constant crop x (source pixels), times relative to the clip start.
    holds: list[tuple[float, float, int]] = field(default_factory=list)
    face_ratio: float = 0.0

    def x_expr(self) -> str:
        if not self.holds:
            return str((self.src_w - self.crop_w) // 2)
        expr = str(self.holds[-1][2])
        for _start, end, x in reversed(self.holds[:-1]):
            expr = f"if(lt(t,{end:.3f}),{x},{expr})"
        return expr


def _even(v: float) -> int:
    return int(v) // 2 * 2


def crop_size(src_w: int, src_h: int, aspect: float) -> tuple[int, int]:
    """Largest crop of the given aspect (w/h) that fits the source."""
    if src_w / src_h > aspect:
        return _even(src_h * aspect), _even(src_h)
    return _even(src_w), _even(src_w / aspect)


def detect_face_track(path: str, start: float, end: float, step: float = 0.4,
                      face_model: str | None = None) -> list[tuple[float, float | None]]:
    """[(t_rel, face_center_x_normalised or None)] over the clip window."""
    try:
        import cv2
    except ImportError:
        log.info("opencv not installed; face tracking disabled (pip install 'vv88[vision]')")
        return []

    detector = None
    yunet = None
    if face_model:
        try:
            yunet = cv2.FaceDetectorYN.create(face_model, "", (320, 320), 0.7)
        except Exception as e:  # noqa: BLE001 - bad model path shouldn't kill the render
            log.warning("could not load YuNet model %s: %s", face_model, e)
    if yunet is None:
        cascade = getattr(getattr(cv2, "data", None), "haarcascades", "") + "haarcascade_frontalface_default.xml"
        detector = cv2.CascadeClassifier(cascade)
        if detector.empty():
            log.info("no Haar cascade bundled with this OpenCV build; face tracking disabled")
            return []

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return []
    track: list[tuple[float, float | None]] = []
    prev_x: float | None = None
    t = start
    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        scale = 640.0 / w
        small = cv2.resize(frame, (640, int(h * scale)))
        faces: list[tuple[float, float, float, float]] = []
        if yunet is not None:
            yunet.setInputSize((small.shape[1], small.shape[0]))
            _, dets = yunet.detect(small)
            if dets is not None:
                faces = [(d[0], d[1], d[2], d[3]) for d in dets]
        else:
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            found = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(36, 36))
            faces = [tuple(map(float, f)) for f in found]
        cx: float | None = None
        if faces:
            # Prefer the biggest face, but stick with the current speaker when sizes are close.
            faces.sort(key=lambda f: f[2] * f[3], reverse=True)
            best = faces[0]
            if prev_x is not None:
                for f in faces[:3]:
                    if f[2] * f[3] >= 0.7 * best[2] * best[3] and abs((f[0] + f[2] / 2) / 640 - prev_x) < 0.1:
                        best = f
                        break
            cx = (best[0] + best[2] / 2) / 640.0
            prev_x = cx
        track.append((t - start, cx))
        t += step
    cap.release()
    return track


def plan_crop(
    src_w: int,
    src_h: int,
    track: list[tuple[float, float | None]],
    duration: float,
    aspect: float = 9 / 16,
    mode: str = "auto",
    dead_zone: float = 0.07,
    min_hold: float = 1.2,
    max_holds: int = 30,
) -> CropPlan:
    cw, ch = crop_size(src_w, src_h, aspect)
    if src_w / src_h <= aspect * 1.05:
        return CropPlan("native", src_w, src_h, cw, ch)
    centre = (src_w - cw) // 2
    if mode == "center":
        return CropPlan("center", src_w, src_h, cw, ch, [(0.0, duration, centre)])
    if mode == "blur":
        return CropPlan("blur", src_w, src_h, cw, ch)

    hits = [(t, x) for t, x in track if x is not None]
    ratio = len(hits) / len(track) if track else 0.0
    if ratio < 0.35:
        fallback = "center" if mode == "face" else "blur"
        plan = CropPlan(fallback, src_w, src_h, cw, ch, face_ratio=ratio)
        if fallback == "center":
            plan.holds = [(0.0, duration, centre)]
        return plan

    # Fill gaps with the last known position, then median-smooth to kill detector noise.
    xs, last = [], hits[0][1]
    for _, x in track:
        last = x if x is not None else last
        xs.append(last)
    arr = np.array(xs, dtype=float)
    k = 5
    padded = np.pad(arr, (k // 2, k // 2), mode="edge")
    smooth = np.array([np.median(padded[i : i + k]) for i in range(arr.size)])
    times = [t for t, _ in track]

    holds: list[list[float]] = [[0.0, 0.0, smooth[0]]]
    pending_since: float | None = None
    for t, x in zip(times, smooth):
        cur = holds[-1]
        if abs(x - cur[2]) > dead_zone:
            pending_since = t if pending_since is None else pending_since
            if t - pending_since >= 0.8 and t - cur[0] >= min_hold:
                cur[1] = pending_since
                holds.append([pending_since, 0.0, x])
                pending_since = None
        else:
            pending_since = None
    holds[-1][1] = duration

    # Re-centre each hold on the median position inside it.
    for h in holds:
        inside = [x for t, x in zip(times, smooth) if h[0] <= t < h[1]]
        if inside:
            h[2] = float(np.median(inside))
    while len(holds) > max_holds:
        i = min(range(len(holds) - 1), key=lambda j: holds[j][1] - holds[j][0])
        holds[i + 1][0] = holds[i][0]
        del holds[i]

    def to_px(xn: float) -> int:
        return int(min(max(xn * src_w - cw / 2, 0), src_w - cw)) // 2 * 2

    return CropPlan(
        "face", src_w, src_h, cw, ch, [(h[0], h[1], to_px(h[2])) for h in holds], face_ratio=ratio
    )
