"""
capture_engine.py - Phase 4: continuous dataset capture (whole objects, exactly once, no dropped frames).

PIPELINE (each step in its own thread, so the camera is never slowed down):
  camera thread  --push_frame()-->  frame queue  -->  segment thread (per camera)  -->  write queue  -->  writer threads
     (copy only, < 1 ms)            (bounded)         stitch + find objects once           (bounded)        PNG/JPG to disk

OUTPUT (one folder per session):
  <out_root>/<PRODUCT>_<date_time>/
      crops/<CLASS>/<CAM>_L<line>_<id>.png    224 x 224 squares, ready for the CNN project
                                              (train with:  train_cnn.py --dataset <...>/crops)
      raw/<CLASS>/...                          (optional) the unscaled crop
      strips/<CAM>/strip_<CAM>_L<first>-<last>.jpg   the full continuous strip in chunks (optional)
      objects.csv                              one row per object (lines, pixels, mm, flags, files)
      session.json                             settings + final counters

CROP FORMAT = the same as the CNN project uses at inference (cashew_cnn_grader._cnn_predict):
  pad the crop to a square with edge-pixel replication (cv2.BORDER_REPLICATE), then resize to 224 x 224.
  Line-scan pixels are not square (0.085 mm across, ~0.35 mm along the flow at 10 kHz / 3.5 m/s), so the
  crop is first stretched along the flow by `aspect` = mm_per_line / mm_per_px to show the real shape.
"""

import csv
import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field, asdict

import cv2
import numpy as np

from object_stream import StreamSegmenter

ROOT = os.path.dirname(os.path.abspath(__file__))

# Class names of the CNN project (D:\CNN\CNN\app\cashew_class_mapping.json) - offered in the UI
CNN_CASHEW_CLASSES = ["GOOD_CASHEW", "TESTA", "UPW", "DW1", "DW2", "WHITE_DW", "SW", "BROWN", "REJECTION",
                      "OIL", "PKW1", "PKW2", "TESTA SMALL"]


