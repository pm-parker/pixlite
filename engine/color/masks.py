"""F5: Local color masks — PixCake 局部调色 (主体/背景/皮肤/天空/头发).
Masks from existing SegFormer 19-class parsing; sky approximated by upper-gradient when no sky class."""
import cv2
import numpy as np

from ..perception import mask_of


def build_mask(parsing, kind, feather=25):
    """kind: subject|background|skin|hair|sky|face. Returns float32 0..1 HxW."""
    h, w = parsing.shape[:2]
    if kind == 'subject':
        m = np.maximum(mask_of(parsing, 'skin', 'nose', 'hair', 'neck', 'cloth',
                               'lip_up', 'lip_low', 'mouth', 'eye_l', 'eye_r',
                               'brow_l', 'brow_r', 'ear_l', 'ear_r'),
                       mask_of(parsing, 'eye_g', 'hat'))
    elif kind == 'background':
        m = mask_of(parsing, 'bg')
    elif kind == 'skin':
        m = mask_of(parsing, 'skin', 'nose', 'lip_up', 'lip_low', 'mouth',
                    'neck', 'ear_l', 'ear_r')
    elif kind == 'hair':
        m = mask_of(parsing, 'hair')
    elif kind == 'face':
        m = mask_of(parsing, 'skin', 'nose', 'lip_up', 'lip_low', 'mouth',
                    'eye_l', 'eye_r', 'brow_l', 'brow_r')
    elif kind == 'sky':
        # SegFormer face-parsing has no sky class; approximate: bg pixels in
        # upper region weighted by vertical gradient (sky is brighter/bluer at top).
        bg = (parsing == 0).astype(np.float32)
        yy = np.linspace(1, 0, h)[:, None] * np.ones((1, w))  # top=1
        # blue dominance of original needed; use luminance-free vertical weight on bg
        m = (bg * (yy ** 1.5) * 255).astype(np.uint8)
    else:
        m = np.zeros((h, w), np.uint8)
    if feather:
        k = max(3, int(feather) * 2 + 1)
        m = cv2.GaussianBlur(m, (k, k), 0)
    return (m.astype(np.float32) / 255.0)[..., None]

def apply_masked(bgr, func, mask):
    """out = bgr*(1-mask) + func(bgr)*mask"""
    out = func(bgr)
    return (bgr.astype(np.float32) * (1 - mask) + out.astype(np.float32) * mask).astype(np.uint8)
