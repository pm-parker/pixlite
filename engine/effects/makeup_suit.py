"""MakeupSuit — 像素蛋糕原厂 24 套整套妆 (Suit/MU0000000X) 参数配方复刻.

数据源: presets/suit_recipes.json — 24 套 × 355 条部位配方
(part/engine/degree/tex/opacity/roi/mix)。贴图 PNG 为加密资产无法直接使用,
本模块用参数驱动: MixColor(非黑)做 LAB 色度偏移, 黑色 MixColor 的条目回退到
部位默认妆色; Opacity×DefaultDegree×degree 合成权重; 478 landmark 定位区域
(腮红→颧骨椭圆, 修容→颧骨下侧, 眉→眉多边形, 睫毛/眼线→眼睑环, 眼影→眼睑
外环, 唇→LIPS_OUTER)。同部位多条目(正/侧脸贴图)取 max 权重合并为一次上妆。
"""
import os, json, cv2, numpy as np

RECIPES_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                            '..', '..', 'presets', 'suit_recipes.json'))
_cache = {}

LIP_PART = 'Lipstick'   # 混淆目录名 = 唇妆 (LipStick/LipColor engine)

# 部位默认 LAB 色差 (无 MixColor 时的回退妆色; OpenCV Lab: a/b 128=中性)
# shift = (dL, da, db)
DEFAULT_SHIFT = {
    'Blusher':  (2, 10, 3),     # 腮红: 红润微提亮
    'Bronzer':  (-7, 5, 9),     # 修容: 暖褐收暗
    'Eyebrow':  (-14, 3, 4),    # 眉: 加深
    'Eyelash':  (-20, 2, 3),    # 睫毛: 深化
    'Eyeliner': (-26, 2, 3),    # 眼线: 最深
    'Eyemakeup': (-6, 6, 6),    # 眼影: 柔和加深带暖调
    LIP_PART:   (-5, 16, 5),    # 唇: 向红 (a+)
}
# 无 MixColor 条目的部位基准 opacity (配方黑色条目通常 opacity 20-80, 但无色
# 信息; 用配方自带 opacity 参与权重, 色用默认板)

def load_recipes():
    if 'r' not in _cache:
        _cache['r'] = json.load(open(RECIPES_PATH, encoding='utf-8'))
    return _cache['r']


def list_suits():
    """[{id, name, n_parts, colored}] 原厂整套妆清单."""
    r = load_recipes()
    out = []
    for sid in sorted(r.keys()):
        entries = r[sid]
        colored = sum(1 for e in entries
                      if e.get('mix') and (e['mix'][0] or e['mix'][1] or e['mix'][2]))
        parts = sorted({e['part'] for e in entries})
        out.append({'id': sid, 'name': sid, 'n_entries': len(entries),
                    'colored': colored, 'parts': parts})
    return out


def _mix_to_shift(mix_bgr01):
    """MixColor (配方为 [r,g,b,a]) → LAB shift: a/b 直接用其色度 (相对中性 128),
    L 向该色亮度方向靠. 该色越饱和妆色越明确."""
    bgr = np.uint8([[[mix_bgr01[2], mix_bgr01[1], mix_bgr01[0]]]])  # rgb→bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)
    da = float(lab[1] - 128)
    db = float(lab[2] - 128)
    # 深色(如深红唇膏 L 低)适度压 L; 亮色微提
    dl = float(np.clip(lab[0] - 128, -20, 10)) * 0.3
    return np.array([dl, da, db], np.float32)


def _part_masks(part, pts, fh, H, W):
    """部位 → [(mask_u8, blur_px)] 全分辨率蒙版列表 (左右各一)."""
    from ..perception import landmarks as L
    masks = []

    def poly(idx, dilate=0, ring=0):
        m = np.zeros((H, W), np.uint8)
        cv2.fillPoly(m, [pts[idx].astype(np.int32)], 255)
        if ring:
            mg = cv2.dilate(m, np.ones((ring, ring), np.uint8))
            m = cv2.subtract(mg, m)
        elif dilate:
            m = cv2.dilate(m, np.ones((dilate, dilate), np.uint8))
        return m

    if part == 'Blusher':
        m = np.zeros((H, W), np.float32)
        for ci in (L.CHEEK_L, L.CHEEK_R):
            cv2.circle(m, tuple(pts[ci].astype(int)), int(fh * 0.18), 1.0, -1)
        # 大羽化防块状边缘 (视觉评审 2026-11-03 v3: 腮红边缘略硬)
        masks.append((np.uint8(m * 255), max(2, fh * 0.22)))
    elif part == 'Bronzer':   # 颧骨下侧偏外 (修容区)
        m = np.zeros((H, W), np.float32)
        for ci, dx in ((L.CHEEK_L, -0.04), (L.CHEEK_R, 0.04)):
            c = pts[ci] + [dx * fh, 0.07 * fh]
            cv2.circle(m, tuple(c.astype(int)), int(fh * 0.20), 1.0, -1)
        masks.append((np.uint8(m * 255), max(2, fh * 0.12)))
    elif part == 'Eyebrow':
        for idx in (L.BROW_L_IDX, L.BROW_R_IDX):
            masks.append((poly(idx, dilate=max(3, int(fh * 0.03))), max(2, fh * 0.02)))
    elif part == 'Eyelash':
        for idx in (L.EYE_L_IDX, L.EYE_R_IDX):
            masks.append((poly(idx, dilate=max(3, int(fh * 0.025))), max(2, fh * 0.015)))
    elif part == 'Eyeliner':
        for idx in (L.EYE_L_IDX, L.EYE_R_IDX):
            masks.append((poly(idx, dilate=max(2, int(fh * 0.012))), max(2, fh * 0.008)))
    elif part == 'Eyemakeup':  # 眼睑外环 (眼影区)
        ring = max(9, int(fh * 0.10))
        for idx in (L.EYE_L_IDX, L.EYE_R_IDX):
            masks.append((poly(idx, ring=ring), max(2, fh * 0.035)))
    elif part == LIP_PART:
        masks.append((poly(L.LIPS_OUTER, dilate=1), max(2, fh * 0.02)))
    return masks


