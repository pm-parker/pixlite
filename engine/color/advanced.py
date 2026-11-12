"""Advanced color ops — F6: curves / levels / color_grading / selective_color / color_editor.
Aligned with PixCake color panel tabs (曲线/色阶/颜色分级/可选颜色/色彩编辑器)."""
import cv2
import numpy as np


# ---------- F6.1 Curves ----------
def _interp_lut(points):
    """points: [[x,y],...] in 0..255 (x monotonic). Build 256-entry LUT via monotone cubic (Fritsch-Carlson)."""
    pts = sorted([list(map(float, p)) for p in points])
    # dedupe x
    ded = [pts[0]]
    for p in pts[1:]:
        if p[0] > ded[-1][0]:
            ded.append(p)
    if len(ded) < 2:
        return np.clip(np.arange(256), 0, 255).astype(np.uint8)
    x = np.array([p[0] for p in ded])
    y = np.array([p[1] for p in ded])
    n = len(x)
    dx = np.diff(x)
    dy = np.diff(y)
    slope = np.zeros(n)
    slope[1:-1] = np.where(dx[:-1] * dx[1:] > 0,
                           (dy[:-1] / dx[:-1] + dy[1:] / dx[1:]) / 2, 0)
    slope[0] = dy[0] / dx[0] if dx[0] > 0 else 0
    slope[-1] = dy[-1] / dx[-1] if dx[-1] > 0 else 0
    # Fritsch-Carlson monotonicity clamp
    for i in range(n - 1):
        if dx[i] == 0:
            slope[i] = slope[i + 1] = 0
        else:
            a = slope[i] * dx[i] / dy[i] if dy[i] != 0 else 0
            b = slope[i + 1] * dx[i] / dy[i] if dy[i] != 0 else 0
            if a * a + b * b > 9:
                t = 3 / np.sqrt(a * a + b * b)
                slope[i] = t * a * dy[i] / dx[i]
                slope[i + 1] = t * b * dy[i] / dx[i]
    xs = np.arange(256, dtype=np.float64)
    out = np.interp(xs, x, y)  # linear fallback base
    for i in range(n - 1):
        m = (xs >= x[i]) & (xs <= x[i + 1])
        h = x[i + 1] - x[i]
        if h <= 0:
            continue
        t = (xs[m] - x[i]) / h
        h00 = 2 * t ** 3 - 3 * t ** 2 + 1
        h10 = t ** 3 - 2 * t ** 2 + t
        h01 = -2 * t ** 3 + 3 * t ** 2
        h11 = t ** 3 - t ** 2
        out[m] = h00 * y[i] + h10 * h * slope[i] + h01 * y[i + 1] + h11 * h * slope[i + 1]
    return np.clip(out, 0, 255).astype(np.uint8)

IDENT = [[0, 0], [255, 255]]

def curves(bgr, rgb=None, r=None, g=None, b=None):
    """RGB master + per-channel spline curves. Each arg = list of [x,y] control points (0..255)."""
    rgb = rgb or IDENT
    lutm = _interp_lut(rgb)
    out = cv2.LUT(bgr, lutm)  # apply master to all channels
    for _name, pts, ch in (('r', r, 2), ('g', g, 1), ('b', b, 0)):  # name 仅表通道顺序, 未用
        if pts:
            lut = _interp_lut(pts)
            c = cv2.LUT(out[..., ch], lut)
            merged = out.copy()
            merged[..., ch] = c
            out = merged
    return out

# ---------- F6.2 Levels ----------
def levels(bgr, in_black=0, in_white=255, gamma=1.0, out_black=0, out_white=255):
    lut = np.arange(256, dtype=np.float64)
    lut = (lut - in_black) * 255.0 / max(in_white - in_black, 1e-6)
    lut = np.clip(lut, 0, 255) / 255.0
    lut = np.power(lut, 1.0 / max(gamma, 1e-6))
    lut = out_black + lut * (out_white - out_black)
    lut = np.clip(lut, 0, 255).astype(np.uint8)
    return cv2.LUT(bgr, lut)

# ---------- F6.3 Color Grading (Lift-Gamma-Gain, shadow/mid/high wheels) ----------
def _zone_weights(lab_l):
    """Three luminance zone weights (normalized to sum 1) from L channel float 0..255."""
    lum = lab_l.astype(np.float32) / 255.0
    ws = np.exp(-((lum - 0.15) ** 2) / 0.045)   # shadows
    wm = np.exp(-((lum - 0.50) ** 2) / 0.08)    # midtones
    wh = np.exp(-((lum - 0.90) ** 2) / 0.045)   # highlights
    s = ws + wm + wh + 1e-6
    return ws / s, wm / s, wh / s

