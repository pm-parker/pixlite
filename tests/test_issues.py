"""Regression tests for GitHub issues #1-#12 (see BUGFIX_REPORT.md, section "Issue 修复").

Run:  python -m pytest -q
Model-dependent tests are skipped when the model is absent (tools/download_models.py).
"""

import importlib
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
MODELS = os.path.join(ROOT, "engine", "perception", "models")
FACE = os.path.join(ROOT, "tests", "test_face.jpg")
ELDER = os.path.join(ROOT, "tests", "rt2_wed_elder_m.jpg")


def need(*names):
    miss = [n for n in names if not os.path.isfile(os.path.join(MODELS, n))]
    return pytest.mark.skipif(bool(miss), reason=f"model missing: {miss}")


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    """Fresh server module with isolated work dir and allowlist = tmp_path/photos + work."""
    photos = tmp_path / "photos"
    photos.mkdir()
    monkeypatch.setenv("PIXLITE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("PIXLITE_ALLOWED_ROOTS", str(photos))
    monkeypatch.setenv("PIXLITE_TOKEN", "x" * 32)
    monkeypatch.setenv("PIXLITE_MAX_UPLOAD_MB", "1")
    monkeypatch.setenv("PIXLITE_MAX_BATCH_FILES", "2")
    import main

    importlib.reload(main)
    from fastapi.testclient import TestClient

    local = TestClient(main.app, client=("127.0.0.1", 5000))
    remote = TestClient(main.app, client=("203.0.113.9", 5000))
    return main, local, remote, photos


# ---------------- #1 auth fail-closed + filesystem allowlist ----------------
def test_auth_fails_closed_without_token(monkeypatch):
    import main

    monkeypatch.setattr(main, "API_TOKEN", "")
    monkeypatch.setattr(main, "ALLOW_NO_TOKEN", False)
    d = main._auth_decision("203.0.113.9", "/api/presets", None, None)
    assert d is not None and d[0] == 401
    assert main._auth_decision("127.0.0.1", "/api/presets", None, None) is None
    assert main._auth_decision("203.0.113.9", "/api/health", None, None) is None
    monkeypatch.setattr(main, "ALLOW_NO_TOKEN", True)
    assert main._auth_decision("203.0.113.9", "/api/presets", None, None) is None


def test_auth_query_key_only_on_resource_routes(monkeypatch):
    import main

    monkeypatch.setattr(main, "API_TOKEN", "k" * 32)
    k = "k" * 32
    assert main._auth_decision("203.0.113.9", "/api/presets", k, None) is None
    rejected = main._auth_decision("203.0.113.9", "/api/presets", None, k)
    assert rejected is not None and rejected[0] == 401  # not via URL
    assert main._auth_decision("203.0.113.9", "/api/image", None, k) is None
    assert main._auth_decision("203.0.113.9", "/api/result/abc", None, k) is None
    wrong = main._auth_decision("203.0.113.9", "/api/presets", "wrong", None)
    assert wrong is not None and wrong[0] == 401


def test_remote_requires_key(srv):
    _, _, remote, _ = srv
    assert remote.get("/api/presets").status_code == 401
    assert remote.get("/api/presets", headers={"X-API-Key": "x" * 32}).status_code == 200


@pytest.mark.parametrize("p", ["/etc/passwd", "/etc", "/"])
def test_paths_outside_roots_forbidden(srv, p):
    _, local, _, _ = srv
    assert local.get("/api/image", params={"path": p}).status_code == 403
    assert local.get("/api/browse", params={"path": p}).status_code == 403
    assert local.get("/api/exif", params={"path": p}).status_code == 403


def test_traversal_and_symlink_escape_forbidden(srv):
    main, local, _, photos = srv
    cv2.imwrite(str(photos / "ok.jpg"), np.zeros((8, 8, 3), np.uint8))
    assert local.get("/api/image", params={"path": str(photos / "ok.jpg")}).status_code == 200
    assert local.get("/api/image", params={"path": str(photos / ".." / ".." / "x.jpg")}).status_code == 403
    (photos / "link").symlink_to("/etc")
    assert not main._path_ok(str(photos / "link" / "hosts"))
    assert local.get("/api/browse", params={"path": str(photos / "link")}).status_code == 403


def test_browse_root_lists_only_allowed_roots(srv):
    _main, local, _, photos = srv
    dirs = local.get("/api/browse").json()["dirs"]
    assert "/" not in dirs and os.path.realpath(str(photos)) in dirs


def test_batch_and_ref_image_paths_checked(srv, tmp_path):
    main, local, _, _ = srv
    outside = tmp_path / "outside"
    outside.mkdir()
    assert local.post("/api/batch", data={"src_dir": str(outside)}).status_code == 403
    with pytest.raises(ValueError):
        main.build_preset("wedding_basic", {"color_match.ref_image": "/etc/passwd"})


# ---------------- #2 resource limits ----------------
def test_upload_size_limit(srv):
    main, local, _, _ = srv
    big = b"\xff" * (main.MAX_UPLOAD_BYTES + 10)
    r = local.post("/api/upload", files={"file": ("a.jpg", big)})
    assert r.status_code == 413
    assert not os.listdir(os.path.join(main.WORK, "uploads"))  # partial file removed
    assert local.post("/api/retouch", files={"file": ("a.jpg", big)}).status_code == 413


def test_pixel_limit(srv, tmp_path, monkeypatch):
    main, *_ = srv
    p = tmp_path / "big.png"
    cv2.imwrite(str(p), np.zeros((400, 400, 3), np.uint8))
    monkeypatch.setattr(main, "MAX_PIXELS", 100 * 100)
    with pytest.raises(ValueError):
        main._load_image(str(p))


def test_batch_file_count_and_concurrency_limits(srv):
    main, local, _, photos = srv
    src = photos / "src"
    src.mkdir()
    for i in range(3):
        cv2.imwrite(str(src / f"{i}.jpg"), np.zeros((8, 8, 3), np.uint8))
    assert local.post("/api/batch", data={"src_dir": str(src)}).status_code == 413
    main.batch_jobs["busy"] = {"status": "running", "job": None}
    sub = photos / "sub"
    sub.mkdir()
    assert local.post("/api/batch", data={"src_dir": str(sub)}).status_code == 429


def test_retouch_concurrency_limit(srv):
    main, local, _, _ = srv
    for i in range(main.MAX_ACTIVE_JOBS):
        main.jobs[f"r{i}"] = {"status": "running", "output": ""}
    assert local.post("/api/retouch", files={"file": ("a.jpg", b"x")}).status_code == 429


def test_ttl_cleanup(srv):
    main, *_ = srv
    old = os.path.join(main.WORK, "deadbeef_out.jpg")
    with open(old, "wb") as fh:
        fh.write(b"x")
    os.utime(old, (1, 1))
    main.jobs["deadbeef"] = {"status": "done", "output": old, "ts": 1}
    main._gc_jobs()
    assert not os.path.exists(old) and "deadbeef" not in main.jobs


def test_batch_cancel():
    from engine.batch import BatchJob

    class P:
        def run(self, img):
            return img

    bj = BatchJob(P(), ROOT, "/tmp/none")
    bj.cancel()
    assert bj._process_one(FACE) == "cancelled"


# ---------------- #3 clean-build packaging ----------------
def test_dockerfile_builds_frontend_and_copies_assets():
    with open(os.path.join(ROOT, "docker", "Dockerfile"), encoding="utf-8") as fh:
        df = fh.read()
    assert "npm ci" in df and "npm run build" in df
    assert "COPY --from=web" in df and "fnpack/etc/lut" in df and "download_models.py" in df


def test_build_pkg_has_no_machine_specific_path():
    with open(os.path.join(ROOT, "fnpack", "build_pkg.py"), encoding="utf-8") as fh:
        s = fh.read()
    assert "Administrator" not in s and "npm" in s and "--verify" in s


# ---------------- #5 path picker ----------------
def test_pathpicker_posix_crumbs():
    node = subprocess.run(["node", "--version"], check=False, capture_output=True)
    if node.returncode != 0:
        pytest.skip("node not available")
    with open(os.path.join(ROOT, "webapp", "src", "components", "PathPicker.vue"), encoding="utf-8") as fh:
        src = fh.read()
    start = src.index("function buildCrumbs")
    end = src.index("function crumb(")
    js = (
        src[start:end]
        + r"""
let cur = ''; const loads = []; function load(p) { loads.push(p); cur = p }
const path = { get value() { return cur } };
const out = {
  root: buildCrumbs('/'), home: buildCrumbs('/home'), deep: buildCrumbs('/vol1/a/b'),
  win: buildCrumbs('C:\\photos\\a'),
};
enter('/vol1'); enter('photos'); out.loads = loads;
console.log(JSON.stringify(out));
"""
    )
    js = js.replace("path.value", "cur")
    r = subprocess.run(["node", "-e", js], check=False, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert [c["path"] for c in o["root"]] == ["/"]
    assert [c["path"] for c in o["home"]] == ["/home"]
    assert [c["path"] for c in o["deep"]] == ["/vol1", "/vol1/a", "/vol1/a/b"]
    assert [c["path"] for c in o["win"]] == ["C:/", "C:/photos", "C:/photos/a"]
    assert o["loads"] == ["/vol1", "/vol1/photos"]


# ---------------- #6 skin tone fail-closed ----------------
def test_skin_tone_skipped_when_mask_unavailable(monkeypatch):
    import engine.pipeline as EP

    monkeypatch.setattr(EP, "_skin_mask", lambda ctx, b: None)
    p = EP.Pipeline.__new__(EP.Pipeline)
    p.preset = {"nodes": [{"op": "skin_tone", "params": {"brighten": 1.0, "whiten": 1.0}}]}
    img = cv2.imread(FACE)
    assert img is not None, FACE
    assert np.array_equal(p.run(img.copy()), img)


@need("face_parsing.onnx", "face_detection_yunet_2023mar.onnx")
def test_skin_tone_leaves_non_skin_unchanged():
    import engine.pipeline as EP
    from engine.perception import get_skin_parser, mask_of

    img = cv2.imread(FACE)
    assert img is not None, FACE
    p = EP.Pipeline.__new__(EP.Pipeline)
    p.preset = {"nodes": [{"op": "face_detect"}, {"op": "skin_tone", "params": {"brighten": 1.0}}]}
    out = p.run(img.copy())
    # 皮肤遮罩包含鼻子 (否则提亮后鼻子留成暗斑)
    skin = mask_of(get_skin_parser()(img), "skin", "nose", dilate=2)
    bg = skin == 0
    assert np.abs(out[bg].astype(int) - img[bg]).max() <= 1
    assert np.abs(out[skin > 128].astype(int) - img[skin > 128]).mean() > 1


# ---------------- #8 background color degree ----------------
@need("modnet.onnx")
@pytest.mark.parametrize("deg", [0.0, 0.5, 1.0])
def test_bg_color_degree(deg):
    from engine.effects import background_fx as B

    img = cv2.imread(FACE)
    assert img is not None, FACE
    out = B.replace_bg_color(img, (200, 30, 30), degree=deg)
    corner = (slice(0, 40), slice(0, 40))
    a = B.get_alpha(img)[corner].mean()
    assert a < 0.05
    want = img[corner].astype(float) * (1 - deg) + np.array([200, 30, 30]) * deg
    assert np.abs(out[corner].astype(float) - want).mean() < 3
    if deg == 0:
        assert np.array_equal(out, img)


# ---------------- #9 per-face classification ----------------
@need("face_detection_yunet_2023mar.onnx", "genderage.onnx")
def test_faces_classified_independently():
    import engine.pipeline as EP

    a = cv2.imread(FACE)
    b = cv2.imread(ELDER)
    assert a is not None and b is not None, (FACE, ELDER)
    b = cv2.resize(b, (int(b.shape[1] * a.shape[0] / b.shape[0]), a.shape[0]))
    two = np.hstack([a, b])
    cap = {}
    orig = EP._LazyCtx

    class C(orig):
        def __init__(self):
            super().__init__()
            cap["c"] = self

    EP._LazyCtx = C
    try:
        p = EP.Pipeline.__new__(EP.Pipeline)
        p.preset = {"nodes": [{"op": "face_detect"}]}
        p.run(two.copy())
    finally:
        EP._LazyCtx = orig
    faces = sorted(cap["c"]["faces"], key=lambda f: f["box"][0])
    groups = [f["person"]["group"] for f in faces]
    assert len(set(groups)) == 2, groups  # old code copied face[0] to everyone
    assert cap["c"].get("mixed_population") is True  # no global routing for mixed groups


# ---------------- #10 model manifest ----------------
def test_manifest_pins_revisions_and_hashes():
    from engine.perception.models_manifest import MODELS as M

    for name, s in M.items():
        sha, size, url = s["sha256"], s["size"], s["url"]
        assert isinstance(sha, str) and len(sha) == 64, name
        assert isinstance(size, int) and size > 0, name
        assert isinstance(url, str) and "/resolve/main/" not in url and "/raw/main/" not in url, name


def test_reject_truncated_model(tmp_path):
    from engine.perception.models_manifest import check

    p = tmp_path / "modnet.onnx"
    p.write_bytes(b"x" * 2048)
    msg = check(str(p), full=False)
    assert msg is not None and "size mismatch" in msg
    from engine.gpu import make_session

    with pytest.raises(RuntimeError):
        make_session(str(p))


@need("genderage.onnx")
def test_installed_model_sha256_ok():
    from engine.perception.models_manifest import check

    assert check(os.path.join(MODELS, "genderage.onnx"), full=True) is None


def test_requirements_pinned():
    for f in ("requirements.txt", os.path.join("fnpack", "requirements.txt")):
        with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#")[0].strip()
                if line:
                    assert "==" in line, (f, line)


# ---------------- #11 quality degree 0 ----------------
def test_quality_enhance_degree_zero_skips_model(monkeypatch):
    import engine.effects.quality_enhance as Q

    def boom(*a, **k):
        raise AssertionError("model must not run at degree 0")

    monkeypatch.setattr(Q, "_tile_forward", boom)
    monkeypatch.setattr(Q, "get_session", boom)
    img = cv2.imread(FACE)
    assert Q.enhance_quality(img, degree=0) is img
    assert Q.enhance_quality(img, degree=-1) is img


# ---------------- #12 legacy scripts exit non-zero ----------------
@pytest.mark.parametrize("script", ["run_e2e_c.py", "run_out_c.py"])
def test_legacy_scripts_exit_code(script):
    with open(os.path.join(ROOT, "tests", script), encoding="utf-8") as fh:
        s = fh.read()
    assert "sys.exit(1 if fails else 0)" in s
