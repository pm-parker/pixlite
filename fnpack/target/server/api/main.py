"""FastAPI service — batch retouch API for NAS photos."""
import os, io, uuid, json, threading, re, glob, time
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import numpy as np, cv2
from engine.pipeline import Pipeline
from engine.batch import BatchJob, collect_images

# ---- LUT filters (PixCake 92-filter system) ----
# 部署配置化: 环境变量优先 (fpk/Docker 部署时指向挂载目录), 缺省退回开发机相对路径
LUT_DIR = os.environ.get('PIXLITE_LUT_DIR') or os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'pixcake-reverse', 'effect_config_decrypted', 'Filters'))

app = FastAPI(title='PixLite')

# ---- 轻量鉴权 (fpk/NAS 部署用): 设 PIXLITE_TOKEN 后, 非 127.0.0.1 请求必须带 X-API-Key ----
API_TOKEN = os.environ.get('PIXLITE_TOKEN', '')

@app.middleware('http')
async def _auth(request, call_next):
    if API_TOKEN:
        client = request.client.host if request.client else ''
        is_local = client in ('127.0.0.1', '::1', 'localhost')
        path = request.url.path
        if not is_local and path.startswith('/api/') and path not in ('/api/health',):
            provided = request.headers.get('X-API-Key') or request.query_params.get('key', '')
            if provided != API_TOKEN:
                return JSONResponse({'error': 'unauthorized'}, status_code=401)
    return await call_next(request)

@app.get('/api/health')
def health():
    return {'ok': True}

@app.get('/api/debug/report')
def debug_report(frontend_events: str = ''):
    """诊断报告: 环境 + 服务端最近错误 + 前端传入的报错。用于用户一键复制转发。"""
    import platform, sys, time as _t
    try:
        from engine.gpu import gpu_status
        gpu = gpu_status()
    except Exception as e:
        gpu = {'error': str(e)}
    report = {
        'generated_at': _t.strftime('%Y-%m-%d %H:%M:%S'),
        'app': 'PixLite 0.1.0',
        'python': sys.version.split()[0],
        'os': platform.platform(),
        'gpu': gpu,
        'lut_dir_ok': os.path.isdir(LUT_DIR),
        'presets_dir_ok': os.path.isdir(PRESETS),
        'server_events': _recent_events[-20:],
        'frontend_events': (json.loads(frontend_events) if frontend_events else [])[:20],
    }
    # 模型完整性
    try:
        import glob as _g
        models = _g.glob(os.path.join(os.path.dirname(__file__), '..', '..', 'engine', 'perception', 'models', '*.onnx'))
        report['models_found'] = len(models)
    except Exception:
        report['models_found'] = -1
    return report

@app.get('/api/gpu')
def gpu_info():
    """GPU 自适应状态: 厂商探测 + 建议 EP + 引导安装命令"""
    from engine.gpu import gpu_status
    return gpu_status()
WORK = os.environ.get('PIXLITE_WORK_DIR') or os.path.join(os.path.dirname(__file__), '..', 'work')
os.makedirs(WORK, exist_ok=True)
PRESETS = os.environ.get('PIXLITE_PRESETS_DIR') or os.path.join(os.path.dirname(__file__), '..', '..', 'presets')

jobs = {}

# ---- 错误收集 (诊断报告用): 最近 50 条服务端错误/警告 ----
_recent_events = []
def _log_event(kind, msg):
    import time as _t
    _recent_events.append({'ts': _t.time(), 'kind': kind, 'msg': str(msg)[:500]})
    del _recent_events[:-50]

def _restore_jobs():
    """重启恢复: work/ 里 <id>_out.jpg 存在且比对应 _src 新 → 该 job 已完成, 重建条目防止前端 localStorage 的 jobId 全部失效。"""
    n = 0
    for p in glob.glob(os.path.join(WORK, '*_out.jpg')):
        jid = os.path.basename(p)[:-8]
        if jid in jobs:
            continue
        jobs[jid] = {'status': 'done', 'progress': 100, 'output': p}
        n += 1
    return n

