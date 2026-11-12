# -*- coding: utf-8 -*-
"""组装 fpk 打包目录并生成可安装结构 (Issue #3: 可复现、可校验、跨平台)。

用法:
    python fnpack/build_pkg.py [--with-models] [--skip-web]

步骤:
1. 构建前端 (webapp: npm ci && npm run build → server/web); --skip-web 时要求 server/web 已存在
2. 拷贝 server / engine / presets / tools 到 fnpack/target
3. --with-models: 先运行 tools/download_models.py --verify, 全部模型 sha256 通过才打进包;
   默认精简包不带模型 (首启用 tools/download_models.py 下载)
4. LUT: PIXLITE_LUT_SRC 指定源目录时同步到 fnpack/etc; 否则使用仓库自带的 fnpack/etc/lut
5. 校验 target 结构 (前端 index.html / LUT 数量 / 必需代码), 不满足则非零退出
"""
import os, sys, shutil, subprocess, glob

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(SRC, 'fnpack')
STG = os.path.join(PKG, 'target')
LUT_SRC = os.environ.get('PIXLITE_LUT_SRC', '')       # 旧代码写死作者 Windows 路径
LUT_DST = os.path.join(PKG, 'etc', 'lut', 'Filters')
WEB = os.path.join(SRC, 'server', 'web')


def die(msg):
    print('ERROR:', msg)
    sys.exit(1)


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


def main(with_models=False, skip_web=False):
    build_web(skip_web)
    if with_models:
        r = subprocess.run([sys.executable, os.path.join(SRC, 'tools', 'download_models.py'), '--verify'])
        if r.returncode != 0:
            die('模型校验失败: 先运行 python tools/download_models.py')
    if os.path.exists(STG):
        shutil.rmtree(STG)
    os.makedirs(STG)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', 'work', '*.map', '*.part')
    for d in ['server', 'engine', 'presets', 'tools']:
        shutil.copytree(os.path.join(SRC, d), os.path.join(STG, d), dirs_exist_ok=True, ignore=ignore)
    shutil.copy(os.path.join(PKG, 'requirements.txt'), os.path.join(STG, 'requirements.txt'))
    models_dst = os.path.join(STG, 'engine', 'perception', 'models')
    if not with_models and os.path.isdir(models_dst):
        for p in glob.glob(os.path.join(models_dst, '*.onnx')):
            os.remove(p)          # 精简包: 只去掉权重, 保留目录
    # LUT: 源存在才替换; 旧代码先无条件删 fnpack/etc 再判断 → 删光仓库自带滤镜
    if LUT_SRC:
        if not os.path.isdir(LUT_SRC):
            die(f'PIXLITE_LUT_SRC 不存在: {LUT_SRC}')
        if os.path.exists(LUT_DST):
            shutil.rmtree(LUT_DST)
        shutil.copytree(LUT_SRC, LUT_DST)
    # 结构校验
    problems = []
    if not os.path.isfile(os.path.join(STG, 'server', 'web', 'index.html')):
        problems.append('target/server/web/index.html 缺失')
    n_lut = len(glob.glob(os.path.join(LUT_DST, '*', 'filter.png')))
    if n_lut == 0:
        problems.append('fnpack/etc/lut/Filters 下没有 LUT')
    for f in ['server/api/main.py', 'engine/pipeline/__init__.py', 'tools/download_models.py']:
        if not os.path.isfile(os.path.join(STG, f)):
            problems.append(f'target/{f} 缺失')
    n_models = len(glob.glob(os.path.join(models_dst, '*.onnx')))
    if problems:
        die('; '.join(problems))
    print('staged target:', STG)
    print(f'with_models = {with_models} (onnx in package: {n_models}), LUT filters = {n_lut}')


if __name__ == '__main__':
    main('--with-models' in sys.argv, '--skip-web' in sys.argv)
