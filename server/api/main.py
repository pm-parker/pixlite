"""FastAPI service — batch retouch API for NAS photos."""
import os, io, uuid, json, threading, re, glob, time, hmac
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import numpy as np, cv2
from engine.pipeline import Pipeline
from engine.batch import BatchJob, collect_images

# ---- LUT filters (PixCake 92-filter system) ----
# 部署配置化: 环境变量优先 (fpk/Docker 部署时指向挂载目录), 缺省退回开发机相对路径
LUT_DIR = os.environ.get('PIXLITE_LUT_DIR') or os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'pixcake-reverse', 'effect_config_decrypted', 'Filters'))

def _app_version():
    """fnOS 注入 TRIM_APPVER; 其他环境读 fnpack/manifest 的 version。"""
    v = os.environ.get('TRIM_APPVER') or os.environ.get('PIXLITE_VERSION')
    if v:
        return v
    try:
        mf = os.path.join(os.path.dirname(__file__), '..', '..', 'fnpack', 'manifest')
        for line in open(mf, encoding='utf-8'):
            if line.split('=')[0].strip() == 'version':
                return line.split('=', 1)[1].strip()
    except OSError:
        pass
    return 'dev'

APP_VERSION = _app_version()
app = FastAPI(title='PixLite', version=APP_VERSION)

# ---- 鉴权 (Issue #1): fail closed ----
# - 设 PIXLITE_TOKEN 后, 非回环请求必须带正确的 X-API-Key。
# - 未设 token 时, 非回环请求一律 401 (旧代码: 未设 token 直接放行, 而服务绑定 0.0.0.0)。
#   确需在可信内网无 token 运行, 显式设置 PIXLITE_ALLOW_NO_TOKEN=1。
# - 弱 token (< 16 字符) 启动时告警。
# - 浏览器 <img>/<a> 无法带自定义头, 因此仅对少数 GET 资源路由
#   (/api/image /api/result /api/filter-thumb) 接受 ?key=; 其余 API 只认 X-API-Key 头。
API_TOKEN = os.environ.get('PIXLITE_TOKEN', '')
ALLOW_NO_TOKEN = os.environ.get('PIXLITE_ALLOW_NO_TOKEN', '') == '1'
_LOOPBACK = ('127.0.0.1', '::1', 'localhost')
_PUBLIC_API = ('/api/health',)
_QUERY_KEY_OK = ('/api/image', '/api/result/', '/api/filter-thumb/')

def _auth_decision(client, path, header_key, query_key, via_gateway=False):
    """返回 None 表示放行, 否则返回 (status, error)。纯函数, 便于测试。"""
    if not path.startswith('/api/') or path in _PUBLIC_API or client in _LOOPBACK:
        return None
    if via_gateway:
        return None   # 飞牛统一网关已校验 NAS 登录态 (见 _GatewayMiddleware)
    if not API_TOKEN:
        return None if ALLOW_NO_TOKEN else (401, 'server has no PIXLITE_TOKEN configured; remote access disabled')
    provided = header_key or ''
    if not provided and query_key and path.startswith(_QUERY_KEY_OK):
        provided = query_key
    if not hmac.compare_digest(provided.encode(), API_TOKEN.encode()):
        return (401, 'unauthorized')
    return None


# ---- 飞牛统一网关 (免密钥访问) ----
# 桌面图标 → https://<NAS>/app/fnnas-pixlite/ → fnOS 校验 NAS 登录态 → 转发到本服务的
# Unix Socket (PIXLITE_GATEWAY_SOCKET, 由 server/run.py 监听), 并附带 X-Trim-Userid /
# X-Trim-Username / X-Trim-Isadmin。经该 socket 进来的请求视为已认证, 用户无需任何密钥。
# 判定依据是 ASGI scope['server'] (监听端点, 由内核 accept 决定, 客户端无法伪造),
# 而不是客户端可写的请求头; TCP 端口上的 X-Trim-* 头一律剥离。
GATEWAY_SOCKET = os.environ.get('PIXLITE_GATEWAY_SOCKET', '')
GATEWAY_PREFIX = os.environ.get('PIXLITE_GATEWAY_PREFIX', '/app/fnnas-pixlite').rstrip('/')
_TRIM_HEADERS = (b'x-trim-userid', b'x-trim-username', b'x-trim-isadmin')


