"""MakeupTransfer — AI 妆容迁移 (对标像素蛋糕"妆容迁移"): BeautyGAN ONNX.
img_A=素颜脸, img_B=带妆参考脸 -> fake_A = A 的脸 + B 的妆.

方案 (残差直贴会被 GAN 光照噪声污染, 弃用): BeautyGAN 只负责生成"妆色调色板"
(fake_A 各妆区 vs 原图各妆区的 LAB 平均色差 — 区域均值天然滤掉逐像素噪声);
贴妆区域用 478 点 landmark 定位 (唇/眼睑环/眉) + 颧骨腮红.
degree 0..1 控制妆感强度. 无 torch, 纯 onnxruntime CPU.
"""
from ..gpu import make_session
import os, glob, cv2, numpy as np

MODEL = os.path.join(os.path.dirname(__file__), '..', 'perception', 'models', 'beautygan.onnx')
REF_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'makeup_refs'))
_cache = {}


def get_session():
    if 's' not in _cache:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _cache['s'] = make_session(MODEL, so)
    return _cache['s']


def list_styles():
    """可用妆容风格名 (assets/makeup_refs 下的 png)."""
    return sorted(os.path.splitext(os.path.basename(p))[0]
                  for p in glob.glob(os.path.join(REF_DIR, '*.png')))


def _align_crop(bgr, box, size=256, pad=1.35):
    """以 face box 为中心裁方形 256x256. 返回 (rgb_float_NCHW, Minv_crop2orig_2x3)."""
    x, y, fw, fh = [float(v) for v in box[:4]]
    cx, cy = x + fw / 2, y + fh / 2
    side = max(fw, fh) * pad
    sc = size / side
    M = np.float32([[sc, 0, (size / 2 - cx * sc)], [0, sc, (size / 2 - cy * sc)]])
    crop = cv2.warpAffine(bgr, M, (size, size), borderMode=cv2.BORDER_REFLECT)
    rgb = crop[..., ::-1].astype(np.float32) / 127.5 - 1.0
    return rgb.transpose(2, 0, 1)[None], cv2.invertAffineTransform(M)


def _forward(a_rgb, b_rgb):
    s = get_session()
    out = s.run(None, {s.get_inputs()[0].name: a_rgb,
                       s.get_inputs()[1].name: b_rgb})[0]  # fake_A: A 的脸 + B 的妆
    return (np.clip(out[0].transpose(1, 2, 0), -1, 1) * 0.5 + 0.5)


_det_cache = None
def get_face_detector_cached():
    global _det_cache
    if _det_cache is None:
        from ..perception import get_face_detector
        _det_cache = get_face_detector()
    return _det_cache


def _dedupe_faces(faces):
    """YuNet 有时对同一张脸输出多个重叠候选 (分数相近) — 按 box IoU 去重,
    同一张脸只留分数最高的一个, 否则同脸会重复上妆放大妆感."""
    out = []
    for f in sorted(faces, key=lambda f: -f.get('score', 1) if isinstance(f, dict) else 1):
        b = f.get('box', f) if isinstance(f, dict) else f[:4]
        dup = False
        for g in out:
            b2 = g.get('box', g)
            x1, y1 = max(b[0], b2[0]), max(b[1], b2[1])
            x2, y2 = min(b[0] + b[2], b2[0] + b2[2]), min(b[1] + b[3], b2[1] + b2[3])
            inter = max(0, x2 - x1) * max(0, y2 - y1)
            if inter > 0.3 * min(b[2] * b[3], b2[2] * b2[3]):
                dup = True
                break
        if not dup:
            out.append(f)
    return out


def _region_mask(pc, idx, dilate=0, ring=0, size=256):
    """在 256 crop 上画 landmark 多边形蒙版; ring>0 时取 dilate 后减自身的环(眼影区)."""
    m = np.zeros((size, size), np.uint8)
    cv2.fillPoly(m, [pc[idx].astype(np.int32)], 255)
    if ring:
        mg = cv2.dilate(m, np.ones((ring, ring), np.uint8))
        m = cv2.subtract(mg, m)
    elif dilate:
        m = cv2.dilate(m, np.ones((dilate, dilate), np.uint8))
    return m


