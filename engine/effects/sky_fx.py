"""SkyFX — 天空检测(色彩+位置启发式) 与 替换/增强 (对标像素蛋糕"天空"功能).
启发式适用于外景蓝天场景; 人像区用 MODNet alpha 保护, 不误伤."""
import contextlib

import cv2
import numpy as np


def detect_sky(bgr, feather=9):
    """返回 float32 天空掩码(0..1, 原分辨率). 蓝色域+最大连通域+上半偏向+人像保护."""
    H, W = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    Hh, Ss, Vv = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    sky = ((Hh >= 95) & (Hh <= 130) & (Ss >= 25) & (Vv >= 70)).astype(np.uint8)
    k = max(H, W) // 30 | 1
    sky = cv2.morphologyEx(sky, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    sky = cv2.morphologyEx(sky, cv2.MORPH_OPEN, np.ones((k // 2, k // 2), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(sky)
    if n > 1:
        big = 1 + int(np.argmax(stats[1:, 4]))
        sky = (lab == big).astype(np.float32)
    else:
        sky = np.zeros((H, W), np.float32)
    # 人像保护: MODNet alpha 前景剔除 (模型缺失时退化为不保护)
    with contextlib.suppress(Exception):
        from .background_fx import get_alpha
        alpha = get_alpha(bgr)
        sky = sky * (1 - (alpha > 0.6).astype(np.float32))
    sky = cv2.GaussianBlur(sky, (0, 0), feather)
    return sky

def replace_sky(bgr, mode='sunset', degree=1.0):
    """天空替换. mode: sunset/azure/storm/dusk 渐变色板(垂直渐变合成, 无版权素材)."""
    H, W = bgr.shape[:2]
    sky = detect_sky(bgr)
    palettes = {
        'sunset': [(200, 140, 60), (60, 90, 220)],   # BGR 上橙下蓝
        'azure':  [(255, 230, 160), (140, 90, 40)],
        'storm':  [(70, 60, 55), (40, 35, 32)],
        'dusk':   [(90, 50, 90), (30, 30, 60)],
    }
    top, bot = palettes.get(mode, palettes['sunset'])
    ramp = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None, None]
    grad = (np.array(bot, np.float32) * (1 - ramp) + np.array(top, np.float32) * ramp)
    grad = np.broadcast_to(grad, (H, W, 3)).astype(np.float32)
    m = (sky[..., None] * degree)
    out = bgr.astype(np.float32) * (1 - m) + grad * m
    return out.clip(0, 255).astype(np.uint8)

def enhance_sky(bgr, degree=0.6):
    """天空增强: 对天空区拉饱和+对比 (蓝天更通透)."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    sky = detect_sky(bgr)[..., None]
    hsv[..., 1] = (hsv[..., 1] * (1 + 0.8 * degree * sky[..., 0])).clip(0, 255)
    hsv[..., 2] = (hsv[..., 2] * (1 + 0.25 * degree * sky[..., 0])).clip(0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
