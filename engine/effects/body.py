"""Body shaping & body fixes — B-group D. 全身美型基础 (瘦腰/瘦腿/长腿),
祛副乳, 祛妊娠纹. Uses cloth/neck/bg parsing classes as body-region proxies
(no dedicated body segmentation model)."""
import numpy as np, cv2
from ..perception import mask_of
from . import wrinkle as _w


def _body_proxy_mask(parsing):
    """Body region proxy: cloth + neck + background is NOT body; use cloth+neck
    eroded, else lower 60% of image below face area."""
    m = cv2.bitwise_or(mask_of(parsing, 'cloth', dilate=0),
                       mask_of(parsing, 'neck', dilate=0))
    return m


def body_slim(bgr, parsing, faces, part='waist', degree=0.5):
    """瘦腰/瘦腿: horizontal inward liquify inside body proxy region bands.
    waist = 45-75% height below face; legs = 75-100%."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0:
        return bgr
    h, w = bgr.shape[:2]
    body = _body_proxy_mask(parsing)
    if body.max() == 0:
        body = np.full((h, w), 255, np.uint8)  # whole-image fallback band
    ys, xs = np.where(body > 128)
    if len(xs) == 0:
        return bgr
    cx = xs.mean()
    if part == 'waist':
        y0, y1 = int(h * 0.45), int(h * 0.78)
    else:  # legs
        y0, y1 = int(h * 0.72), h
    band = np.zeros((h, w), np.float32)
    band[y0:y1, :] = 1.0
    band *= cv2.GaussianBlur((body > 128).astype(np.float32), (0, 0), 12)
    if band.max() == 0:
        return bgr
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    horiz = np.sign(xx - cx)
    dispX = (horiz * (w * 0.025 * d) * band).astype(np.float32)
    return cv2.remap(bgr, xx - dispX, yy, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REFLECT)


def leg_lengthen(bgr, parsing, degree=0.5):
    """长腿: vertical stretch of lower region via inverse-scale remap."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0:
        return bgr
    h, w = bgr.shape[:2]
    y0 = int(h * 0.62)
    k = 1.0 + 0.06 * d
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    newy = yy.copy()
    sel = yy >= y0
    newy[sel] = y0 + (yy[sel] - y0) / k
    # blend the stretch over a transition band to avoid a visible seam
    tr = (yy >= y0) & (yy < y0 + h * 0.08)
    newy[tr] = yy[tr] + (newy[tr] - yy[tr]) * ((yy[tr] - y0) / (h * 0.08))
    return cv2.remap(bgr, xx, newy, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REFLECT)


def axilla_fix(bgr, parsing, faces, degree=0.5):
    """祛副乳: brighten the dark armpit bulge area — dark low-freq region at the
    sides of the chest (cloth/skin boundary, upper body side thirds)."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0:
        return bgr
    h, w = bgr.shape[:2]
    body = _body_proxy_mask(parsing)
    m = cv2.bitwise_or(body, mask_of(parsing, 'skin', dilate=0))
    roi = np.zeros((h, w), np.uint8)
    roi[int(h * 0.25):int(h * 0.50), :] = 255
    roi[:, int(w * 0.40):int(w * 0.60)] = 0  # exclude center chest
    roi = cv2.bitwise_and(roi, cv2.dilate(m, np.ones((9, 9), np.uint8)))
    if roi.max() == 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    Lc = lab[..., 0]
    local = cv2.GaussianBlur(Lc, (0, 0), 20)
    dark = (roi > 128) & (local - Lc > 5)
    if not dark.any():
        return bgr
    lab[..., 0][dark] = np.clip(Lc[dark] + (local - Lc)[dark] * 0.8 * d, 0, 255)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def stretch_marks(bgr, parsing, faces, degree=0.5):
    """祛妊娠纹: abdomen streak high-freq suppression — reuse wrinkle engine on
    a belly ROI band (0.45-0.85 height), line-direction lowpass."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0:
        return bgr
    h, w = bgr.shape[:2]
    roi = np.zeros((h, w), np.uint8)
    roi[int(h * 0.42):int(h * 0.88), :] = 255
    roi = cv2.bitwise_and(roi, cv2.bitwise_or(mask_of(parsing, 'skin', dilate=2),
                                              _body_proxy_mask(parsing)))
    if roi.max() == 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    Lc = lab[..., 0]
    low = cv2.GaussianBlur(Lc, (0, 0), 3.0)
    detail = Lc - low
    neg = np.clip(detail, -35, 0)  # streaks are darker lines
    sel = roi > 128
    lab[..., 0][sel] = np.clip((Lc - neg * d)[sel], 0, 255)
    # soften residual low-freq banding
    low2 = cv2.GaussianBlur(lab[..., 0], (0, 0), 25)
    lab[..., 0][sel] = np.clip(lab[..., 0][sel] +
                               np.clip(low2 - Lc, 0, None)[sel] * 0.15 * d, 0, 255)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
