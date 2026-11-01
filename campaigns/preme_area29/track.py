"""Face-aware horizontal tracking for the crop window."""
import cv2, numpy as np

_front = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
_prof = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")


def _faces(img):
    g = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    out = []
    for c in (_front, _prof):
        for (x, y, w, h) in c.detectMultiScale(g, 1.12, 6, minSize=(42, 42)):
            out.append((x + w / 2, w * h))
    gf = cv2.flip(g, 1)  # right-facing profiles
    for (x, y, w, h) in _prof.detectMultiScale(gf, 1.12, 6, minSize=(42, 42)):
        out.append((g.shape[1] - (x + w / 2), w * h))
    return out


def track(src, keep, crop_w, step=0.5, src_w=1920):
    """Return [(out_t, x0_px)] crop-left positions on the output timeline."""
    cap = cv2.VideoCapture(src)
    pts, acc = [], 0.0
    scale = 960 / src_w
    for si, (a, b) in enumerate(keep):
        seg = []
        t = a
        while t <= b + 1e-6:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, fr = cap.read()
            cx = None
            if ok:
                fr = cv2.resize(fr, (960, 540))
                f = _faces(fr)
                if f:
                    big = max(ar for _, ar in f)
                    f = [(x, ar) for x, ar in f if ar > 0.25 * big]  # ignore small background faces
                    cx = sum(x * ar for x, ar in f) / sum(ar for _, ar in f) / scale
            seg.append([acc + (t - a), cx])
            t += step
        # fill gaps from neighbours, default centre
        xs = [c for _, c in seg if c is not None]
        if not xs:
            for p in seg:
                p[1] = src_w / 2
        else:
            last = xs[0]
            for p in seg:
                if p[1] is None:
                    p[1] = last
                last = p[1]
        # median-3 then velocity limit (300 px/s) inside the segment
        raw = [p[1] for p in seg]
        med = [float(np.median(raw[max(0, i - 2): i + 3])) for i in range(len(raw))]
        sm = [med[0]]
        for v in med[1:]:
            d = max(-300 * step, min(300 * step, v - sm[-1]))
            sm.append(sm[-1] + d)
        for p, v in zip(seg, sm):
            x0 = int(max(0, min(src_w - crop_w, v - crop_w / 2)))
            pts.append((round(p[0], 3), x0, si))
        acc += b - a
    return pts


def x_expr(pts):
    """Piecewise-linear crop x expression in t (output time)."""
    terms = []
    for (t0, x0, s0), (t1, x1, s1) in zip(pts, pts[1:]):
        if s0 != s1:
            continue  # hard cut: hold, next segment starts fresh
        terms.append(f"gte(t,{t0})*lt(t,{t1})*({x0}+({x1 - x0})*(t-{t0})/{t1 - t0:.3f})")
    # segment tails / single points: hold value from the last sample of each segment
    by_seg = {}
    for t, x, s in pts:
        by_seg.setdefault(s, []).append((t, x))
    holds = []
    seg_list = sorted(by_seg)
    for i, s in enumerate(seg_list):
        tl, xl = by_seg[s][-1]
        t_end = by_seg[seg_list[i + 1]][0][0] if i + 1 < len(seg_list) else 99999
        holds.append(f"gte(t,{tl})*lt(t,{t_end})*{xl}")
    return "+".join(terms + holds)