_restore_jobs()

class DictPipeline(Pipeline):
    def __init__(self, preset):
        self.preset = preset

OVERRIDABLE = {
    'skin_retouch': ('skin_retouch', ['strength', 'texture_keep']),
    'skin_tone_h': ('skin_tone', []),
    'teeth_whiten': ('teeth_whiten', ['degree']),
    'eye_brighten': ('eye_brighten', ['degree']),
    'grain_d': ('grain', ['density']),
    'facesurgery_d': ('facesurgery', ['degree']),
}

def build_preset(base_name, overrides, routing=None, person=None):
    """Load preset JSON, apply client slider overrides (op.param=value).
    Unknown ops (tone/hsl/white_balance/lut) are appended as new nodes.
    routing: {group: {op.param: v}} auto per-person routing; person: manual override."""
    preset = json.load(open(os.path.join(PRESETS, base_name + '.json'), encoding='utf-8'))
    if routing:
        preset['routing'] = routing
    if person:
        for n in preset['nodes']:
            if n['op'] == 'face_detect':
                n.setdefault('params', {})['person'] = person
    if not overrides:
        return preset
    # group flat dotted keys by op prefix
    by_op = {}
    for key, val in overrides.items():
        if '.' not in key:
            continue
        if key.startswith('rt.'):        # routing passthrough (engine-scale values)
            key = key[3:]
        op, param = key.split('.', 1)
        by_op.setdefault(op, {})[param] = val
    for node in preset.get('nodes', []):
        op = node['op']
        if op in by_op:
            node.setdefault('params', {}).update(by_op.pop(op))
    # hsl special: keys hsl.0.h .. hsl.7.l -> nested dict
    if 'hsl' in by_op:
        bands = {}
        for k, v in by_op.pop('hsl').items():
            parts = k.split('.')
            if len(parts) == 2:
                bands.setdefault(int(parts[0]), {})[parts[1]] = v
        by_op['hsl'] = bands
    if 'lut' in by_op and 'filter' in by_op['lut']:
        fid = by_op['lut'].pop('filter')
        lut_path = os.path.join(LUT_DIR, fid, 'filter.png')
        if not os.path.isfile(lut_path):
            raise ValueError(f'LUT 滤镜不存在: {fid} (应为 /api/filters 返回的完整 ID, 如 FT00000001)')
        by_op['lut']['path'] = lut_path
    # F5 local color masks: by_op['mask'] = {'<region>.<op>.<param>': v} (mask prefix stripped)
    masks = by_op.pop('mask', {})
    for key, val in masks.items():
        parts = key.split('.')          # region, op, [param...]
        if len(parts) >= 2:
            region, mop = parts[0], parts[1]
            param = '.'.join(parts[2:])
            node = {'op': mop, 'params': {param: val} if param else {}, 'mask': region}
            preset.setdefault('nodes', []).append(node)
    # curves control points: convert flat dict {rgb:[..]} untouched; selective_color
    # flat keys sc.<color>.<comp> handled above via op prefix 'selective_color'
    ORDER = ['ai_calibrate', 'white_balance', 'tone', 'hsl', 'lut']
    for op in ORDER:
        if op in by_op:
            preset.setdefault('nodes', []).append({'op': op, 'params': by_op.pop(op)})
    for op in list(by_op.keys()):
        preset.setdefault('nodes', []).append({'op': op, 'params': by_op.pop(op)})
    return preset


def _load_image(path):
    """Read image; RAW formats (.dng/.cr2/.nef/.arw/.rw2/.pef/.raw) via rawpy postprocess."""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.dng', '.cr2', '.nef', '.arw', '.rw2', '.pef', '.raw', '.orf', '.raf', '.srw'):
        import rawpy
        with rawpy.imread(path) as rp:
            rgb = rp.postprocess(use_camera_wb=True, output_bps=8, no_auto_bright=False)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)

