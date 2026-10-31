"""
ball_speed.py - measure product speed with a 10 mm ball (Phase 2).

IDEA (simple example)
  A line-scan camera takes one line every T microseconds. A ball of diameter D falling at
  speed v passes the scan line in D / v seconds, so it appears  D / (v * T)  lines tall.
  Example: D = 10 mm, v = 3.5 m/s, T = 100 us  ->  10 / (3500 * 0.0001) = 28.6 lines.
  We measure the height in lines and solve for v:   v = D / (height_lines * T)
  (small correction: exposure adds  exposure / T  lines of motion blur, subtracted below).

HOW TO USE ON THE MACHINE  (sorter program CLOSED, feeder OFF, air OFF)
    python tools\\ball_speed.py --seconds 60
  1. Wait for "READY - drop balls now" (the first second learns the empty background).
  2. Drop a 10 mm ball through the chute, many times (20-50 drops), across the width.
  3. Result: speed per drop for FRONT and REAR, mean, variation, saved to logs/ball_speed_<time>.json
     (tools/latency_report.py uses this file).
  Camera settings are only READ, never changed.

OTHER MODES
    python tools\\ball_speed.py --simulate              (no hardware: synthetic balls, checks the maths)
    python tools\\ball_speed.py --images strip.png --line-period-us 100 --exposure-us 100
"""

import argparse
import glob
import json
import os
import statistics
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from object_stream import StreamSegmenter  # noqa: E402

MM_PER_PX = 330.0 / 3881.0      # across the chute (FRONT start..end pixel); refined by --mm-per-px


def ball_speed_from_object(obj, ball_mm, line_period_us, exposure_us):
    """Speed (m/s) from one detected ball."""
    blur_lines = (exposure_us / line_period_us) if exposure_us else 0.0
    lines = obj.height_lines - blur_lines
    if lines <= 0:
        return None
    return (ball_mm / 1000.0) / (lines * line_period_us * 1e-6)


def analyse(objects, ball_mm, line_period_us, exposure_us, mm_per_px, label, out):
    expect_w = ball_mm / mm_per_px
    speeds, rows = [], []
    for o in objects:
        if "too_long" in o.flags or "partial_start" in o.flags:
            continue
        if not (0.6 * expect_w <= o.width_px <= 1.4 * expect_w):
            continue                                   # not a 10 mm ball (dust, hand, ...)
        v = ball_speed_from_object(o, ball_mm, line_period_us, exposure_us)
        if v is None:
            continue
        speeds.append(v)
        rows.append({"line_top": o.line_top, "height_lines": o.height_lines, "width_px": o.width_px,
                     "width_mm": round(o.width_px * mm_per_px, 2), "x_center_px": round(o.cx, 1),
                     "speed_mps": round(v, 4)})
    res = {"camera": label, "n_balls": len(speeds), "line_period_us": line_period_us,
           "exposure_us": exposure_us, "ball_mm": ball_mm, "balls": rows}
    out(f"\n[{label}] balls measured: {len(speeds)}   (line period {line_period_us:.2f} us, exposure {exposure_us} us)")
    if speeds:
        mean = statistics.fmean(speeds)
        sd = statistics.pstdev(speeds) if len(speeds) > 1 else 0.0
        res.update({"mean_mps": round(mean, 4), "std_mps": round(sd, 4), "min_mps": round(min(speeds), 4),
                    "max_mps": round(max(speeds), 4), "cv_percent": round(100 * sd / mean, 2)})
        out(f"   speed mean {mean:.3f} m/s   std {sd:.3f}   min {min(speeds):.3f}   max {max(speeds):.3f}   "
            f"variation {100 * sd / mean:.1f} %")
        out(f"   ball width check: mean {statistics.fmean(r['width_mm'] for r in rows):.2f} mm "
            f"(should be ~{ball_mm} mm; if not, the mm/px value is wrong)")
        out(f"   along-flow resolution: {mean * line_period_us / 1000:.3f} mm per line "
            f"(across: {mm_per_px:.3f} mm per pixel)")
        mean_h = statistics.fmean(r["height_lines"] for r in rows)
        res["per_drop_resolution_percent"] = round(100.0 / mean_h, 2)
        out(f"   note: each ball is ~{mean_h:.0f} lines tall, so one drop is accurate to about "
            f"+/-{100.0 / mean_h:.1f} % (the mean of many drops is much more accurate; "
            f"the 'variation' above includes this +/-1 line effect)")
    return res


