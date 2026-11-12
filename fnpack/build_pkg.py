# -*- coding: utf-8 -*-
"""组装飞牛 fnOS 应用目录并用官方 fnpack 打成 .fpk (本地与 GitHub Actions 共用)。

用法:
    python fnpack/build_pkg.py [--with-models] [--skip-web] [--version X.Y.Z]
                               [--fnpack /path/to/fnpack] [--out dist]

产物布局 (fnpack 1.2.x 要求, 见 https://developer.fnnas.com/docs/cli/fnpack/):
    fnpack/build/fnnas.pixlite/
      manifest  ICON.PNG  ICON_256.PNG  cmd/  config/  wizard/
      app/                          → 安装后解压到 ${TRIM_APPDEST}
        server/ engine/ presets/ tools/ requirements.txt
        lut/Filters/                (cmd/main: PIXLITE_LUT_DIR=${TRIM_APPDEST}/lut/Filters)
        ui/config  ui/images/       (desktop_uidir = ui, 桌面入口)

步骤:
1. 构建前端 (webapp: npm ci && npm run build → server/web); --skip-web 时要求 server/web 已存在
2. --with-models: tools/download_models.py --verify 全部通过才把模型打进包; 默认精简包不带模型
3. 组装上述目录, 版本号可由 --version 覆盖 manifest (CI 用 tag 号)
4. 找到 fnpack (--fnpack / $FNPACK / PATH) 时执行 fnpack build, 输出到 --out
   (默认 dist/), 文件名 <appname>_<version>[_full].fpk; 找不到 fnpack 只组装目录
5. 结构校验失败或 fnpack 失败时非零退出
"""
import os, sys, shutil, subprocess, glob, json, re, argparse

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(SRC, 'fnpack')
BUILD = os.path.join(PKG, 'build')
LUT_SRC = os.environ.get('PIXLITE_LUT_SRC', '')
LUT_REPO = os.path.join(PKG, 'etc', 'lut', 'Filters')
WEB = os.path.join(SRC, 'server', 'web')
META = ['manifest', 'ICON.PNG', 'ICON_256.PNG', 'cmd', 'config', 'wizard']
CODE = ['server', 'engine', 'presets', 'tools']


def die(msg):
    print('ERROR:', msg)
    sys.exit(1)


def read_manifest(path):
    kv = {}
    for line in open(path, encoding='utf-8'):
        if '=' in line:
            k, v = line.split('=', 1)
            kv[k.strip()] = v.strip()
    return kv


def set_manifest_version(path, version):
    s = open(path, encoding='utf-8').read()
    s, n = re.subn(r'^(version\s*=\s*).*$', lambda m: m.group(1) + version, s, flags=re.M)
    if n != 1:
        die('manifest 中没有 version 字段')
    open(path, 'w', encoding='utf-8').write(s)


def build_web(skip):
    if skip:
        if not os.path.isfile(os.path.join(WEB, 'index.html')):
            die('server/web 不存在, 去掉 --skip-web 让脚本构建前端')
        return
    npm = shutil.which('npm')
    if not npm:
        die('未找到 npm (Node 22+), 无法构建前端; 或先手动构建后加 --skip-web')
    wd = os.path.join(SRC, 'webapp')
    subprocess.run([npm, 'ci', '--no-audit', '--no-fund'], cwd=wd, check=True)
    subprocess.run([npm, 'run', 'build'], cwd=wd, check=True)


def write_ui(app_dir, mf):
    """desktop_uidir=ui: 桌面图标 → 打开 http://<NAS>:<service_port>/"""
    ui = os.path.join(app_dir, 'ui')
    os.makedirs(os.path.join(ui, 'images'), exist_ok=True)
    shutil.copy(os.path.join(PKG, 'ICON.PNG'), os.path.join(ui, 'images', 'icon_64.png'))
    shutil.copy(os.path.join(PKG, 'ICON_256.PNG'), os.path.join(ui, 'images', 'icon_256.png'))
    entry = mf.get('desktop_applaunchname', mf['appname'] + '.Application')
    cfg = {'.url': {entry: {
        'title': mf.get('display_name', mf['appname']),
        'icon': 'images/icon_{0}.png',
        'type': 'url',
        'protocol': 'http',
        'port': mf.get('service_port', '8300'),
        'url': '/',
        'allUsers': True,
    }}}
    with open(os.path.join(ui, 'config'), 'w', encoding='utf-8') as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=4)