def _is_gateway_scope(scope):
    srv = scope.get('server')
    return bool(GATEWAY_SOCKET) and bool(srv) and srv[0] == GATEWAY_SOCKET


class _GatewayMiddleware:
    """纯 ASGI 中间件: 识别网关流量、去掉网关路径前缀、剥离伪造的 X-Trim-* 头。"""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        via = _is_gateway_scope(scope)
        hdrs = scope.get('headers') or []
        if not via:
            hdrs = [(k, v) for k, v in hdrs if k.lower() not in _TRIM_HEADERS]
        path = scope.get('path', '')
        if GATEWAY_PREFIX and (path == GATEWAY_PREFIX or path.startswith(GATEWAY_PREFIX + '/')):
            if path == GATEWAY_PREFIX and scope['type'] == 'http':
                # 前端用相对路径请求 API, 页面 URL 必须以 / 结尾
                q = scope.get('query_string', b'')
                loc = (GATEWAY_PREFIX + '/' + ('?' + q.decode('latin-1') if q else '')).encode()
                await send({'type': 'http.response.start', 'status': 308,
                            'headers': [(b'location', loc), (b'content-length', b'0')]})
                await send({'type': 'http.response.body', 'body': b''})
                return
            path = path[len(GATEWAY_PREFIX):] or '/'
        state = dict(scope.get('state') or {})
        state['via_gateway'] = via
        scope = dict(scope, path=path, raw_path=path.encode(), headers=hdrs, state=state)
        return await self.app(scope, receive, send)


def _gateway_user(request):
    if not getattr(request.state, 'via_gateway', False):
        return None
    h = request.headers
    return {'uid': h.get('x-trim-userid', ''), 'username': h.get('x-trim-username', ''),
            'is_admin': h.get('x-trim-isadmin', '') == 'true'}


@app.middleware('http')
async def _auth(request, call_next):
    client = request.client.host if request.client else ''
    via = getattr(request.state, 'via_gateway', False)
    d = _auth_decision(client, request.url.path, request.headers.get('X-API-Key'),
                       request.query_params.get('key'), via_gateway=via)
    if d:
        return JSONResponse({'error': d[1], 'auth_required': True}, status_code=d[0])
    return await call_next(request)


app.add_middleware(_GatewayMiddleware)

@app.get('/api/health')
def health(request: Request):
    """公开接口: 告诉前端当前连接的认证方式, 便于免密钥 / 引导填写密钥。"""
    client = request.client.host if request.client else ''
    if getattr(request.state, 'via_gateway', False):
        mode = 'gateway'
    elif client in _LOOPBACK:
        mode = 'local'
    elif API_TOKEN:
        mode = 'token'
    else:
        mode = 'open' if ALLOW_NO_TOKEN else 'disabled'
    return {'ok': True, 'auth': mode, 'version': APP_VERSION}


@app.get('/api/auth/info')
def auth_info(request: Request):
    """网关模式下给 NAS 管理员展示"远程直连密钥" (直接访问 :8300 或第三方客户端时使用),
    取代 SSH 读取 var/api_token。非管理员 / 非网关请求不返回密钥。"""
    u = _gateway_user(request)
    out = {'via_gateway': bool(u), 'user': (u or {}).get('username', '')}
    if u and u['is_admin'] and API_TOKEN:
        out['direct_token'] = API_TOKEN
    return out

