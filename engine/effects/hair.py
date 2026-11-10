"""Hair effects — 发色调整/祛碎发/白发 (B-group local CV).
Hue/sat shift inside hair mask; flyaway = high-frequency strands at hair
boundary suppressed by inward median inpainting; white-hair = bright
low-sat hair pixels remapped toward dominant hair color."""
import numpy as np, cv2
from ..perception import mask_of


def hair_recolor(bgr, parsing, hue=0, sat=0, light=0):
    """发色调整: HSV deltas inside hair mask. UI ±100 -> hue ±20, sat/light ±50."""
    m = mask_of(parsing, 'hair', dilate=2)
    if m.max() == 0 or (hue == 0 and sat == 0 and light == 0):
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    sel = m > 128
    hsv[..., 0][sel] += hue / 100.0 * 20
    hsv[..., 1][sel] *= (1 + sat / 100.0 * 0.5)
    hsv[..., 2][sel] *= (1 + light / 100.0 * 0.35)
    out = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    w = (m.astype(np.float32) / 255)[..., None]
    return (bgr * (1 - w) + out * w).astype(np.uint8)


def flyaway_remove(bgr, parsing, degree=0.5):
    """祛碎发: thin bright/dark strands on hair boundary -> median replacement."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0:
        return bgr
    m = mask_of(parsing, 'hair', dilate=2)
    if m.max() == 0:
        return bgr
    boundary = cv2.subtract(cv2.dilate(m, np.ones((9, 9), np.uint8)), m)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    low = cv2.GaussianBlur(gray, (0, 0), 2.5)
    hf = gray - low
    strand = (np.abs(hf) > 6) & (boundary > 128)
    if not strand.any():
        return bgr
    med = np.stack([cv2.medianBlur(bgr[..., c], 9) for c in range(3)], axis=-1).astype(np.float32)
    w = (strand.astype(np.float32) * d)[..., None]
    return np.clip(bgr * (1 - w) + med * w, 0, 255).astype(np.uint8)


def white_hair_remove(bgr, parsing, degree=0.5):
    """祛白发: bright low-sat hair pixels remapped toward dominant hair color."""
    d = float(np.clip(degree, 0, 1))
    m = mask_of(parsing, 'hair', dilate=1)
    if d <= 0 or m.max() == 0:
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    sel = (m > 128) & (hsv[..., 2] > 150) & (hsv[..., 1] < 60)
    if not sel.any():
        return bgr
    # dominant hair color (median of non-white hair pixels)
    core = (m > 128) & ~sel
    if not core.any():
        return bgr
    dom = np.array([np.median(hsv[..., 0][core]), np.median(hsv[..., 1][core]), np.median(hsv[..., 2][core])])
    hsv[..., 0][sel] = dom[0]
    hsv[..., 1][sel] += (dom[1] - hsv[..., 1][sel]) * d
    hsv[..., 2][sel] += (dom[2] - hsv[..., 2][sel]) * d * 0.7
    return cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
