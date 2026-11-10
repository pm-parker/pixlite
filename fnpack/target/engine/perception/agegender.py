"""AgeGender — local face attribute classification (insightface genderage.onnx).
Aligned with PixCake 人物属性: 女/男/儿童/长辈女/长辈男 auto-routing.
Input: 96x96 RGB face crop (float32, no norm). Output: [female_logit, -female_logit, age/100].
gender: dim0>0 -> female (calibrated on 6 known faces). age: dim2*100 years. child: age < 14; elder: age > 55."""
from ..gpu import make_session
import os, cv2, numpy as np, onnxruntime as ort

MODEL = os.path.join(os.path.dirname(__file__), 'models', 'genderage.onnx')

_cache = {}
_lock = __import__('threading').Lock()

def get_session():
    with _lock:
        if 's' not in _cache:
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            _cache['s'] = make_session(MODEL, so)
        return _cache['s']

def classify(bgr, box):
    """bgr: full image; box: (x,y,w,h) face box. -> dict(gender='female'|'male', age=float, group=str)"""
    x, y, w, h = [int(v) for v in box]
    H, W = bgr.shape[:2]
    face = bgr[max(0,y):min(H,y+h), max(0,x):min(W,x+w)]
    if face.size == 0:
        return None
    face = cv2.resize(face, (96, 96))
    rgb = cv2.cvtColor(face, cv2.COLOR_BGR2RGB).astype(np.float32)
    out = get_session().run(None, {'data': rgb.transpose(2, 0, 1)[None]})[0].flatten()
    male_logit, _female_logit, age_frac = float(out[0]), float(out[1]), float(out[2])
    gender = 'female' if male_logit > 0 else 'male'   # dim0>0=female (calibrated)
    age = max(0.0, min(100.0, age_frac * 100.0))
    if age < 14:
        group = 'child'
    elif age > 55:
        group = 'elder_' + ('m' if gender == 'male' else 'f')
    else:
        group = gender
    return {'gender': gender, 'age': round(age, 1), 'group': group,
            'male_logit': round(male_logit, 3)}
