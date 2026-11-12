"""FaceSurgery engine — aligned with PixCake FaceSurgery HeadNarrow.
Uses extracted 24-point contour factor tables (presets/facesurgery_headnarrow.json):
normal/left/right profiles keyed by head yaw, pop factors, degree 0-100.
Geometry: face box (YuNet) -> 24-point closed jaw contour -> MLS/forward-warp inward."""
import json
import os

import cv2
import numpy as np

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_profile(path=None):
    p = path or os.path.join(_HERE, '..', 'presets', 'facesurgery_headnarrow.json')
    return json.load(open(p, encoding='utf-8'))

def contour_points_24(box, img_shape):
    """Build 24-point closed contour from face box (YuNet fallback).
    Order mimics PixCake contour arrays: from left temple, down jaw, chin, up right side.
    Returns (24,2) float points in image coords."""
    x, y, fw, fh = box
    cx = x + fw / 2
    # parametric jaw ellipse (semi-axes: fw/2, fh*0.62 centered at (cx, y+fh*0.55))
    acx, acy = cx, y + fh * 0.55
    rx, ry = fw * 0.5, fh * 0.62
    theta = np.linspace(np.pi * 0.92, np.pi * 0.08, 24)  # from left temple over chin to right temple
    pts = np.stack([acx + rx * np.cos(theta), acy + ry * np.sin(theta)], axis=1)
    return pts

def apply_head_narrow(img, faces, degree=50, profile=None, yaw='normal'):
    """HeadNarrow: pull 24-point contour inward. Single composite remap (2.2x faster
    than sequential pushes, same visual result)."""
    if profile is None:
        profile = load_profile()
    if degree <= 0 or not faces:
        return img
    d = min(degree, 100) / 100.0
    nf = np.array(profile['contour'][yaw if yaw in profile['contour'] else 'normal'], np.float32)
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dispX = np.zeros((h, w), np.float32)
    dispY = np.zeros((h, w), np.float32)
    for f in faces:
        if 'box' not in f:
            continue
        x, y, fw, fh = f['box']
        cx, cy = x + fw / 2, y + fh / 2
        pts = contour_points_24(f['box'], img.shape)
        for p, factor in zip(pts, nf):
            dirv = np.array([cx - p[0], cy - p[1]])
            n = np.linalg.norm(dirv)
            if n < 1e-3:
                continue
            dirv = dirv / n
            strength = factor * d * min(h, w) * 0.004
            radius = min(h, w) * 0.055 * factor
            dx, dy = p[0] - xx, p[1] - yy
            dist = np.sqrt(dx * dx + dy * dy)
            fall = np.clip(1 - dist / radius, 0, 1) ** 2
            dispX += dirv[0] * strength * fall
            dispY += dirv[1] * strength * fall
    return cv2.remap(img, xx - dispX, yy - dispY, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
