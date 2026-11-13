"""Preset library: values must reach the engine at the intended strength.

Covers the "every preset looks broken" report: preset values were double-scaled by the
front-end slider mapping, default slider values overwrote the base preset, and color_match
without a reference image silently ran a linear auto-exposure on every preset."""

import json
import os
import subprocess
import sys

import cv2
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "server", "api"))

LIB = os.path.join(ROOT, "presets", "preset_library.json")


def _node(js):
    if subprocess.run(["node", "--version"], capture_output=True, check=False).returncode != 0:
        pytest.skip("node not available")
    with open(os.path.join(ROOT, "webapp", "src", "lib.js"), encoding="utf-8") as fh:
        lib = fh.read()
    lib = lib.replace("import { reactive, watch } from 'vue'", "const reactive = o => o, watch = () => {}")
    script = "globalThis.localStorage = {getItem: () => null, setItem() {}, removeItem() {}};\n" + lib + "\n" + js
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout)


def _library_engine():
    """Exactly what Workspace.applyLibPreset + runRetouch send for every library preset."""
    with open(LIB, encoding="utf-8") as fh:
        lib = fh.read()
    return _node(
        f"const lib = {lib};"
        "console.log(JSON.stringify(lib.map(it => ({name: it.name,"
        " engine: toEngineOverrides({...defaultOverrides(), ...(it.overrides || {})})}))))"
    )


def test_default_sliders_do_not_override_base_preset():
    out = _node("console.log(JSON.stringify(toEngineOverrides(defaultOverrides())))")
    # 旧代码: face_enhance.degree=0 覆盖基础预设 0.38 (深度修复 0.8), skin_retouch 被 base 覆盖
    for k in ("face_enhance.degree", "skin_retouch.strength", "teeth_whiten.degree",
              "facesurgery.degree", "eye_brighten.degree", "blemish.degree"):
        assert k not in out, k


def test_base_slider_mapping_is_monotonic_and_clamped():
    out = _node(
        "const d = defaultOverrides(); const f = v => toEngineOverrides({...d, 'skin_retouch.strength': v});"
        "console.log(JSON.stringify([f(-100), f(-25), f(50), f(100)].map(o => o['skin_retouch.strength'])))"
    )
    assert out == sorted(out) and out[0] >= 0 and out[-1] <= 1
    assert out[1] == pytest.approx(0.2)   # 质感肌: 云端磨皮 0.2 → 引擎 0.2 (旧: 0.602)


def test_library_values_reach_engine_unscaled():
    eng = {p["name"]: p["engine"] for p in _library_engine()}
    tex = eng["质感肌"]
    assert tex["blemish.degree"] == pytest.approx(1.0)        # 旧: 0.01
    assert tex["catchlight.degree"] == pytest.approx(0.7)     # 旧: 0.007
    assert tex["lip_makeup.degree"] == pytest.approx(0.3)     # 旧: 0.003
    assert tex["skin_retouch.strength"] == pytest.approx(0.2)  # 旧: 0.602
    assert tex["teeth_whiten.degree"] == pytest.approx(0.35)   # 旧: 0.502
    assert tex["facesurgery.degree"] == pytest.approx(72.5, abs=0.1)
    # 手写预设的磨皮键原为 skin_retouch.degree (引擎不读), 迁移为 strength 后各预设强度不同
    # (滑杆为默认值时不下发, 由基础预设 0.45 生效)
    st = [eng[n].get("skin_retouch.strength", 0.45) for n in ("自然磨皮", "质感磨皮", "水润磨皮", "奶油肌")]
    assert st == pytest.approx([0.45, 0.6, 0.7, 0.8])


def test_library_engine_values_in_range():
    for p in _library_engine():
        for k, v in p["engine"].items():
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            op = k.split(".")[0]
            if op in ("tone", "white_balance", "lut", "grain", "smile", "facesurgery", "hsl",
                      "color", "makeup_transfer", "profile"):
                continue
            assert 0 <= v <= 1, (p["name"], k, v)


def test_color_match_without_reference_is_identity():
    from main import DictPipeline

    rng = np.random.default_rng(0)
    for mean in (40, 200):   # 暗图曾被提亮 ~+25 L, 亮图曾被压暗 ~-30 L
        img = np.clip(rng.normal(mean, 20, (64, 64, 3)), 0, 255).astype(np.uint8)
        out = DictPipeline({"nodes": [{"op": "color_match", "params": {"protect_skin": True}}]}).run(img.copy())
        assert np.array_equal(out, img)


