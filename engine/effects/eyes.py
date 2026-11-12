"""Eye effects needing 478-pt mesh / parsing sides — B-group C/D upgrade.
亮眼左/右 (per-side), 祛双眼皮贴左/右, 眼镜祛反光, 眼神光.
Side convention: 'l'/'r' = IMAGE left/right, resolved at runtime by centroid."""
import cv2
import numpy as np

from ..perception import landmarks as L
from ..perception import mask_of


def _side_masks(parsing, cls_l, cls_r):
    """Return (image-left mask, image-right mask) for a left/right class pair,
    resolved by centroid x (SegFormer l_/r_ labels are SUBJECT-side)."""
    a = mask_of(parsing, cls_l, dilate=1)
    b = mask_of(parsing, cls_r, dilate=1)
    def cx(m):
        _ys, xs = np.where(m > 128)
        return xs.mean() if len(xs) else -1
    if cx(a) > cx(b):
        a, b = b, a
    return a, b


def _eye_masks(parsing):
    return _side_masks(parsing, 'eye_l', 'eye_r')


def eye_brighten_side(bgr, parsing, degree=0.4, side='l'):
    """亮眼左/右: LAB L lift inside ONE eye mask (image side)."""
    d = float(np.clip(degree, 0, 1))
    ml, mr = _eye_masks(parsing)
    m = ml if side == 'l' else mr
    if d <= 0 or m.max() == 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    sel = m > 128
    lab[..., 0][sel] = np.clip(lab[..., 0][sel] * (1 + 0.25 * d), 0, 255)
    out = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    w = (m.astype(np.float32) / 255)[..., None]
    return (bgr * (1 - w) + out * w).astype(np.uint8)


def _lid_band(face, idx_eye, shape, k0=0.45, k1=1.5):
    """Eyelid band above the eye: from mesh eye pts, extruded upward by k of
    eye height. Returns soft uint8 mask."""
    m = np.zeros(shape[:2], np.uint8)
    pts = face['mesh'][idx_eye]
    top = pts.min(axis=0)
    bot = pts.max(axis=0)
    eh = max(bot[1] - top[1], 3)
    x0, x1 = int(top[0]), int(bot[0])
    y0 = int(max(0, top[1] - eh * k1))
    y1 = int(top[1] - eh * (1 - k1) + eh * k0)
    cv2.rectangle(m, (x0, y0), (max(x1, x0 + 2), max(y1, y0 + 2)), 255, -1)
    return cv2.GaussianBlur(m, (0, 0), 3)


def lid_patch_remove(bgr, parsing, faces, degree=0.5, side='l'):
    """祛双眼皮贴: suppress the bright double-eyelid-tape band on the eyelid.
    Tape = thin bright high-freq streak in upper-lid band -> compress toward
    local median L, keep skin texture."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0 or not faces:
        return bgr
    f = faces[0]
    if 'mesh' not in f:
        return bgr
    idx = L.EYE_L_IDX if side == 'l' else L.EYE_R_IDX
    band = _lid_band(f, idx, bgr.shape)
    band = cv2.bitwise_and(band, mask_of(parsing, 'skin', dilate=4))
    if band.max() == 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    Lc = lab[..., 0]
    low = cv2.GaussianBlur(Lc, (0, 0), 3.0)
    hf = Lc - low
    # tape: bright streak (positive HF) in band
    sel = (band > 128) & (hf > 3)
    if not sel.any():
        return bgr
    med = cv2.medianBlur(Lc.astype(np.uint8), 9).astype(np.float32)
    lab[..., 0][sel] = np.clip(Lc[sel] + (med[sel] - Lc[sel]) * 0.8 * d
                               - hf[sel] * 0.5 * d, 0, 255)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def glasses_antiglare(bgr, parsing, degree=0.5):
    """眼镜祛反光: inside eye_g (glasses) mask, compress specular highlights
    (L >> local mean, low sat) toward neighborhood, keep frame texture."""
    d = float(np.clip(degree, 0, 1))
    m = mask_of(parsing, 'eye_g', dilate=3)
    if m.max() == 0:
        # no glasses detected: fall back to the eye region (dilated eye ring) —
        # glare/hotspots near the eyes still get compressed
        m = cv2.bitwise_or(mask_of(parsing, 'eye_l', dilate=5),
                           mask_of(parsing, 'eye_r', dilate=5))
    if d <= 0 or m.max() == 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    Lc = lab[..., 0]
    low = cv2.GaussianBlur(Lc, (0, 0), 15)
    spec = (Lc - low > 8) & (hsv[..., 1] < 80) & (m > 128)
    if not spec.any():
        return bgr
    detail = Lc - cv2.GaussianBlur(Lc, (0, 0), 3)
    lab[..., 0][spec] = np.clip((low + detail * 0.5)[spec], 0, 255)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def catchlight(bgr, parsing, faces, degree=0.5, template='dot'):
    """眼神光: small soft specular dot at 10 o'clock of each pupil, masked to
    iris region. degree 0-1 controls opacity/size."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0 or not faces:
        return bgr
    f = faces[0]
    if 'mesh' not in f:
        return bgr
    out = bgr.astype(np.float32)
    pts = f['mesh']
    for key in ('iris_l', 'iris_r'):
        c = f[key]
        eye_pts = pts[L.EYE_L_IDX] if key == 'iris_l' else pts[L.EYE_R_IDX]
        ew = max(eye_pts[:, 0].max() - eye_pts[:, 0].min(), 4)
        r = max(ew * 0.10 * (0.7 + 0.5 * d), 1.5)
        # offset up-left (10 o'clock)
        cc = (c[0] - r * 0.9, c[1] - r * 0.9)
        yy, xx = np.mgrid[0:bgr.shape[0], 0:bgr.shape[1]].astype(np.float32)
        dist = np.sqrt((xx - cc[0]) ** 2 + (yy - cc[1]) ** 2)
        dot = np.clip(1 - dist / (r * 1.6), 0, 1) ** 1.5
        if dot.max() <= 0:
            continue
        add = 110 * d * dot
        for ch in range(3):
            out[..., ch] += add
    return np.clip(out, 0, 255).astype(np.uint8)
