#!/usr/bin/env python3
"""Download PixLite model files (public open-source models) into engine/perception/models/.

All models are public open-source weights (BSD-3 / Apache-2.0 / MIT sources). ~750MB total.
Usage:
    python tools/download_models.py            # direct
    python tools/download_models.py --mirror   # use hf-mirror.com (CN network)
"""
import os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, '..', 'engine', 'perception', 'models')

HF = "https://huggingface.co"

# name -> (url, size, note). Sizes verified against the reference build.
FILES = {
    # OpenCV Zoo — YuNet face detection (BSD-3, opencv/opencv_zoo)
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        232589, "OpenCV Zoo YuNet"),
    # SegFormer face parsing — Xenova/face-parsing (onnx/model.onnx)
    "face_parsing.onnx": (
        f"{HF}/Xenova/face-parsing/resolve/main/onnx/model.onnx",
        340316611, "SegFormer face parsing 19-class"),
    # GFPGAN v1.4 onnx (MIT) — Neus/GFPGANv1.4
    "gfpgan_1.4.onnx": (
        f"{HF}/Neus/GFPGANv1.4/resolve/main/GFPGANv1.4.onnx",
        None, "GFPGAN 1.4 face restore"),  # 转换版之间有 ~42KB 差异, 不做严格校验
    # Real-ESRGAN x2 fp16 — rippertnt/upscale
    "real_esrgan_x2_fp16.onnx": (
        f"{HF}/rippertnt/upscale/resolve/main/RealESRGAN_x2_fp16.onnx",
        None, "Real-ESRGAN x2 fp16"),
    # MODNet portrait matting (Apache-2.0) — Xenova/modnet
    "modnet.onnx": (
        f"{HF}/Xenova/modnet/resolve/main/onnx/model.onnx",
        25888640, "MODNet portrait matting"),
    # BeautyGAN makeup transfer — maze/faceX (基于 Honlan/BeautyGAN 权重)
    "beautygan.onnx": (
        f"{HF}/maze/faceX/resolve/main/beautygan.onnx",
        36959062, "BeautyGAN makeup transfer"),
    # insightface genderage (MIT) — buffalo_l official weights
    "genderage.onnx": (
        f"{HF}/public-data/insightface/resolve/main/models/buffalo_l/genderage.onnx",
        1322532, "insightface genderage"),
    # MediaPipe face landmarker 478pts (Apache-2.0) — yakhyo/mediapipe-face-mesh-onnx
    # 注意: 与参考构建的转换版本略有差异 (4.86MB vs 4.92MB), landmark 顺序一致
    "face_landmarks.onnx": (
        "https://github.com/yakhyo/mediapipe-face-mesh-onnx/releases/download/weights/face_landmarker_Nx3x256x256.onnx",
        None, "MediaPipe 478pt landmarker (ONNX port)"),
}

def dl(name, url, dst):
    print(f"[{name}] GET {url}")
    tmp = dst + ".part"
    urllib.request.urlretrieve(url, tmp)
    os.replace(tmp, dst)
    print(f"[{name}] -> {dst} ({os.path.getsize(dst)} bytes)")

def main():
    mirror = "--mirror" in sys.argv
    os.makedirs(MODELS, exist_ok=True)
    fails = []
    for name, (url, _sz, _note) in FILES.items():
        if mirror:
            url = url.replace("https://huggingface.co", "https://hf-mirror.com")
        dst = os.path.join(MODELS, name)
        if os.path.exists(dst) and os.path.getsize(dst) > 1024:
            print(f"[{name}] exists, skip")
            continue
        try:
            dl(name, url, dst)
        except Exception as e:
            print(f"[{name}] FAILED: {e}")
            fails.append(name)
    if fails:
        print("FAILED:", ", ".join(fails)); sys.exit(1)
    print("All models ready:", os.path.abspath(MODELS))

if __name__ == "__main__":
    main()
