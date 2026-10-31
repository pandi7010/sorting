"""
disk_speed_test.py - is this PC's disk fast enough for continuous capture? (Phase 4)

    python tools\\disk_speed_test.py                       (tests C: project folder and D:\\ if present)
    python tools\\disk_speed_test.py --dirs E:\\capture --size-mb 2000

Measures (writes temporary files, deletes them afterwards):
  1. big sequential write (strip images)       MB/s
  2. many small files (224 x 224 crops)         files/s and MB/s
  3. CPU time to compress one 2048-line strip (JPG / PNG) and one crop (PNG)
Then compares with what capture needs at 10 / 15 / 20 kHz for one and two cameras.
"""

import argparse
import os
import shutil
import sys
import tempfile
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def realistic_strip(lines=2048, width=4096, seed=1):
    """Background + sensor noise + many product-like blobs (compresses like a real strip)."""
    rng = np.random.default_rng(seed)
    img = np.empty((lines, width, 3), np.uint8)
    img[:] = (150, 90, 40)
    img += rng.integers(0, 12, size=img.shape, dtype=np.uint8)
    for _ in range(400):
        y, x = int(rng.integers(0, lines - 80)), int(rng.integers(0, width - 300))
        h, w = int(rng.integers(20, 80)), int(rng.integers(60, 300))
        tex = rng.integers(120, 230, size=(h, w, 3), dtype=np.uint8)
        img[y:y + h, x:x + w] = cv2.GaussianBlur(tex, (5, 5), 0)
    return img


def seq_write(d, size_mb):
    path = os.path.join(d, "seq.bin")
    block = os.urandom(8 * 1024 * 1024)
    n = max(1, size_mb // 8)
    t0 = time.perf_counter()
    with open(path, "wb", buffering=0) as f:
        for _ in range(n):
            f.write(block)
        os.fsync(f.fileno())
    dt = time.perf_counter() - t0
    os.remove(path)
    return n * 8 / dt


def small_files(d, crop_png, count):
    sub = os.path.join(d, "crops")
    os.makedirs(sub, exist_ok=True)
    t0 = time.perf_counter()
    for i in range(count):
        with open(os.path.join(sub, f"c{i:05d}.png"), "wb") as f:
            f.write(crop_png)
    dt = time.perf_counter() - t0
    shutil.rmtree(sub, ignore_errors=True)
    return count / dt, count * len(crop_png) / 1e6 / dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", nargs="*")
    ap.add_argument("--size-mb", type=int, default=1024)
    ap.add_argument("--files", type=int, default=2000)
    args = ap.parse_args()
    dirs = args.dirs or [ROOT] + (["D:\\"] if os.path.exists("D:\\") else [])

    out = []

    def say(s=""):
        print(s)
        out.append(s)

    strip = realistic_strip()
    t0 = time.perf_counter(); ok, jpg = cv2.imencode(".jpg", strip, [cv2.IMWRITE_JPEG_QUALITY, 95]); t_jpg = time.perf_counter() - t0
    t0 = time.perf_counter(); ok, png = cv2.imencode(".png", strip, [cv2.IMWRITE_PNG_COMPRESSION, 1]); t_png = time.perf_counter() - t0
    crop = cv2.resize(strip[100:400, 500:800], (224, 224))
    t0 = time.perf_counter()
    for _ in range(50):
        ok, crop_png = cv2.imencode(".png", crop, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    t_crop = (time.perf_counter() - t0) / 50
    raw_mb = strip.nbytes / 1e6
    say("# Disk speed test")
    say(f"\nStrip 2048 x 4096 RGB = {raw_mb:.1f} MB raw -> JPG95 {len(jpg) / 1e6:.1f} MB ({t_jpg * 1000:.0f} ms), "
        f"PNG {len(png) / 1e6:.1f} MB ({t_png * 1000:.0f} ms); crop 224x224 PNG {len(crop_png) / 1e3:.0f} kB ({t_crop * 1000:.1f} ms)")
    jpg_ratio = len(jpg) / strip.nbytes
    png_ratio = len(png) / strip.nbytes

    results = {}
    for d in dirs:
        try:
            tmp = tempfile.mkdtemp(prefix="lamton_disk_", dir=d)
        except Exception as e:
            say(f"\n{d}: cannot write ({e})")
            continue
        free_gb = shutil.disk_usage(d).free / 1e9
        if free_gb < args.size_mb / 1000 * 2:
            say(f"\n{d}: only {free_gb:.1f} GB free - skipped")
            shutil.rmtree(tmp, ignore_errors=True)
            continue
        try:
            seq = seq_write(tmp, args.size_mb)
            fps, fmbs = small_files(tmp, crop_png.tobytes(), args.files)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        results[d] = (seq, fps, fmbs, free_gb)
        say(f"\n## {d}  (free {free_gb:.0f} GB)")
        say(f"- sequential write: **{seq:.0f} MB/s**")
        say(f"- small crop files: **{fps:.0f} files/s** ({fmbs:.1f} MB/s)")

    say("\n## What capture needs (per second)")
    say("| line rate | cameras | raw | strips as JPG | strips as PNG | JPG CPU (cores) |")
    say("|---|---|---|---|---|---|")
    for rate in (10000, 15000, 20000):
        for cams in (1, 2):
            raw = rate * 4096 * 3 * cams / 1e6
            jpg_cpu = rate * cams / 2048 * t_jpg
            say(f"| {rate} | {cams} | {raw:.0f} MB/s | {raw * jpg_ratio:.0f} MB/s | {raw * png_ratio:.0f} MB/s | {jpg_cpu:.1f} |")
    say("Crops: e.g. 100 cashews/s per camera = 100 files/s, about "
        f"{100 * len(crop_png) / 1e6:.1f} MB/s - small.")
    say("\n## Verdict")
    for d, (seq, fps, fmbs, free) in results.items():
        need_jpg_2cam_15k = 15000 * 4096 * 3 * 2 / 1e6 * jpg_ratio
        need_png_2cam_15k = 15000 * 4096 * 3 * 2 / 1e6 * png_ratio
        verdict = []
        verdict.append(f"JPG strips, 2 cameras, 15 kHz need {need_jpg_2cam_15k:.0f} MB/s -> "
                       + ("OK" if seq > 2 * need_jpg_2cam_15k else "TOO SLOW"))
        verdict.append(f"PNG strips need {need_png_2cam_15k:.0f} MB/s -> " + ("OK" if seq > 1.5 * need_png_2cam_15k else "TOO SLOW"))
        verdict.append(f"crops only (100/s per camera): {'OK' if fps > 400 else 'TOO SLOW'}")
        hours = free * 1000 / max(1e-6, need_jpg_2cam_15k) / 3600
        verdict.append(f"free space lasts ~{hours:.1f} h of JPG strips (2 cameras, 15 kHz)")
        say(f"- **{d}**: " + "; ".join(verdict))
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    path = os.path.join(ROOT, "logs", f"disk_speed_{time.strftime('%Y%m%d_%H%M%S')}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"\nSaved: {path}")


if __name__ == "__main__":
    main()
