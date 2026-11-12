"""478-point face landmarks via ONNX (Google MediaPipe face_landmarks_detector
tflite converted to ONNX; onnxruntime only, no mediapipe Python package).
Input: 256x256 crop around face box; output 478 (x,y,z) normalized to canvas.
Canonical indices: mesh 0-467, left iris 468-472, right iris 473-477.
'left'/'right' below are IMAGE-side (image-left / image-right) for stability.
Verified against SegFormer eye/lip mask bboxes on tests/test_face.jpg."""
from ..gpu import make_session
import numpy as np, cv2, os

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
from .model_store import model_path as _model_path
LANDMARK_MODEL_NAME = 'face_landmarks.onnx'
_sess_cache = None


def _sess():
    global _sess_cache
    if _sess_cache is None:
        import onnxruntime as ort
        _sess_cache = make_session(_model_path(LANDMARK_MODEL_NAME))
    return _sess_cache


# canonical MediaPipe mesh indices
MOUTH_L, MOUTH_R = 61, 291
CHIN = 152
FOREHEAD_TOP = 10
NOSE_TIP = 1
# image-left eye (canonical subject-right, idx 33 corner) / image-right eye
EYE_L_IDX = [33, 133, 160, 159, 158, 144, 153, 145, 246, 161, 163, 7]
EYE_R_IDX = [263, 362, 385, 386, 387, 373, 380, 374, 466, 388, 390, 249]
BROW_L_IDX = [70, 63, 105, 66, 107, 46, 53, 52, 65]
BROW_R_IDX = [300, 293, 334, 296, 336, 276, 283, 282, 295]
LIPS_OUTER = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375,
              291, 409, 270, 269, 267, 0, 37, 39, 40, 185]
UPPER_LIP_TOP = [61, 76, 62, 0, 291, 308, 409, 267, 269, 37, 39, 40, 185]
UPPER_LIP_BOTTOM = [61, 78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308, 291]
CHEEK_L = 50
CHEEK_R = 280


def get_landmarks(bgr, face_box):
    """face_box = [x,y,w,h]. Returns {'pts': (478,2) image coords,
    'iris_l','iris_r'} or None."""
    if face_box is None:
        return None
    x, y, fw, fh = [float(v) for v in face_box[:4]]
    cx, cy = x + fw / 2, y + fh / 2
    side = max(fw, fh) * 1.4
    x0, y0 = cx - side / 2, cy - side / 2
    sc = 256.0 / side
    M = np.float32([[sc, 0, -x0 * sc], [0, sc, -y0 * sc]])
    crop = cv2.warpAffine(bgr, M, (256, 256), borderMode=cv2.BORDER_REPLICATE)
    inp = (crop[..., ::-1].astype(np.float32) / 127.5 - 1.0)[None]   # NHWC
    s = _sess()
    meta = s.get_inputs()[0]
    # tools/download_models.py 下载的公开 ONNX 版输入是 NCHW (Nx3x256x256),
    # 参考构建的转换版是 NHWC; 按模型声明的输入形状自适应。否则 NCHW 模型直接抛
    # INVALID_ARGUMENT, 被 attach_mesh 静默吞掉, 所有依赖 478 点的效果全部失效。
    shp = meta.shape
    if len(shp) == 4 and shp[1] == 3:
        inp = np.ascontiguousarray(inp.transpose(0, 3, 1, 2))
    out = s.run(None, {meta.name: inp})[0]
    pts = out.reshape(478, 3)[:, :2]
    p = pts / np.float32([sc, sc]) + np.float32([x0, y0])
    # sanity: mesh must land near the face box
    if p[:, 0].min() < x - fw or p[:, 0].max() > x + fw * 2 or \
       p[:, 1].min() < y - fh or p[:, 1].max() > y + fh * 2:
        return None
    iris_l = p[468:473].mean(axis=0)   # canonical left-iris center
    iris_r = p[473:478].mean(axis=0)
    if iris_l[0] > iris_r[0]:          # canonical naming vs image side
        iris_l, iris_r = iris_r, iris_l
    return {'pts': p, 'iris_l': iris_l, 'iris_r': iris_r}


def attach_mesh(bgr, faces):
    """Attach 478-pt mesh info to each YuNet face dict (in place)."""
    for f in faces:
        box = f.get('box') if isinstance(f, dict) else f[:4]
        m = get_landmarks(bgr, box)
        if m is not None and isinstance(f, dict):
            f['mesh'] = m['pts']
            f['iris_l'] = m['iris_l']
            f['iris_r'] = m['iris_r']
    return faces


def poly_mask(pts, idx, shape, feather=5):
    """Polygon fill -> soft mask (uint8) with gaussian feather."""
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillPoly(m, [pts[idx].astype(np.int32)], 255)
    if feather:
        m = cv2.GaussianBlur(m, (0, 0), feather)
    return m
