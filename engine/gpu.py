"""GPU execution provider 自适应工厂 — 环境变量 PIXLITE_EP 控制, auto 全厂商探测。

飞牛 fnOS 应用中心驱动包对应关系:
- NVIDIA: 应用中心装 Nvidia-Driver-560 → venv 装 onnxruntime-gpu → CUDAExecutionProvider
- AMD:    应用中心装 AMD ROCm 7.2     → venv 装 onnxruntime-rocm → ROCmExecutionProvider
- Intel:  核显驱动系统自带(i915)      → venv 装 onnxruntime-openvino → OpenVINOExecutionProvider
"""
import contextlib
import os
import subprocess

_nvidia_hint_paths = ('/proc/driver/nvidia/version', '/dev/nvidia0', '/usr/lib/x86_64-linux-gnu/libcuda.so.1')
_amd_hint_paths = ('/sys/class/drm/card0/device/vendor',)  # 0x1002 = AMD
_amd_rocm_paths = ('/opt/rocm', '/usr/lib/x86_64-linux-gnu/libamdhip64.so')

def _has_file_any(paths):
    return any(os.path.exists(p) for p in paths)

def _pci_vendor():
    """读 GPU PCI vendor id (0x10de=NVIDIA, 0x1002=AMD, 0x8086=Intel)"""
    with contextlib.suppress(Exception):
        for d in os.listdir('/sys/class/drm'):
            if d.startswith('card') and d[4:].isdigit():
                with open(f'/sys/class/drm/{d}/device/vendor') as fh:
                    return fh.read().strip()
    return None

def detect_gpu():
    """返回 (厂商, 建议 EP, 建议 pip 包, 引导说明)。探测不到 GPU 返回 ('none','cpu',None,None)。"""
    vendor = _pci_vendor()  # Windows 下为 None, 走别的探测
    if os.name == 'nt':
        txt = ''
        for cmd in (['powershell','-NoProfile','-Command','(Get-CimInstance Win32_VideoController).Name'],
                    ['wmic','path','win32_VideoController','get','name']):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
                txt = (r.stdout or '').lower()
                if txt.strip():
                    break
            except Exception:
                continue
        if 'nvidia' in txt or 'geforce' in txt or 'quadro' in txt or 'tesla' in txt:
            return ('nvidia', 'dml', 'onnxruntime-directml', 'Windows 下推荐 DirectML (免 CUDA 工具链)')
        if 'amd' in txt or 'radeon' in txt:
            return ('amd', 'dml', 'onnxruntime-directml', 'Windows 下推荐 DirectML (免 ROCm 工具链)')
        if 'intel' in txt and ('uhd' in txt or 'iris' in txt or 'arc' in txt or 'graphics' in txt):
            return ('intel', 'dml', 'onnxruntime-directml', 'Intel 核显走 DirectML')
        if txt.strip():
            return ('unknown', 'cpu', None, f'未识别的显卡: {txt.strip()[:60]}, 可手动设 PIXLITE_EP=dml 尝试')
        return ('unknown', 'cpu', None, None)
    # Linux / fnOS
    if vendor == '0x10de' or _has_file_any(_nvidia_hint_paths):
        return ('nvidia', 'cuda', 'onnxruntime-gpu',
                '飞牛应用中心安装 Nvidia-Driver-560 后: pip install onnxruntime-gpu, 设 PIXLITE_EP=cuda')
    if vendor == '0x1002' or _has_file_any(_amd_rocm_paths):
        has_rocm = _has_file_any(_amd_rocm_paths)
        has_mgx = _has_file_any(['/opt/rocm/bin/migraphx-driver', '/usr/bin/migraphx-driver'])
        # AMD 现实路径 (2026): onnxruntime 官方 ROCm EP 已在 1.23 移除, 迁移 = MIGraphX EP
        # AMD 官方 wheel (含 MIGraphX EP): pip install https://repo.radeon.com/rocm/manylinux/rocm-rel-6.4.1/onnxruntime_rocm-1.21.0-cp312-cp312-manylinux_2_28_x86_64.whl
        # 780M = gfx1103: ROCm 7.1+ 官方支持; 老版本可 HSA_OVERRIDE_GFX_VERSION=11.0.2 兼容跑
        if has_mgx or has_rocm:
            return ('amd', 'migraphx', 'onnxruntime-rocm(AMD官方wheel, 见github.com/ROCm/onnxruntime)',
                    '飞牛应用中心装「AMD MIGraphX」AI 引擎或「AMD ROCm 7.2」; 然后 pip 安装 AMD 官方 '
                    'onnxruntime_rocm wheel (repo.radeon.com), 设 PIXLITE_EP=migraphx; '
                    '780M(gfx1103) 若报不支持, 启动前 export HSA_OVERRIDE_GFX_VERSION=11.0.2')
        return ('amd', 'cpu', None, '飞牛应用中心装「AMD MIGraphX」AI 引擎后重启应用')
    if vendor == '0x8086':
        return ('intel', 'openvino', 'onnxruntime-openvino',
                'Intel 核显: pip install onnxruntime-openvino, 设 PIXLITE_EP=openvino')
    return ('unknown', 'cpu', None, None)

