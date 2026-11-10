"""Per-op verification: each new op on tests/test_face.jpg, diff>0, save to tests/out_c/."""
import sys, os, json, time
import numpy as np, cv2
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from engine.perception import get_face_detector, get_skin_parser, mask_of
from engine.perception import landmarks as LM
from engine.effects import eyes, teeth, makeup, hair2, body

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'tests', 'out_c')
os.makedirs(OUT, exist_ok=True)

img = cv2.imread(os.path.join(ROOT, 'tests', 'test_face.jpg'))
det = get_face_detector(); faces = det(img)
LM.attach_mesh(img, faces)
assert 'mesh' in faces[0], 'mesh attach failed'
par = get_skin_parser()(img)
print('mesh ok, pts', faces[0]['mesh'].shape, 'iris_l', faces[0]['iris_l'])

def diff(a, b):
    return float(np.abs(a.astype(int) - b.astype(int)).mean())

OPS = [
    ('eye_brighten_l', lambda: eyes.eye_brighten_side(img, par, 1.0, 'l')),
    ('eye_brighten_r', lambda: eyes.eye_brighten_side(img, par, 1.0, 'r')),
    ('lid_patch_l', lambda: eyes.lid_patch_remove(img, par, faces, 1.0, 'l')),
    ('lid_patch_r', lambda: eyes.lid_patch_remove(img, par, faces, 1.0, 'r')),
    ('antiglare', lambda: eyes.glasses_antiglare(img, par, 1.0)),
    ('catchlight', lambda: eyes.catchlight(img, par, faces, 1.0)),
    ('smile_manage', lambda: teeth.smile_manage(img, faces, 4)),
    ('gum_fix_l', lambda: teeth.gum_fix(img, par, faces, 1.0, 'l')),
    ('gum_fix_r', lambda: teeth.gum_fix(img, par, faces, 1.0, 'r')),
    ('teeth_align', lambda: teeth.teeth_align(img, par, faces, 4)),
    ('teeth_fix', lambda: teeth.teeth_fix(img, par, faces, 1.0)),
    ('makeup_highlight', lambda: makeup.makeup_highlight(img, par, faces, 1.0)),
    ('makeup_shadow', lambda: makeup.makeup_shadow(img, par, faces, 1.0)),
    ('brow_enh_l', lambda: makeup.brow_enhance(img, par, 1.0, 'l')),
    ('brow_enh_r', lambda: makeup.brow_enhance(img, par, 1.0, 'r')),
    ('eye_makeup_l', lambda: makeup.eye_makeup_enhance(img, par, 1.0, 'l')),
    ('eye_makeup_r', lambda: makeup.eye_makeup_enhance(img, par, 1.0, 'r')),
    ('lip_makeup', lambda: makeup.lip_makeup_enhance(img, par, 1.0)),
    ('hair_smooth', lambda: hair2.hair_smooth(img, par, 1.0)),
    ('hair_gap', lambda: hair2.hair_gap_fill(img, par, 1.0)),
    ('crown', lambda: hair2.crown_lift(img, par, faces, 1.0)),
    ('hair_volume_l', lambda: hair2.hair_volume(img, par, 1.0, 'l')),
    ('hair_volume_r', lambda: hair2.hair_volume(img, par, 1.0, 'r')),
    ('hair_unify', lambda: hair2.hair_unify(img, par, 1.0)),
    ('body_waist', lambda: body.body_slim(img, par, faces, 'waist', 1.0)),
    ('body_leg_thin', lambda: body.body_slim(img, par, faces, 'legs', 1.0)),
    ('body_leg_long', lambda: body.leg_lengthen(img, par, 1.0)),
    ('axilla', lambda: body.axilla_fix(img, par, faces, 1.0)),
    ('striae', lambda: body.stretch_marks(img, par, faces, 1.0)),
]

diffs = {}
fails = []
for name, fn in OPS:
    t0 = time.time()
    try:
        out = fn()
        d = diff(img, out)
        diffs[name] = round(d, 4)
        cv2.imwrite(os.path.join(OUT, name + '.jpg'), out)
        flag = 'OK ' if d > 0 else 'ZERO-DIFF'
        if d <= 0:
            fails.append(name)
        print(f'{flag} {name:18s} diff={d:.4f}  {time.time()-t0:.2f}s')
    except Exception as e:
        fails.append(name)
        print(f'FAIL {name}: {type(e).__name__}: {e}')

json.dump(diffs, open(os.path.join(OUT, 'diffs.json'), 'w'), indent=1)
print('\nfails:', fails if fails else 'none')