# ---------------------------------------------------------------------------- live cameras (read-only)
def run_live(args, out):
    import mvsdk
    roles = {}
    try:
        with open(os.path.join(ROOT, "camera_config.json")) as f:
            for role, d in json.load(f).items():
                roles[str(d.get("sn", "")).strip()] = role
    except Exception:
        pass
    devs = mvsdk.CameraEnumerateDevice()
    if not devs:
        out("No cameras found. Is the sorter program closed? Are the cameras powered and on the network?")
        return []
    results = []
    cams = []
    for dev in devs:
        sn = dev.GetSn()
        role = roles.get(sn, sn)
        if args.camera != "BOTH" and role != args.camera:
            continue
        h = mvsdk.CameraInit(dev, -1, -1)          # loads the camera's saved settings; nothing is changed
        cap = mvsdk.CameraGetCapability(h)
        buf_size = cap.sResolutionRange.iWidthMax * cap.sResolutionRange.iHeightMax * 3
        buf = mvsdk.CameraAlignMalloc(buf_size, 16)
        exposure = mvsdk.CameraGetExposureTime(h)
        cams.append({"h": h, "role": role, "buf": buf, "exposure": exposure, "frames": [], "ts": []})
        mvsdk.CameraPlay(h)
    out(f"Recording {args.seconds:.0f} s from {[c['role'] for c in cams]}. First 1 s = background (no balls!).")
    t_end = time.time() + args.seconds
    ready_said = False
    try:
        while time.time() < t_end:
            if not ready_said and time.time() > t_end - args.seconds + 1.0:
                out("READY - drop balls now")
                ready_said = True
            for c in cams:
                try:
                    raw, head = mvsdk.CameraGetImageBuffer(c["h"], 50)
                except mvsdk.CameraException:
                    continue
                # convert to BGR for THIS call only (does not change the camera's settings)
                mvsdk.CameraImageProcessEx(c["h"], raw, c["buf"], head, mvsdk.CAMERA_MEDIA_TYPE_BGR8, 0)
                mvsdk.CameraReleaseImageBuffer(c["h"], raw)
                n = head.iWidth * head.iHeight * 3
                arr = np.frombuffer((mvsdk.c_ubyte * n).from_address(c["buf"]), np.uint8)
                c["frames"].append(arr.reshape(head.iHeight, head.iWidth, 3).copy())
                c["ts"].append(head.uiTimeStamp)
    finally:
        for c in cams:
            mvsdk.CameraUnInit(c["h"])
            mvsdk.CameraAlignFree(c["buf"])
    for c in cams:
        frames = c["frames"]
        if len(frames) < 10:
            out(f"[{c['role']}] too few frames ({len(frames)})")
            continue
        height = frames[0].shape[0]
        dts = np.diff(np.array(c["ts"], dtype=np.int64) & 0xFFFFFFFF) / 10.0  # ms
        dts = dts[dts > 0]
        line_period_us = float(np.median(dts)) * 1000.0 / height
        if c["role"] == "REAR":
            frames = [cv2.flip(f, 1) for f in frames]
        n_bg = max(1, int(1.0 / (np.median(dts) / 1000.0)))
        objs = segment(frames, frames[:n_bg], args)
        results.append(analyse(objs, args.ball_mm, line_period_us, c["exposure"], args.mm_per_px, c["role"], out))
    return results


def segment(frames, bg_frames, args):
    seg = StreamSegmenter(min_area_px=50, pad=4, max_object_lines=20000)
    seg.bgm.threshold = args.threshold
    seg.bgm.learn(bg_frames)
    objs = []
    line = 0
    for f in frames:
        objs += seg.push(f, first_line=line)
        line += f.shape[0]
    return objs + seg.flush()