def get_providers():
    ep = os.environ.get('PIXLITE_EP', 'auto').lower()
    fixed = {'cpu': ['CPUExecutionProvider'],
             'cuda': ['CUDAExecutionProvider', 'CPUExecutionProvider'],
             'rocm': ['ROCmExecutionProvider', 'CPUExecutionProvider'],
             'migraphx': ['MIGraphXExecutionProvider', 'CPUExecutionProvider'],
             'dml': ['DmlExecutionProvider', 'CPUExecutionProvider'],
             'openvino': ['OpenVINOExecutionProvider', 'CPUExecutionProvider']}
    if ep in fixed:
        return fixed[ep]
    if ep != 'auto':
        return ['CPUExecutionProvider']
    _, suggest, _, _ = detect_gpu()
    if suggest == 'cpu':
        return ['CPUExecutionProvider']
    # auto: 只在对应 EP 真正可用时启用, 否则回退 CPU
    with contextlib.suppress(Exception):
        import onnxruntime as ort
        avail = ort.get_available_providers()
        want = fixed[suggest][0]
        if want in avail:
            return [want, 'CPUExecutionProvider']
    return ['CPUExecutionProvider']

class ModelUnavailable(RuntimeError):
    """模型文件缺失/不完整。服务端据此返回 503 + "模型下载中" 提示, 而不是 500。"""
    def __init__(self, name, reason=''):
        self.model = name
        super().__init__(f'模型 {name} 尚未就绪 ({reason}); 正在后台自动下载, 完成后即可使用')


def make_session(model_path, sess_options=None):
    """Issue #10: 加载前校验模型 (存在 + 大小与清单一致), 截断/被替换的模型给出明确错误,
    而不是在推理时报晦涩的 ONNX 异常。完整 sha256 校验由 tools/download_models.py 负责;
    PIXLITE_VERIFY_MODELS=sha256 时加载前也做完整哈希校验。"""
    import onnxruntime as ort

    from .perception.models_manifest import check
    reason = check(model_path, full=os.environ.get('PIXLITE_VERIFY_MODELS') == 'sha256')
    if reason:
        raise ModelUnavailable(os.path.basename(model_path), reason)
    return ort.InferenceSession(model_path, sess_options, providers=get_providers())

# 飞牛应用市场驱动包名 (引导用户去应用中心装)
FNOS_DRIVER_PACKAGES = {
    'nvidia': {'pkg': 'Nvidia-Driver-560', 'where': '飞牛应用中心'},
    'amd':    {'pkg': 'AMD ROCm 7.2',      'where': '飞牛应用中心'},
    'intel':  {'pkg': None,                 'where': '核显驱动系统自带, 无需安装'},
}

def gpu_status():
    """供 /api/gpu 展示: 厂商/建议 EP/当前 providers/结构化引导(应用市场驱动包 + pip 包 + 参数)"""
    vendor, suggest, pip_pkg, guide = detect_gpu()
    drv = FNOS_DRIVER_PACKAGES.get(vendor, {})
    accel_ready = get_providers() != ['CPUExecutionProvider'] if vendor != 'none' else False
    return {
        'vendor': vendor, 'suggest_ep': suggest, 'pip_package': pip_pkg,
        'guide': guide, 'providers': get_providers(),
        'current_ep': os.environ.get('PIXLITE_EP', 'auto'),
        # 结构化引导: 第一步去哪装驱动, 第二步装 pip 包, 第三步设参数
        'install_steps': ([
            {'step': 1, 'action': f"在{drv.get('where','飞牛应用中心')}安装「{drv['pkg']}」驱动包", 'link': None},
            {'step': 2, 'action': f"安装 Python 推理包: pip install {pip_pkg}", 'link': None},
            {'step': 3, 'action': f"重启应用并将 PIXLITE_EP 设为 {suggest}", 'link': None},
        ] if drv.get('pkg') and pip_pkg else None),
        'accel_ready': accel_ready,
    }