def _parse_frontend_events(raw):
    """前端事件 JSON 损坏/非列表时不应让诊断接口本身 500 (诊断报告正是出错时才用)。"""
    if not raw:
        return []
    try:
        ev = json.loads(raw)
    except Exception:
        return [{'kind': 'unparsable_frontend_events', 'raw': raw[:500]}]
    return ev[:20] if isinstance(ev, list) else [ev]

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
        'app': f'PixLite {APP_VERSION}',
        'python': sys.version.split()[0],
        'os': platform.platform(),
        'gpu': gpu,
        'lut_dir_ok': os.path.isdir(LUT_DIR),
        'presets_dir_ok': os.path.isdir(PRESETS),
        'server_events': _recent_events[-20:],
        'frontend_events': _parse_frontend_events(frontend_events),
    }
    # 模型完整性 (自动下载状态)
    try:
        st = model_store.status()
        report['models_found'] = st['ready_count']
        report['models'] = {k: v for k, v in st.items() if k != 'models'}
    except Exception:
        report['models_found'] = -1
    return report


# ---- 模型自动下载 (用户零操作) ----
# 启动时后台线程检查 8 个模型: full 包内置的直接用; 缺失的自动下载到 PIXLITE_MODELS_DIR
# (fnOS: ${TRIM_PKGVAR}/models, 升级不丢), 断点续传 + 多源切换 + sha256 校验, 失败每 5 分钟重试。
# 前端轮询 /api/models/status 显示进度; 模型未就绪时精修返回 503 + 友好提示。
from engine.perception import model_store
from engine.gpu import ModelUnavailable

if os.environ.get('PIXLITE_AUTO_DOWNLOAD_MODELS', '1') == '1':
    model_store.start_background(log=lambda m: _log_event('models', m))


def _models_not_ready():
    """模型未就绪时的统一 503 响应 (前端据此显示下载进度, 而不是报错)。
    PIXLITE_REQUIRE_MODELS=0 关闭该检查 (单元测试 / 只用滤镜等不需模型的开发场景)。"""
    if os.environ.get('PIXLITE_REQUIRE_MODELS', '1') != '1':
        return None
    st = model_store.status()
    if st['ready']:
        return None
    msg = (f'AI 模型首次使用需要下载，已完成 {st["percent"]}%，下载完成后即可精修'
           if st['phase'] != 'failed' else f'AI 模型下载失败，系统会自动重试：{st["error"][:120]}')
    body = {k: v for k, v in st.items() if k != 'models'}
    body.update(error=msg, models_pending=True, download_error=st['error'])
    return JSONResponse(body, status_code=503)


@app.get('/api/models/status')
def models_status():
    return model_store.status()


@app.post('/api/models/retry')
def models_retry():
    started = model_store.retry_now(log=lambda m: _log_event('models', m))
    return {'started': started, **{k: v for k, v in model_store.status().items() if k != 'models'}}

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

_PRESET_RE = re.compile(r'^[\w\-]+$')

def _preset_path(name):
    """preset 名只允许 [字母数字_-]: 旧代码直接拼路径, preset='../../x' 可读任意 .json。"""
    if not name or not _PRESET_RE.match(name):
        raise ValueError(f'非法预设名: {name!r}')
    p = os.path.join(PRESETS, name + '.json')
    if not os.path.isfile(p):
        raise ValueError(f'预设不存在: {name}')
    return p

def _load_preset(name):
    with open(_preset_path(name), encoding='utf-8') as fh:
        d = json.load(fh)
    if not isinstance(d, dict) or not isinstance(d.get('nodes'), list):
        raise ValueError(f'{name} 不是可执行预设 (缺 nodes)')
    return d

def build_preset(base_name, overrides, routing=None, person=None):
    """Load preset JSON, apply client slider overrides (op.param=value).
    Unknown ops (tone/hsl/white_balance/lut) are appended as new nodes.
    routing: {group: {op.param: v}} auto per-person routing; person: manual override."""
    preset = _load_preset(base_name)
    if routing:
        preset['routing'] = routing
    if person:
        for n in preset['nodes']:
            if n['op'] == 'face_detect':
                n.setdefault('params', {})['person'] = person
    if not overrides:
        return preset
    # Issue #1: 追色参考图是客户端给的服务器路径, 同样必须在允许根目录内
    ref = overrides.get('color_match.ref_image') or overrides.get('rt.color_match.ref_image')
    if ref and not _path_ok(ref):
        raise ValueError('color_match.ref_image 路径不在允许访问的目录内')
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
    if 'lut' in by_op and not by_op['lut'].get('filter', 'x'):
        by_op.pop('lut')          # 空 filter = 未选滤镜 (前端"无"), 不应整单报错
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


