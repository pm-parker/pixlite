"""Model store: locate, download and verify ONNX models automatically (stdlib only).

- Lookup order for a model file: bundled dir (engine/perception/models, filled in the
  `_full.fpk`) → PIXLITE_MODELS_DIR (persistent; fnOS: ${TRIM_PKGVAR}/models, survives
  upgrades). Missing models are downloaded into PIXLITE_MODELS_DIR.
- Downloads: resumable (.part + HTTP Range), several sources per model with automatic
  failover (official → hf-mirror.com → GitHub proxy), retries with backoff, and every file
  is verified by size + SHA-256 (models_manifest.py) before it is moved into place.
- `start_background()` is called by the server at startup so users never run anything by
  hand; `status()` feeds /api/models/status and the progress banner in the web UI.

Pure stdlib so tools/download_models.py can use it before numpy/cv2 are installed.
"""
import importlib.util
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_manifest():
    spec = importlib.util.spec_from_file_location('pixlite_models_manifest',
                                                  os.path.join(_HERE, 'models_manifest.py'))
    if spec is None or spec.loader is None:
        raise ImportError('models_manifest.py not found next to model_store.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_mm = _load_manifest()
MODELS, check, sha256_of = _mm.MODELS, _mm.check, _mm.sha256_of

BUNDLED_DIR = os.path.join(_HERE, 'models')
HF_MIRROR = 'https://hf-mirror.com'
GH_PROXIES = ('https://ghfast.top/',)          # content is still sha256-verified
CHUNK = 1024 * 1024


def models_dir():
    return os.environ.get('PIXLITE_MODELS_DIR') or BUNDLED_DIR


def model_path(name):
    """Existing file if any (bundled first, then store dir), else the download target."""
    for d in (BUNDLED_DIR, models_dir()):
        p = os.path.join(d, name)
        if os.path.isfile(p) and check(p, full=False) is None:
            return p
    return os.path.join(models_dir(), name)


def sources(name):
    """Candidate URLs, preferred order. CN systems (TRIM_SYS_LANGUAGE=zh*) try mirrors first."""
    url = MODELS[name]['url']
    alts = []
    if url.startswith('https://huggingface.co/'):
        alts.append(url.replace('https://huggingface.co', HF_MIRROR, 1))
    alts += MODELS[name].get('mirrors', [])
    if url.startswith('https://github.com/'):
        alts += [p + url for p in GH_PROXIES]
    lang = (os.environ.get('TRIM_SYS_LANGUAGE') or os.environ.get('LANG') or '').lower()
    prefer_mirror = os.environ.get('PIXLITE_MODEL_MIRROR_FIRST', '1' if lang.startswith('zh') else '0') == '1'
    out = alts + [url] if prefer_mirror else [url] + alts
    return list(dict.fromkeys(out))


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.models = {}          # name -> {state, done, total, error, source}
        self.phase = 'idle'       # idle | checking | downloading | ready | failed
        self.error = ''
        self.started = 0.0
        self.speed = 0.0


_S = _State()


def _set(name, **kw):
    with _S.lock:
        _S.models.setdefault(name, {'state': 'pending', 'done': 0, 'total': MODELS[name]['size'],
                                    'error': '', 'source': ''}).update(kw)


def _ready(name):
    return check(model_path(name), full=False) is None


def missing_models():
    return [n for n in MODELS if not _ready(n)]


def all_ready():
    return not missing_models()


def status():
    with _S.lock:
        models = {n: dict(v) for n, v in _S.models.items()}
        phase, err, speed = _S.phase, _S.error, _S.speed
    for n in MODELS:
        if n not in models:
            models[n] = {'state': 'ready' if _ready(n) else 'pending', 'done': 0,
                         'total': MODELS[n]['size'], 'error': '', 'source': ''}
        if models[n]['state'] == 'ready' or (models[n]['state'] == 'pending' and _ready(n)):
            models[n].update(state='ready', done=MODELS[n]['size'])
    total = sum(MODELS[n]['size'] for n in MODELS)
    done = sum(min(m['done'], m['total']) for m in models.values())
    ready = all(m['state'] == 'ready' for m in models.values())
    if ready:
        phase = 'ready'
    return {'phase': phase, 'ready': ready, 'error': err, 'done_bytes': done, 'total_bytes': total,
            'percent': round(done * 100.0 / total, 1) if total else 100.0,
            'speed_bps': round(speed), 'dir': models_dir(),
            'ready_count': sum(m['state'] == 'ready' for m in models.values()),
            'count': len(models), 'models': models}


def _fetch(url, part, name, timeout):
    """Download url into part, resuming from its current size. Returns final size."""
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if not url.startswith(('http://', 'https://')):   # S310: 只允许 http(s) 模型源
        raise ValueError(f'unsupported model url scheme: {url.split(":")[0]}')
    req = urllib.request.Request(url, headers={'User-Agent': 'PixLite-model-fetch/1.0'})  # noqa: S310 — url 来自 models_manifest 静态清单，scheme 已白名单校验
    if have:
        req.add_header('Range', f'bytes={have}-')
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — scheme 已白名单校验
        if have and r.status != 206:          # server ignored Range: restart
            have = 0
        mode = 'ab' if have else 'wb'
        t0, n0 = time.time(), have
        with open(part, mode) as fh:
            while True:
                buf = r.read(CHUNK)
                if not buf:
                    break
                fh.write(buf)
                have += len(buf)
                dt = time.time() - t0
                if dt > 1:
                    with _S.lock:
                        _S.speed = (have - n0) / dt
                    t0, n0 = time.time(), have
                _set(name, done=have)
    return have


def download_one(name, dest_dir=None, attempts=3, timeout=60, log=print):
    """Download + verify one model. Raises RuntimeError when every source fails."""
    spec = MODELS[name]
    dest_dir = dest_dir or models_dir()
    os.makedirs(dest_dir, exist_ok=True)
    dst = os.path.join(dest_dir, name)
    part = dst + '.part'
    errors = []
    for url in sources(name):
        for i in range(attempts):
            _set(name, state='downloading', source=url.split('/')[2], error='')
            try:
                size = _fetch(url, part, name, timeout)
                if size > spec['size']:
                    raise ValueError(f'size {size} > expected {spec["size"]}')
                if size < spec['size']:
                    raise OSError(f'incomplete {size}/{spec["size"]}')
                digest = sha256_of(part)
                if digest != spec['sha256']:
                    os.remove(part)               # corrupt / tampered: never resume from it
                    raise ValueError(f'sha256 mismatch {digest[:12]}')
                os.replace(part, dst)
                _set(name, state='ready', done=spec['size'])
                log(f'[{name}] ready ({size} bytes, sha256 OK) via {url.split("/")[2]}')
                return dst
            except (urllib.error.URLError, OSError, ValueError) as e:
                errors.append(f'{url.split("/")[2]}: {e}')
                log(f'[{name}] attempt {i + 1} via {url.split("/")[2]} failed: {e}')
                if isinstance(e, urllib.error.HTTPError) and e.code in (403, 404, 410):
                    break                          # permanent for this source
                time.sleep(min(2 ** i, 10))
    _set(name, state='failed', error='; '.join(errors[-3:])[:300])
    raise RuntimeError(f'{name}: all sources failed ({"; ".join(errors[-3:])})')


def ensure_all(log=print):
    """Download every missing model (smallest first so basic features come up quickly)."""
    with _S.lock:
        _S.phase, _S.error, _S.started = 'checking', '', time.time()
    todo = sorted(missing_models(), key=lambda n: MODELS[n]['size'])
    for n in MODELS:
        _set(n, state='pending' if n in todo else 'ready',
             done=0 if n in todo else MODELS[n]['size'])
    need = sum(MODELS[n]['size'] for n in todo)
    if todo:
        os.makedirs(models_dir(), exist_ok=True)
        free = shutil.disk_usage(models_dir()).free
        if free < need * 1.05:
            msg = f'磁盘空间不足: 需要 {need / 1e9:.1f} GB, 可用 {free / 1e9:.1f} GB ({models_dir()})'
            with _S.lock:
                _S.phase, _S.error = 'failed', msg
            raise RuntimeError(msg)
        with _S.lock:
            _S.phase = 'downloading'
    failed = []
    for n in todo:
        try:
            download_one(n, log=log)
        except RuntimeError as e:
            failed.append(str(e))
    with _S.lock:
        _S.phase = 'failed' if failed else 'ready'
        _S.error = '; '.join(failed)[:500]
    return not failed


def start_background(log=None, retry_every=300):
    """Start (once) a daemon thread that downloads missing models; failed runs are retried
    every `retry_every` seconds until all models are ready (e.g. NAS was offline)."""
    log = log or (lambda m: print(m, file=sys.stderr, flush=True))
    with _S.lock:
        if _S.thread and _S.thread.is_alive():
            return False

    def loop():
        while True:
            try:
                if ensure_all(log=log):
                    return
            except Exception as e:                 # disk full etc.
                log(f'model download failed: {e}')
            time.sleep(retry_every)

    t = threading.Thread(target=loop, name='pixlite-model-fetch', daemon=True)
    with _S.lock:
        _S.thread = t
    t.start()
    return True


def retry_now(log=None):
    """User-triggered retry from the UI."""
    return start_background(log=log, retry_every=300)
