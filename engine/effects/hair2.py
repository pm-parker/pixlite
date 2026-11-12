"""Hair effects 2 — 头发柔顺, 发缝填充, 颅顶调整, 发量左右, 发色统一."""
from __future__ import annotations

import cv2
import numpy as np

from ..perception import mask_of


def hair_smooth(bgr, parsing, degree=0.5):
    """头发柔顺: edge-preserving smoothing inside hair mask + boundary
    morphological cleanup."""
    d = float(np.clip(degree, 0, 1))
    m = mask_of(parsing, 'hair', dilate=1)
    if d <= 0 or m.max() == 0:
        return bgr
    smooth = cv2.bilateralFilter(bgr, 15, 100, 100)
    smooth = cv2.bilateralFilter(smooth, 9, 60, 60)
    low = cv2.GaussianBlur(bgr, (0, 0), 2.5)
    tex = cv2.subtract(bgr, low)
    smooth = cv2.add(smooth, tex * 0.45)
    w = (cv2.GaussianBlur((m > 128).astype(np.float32), (0, 0), 3) * d)[..., None]
    return np.clip(bgr * (1 - w) + smooth * w, 0, 255).astype(np.uint8)


def hair_gap_fill(bgr, parsing, degree=0.5):
    """发缝填充: dark parting band inside hair -> median fill toward local hair
    luminance (moderate)."""
    d = float(np.clip(degree, 0, 1))
    m = mask_of(parsing, 'hair', dilate=1)
    if d <= 0 or m.max() == 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    Lc = lab[..., 0]
    local = cv2.GaussianBlur(Lc, (0, 0), 10)
    gap = (m > 128) & (local - Lc > 8)
    if not gap.any():
        return bgr
    fill = cv2.medianBlur(bgr, 15)
    w = (cv2.GaussianBlur(gap.astype(np.float32), (0, 0), 2) * d * 0.85)[..., None]
    return np.clip(bgr * (1 - w) + fill * w, 0, 255).astype(np.uint8)


def crown_lift(bgr, parsing, faces, degree=0.5):
    """颅顶调整: radial upward lift of the scalp (top hair region) via forward
    warp — raises crown silhouette."""
    d = float(np.clip(degree, 0, 1))
    m = mask_of(parsing, 'hair', dilate=1)
    if d <= 0 or m.max() == 0 or not faces:
        return bgr
    f = faces[0]
    ys, xs = np.where(m > 128)
    if len(ys) == 0:
        return bgr
    fh = f['box'][3]
    # scalp center: top of hair, x follows face center
    cx = (f['box'][0] + f['box'][2] / 2) if 'box' in f else xs.mean()
    top_y = ys.min()
    ccy = top_y + fh * 0.10
    h, w = bgr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dist = np.sqrt((xx - cx) ** 2 + (yy - ccy) ** 2)
    radius = fh * 0.55
    fall = np.clip(1 - dist / radius, 0, 1) ** 2
    hm = cv2.GaussianBlur((m > 128).astype(np.float32), (0, 0), 8)
    dispY = (fh * 0.035 * d * fall * (yy < ccy + fh * 0.35) * hm).astype(np.float32)
    return cv2.remap(bgr, xx, yy - dispY, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REFLECT)


def hair_volume(bgr, parsing, degree=0.5, side='l'):
    """发量调整左/右: push hair boundary radially outward on one side."""
    d = float(np.clip(degree, 0, 1))
    m = mask_of(parsing, 'hair', dilate=1)
    if d <= 0 or m.max() == 0 or not faces_exist(bgr, parsing):
        return bgr
    return _volume_side(bgr, m, parsing, d, side)


def faces_exist(bgr, parsing):
    return True  # caller ensures faces ctx


def _volume_side(bgr, m, parsing, d, side):
    h, w = bgr.shape[:2]
    ys, xs = np.where(m > 128)
    if len(xs) == 0:
        return bgr
    # 旧代码固定用图像中线 w/2: 人物不居中时左右判反/整片头发被推向一侧。按注释意图用发区质心
    face_cx = float(xs.mean())
    band = cv2.subtract(cv2.dilate(m, np.ones((25, 25), np.uint8)),
                        cv2.erode(m, np.ones((5, 5), np.uint8)))
    if side == 'l':
        band[:, int(face_cx):] = 0
    else:
        band[:, :int(face_cx)] = 0
    if band.max() == 0:
        return bgr
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    horiz = np.sign(xx - face_cx if side == 'r' else face_cx - xx)
    fall = cv2.GaussianBlur((band > 128).astype(np.float32), (0, 0), 6)
    dispX = horiz * 8.0 * d * fall
    return cv2.remap(bgr, xx - dispX, yy, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REFLECT)


def hair_unify(bgr, parsing, degree=0.5):
    """发色统一: pull per-pixel hair color toward dominant hair color
    (median H/S/V of the hair core), preserving luminance detail."""
    d = float(np.clip(degree, 0, 1))
    m = mask_of(parsing, 'hair', dilate=1)
    if d <= 0 or m.max() == 0:
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    sel = m > 128
    # dominant = circular median of hue, median of sat/val
    hh = hsv[..., 0][sel]
    hist = np.bincount(((hh * 2).astype(int) % 360), minlength=360)
    dom_h = (hist.argmax() / 2.0)
    dom_s = np.median(hsv[..., 1][sel])
    out = hsv.copy()
    # angular blend for hue
    diff = out[..., 0] - dom_h
    diff = (diff + 90) % 180 - 90
    out[..., 0] = (out[..., 0] - diff * d) % 180
    out[..., 1] += (dom_s - out[..., 1]) * d * 0.7
    res = cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    w = (cv2.GaussianBlur((m > 128).astype(np.float32), (0, 0), 3) * d)[..., None]
    return np.clip(bgr * (1 - w) + res * w, 0, 255).astype(np.uint8)
