"""Regression tests for bugs fixed in v0.1.10 (see BUGFIX_REPORT.md).

Run:  python -m pytest tests/test_regressions.py -q
Tests that need ONNX models (tools/download_models.py) are skipped when the model is absent.
"""
import os, sys, json, glob, importlib
import numpy as np, cv2, pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'server', 'api'))

from engine import color as C
from engine.pipeline import Pipeline
import engine.pipeline as EP

MODELS = os.path.join(ROOT, 'engine', 'perception', 'models')
FACE = os.path.join(ROOT, 'tests', 'test_face.jpg')


def need(*names):
    miss = [n for n in names if not os.path.isfile(os.path.join(MODELS, n))]
    return pytest.mark.skipif(bool(miss), reason=f'model missing: {miss}')


def run_nodes(img, nodes):
    p = Pipeline.__new__(Pipeline)
    p.preset = {'nodes': nodes}
    return p.run(img.copy())


def identity_lut(k=8):
    n = k * k
    lut = np.zeros((k * n, k * n, 3), np.uint8)
    v = np.round(np.arange(n) * 255 / (n - 1)).astype(np.uint8)
    for t in range(n):
        ty, tx = divmod(t, k)
        tile = lut[ty * n:(ty + 1) * n, tx * n:(tx + 1) * n]
        tile[..., 2] = v[None, :]   # x = red
        tile[..., 1] = v[:, None]   # y = green
        tile[..., 0] = v[t]         # tile = blue
    return lut


# ---------------- color ----------------
def test_lut_identity_is_noop():
    img = cv2.imread(FACE)
    out = C.apply_lut(img, identity_lut(), degree=100)
    assert np.abs(out.astype(int) - img).mean() < 1.0


def test_lut_depends_on_color_not_position():
    img = np.full((40, 60, 3), (30, 140, 200), np.uint8)
    lut = cv2.imread(sorted(glob.glob(os.path.join(ROOT, 'fnpack', 'etc', 'lut', 'Filters', 'FT*', 'filter.png')))[0])
    out = C.apply_lut(img, lut, degree=100)
    assert out.reshape(-1, 3).std(axis=0).max() < 0.5   # uniform in -> uniform out


@pytest.mark.parametrize('t', [50, -50])
def test_white_balance_temperature_direction(t):
    img = np.full((4, 4, 3), (120, 120, 120), np.uint8)
    b, g, r = C.white_balance(img, t, 0)[0, 0].astype(int)
    if t > 0:
        assert r > 120 and b < 120          # warm
    else:
        assert b > 120 and r < 120          # cool (old code: no-op)


@pytest.mark.parametrize('g', [50, -50])
def test_white_balance_tint_direction(g):
    img = np.full((4, 4, 3), (120, 120, 120), np.uint8)
    b, gg, r = C.white_balance(img, 0, g)[0, 0].astype(int)
    assert (gg < 120) if g > 0 else (gg > 120)   # magenta(+) lowers green


def test_hsl_every_band_reachable():
    # pure hues at each band center must be affected by their own band
    for band, deg in enumerate(C.HSL_CENTERS):
        hsv = np.full((2, 2, 3), (int(deg / 2) % 180, 200, 160), np.uint8)
        px = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        out = C.hsl_adjust(px, band, 0, 0, -100)
        assert np.abs(out.astype(int) - px).sum() > 0, f'band {band} ({deg}°) no effect'


def test_hsl_blue_band_does_not_touch_green():
    green = cv2.cvtColor(np.full((2, 2, 3), (60, 200, 160), np.uint8), cv2.COLOR_HSV2BGR)
    out = C.hsl_adjust(green, 5, 0, 0, -100)    # band 5 = 蓝色
    assert np.array_equal(out, green)


def test_tone_brightness_and_dehaze_have_effect():
    img = cv2.imread(FACE)
    for k in ('brightness', 'dehaze'):
        out = C.tone_adjust(img, {k: 50})
        assert np.abs(out.astype(int) - img).mean() > 0.5, k


# ---------------- pipeline ----------------
def test_skin_tone_keeps_chroma(monkeypatch):
    """brighten-only must not wipe skin color (old code clipped LAB a/b to 127)."""
    monkeypatch.setattr(EP, '_skin_mask', lambda ctx, b: np.full(b.shape[:2], 255, np.uint8))
    skin = np.full((16, 16, 3), (150, 170, 215), np.uint8)
    out = run_nodes(skin, [{'op': 'skin_tone', 'params': {'brighten': 0.17}}])
    lab_in = cv2.cvtColor(skin, cv2.COLOR_BGR2LAB)[0, 0].astype(int)
    lab_out = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)[0, 0].astype(int)
    assert abs(lab_out[1] - lab_in[1]) <= 2 and abs(lab_out[2] - lab_in[2]) <= 2
    assert lab_out[0] >= lab_in[0]


@need('face_parsing.onnx')
def test_skin_mask_import_works():
    assert EP._skin_mask({}, cv2.imread(FACE)) is not None


@need('face_parsing.onnx', 'face_detection_yunet_2023mar.onnx')
def test_parsing_lazy_when_preset_lacks_face_parsing_node():
    img = cv2.imread(FACE)
    out = run_nodes(img, [{'op': 'face_detect'}, {'op': 'blemish', 'params': {'degree': .8}}])
    assert out.shape == img.shape


