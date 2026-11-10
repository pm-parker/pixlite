"""MLS (Moving Least Squares) warp — aligned with FaceSurgery/PoseRefine/Liquify.
Reference: Schaefer et al. 2006; PixCake uses control-point driven MLS (FaceSurgery
ControlNodes + liquify GLSL). CPU version via dense remap (LiquifyRunWarp.frag semantics)."""
import numpy as np, cv2
from . import smile

def mls_rigid_warp(img, src_pts, dst_pts, grid_step=8, alpha=1.0):
    """Rigid MLS. src_pts=original landmark positions, dst_pts=target positions."""
    h, w = img.shape[:2]
    p = np.asarray(src_pts, np.float32)
    q = np.asarray(dst_pts, np.float32)
    ys, xs = np.mgrid[0:h:grid_step, 0:w:grid_step].astype(np.float32)
    v = np.stack([xs, ys], axis=-1).reshape(-1, 2)

    # w_i = 1/|p_i - v|^{2alpha}
    diff = v[:, None, :] - p[None, :, :]
    wts = 1.0 / (np.linalg.norm(diff, axis=2) ** (2 * alpha) + 1e-8)
    phat = p - (wts[:, None] * p).sum(1, keepdims=True) / wts.sum(1, keepdims=True)
    qhat = q - (wts[:, None] * q).sum(1, keepdims=True) / wts.sum(1, keepdims=True)

    mu = (wts[:, None] * (phat[..., 0] * qhat[:, 0] + phat[..., 1] * qhat[:, 1])).sum(1)
    out_pts = np.empty_like(v)
    # solve per-point rigid transform
    for i in range(len(v)):
        A = (wts[i, :, None] * phat).T @ qhat
        U, S, Vt = np.linalg.svd(A)
        R = (Vt.T @ U.T)
        if np.linalg.det(R) < 0:
            Vt[-1] *= -1; R = Vt.T @ U.T
        out_pts[i] = (v[i] - (wts[i] * p).sum(0) / wts.sum(0)) @ R + (wts[i] * q).sum(0) / wts.sum(0)

    # build remap from sparse grid (scattered interpolation)
    gx = (v[:, 0] / grid_step).astype(int); gy = (v[:, 1] / grid_step).astype(int)
    gh, gw = h // grid_step + 1, w // grid_step + 1
    mapx = np.zeros((gh, gw), np.float32); mapy = np.zeros((gh, gw), np.float32)
    ok = (gx < gw) & (gy < gh)
    mapx[gy[ok], gx[ok]] = out_pts[ok, 0]
    mapy[gy[ok], gx[ok]] = out_pts[ok, 1]
    # fill empty grid cells via nearest
    mask0 = mapx == 0
    if mask0.any():
        ny, nx = np.where(~mask0)
        if len(ny):
            from scipy.spatial import cKDTree
        # fallback: griddata-free nearest fill
        valid_pts = np.stack([mapx[~mask0], mapy[~mask0]], 1) if (~mask0).any() else None
        yy, xx = np.where(mask0)
        if valid_pts is not None and len(yy):
            vy, vx = np.where(~mask0)
            d = ((yy[:, None] - vy[None]) ** 2 + (xx[:, None] - vx[None]) ** 2)
            nn = d.argmin(1)
            mapx[mask0] = mapx[vy[nn], vx[nn]]
            mapy[mask0] = mapy[vy[nn], vx[nn]]
    mapx = cv2.resize(mapx, (w, h), interpolation=cv2.INTER_LINEAR)
    mapy = cv2.resize(mapy, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.remap(img, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

def liquify_push(img, center, radius, strength, direction=None):
    """Manual liquify brush — aligned LiquifyPush semantics (forward warp)."""
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.stack([xx - center[0], yy - center[1]], -1)
    dist = np.linalg.norm(d, axis=-1)
    fall = np.clip(1 - dist / radius, 0, 1) ** 2
    if direction is None:
        disp = d / (np.linalg.norm(d, axis=-1, keepdims=True) + 1e-6) * (strength * fall)[..., None]
    else:
        disp = np.array(direction, np.float32) * (strength * fall)[..., None]
    mapx = (xx - disp[..., 0]).astype(np.float32)
    mapy = (yy - disp[..., 1]).astype(np.float32)
    return cv2.remap(img, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
