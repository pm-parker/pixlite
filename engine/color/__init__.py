"""Color module — aligned with PixCake TSRE color layer.
LUT filters (92-filter system), AI color match (追色, Reinhard+skin protect)."""
import cv2
import numpy as np


def _lut_to_cube(lut_img):
    """Square PNG LUT (k*k tiles of n x n, n = k*k; 512x512 -> 8x8 tiles of 64) -> cube[b, g, r] (BGR).
    Layout (verified on fnpack/etc/lut/Filters/FT*/filter.png): tile index = blue level
    (row-major), inside a tile x = red, y = green."""
    h, w = lut_img.shape[:2]
    if w != h:
        raise ValueError('only square PNG LUTs supported')
    k = round(w ** (1.0 / 3))
    n = k * k
    if k * n != w:
        raise ValueError(f'unsupported LUT size {w}x{h} (expect k^3 square, e.g. 512 = 8 tiles x 64)')
    t = lut_img[..., :3].reshape(k, n, k, n, 3)          # [ty, g, tx, r, c]
    return np.ascontiguousarray(t.transpose(0, 2, 1, 3, 4).reshape(n, n, n, 3)).astype(np.float32)


def apply_lut(bgr, lut_img, degree=70):
    """Aligned: Filter effect (DegreeRange 0-100, DefaultDegree 70, filter.png = 512x512 LUT).
    3D LUT lookup with trilinear interpolation.
    BUGFIX: 旧实现按像素的 *坐标* (x->blue, y->green) 而非像素颜色查表, 输出与图像内容
    无关 (identity LUT 下平均误差 ~63/255), 92 个滤镜全部产生错误的空间渐变色。"""
    cube = _lut_to_cube(lut_img)
    n = cube.shape[0]
    f = bgr.astype(np.float32) * ((n - 1) / 255.0)
    i0 = np.floor(f).astype(np.intp)
    np.clip(i0, 0, n - 2, out=i0)
    fr = f - i0
    b0, g0, r0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fb, fg, frr = fr[..., 0:1], fr[..., 1:2], fr[..., 2:3]
    out = np.zeros((*bgr.shape[:2], 3), np.float32)
    for db, wb in ((0, 1 - fb), (1, fb)):
        for dg, wg in ((0, 1 - fg), (1, fg)):
            for dr, wr in ((0, 1 - frr), (1, frr)):
                out += cube[b0 + db, g0 + dg, r0 + dr] * (wb * wg * wr)
    m = float(np.clip(degree, 0, 100)) / 100.0
    res = bgr.astype(np.float32) * (1 - m) + out * m
    return np.clip(res + 0.5, 0, 255).astype(np.uint8)