@need('face_detection_yunet_2023mar.onnx', 'modnet.onnx')
def test_sky_ops_run_without_face():
    land = np.zeros((200, 300, 3), np.uint8)
    land[:100] = (230, 170, 90)
    land[100:] = (40, 120, 60)
    out = run_nodes(land, [{'op': 'face_detect'}, {'op': 'sky_replace', 'params': {'mode': 'sunset'}}])
    assert np.abs(out.astype(int) - land).mean() > 1


@need('face_landmarks.onnx', 'face_detection_yunet_2023mar.onnx')
def test_landmarks_nchw_model():
    from engine.perception import get_face_detector, landmarks as LM
    img = cv2.imread(FACE)
    f = get_face_detector()(img)[0]
    m = LM.get_landmarks(img, f['box'])
    assert m is not None and m['pts'].shape == (478, 2)
    assert m['iris_l'][0] < m['iris_r'][0]


@need('real_esrgan_x2_fp16.onnx')
def test_quality_enhance_fp16_model():
    from engine.effects.quality_enhance import enhance_quality
    img = cv2.imread(FACE)[:128, :128]
    out = enhance_quality(img, degree=0.6)
    assert out.shape == img.shape
    assert np.abs(out.astype(float) - img).mean((0, 1)).max() < 3   # no channel swap tint


@need('face_parsing.onnx', 'face_detection_yunet_2023mar.onnx', 'face_landmarks.onnx')
def test_teeth_fix_is_local():
    from engine.perception import get_face_detector, get_skin_parser, landmarks as LM
    from engine.effects import teeth
    img = cv2.imread(FACE)
    faces = get_face_detector()(img)
    LM.attach_mesh(img, faces)
    out = teeth.teeth_fix(img, get_skin_parser()(img), faces, degree=1.0)
    corner = (slice(0, 60), slice(0, 60))           # background corner far from mouth
    assert np.array_equal(out[corner], img[corner])


def test_parse_cache_key_includes_shape():
    from engine.perception import cached_parse
    class Fake:
        def _raw(self, b): return np.zeros(b.shape[:2], np.uint8)
    a = np.arange(24, dtype=np.uint8).reshape(2, 4, 3)
    b = a.reshape(4, 2, 3)
    assert cached_parse(Fake(), a).shape == (2, 4)
    assert cached_parse(Fake(), b).shape == (4, 2)


# ---------------- server ----------------
@pytest.fixture(scope='module')
def client(tmp_path_factory):
    os.environ['PIXLITE_WORK_DIR'] = str(tmp_path_factory.mktemp('work'))
    # 路径白名单 (Issue #1): 允许仓库目录与 pytest 临时目录
    os.environ['PIXLITE_ALLOWED_ROOTS'] = os.pathsep.join([ROOT, str(tmp_path_factory.getbasetemp())])
    import main
    importlib.reload(main)
    from fastapi.testclient import TestClient
    # 本机回环客户端 (未配置 token 时远程请求会被 fail-closed 拒绝, 见 test_issues.py)
    return TestClient(main.app, client=('127.0.0.1', 50000)), main


def test_server_imports_without_web_dir(client):
    _, main = client
    assert main.app is not None


def test_debug_report_tolerates_bad_json(client):
    c, _ = client
    r = c.get('/api/debug/report', params={'frontend_events': '{bad'})
    assert r.status_code == 200


@pytest.mark.parametrize('name', ['../../etc/passwd', 'nope_missing'])
def test_retouch_rejects_bad_preset(client, name):
    c, main = client
    n0 = len(main.jobs)
    r = c.post('/api/retouch', files={'file': ('a.jpg', b'x')}, data={'preset': name})
    assert r.status_code == 400 and len(main.jobs) == n0


def test_retouch_bad_json_leaves_no_running_job(client):
    c, main = client
    n0 = len(main.jobs)
    r = c.post('/api/retouch', files={'file': ('a.jpg', b'x')}, data={'overrides': '{bad'})
    assert r.status_code == 400 and len(main.jobs) == n0


def test_batch_rejects_bad_preset(client, tmp_path):
    c, _ = client
    r = c.post('/api/batch', data={'src_dir': str(tmp_path), 'preset': 'lut_names'})
    assert r.status_code == 400


def test_batch_rejects_dst_inside_src(client, tmp_path):
    c, _ = client
    r = c.post('/api/batch', data={'src_dir': str(tmp_path), 'dst_dir': str(tmp_path / 'out')})
    assert r.status_code == 400


def test_result_missing_file_is_404(client):
    c, main = client
    main.jobs['deadbeef'] = {'status': 'done', 'progress': 1, 'output': '/nonexistent/x.jpg'}
    assert c.get('/api/result/deadbeef').status_code == 404


def test_exif_shutter_format(client):
    c, _ = client
    r = c.get('/api/exif', params={'path': os.path.join(ROOT, 'tests', 'test_exif.jpg')})
    assert r.status_code == 200
    j = r.json()
    assert isinstance(j['aperture'], (int, float))


def test_empty_lut_filter_is_ignored(client):
    _, main = client
    p = main.build_preset('wedding_basic', {'lut.filter': '', 'lut.degree': 70})
    assert all(n['op'] != 'lut' for n in p['nodes'])
