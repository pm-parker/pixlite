"""Smile management + teeth ops on the 478-pt mesh — B-group C/D upgrade.
笑容管理 (5 levels), 露龈调整左/右, 牙齿排齐, 牙齿祛瑕疵."""
import numpy as np, cv2
from ..perception import mask_of
from ..perception import landmarks as L


def smile_manage(bgr, faces, level=3):
    """笑容管理: amplifies the natural smile — mouth corners 61/291 lifted
    AND cheeks (50/280) pushed slightly up-out for a genuine smile shape.
    level 1-5 -> corner lift 0.015..0.055 of face height."""
    d = float(np.clip(level, 0, 5)) / 5.0
    if d <= 0 or not faces:
        return bgr
    f = faces[0]
    if 'mesh' not in f:
        return bgr
    h, w = bgr.shape[:2]
    pts = f['mesh']
    box = f['box']; fh = box[3]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dispX = np.zeros((h, w), np.float32); dispY = np.zeros((h, w), np.float32)
    radius = fh * 0.22
    anchors = [(pts[L.MOUTH_L], (0, -fh * 0.045 * d)),
               (pts[L.MOUTH_R], (0, -fh * 0.045 * d)),
               (pts[L.CHEEK_L], (-fh * 0.012 * d, -fh * 0.02 * d)),
               (pts[L.CHEEK_R], (fh * 0.012 * d, -fh * 0.02 * d))]
    for c, (dx, dy) in anchors:
        ddx, ddy = c[0] - xx, c[1] - yy
        dist = np.sqrt(ddx * ddx + ddy * ddy)
        fall = np.clip(1 - dist / radius, 0, 1) ** 2
        dispX += dx * fall; dispY += dy * fall
    return cv2.remap(bgr, xx - dispX, yy - dispY, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REFLECT)


def _teeth_mask(bgr, parsing, faces):
    """Teeth = bright low-sat pixels inside mouth region (parsing mouth ∩
    mesh lip polygon), excluding lips (reddish). Mouth closed -> inner-lip
    polygon fallback (mild effect, no visible teeth)."""
    m = mask_of(parsing, 'mouth', dilate=1)
    if faces and 'mesh' in faces[0]:
        poly = L.poly_mask(faces[0]['mesh'], L.LIPS_OUTER, bgr.shape, feather=2)
        if m.max() == 0:
            # parser found no open-mouth class: use eroded lip polygon interior
            m = cv2.erode(poly, np.ones((3, 3), np.uint8))
        else:
            m = cv2.bitwise_and(m, poly)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    teeth = (m > 128) & (hsv[..., 1] < 110) & (hsv[..., 2] > 110)
    if not teeth.any():
        teeth = m > 128
    return m, teeth


def gum_fix(bgr, parsing, faces, degree=0.5, side='l'):
    """露龈调整左/右: pull the upper-lip top edge DOWN over the gum on one
    half of the mouth (forward-warp downward in half-mouth ROI)."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0 or not faces:
        return bgr
    f = faces[0]
    if 'mesh' not in f:
        return bgr
    pts = f['mesh']; box = f['box']; fh = box[3]
    mx = (pts[L.MOUTH_L][0] + pts[L.MOUTH_R][0]) / 2
    my = (pts[L.MOUTH_L][1] + pts[L.MOUTH_R][1]) / 2
    half_w = (pts[L.MOUTH_R][0] - pts[L.MOUTH_L][0]) * 0.30
    cx = (pts[L.MOUTH_L][0] + mx) / 2 if side == 'l' else (mx + pts[L.MOUTH_R][0]) / 2
    h, w = bgr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    radius = fh * 0.13
    ddx, ddy = cx - xx, my - yy
    dist = np.sqrt(ddx * ddx + ddy * ddy)
    fall = np.clip(1 - dist / radius, 0, 1) ** 2
    # restrict: sample from above => content shifts down, only above lip line
    zone = (yy < my + fh * 0.02)
    dispY = fh * 0.035 * d * fall * zone
    return cv2.remap(bgr, xx, yy - dispY, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REFLECT)


def teeth_align(bgr, parsing, faces, level=3):
    """牙齿排齐 (5 档): inside teeth mask, blend with an elliptically-warped
    (straightened) version + median smoothing to visually align crowded teeth.
    level 1-5 -> blend strength 0.2..0.8."""
    lv = float(np.clip(level, 0, 5)) / 5.0
    if lv <= 0 or not faces:
        return bgr
    f = faces[0]
    if 'mesh' not in f:
        return bgr
    m, teeth = _teeth_mask(bgr, parsing, faces)
    if not teeth.any():
        # mouth closed / no visible teeth: fall back to inner-mouth mask
        teeth = m > 128
    pts = f['mesh']
    # warp interior teeth region: map by cubic distortion toward mouth-center line
    h, w = bgr.shape[:2]
    cx, cy = (pts[L.MOUTH_L] + pts[L.MOUTH_R]) / 2
    mw = pts[L.MOUTH_R][0] - pts[L.MOUTH_L][0]
    strength = 0.06 * lv
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx = (xx - cx) / (mw / 2 + 1e-6)
    mapx = xx + np.sign(nx) * (nx ** 2) * mw * strength
    warped = cv2.remap(bgr, mapx.astype(np.float32), yy, cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_REFLECT)
    med = np.stack([cv2.medianBlur(warped[..., c], 5) for c in range(3)], -1)
    soft = (0.5 * warped + 0.5 * med).astype(np.float32)
    wmask = (cv2.GaussianBlur(teeth.astype(np.float32), (0, 0), 1.5) * lv)[..., None]
    return np.clip(bgr * (1 - wmask) + soft * wmask, 0, 255).astype(np.uint8)


def teeth_fix(bgr, parsing, faces, degree=0.5):
    """牙齿祛瑕疵: inside teeth mask, remove high-frequency spots/outliers
    (food bits, cracks) via local median on outlier pixels."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0 or not faces:
        return bgr
    m, teeth = _teeth_mask(bgr, parsing, faces)
    if not teeth.any():
        teeth = m > 128  # mouth closed fallback
    if not teeth.any():
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    Lc = lab[..., 0]
    med = cv2.medianBlur(Lc.astype(np.uint8), 7).astype(np.float32)
    dev = np.abs(Lc - med)
    spot = teeth & (dev > 8)
    if not spot.any():
        return bgr
    bgr = bgr.astype(np.float32)
    for c in range(3):
        medc = cv2.medianBlur(bgr[..., c].astype(np.uint8), 7).astype(np.float32)
        bgr[..., c] = bgr[..., c] * (1 - d) + medc * d
    return np.clip(bgr, 0, 255).astype(np.uint8)
