"""QualityEnhance — Real-ESRGAN x2 超分 + 细节重建 (对标像素蛋糕"画质修复/超清画质").
degree 控制: 0=原图, 1=全超分回贴; 分块处理防大图 OOM."""
from ..gpu import make_session
import os, cv2, numpy as np, onnxruntime as ort

MODEL = os.path.join(os.path.dirname(__file__), '..', 'perception', 'models', 'real_esrgan_x2_fp16.onnx')
_cache = {}

def get_session():
    if 's' not in _cache:
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _cache['s'] = make_session(MODEL, so)
    return _cache['s']

def _in_dtype():
    t = get_session().get_inputs()[0].type
    return np.float16 if 'float16' in t else np.float32


def _tile_forward(bgr, tile=256, scale=2):
    """分块超分+无缝拼回（overlap 16 羽化）."""
    H, W = bgr.shape[:2]
    out = np.zeros((H * scale, W * scale, 3), np.float32)
    weight = np.zeros((H * scale, W * scale, 1), np.float32)
    ov = 16
    for y in range(0, H, tile - ov):
        for x in range(0, W, tile - ov):
            y1, x1 = y, x
            y2, x2 = min(H, y + tile), min(W, x + tile)
            # BGR -> RGB (Real-ESRGAN 以 RGB 训练), 输入 dtype 跟随模型声明:
            # 下载脚本给的是 fp16 模型, 旧代码喂 float32 → INVALID_ARGUMENT, 画质修复整单失败
            t = bgr[y1:y2, x1:x2, ::-1].astype(np.float32) / 255.0
            t = np.ascontiguousarray(t.transpose(2, 0, 1)[None]).astype(_in_dtype())
            r = get_session().run(None, {'input': t})[0][0].astype(np.float32)  # 3,s*2,s*2
            r = r.transpose(1, 2, 0)[..., ::-1]
            Y, X = y1 * scale, x1 * scale
            out[Y:Y + r.shape[0], X:X + r.shape[1]] += r
            weight[Y:Y + r.shape[0], X:X + r.shape[1]] += 1.0
    return (out / np.maximum(weight, 1e-6) * 255.0).clip(0, 255).astype(np.uint8)

def enhance_quality(bgr, degree=0.7, max_side=2048):
    """超分重建后按 degree 混合回原分辨率（保留原始构图, 只提升质感）.
    Issue #11: degree<=0 直接返回原图, 不加载模型、不做推理。"""
    degree = float(np.clip(degree, 0, 1))
    if degree <= 0:
        return bgr
    H, W = bgr.shape[:2]
    # 限边: 大图先缩到 max_side 再超分, 防 CPU 巨耗时
    proc = bgr
    pw, ph = W, H
    if max(H, W) > max_side:
        sc = max_side / max(H, W)
        proc = cv2.resize(bgr, (int(W * sc), int(H * sc)), interpolation=cv2.INTER_AREA)
        pw, ph = proc.shape[1], proc.shape[0]
    up = _tile_forward(proc)
    # 回贴原分辨率（超分带来的锐利细节保留在降采样中）
    up_orig = cv2.resize(up, (W, H), interpolation=cv2.INTER_AREA)
    # 细节层混合: 高频来自超分, 低频保持原图(不偏色)
    blur_up = cv2.GaussianBlur(up_orig, (0, 0), 3.0)
    blur_o = cv2.GaussianBlur(bgr, (0, 0), 3.0)
    detail = up_orig.astype(np.float32) - blur_up.astype(np.float32)
    out = (bgr.astype(np.float32) + detail * degree).clip(0, 255).astype(np.uint8)
    return out
