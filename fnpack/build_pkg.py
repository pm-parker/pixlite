# -*- coding: utf-8 -*-
"""组装 fpk 打包目录并生成可安装结构。
用法: python build_pkg.py [--with-models]
产物: fnpack/target (用 fnpack build 打 .fpk) 或 fnpack/dist (tar.gz 手动装)
"""
import os, sys, shutil

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(SRC, 'fnpack')
STG = os.path.join(PKG, 'target')
LUT = r'C:\Users\Administrator\pixcake-reverse\effect_config_decrypted\Filters'
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
    etc = os.path.join(PKG, 'etc')
    if os.path.exists(etc): shutil.rmtree(etc)
    if os.path.exists(LUT):
        shutil.copytree(LUT, os.path.join(etc, 'lut', 'Filters'))
    print('staged target:', STG)
    print('with_models =', with_models)

if __name__ == '__main__':
    main('--with-models' in sys.argv)