def color_grading(bgr, shadow=None, mid=None, high=None):
    """Each zone: {h: -180..180 hue shift, s: -100..100 sat, l: -100..100 light}.
    Hue/sat done in LAB a/b space (hue≈rotate a,b; sat≈scale a,b)."""
    shadow = shadow or {}
    mid = mid or {}
    high = high or {}
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    ws, wm, wh = _zone_weights(lab[..., 0])
    zones = [(shadow, ws), (mid, wm), (high, wh)]
    a = lab[..., 1] - 128.0
    b = lab[..., 2] - 128.0
    L = lab[..., 0]
    for z, w in zones:
        h = float(z.get('h', 0))
        s = float(z.get('s', 0))
        li = float(z.get('l', 0))
        if not (h or s or li):
            continue
        th = np.deg2rad(h)
        cos_t, sin_t = np.cos(th), np.sin(th)
        # rotate chroma (hue shift)
        a2 = a * cos_t - b * sin_t
        b2 = a * sin_t + b * cos_t
        a = a + (a2 - a) * w
        b = b + (b2 - b) * w
        # saturation: scale chroma magnitude
        if s:
            a = a * (1 + s / 100.0 * w)
            b = b * (1 + s / 100.0 * w)
        # lightness
        if li:
            L = L + li * 0.6 * w
    lab[..., 1] = np.clip(a + 128, 0, 255)
    lab[..., 2] = np.clip(b + 128, 0, 255)
    lab[..., 0] = np.clip(L, 0, 255)
    return cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR)

# ---------- F6.4 Selective Color (9 ranges, CMYK components) ----------
_SC_RANGES = {  # name -> (hue_center_deg, halfwidth)
    'red': (0, 25), 'yellow': (60, 30), 'green': (120, 35),
    'cyan': (180, 30), 'blue': (240, 35), 'magenta': (300, 30),
}
def _sc_weight(hsv, name):
    h = hsv[..., 0].astype(np.float32) * 2.0  # 0..360
    s = hsv[..., 1] / 255.0
    v = hsv[..., 2] / 255.0
    if name in _SC_RANGES:
        c, hw = _SC_RANGES[name]
        d = np.abs((h - c + 180) % 360 - 180)
        w = np.clip(1 - d / hw, 0, 1)
        return w * s  # only colored pixels
    if name == 'white':
        return np.clip((v - 0.85) / 0.15, 0, 1) * (1 - s)
    if name == 'neutral':
        return np.clip(1 - np.abs(v - 0.5) / 0.5, 0, 1) * (1 - s)
    if name == 'black':
        return np.clip((0.15 - v) / 0.15, 0, 1)
    return np.zeros(hsv.shape[:2], np.float32)

def selective_color(bgr, adjustments=None):
    """adjustments: {color: {c,m,y,k: -100..100, absolute: bool}}.
    CMY deltas applied in RGB space: cyan≈(1-R), magenta≈(1,G,1), yellow≈(1,1,B), k=uniform darken.
    relative scales by component presence, absolute adds fixed amount."""
    adjustments = adjustments or {}
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    out = bgr.astype(np.float32)
    for color, adj in adjustments.items():
        w = _sc_weight(hsv, color)[..., None]
        if not w.any():
            continue
        abs_mode = bool(adj.get('absolute', False))
        f = out / 255.0
        deltas = {}
        for comp, rgbv in (('c', [1, 0, 0]), ('m', [0, 1, 0]), ('y', [0, 0, 1])):
            val = float(adj.get(comp, 0)) / 100.0
            if not val:
                continue
            if abs_mode:
                d = np.array(rgbv, np.float32) * val
            else:  # relative: scale presence of complementary primary
                pres = 1 - f * np.array(rgbv, np.float32)  # presence of that ink
                d = pres * val * np.array(rgbv, np.float32)
            deltas[comp] = d
        k = float(adj.get('k', 0)) / 100.0
        if k:
            if abs_mode:
                deltas['k'] = -np.ones_like(f) * k
            else:
                deltas['k'] = -f * k
        total = sum(deltas.values()) if deltas else 0
        out = out + total * 255.0 * w
    return np.clip(out, 0, 255).astype(np.uint8)

# ---------- F6.5 Color Editor (8-band hue-shift ring + HSL) ----------
_CE_BANDS = [  # (hue_center, halfwidth) — aligned with HSL 8 bands (OpenCV hue 0..179)
    (0, 11), (15, 15), (30, 15), (45, 22), (75, 22), (110, 26), (140, 20), (160, 19),
]
def color_editor(bgr, bands=None, global_hue=0):
    """bands: {0..7: {h,s,l}} — h is hue rotation in degrees. global_hue: whole-image hue rotate."""
    out = bgr
    if global_hue:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 0] = (hsv[..., 0] + global_hue / 2.0) % 180
        out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    bands = bands or {}
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
    h = hsv[..., 0] * 2.0  # 0..360
    total_h = np.zeros_like(h)
    total_s = np.zeros_like(h)
    total_l = np.zeros_like(h)
    wsum = np.zeros_like(h)
    for i, adj in bands.items():
        i = int(i)
        if i >= len(_CE_BANDS):
            continue
        c, hw = _CE_BANDS[i]
        d = np.abs((h - c + 180) % 360 - 180)
        w = np.clip(1 - d / hw, 0, 1) ** 1.2
        wsum += w
        total_h += w * float(adj.get('h', 0)) / 2.0   # deg->OpenCV units
        total_s += w * float(adj.get('s', 0))
        total_l += w * float(adj.get('l', 0))
    wsum = np.maximum(wsum, 1e-6)
    hsv[..., 0] = (hsv[..., 0] + total_h / wsum) % 180
    hsv[..., 1] = np.clip(hsv[..., 1] * (1 + total_s / wsum / 100.0), 0, 255)
    v = hsv[..., 2]
    # lightness in LCH-ish sense: scale V by L of pixel
    hsv[..., 2] = np.clip(v + (total_l / wsum) * 0.6, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
