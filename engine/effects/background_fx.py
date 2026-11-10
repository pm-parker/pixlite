"""BackgroundFX — MODNet 人像抠像 + 背景虚化/替换 (对标像素蛋糕"背景"板块: 背景虚化/背景颜色)."""
from ..gpu import make_session
import os, cv2, numpy as np, onnxruntime as ort

MODEL = os.path.join(os.path.dirname(__file__), '..', 'perception', 'models', 'modnet.onnx')
_cache = {}

def get_session():
    if 's' not in _cache:
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _cache['s'] = make_session(MODEL, so)
    return _cache['s']

def get_alpha(bgr, ref_side=512):
    """人像 alpha matte (0..1 float32, 原分辨率). MODNet 标准预处理: /255, 无 mean-std."""
    H, W = bgr.shape[:2]
    # MODNet(Xenova) 非正方形输入内部 concat 维度崩 → pad 成正方形跑,再裁回
    side = max(H, W)
    canvas = np.zeros((side, side, 3), np.float32)
    x0, y0 = (side - W) // 2, (side - H) // 2
    canvas[y0:y0+H, x0:x0+W] = bgr
    proc = cv2.resize(canvas, (ref_side, ref_side), interpolation=cv2.INTER_AREA)
    x = proc.astype(np.float32) / 255.0
    x = x.transpose(2, 0, 1)[None]
    a = get_session().run(None, {'input': x})[0][0, 0]
    ya, yb = int(y0/side*ref_side), int((y0+H)/side*ref_side)
    xa, xb = int(x0/side*ref_side), int((x0+W)/side*ref_side)
    a = a[ya:yb, xa:xb]
    return cv2.resize(a, (W, H), interpolation=cv2.INTER_LINEAR)

def blur_bg(bgr, strength=0.7):
    """背景景深虚化. strength 0..1 控制虚化量."""
    H, W = bgr.shape[:2]
    alpha = get_alpha(bgr)[..., None]
    k = max(int(3 + strength * 40), 3) | 1  # 奇数
    blurred = cv2.GaussianBlur(bgr, (k, k), 0)
    return (bgr.astype(np.float32) * alpha + blurred.astype(np.float32) * (1 - alpha)).astype(np.uint8)

def replace_bg_color(bgr, color=(18, 18, 18), degree=1.0):
    """背景纯色替换 (默认影棚深灰)."""
    alpha = get_alpha(bgr)[..., None]
    bg = np.zeros_like(bgr); bg[:] = color
    mix = bg.astype(np.float32) * degree
    return (bgr.astype(np.float32) * alpha + mix * (1 - alpha)).astype(np.uint8)
