"""GFPEnhance — GFPGAN 1.4 ONNX face restoration (开源效果层, 对标像素蛋糕磨皮质感).
流程: 人脸检测(yunet) → 512x512 裁剪(带余量) → GFPGAN → 回贴(羽化融合)
degree 由滑杆控制回贴强度 (原脸与增强脸的 alpha 混合)."""
from ..gpu import make_session
import os, cv2, numpy as np, onnxruntime as ort

MODEL = os.path.join(os.path.dirname(__file__), '..', 'perception', 'models', 'gfpgan_1.4.onnx')
_cache = {}
_lock = __import__('threading').Lock()

def get_session():
    with _lock:
        if 's' not in _cache:
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            _cache['s'] = make_session(MODEL, so)
        return _cache['s']

def _pre(bgr512):
    img = cv2.cvtColor(bgr512, cv2.COLOR_BGR2RGB).astype(np.float32) / 127.5 - 1.0
    return img.transpose(2, 0, 1)[None]

def _post(out):
    img = out[0].transpose(1, 2, 0)
    img = ((img + 1.0) * 127.5).clip(0, 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

def enhance_face(bgr, degree=1.0):
    """bgr: full image. degree 0..1 控制混合强度. 返回增强后全图."""
    H, W = bgr.shape[:2]
    det = cv2.FaceDetectorYN.create(os.path.join(os.path.dirname(__file__), '..', 'perception', 'models',
                                    'face_detection_yunet_2023mar.onnx'), '', (320, 320), 0.6, 10)
    det.setInputSize((W, H))
    n, faces = det.detect(bgr)
    if faces is None:
        return bgr
    out = bgr.copy()
    for f in faces[:6]:   # 最多6张脸
        x, y, w, h = [int(v) for v in f[:4]]
        # 余量 0.6 倍, GFPGAN 对上下文敏感
        mx, my = int(w * 0.6), int(h * 0.6)
        x1, y1 = max(0, x - mx), max(0, y - my)
        x2, y2 = min(W, x + w + mx), min(H, y + h + my)
        crop = out[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        ch, cw = crop.shape[:2]
        inp = cv2.resize(crop, (512, 512), interpolation=cv2.INTER_LINEAR)
        res = get_session().run(None, {'input': _pre(inp)})[0]
        res = _post(res)
        res = cv2.resize(res, (cw, ch), interpolation=cv2.INTER_LANCZOS4)
        # 羽化椭圆融合
        mask = np.zeros((ch, cw), np.float32)
        cv2.ellipse(mask, (cw // 2, ch // 2), (int(cw * 0.45), int(ch * 0.45)),
                    0, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), max(cw, ch) * 0.06)
        mask = (mask * degree)[..., None]
        out[y1:y2, x1:x2] = (res * mask + crop * (1 - mask)).astype(np.uint8)
    return out