# ---------------------------------------------------------------------------- simulation
def make_ball_frames(speed_mps, speed_sd, n_balls, line_period_us, exposure_us, ball_mm, mm_per_px,
                     frame_h=200, width=4096, seed=3):
    rng = np.random.default_rng(seed)
    strip_lines = n_balls * 400 + 1000
    strip = np.full((strip_lines, width, 3), (170, 100, 40), np.uint8)
    true_speeds = []
    line = 300
    r_px = ball_mm / mm_per_px / 2
    for _ in range(n_balls):
        v = max(0.5, rng.normal(speed_mps, speed_sd))
        true_speeds.append(v)
        h_lines = (ball_mm / 1000.0) / (v * line_period_us * 1e-6) + exposure_us / line_period_us
        cx = rng.uniform(300, width - 300)
        cy = line + h_lines / 2
        yy, xx = np.ogrid[int(line):int(line + h_lines) + 2, int(cx - r_px) - 1:int(cx + r_px) + 2]
        inside = ((yy + 0.5 - cy) / (h_lines / 2)) ** 2 + ((xx + 0.5 - cx) / r_px) ** 2 <= 1.0
        strip[int(line):int(line + h_lines) + 2, int(cx - r_px) - 1:int(cx + r_px) + 2][inside] = (40, 40, 40)
        line += h_lines + rng.uniform(80, 300)
    total = (int(line) // frame_h + 1) * frame_h
    frames = [strip[i:i + frame_h] for i in range(0, total, frame_h)]
    return frames, true_speeds


def run_simulation(args, out):
    out("SIMULATION: synthetic 10 mm balls, speed 3.5 +/- 0.15 m/s, line period 100 us, exposure 60 us")
    frames, truth = make_ball_frames(3.5, 0.15, 40, 100.0, 60.0, args.ball_mm, args.mm_per_px)
    bg = [np.full((32, 4096, 3), (170, 100, 40), np.uint8)]
    objs = segment(frames, bg, args)
    res = analyse(objs, args.ball_mm, 100.0, 60.0, args.mm_per_px, "SIM", out)
    out(f"   TRUE mean {statistics.fmean(truth):.3f} m/s, std {statistics.pstdev(truth):.3f}")
    res["true_mean_mps"] = round(statistics.fmean(truth), 4)
    res["true_std_mps"] = round(statistics.pstdev(truth), 4)
    return [res]


def main():
    ap = argparse.ArgumentParser(description="Product speed from a dropped 10 mm ball")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--camera", choices=["FRONT", "REAR", "BOTH"], default="BOTH")
    ap.add_argument("--ball-mm", type=float, default=10.0)
    ap.add_argument("--mm-per-px", type=float, default=MM_PER_PX)
    ap.add_argument("--threshold", type=int, default=40, help="colour difference from background")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--images", nargs="*", help="saved strip/frame images (offline mode)")
    ap.add_argument("--line-period-us", type=float, help="needed with --images")
    ap.add_argument("--exposure-us", type=float, default=0.0)
    args = ap.parse_args()

    lines = []

    def out(s):
        print(s)
        lines.append(s)

    if args.simulate:
        results = run_simulation(args, out)
    elif args.images:
        if not args.line_period_us:
            sys.exit("--line-period-us is required with --images")
        files = sorted(f for p in args.images for f in glob.glob(p))
        frames = [cv2.imread(f) for f in files]
        objs = segment(frames, frames[:1], args)
        results = [analyse(objs, args.ball_mm, args.line_period_us, args.exposure_us, args.mm_per_px, "IMAGES", out)]
    else:
        results = run_live(args, out)

    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    path = os.path.join(ROOT, "logs", f"ball_speed_{time.strftime('%Y%m%d_%H%M%S')}{'_SIM' if args.simulate else ''}.json")
    with open(path, "w") as f:
        json.dump({"results": results, "report": lines}, f, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(f"\nSaved: {path}")
    return results


if __name__ == "__main__":
    main()