def _gc_jobs(max_jobs=200, max_files=500):
    """防内存/磁盘无限增长: jobs dict 超限删最旧; work 目录超限删最旧 _src (保留 _out 供导出)."""
    if len(jobs) > max_jobs:
        for k in sorted(jobs, key=lambda x: jobs[x].get('ts', 0))[:len(jobs) - max_jobs]:
            jobs.pop(k, None)
    srcs = sorted(glob.glob(os.path.join(WORK, '*_src*')), key=os.path.getmtime)
    for p in srcs[:max(0, len(srcs) - max_files)]:
        try: os.remove(p)
        except OSError: pass

def _run_job(job_id, path, preset, out_path, overrides=None, routing=None, person=None):
    jobs[job_id]['ts'] = time.time()
    try:
        bgr = _load_image(path)
        pipe = DictPipeline(build_preset(preset, overrides, routing, person))
        out = pipe.run(bgr, progress_cb=lambda p: jobs[job_id].update(progress=p))
        cv2.imwrite(out_path, out)
        jobs[job_id].update(status='done', output=out_path)
    except Exception as e:
        import traceback
        _log_event('job_error', traceback.format_exc()[-1500:])
        jobs[job_id].update(status='error', error=str(e)[:300])
    finally:
        try: _gc_jobs()
        except Exception: pass

@app.post('/api/retouch')
async def retouch(file: UploadFile = File(...), preset: str = Form('wedding_basic'),
                  overrides: str = Form(''), routing: str = Form(''), person: str = Form('')):
    job_id = uuid.uuid4().hex[:12]
    src = os.path.join(WORK, job_id + '_src' + os.path.splitext(file.filename)[1])
    out = os.path.join(WORK, job_id + '_out.jpg')
    open(src, 'wb').write(await file.read())
    jobs[job_id] = {'status': 'running', 'progress': 0, 'output': out}
    # 参数损坏应显式报 400，禁止静默吞掉（否则用户滑杆设置全部丢失且无提示）
    try:
        ov = json.loads(overrides) if overrides else None
    except Exception as e:
        return JSONResponse({'error': f'overrides JSON 无效: {e}'}, status_code=400)
    try:
        rt = json.loads(routing) if routing else None
    except Exception as e:
        return JSONResponse({'error': f'routing JSON 无效: {e}'}, status_code=400)
    threading.Thread(target=_run_job, args=(job_id, src, preset, out, ov, rt, person or None), daemon=True).start()
    return {'job': job_id}

@app.get('/api/job/{job_id}')
def job(job_id: str):
    return jobs.get(job_id, {'status': 'unknown'})

@app.get('/api/result/{job_id}')
def result(job_id: str):
    j = jobs.get(job_id)
    if j and j['status'] == 'done':
        return FileResponse(j['output'])
    return JSONResponse(j or {}, 404)

@app.get('/api/preset_library')
def preset_library():
    p = os.path.join(PRESETS, 'preset_library.json')
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8'))
    return []

@app.get('/api/makeup_suits')
def makeup_suits():
    """原厂 24 套整套妆清单 (参数配方复刻)."""
    try:
        from engine.effects.makeup_suit import list_suits
        return list_suits()
    except Exception as e:
        return JSONResponse({'error': str(e)[:200]}, 500)

@app.get('/api/presets')
def presets():
    return [f[:-5] for f in os.listdir(PRESETS) if f.endswith('.json')]

# ---- batch directory mode ----
batch_jobs = {}

def _run_batch(job_id, src_dir, dst_dir, preset, recursive, workers):
    bj = batch_jobs[job_id]['job']
    def cb(done, total):
        batch_jobs[job_id].update(done=done, total=total,
                                  progress=round(done / max(total, 1), 3))
    try:
        summary = bj.run(progress_cb=cb)
        batch_jobs[job_id].update(status='done', **summary)
    except Exception as e:
        batch_jobs[job_id].update(status='error', error=str(e)[:300])