def load_presets():
    try:
        with open(os.path.join(ROOT, "product_presets.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"mm_per_px_across": 0.085, "products": {}}


@dataclass
class CaptureConfig:
    product: str = "CASHEWS"
    class_label: str = "UNLABELLED"
    cameras: tuple = ("FRONT",)
    out_root: str = os.path.join(ROOT, "dataset_capture", "continuous")
    save_crops: bool = True
    save_raw: bool = False
    save_strips: bool = True
    strip_lines: int = 2048
    strip_format: str = "jpg"           # jpg (small, fast) or png (lossless, ~5x bigger)
    jpg_quality: int = 95
    crop_size: int = 224
    pad_px: int = 10
    min_size_mm: float = 5.0
    max_size_mm: float = 35.0
    threshold: int = 30
    mm_per_px: float = 0.085            # across the chute
    mm_per_line: float = 0.0            # along the flow; 0 = unknown -> no aspect correction
    x_range: tuple = None               # (start_pixel, end_pixel) - only look at the chute
    frame_queue: int = 600              # frames buffered per camera (~12 s at 50 frames/s)
    write_queue: int = 4000
    writer_threads: int = 2

    @classmethod
    def from_preset(cls, product, **kw):
        p = load_presets()
        pr = p.get("products", {}).get(product, {})
        c = cls(product=product, mm_per_px=p.get("mm_per_px_across", 0.085), **kw)
        for k, attr in (("crop_pad_px", "pad_px"), ("crop_size", "crop_size"), ("min_size_mm", "min_size_mm"),
                        ("max_size_mm", "max_size_mm"), ("threshold", "threshold")):
            if k in pr and attr not in kw:
                setattr(c, attr, pr[k])
        return c


@dataclass
class CaptureCounters:
    frames: dict = field(default_factory=dict)
    objects: dict = field(default_factory=dict)
    dropped_frames: dict = field(default_factory=dict)      # gaps in the camera timestamps
    queue_overflow: dict = field(default_factory=dict)      # frames we could not even queue (PC too slow)
    rejected_size: int = 0
    files_written: int = 0
    bytes_written: int = 0
    write_errors: int = 0
    write_backlog_max: int = 0


def square_224(crop, size=224):
    """Same preparation as the CNN app: replicate-pad to a square, resize to size x size."""
    h, w = crop.shape[:2]
    m = max(h, w)
    top = (m - h) // 2
    left = (m - w) // 2
    sq = cv2.copyMakeBorder(crop, top, m - h - top, left, m - w - left, cv2.BORDER_REPLICATE)
    return cv2.resize(sq, (size, size), interpolation=cv2.INTER_AREA if m > size else cv2.INTER_CUBIC)


class CaptureSession:
    def __init__(self, cfg: CaptureConfig, session_name=None):
        self.cfg = cfg
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.dir = os.path.join(cfg.out_root, session_name or f"{cfg.product}_{stamp}")
        self.c = CaptureCounters()
        self.running = False
        self._fq = {}
        self._seg_threads = {}
        self._wq = queue.Queue(maxsize=cfg.write_queue)
        self._writers = []
        self._csv_lock = threading.Lock()
        self._csv = None
        self._csv_writer = None
        self._seg = {}
        self._frame_meta = {}
        self._strip = {}
        self._learned = {}
        self.t_start = None
        self.warnings = []

    # ------------------------------------------------------------------ control
    def start(self):
        os.makedirs(self.dir, exist_ok=True)
        self._csv = open(os.path.join(self.dir, "objects.csv"), "w", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv)
        self._csv_writer.writerow(["id", "camera", "class", "crop_file", "line_top", "line_bottom", "x_left", "x_right",
                                   "width_px", "height_lines", "width_mm", "length_mm", "area_px", "flags"])
        self.running = True
        self.t_start = time.time()
        for cam in self.cfg.cameras:
            self._fq[cam] = queue.Queue(maxsize=self.cfg.frame_queue)
            seg = StreamSegmenter(min_area_px=20, pad=self.cfg.pad_px, x_range=self.cfg.x_range,
                                  max_object_lines=8000)
            seg.bgm.threshold = self.cfg.threshold
            self._seg[cam] = seg
            for d in (self.c.frames, self.c.objects, self.c.dropped_frames, self.c.queue_overflow):
                d[cam] = 0
            t = threading.Thread(target=self._segment_loop, args=(cam,), name=f"Capture-{cam}", daemon=True)
            self._seg_threads[cam] = t
            t.start()
        for i in range(self.cfg.writer_threads):
            t = threading.Thread(target=self._writer_loop, name=f"CaptureWriter{i}", daemon=True)
            self._writers.append(t)
            t.start()

    def learn_background(self, cam, frames):
        """Call with a few frames of the EMPTY chute (feeder off) before feeding product."""
        if cam in self._seg:
            self._seg[cam].bgm.learn(frames)
            self._learned[cam] = True

    def stop(self, timeout=60):
        """Finish: process all queued frames, flush open objects, write everything, close files."""
        for cam, q in self._fq.items():
            q.put(None)                                   # end marker (blocking: never lost)
        for t in self._seg_threads.values():
            t.join(timeout)
        for _ in self._writers:
            self._wq.put(None)
        for t in self._writers:
            t.join(timeout)
        self.running = False
        if self._csv:
            self._csv.close()
        with open(os.path.join(self.dir, "session.json"), "w", encoding="utf-8") as f:
            cfg = asdict(self.cfg)
            json.dump({"config": cfg, "counters": asdict(self.c), "seconds": round(time.time() - self.t_start, 1),
                       "warnings": self.warnings}, f, indent=2, default=str)
        return self.c

    # ------------------------------------------------------------------ camera side (must be fast)
    def push_frame(self, cam, frame, meta=None):
        """Called from the camera thread. Copies the frame (the SDK buffer is reused) and queues it."""
        if not self.running or cam not in self._fq:
            return False
        try:
            self._fq[cam].put_nowait((frame.copy(), dict(meta or {})))
            return True
        except queue.Full:
            self.c.queue_overflow[cam] += 1
            return False

    # ------------------------------------------------------------------ segmentation thread
    def _segment_loop(self, cam):
        seg = self._seg[cam]
        q = self._fq[cam]
        next_line = 0
        last_ts = None
        frame_ms = None
        while True:
            item = q.get()
            if item is None:
                break
            frame, meta = item
            h = frame.shape[0]
            # dropped-frame detection from the camera's own timestamps (0.1 ms units)
            ts = meta.get("cam_ts")
            if ts is not None and last_ts is not None:
                dt = ((ts - last_ts) % 2 ** 32) / 10.0
                if frame_ms is None and dt > 0:
                    frame_ms = dt
                elif frame_ms:
                    k = int(round(dt / frame_ms))
                    if k > 1:
                        self.c.dropped_frames[cam] += k - 1
                        next_line += (k - 1) * h            # the lost lines create a gap in the strip
                    frame_ms = 0.95 * frame_ms + 0.05 * dt if k == 1 else frame_ms
            last_ts = ts
            if "first_line" in meta:
                next_line = int(meta["first_line"])
            self.c.frames[cam] += 1
            if self.cfg.save_strips:
                self._add_strip(cam, frame, next_line)
            for obj in seg.push(frame, first_line=next_line):
                self._emit(cam, obj)
            next_line += h
        for obj in seg.flush():
            self._emit(cam, obj)
        if self.cfg.save_strips:
            self._flush_strip(cam)

    def _emit(self, cam, obj):
        cfg = self.cfg
        width_mm = obj.width_px * cfg.mm_per_px
        length_mm = obj.height_lines * cfg.mm_per_line if cfg.mm_per_line else 0.0
        size_mm = max(width_mm, length_mm)
        if size_mm < cfg.min_size_mm or size_mm > cfg.max_size_mm:
            self.c.rejected_size += 1
            return
        self.c.objects[cam] += 1
        crop = obj.crop
        if cfg.mm_per_line and cfg.mm_per_px:
            aspect = cfg.mm_per_line / cfg.mm_per_px          # stretch along the flow -> square pixels
            if abs(aspect - 1.0) > 0.02:
                crop = cv2.resize(crop, (crop.shape[1], max(1, int(round(crop.shape[0] * aspect)))),
                                  interpolation=cv2.INTER_LINEAR)
        label = cfg.class_label or "UNLABELLED"
        name = f"{cam}_L{obj.line_top:09d}_{obj.obj_id:06d}.png"
        rel = os.path.join("crops", label, name)
        if cfg.save_crops:
            self._queue_write(os.path.join(self.dir, rel), square_224(crop, cfg.crop_size), ".png")
        if cfg.save_raw:
            self._queue_write(os.path.join(self.dir, "raw", label, name), crop, ".png")
        with self._csv_lock:
            self._csv_writer.writerow([obj.obj_id, cam, label, rel.replace("\\", "/"), obj.line_top, obj.line_bottom,
                                       obj.x_left, obj.x_right, obj.width_px, obj.height_lines,
                                       round(width_mm, 2), round(length_mm, 2), obj.area_px, "|".join(obj.flags)])

    # ------------------------------------------------------------------ strips
    def _add_strip(self, cam, frame, first_line):
        s = self._strip.get(cam)
        if s and first_line != s["next"]:                     # gap -> close the current strip
            self._flush_strip(cam)
            s = None
        if s is None:
            s = {"parts": [], "first": first_line, "next": first_line, "rows": 0}
            self._strip[cam] = s
        s["parts"].append(frame)
        s["rows"] += frame.shape[0]
        s["next"] = first_line + frame.shape[0]
        if s["rows"] >= self.cfg.strip_lines:
            self._flush_strip(cam)

    def _flush_strip(self, cam):
        s = self._strip.pop(cam, None)
        if not s or not s["parts"]:
            return
        img = np.concatenate(s["parts"], axis=0)
        ext = ".png" if self.cfg.strip_format == "png" else ".jpg"
        path = os.path.join(self.dir, "strips", cam, f"strip_{cam}_L{s['first']:09d}-{s['next'] - 1:09d}{ext}")
        self._queue_write(path, img, ext)

    # ------------------------------------------------------------------ writing
    def _queue_write(self, path, img, ext):
        self._wq.put((path, img, ext))                       # blocks only if the disk is far behind
        n = self._wq.qsize()
        if n > self.c.write_backlog_max:
            self.c.write_backlog_max = n
        if n > 0.5 * self.cfg.write_queue and "DISK TOO SLOW" not in " ".join(self.warnings):
            self.warnings.append("DISK TOO SLOW: write queue more than half full - "
                                 "switch strips off / use JPG / use an NVMe SSD")

    MIN_FREE_GB_STRIPS = 3.0      # below this: strips are switched off (crops continue)
    MIN_FREE_GB_STOP = 1.0        # below this: nothing more is written (never fill the system disk)

    def _check_space(self):
        import shutil
        try:
            free_gb = shutil.disk_usage(self.dir).free / 1e9
        except Exception:
            return True
        if free_gb < self.MIN_FREE_GB_STOP:
            if "DISK FULL" not in " ".join(self.warnings):
                self.warnings.append(f"DISK FULL: only {free_gb:.1f} GB free - writing stopped")
            return False
        if free_gb < self.MIN_FREE_GB_STRIPS and self.cfg.save_strips:
            self.cfg.save_strips = False
            self.warnings.append(f"LOW DISK SPACE ({free_gb:.1f} GB): strip images switched off, crops continue")
        return True

    def _writer_loop(self):
        params = {".jpg": [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpg_quality], ".png": [cv2.IMWRITE_PNG_COMPRESSION, 1]}
        n = 0
        space_ok = True
        while True:
            item = self._wq.get()
            if item is None:
                break
            path, img, ext = item
            n += 1
            if n % 50 == 1:
                space_ok = self._check_space()
            if not space_ok:
                self.c.write_errors += 1
                continue
            try:
                ok, buf = cv2.imencode(ext, img, params[ext])
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(buf.tobytes())
                self.c.files_written += 1
                self.c.bytes_written += len(buf)
            except Exception:
                self.c.write_errors += 1

    # ------------------------------------------------------------------ status
    def status(self):
        el = max(1e-6, time.time() - (self.t_start or time.time()))
        return {
            "objects": dict(self.c.objects), "total_objects": sum(self.c.objects.values()),
            "frames": dict(self.c.frames), "dropped_frames": dict(self.c.dropped_frames),
            "queue_overflow": dict(self.c.queue_overflow),
            "frame_queue": {c: q.qsize() for c, q in self._fq.items()},
            "write_backlog": self._wq.qsize(), "files": self.c.files_written,
            "MB_written": round(self.c.bytes_written / 1e6, 1), "MB_per_s": round(self.c.bytes_written / 1e6 / el, 1),
            "rejected_size": self.c.rejected_size, "write_errors": self.c.write_errors, "warnings": list(self.warnings),
        }