# ---- 资源上限 (Issue #2), 均可用环境变量调整 ----
def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default
MAX_UPLOAD_BYTES = _env_int('PIXLITE_MAX_UPLOAD_MB', 200) * 1024 * 1024   # 单文件 (RAW 可达 100MB+)
MAX_PIXELS = _env_int('PIXLITE_MAX_PIXELS', 100_000_000)                  # 解码后像素数 (约 1 亿)
MAX_ACTIVE_JOBS = _env_int('PIXLITE_MAX_ACTIVE_JOBS', 4)                  # 同时运行的单图精修
MAX_BATCH_FILES = _env_int('PIXLITE_MAX_BATCH_FILES', 5000)               # 单批图片数
MAX_ACTIVE_BATCHES = _env_int('PIXLITE_MAX_ACTIVE_BATCHES', 1)            # 同时运行的批量任务
MAX_BATCH_WORKERS = _env_int('PIXLITE_MAX_BATCH_WORKERS', 8)
JOB_TTL_SEC = _env_int('PIXLITE_JOB_TTL_HOURS', 72) * 3600                # work 目录产物保留时长

async def _save_upload(file, dst):
    """流式落盘并限制字节数; 超限删除半成品并抛 ValueError。"""
    n = 0
    try:
        with open(dst, 'wb') as fh:
            while chunk := await file.read(1024 * 1024):
                n += len(chunk)
                if n > MAX_UPLOAD_BYTES:
                    raise ValueError(f'文件超过上传上限 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB (PIXLITE_MAX_UPLOAD_MB)')
                fh.write(chunk)
    except BaseException:
        try: os.remove(dst)
        except OSError: pass
        raise
    return n

def _check_dims(path):
    """解码前读取图像头尺寸, 超过 MAX_PIXELS 拒绝 (防解压炸弹)。"""
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return   # 非 PIL 可识别 (RAW 等): 交给解码器 + OPENCV_IO_MAX_IMAGE_PIXELS
    if w * h > MAX_PIXELS:
        raise ValueError(f'图片尺寸 {w}x{h} 超过上限 {MAX_PIXELS} 像素 (PIXLITE_MAX_PIXELS)')

def _load_image(path):
    """Read image; RAW formats (.dng/.cr2/.nef/.arw/.rw2/.pef/.raw) via rawpy postprocess."""
    _check_dims(path)
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
        # 不淘汰进行中的 job (否则前端轮询拿到 unknown 判失败)
        done = [k for k in jobs if jobs[k].get('status') != 'running']
        for k in sorted(done, key=lambda x: jobs[x].get('ts', 0))[:len(jobs) - max_jobs]:
            jobs.pop(k, None)
    running_src = {os.path.basename(j.get('src', '')) for j in list(jobs.values())
                   if j.get('status') == 'running'}
    srcs = sorted(glob.glob(os.path.join(WORK, '*_src*')), key=os.path.getmtime)
    srcs = [p for p in srcs if os.path.basename(p) not in running_src]
    for p in srcs[:max(0, len(srcs) - max_files)]:
        try: os.remove(p)
        except OSError: pass
    # Issue #2: TTL 清理 — 超过 JOB_TTL_SEC 的 work 产物 (_src/_out/ref/uploads) 与对应 job 条目
    now = time.time()
    running_out = {j.get('output') for j in list(jobs.values()) if j.get('status') == 'running'}
    for p in glob.glob(os.path.join(WORK, '*')) + glob.glob(os.path.join(WORK, 'uploads', '*')):
        try:
            if os.path.isfile(p) and p not in running_out and \
               os.path.basename(p) not in running_src and now - os.path.getmtime(p) > JOB_TTL_SEC:
                os.remove(p)
        except OSError:
            pass
    for k in [k for k, j in list(jobs.items()) if j.get('status') != 'running'
              and now - j.get('ts', now) > JOB_TTL_SEC]:
        jobs.pop(k, None)
    for k in [k for k, j in list(batch_jobs.items()) if j.get('status') != 'running'
              and now - j.get('ts', now) > JOB_TTL_SEC]:
        batch_jobs.pop(k, None)