@app.post('/api/batch')
async def batch(src_dir: str = Form(...), dst_dir: str = Form(''),
                preset: str = Form('wedding_basic'), recursive: bool = Form(True),
                workers: int = Form(2)):
    workers = max(1, min(workers, 8))  # 上限防资源耗尽
    if not os.path.isdir(src_dir):
        return JSONResponse({'error': 'src_dir not found'}, 400)
    dst = dst_dir or (src_dir.rstrip('\\/') + '_pixlite')
    files = collect_images(src_dir, recursive)
    job_id = uuid.uuid4().hex[:12]
    pipe_holder = Pipeline(os.path.join(PRESETS, preset + '.json'))
    bj = BatchJob(pipe_holder, src_dir, dst, recursive=recursive, workers=workers)
    batch_jobs[job_id] = {'status': 'running', 'done': 0, 'total': len(files),
                          'progress': 0, 'dst': dst, 'job': bj}
    threading.Thread(target=_run_batch, args=(job_id, src_dir, dst, preset, recursive, workers), daemon=True).start()
    return {'job': job_id, 'total': len(files), 'dst': dst}

@app.get('/api/batch/{job_id}')
def batch_status(job_id: str):
    j = batch_jobs.get(job_id)
    if not j:
        return JSONResponse({'status': 'unknown'}, 404)
    return {k: v for k, v in j.items() if k != 'job'}

DENY_PREFIX = ('c:/windows', 'c:/users/administrator/appdata')

def _path_ok(p):
    """内部工具轻量防护: 拒系统敏感目录。"""
    rp = os.path.realpath(p).lower().replace(os.sep, '/')
    return not any(rp.startswith(d) for d in DENY_PREFIX)

@app.get('/api/image')
def serve_image(path: str):
    """Serve an image file from disk (NAS path) for canvas/thumbnails."""
    if not _path_ok(path):
        return JSONResponse({'error': 'forbidden'}, 403)
    if not os.path.isfile(path):
        return JSONResponse({'error': 'not found'}, 404)
    ext = os.path.splitext(path)[1].lower()
    mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
            '.webp': 'image/webp'}.get(ext, 'application/octet-stream')
    if mime == 'application/octet-stream':
        return JSONResponse({'error': 'unsupported'}, 400)
    return FileResponse(path, media_type=mime)

@app.get('/api/browse')
def browse(path: str = ''):
    """List dirs/images for NAS path picker."""
    if not path:
        import string
        dirs = [f'{c}:\\' for c in 'CDEFGH' if os.path.isdir(f'{c}:\\')]
        # NAS UNC 根: 枚举已映射盘之外, 加常见 NAS 主机入口提示由前端手输; 这里补 rclone/网盘映射盘
        return {'dirs': dirs}
    if not os.path.isdir(path):
        return JSONResponse({'error': 'not a dir'}, 400)
    try:
        entries = os.listdir(path)
    except PermissionError:
        return JSONResponse({'error': 'permission'}, 403)
    dirs = [e for e in entries if os.path.isdir(os.path.join(path, e)) and not e.startswith('.')]
    imgs = [e for e in entries if os.path.splitext(e)[1].lower() in
            ('.jpg', '.jpeg', '.png', '.webp', '.dng', '.cr2', '.arw', '.nef')]
    return {'current': path, 'dirs': dirs[:500], 'images': imgs[:500], 'n_images': len(imgs)}

# ---- LUT filters (PixCake 92-filter system) ----
LUT_NAMES_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'presets', 'lut_names.json'))
try:
    LUT_NAMES = json.load(open(LUT_NAMES_FILE, encoding='utf-8'))
except Exception:
    LUT_NAMES = {}

