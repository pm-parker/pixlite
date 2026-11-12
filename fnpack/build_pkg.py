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
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from typing import NoReturn

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(SRC, 'fnpack')
BUILD = os.path.join(PKG, 'build')
LUT_SRC = os.environ.get('PIXLITE_LUT_SRC', '')
LUT_REPO = os.path.join(PKG, 'etc', 'lut', 'Filters')
WEB = os.path.join(SRC, 'server', 'web')
META = ['manifest', 'ICON.PNG', 'ICON_256.PNG', 'cmd', 'config', 'wizard']
CODE = ['server', 'engine', 'presets', 'tools']


def die(msg) -> NoReturn:
    print('ERROR:', msg)
    sys.exit(1)


def read_manifest(path):
    kv = {}
    try:
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                if '=' in line:
                    k, v = line.split('=', 1)
                    kv[k.strip()] = v.strip()
    except OSError as e:
        die(f'manifest 不可读: {path} ({e})')
    return kv


def set_manifest_version(path, version):
    try:
        with open(path, encoding='utf-8') as fh:
            s = fh.read()
    except OSError as e:
        die(f'staged manifest 不可读: {path} ({e})')
    s, n = re.subn(r'^(version\s*=\s*).*$', lambda m: m.group(1) + version, s, flags=re.M)
    if n != 1:
        die('manifest 中没有 version 字段')
    try:
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(s)
    except OSError as e:
        die(f'staged manifest 不可写: {path} ({e})')


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
    """desktop_uidir=ui: 桌面图标走飞牛统一网关 /app/<appname>/ (https://developer.fnnas.com/docs/core-concepts/gateway-registration/)
    fnOS 先校验 NAS 登录态, 再转发到 ${TRIM_APPDEST}/app.sock, 用户无需任何连接密钥。"""
    ui = os.path.join(app_dir, 'ui')
    try:
        os.makedirs(os.path.join(ui, 'images'), exist_ok=True)
        shutil.copy(os.path.join(PKG, 'ICON.PNG'), os.path.join(ui, 'images', 'icon_64.png'))
        shutil.copy(os.path.join(PKG, 'ICON_256.PNG'), os.path.join(ui, 'images', 'icon_256.png'))
    except OSError as e:
        die(f'复制图标失败: {e}')
    entry = mf.get('desktop_applaunchname', mf['appname'] + '.Application')
    # 前缀不带点: 应用中心只给 type=iframe 的条目落库网关字段, 且带点包名的前缀
    # (如 /app/fnnas.pixlite) 解析异常; 统一转连字符, 对齐系统应用惯例 (/app/trim-preview)
    prefix = '/app/' + mf['appname'].replace('.', '-')
    cfg = {'.url': {entry: {
        'title': mf.get('display_name', mf['appname']),
        'icon': 'images/icon_{0}.png',
        # type 必须是 iframe: 实测 fnOS 1.2.1010 应用中心只给 iframe 条目注册统一网关
        # (type=url 时 gatewayPrefix/gatewaySocket 不落库, 路由永不注册 → 点图标 Not Found)
        'type': 'iframe',
        'protocol': '',              # 网关入口忽略 protocol/port, 沿用当前访问域名
        'gatewayPrefix': prefix,
        'gatewaySocket': 'app.sock',
        'url': prefix + '/',
        'allUsers': True,
    }}}
    try:
        with open(os.path.join(ui, 'config'), 'w', encoding='utf-8') as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=4)
    except (OSError, ValueError) as e:
        die(f'写入 ui/config 失败: {e}')