def _run_job(job_id, path, preset, out_path, overrides=None, routing=None, person=None):
    jobs[job_id]['ts'] = time.time()
    try:
        bgr = _load_image(path)
        if bgr is None:
            raise ValueError('无法解码图片 (文件损坏或格式不支持)')
        pipe = DictPipeline(build_preset(preset, overrides, routing, person))
        out = pipe.run(bgr, progress_cb=lambda p: jobs[job_id].update(progress=p))
        ok, buf = cv2.imencode('.jpg', out, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            raise RuntimeError('结果编码失败')
        buf.tofile(out_path)   # unicode-safe (cv2.imwrite 在 Windows 中文路径下静默失败)
        jobs[job_id].update(status='done', output=out_path)
    except ModelUnavailable as e:
        jobs[job_id].update(status='error', error=str(e)[:300], models_pending=True)
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
    # 参数损坏应显式报 400，禁止静默吞掉（否则用户滑杆设置全部丢失且无提示）
    # 先校验再落盘/登记 job: 旧代码先写文件+登记 running 再 return 400, 留下永不结束的
    # running job 和孤儿上传文件
    try:
        ov = json.loads(overrides) if overrides else None
    except Exception as e:
        return JSONResponse({'error': f'overrides JSON 无效: {e}'}, status_code=400)
    try:
        rt = json.loads(routing) if routing else None
    except Exception as e:
        return JSONResponse({'error': f'routing JSON 无效: {e}'}, status_code=400)
    try:
        _preset_path(preset)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    nr = _models_not_ready()          # 先做廉价校验, 再判断模型; 不落盘上传文件
    if nr:
        return nr
    job_id = uuid.uuid4().hex[:12]
    ext = os.path.splitext(os.path.basename(file.filename or ''))[1].lower()
    if not re.match(r'^\.[a-z0-9]{1,5}$', ext):
        ext = '.jpg'
    src = os.path.join(WORK, job_id + '_src' + ext)
    out = os.path.join(WORK, job_id + '_out.jpg')
    if sum(1 for j in list(jobs.values()) if j.get('status') == 'running') >= MAX_ACTIVE_JOBS:
        return JSONResponse({'error': f'并发精修任务已达上限 {MAX_ACTIVE_JOBS}, 请稍后重试'}, 429)
    try:
        await _save_upload(file, src)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, 413)
    jobs[job_id] = {'status': 'running', 'progress': 0, 'output': out, 'src': src, 'ts': time.time()}
    threading.Thread(target=_run_job, args=(job_id, src, preset, out, ov, rt, person or None), daemon=True).start()
    return {'job': job_id}

@app.get('/api/job/{job_id}')
def job(job_id: str):
    j = jobs.get(job_id)
    if not j:
        return {'status': 'unknown'}
    return {k: v for k, v in j.items() if k != 'src'}   # 不向前端暴露服务器内部路径

@app.get('/api/result/{job_id}')
def result(job_id: str):
    j = jobs.get(job_id)
    if j and j['status'] == 'done' and os.path.isfile(j['output']):
        return FileResponse(j['output'], media_type='image/jpeg')
    return JSONResponse({k: v for k, v in (j or {}).items() if k != 'src'} or {'status': 'unknown'}, 404)

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
_batch_lock = threading.Lock()

def _run_batch(job_id, src_dir, dst_dir, preset, recursive, workers):
    bj = batch_jobs[job_id]['job']
    def cb(done, total):
        batch_jobs[job_id].update(done=done, total=total,
                                  progress=round(done / max(total, 1), 3))
    try:
        summary = bj.run(progress_cb=cb)
        batch_jobs[job_id].update(status='cancelled' if bj.cancelled else 'done', **summary)
    except Exception as e:
        batch_jobs[job_id].update(status='error', error=str(e)[:300])

