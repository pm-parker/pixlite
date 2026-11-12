"""Batch directory processing — NAS core scenario.
Walk a directory (optionally recursive), run pipeline on each image, mirror
output structure. Supports resume, concurrency, RAW formats."""
import os, sys, time, json, threading, queue
from concurrent.futures import ThreadPoolExecutor, as_completed

IMG_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}
RAW_EXTS = {'.dng', '.cr2', '.cr3', '.arw', '.nef', '.raf', '.orf', '.rw2'}

def collect_images(src_dir, recursive=True):
    out = []
    it = os.walk(src_dir) if recursive else [(src_dir, [], os.listdir(src_dir))]
    for root, dirs, files in it:
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in IMG_EXTS or ext in RAW_EXTS:
                out.append(os.path.join(root, f))
    return sorted(out)

MAX_PIXELS = int(os.environ.get('PIXLITE_MAX_PIXELS', 100_000_000))

def _check_dims(path):
    """解码前按图像头尺寸拒绝超大图 (防解压炸弹, Issue #2)。"""
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return
    if w * h > MAX_PIXELS:
        raise ValueError(f'image {w}x{h} exceeds PIXLITE_MAX_PIXELS={MAX_PIXELS}')

def load_image(path):
    import cv2, numpy as np
    _check_dims(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in RAW_EXTS:
        import rawpy
        with rawpy.imread(path) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    data = np.fromfile(path, dtype=np.uint8)  # unicode-safe on win
    return cv2.imdecode(data, cv2.IMREAD_COLOR)

def save_image(img, path, quality=92):
    import cv2, numpy as np
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ext = os.path.splitext(path)[1].lower()
    if ext == '.jpg' or ext == '.jpeg':
        cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tofile(path)
    elif ext == '.png':
        cv2.imencode('.png', img)[1].tofile(path)
    else:
        cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tofile(os.path.splitext(path)[0] + '.jpg')

class BatchJob:
    def __init__(self, pipeline, src_dir, dst_dir, recursive=True, workers=2, skip_existing=True):
        self.pipeline = pipeline
        self.src, self.dst = src_dir, dst_dir
        self.recursive, self.workers, self.skip_existing = recursive, workers, skip_existing
        self.total = self.done = self.failed = 0
        self.errors = []
        self.status = 'pending'
        self.cancelled = False
        self._lock = threading.Lock()

    def cancel(self):
        self.cancelled = True

    def _process_one(self, src_path):
        if self.cancelled:
            return 'cancelled'
        rel = os.path.relpath(src_path, self.src)
        dst_path = os.path.join(self.dst, os.path.splitext(rel)[0] + '.jpg')
        if self.skip_existing and os.path.exists(dst_path):
            return 'skipped'
        img = load_image(src_path)
        if img is None:
            return 'load_error'
        out = self.pipeline.run(img)
        save_image(out, dst_path)
        return 'ok'

    def run(self, progress_cb=None):
        files = collect_images(self.src, self.recursive)
        self.total = len(files)
        self.status = 'running'
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(self._process_one, f): f for f in files}
            for fut in as_completed(futs):
                src = futs[fut]
                try:
                    r = fut.result()
                    with self._lock:
                        if r == 'ok' or r == 'skipped':
                            self.done += 1
                        elif r == 'cancelled':
                            pass
                        else:
                            self.failed += 1
                            self.errors.append((src, r))
                except Exception as e:
                    with self._lock:
                        self.failed += 1
                        self.errors.append((src, str(e)[:200]))
                if progress_cb:
                    progress_cb(self.done + self.failed, self.total)
        self.status = 'cancelled' if self.cancelled else 'done'
        return {
            'cancelled': self.cancelled,
            'total': self.total, 'done': self.done, 'failed': self.failed,
            'elapsed': round(time.time() - t0, 1),
            'errors': self.errors[:20],
        }
