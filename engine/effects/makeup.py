"""Makeup effects — B-group C/D upgrade. 高光立体/阴影立体, 眉毛增强左/右,
眼妆增强左/右, 唇妆增强: HSL boosts inside parsing masks with feather."""
import numpy as np, cv2
from ..perception import mask_of
from .eyes import _side_masks


def _boost(bgr, mask, sat=0.3, light=0.15, contrast=0.0):
    """Boost saturation/lightness inside soft mask."""
    if mask.max() == 0:
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] *= (1 + sat)
    hsv[..., 2] *= (1 + light)
    out = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    w = (cv2.GaussianBlur((mask > 128).astype(np.float32), (0, 0), 3))[..., None]
    return np.clip(bgr * (1 - w) + out * w, 0, 255).astype(np.uint8)


def makeup_highlight(bgr, parsing, faces, degree=0.5):
    """高光立体: nose bridge + forehead + cheekbone highlight — LAB L lift on
    T-zone band derived from mesh, feathered."""
    d = float(np.clip(degree, 0, 1))
    from ..perception import landmarks as L
    if d <= 0 or not faces or 'mesh' not in faces[0]:
        return bgr
    pts = faces[0]['mesh']
    m = np.zeros(bgr.shape[:2], np.float32)
    # nose bridge band: forehead(10) -> nose tip(1)
    a, b = pts[L.FOREHEAD_TOP], pts[L.NOSE_TIP]
    for t in np.linspace(0.2, 1.0, 8):
        c = a + (b - a) * t
        cv2.circle(m, tuple(c.astype(int)), max(3, int(faces[0]['box'][3] * 0.05)),
                   1.0, -1)
    # forehead ellipse + cheekbone dots
    fh = faces[0]['box'][3]
    cv2.ellipse(m, tuple(((pts[10] + pts[151]) / 2).astype(int)),
                (int(fh * 0.22), int(fh * 0.10)), 0, 0, 360, 0.7, -1)
    for ci in (L.CHEEK_L, L.CHEEK_R, 425, 205):
        cv2.circle(m, tuple(pts[ci].astype(int)), int(fh * 0.08), 0.6, -1)
    m = np.clip(m, 0, 1)
    m = cv2.GaussianBlur(m, (0, 0), fh * 0.03)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[..., 0] = np.clip(lab[..., 0] + 16 * d * m, 0, 255)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def makeup_shadow(bgr, parsing, faces, degree=0.5):
    """阴影立体: darken sides of nose + under-cheekbone hollow + jaw sides."""
    d = float(np.clip(degree, 0, 1))
    from ..perception import landmarks as L
    if d <= 0 or not faces or 'mesh' not in faces[0]:
        return bgr
    pts = faces[0]['mesh']
    m = np.zeros(bgr.shape[:2], np.float32)
    fh = faces[0]['box'][3]
    # nose sides: offset from bridge line
    a, b = pts[L.FOREHEAD_TOP], pts[L.NOSE_TIP]
    for t in np.linspace(0.35, 0.95, 7):
        c = a + (b - a) * t
        dirv = np.array([b[1] - a[1], -(b[0] - a[0])])
        dirv /= np.linalg.norm(dirv) + 1e-6
        for s in (-1, 1):
            p = c + dirv * fh * 0.055 * s
            cv2.circle(m, tuple(p.astype(int)), int(fh * 0.035), 0.8, -1)
    # cheek hollows: below cheek surface points, outward
    for ci, s in ((L.CHEEK_L, -1), (L.CHEEK_R, 1)):
        p = pts[ci] + np.array([s * fh * 0.05, fh * 0.06])
        cv2.circle(m, tuple(p.astype(int)), int(fh * 0.09), 0.7, -1)
    m = np.clip(m, 0, 1)
    m = cv2.GaussianBlur(m, (0, 0), fh * 0.035)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[..., 0] = np.clip(lab[..., 0] - 18 * d * m, 0, 255)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def brow_enhance(bgr, parsing, degree=0.5, side='l'):
    """眉毛增强左/右: darken + saturate brow hairs in brow mask."""
    d = float(np.clip(degree, 0, 1))
    ml, mr = _side_masks(parsing, 'brow_l', 'brow_r')
    m = ml if side == 'l' else mr
    if d <= 0 or m.max() == 0:
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] *= (1 + 0.35 * d)
    hsv[..., 2] *= (1 - 0.22 * d)
    out = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    w = (cv2.GaussianBlur((m > 128).astype(np.float32), (0, 0), 2) * d)[..., None]
    return np.clip(bgr * (1 - w) + out * w, 0, 255).astype(np.uint8)


def eye_makeup_enhance(bgr, parsing, degree=0.5, side='l'):
    """眼妆增强左/右: saturate + slightly darken the made-up lid area (eye mask
    dilated ring)."""
    d = float(np.clip(degree, 0, 1))
    ml, mr = _side_masks(parsing, 'eye_l', 'eye_r')
    m = ml if side == 'l' else mr
    ring = cv2.subtract(cv2.dilate(m, np.ones((15, 15), np.uint8)), m)
    ring = cv2.bitwise_and(ring, mask_of(parsing, 'skin', dilate=6))
    if d <= 0 or ring.max() == 0:
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] *= (1 + 0.45 * d)
    hsv[..., 2] *= (1 - 0.08 * d)
    out = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    w = (cv2.GaussianBlur((ring > 128).astype(np.float32), (0, 0), 3) * d)[..., None]
    return np.clip(bgr * (1 - w) + out * w, 0, 255).astype(np.uint8)


def lip_makeup_enhance(bgr, parsing, degree=0.5):
    """唇妆增强: saturation + slight darkening for richer lipstick."""
    d = float(np.clip(degree, 0, 1))
    m = cv2.bitwise_or(mask_of(parsing, 'lip_up', dilate=1),
                       mask_of(parsing, 'lip_low', dilate=1))
    if d <= 0 or m.max() == 0:
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] *= (1 + 0.55 * d)
    hsv[..., 2] *= (1 - 0.10 * d)
    out = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    w = (cv2.GaussianBlur((m > 128).astype(np.float32), (0, 0), 2) * d)[..., None]
    return np.clip(bgr * (1 - w) + out * w, 0, 255).astype(np.uint8)