def _skin_tone(monkeypatch, img, params):
    from main import DictPipeline

    import engine.pipeline as EP

    monkeypatch.setattr(EP, "_skin_mask", lambda ctx, b: np.full(b.shape[:2], 255, np.uint8))
    return DictPipeline({"nodes": [{"op": "skin_tone", "params": params}]}).run(img.copy())


def test_skin_brighten_is_clamped_curve(monkeypatch):
    ramp = np.repeat(np.linspace(0, 255, 256).astype(np.uint8)[None, :, None], 3, 2)
    L = lambda im: cv2.cvtColor(im, cv2.COLOR_BGR2LAB)[0, :, 0].astype(int)   # noqa: E731
    one = _skin_tone(monkeypatch, ramp, {"brighten": 1.0})
    ten = _skin_tone(monkeypatch, ramp, {"brighten": 10})   # 旧预设写法: 曾被当成 L×1.6
    assert np.array_equal(one, ten)
    d = L(one) - L(ramp)
    assert d.max() <= 18 and d[-3:].max() <= 2 and d[:3].max() <= 2   # 高光/暗部几乎不动
    assert (L(one) >= L(ramp) - 1).all()


def test_skin_rouge_and_legacy_rosy_take_effect(monkeypatch):
    skin = np.full((8, 8, 3), (150, 170, 215), np.uint8)
    a = lambda im: int(cv2.cvtColor(im, cv2.COLOR_BGR2LAB)[0, 0, 1])   # noqa: E731
    assert a(_skin_tone(monkeypatch, skin, {"rouge": 0.5})) > a(skin)
    assert a(_skin_tone(monkeypatch, skin, {"rosy": 0.5})) == a(_skin_tone(monkeypatch, skin, {"rouge": 0.5}))


def test_library_uses_engine_key_names():
    with open(LIB, encoding="utf-8") as fh:
        lib = json.load(fh)
    keys = {k for p in lib for k in p["overrides"]}
    assert "skin_tone.rosy" not in keys and "skin_retouch.degree" not in keys


def test_defaults_add_no_makeup_or_color_node():
    out = _node("console.log(JSON.stringify(toEngineOverrides(defaultOverrides())))")
    # 旧: makeup_transfer.level=2 透传 → 每个预设都被追加 0.7 强度舞台妆; color.* 透传成无效节点
    assert not any(k.startswith(("makeup_transfer.", "color.")) for k in out), out
    on = _node("console.log(JSON.stringify(toEngineOverrides({...defaultOverrides(),"
               " 'makeup_transfer.degree': 50, 'makeup_transfer.level': 3})))")
    assert on["makeup_transfer.degree"] == 0.5 and on["makeup_transfer.level"] == 3


def test_library_tint_in_cloud_range():
    with open(LIB, encoding="utf-8") as fh:
        lib = json.load(fh)
    # 旧: 云端 pf3003 按 ×550 导入, 49.5/66/99 让绿色通道降 6~12%, 整图偏紫
    assert all(abs(p["overrides"].get("tone.tint", 0)) <= 20 for p in lib)


def test_library_luts_are_colour_and_readable():
    with open(LIB, encoding="utf-8") as fh:
        lib = json.load(fh)
    for fid in {p["overrides"]["lut.filter"] for p in lib if p["overrides"].get("lut.filter")}:
        lut = cv2.imread(os.path.join(ROOT, "fnpack", "etc", "lut", "Filters", fid, "filter.png"))
        assert lut is not None, fid
        # 森林 FT00000008 是饱和度 0 的黑白 LUT, 「户外草坪」套用后整图变黑白
        assert cv2.cvtColor(lut, cv2.COLOR_BGR2HSV)[..., 1].mean() > 20, fid


def test_body_slim_narrows_and_needs_a_body():
    from engine.effects import body as B
    from engine.perception import CLS

    h, w = 800, 400
    img = np.full((h, w, 3), 255, np.uint8)
    img[150:, 150:250] = 0
    par = np.zeros((h, w), np.uint8)
    par[150:, 150:250] = CLS["cloth"]
    faces = [{"box": (170, 40, 60, 80)}]
    out = B.body_slim(img, par, faces, "waist", 1.0)
    width = lambda y: int((out[y, :, 0] < 128).sum())   # noqa: E731
    assert width(300) < 100 and width(700) == 100        # 腰部收窄 (旧: 100 → 108), 腿部不动
    assert np.array_equal(B.body_slim(img, par, [], "waist", 1.0), img)   # 无人脸不处理
    headshot = np.zeros((h, w), np.uint8)
    assert np.array_equal(B.body_slim(img, headshot, faces, "waist", 1.0), img)


