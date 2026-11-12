#!/usr/bin/env python3
"""Download PixLite model files (public open-source models) into engine/perception/models/.

All models are public open-source weights (BSD-3 / Apache-2.0 / MIT sources). ~1.1GB total.
URLs are pinned to immutable revisions and every file is verified by size + SHA-256
(see engine/perception/models_manifest.py). Existing files are re-verified; invalid
ones are replaced. A download is only moved into place after it passes verification.

Usage:
    python tools/download_models.py            # direct
    python tools/download_models.py --mirror   # use hf-mirror.com (CN network)
    python tools/download_models.py --verify   # only verify existing files, no download
"""
import os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from engine.perception.models_manifest import MODELS as FILES, check, sha256_of  # noqa: E402

MODELS = os.path.join(ROOT, 'engine', 'perception', 'models')


def verify_file(path, spec):
    size = os.path.getsize(path)
    if size != spec['size']:
        raise ValueError(f'size {size} != expected {spec["size"]}')
    digest = sha256_of(path)
    if digest != spec['sha256']:
        raise ValueError(f'sha256 {digest} != expected {spec["sha256"]}')


def dl(name, url, dst):
    print(f"[{name}] GET {url}")
    tmp = dst + ".part"
    try:
        urllib.request.urlretrieve(url, tmp)
        verify_file(tmp, FILES[name])
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    print(f"[{name}] -> {dst} ({os.path.getsize(dst)} bytes, sha256 OK)")


def main():
    mirror = "--mirror" in sys.argv
    verify_only = "--verify" in sys.argv
    os.makedirs(MODELS, exist_ok=True)
    fails = []
    for name, spec in FILES.items():
        url = spec['url']
        if mirror:
            url = url.replace("https://huggingface.co", "https://hf-mirror.com")
        dst = os.path.join(MODELS, name)
        if os.path.exists(dst):
            reason = check(dst, full=True)
            if reason is None:
                print(f"[{name}] exists, verified")
                continue
            print(f"[{name}] invalid ({reason})" + ("" if verify_only else ", re-downloading"))
            if verify_only:
                fails.append(name)
                continue
        elif verify_only:
            print(f"[{name}] missing")
            fails.append(name)
            continue
        try:
            dl(name, url, dst)
        except Exception as e:
            print(f"[{name}] FAILED: {e}")
            fails.append(name)
    if fails:
        print("FAILED:", ", ".join(fails))
        sys.exit(1)
    print("All models ready:", os.path.abspath(MODELS))


if __name__ == "__main__":
    main()
