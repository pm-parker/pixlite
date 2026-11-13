"""beta.4: inputs that looked usable but did nothing (image panel, batch with workbench
parameters, preset dropdowns)."""

import importlib
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "server", "api"))


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("PIXLITE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("PIXLITE_ALLOWED_ROOTS", str(tmp_path))
    import main

    importlib.reload(main)
    from fastapi.testclient import TestClient

    return main, TestClient(main.app, client=("127.0.0.1", 1)), tmp_path


def test_presets_lists_only_executable(srv):
    _, c, _ = srv
    names = c.get("/api/presets").json()
    assert "wedding_basic" in names
    for n in ("lut_names", "suit_recipes", "facesurgery_headnarrow", "preset_library"):
        assert n not in names, n


def test_batch_accepts_overrides(srv, monkeypatch):
    main, c, tmp = srv
    src = tmp / "src"
    src.mkdir()
    captured = {}

    class FakeJob:
        def __init__(self, pipe, *a, **k):
            captured["preset"] = pipe.preset

        def run(self, progress_cb=None):
            return {"total": 0, "done": 0, "failed": 0, "elapsed": 0, "errors": []}

        cancelled = False

    monkeypatch.setattr(main, "BatchJob", FakeJob)
    ov = {"sky_replace.degree": 0.8, "sky_replace.mode": "storm", "white_balance.temperature": 30}
    r = c.post("/api/batch", data={"src_dir": str(src), "overrides": json.dumps(ov)})
    assert r.status_code == 200, r.text
    ops = {n["op"]: n.get("params", {}) for n in captured["preset"]["nodes"]}
    assert ops["sky_replace"] == {"degree": 0.8, "mode": "storm"}
    assert ops["white_balance"]["temperature"] == 30


@pytest.mark.parametrize("bad", ["{bad", "[1, 2]"])
def test_batch_rejects_bad_overrides(srv, bad):
    _, c, tmp = srv
    (tmp / "s").mkdir()
    assert c.post("/api/batch", data={"src_dir": str(tmp / "s"), "overrides": bad}).status_code == 400


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


def test_image_panel_maps_to_engine():
    out = _node(
        "const d = defaultOverrides();"
        "const v = {...d, 'img.quality_enhance': 60, 'img.blur_background': 50, 'img.sky_enhance': 40,"
        " 'img.sky_replace': 80, 'sky_replace.mode': 'azure'};"
        "console.log(JSON.stringify({def: toEngineOverrides(d), img: toEngineOverrides(v),"
        " max: toEngineOverrides({...d, 'img.quality_enhance': 20, 'quality_enhance.degree': 90})}))"
    )
    assert not any(k.startswith(("blur_background", "sky_")) for k in out["def"])
    img = out["img"]
    assert img["quality_enhance.degree"] == 0.6 and img["blur_background.degree"] == 0.5
    assert img["sky_enhance.degree"] == 0.4 and img["sky_replace.degree"] == 0.8
    assert img["sky_replace.mode"] == "azure"
    assert out["max"]["quality_enhance.degree"] == 0.9


def test_image_panel_template_has_no_undefined_bindings():
    with open(os.path.join(ROOT, "webapp", "src", "components", "ParamPanel.vue"), encoding="utf-8") as fh:
        s = fh.read()
    assert "params[p.key]" not in s and "onParam(" not in s


def test_homepage_preset_list_defined():
    with open(os.path.join(ROOT, "webapp", "src", "views", "HomePage.vue"), encoding="utf-8") as fh:
        s = fh.read()
    assert "const presetList = computed" in s
    assert "套用工作台当前参数" in s


@pytest.mark.parametrize("name", ["texture", "bw", "basic"])
def test_profiles_change_image(name):
    import cv2
    import numpy as np

    from engine import color as C

    img = cv2.imread(os.path.join(ROOT, "tests", "test_face.jpg"))
    out = C.apply_profile(img, name, 1.0)
    assert np.abs(out.astype(int) - img).mean() > 3
    assert np.array_equal(C.apply_profile(img, name, 0.0), img)
    if name == "bw":
        assert int(np.abs(out[..., 0].astype(int) - out[..., 2]).max()) == 0


def test_profile_standard_is_identity_and_reaches_pipeline():
    import cv2
    import numpy as np
    from main import DictPipeline, build_preset

    from engine import color as C

    img = cv2.imread(os.path.join(ROOT, "tests", "test_face.jpg"))
    assert np.array_equal(C.apply_profile(img, "standard", 1.0), img)
    p = build_preset("wedding_basic", {"profile.name": "bw", "profile.strength": 1.0})
    node = next(n for n in p["nodes"] if n["op"] == "profile")
    assert node["params"] == {"name": "bw", "strength": 1.0}
    pipe = DictPipeline({"nodes": [node]})
    out = pipe.run(img.copy())
    assert int(np.abs(out[..., 0].astype(int) - out[..., 2]).max()) == 0


def test_profile_maps_from_ui():
    out = _node(
        "const d = defaultOverrides();"
        "console.log(JSON.stringify({def: toEngineOverrides(d),"
        " bw: toEngineOverrides({...d, 'color.profile': 'bw', 'color.profile_strength': 60})}))"
    )
    assert not any(k.startswith("profile.") for k in out["def"])
    assert out["bw"]["profile.name"] == "bw" and out["bw"]["profile.strength"] == 0.6


def test_unimplemented_entries_marked_wip():
    with open(os.path.join(ROOT, "webapp", "src", "components", "ParamPanel.vue"), encoding="utf-8") as fh:
        s = fh.read()
    assert "setTimeout(r, 900)" not in s  # fake multi-image unify removed
    assert "多图色彩统一</el-button>" in s and "text disabled" in s
    assert "setVal('__scope'" not in s  # scope chips no longer pretend to work


def test_hair_smooth_runs_on_uint8():
    """Regression for ef23d64: tex * 0.45 (float64) + uint8 crashed cv2.add."""
    import numpy as np

    from engine.effects.hair2 import hair_smooth

    img = np.random.default_rng(0).integers(0, 255, (64, 64, 3), dtype=np.uint8)
    parsing = np.zeros((64, 64), np.uint8)
    parsing[10:40, 10:50] = 13  # hair
    out = hair_smooth(img, parsing, 1.0)
    assert out.dtype == np.uint8 and out.shape == img.shape
    assert np.abs(out[15:35, 15:45].astype(int) - img[15:35, 15:45]).mean() > 1
