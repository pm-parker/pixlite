"""Perception layer — aligned with PixCake TSAI detection layer.
face mesh 478pts + pose + parsing + matting, all local ONNX/MediaPipe."""
import hashlib
import os
import threading
from collections import OrderedDict

import cv2
import numpy as np

from ..gpu import make_session

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')

_detector_cache = {}
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
# face-parsing 结果缓存: key=图像内容 sha1, 同图调参重跑免重推理 (LRU=4)
_parse_result_cache = OrderedDict()
_parse_result_lock = threading.Lock()
PARSE_CACHE_MAX = 4

def cached_parse(parser, bgr):
    # key 含 shape: 相同字节、不同宽高的图 (如转置) 否则会命中错误尺寸的 parsing
    h = hashlib.sha256(repr(bgr.shape).encode() + np.ascontiguousarray(bgr).tobytes()).hexdigest()
    with _parse_result_lock:
        hit = _parse_result_cache.get(h)
        if hit is not None:
            _parse_result_cache.move_to_end(h)
            return hit
    out = parser._raw(bgr)
    with _parse_result_lock:
        _parse_result_cache[h] = out
        while len(_parse_result_cache) > PARSE_CACHE_MAX:
            _parse_result_cache.popitem(last=False)
    return out
def get_skin_parser():
    with _parser_lock:
        if 'p' not in _parser_cache:
            _parser_cache['p'] = SkinParser()
        return _parser_cache['p']

class FaceDetector:
    """YuNet (OpenCV DNN): fast face boxes + 5 pts. No mediapipe dependency
    (mp 1.x on py3.14 has broken C dispatcher). Aligned: face_detect node."""
    MODEL_NAME = 'face_detection_yunet_2023mar.onnx'
    def __init__(self, max_faces=10, score=0.6):
        from .model_store import model_path
        from .models_manifest import check
        path = model_path(self.MODEL_NAME)
        reason = check(path, full=False)
        if reason:   # 与 make_session 一致: 给出可读错误, 而不是 OpenCV parseFile 异常
            from ..gpu import ModelUnavailable
            raise ModelUnavailable(self.MODEL_NAME, reason)
        self.det = cv2.FaceDetectorYN.create(path, '', (320, 320), score, max_faces)
        self.max_faces = max_faces

    def __call__(self, bgr):
        h, w = bgr.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(bgr)
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
        return _nms_faces(out, iou_thr=0.3)

def _nms_faces(faces, iou_thr=0.3):
    """YuNet 常对同一张脸吐多个高重叠框 — 去重, 否则 facesurgery 逐框
    做全图位移场, 时间 ×N (实测 8 框 23s → 1 框 2.8s)。"""
    def iou(a, b):
        ax, ay, aw, ah = a['box']
        bx, by, bw, bh = b['box']
        x1 = max(ax, bx)
        y1 = max(ay, by)
        x2 = min(ax + aw, bx + bw)
        y2 = min(ay + ah, by + bh)
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        return inter / (aw * ah + bw * bh - inter + 1e-6)
    fs = sorted(faces, key=lambda f: -f['score'])
    keep = []
    for f in fs:
        if all(iou(f, k) < iou_thr for k in keep):
            keep.append(f)
    return keep

def estimate_head_yaw(landmarks):
    """Aligned: FaceSurgery normal/side profile switch (AngleRange)."""
    L, R, N = landmarks[33], landmarks[263], landmarks[1]
    d_lr = R[0] - L[0]
    d_ln = N[0] - L[0]
    ratio = d_ln / max(d_lr, 1e-6)
    return ratio  # ~0.5 frontal, toward 0/1 = side

class SkinParser:
    """SegFormer face-parsing (jonathandinu, ONNX) — 19 classes.
    Labels: 0 bg,1 skin,2 nose,3 eye_g,4 l_eye,5 r_eye,6 l_brow,7 r_brow,
            8 l_ear,9 r_ear,10 mouth,11 u_lip,12 l_lip,13 hair,14 hat,
            15 ear_r,16 neck_l,17 neck,18 cloth"""
    def __init__(self, model_path=None):
        from .model_store import model_path as _mp
        path = model_path or _mp('face_parsing.onnx')
        self.sess = make_session(path)
        self.inp = self.sess.get_inputs()[0].name

    def _raw(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        x = cv2.resize(rgb, (512, 512)).astype(np.float32) / 255.0
        x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        x = x.transpose(2, 0, 1)[None].astype(np.float32)
        logits = np.asarray(self.sess.run(None, {self.inp: x})[0])[0]
        seg = logits.argmax(0).astype(np.uint8)
        return cv2.resize(seg, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_NEAREST)

    def __call__(self, bgr):
        return cached_parse(self, bgr)

# label ids (SegFormer 19-class)
CLS = {'bg':0,'skin':1,'nose':2,'eye_g':3,'eye_l':4,'eye_r':5,'brow_l':6,'brow_r':7,
       'ear_l':8,'ear_r':9,'mouth':10,'lip_up':11,'lip_low':12,'hair':13,'hat':14,
       'earring':15,'necklace':16,'neck':17,'cloth':18}

def mask_of(parsing, *keys, dilate=0):
    m = np.isin(parsing, [CLS[k] for k in keys]).astype(np.uint8) * 255
    if dilate:
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate)))
    return m