def color_match(src, ref, protect_skin_mask=None, strength=1.0):
    """Aligned: AI追色 (AIColorMatch / ColorMatchFilter). Reinhard stats transfer in LAB,
    skin-protected: skin regions get reduced transfer (preserve natural tone)."""
    src_f = src.astype(np.float32)
    ref_f = ref.astype(np.float32)
    s = cv2.cvtColor(src_f.astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    r = cv2.cvtColor(ref_f.astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    out = s.copy()
    for ch in range(3):
        s_mean, s_std = s[..., ch].mean(), s[..., ch].std() + 1e-6
        r_mean, r_std = r[..., ch].mean(), r[..., ch].std() + 1e-6
        transferred = (s[..., ch] - s_mean) / s_std * r_std + r_mean
        # limit chroma shift (channels a,b) to avoid tone drift; full transfer on L
        if ch > 0:
            transferred = s[..., ch] + (transferred - s[..., ch]) * 0.6
        out[..., ch] = s[..., ch] + (transferred - s[..., ch]) * strength
    out_img = cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    if protect_skin_mask is not None and protect_skin_mask.any():
        m = (protect_skin_mask.astype(np.float32) / 255)[..., None]
        # skin: apply only 30% of the transfer
        skin_transfer = src.astype(np.float32) * 0.7 + out_img.astype(np.float32) * 0.3
        out_img = src.astype(np.float32) * (1 - m) + skin_transfer * m
    return np.clip(out_img, 0, 255).astype(np.uint8)

def auto_exposure(bgr, target_luma=0.46):
    """Aligned: RawEngine CalBrightness/AutoExposure concept — global L-matching."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    cur = lab[..., 0].mean() / 255.0
    gain = target_luma / max(cur, 1e-3)
    gain = float(np.clip(gain, 0.7, 1.4))
    lab[..., 0] = np.clip(lab[..., 0] * gain, 0, 255)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def auto_white_balance(bgr):
    """Aligned: AICalibrate 白平衡 — gray-world with luminance preservation."""
    f = bgr.astype(np.float32)
    means = f.reshape(-1, 3).mean(axis=0)
    g = means.mean()
    gains = np.where(means > 1e-3, g / np.maximum(means, 1e-3), 1.0)
    gains = np.clip(gains, 0.6, 1.6)
    out = f * gains[None, None, :]
    return np.clip(out, 0, 255).astype(np.uint8)


def tone_adjust(bgr, params):
    """Aligned: 色彩调节 影调 10 滑杆 (±100) — exposure/contrast/highlight/shadow/
    whites/blacks/texture/clarity/vibrance/saturation. Operates in float [0,1]."""
    # temp/tint pass-through to white_balance semantics (cloud preset pf 3000/3003)
    if params.get('temp') or params.get('tint'):
        bgr = white_balance(bgr, params.get('temp', 0), params.get('tint', 0))
    f = bgr.astype(np.float32) / 255.0
    exp = params.get('exposure', 0) / 100.0
    con = params.get('contrast', 0) / 100.0
    hi = params.get('highlight', 0) / 100.0
    sh = params.get('shadow', 0) / 100.0
    wh = params.get('whites', 0) / 100.0
    bl = params.get('blacks', 0) / 100.0
    tex = params.get('texture', 0) / 100.0
    cla = params.get('clarity', 0) / 100.0
    vib = params.get('vibrance', 0) / 100.0
    sat = params.get('saturation', 0) / 100.0
    bri = params.get('brightness', 0) / 100.0
    dhz = params.get('dehaze', 0) / 100.0
    if exp:
        f = f * (2.0 ** exp)
    if bri:   # 亮度: 中间调 gamma (黑白点不动). 前端有此滑杆, 旧引擎未实现 → 拖动无效
        f = np.power(np.clip(f, 0, 1), 1.0 / (1.0 + 0.6 * bri) if bri > 0 else 1.0 - 0.6 * bri)
    if dhz:   # 祛雾: 暗通道估计雾浓度, 去除灰雾并补对比 (前端有此滑杆, 旧引擎未实现)
        dark = cv2.erode(np.clip(f, 0, 1).min(axis=2), np.ones((15, 15), np.uint8))
        haze = cv2.GaussianBlur(dark, (0, 0), 15)[..., None]
        k = 0.8 * dhz
        f = (f - k * haze) / np.maximum(1 - k * haze, 0.2)
    if con:
        f = (f - 0.5) * (1 + 0.8 * con) + 0.5
    if hi:   # highlight recover/boost: weight by brightness
        f = f + hi * 0.6 * np.clip(f - 0.6, 0, 1)
    if sh:
        f = f + sh * 0.6 * np.clip(0.4 - f, 0, 1)
    if wh:
        f = f + wh * 0.3 * np.clip(f - 0.85, 0, 1)
    if bl:
        f = f + bl * 0.3 * np.clip(0.15 - f, 0, 1)
    f = np.clip(f, 0, 1)
    if tex:
        blur = cv2.GaussianBlur((f * 255).astype(np.uint8), (0, 0), 2.0).astype(np.float32) / 255.0
        f = np.clip(f + (f - blur) * tex * 1.2, 0, 1)
    if cla:
        blur = cv2.GaussianBlur((f * 255).astype(np.uint8), (0, 0), 12.0).astype(np.float32) / 255.0
        f = np.clip(f + (f - blur) * cla * 0.8, 0, 1)
    out = (np.clip(f, 0, 1) * 255).astype(np.uint8)
    if vib or sat:
        hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
        s = hsv[..., 1]
        if vib:  # vibrance boosts low-sat pixels more
            s = s + vib * 60.0 * (1 - s / 255.0)
        if sat:
            s = s * (1 + sat)
        hsv[..., 1] = np.clip(s, 0, 255)
        out = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    return out


def white_balance(bgr, temperature=0, tint=0):
    """Aligned: 白平衡 色温/色调滑杆 (±100). temperature: warm(+)/cool(-),
    tint: magenta(+)/green(-).
    BUGFIX: 旧实现 temperature>0 反而 B 增 R 减 (变冷); temperature<0 先乘后除完全抵消
    (负色温无效); tint 对 R/B 用了平方系数, tint>0 实际偏绿, tint<0 三通道同时变亮。"""
    t = float(np.clip(temperature, -100, 100)) / 100.0
    g = float(np.clip(tint, -100, 100)) / 100.0
    f = bgr.astype(np.float32)
    if t:
        f[..., 2] *= (1 + 0.25 * t)      # warm: R up
        f[..., 0] *= (1 - 0.25 * t)      # warm: B down
    if g:
        f[..., 1] *= (1 - 0.12 * g)      # magenta(+): G down / green(-): G up
        f[..., 0] *= (1 + 0.06 * g)
        f[..., 2] *= (1 + 0.06 * g)
    return np.clip(f + 0.5, 0, 255).astype(np.uint8)


# 调色 → 配置文件 (对标 Lightroom/像素蛋糕 "Profile"): 作用在其它调色之前的基础色彩渲染。
# standard 为恒等 (不改图); strength 0..1 与原图线性混合。
PROFILES = ("standard", "texture", "bw", "basic")


def apply_profile(bgr, name="standard", strength=1.0):
    """texture: 中频局部对比 + 轻微提饱和 (质感); bw: 亮度加权黑白 (保留肤色层次);
    basic: 低对比、低饱和的平淡起点 (便于后续自行调色)。"""
    s = float(np.clip(strength, 0, 1))
    if name not in PROFILES or name == "standard" or s <= 0:
        return bgr
    f = bgr.astype(np.float32) / 255.0
    if name == "texture":
        blur = cv2.GaussianBlur(f, (0, 0), 6.0)
        out = np.clip(f + (f - blur) * 0.6, 0, 1)
        hsv = cv2.cvtColor((out * 255).astype(np.uint8), cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 1] = np.clip(hsv[..., 1] * 1.08, 0, 255)
        out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32) / 255.0
    elif name == "bw":
        # 通道混合: 红通道权重偏高, 人像肤色更亮更干净 (类似胶片红滤镜)
        gray = f[..., 2] * 0.40 + f[..., 1] * 0.45 + f[..., 0] * 0.15
        gray = np.clip((gray - 0.5) * 1.08 + 0.5, 0, 1)
        out = np.repeat(gray[..., None], 3, axis=2)
    else:  # basic
        mean = f.mean(axis=2, keepdims=True)
        out = (f - 0.5) * 0.85 + 0.5
        out = mean + (out - mean) * 0.85
        out = np.clip(out, 0, 1)
    res = f * (1 - s) + out * s
    return np.clip(res * 255 + 0.5, 0, 255).astype(np.uint8)


# HSL 8 色相带中心 (度, 0..360): 红/橙/黄/绿/浅绿/蓝/紫/洋红 (与前端 HSL_BANDS 顺序一致)
HSL_CENTERS = (0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 270.0, 300.0)


def _hue_band_weight(hue_deg, band):
    """三角权重: 带中心=1, 线性衰减到相邻带中心=0 (左右跨度可不同)。"""
    c = HSL_CENTERS[band]
    left = (c - HSL_CENTERS[band - 1]) % 360
    right = (HSL_CENTERS[(band + 1) % len(HSL_CENTERS)] - c) % 360
    d = (hue_deg - c + 180.0) % 360.0 - 180.0          # signed -180..180
    span = np.where(d < 0, left, right)
    return np.clip(1 - np.abs(d) / span, 0, 1)


def hsl_adjust(bgr, band, h, s, lum):
    """Aligned: HSL 8色相带 (红/橙/黄/绿/浅绿/蓝/紫/洋红) ±100 色相/饱和度/明亮度.
    BUGFIX: 旧实现每带只占 22.5° (8 带合计只覆盖 0-180°), 蓝/紫/洋红像素 (180-360°)
    任何带都调不动, 而"蓝色"带实际作用在绿色上。"""
    band = int(band) % len(HSL_CENTERS)
    if not (h or s or lum):
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    hh = hsv[..., 0]  # 0..179
    w = _hue_band_weight(hh * 2.0, band)
    w = w * np.clip(hsv[..., 1] / 40.0, 0, 1)   # 近灰像素无可靠色相, 不参与
    hsv[..., 0] = (hh + h * 0.09 * w) % 180
    hsv[..., 1] = np.clip(hsv[..., 1] + s * 1.2 * w, 0, 255)
    hsv[..., 2] = np.clip(hsv[..., 2] + lum * 1.2 * w, 0, 255)
    return cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