@app.post('/api/batch')
async def batch(src_dir: str = Form(...), dst_dir: str = Form(''),
                preset: str = Form('wedding_basic'), recursive: bool = Form(True),
                workers: int = Form(2)):
    workers = max(1, min(workers, MAX_BATCH_WORKERS))  # 上限防资源耗尽
    if not _path_ok(src_dir):
        return JSONResponse({'error': 'forbidden'}, 403)
    if not os.path.isdir(src_dir):
        return JSONResponse({'error': 'src_dir not found'}, 400)
    dst = dst_dir or (src_dir.rstrip('\\/') + '_pixlite')
    if not _path_ok(dst):
        return JSONResponse({'error': 'dst_dir forbidden'}, 403)
    with _batch_lock:
        if sum(1 for j in batch_jobs.values() if j.get('status') == 'running') >= MAX_ACTIVE_BATCHES:
            return JSONResponse({'error': f'已有 {MAX_ACTIVE_BATCHES} 个批量任务在运行, 请稍后再试'}, 429)
    try:
        pipe_holder = DictPipeline(_load_preset(preset))
    except ValueError as e:
        return JSONResponse({'error': str(e)}, 400)
    # 输出目录不能在源目录内: 递归模式下第二次运行会把上次输出当输入再精修一遍
    if os.path.realpath(dst).startswith(os.path.realpath(src_dir).rstrip(os.sep) + os.sep):
        return JSONResponse({'error': 'dst_dir 不能位于 src_dir 内部'}, 400)
    nr = _models_not_ready()
    if nr:
        return nr
    files = collect_images(src_dir, recursive)
    if len(files) > MAX_BATCH_FILES:
        return JSONResponse({'error': f'图片数 {len(files)} 超过单批上限 {MAX_BATCH_FILES} (PIXLITE_MAX_BATCH_FILES)'}, 413)
    job_id = uuid.uuid4().hex[:12]
    bj = BatchJob(pipe_holder, src_dir, dst, recursive=recursive, workers=workers)
    batch_jobs[job_id] = {'status': 'running', 'done': 0, 'total': len(files),
                          'progress': 0, 'dst': dst, 'job': bj, 'ts': time.time()}
    threading.Thread(target=_run_batch, args=(job_id, src_dir, dst, preset, recursive, workers), daemon=True).start()
    return {'job': job_id, 'total': len(files), 'dst': dst}

@app.post('/api/batch/{job_id}/cancel')
def batch_cancel(job_id: str):
    """Issue #2: 取消批量任务 (已在处理的图片完成后停止, 未开始的跳过)。"""
    j = batch_jobs.get(job_id)
    if not j:
        return JSONResponse({'status': 'unknown'}, 404)
    j['job'].cancel()
    return {'status': 'cancelling'}

@app.get('/api/batch/{job_id}')
def batch_status(job_id: str):
    j = batch_jobs.get(job_id)
    if not j:
        return JSONResponse({'status': 'unknown'}, 404)
    return {k: v for k, v in j.items() if k != 'job'}

# ---- 文件系统访问白名单 (Issue #1) ----
# 旧实现只拒绝两个 Windows 目录, POSIX 上任何路径 (/etc, /root, ...) 都可被读取/浏览/批处理。
# 现在所有接受客户端路径的接口 (browse/image/exif/batch/color_match.ref_image) 只允许
# realpath (解析符号链接后) 位于允许根目录之内。
# PIXLITE_ALLOWED_ROOTS: os.pathsep 分隔; 未设置时默认 = 工作目录 + 常见 NAS 数据卷。
def _default_roots():
    if os.name == 'nt':
        return [f'{c}:\\' for c in 'CDEFGH' if os.path.isdir(f'{c}:\\')]
    roots = sorted(glob.glob('/vol[0-9]*')) + ['/var/apps/fnnas.pixlite/share', '/share', '/mnt',
                                               '/media', '/home']
    return [r for r in roots if os.path.isdir(r)]

def _allowed_roots():
    env = os.environ.get('PIXLITE_ALLOWED_ROOTS')
    roots = [r for r in env.split(os.pathsep) if r] if env else _default_roots()
    roots = roots + [WORK]
    return [os.path.realpath(r) for r in roots]

