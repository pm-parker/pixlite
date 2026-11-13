"""Blemish removal — 祛斑祛痘/祛痣/身体祛瑕疵 (B-group local CV).
High-frequency outlier detection inside skin mask -> local median replacement
+ partial texture re-add. mode='mole' targets small strong dark spots,
mode='spot' general blemishes, region='body' targets skin outside the face box."""
import cv2
import numpy as np

from ..perception import mask_of


def skin_region_mask(parsing, faces=None, region='face'):
    if region == 'body':
        m = cv2.bitwise_or(mask_of(parsing, 'skin', dilate=2), mask_of(parsing, 'neck', dilate=2))
        for f in (faces or []):
            if 'box' not in f:
                continue
            x, y, w, h = f['box']
            x0, y0 = int(max(x - w * 0.15, 0)), int(max(y - h * 0.1, 0))
            x1, y1 = int(min(x + w * 1.15, parsing.shape[1])), int(min(y + h * 1.1, parsing.shape[0]))
            m[y0:y1, x0:x1] = 0
        return m
    m = mask_of(parsing, 'skin', dilate=2)
    ex = cv2.bitwise_or(mask_of(parsing, 'eye_l', dilate=6), mask_of(parsing, 'eye_r', dilate=6))
    ex = cv2.bitwise_or(ex, mask_of(parsing, 'mouth', dilate=4))
    return cv2.subtract(m, ex)


def blemish_remove(bgr, parsing, faces=None, degree=0.5, mode='spot', region='face'):
    d = float(np.clip(degree, 0, 1))
    if d <= 0:
        return bgr
    m = skin_region_mask(parsing, faces, region)
    if m.max() == 0:
        return bgr
    k = 9 if mode == 'mole' else 15
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    base = cv2.GaussianBlur(gray, (0, 0), 2.0)
    detail = gray - base
    # 远离遮罩边缘 (中值窗口的一半), 否则中值把头发/眉毛/背景颜色拖进皮肤
    m = cv2.erode(m, np.ones((k // 2 + 1, k // 2 + 1), np.uint8))
    sel = (m > 128)
    if not sel.any():
        return bgr
    # 自适应阈值: 旧代码固定 |detail|>4 (接近噪声/颗粒水平), 正常皮肤纹理几乎全被当成瑕疵抹掉,
    # 祛斑=整脸磨平。改为皮肤区域细节的鲁棒标准差的倍数, 只挑出明显偏离的斑点。
    # 斑点是成片的, 噪声/颗粒是单像素的: 先对细节层做轻微平滑再判定, 单像素噪声不会被当成瑕疵
    blob = cv2.GaussianBlur(detail, (0, 0), 1.0)
    mad = float(np.median(np.abs(blob[sel]))) * 1.4826
    thr = max(7.0 if mode == 'mole' else 4.0, 3.0 * mad)
    spot = (blob < -thr) & sel if mode == 'mole' else (np.abs(blob) > thr) & sel
    spot = (spot.astype(np.uint8) * 255)
    spot = cv2.morphologyEx(spot, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    spot = cv2.dilate(spot, np.ones((3, 3), np.uint8), iterations=1 if mode == 'mole' else 2)
    spot = cv2.bitwise_and(spot, m)
    if spot.max() == 0:
        return bgr
    # per-channel median replacement + partial texture re-add
    rep = np.zeros_like(bgr, np.float32)
    for c in range(3):
        med = cv2.medianBlur(bgr[..., c], k).astype(np.float32)
        rep[..., c] = med + detail * 0.3  # texture re-paste (30%)
    w = (spot.astype(np.float32) / 255)[..., None] * d
    out = bgr.astype(np.float32) * (1 - w) + rep * w
    return np.clip(out, 0, 255).astype(np.uint8)
