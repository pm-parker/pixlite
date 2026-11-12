"""Region fixes — 祛油光/祛黑眼圈/祛眼袋/眼白净化/祛红血丝/唇纹修整 (B-group local CV).
All ops work on SegFormer parsing masks; ROIs derived by shifting/dilating eye
and lip masks. LAB L-channel surgery keeps hue natural."""
import cv2
import numpy as np

from ..perception import mask_of


def _shift(m, dx, dy):
    h, w = m.shape[:2]
    M = np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float32)
    return cv2.warpAffine(m, M, (w, h))


def _eye_mask(parsing, dilate=1):
    return cv2.bitwise_or(mask_of(parsing, 'eye_l', dilate=dilate),
                          mask_of(parsing, 'eye_r', dilate=dilate))


def _below_eye_roi(parsing, k0=0.8, k1=1.9):
    """Band under each eye (dark circles k0~0.9, eye bags ~1.0-1.9 eye heights)."""
    eye = _eye_mask(parsing, dilate=2)
    m = np.zeros_like(eye)
    for side in ('eye_l', 'eye_r'):
        e = mask_of(parsing, side, dilate=2)
        ys, _xs = np.where(e > 128)
        if len(ys) == 0:
            continue
        hgt = max(ys.max() - ys.min(), 4)
        band = cv2.bitwise_or(_shift(e, 0, int(hgt * k0)), _shift(e, 0, int(hgt * k1)))
        m = cv2.bitwise_or(m, band)
    return cv2.bitwise_and(m, mask_of(parsing, 'skin', dilate=4))


def dark_circle(bgr, parsing, degree=0.5):
    """祛黑眼圈: lift L-deficit under eyes toward local mean + neutralize a/b."""
    d = float(np.clip(degree, 0, 1))
    roi = _below_eye_roi(parsing, 0.5, 1.1)
    if d <= 0 or roi.max() == 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[..., 0]
    local = cv2.GaussianBlur(L, (0, 0), 18)
    deficit = np.clip(local - L, 0, None)
    sel = (roi > 128) & (deficit > 2)
    lab[..., 0][sel] = np.clip(L[sel] + deficit[sel] * 0.85 * d, 0, 255)
    # desaturate bluish/purplish cast
    for c in (1, 2):
        v = lab[..., c]
        loc = cv2.GaussianBlur(v, (0, 0), 18)
        lab[..., c][sel] = v[sel] + (loc[sel] - v[sel]) * 0.5 * d
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def eye_bag(bgr, parsing, degree=0.5):
    """祛眼袋: suppress bag-crease high frequency below eyes + soften puff shading."""
    d = float(np.clip(degree, 0, 1))
    roi = _below_eye_roi(parsing, 1.0, 2.0)
    if d <= 0 or roi.max() == 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[..., 0]
    low = cv2.GaussianBlur(L, (0, 0), 4.0)
    detail = L - low
    neg = np.clip(detail, -40, 0)  # bag crease lines are darker
    sel = roi > 128
    lab[..., 0][sel] = np.clip((L - neg * d)[sel], 0, 255)
    # soften the puffy low-freq bulge slightly
    low2 = cv2.GaussianBlur(L, (0, 0), 25)
    lab[..., 0][sel] = np.clip(lab[..., 0][sel] + np.clip(low2 - L, 0, None)[sel] * 0.25 * d, 0, 255)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def _sclera(bgr, eye_mask):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    return (eye_mask > 128) & (s < 70) & (v > 80)


def eye_white(bgr, parsing, degree=0.5):
    """眼白净化: brighten + desaturate sclera pixels inside eye masks."""
    d = float(np.clip(degree, 0, 1))
    m = _eye_mask(parsing, dilate=0)
    if d <= 0 or m.max() == 0:
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    sel = _sclera(bgr, m)
    if not sel.any():
        return bgr
    hsv[..., 1][sel] *= (1 - 0.6 * d)
    hsv[..., 2][sel] = np.clip(hsv[..., 2][sel] + 55 * d * (hsv[..., 2][sel] / 255.0), 0, 255)
    out = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    w = (m.astype(np.float32) / 255)[..., None]
    return (bgr * (1 - w) + out * w).astype(np.uint8)


def red_vein(bgr, parsing, degree=0.5):
    """祛红血丝: desaturate reddish high-sat pixels inside eye masks."""
    d = float(np.clip(degree, 0, 1))
    m = _eye_mask(parsing, dilate=0)
    if d <= 0 or m.max() == 0:
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    sel = (m > 128) & (s > 45) & ((h < 12) | (h > 165)) & (v > 60)
    if not sel.any():
        return bgr
    hsvf = hsv.astype(np.float32)
    hsvf[..., 1][sel] *= (1 - 0.75 * d)
    hsvf[..., 2][sel] = np.clip(hsvf[..., 2][sel] + 30 * d, 0, 255)
    out = cv2.cvtColor(np.clip(hsvf, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    w = (m.astype(np.float32) / 255)[..., None]
    return (bgr * (1 - w) + out * w).astype(np.uint8)


def oil_control(bgr, parsing, degree=0.5):
    """祛油光: specular detection (L above local mean, low saturation) ->
    compress the low-freq highlight while keeping texture/detail intact."""
    d = float(np.clip(degree, 0, 1))
    m = mask_of(parsing, 'skin', dilate=1)
    if d <= 0 or m.max() == 0:
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[..., 1].astype(np.float32)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[..., 0]
    low = cv2.GaussianBlur(L, (0, 0), 12)
    spec = (L - low > 6) & (sat < 85) & (m > 128)
    if not spec.any():
        return bgr
    lower = cv2.GaussianBlur(low, (0, 0), 30)
    detail = L - low
    low2 = low - np.clip(low - lower, 0, None) * 0.7 * d * spec
    lab[..., 0] = np.clip(low2 + detail, 0, 255)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def lip_polish(bgr, parsing, degree=0.5):
    """唇纹修整: edge-preserving smoothing inside lip masks (high-freq lip lines)."""
    d = float(np.clip(degree, 0, 1))
    m = cv2.bitwise_or(mask_of(parsing, 'lip_up', dilate=1), mask_of(parsing, 'lip_low', dilate=1))
    if d <= 0 or m.max() == 0:
        return bgr
    smooth = cv2.bilateralFilter(bgr, 15, 120, 120)
    w = cv2.GaussianBlur((m > 128).astype(np.float32), (0, 0), 2)[..., None] * d
    out = bgr.astype(np.float32) * (1 - w) + smooth.astype(np.float32) * w
    return np.clip(out, 0, 255).astype(np.uint8)
