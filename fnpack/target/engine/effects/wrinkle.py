"""Wrinkle smoothing — 纹路平滑 + 祛双下巴 (B-group local CV).
Directional low-pass on oriented line-type masks (generated procedurally from
parsing landmarks), and double-chin shadow reshaping via jaw-shadow gradient."""
import numpy as np, cv2
from ..perception import mask_of

REGIONS = {
    # region: (anchor part, dy offset factor of face height, band half-height)
    'forehead': (None, None, None),   # special: above brows
    'sichuan': ('nose', 0.02, 0.03),  # glabella between brows
    'eye': ('eye_l', 0.05, 0.04),
    'nose': ('nose', 0.0, 0.04),
    'cheek': ('skin', 0.18, 0.06),
    'mouth': ('mouth', 0.08, 0.04),
    'puppet': ('mouth', 0.14, 0.05),
    'neck': ('neck', 0.0, 0.10),
}


def _face_ctx(faces, shape):
    h, w = shape[:2]
    if faces and 'box' in faces[0]:
        x, y, fw, fh = faces[0]['box']
        return int(x), int(y), int(fw), int(fh)
    return int(w*0.3), int(h*0.15), int(w*0.4), int(h*0.5)


def _part_mask(parsing, faces, anchor):
    """Anchor part mask with box-ratio fallback (mouth often unparsed when closed)."""
    m = mask_of(parsing, anchor, dilate=2)
    if m.max() > 0:
        return m
    if faces and 'box' in faces[0]:
        x, y, fw, fh = faces[0]['box']
        h, w = parsing.shape[:2]
        m = np.zeros((h, w), np.uint8)
        if anchor == 'mouth':
            cy, rx, ry = y + fh * 0.74, fw * 0.20, fh * 0.04
        elif anchor == 'nose':
            cy, rx, ry = y + fh * 0.62, fw * 0.10, fh * 0.05
        else:
            return m
        cv2.ellipse(m, (int(x + fw / 2), int(cy)), (int(rx), int(ry)), 0, 0, 360, 255, -1)
    return m


def region_mask(parsing, faces, region):
    h, w = parsing.shape[:2]
    x, y, fw, fh = _face_ctx(faces, parsing.shape)
    blank = np.zeros((h, w), np.uint8)
    if region == 'forehead':
        brows = cv2.bitwise_or(mask_of(parsing, 'brow_l', dilate=2), mask_of(parsing, 'brow_r', dilate=2))
        ys, xs = np.where(brows > 128)
        y1 = int(ys.max()) if len(ys) else int(y + fh * 0.30)
        skin = mask_of(parsing, 'skin', dilate=1)
        band = np.zeros_like(blank)
        band[max(y1 - int(fh*0.02), 0):y1, max(x-int(fw*0.1), 0):int(x+fw*1.1)] = 255
        return cv2.bitwise_and(band, skin)
    if region.startswith('nasolabial'):
        side = region.rsplit('_', 1)[-1] if region != 'nasolabial' else 'both'
        # lines from nose wings to mouth corners
        mouth = _part_mask(parsing, faces, 'mouth')
        nose = _part_mask(parsing, faces, 'nose')
        mys, mxs = np.where(mouth > 128)
        nys, nxs = np.where(nose > 128)
        if len(mys) == 0:
            return blank
        my_bottom = int(mys.max())
        mx0, mx1 = int(mxs.min()), int(mxs.max())
        ny = int(nys.max()) if len(nys) else my_bottom - int(fh * 0.1)
        nx0, nx1 = int(nxs.min()), int(nxs.max())
        skin = mask_of(parsing, 'skin', dilate=1)
        band = np.zeros_like(blank)
        thick = max(int(fh * 0.05), 4)
        for i, sd in enumerate(('l', 'r')):
            if side != 'both' and sd != side:
                continue
            cv2.line(band, (nx0 if i == 0 else nx1, ny),
                     (mx0 if i == 0 else mx1, my_bottom), 255, thick)
        return cv2.bitwise_and(band, skin)
    if region == 'jaw_shadow':
        return mask_of(parsing, 'skin', dilate=2)  # full face; op handles chin zone
    anchor, dyf, hbf = REGIONS.get(region, (None, None, None))
    if anchor is None:
        return blank
    part = _part_mask(parsing, faces, anchor)
    if part.max() == 0:
        return blank
    ys, xs = np.where(part > 128)
    yc = int(ys.mean())
    band = np.zeros_like(blank)
    dyy = int(fh * (dyf or 0))
    hh = max(int(fh * (hbf or 0.04)), 4)
    band[ max(yc+dyy-hh, 0): yc+dyy+hh, : ] = 255
    return cv2.bitwise_and(band, mask_of(parsing, 'skin', dilate=2))


def wrinkle_smooth(bgr, parsing, faces=None, region='cheek', degree=0.5):
    """Directional low-pass: suppress line-orthogonal high-freq (dark line fill)
    inside region mask, keep pores via detail re-add."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0:
        return bgr
    m = region_mask(parsing, faces or [], region)
    if region == 'nasolabial' and isinstance(faces, list) and len(faces) == 0:
        pass
    if m.max() == 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[..., 0]
    low = cv2.GaussianBlur(L, (0, 0), 3.0)
    detail = L - low
    neg = np.clip(detail, -35, 0)  # wrinkle creases darker than surroundings
    sel = m > 128
    L2 = L - neg * d
    # extra very-low-freq fill (shading of the crease)
    low2 = cv2.GaussianBlur(L, (0, 0), 20)
    L2 = L2 - np.clip(low2 - L, 0, None) * 0.15 * d
    lab[..., 0][sel] = np.clip(L2[sel], 0, 255)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def double_chin(bgr, parsing, faces=None, degree=0.5):
    """祛双下巴: chin/jaw shadow band lifting (L gradient compression) + optional
    jawline shading enhancement below the mandible edge."""
    d = float(np.clip(degree, 0, 1))
    if d <= 0:
        return bgr
    x, y, fw, fh = _face_ctx(faces or [], parsing.shape)
    skin = cv2.bitwise_or(mask_of(parsing, 'skin', dilate=2), mask_of(parsing, 'neck', dilate=2))
    mouth = mask_of(parsing, 'mouth', dilate=0)
    ys, _ = np.where(mouth > 128)
    y0 = int(ys.max()) if len(ys) else int(y + fh * 0.72)
    y1 = int(min(y0 + fh * 0.45, parsing.shape[0]))
    band = np.zeros_like(skin)
    band[y0:y1, max(int(x - fw*0.15), 0):int(x + fw*1.15)] = 255
    band = cv2.bitwise_and(band, skin)
    if band.max() == 0:
        return bgr
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[..., 0]
    low = cv2.GaussianBlur(L, (0, 0), 15)
    deficit = np.clip(low - L, 0, None)  # chin shadow darker than blurred mean
    sel = band > 128
    lab[..., 0][sel] = np.clip(L[sel] + deficit[sel] * 0.8 * d, 0, 255)
    # jawline accent: darken a thin line just below the jaw edge
    edge = cv2.morphologyEx(band, cv2.MORPH_GRADIENT, np.ones((7, 7), np.uint8))
    edge = cv2.bitwise_and(edge, band)
    lab[..., 0][(edge > 128)] = np.clip(lab[..., 0][(edge > 128)] - 12 * d, 0, 255)
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
