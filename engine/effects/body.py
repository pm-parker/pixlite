"""Body shaping & body fixes — B-group D. 全身美型基础 (瘦腰/瘦腿/长腿),
祛副乳, 祛妊娠纹. Uses cloth/neck/bg parsing classes as body-region proxies
(no dedicated body segmentation model)."""
import cv2
import numpy as np

from ..perception import mask_of


def _body_proxy_mask(parsing):
    """Body region proxy: cloth + neck + background is NOT body; use cloth+neck
    eroded, else lower 60% of image below face area."""
    m = cv2.bitwise_or(mask_of(parsing, 'cloth', dilate=0),
                       mask_of(parsing, 'neck', dilate=0))
    return m


def _face_anchors(faces):
    """[(chin_y, face_h, face_cx)] for every detected face.
    身体区域按各自人脸框定位: 旧代码按整图固定比例取带, 头像照里正好落在脸/眼睛上;
    多人合影时也不能用同一个中心线去收所有人。"""
    out = []
    for f in faces or []:
        if 'box' in f:
            x, y, fw, fh = (float(v) for v in f['box'])
            out.append((y + fh, fh, x + fw / 2))
    return out


def body_slim(bgr, parsing, faces, part='waist', degree=0.5):
    """瘦腰/瘦腿: horizontal inward liquify inside each person's body region.
    waist = 1.5-3.5 face heights below the chin; legs = from 4 face heights below the chin
    to the bottom; columns limited to ±1.8 face heights around the face. 没有人脸或带不在
    画面内 (头像/半身照) 时不处理。"""
    d = float(np.clip(degree, 0, 1))
    if d <= 0:
        return bgr
    h, w = bgr.shape[:2]
    body = _body_proxy_mask(parsing) > 128
    if not body.any():   # 旧: 无身体时整图作为身体 → 背景被拉扯
        return bgr
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    disp = np.zeros((h, w), np.float32)
    for chin, fh, fcx in _face_anchors(faces):
        y0, y1 = (chin + 1.5 * fh, chin + 3.5 * fh) if part == 'waist' else (chin + 4.0 * fh, float(h))
        y0, y1 = int(max(0, y0)), int(min(h, y1))
        x0, x1 = int(max(0, fcx - 1.8 * fh)), int(min(w, fcx + 1.8 * fh))
        if y1 - y0 < 0.5 * fh or x1 <= x0:
            continue
        _ys, xs = np.nonzero(body[y0:y1, x0:x1])
        if len(xs) < 50:
            continue
        xs = xs + x0
        cx = float(np.median(xs))
        bw = float(np.percentile(xs, 95) - np.percentile(xs, 5)) or 1.0
        win = np.zeros((h, w), np.float32)
        win[y0:y1, x0:x1] = 1.0
        win = cv2.GaussianBlur(win, (0, 0), max(4.0, 0.15 * fh))   # 带边缘平滑过渡
        win *= cv2.GaussianBlur(cv2.dilate(body.astype(np.float32), np.ones((5, 5), np.uint8)),
                                (0, 0), max(6.0, 0.05 * bw))
        # 平滑的向内位移: 中线两侧连续 (tanh), 采样点取在更外侧 → 轮廓向中线收窄。
        # BUGFIX: 旧代码 remap(xx - disp) 从内侧采样, 实际把身体向外推宽 (100px → 108px)。
        disp += np.tanh((xx - cx) / (0.25 * bw)) * (0.05 * bw * d) * win
    if not disp.any():
        return bgr
    return cv2.remap(bgr, xx + disp, yy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


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
    anchors = _face_anchors(faces)
    if not anchors:
        return bgr
    body = _body_proxy_mask(parsing)
    m = cv2.dilate(cv2.bitwise_or(body, mask_of(parsing, 'skin', dilate=0)), np.ones((9, 9), np.uint8))
    roi = np.zeros((h, w), np.uint8)
    # 腋下区域 (每个人): 下巴以下 0.3~1.6 个脸高, 左右 ±1.6 脸高, 避开胸口中线 ±0.6 脸高。
    # BUGFIX: 旧代码固定取整图 25%~50% 高度, 头像照里正好是眼睛/眉毛, 被当成暗部提亮。
    for chin, fh, fcx in anchors:
        y0, y1 = int(max(0, chin + 0.3 * fh)), int(min(h, chin + 1.6 * fh))
        for xa, xb in ((fcx - 1.6 * fh, fcx - 0.6 * fh), (fcx + 0.6 * fh, fcx + 1.6 * fh)):
            xa, xb = int(max(0, xa)), int(min(w, xb))
            if y1 > y0 and xb > xa:
                roi[y0:y1, xa:xb] = 255
    roi = cv2.bitwise_and(roi, m)
    roi[mask_of(parsing, 'hair', 'bg', dilate=3) > 0] = 0
    if roi.max() == 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    Lc = lab[..., 0]
    local = cv2.GaussianBlur(Lc, (0, 0), 20)
    # 软权重 (旧: >5 的硬阈值逐像素提亮 → 衣褶上出现斑块): 区域边缘羽化, 暗度渐入
    soft = cv2.GaussianBlur(roi.astype(np.float32) / 255.0, (0, 0), 6) * (roi > 0)
    wgt = soft * np.clip((local - Lc - 3.0) / 10.0, 0, 1)
    if not wgt.any():
        return bgr
    lab[..., 0] = np.clip(Lc + (local - Lc) * 0.8 * d * wgt, 0, 255)
    adj = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    out = bgr.copy()
    sel = wgt > 0   # 只写回作用区域, 区域外不经 LAB 往返 (避免全图 ±1 误差)
    out[sel] = adj[sel]
    return out


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