@app.get('/api/filters')
def filters():
    out = []
    for d in sorted(glob.glob(os.path.join(LUT_DIR, 'FT*'))):
        name = os.path.basename(d)
        out.append({'id': name, 'label': LUT_NAMES.get(name, name),
                    'name_cn': LUT_NAMES.get(name, '')})
    return out

@app.get('/api/luts')
def luts():
    """F3: LUT id -> 中文名称列表."""
    return [{'id': k, 'name_cn': v} for k, v in sorted(LUT_NAMES.items())]

@app.get('/api/exif')
def exif(path: str):
    """F2: EXIF via Pillow — 相机/镜头/焦距/光圈/快门/ISO/时间/GPS."""
    if not os.path.isfile(path):
        return JSONResponse({'error': 'not found'}, 404)
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS
        img = Image.open(path)
        raw = img.getexif()
        ex = {}
        ifd = {}
        for tid, val in raw.items():
            ex[TAGS.get(tid, str(tid))] = val
        for ifd_name, parse in (('Exif', getattr(img, '_getexif', None)),):
            pass
        # sub-IFDs
        try:
            from PIL.Image import Exif
            sub = raw.get_ifd(0x8769) if hasattr(raw, 'get_ifd') else {}
            for tid, val in sub.items():
                ex[TAGS.get(tid, str(tid))] = val
        except Exception:
            pass
        def _g(*keys, default=''):
            for k in keys:
                for ek, ev in ex.items():
                    if ek == k:
                        return ev
            return default
        def _rat(v):
            try:
                n, d = v
                return round(n / d, 2) if d else default
            except Exception:
                return v
        focal = _g('FocalLength', 'FocalLengthIn35mmFilm')
        if isinstance(focal, tuple):
            focal = round(focal[0] / focal[1], 1) if focal[1] else focal[0]
        ape = _g('FNumber')
        if isinstance(ape, tuple):
            ape = round(ape[0] / ape[1], 1) if ape[1] else ape[0]
        expo = _g('ExposureTime')
        if isinstance(expo, tuple) and expo[0] and expo[1]:
            expo = f'{expo[0]}/{expo[1]}'
        # GPS
        gps = {}
        try:
            gi = raw.get_ifd(0x8825) if hasattr(raw, 'get_ifd') else {}
            for tid, val in gi.items():
                gps[GPSTAGS.get(tid, str(tid))] = str(val)
        except Exception:
            pass
        return {
            'camera': str(_g('Make', 'Model')) or str(_g('Model')),
            'model': str(_g('Model')),
            'lens': str(_g('LensModel', 'LensSpecification')),
            'focal': focal,
            'aperture': ape,
            'shutter': expo,
            'iso': _g('ISOSpeedRatings', 'PhotographicSensitivity', default=''),
            'datetime': str(_g('DateTimeOriginal', 'DateTime')),
            'gps': gps,
            'width': img.width, 'height': img.height,
        }
    except Exception as e:
        return JSONResponse({'error': str(e)[:200]}, 500)


@app.get('/api/filter-thumb/{fid}')
def filter_thumb(fid: str):
    """Serve a LUT texture png directly (browser renders as thumbnail)."""
    if not re.match(r'^FT\d+$', fid):
        return JSONResponse({'error': 'bad id'}, 400)
    p = os.path.join(LUT_DIR, fid, 'filter.png')
    if not os.path.isfile(p):
        return JSONResponse({'error': 'not found'}, 404)
    return FileResponse(p, media_type='image/png')

# ---- AI color-match reference image upload ----
@app.post('/api/ref')
async def upload_ref(file: UploadFile = File(...)):
    job_id = 'ref' + uuid.uuid4().hex[:10]
    p = os.path.join(WORK, job_id + os.path.splitext(file.filename)[1])
    open(p, 'wb').write(await file.read())
    return {'path': p, 'url': '/api/image?path=' + p.replace('\\', '/')}

app.mount('/', StaticFiles(directory=os.path.join(os.path.dirname(__file__), '..', 'web'), html=True), name='web')
