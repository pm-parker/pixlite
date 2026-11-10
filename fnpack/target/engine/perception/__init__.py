"""Perception layer — aligned with PixCake TSAI detection layer.
face mesh 478pts + pose + parsing + matting, all local ONNX/MediaPipe."""
from ..gpu import make_session
import numpy as np, cv2, os

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')

_detector_cache = {}
import threading
_tls = threading.local()
def get_face_detector():
    """Per-thread FaceDetector: cv2.dnn Net is NOT thread-safe; batch workers
    sharing one net caused intermittent dnn assertion failures (QA FAIL#1)."""
    det = getattr(_tls, 'det', None)
    if det is None:
        det = _tls.det = FaceDetector()
    return det

_parser_cache = {}
_parser_lock = threading.Lock()
def get_skin_parser():
    with _parser_lock:
        if 'p' not in _parser_cache:
            _parser_cache['p'] = SkinParser()
        return _parser_cache['p']

class FaceDetector:
    """YuNet (OpenCV DNN): fast face boxes + 5 pts. No mediapipe dependency
    (mp 1.x on py3.14 has broken C dispatcher). Aligned: face_detect node."""
    MODEL = os.path.join(MODELS_DIR, 'face_detection_yunet_2023mar.onnx')
    def __init__(self, max_faces=10, score=0.6):
        self.det = cv2.FaceDetectorYN.create(self.MODEL, '', (320, 320), score, max_faces)
        self.max_faces = max_faces

    def __call__(self, bgr):
        h, w = bgr.shape[:2]
        self.det.setInputSize((w, h))
        n, faces = self.det.detect(bgr)
        out = []
        if faces is not None:
            for f in faces[:self.max_faces]:
                x, y, fw, fh = f[:4]
                out.append({
                    'box': [float(x), float(y), float(fw), float(fh)],
                    'score': float(f[14]),
                    # pseudo-landmarks from box (YuNet gives 5 pts at [4:14])
                    'landmarks': np.array(f[4:14]).reshape(5, 2),
                })
        return out

def estimate_head_yaw(landmarks):
    """Aligned: FaceSurgery normal/side profile switch (AngleRange)."""
    L, R, N = landmarks[33], landmarks[263], landmarks[1]
    d_lr = R[0] - L[0]; d_ln = N[0] - L[0]
    ratio = d_ln / max(d_lr, 1e-6)
    return ratio  # ~0.5 frontal, toward 0/1 = side

class SkinParser:
    """SegFormer face-parsing (jonathandinu, ONNX) — 19 classes.
    Labels: 0 bg,1 skin,2 nose,3 eye_g,4 l_eye,5 r_eye,6 l_brow,7 r_brow,
            8 l_ear,9 r_ear,10 mouth,11 u_lip,12 l_lip,13 hair,14 hat,
            15 ear_r,16 neck_l,17 neck,18 cloth"""
    def __init__(self, model_path=None):
        path = model_path or os.path.join(MODELS_DIR, 'face_parsing.onnx')
        import onnxruntime as ort
        self.sess = make_session(path)
        self.inp = self.sess.get_inputs()[0].name

    def __call__(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        x = cv2.resize(rgb, (512, 512)).astype(np.float32) / 255.0
        x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        x = x.transpose(2, 0, 1)[None].astype(np.float32)
        logits = self.sess.run(None, {self.inp: x})[0][0]
        seg = logits.argmax(0).astype(np.uint8)
        return cv2.resize(seg, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_NEAREST)

# label ids (SegFormer 19-class)
CLS = {'bg':0,'skin':1,'nose':2,'eye_g':3,'eye_l':4,'eye_r':5,'brow_l':6,'brow_r':7,
       'ear_l':8,'ear_r':9,'mouth':10,'lip_up':11,'lip_low':12,'hair':13,'hat':14,
       'earring':15,'necklace':16,'neck':17,'cloth':18}

def mask_of(parsing, *keys, dilate=0):
    m = np.isin(parsing, [CLS[k] for k in keys]).astype(np.uint8) * 255
    if dilate:
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate)))
    return m
