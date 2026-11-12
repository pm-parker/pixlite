#!/usr/bin/env python3
"""Download PixLite model files (public open-source models).

普通用户不需要运行本脚本: 服务启动后会在后台自动下载缺失模型 (engine/perception/model_store.py)。
本脚本用于 CI 构建内置模型的 full 包、开发机预下载, 或排障。

All models are public open-source weights (BSD-3 / Apache-2.0 / MIT sources). ~1.1GB total.
URLs are pinned to immutable revisions and every file is verified by size + SHA-256
(engine/perception/models_manifest.py). Downloads resume after interruption and fail over
between sources (official → hf-mirror.com → GitHub proxy). Stdlib only.

Usage:
    python tools/download_models.py              # into engine/perception/models (bundled dir)
    python tools/download_models.py --dir DIR    # into another directory
    python tools/download_models.py --mirror     # try mirrors first (CN network)
    python tools/download_models.py --verify     # only verify existing files, no download
"""
import os, sys, argparse, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# 按文件加载, 不经过 engine.perception/__init__ (其依赖 numpy/cv2/onnxruntime)
_spec = importlib.util.spec_from_file_location(
    'pixlite_model_store', os.path.join(ROOT, 'engine', 'perception', 'model_store.py'))
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=store.BUNDLED_DIR)
    ap.add_argument('--mirror', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args()
    if a.mirror:
        os.environ['PIXLITE_MODEL_MIRROR_FIRST'] = '1'
    os.makedirs(a.dir, exist_ok=True)
    fails = []
    for name in store.MODELS:
        dst = os.path.join(a.dir, name)
        if os.path.exists(dst):
            reason = store.check(dst, full=True)
            if reason is None:
                print(f'[{name}] exists, verified')
                continue
            print(f'[{name}] invalid ({reason})' + ('' if a.verify else ', re-downloading'))
            if not a.verify:
                os.remove(dst)
        elif a.verify:
            print(f'[{name}] missing')
        if a.verify:
            fails.append(name)
            continue
        try:
            store.download_one(name, dest_dir=a.dir)
        except Exception as e:
            print(f'[{name}] FAILED: {e}')
            fails.append(name)
    if fails:
        print('FAILED:', ', '.join(fails))
        sys.exit(1)
    print('All models ready:', os.path.abspath(a.dir))


if __name__ == '__main__':
    main()