def _lab_shift(before, after, sel):
    """区域平均 LAB 色差 (妆色调色板的一个条目)."""
    if sel.sum() < 30:
        return np.zeros(3, np.float32)
    a = cv2.cvtColor(before, cv2.COLOR_RGB2LAB).astype(np.float32)[sel].mean(0)
    b = cv2.cvtColor(after, cv2.COLOR_RGB2LAB).astype(np.float32)[sel].mean(0)
    return b - a


def _apply_lab_shift(bgr, weight, shift):
    """在 soft weight 蒙版内应用 LAB 色差 shift, 保持纹理与亮度结构.
    OpenCV LAB 通道编码: L 0..255, a/b 0..255 (128=中性)."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[..., 0] = np.clip(lab[..., 0] + shift[0] * weight, 0, 255)
    lab[..., 1] = np.clip(lab[..., 1] + shift[1] * weight, 0, 255)
    lab[..., 2] = np.clip(lab[..., 2] + shift[2] * weight, 0, 255)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


# 妆感三档 profile (像素蛋糕预设本身 fe 全 0 —— 妆在其独立妆造包中,
# 无 pf 数据可分层; 故显式定义可复现档位, 数值按视觉评审标定 2026-11-03)
INTENSITY_PROFILES = {
    1: dict(ab_mul=2.0, l_gloss=0.15, blush=0.35, ring=11, lip_min=2.0),  # 淡妆·日常
    2: dict(ab_mul=4.0, l_gloss=0.30, blush=0.60, ring=15, lip_min=3.0),  # 舞台·默认
    3: dict(ab_mul=6.5, l_gloss=0.45, blush=0.85, ring=19, lip_min=5.0),  # 浓妆·婚拍
}

def transfer_makeup(bgr, faces=None, degree=0.7, style=None, level=2):
    """AI 妆容迁移. degree 0..1 控制妆感强度; level 1/2/3 = 淡妆/舞台/浓妆;
    style 为参考妆图名(默认第一张).
    妆区: 唇/眼睑(眼影)/眉 用 478pt landmark 定位; 颧骨腮红取唇妆同系色调.
    无脸/无模型时原样返回."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0 or not os.path.exists(MODEL):
        return bgr
    prof = INTENSITY_PROFILES[int(level) if level in INTENSITY_PROFILES else 2]
    if faces is None:
        faces = get_face_detector_cached()(bgr)
    if not faces:
        return bgr
    faces = _dedupe_faces(faces)
    styles = list_styles()
    if not styles:
        return bgr
    style = style if style in styles else styles[0]
    ref = cv2.imread(os.path.join(REF_DIR, style + '.png'))
    if ref is None:
        return bgr

    out = bgr.copy()
    H, W = bgr.shape[:2]
    from ..perception import landmarks as L
    for f in (faces if isinstance(faces, list) else [faces]):
        box = f.get('box', f) if isinstance(f, dict) else f[:4]
        lm = L.get_landmarks(bgr, box)
        if lm is None:
            continue
        pts = lm['pts']
        a_in, Minv = _align_crop(bgr, box)
        # 参考图: 人脸检测裁 256; 检测失败则中心裁方形
        try:
            rfaces = get_face_detector_cached()(ref)
            rbox = rfaces[0]['box'] if rfaces else None
        except Exception:
            rbox = None
        if rbox is None:
            h, w = ref.shape[:2]
            side = min(h, w) * 0.9
            rbox = [(w - side) / 2, (h - side) / 2, side, side]
        b_in, _ = _align_crop(ref, rbox)
        made = (_forward(a_in, b_in) * 255).astype(np.uint8)          # rgb 256
        orig = (a_in[0].transpose(1, 2, 0) * 127.5 + 127.5).astype(np.uint8)  # 同几何 rgb
        # landmark 投到 256 crop 坐标
        Mf = cv2.invertAffineTransform(Minv)
        pc = (Mf @ np.hstack([pts, np.ones((len(pts), 1))]).T).T
        fh = float(box[3])

        # ---- 调色板: 妆区相对全图的 LAB 色差 ----
        # (直接色差含 GAN 全局提亮/去饱和伪影; 减去全图均值后剩下的才是妆)
        glob = _lab_shift(orig, made, np.ones((256, 256), bool))
        m_lip = _region_mask(pc, L.LIPS_OUTER, dilate=1)
        m_lid_l = _region_mask(pc, L.EYE_L_IDX, ring=prof['ring'])
        m_lid_r = _region_mask(pc, L.EYE_R_IDX, ring=prof['ring'])
        m_brow_l = _region_mask(pc, L.BROW_L_IDX, dilate=9)
        m_brow_r = _region_mask(pc, L.BROW_R_IDX, dilate=9)
        sh_lip = _lab_shift(orig, made, m_lip > 0) - glob
        sh_lid = (_lab_shift(orig, made, m_lid_l > 0) + _lab_shift(orig, made, m_lid_r > 0)) / 2 - glob
        sh_brow = (_lab_shift(orig, made, m_brow_l > 0) + _lab_shift(orig, made, m_brow_r > 0)) / 2 - glob
        # L: 只保留正向提亮(光泽感), 负值是 GAN 光照伪影要丢弃; a/b: 色调放大让妆感可见
        for sh in (sh_lip, sh_lid, sh_brow):
            sh[0] = max(sh[0], 0.0) * prof['l_gloss']
            sh[1] *= prof['ab_mul']
            sh[2] *= prof['ab_mul']
        # 唇妆约束: 唇色只允许向红 (a+) — 裸唇参考的"去饱和"方向会出蓝唇, 钳到微红
        sh_lip[1] = max(sh_lip[1], prof['lip_min'])
        # 眼影色度限幅: 允许冷调烟熏但不霓虹
        sh_lid[1] = float(np.clip(sh_lid[1], -8, 8))
        sh_lid[2] = float(np.clip(sh_lid[2], -8, 8))

        # ---- 全分辨率 landmark 蒙版 (羽化) ----
        def soft(m_crop, blur):
            mf = cv2.warpAffine(m_crop, Minv, (W, H))
            s = cv2.GaussianBlur((mf > 0).astype(np.float32), (0, 0), blur)
            return np.clip(s, 0, 1)

        # 唇妆
        out = _apply_lab_shift(out, soft(m_lip, max(2, fh * 0.015)) * d, sh_lip)
        # 眼影: 眼睑环 (landmark 定位)
        m_lid = cv2.bitwise_or(m_lid_l, m_lid_r)
        out = _apply_lab_shift(out, soft(m_lid, max(2, fh * 0.02)) * d, sh_lid)
        # 眉: 用眉色差加深
        m_brow = cv2.bitwise_or(m_brow_l, m_brow_r)
        out = _apply_lab_shift(out, soft(m_brow, max(2, fh * 0.015)) * d * 0.8, sh_brow)

        # ---- 腮红: 唇妆同系色调, 颧骨椭圆 (landmark 定位) ----
        sh_blush = sh_lip.copy()
        sh_blush[0] *= 0.5
        blush_m = np.zeros((H, W), np.float32)
        for ci in (L.CHEEK_L, L.CHEEK_R):
            c = pts[ci]
            cv2.circle(blush_m, tuple(c.astype(int)), int(fh * 0.16), 0.8, -1)
        blush_m = cv2.GaussianBlur(blush_m, (0, 0), fh * 0.08)
        out = _apply_lab_shift(out, np.clip(blush_m, 0, 1) * d * prof['blush'], sh_blush)
    return out
