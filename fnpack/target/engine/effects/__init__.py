"""Effects layer — aligned with PixCake MagicEngine + TSAI pipelines.
Core retouch ops implemented with OpenCV (CPU) mirroring extracted GLSL semantics."""
import numpy as np, cv2
from ..perception import mask_of, CLS
from . import blemish, region_fix, wrinkle, hair
from . import eyes, teeth, makeup, hair2, body
from ..perception import landmarks as LM

def skin_retouch(bgr, parsing, strength=0.6, texture_keep=0.35):
    """Aligned: FaceDermabrasionHLF / SkinRetune. GLSL ref: SkinAdjustV2.frag.
    Edge-preserving smoothing on skin mask + high-freq texture restore."""
    skin = mask_of(parsing, 'skin', dilate=3)
    if skin.max() == 0:
        return bgr
    # guided-filter style: bilateral for edge preservation (CPU substitute for guided filter)
    smooth = cv2.bilateralFilter(bgr, 9, 60, 60)
    smooth = cv2.bilateralFilter(smooth, 5, 40, 40)
    # texture layer = original - lowfreq, re-add * texture_keep
    low = cv2.GaussianBlur(bgr, (0, 0), 3.0)
    texture = cv2.subtract(bgr, low)
    smooth = cv2.add(smooth, (texture * texture_keep).astype(np.uint8))
    m = (skin.astype(np.float32) / 255.0)[..., None] * strength
    out = bgr.astype(np.float32) * (1 - m) + smooth.astype(np.float32) * m
    return np.clip(out, 0, 255).astype(np.uint8)

def teeth_whiten(bgr, parsing, degree=0.5):
    """Aligned: TeethWhitenV3_PixCook (degree from effect.json)."""
    mask = mask_of(parsing, 'mouth', dilate=1)
    if mask.max() == 0:
        return bgr
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    # desaturate + brighten teeth zone
    s, v = hsv[..., 1], hsv[..., 2]
    sel = mask > 128
    s[sel] *= (1 - 0.7 * degree)
    v[sel] += 60 * degree * (v[sel] / 255.0)
    hsv[..., 1], hsv[..., 2] = s, v
    out = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    m = (mask.astype(np.float32) / 255)[..., None]
    return (bgr * (1 - m) + out * m).astype(np.uint8)

def eye_brighten(bgr, parsing, degree=0.4):
    """Aligned: EyeWhiten / BrightEye."""
    m = cv2.bitwise_or(mask_of(parsing, 'eye_l', dilate=1), mask_of(parsing, 'eye_r', dilate=1))
    if m.max() == 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    sel = m > 128
    lab[..., 0][sel] = np.clip(lab[..., 0][sel] * (1 + 0.25 * degree), 0, 255)
    out = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    mm = (m.astype(np.float32) / 255)[..., None]
    return (bgr * (1 - mm) + out * mm).astype(np.uint8)

def skin_tone_hsl(bgr, parsing, hsl, preset='nuanhuaipi'):
    """Aligned: SkinAdjust 8-seg hue/sat/luma arrays (all_effects.json SkinAdjust).
    hsl: [h0..h7, s0..s7, l0..l7] delta arrays applied in HSV."""
    # 防御: 前端历史版本发 8 元素 [h,s,l,0...] → 旧语义 s/l 会落空, 按全波段 delta 展开
    if len(hsl) == 8:
        hsl = list(hsl[:1]) * 8 + list(hsl[1:2]) * 8 + list(hsl[2:3]) * 8
    hsl = (list(hsl) + [0] * 24)[:24]
    h = hsl[0:8]
    s = hsl[8:16]
    l = hsl[16:24]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    skin = (mask_of(parsing, 'skin', dilate=2) > 128)
    hh = hsv[..., 0].astype(int)  # 0-179
    for i in range(8):
        # 末带上界取 180: 旧代码 8*22=176, 色相 176-179 (正红) 的肤色像素不被任何带覆盖
        band = (hh >= i * 22) & (hh < ((i + 1) * 22 if i < 7 else 180)) & skin
        hsv[..., 0][band] += h[i]
        hsv[..., 1][band] += s[i] * 2.55
        hsv[..., 2][band] += l[i] * 2.55
    # 色相是环形 0..179: 旧代码直接 clip 到 0..255, 超过 179 的值被 OpenCV 当非法色相
    hsv[..., 0] = np.mod(hsv[..., 0], 180)
    out = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    mm = (mask_of(parsing, 'skin', dilate=2).astype(np.float32) / 255)[..., None]
    return (bgr * (1 - mm) + out * mm).astype(np.uint8)

def grain(bgr, density=0.12, luminance=0.29):
    """Aligned: RealGrain (NoiseBlend.frag semantics): luminance-correlated noise."""
    noise = np.random.default_rng(7).normal(0, 255 * density * 0.5, bgr.shape[:2])
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    corr = 1 - np.abs(gray - luminance)
    noise *= corr
    return np.clip(bgr.astype(np.float32) + noise[..., None], 0, 255).astype(np.uint8)
