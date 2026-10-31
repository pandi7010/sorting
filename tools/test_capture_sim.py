"""
Phase 4 test: continuous capture with simulated cameras (no hardware).

    python tools\\test_capture_sim.py

Two simulated cameras (FRONT + REAR) each deliver 500 objects, ~half of them crossing a frame boundary,
at REAL camera speed (200-line frames every 20 ms = 10 000 lines/s). Pass = per camera 500 crops written,
0 duplicates, 0 missed, 0 dropped frames, 0 queue overflow, crops 224 x 224, strips cover every line.
Then a second run with one frame missing in the camera timestamps must report exactly 1 dropped frame.
"""
import csv
import os
import shutil
import sys
import tempfile
import threading
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from capture_engine import CaptureConfig, CaptureSession  # noqa: E402
from object_stream import make_synthetic_stream  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures += 1


BG = (160, 90, 30)
H = 200


def camera_thread(sess, cam, frames, skip=None, realtime=True):
    """Simulated camera: one frame every 20 ms, camera timestamps in 0.1 ms."""
    ts = 123456
    t_next = time.perf_counter()
    for i, f in enumerate(frames):
        if skip is not None and i == skip:
            ts += 200                                   # this frame is 'lost' on the network
            continue
        sess.push_frame(cam, f, {"cam_ts": ts % 2 ** 32})
        ts += 200                                       # 20 ms
        if realtime:
            t_next += 0.020
            time.sleep(max(0.0, t_next - time.perf_counter()))


tmp = tempfile.mkdtemp(prefix="lamton_capture_")
streams = {cam: make_synthetic_stream(n_objects=500, frame_h=H, seed=s) for cam, s in (("FRONT", 21), ("REAR", 22))}

cfg = CaptureConfig(product="TEST", class_label="GOOD_CASHEW", cameras=("FRONT", "REAR"), out_root=tmp,
                    min_size_mm=0.5, max_size_mm=50, mm_per_px=0.085, mm_per_line=0.35, save_strips=True,
                    strip_lines=2048, pad_px=6)
sess = CaptureSession(cfg, session_name="run1")
sess.start()
for cam in cfg.cameras:
    sess.learn_background(cam, [np.full((32, 4096, 3), BG, np.uint8)])
t0 = time.perf_counter()
threads = [threading.Thread(target=camera_thread, args=(sess, cam, streams[cam][0])) for cam in cfg.cameras]
for t in threads:
    t.start()
peak_q = 0
while any(t.is_alive() for t in threads):
    st = sess.status()
    peak_q = max([peak_q] + list(st["frame_queue"].values()))
    time.sleep(0.05)
c = sess.stop()
dt = time.perf_counter() - t0
st = sess.status()
print(f"   {sum(c.frames.values())} frames in {dt:.1f} s, peak frame queue {peak_q}, files {c.files_written}, "
      f"{c.bytes_written / 1e6:.1f} MB, write backlog max {c.write_backlog_max}")

rows = list(csv.DictReader(open(os.path.join(sess.dir, "objects.csv"), encoding="utf-8")))
for cam in cfg.cameras:
    mine = [r for r in rows if r["camera"] == cam]
    keys = [(int(r["line_top"]), int(r["line_bottom"]), int(r["x_left"]), int(r["x_right"])) for r in mine]
    truth = set(streams[cam][1])
    check(f"[{cam}] 500 objects in -> {len(mine)} out", len(mine) == 500)
    check(f"[{cam}] 0 duplicates", len(keys) == len(set(keys)), len(keys) - len(set(keys)))
    check(f"[{cam}] 0 missed / 0 wrong", set(keys) == truth, (len(truth - set(keys)), len(set(keys) - truth)))
    check(f"[{cam}] 0 dropped frames", c.dropped_frames[cam] == 0, c.dropped_frames[cam])
    check(f"[{cam}] 0 queue overflow (camera never blocked)", c.queue_overflow[cam] == 0, c.queue_overflow[cam])
crop_dir = os.path.join(sess.dir, "crops", "GOOD_CASHEW")
files = os.listdir(crop_dir)
check("1000 crop files written (ImageFolder layout crops/<CLASS>/)", len(files) == 1000, len(files))
sizes = {cv2.imread(os.path.join(crop_dir, f)).shape for f in files[:50]}
check("crops are 224 x 224 x 3", sizes == {(224, 224, 3)}, sizes)
strip_rows = 0
for cam in cfg.cameras:
    for f in os.listdir(os.path.join(sess.dir, "strips", cam)):
        a, b = f.split("_L")[1].split(".")[0].split("-")
        strip_rows += int(b) - int(a) + 1
total_rows = sum(len(streams[cam][0]) * H for cam in cfg.cameras)
check("strips cover every captured line", strip_rows == total_rows, (strip_rows, total_rows))
check("no write errors", c.write_errors == 0, c.write_errors)

# ---- run 2: one lost frame must be reported
cfg2 = CaptureConfig(product="TEST", class_label="X", cameras=("FRONT",), out_root=tmp, min_size_mm=0.5,
                     max_size_mm=50, save_strips=False)
sess2 = CaptureSession(cfg2, session_name="run2")
sess2.start()
sess2.learn_background("FRONT", [np.full((32, 4096, 3), BG, np.uint8)])
camera_thread(sess2, "FRONT", streams["FRONT"][0][:60], skip=30, realtime=False)
c2 = sess2.stop()
check("lost frame detected from camera timestamps (dropped_frames = 1)", c2.dropped_frames["FRONT"] == 1,
      c2.dropped_frames["FRONT"])
rows2 = list(csv.DictReader(open(os.path.join(sess2.dir, "objects.csv"), encoding="utf-8")))
keys2 = [(r["line_top"], r["x_left"]) for r in rows2]
check("lost frame: still no duplicates", len(keys2) == len(set(keys2)))

shutil.rmtree(tmp, ignore_errors=True)
print("\nALL TESTS PASSED" if failures == 0 else f"\n{failures} TEST(S) FAILED")
sys.exit(1 if failures else 0)
