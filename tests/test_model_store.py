"""Automatic model provisioning (engine/perception/model_store.py): failover between
sources, resume after a dropped connection, sha256 rejection, persistent dir lookup,
and the server's 503 'models pending' contract."""
import hashlib
import http.server
import importlib
import importlib.util
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'server', 'api'))


def load_store():
    spec = importlib.util.spec_from_file_location(
        'ms_test', os.path.join(ROOT, 'engine', 'perception', 'model_store.py'))
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def fake_models(tmp_path, monkeypatch):
    """Two synthetic 'models' served by a local HTTP server: /dead/ -> 404, /flaky/ drops the
    first connection half-way, /bad/ serves tampered bytes."""
    srv_dir = tmp_path / 'srv'
    srv_dir.mkdir()
    blobs = {'a.onnx': os.urandom(300_000), 'b.onnx': os.urandom(120_000)}
    for k, v in blobs.items():
        (srv_dir / k).write_bytes(v)
    state = {'cut': set()}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            kind, name = self.path.strip('/').split('/')
            if kind == 'dead':
                return self.send_error(404)
            data = blobs[name] if kind != 'bad' else b'X' + blobs[name][1:]
            start = int(self.headers['Range'].split('=')[1].split('-')[0]) if self.headers.get('Range') else 0
            self.send_response(206 if start else 200)
            if start:
                self.send_header('Content-Range', f'bytes {start}-{len(data) - 1}/{len(data)}')
            self.send_header('Content-Length', str(len(data) - start))
            self.end_headers()
            body = data[start:]
            if kind == 'flaky' and name not in state['cut']:
                state['cut'].add(name)
                self.wfile.write(body[:len(body) // 2])
                self.wfile.flush()
                self.connection.close()
                return
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_port}'
    ms = load_store()
    ms.MODELS.clear()
    for k, v in blobs.items():
        ms.MODELS[k] = dict(url=f'{base}/dead/{k}', mirrors=[f'{base}/flaky/{k}'],
                            size=len(v), sha256=hashlib.sha256(v).hexdigest())
    monkeypatch.setattr(ms, 'BUNDLED_DIR', str(tmp_path / 'bundled'))
    monkeypatch.setenv('PIXLITE_MODELS_DIR', str(tmp_path / 'store'))
    monkeypatch.setenv('PIXLITE_MODEL_MIRROR_FIRST', '0')
    monkeypatch.setattr(ms.time, 'sleep', lambda s: None)
    yield ms, base, tmp_path
    httpd.shutdown()


def test_failover_and_resume(fake_models):
    ms, _, tmp = fake_models
    logs = []
    assert ms.ensure_all(log=logs.append)
    st = ms.status()
    assert st['ready'] and st['percent'] == 100.0 and st['phase'] == 'ready'
    for k in ms.MODELS:
        assert ms.check(os.path.join(tmp / 'store', k), full=True) is None
    assert any('404' in line for line in logs)        # official source dead -> skipped
    assert any('incomplete' in line for line in logs)  # dropped connection -> resumed


def test_tampered_source_rejected(fake_models):
    ms, base, tmp = fake_models
    ms.MODELS['a.onnx']['url'] = f'{base}/bad/a.onnx'
    ms.MODELS['a.onnx']['mirrors'] = []
    with pytest.raises(RuntimeError, match='sha256'):
        ms.download_one('a.onnx', attempts=1, log=lambda m: None)
    assert not os.path.exists(tmp / 'store' / 'a.onnx')
    assert ms.status()['models']['a.onnx']['state'] == 'failed'


def test_bundled_models_preferred(fake_models):
    ms, _, tmp = fake_models
    os.makedirs(ms.BUNDLED_DIR)
    blob = (tmp / 'srv' / 'a.onnx').read_bytes()
    (tmp / 'bundled' / 'a.onnx').write_bytes(blob)
    assert ms.model_path('a.onnx') == os.path.join(ms.BUNDLED_DIR, 'a.onnx')
    assert ms.missing_models() == ['b.onnx']
    assert ms.model_path('b.onnx') == os.path.join(str(tmp / 'store'), 'b.onnx')


def test_cn_systems_try_mirror_first(fake_models, monkeypatch):
    ms, base, _ = fake_models
    monkeypatch.delenv('PIXLITE_MODEL_MIRROR_FIRST')
    monkeypatch.setenv('TRIM_SYS_LANGUAGE', 'zh-CN')
    assert ms.sources('a.onnx')[0] == f'{base}/flaky/a.onnx'
    monkeypatch.setenv('TRIM_SYS_LANGUAGE', 'en-US')
    assert ms.sources('a.onnx')[0] == f'{base}/dead/a.onnx'


def test_real_manifest_sources_have_mirrors():
    ms = load_store()
    for name in ms.MODELS:
        assert len(ms.sources(name)) >= 2, name   # every model has a fallback source


def test_server_returns_503_while_models_pending(tmp_path, monkeypatch):
    monkeypatch.setenv('PIXLITE_WORK_DIR', str(tmp_path / 'w'))
    monkeypatch.setenv('PIXLITE_AUTO_DOWNLOAD_MODELS', '0')
    monkeypatch.setenv('PIXLITE_REQUIRE_MODELS', '1')
    monkeypatch.setenv('PIXLITE_ALLOWED_ROOTS', str(tmp_path))
    import main  # pi-lens-ignore: 动态 sys.path 注入, pyright 无法静态解析
    importlib.reload(main)
    monkeypatch.setattr(main.model_store, 'status', lambda: {
        'phase': 'downloading', 'ready': False, 'error': '', 'percent': 42.0, 'done_bytes': 1,
        'total_bytes': 2, 'speed_bps': 0, 'dir': '', 'ready_count': 3, 'count': 8, 'models': {}})
    from fastapi.testclient import TestClient
    c = TestClient(main.app, client=('127.0.0.1', 1))
    r = c.post('/api/retouch', files={'file': ('a.jpg', b'x')})
    assert r.status_code == 503 and r.json()['models_pending'] and '42.0%' in r.json()['error']
    src = tmp_path / 'photos'
    src.mkdir()
    assert c.post('/api/batch', data={'src_dir': str(src)}).status_code == 503
    assert c.get('/api/models/status').status_code == 200


def test_missing_model_raises_friendly_error(tmp_path, monkeypatch):
    from engine.gpu import ModelUnavailable, make_session
    with pytest.raises(ModelUnavailable, match='自动下载'):
        make_session(str(tmp_path / 'modnet.onnx'))
