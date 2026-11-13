"""Rendered-output regression for every preset (needs the ONNX models; auto-skips without).

Each library preset is sent exactly as the web UI does (defaultOverrides + preset overrides →
toEngineOverrides → build_preset) and rendered on the bundled test portraits. The output must
stay close to the input in global brightness, must not blow out highlights and must keep the
image structure. These are the symptoms of the "every preset breaks the photo" report:
auto-exposure on every preset (dL +25 / -42), blown highlights (up to +20%), face blocks and
body warps (SSIM down to 0.42).

Set PIXLITE_QUALITY_REPORT=<path> to also write all metrics as JSON (CI uploads it)."""

import json
import os
import sys

import cv2
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "server", "api"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MODELS = os.path.join(ROOT, "engine", "perception", "models")
NEEDED = ("face_detection_yunet_2023mar.onnx", "face_parsing.onnx", "gfpgan_1.4.onnx", "genderage.onnx")
IMAGES = ("test_face.jpg", "female_test.jpg", "ag_male_old.jpg")   # 中性 / 偏暗 / 偏亮 (白衬衫)
LONG_EDGE = 384

# 阈值 (判据, 不是目标值)。dL: 平均亮度变化 (LAB L, 0..255); clip: 高光溢出像素占比的增量 (百分点);
# ssim: 灰度 SSIM。调色/滤镜预设本身会改变亮度, 阈值按「明显崩坏」设定, 留有余量。
MAX_ABS_DL = 30.0
MAX_CLIP_INCREASE = 8.0
MIN_SSIM = 0.70
# 滤镜文件本身把中性灰 130 映射到 RGB(77,69,110) (亮度 -50、明显偏蓝), 套用它的预设整图压暗
# 25~41。这是 LUT 素材问题, 不是流水线问题, 需人工确认素材后再决定替换: 暂不检查这些预设的 dL。
KNOWN_DARK_LUTS = {"FT00000007"}   # 日系

pytestmark = pytest.mark.skipif(
    any(not os.path.isfile(os.path.join(MODELS, n)) for n in NEEDED),
    reason="models missing (run tools/download_models.py)")


def ssim(a, b):
    a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float64)
    b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    g = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)   # noqa: E731
    ma, mb = g(a), g(b)
    va, vb, cov = g(a * a) - ma * ma, g(b * b) - mb * mb, g(a * b) - ma * mb
    m = ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))
    return float(m.mean())


def metrics(src, out):
    l0 = cv2.cvtColor(src, cv2.COLOR_BGR2LAB)[..., 0].astype(np.float64)
    l1 = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)[..., 0].astype(np.float64)
    clip = lambda im: 100.0 * float((im.max(axis=2) >= 250).mean())   # noqa: E731
    return {"dL": round(float(l1.mean() - l0.mean()), 2),
            "clip_inc": round(clip(out) - clip(src), 2),
            "ssim": round(ssim(src, out), 3)}


def _jobs():
    from test_presets import _library_engine

    with open(os.path.join(ROOT, "presets", "preset_library.json"), encoding="utf-8") as fh:
        lib = {p["name"]: p for p in json.load(fh)}
    jobs = [("wedding_basic", p["name"], p["engine"], lib[p["name"]].get("routing"),
             p["engine"].get("lut.filter") in KNOWN_DARK_LUTS)
            for p in _library_engine()]
    jobs.append(("wedding_basic", "婚纱一键(默认)", {}, None, False))
    jobs.append(("gfp_deep_test", "深度修复", {}, None, False))
    return jobs


def test_presets_do_not_wreck_the_photo(monkeypatch):
    monkeypatch.setenv("PIXLITE_LUT_DIR", os.path.join(ROOT, "fnpack", "etc", "lut", "Filters"))
    import importlib

    import main

    importlib.reload(main)
    jobs = _jobs()
    report, bad = [], []
    for name in IMAGES:
        src = cv2.imread(os.path.join(ROOT, "tests", name))
        assert src is not None, name
        s = LONG_EDGE / max(src.shape[:2])
        src = cv2.resize(src, (round(src.shape[1] * s), round(src.shape[0] * s)), interpolation=cv2.INTER_AREA)
        for base, preset, eng, routing, dark_lut in jobs:
            out = main.DictPipeline(main.build_preset(base, eng, routing, "")).run(src.copy())
            m = {"image": name, "preset": preset, **metrics(src, out)}
            report.append(m)
            dl_bad = abs(m["dL"]) > MAX_ABS_DL and not dark_lut
            if dl_bad or m["clip_inc"] > MAX_CLIP_INCREASE or m["ssim"] < MIN_SSIM:
                bad.append(m)
    path = os.environ.get("PIXLITE_QUALITY_REPORT")
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
    assert not bad, "presets out of range:\n" + "\n".join(json.dumps(b, ensure_ascii=False) for b in bad)