DENY_PREFIX = ('c:/windows', 'c:/users/administrator/appdata')

def _path_ok(p):
    """realpath 必须落在允许根目录内 (防 ../ 与符号链接逃逸), 且不在系统敏感目录。"""
    if not p or '\x00' in p:
        return False
    try:
        rp = os.path.realpath(p)
    except (OSError, ValueError):
        return False
    low = rp.lower().replace(os.sep, '/')
    if any(low.startswith(d) for d in DENY_PREFIX):
        return False
    for r in _allowed_roots():
        if rp == r or rp.startswith(r.rstrip(os.sep) + os.sep):
            return True
    return False

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

@app.post('/api/upload')
async def upload_file(file: UploadFile = File(...), subdir: str = 'uploads'):
    """本机图片直传 (浏览器 file input → NAS work 目录), 返回保存路径。"""
    import time as _t, uuid as _u
    fn = os.path.basename(file.filename or 'upload.jpg')
    ext = os.path.splitext(fn)[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.dng', '.cr2', '.arw', '.nef'):
        return JSONResponse({'error': 'unsupported'}, 400)
    d = os.path.join(WORK, 'uploads')
    os.makedirs(d, exist_ok=True)
    name = f"{_t.strftime('%Y%m%d_%H%M%S')}_{_u.uuid4().hex[:6]}{ext}"
    dst = os.path.join(d, name)
    try:
        await _save_upload(file, dst)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, 413)
    return {'path': dst, 'name': fn, 'size': os.path.getsize(dst)}

@app.get('/api/browse')
def browse(path: str = ''):
    """List dirs/images for NAS path picker. 根列表 = 允许访问的根目录 (Issue #1: 不再列出 '/')。"""
    if not path:
        return {'dirs': [r for r in _allowed_roots() if os.path.isdir(r)]}
    if not _path_ok(path):
        return JSONResponse({'error': 'permission'}, 403)
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
    if not _path_ok(path):
        return JSONResponse({'error': 'forbidden'}, 403)
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
        def _num(v, nd=1):
            # Pillow 返回 IFDRational (非 tuple), 旧代码的 isinstance(tuple) 分支从不命中,
            # JSON 序列化后焦距/光圈会变成 "8.0" 之类不稳定格式; 统一转 float
            try:
                if isinstance(v, tuple):
                    return round(v[0] / v[1], nd) if v[1] else v[0]
                return round(float(v), nd) if v not in ('', None) else ''
            except Exception:
                return str(v)
        focal = _num(_g('FocalLength', 'FocalLengthIn35mmFilm'))
        ape = _num(_g('FNumber'))
        expo = _g('ExposureTime')
        try:
            ev = float(expo) if not isinstance(expo, tuple) else (expo[0] / expo[1] if expo[1] else 0)
            # 旧代码只处理 tuple, Pillow 实际返回 IFDRational → 原样透出 (如 1/85 s 显示为小数或 "1/85" 不定)
            expo = (f'1/{round(1 / ev)}' if 0 < ev < 1 else f'{ev:g}') if ev else ''
        except Exception:
            expo = str(expo)
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
    ext = os.path.splitext(os.path.basename(file.filename or ''))[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.bmp'):
        return JSONResponse({'error': 'unsupported'}, 400)
    p = os.path.join(WORK, job_id + ext)
    try:
        await _save_upload(file, p)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, 413)
    return {'path': p, 'url': '/api/image?path=' + p.replace('\\', '/')}

# server/web 是 webapp 的构建产物 (已 .gitignore): 旧代码无条件 mount, 目录不存在时
# 模块 import 即抛 RuntimeError → 新克隆 / Docker 镜像 (Dockerfile 不构建前端) 下服务起不来
WEB_DIR = os.path.join(os.path.dirname(__file__), '..', 'web')
if os.path.isdir(WEB_DIR):
    app.mount('/', StaticFiles(directory=WEB_DIR, html=True), name='web')
else:
    _log_event('warn', f'web dir missing: {WEB_DIR} (run `npm run build` in webapp/)')