def test_axilla_stays_below_chin():
    from engine.effects import body as B
    from engine.perception import CLS

    h, w = 400, 400
    rng = np.random.default_rng(1)
    img = np.clip(rng.normal(150, 40, (h, w, 3)), 0, 255).astype(np.uint8)
    par = np.full((h, w), CLS["skin"], np.uint8)   # 头像照: 解析几乎全是皮肤
    faces = [{"box": (100, 60, 200, 260)}]          # 下巴在 y=320
    out = B.axilla_fix(img, par, faces, 1.0)
    assert np.array_equal(out[:320], img[:320])     # 旧: 固定 25%~50% 高度 → 改到眼睛眉毛


def _built(name):
    import main

    with open(LIB, encoding="utf-8") as fh:
        lib = {p["name"]: p for p in json.load(fh)}
    eng = {p["name"]: p["engine"] for p in _library_engine()}[name]
    return main.build_preset("wedding_basic", eng, lib[name].get("routing"), "female")


def test_retouch_nodes_run_before_color_and_grain(monkeypatch):
    monkeypatch.setenv("PIXLITE_LUT_DIR", os.path.join(ROOT, "fnpack", "etc", "lut", "Filters"))
    import importlib

    import main

    importlib.reload(main)
    ops = [n["op"] for n in _built("质感肌")["nodes"]]
    first_global = min(ops.index(o) for o in ("color_match", "tone", "lut") if o in ops)
    for o in ("blemish", "dark_circle", "wrinkle", "catchlight", "body_waist", "skin_retouch"):
        if o in ops:
            assert ops.index(o) < first_global, (o, ops)
    assert ops[-1] == "grain"
    # 亮眼: 双眼节点与单眼节点不再重复执行, 合并成左右各一次
    assert "eye_brighten" not in ops and ops.count("eye_brighten_l") == 1 and ops.count("eye_brighten_r") == 1


def test_grain_size_changes_texture_not_strength():
    from engine.effects import grain

    img = np.full((256, 256, 3), 120, np.uint8)
    fine, coarse = grain(img, 0.04, size=10), grain(img, 0.04, size=80)
    sd = lambda o: float(o.astype(float).std())   # noqa: E731
    assert abs(sd(fine) - sd(coarse)) < 0.25 * sd(fine)   # 强度相同
    lap = lambda o: float(np.abs(cv2.Laplacian(o[..., 0], cv2.CV_32F)).mean())   # noqa: E731
    assert lap(coarse) < 0.5 * lap(fine)                   # size 越大颗粒越粗 (旧: size 被忽略)


def test_blemish_keeps_normal_skin_texture():
    from engine.effects.blemish import blemish_remove
    from engine.perception import CLS

    rng = np.random.default_rng(3)
    img = np.clip(rng.normal(170, 3, (128, 128, 3)), 0, 255).astype(np.uint8)   # 正常皮肤纹理
    img[60:64, 60:64] = 120                                                       # 一个痘印
    par = np.full((128, 128), CLS["skin"], np.uint8)
    out = blemish_remove(img, par, None, 1.0)
    tex = np.abs(out.astype(int) - img.astype(int))
    tex[56:68, 56:68] = 0
    assert tex.mean() < 0.3                                   # 旧: 阈值 4 ≈ 纹理水平, 整片抹平
    assert out[61, 61].astype(int).mean() > 140               # 斑点被修掉


def test_refine_parsing_restores_eyes_from_landmarks():
    import engine.pipeline as EP
    from engine.perception import CLS
    from engine.perception import landmarks as LM

    par = np.full((100, 100), CLS["skin"], np.uint8)   # 解析把眼睛判成了皮肤
    mesh = np.zeros((478, 2), np.float32)
    for idx, cx in ((LM.EYE_L_IDX, 30), (LM.EYE_R_IDX, 70)):
        for k, i in enumerate(idx):
            a = 2 * np.pi * k / len(idx)
            mesh[i] = (cx + 8 * np.cos(a), 40 + 4 * np.sin(a))
    out = EP._refine_parsing(par, [{"box": (10, 10, 80, 80), "mesh": mesh}])
    assert out[40, 30] == CLS["eye_l"] and out[40, 70] == CLS["eye_r"]
    assert out[80, 50] == CLS["skin"] and par[40, 30] == CLS["skin"]   # 不改其它区域, 不改缓存原数组
