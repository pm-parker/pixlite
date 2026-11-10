"""Smile warp — 表情微笑嘴角 (B-group local CV).
Mouth-corner anchors estimated from YuNet 5-pt (mouth corners in landmarks
3/4) with box-ratio fallback; radial MLS-style lift of both corners."""
import numpy as np, cv2


def _mouth_corners(face):
    lms = face.get('landmarks')
    if lms is not None and len(lms) >= 5:
        # YuNet 5pt order: eyes 0-3, nose 4? actual: [eyeL, eyeR, nose, mouthL, mouthR]
        return np.array(lms[3], float), np.array(lms[4], float)
    x, y, w, h = face['box']
    my = y + h * 0.72
    return np.array([x + w * 0.25, my]), np.array([x + w * 0.75, my])


def smile_lift(img, faces, degree=50):
    """Lift mouth corners. degree 0-100 -> vertical displacement up to ~4.5% face height."""
    d = float(np.clip(degree, 0, 100)) / 100.0
    if d <= 0 or not faces:
        return img
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dispX = np.zeros((h, w), np.float32)
    dispY = np.zeros((h, w), np.float32)
    for f in faces:
        if 'box' not in f:
            continue
        x, y, fw, fh = f['box']
        cl, cr = _mouth_corners(f)
        radius = min(fw * 0.18, min(h, w) * 0.08)
        lift = fh * 0.045 * d
        for c in (cl, cr):
            dx, dy = c[0] - xx, c[1] - yy
            dist = np.sqrt(dx*dx + dy*dy)
            fall = np.clip(1 - dist / radius, 0, 1) ** 2
            dispY -= lift * fall  # negative = sample from above = content moves up
    return cv2.remap(img, xx - dispX, yy - dispY, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REFLECT)
