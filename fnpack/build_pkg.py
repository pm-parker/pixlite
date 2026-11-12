# -*- coding: utf-8 -*-
"""组装 fpk 打包目录并生成可安装结构。
用法: python build_pkg.py [--with-models]
产物: fnpack/target (用 fnpack build 打 .fpk) 或 fnpack/dist (tar.gz 手动装)
"""
import os, sys, shutil

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(SRC, 'fnpack')
STG = os.path.join(PKG, 'target')
# LUT 源目录: 环境变量优先; 旧代码写死作者 Windows 路径
LUT = os.environ.get('PIXLITE_LUT_SRC') or r'C:\Users\Administrator\pixcake-reverse\effect_config_decrypted\Filters'
MODELS = os.path.join(SRC, 'engine', 'perception', 'models')

def main(with_models=False):
    # target: 代码
    if os.path.exists(STG): shutil.rmtree(STG)
    os.makedirs(STG)
    for d in ['server', 'engine', 'presets']:
        shutil.copytree(os.path.join(SRC, d), os.path.join(STG, d), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'work', '*.map'))
    shutil.copy(os.path.join(PKG, 'requirements.txt'), os.path.join(STG, 'requirements.txt'))
    if with_models:
        # 模型已随 engine 拷贝, 不再处理
        pass
    else:
        # 精简包: 移除模型, engine 运行时若缺模型会报错 → 首启从 var/models 软链或下载
        models_dst = os.path.join(STG, 'engine', 'perception', 'models')
        if os.path.exists(models_dst):
            shutil.rmtree(models_dst)
    # etc: LUT
    # BUGFIX: 旧代码先无条件删除 fnpack/etc 再判断 LUT 源是否存在 → 在作者机器以外
    # 运行一次就把仓库自带的 92 个 LUT 滤镜全部删掉, 打出的包没有滤镜
    etc = os.path.join(PKG, 'etc')
    if os.path.isdir(LUT):
        if os.path.exists(etc): shutil.rmtree(etc)
        shutil.copytree(LUT, os.path.join(etc, 'lut', 'Filters'))
    else:
        print('LUT source not found, keep existing', etc)
    print('staged target:', STG)
    print('with_models =', with_models)

if __name__ == '__main__':
    main('--with-models' in sys.argv)