def find_fnpack(arg):
    for c in (arg, os.environ.get('FNPACK'), shutil.which('fnpack')):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--with-models', action='store_true')
    ap.add_argument('--skip-web', action='store_true')
    ap.add_argument('--version', default='')
    ap.add_argument('--fnpack', default='')
    ap.add_argument('--out', default=os.path.join(SRC, 'dist'))
    a = ap.parse_args()
    if a.version and not re.match(r'^\d+\.\d+\.\d+$', a.version):
        die(f'--version 需为 X.Y.Z, 实际: {a.version}')

    build_web(a.skip_web)
    if a.with_models:
        r = subprocess.run([sys.executable, os.path.join(SRC, 'tools', 'download_models.py'), '--verify'])
        if r.returncode != 0:
            die('模型校验失败: 先运行 python tools/download_models.py')
    if LUT_SRC:   # 可选: 从外部目录刷新仓库自带 LUT
        if not os.path.isdir(LUT_SRC):
            die(f'PIXLITE_LUT_SRC 不存在: {LUT_SRC}')
        if os.path.exists(LUT_REPO):
            shutil.rmtree(LUT_REPO)
        shutil.copytree(LUT_SRC, LUT_REPO)

    mf = read_manifest(os.path.join(PKG, 'manifest'))
    appname = mf['appname']
    stage = os.path.join(BUILD, appname)
    if os.path.exists(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    for m in META:
        s = os.path.join(PKG, m)
        (shutil.copytree if os.path.isdir(s) else shutil.copy)(s, os.path.join(stage, m))
    for f in glob.glob(os.path.join(stage, 'cmd', '*')):
        os.chmod(f, 0o755)
    if a.version:
        set_manifest_version(os.path.join(stage, 'manifest'), a.version)
    version = read_manifest(os.path.join(stage, 'manifest'))['version']

    app = os.path.join(stage, 'app')
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', 'work', '*.map', '*.part', '.DS_Store')
    for d in CODE:
        shutil.copytree(os.path.join(SRC, d), os.path.join(app, d), ignore=ignore)
    shutil.copy(os.path.join(PKG, 'requirements.txt'), os.path.join(app, 'requirements.txt'))
    shutil.copytree(LUT_REPO, os.path.join(app, 'lut', 'Filters'), ignore=ignore)
    models_dst = os.path.join(app, 'engine', 'perception', 'models')
    if not a.with_models:
        for p in glob.glob(os.path.join(models_dst, '*.onnx')):
            os.remove(p)
    write_ui(app, mf)

    # 结构校验
    problems = []
    for f in ['manifest', 'ICON.PNG', 'ICON_256.PNG', 'cmd/main', 'config/privilege', 'config/resource',
              'app/ui/config', 'app/server/web/index.html', 'app/server/api/main.py',
              'app/engine/pipeline/__init__.py', 'app/tools/download_models.py', 'app/requirements.txt']:
        if not os.path.isfile(os.path.join(stage, f)):
            problems.append(f'{f} 缺失')
    for f in ['config/privilege', 'config/resource', 'app/ui/config']:
        try:
            json.load(open(os.path.join(stage, f), encoding='utf-8'))
        except Exception as e:
            problems.append(f'{f} 不是合法 JSON: {e}')
    n_lut = len(glob.glob(os.path.join(app, 'lut', 'Filters', '*', 'filter.png')))
    if n_lut == 0:
        problems.append('app/lut/Filters 下没有 LUT')
    n_models = len(glob.glob(os.path.join(models_dst, '*.onnx')))
    if problems:
        die('; '.join(problems))
    print(f'staged: {stage}  version={version} with_models={a.with_models} '
          f'(onnx: {n_models}) LUT={n_lut}')

    fp = find_fnpack(a.fnpack)
    if not fp:
        print('fnpack 未找到 (--fnpack / $FNPACK / PATH), 只组装目录, 未生成 .fpk')
        return
    subprocess.run([fp, 'build', '--directory', stage], cwd=BUILD, check=True)
    produced = [p for p in glob.glob(os.path.join(BUILD, '*.fpk')) + glob.glob(os.path.join(stage, '*.fpk'))]
    if not produced:
        die('fnpack 未生成 .fpk')
    os.makedirs(a.out, exist_ok=True)
    name = f'{appname}_{version}{"_full" if a.with_models else ""}.fpk'
    dst = os.path.join(a.out, name)
    shutil.move(max(produced, key=os.path.getmtime), dst)
    print(f'fpk: {dst} ({os.path.getsize(dst)} bytes)')


if __name__ == '__main__':
    main()
