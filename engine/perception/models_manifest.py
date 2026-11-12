"""Pinned model manifest (Issue #10).

Every model is pinned to an immutable revision (HF commit sha / git commit sha) and
verified by size + SHA-256 after download. tools/download_models.py downloads from
this table; engine.gpu.make_session refuses to load a model whose size does not match
(cheap check, catches truncated / replaced files before inference).

To update a model: change url/revision, then set size/sha256 from the new artifact.
"""
import hashlib
import os

HF = "https://huggingface.co"

# name -> dict(url, size, sha256, note)
MODELS = {
    "face_detection_yunet_2023mar.onnx": dict(
        url="https://github.com/opencv/opencv_zoo/raw/f12e12798e8314f7c074a6656816c048dcc95b7a/"
            "models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        size=232589,
        sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        note="OpenCV Zoo YuNet (BSD-3)"),
    "face_parsing.onnx": dict(
        url=f"{HF}/Xenova/face-parsing/resolve/f25b9b521a8783d4e78e80e026ef4c2a15f821e0/onnx/model.onnx",
        size=340316611,
        sha256="6d4e67af60ff78184745ebf74cc15163c0adc27d45cdeba31e3a03d1096fb8c3",
        note="SegFormer face parsing 19-class"),
    "gfpgan_1.4.onnx": dict(
        url=f"{HF}/Neus/GFPGANv1.4/resolve/944e127f1eae51e3471cc943e9df3be135e681a9/GFPGANv1.4.onnx",
        size=340256686,
        sha256="cd7311b8d9e13cdb1e208b12363182da58c7bf45e26d1aa67bbeac4751aae92e",
        note="GFPGAN 1.4 face restore (MIT)"),
    "real_esrgan_x2_fp16.onnx": dict(
        url=f"{HF}/rippertnt/upscale/resolve/414c2d638146c757f16ce4130869180af2110159/RealESRGAN_x2_fp16.onnx",
        size=36145554,
        sha256="22a5d72274bacd09a33dba691cc02f0819c3da218ed382e1e318e34a993a7ffe",
        note="Real-ESRGAN x2 fp16"),
    "modnet.onnx": dict(
        url=f"{HF}/Xenova/modnet/resolve/fa2fa546052fba4c08921230a26cc69a333fca12/onnx/model.onnx",
        size=25888640,
        sha256="07c308cf0fc7e6e8b2065a12ed7fc07e1de8febb7dc7839d7b7f15dd66584df9",
        note="MODNet portrait matting (Apache-2.0)"),
    "beautygan.onnx": dict(
        url=f"{HF}/maze/faceX/resolve/69669587993663e466575b897aa26a267a79bc94/beautygan.onnx",
        size=36959062,
        sha256="0f0adc95890f15e0549a64a3388acf69b02dbc0538f16e65514937d1766bdafa",
        note="BeautyGAN makeup transfer"),
    "genderage.onnx": dict(
        url=f"{HF}/public-data/insightface/resolve/33c1063c49c785b7652d3fd529f86fa4f149392b/"
            "models/buffalo_l/genderage.onnx",
        size=1322532,
        sha256="4fde69b1c810857b88c64a335084f1c3fe8f01246c9a191b48c7bb756d6652fb",
        note="insightface genderage (MIT)"),
    # GitHub release asset (tag 'weights'); content pinned by sha256
    "face_landmarks.onnx": dict(
        url="https://github.com/yakhyo/mediapipe-face-mesh-onnx/releases/download/weights/"
            "face_landmarker_Nx3x256x256.onnx",
        size=4864717,
        sha256="111795f8703cdeb6d0c68a9f3cc966a0f23f8786bb00f4577a11f461fc4276ac",
        note="MediaPipe 478pt landmarker ONNX port (Apache-2.0), NCHW input"),
}


def sha256_of(path, bufsize=4 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        while chunk := fh.read(bufsize):
            h.update(chunk)
    return h.hexdigest()


def check(path, full=True):
    """Return None if `path` matches its manifest entry, else a reason string.
    full=False only checks existence + size (fast, used before inference)."""
    name = os.path.basename(path)
    spec = MODELS.get(name)
    if not os.path.isfile(path):
        return f'missing: {path}'
    if spec is None:
        return None                      # not a managed model (custom / test)
    size = os.path.getsize(path)
    if size != spec['size']:
        return f'size mismatch for {name}: {size} != expected {spec["size"]}'
    if full:
        digest = sha256_of(path)
        if digest != spec['sha256']:
            return f'sha256 mismatch for {name}: {digest}'
    return None
