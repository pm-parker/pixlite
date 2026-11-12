"""E2E: build_preset('wedding_basic', overrides) full pipeline for all new ops
(overrides ON vs baseline), diff > 0. Mirrors slider-drag path via server main.build_preset."""
import sys, os, json
import numpy as np, cv2
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server', 'api'))
from main import build_preset, DictPipeline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'tests', 'out_c')
img = cv2.imread(os.path.join(ROOT, 'tests', 'test_face.jpg'))

base_pipe = DictPipeline(build_preset('wedding_basic', {}))
baseline = base_pipe.run(img.copy())

OPS = {
    'eye_brighten_r': {'degree': 1.0}, 'lid_patch_l': {'degree': 1.0},
    'lid_patch_r': {'degree': 1.0}, 'antiglare': {'degree': 1.0},
    'catchlight': {'degree': 1.0}, 'smile_manage': {'level': 4},
    'gum_fix_l': {'degree': 1.0}, 'gum_fix_r': {'degree': 1.0},
    'teeth_align': {'level': 4}, 'teeth_fix': {'degree': 1.0},
    'makeup_highlight': {'degree': 1.0}, 'makeup_shadow': {'degree': 1.0},
    'brow_enh_l': {'degree': 1.0}, 'brow_enh_r': {'degree': 1.0},
    'eye_makeup_l': {'degree': 1.0}, 'eye_makeup_r': {'degree': 1.0},
    'lip_makeup': {'degree': 1.0}, 'hair_smooth': {'degree': 1.0},
    'hair_gap': {'degree': 1.0}, 'crown': {'degree': 1.0},
    'hair_volume_l': {'degree': 1.0}, 'hair_volume_r': {'degree': 1.0},
    'hair_unify': {'degree': 1.0}, 'body_waist': {'degree': 1.0},
    'body_leg_thin': {'degree': 1.0}, 'body_leg_long': {'degree': 1.0},
    'axilla': {'degree': 1.0}, 'striae': {'degree': 1.0},
}

diffs = {}
fails = []
for op, params in OPS.items():
    ov = {f'{op}.{k}': v for k, v in params.items()}
    pipe = DictPipeline(build_preset('wedding_basic', ov))
    out = pipe.run(img.copy())
    d = float(np.abs(out.astype(int) - baseline.astype(int)).mean())
    diffs[op] = round(d, 4)
    if d <= 0:
        fails.append(op)
    print(f"{'OK ' if d>0 else 'ZERO'} {op:18s} e2e_diff={d:.4f}")

# full-on composite
all_ov = {}
for op, params in OPS.items():
    for k, v in params.items():
        all_ov[f'{op}.{k}'] = v
out = DictPipeline(build_preset('wedding_basic', all_ov)).run(img.copy())
d_all = float(np.abs(out.astype(int) - baseline.astype(int)).mean())
cv2.imwrite(os.path.join(OUT, 'e2e_cgroup_full.jpg'), out)
cv2.imwrite(os.path.join(OUT, 'e2e_cgroup_baseline.jpg'), baseline)
diffs['_full_all'] = round(d_all, 4)
print(f'\nfull-on diff vs baseline = {d_all:.4f}')
json.dump(diffs, open(os.path.join(OUT, 'e2e_diffs.json'), 'w'), indent=1)
print('fails:', fails if fails else 'none')
# Issue #12: 失败必须以非零退出码结束, 便于 CI / pytest 捕获
sys.exit(1 if fails else 0)