def apply_suit(bgr, faces=None, suit_id='MU00000001', degree=0.7):
    """按原厂整套妆配方上妆. degree 0..1 全局强度. 无脸/无配方原样返回."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0:
        return bgr
    recipes = load_recipes()
    if suit_id not in recipes:
        return bgr
    if faces is None:
        from .makeup_transfer import get_face_detector_cached, _dedupe_faces
        faces = get_face_detector_cached()(bgr)
    faces = faces or []
    from .makeup_transfer import _dedupe_faces
    faces = _dedupe_faces(faces)

    # ---- 配方聚合: 同部位多条目 → max 权重 + 优先取非黑 MixColor ----
    agg = {}   # part -> {'w': float, 'shift': np.array3, 'n': int}
    for e in recipes[suit_id]:
        part = e['part']
        w = float(e.get('opacity') or 0) / 100.0 * (float(e.get('degree') or 0) / 100.0)
        mix = e.get('mix')
        colored = bool(mix) and (mix[0] or mix[1] or mix[2])
        a = agg.setdefault(part, {'w': 0.0, 'shift': None, 'n': 0})
        a['n'] += 1
        a['w'] = max(a['w'], w)
        if colored and a['shift'] is None:
            a['shift'] = _mix_to_shift(mix)
    for part, a in agg.items():
        if a['shift'] is None:
            a['shift'] = np.array(DEFAULT_SHIFT.get(part, (0, 4, 4)), np.float32)
        # 安全限幅: 防"刷白/过暗"(视觉评审 2026-11-03: Eyelash 原 mix 色过暖致上眼皮发白)
        sh = np.array([np.clip(a['shift'][0], -22, 6),
                       np.clip(a['shift'][1], -12, 14),
                       np.clip(a['shift'][2], -12, 12)], np.float32)
        # 深色部位 L 衰减 0.8 (评审: 22套暗部丢细节 → 整体压暗过重)
        if sh[0] < -10:
            sh[0] *= 0.8
        a['shift'] = sh
        # 部位增益: 无色条目只有默认板, 稍放大保证可见
        gain = 1.15 if part not in ('Eyelash', 'Eyebrow') else 1.0
        if part == 'Blusher':
            gain = 2.2   # 原厂配方 opacity 低(20), 视觉评审偏淡 → 腮红增益(评审标定 2026-11-03)
        a['w'] = float(np.clip(a['w'] * gain, 0, 1))
    # 眼区互斥: Eyelash/Eyeliner/Eyemakeup 同在上眼睑, 只保留权重最高的一层做彩色, 其余减弱
    eye_parts = [p for p in ('Eyelash', 'Eyeliner', 'Eyemakeup') if p in agg]
    if eye_parts:
        top = max(eye_parts, key=lambda p: agg[p]['w'])
        for p in eye_parts:
            if p != top:
                agg[p]['w'] *= 0.35   # 次要眼层大幅减弱, 防叠加过曝
                agg[p]['shift'][0] *= 0.5

    out = bgr.copy()
    H, W = bgr.shape[:2]
    from ..perception import landmarks as L
    for f in (faces if isinstance(faces, list) else [faces]):
        box = f.get('box', f) if isinstance(f, dict) else f[:4]
        lm = L.get_landmarks(bgr, box)
        if lm is None:
            continue
        pts = lm['pts']
        fh = float(box[3])
        for part, a in agg.items():
            if part == 'Eyepupil':   # 美瞳贴图无颜色配方, 跳过 (记录在案)
                continue
            for m, blur in _part_masks(part, pts, fh, H, W):
                soft = cv2.GaussianBlur((m > 0).astype(np.float32), (0, 0), blur)
                w = np.clip(soft, 0, 1) * d * a['w']
                if w.max() <= 0:
                    continue
                lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB).astype(np.float32)
                lab[..., 0] = np.clip(lab[..., 0] + a['shift'][0] * w, 0, 255)
                lab[..., 1] = np.clip(lab[..., 1] + a['shift'][1] * w, 0, 255)
                lab[..., 2] = np.clip(lab[..., 2] + a['shift'][2] * w, 0, 255)
                out = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    return out