def find_fnpack(arg):
    for c in (arg, os.environ.get('FNPACK'), shutil.which('fnpack')):
        if not c or not os.path.isfile(c):
            continue
        try:
            executable = os.access(c, os.X_OK)
        except OSError:
            executable = False
        if executable:
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
    # fnOS manifest 允许 "2.1.3-beta" 这类预发布后缀 (developer.fnnas.com/docs/core-concepts/manifest)
    if a.version and not re.match(r'^\d+\.\d+\.\d+(-[0-9A-Za-z][0-9A-Za-z.]*)?$', a.version):
        die(f'--version 需为 X.Y.Z 或 X.Y.Z-beta.N, 实际: {a.version}')

    build_web(a.skip_web)
    if a.with_models:
        r = subprocess.run([sys.executable, os.path.join(SRC, 'tools', 'download_models.py'), '--verify'])
        if r.returncode != 0:
            die('模型校验失败: 先运行 python tools/download_models.py')
    if LUT_SRC:   # 可选: 从外部目录刷新仓库自带 LUT
        if not os.path.isdir(LUT_SRC):
            die(f'PIXLITE_LUT_SRC 不存在: {LUT_SRC}')
        try:
            if os.path.exists(LUT_REPO):
                shutil.rmtree(LUT_REPO)
            shutil.copytree(LUT_SRC, LUT_REPO)
        except OSError as e:
            die(f'刷新 LUT 失败: {e}')

    mf = read_manifest(os.path.join(PKG, 'manifest'))
    appname = mf['appname']
    stage = os.path.join(BUILD, appname)
    try:
        if os.path.exists(stage):
            shutil.rmtree(stage)
        os.makedirs(stage)
    except OSError as e:
        die(f'准备打包目录失败: {e}')
    try:
        for m in META:
            s = os.path.join(PKG, m)
            (shutil.copytree if os.path.isdir(s) else shutil.copy)(s, os.path.join(stage, m))
    except OSError as e:
        die(f'复制元数据失败: {e}')
    for f in glob.glob(os.path.join(stage, 'cmd', '*')):
        os.chmod(f, 0o755)  # noqa: S103 — cmd/* 是安装器直接执行的钩子脚本，必须有执行位
    if a.version:
        set_manifest_version(os.path.join(stage, 'manifest'), a.version)
    version = read_manifest(os.path.join(stage, 'manifest'))['version']

    app = os.path.join(stage, 'app')
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', 'work', '*.map', '*.part', '.DS_Store')
    try:
        for d in CODE:
            shutil.copytree(os.path.join(SRC, d), os.path.join(app, d), ignore=ignore)
        shutil.copy(os.path.join(PKG, 'requirements.txt'), os.path.join(app, 'requirements.txt'))
        shutil.copytree(LUT_REPO, os.path.join(app, 'lut', 'Filters'), ignore=ignore)
    except OSError as e:
        die(f'复制代码到 app/ 失败: {e}')
    models_dst = os.path.join(app, 'engine', 'perception', 'models')
    if not a.with_models:
        try:
            for p in glob.glob(os.path.join(models_dst, '*.onnx')):
                os.remove(p)
        except OSError as e:
            die(f'清理内置模型失败: {e}')
    write_ui(app, mf)

    # 结构校验
    problems = []
    for f in ['manifest', 'ICON.PNG', 'ICON_256.PNG', 'cmd/main', 'config/privilege', 'config/resource',
              'app/ui/config', 'app/server/web/index.html', 'app/server/api/main.py', 'app/server/run.py',
              'app/engine/perception/model_store.py', 'app/engine/perception/models_manifest.py',
              'app/engine/pipeline/__init__.py', 'app/tools/download_models.py', 'app/requirements.txt']:
        if not os.path.isfile(os.path.join(stage, f)):
            problems.append(f'{f} 缺失')
    for f in ['config/privilege', 'config/resource', 'app/ui/config']:
        try:
            with open(os.path.join(stage, f), encoding='utf-8') as fh:
                json.load(fh)
        except Exception as e:
            problems.append(f'{f} 不是合法 JSON: {e}')
    try:
        with open(os.path.join(stage, 'app/ui/config'), encoding='utf-8') as fh:
            ent = next(iter(json.load(fh)['.url'].values()))
        if ent.get('gatewaySocket') != 'app.sock' or not ent.get('gatewayPrefix', '').startswith('/app/'):
            problems.append('app/ui/config 未配置统一网关入口')
        elif '.' in ent.get('gatewayPrefix', '') or ent.get('type') != 'iframe':
            problems.append('gatewayPrefix 含点或 type!=iframe (应用中心不解析, 路由不会注册)')
    except Exception as e:
        problems.append(f'app/ui/config 解析失败: {e}')
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
    produced = list(glob.glob(os.path.join(BUILD, '*.fpk')) + glob.glob(os.path.join(stage, '*.fpk')))
    if not produced:
        die('fnpack 未生成 .fpk')
    name = f'{appname}_{version}{"_full" if a.with_models else ""}.fpk'
    dst = os.path.join(a.out, name)
    try:
        os.makedirs(a.out, exist_ok=True)
        shutil.move(max(produced, key=os.path.getmtime), dst)
    except OSError as e:
        die(f'输出 .fpk 失败: {e}')
    print(f'fpk: {dst} ({os.path.getsize(dst)} bytes)')


if __name__ == '__main__':
    main()
