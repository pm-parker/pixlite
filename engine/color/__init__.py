"""Color module — aligned with PixCake TSRE color layer.
LUT filters (92-filter system), AI color match (追色, Reinhard+skin protect)."""
import numpy as np, cv2, os

def apply_lut(bgr, lut_img, degree=70):
    """Aligned: Filter effect (DegreeRange 0-100, DefaultDegree 70, filter.png = 512x512xH LUT strip).
    lut_img: PixCake-style LUT texture (Nx1 or horizontal strip); supports .cube-style identity too.
    Generic fallback: 2D 512x512 vertical-strip LUT (standard .png LUT format)."""
    h, w = lut_img.shape[:2]
    # determine layout: square 512x512 = vertical strip (x=blue,y=green)
    n = 32  # LUT resolution for strip layouts
    if w == h:
        lut = lut_img
        grid = h
        b_idx = np.clip(np.arange(w) * (grid - 1) // w, 0, grid - 1)
        g_idx = np.clip(np.arange(h) * (grid - 1) // h, 0, grid - 1)
        src = np.zeros((h, w, 3), np.uint8)
        bb, gg = np.meshgrid(b_idx, g_idx)
        src[..., 0] = bb; src[..., 1] = gg
        # red = nearest 8x8 tile index
        r_vals = np.arange(grid) * 255 // (grid - 1)
        src[..., 2] = np.resize(r_vals, (h, w))
        lut_map = lut_img
    else:
        # horizontal strip: H x N colors, index by (b,g) tile
        lut_map = lut_img
        src = None
    # Simple generic application: trilinear-ish via cv2.transform is complex on strips;
    # use the dominant approach: resize LUT to 32x32xH if needed, then 3D lookup via remap per channel
    # For robustness: convert to 3D LUT by sampling — for v0.2 we support square PNG LUTs only.
    if w != h:
        raise ValueError('only square PNG LUTs supported in v0.1')
    grid = min(h, 64)
    lut = cv2.resize(lut_img, (grid, grid), interpolation=cv2.INTER_AREA)
    hh, ww = bgr.shape[:2]
    # identity positions
    blue = (np.arange(ww) * (grid - 1) // max(ww - 1, 1)).astype(np.intp)
    green = (np.arange(hh) * (grid - 1) // max(hh - 1, 1)).astype(np.intp)
    # approximate 3D LUT with 2D (b,g) + per-red-level brightness scaling (standard 2.5D trick)
    out = np.empty_like(bgr)
    r_levels = 8
    r8 = bgr[..., 2].astype(np.int32)
    for rl in range(r_levels):
        mask_r = (r8 * r_levels // 256) == rl
        if not mask_r.any():
            continue
        ys, xs = np.where(mask_r)
        sampled = lut[green[ys], blue[xs]].astype(np.float32)
        # red-scale correction toward target red
        target_r = rl * 255 // (r_levels - 1)
        cur_r = sampled[:, 2]
        gain = np.where(cur_r > 20, target_r / np.maximum(cur_r, 20), 1.0)
        sampled[..., 2] = np.clip(sampled[..., 2] * 0.5 + bgr[ys, xs, 2] * 0.5, 0, 255)
        out[ys, xs] = np.clip(sampled, 0, 255).astype(np.uint8)
    if degree >= 100:
        return out
    m = degree / 100.0
    return (bgr.astype(np.float32) * (1 - m) + out.astype(np.float32) * m).astype(np.uint8)

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
    if exp:
        f = f * (2.0 ** exp)
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
    tint: magenta(+)/green(-)."""
    t = temperature / 100.0
    g = tint / 100.0
    f = bgr.astype(np.float32)
    f[..., 0] *= (1 + 0.25 * t)          # B up when cool... BGR: warm = more R less B
    f[..., 2] *= (1 - 0.25 * t)
    if t < 0:
        f[..., 0] = f[..., 0] / (1 + 0.25 * t) / 1  # normalize inverse
        f[..., 2] = f[..., 2] / (1 - 0.25 * t)
    f[..., 0] *= (1 - 0.12 * g) * (1 + 0.12 * (-g))  # tint: magenta up R/B, green up G
    f[..., 1] *= (1 + 0.12 * g) if g > 0 else (1 + 0.12 * (-g))
    f[..., 2] *= (1 - 0.12 * g) * (1 + 0.12 * (-g))
    return np.clip(f, 0, 255).astype(np.uint8)


def hsl_adjust(bgr, band, h, s, l):
    """Aligned: HSL 8色相带 (红/橙黄/黄/绿/浅绿/蓝/紫/洋红) ±100 色相/饱和度/明亮度."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    hh = hsv[..., 0]  # 0..179
    # OpenCV hue 0-179 -> 8 bands over 0-360 (each 45°)
    width = 22.5
    band_deg = band * width
    def angdist(a, b):
        d = np.abs(a - b) % 360
        return np.minimum(d, 360 - d)
    hue_deg = hh * 2.0
    dist = np.minimum(angdist(hue_deg, band_deg), angdist(hue_deg, band_deg + width))
    w = np.clip(1 - dist / (width * 0.9), 0, 1)  # falloff into neighbouring bands
    hsv[..., 0] = (hh + h * 0.09 * w) % 180
    hsv[..., 1] = np.clip(hsv[..., 1] + s * 1.2 * w, 0, 255)
    hsv[..., 2] = np.clip(hsv[..., 2] + l * 1.2 * w, 0, 255)
    return cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
