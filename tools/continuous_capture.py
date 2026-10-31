"""
continuous_capture.py - Phase 4 capture WITHOUT the sorter program (or as a simulation).

    python tools\\continuous_capture.py --simulate 500                (no hardware: 500 synthetic objects)
    python tools\\continuous_capture.py --product CASHEWS --class GOOD_CASHEW --camera FRONT --seconds 120

LIVE MODE: the sorter program must be CLOSED (one program per camera). Camera settings are only read.
  1. Feeder OFF. The first second learns the empty background ("READY - start the feeder").
  2. Run the feeder (e.g. with relay.py, relay 1) with ONE class of product.
  3. After --seconds (or Ctrl+C) everything is written; the summary shows objects and dropped frames.
For capture while the sorter runs (feeder from the sorter, ejection off), use the CONTINUOUS CAPTURE
button instead (features_config.json "continuous_capture_button": true).
"""

import argparse
import json
import os
import sys
import threading
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from capture_engine import CaptureConfig, CaptureSession  # noqa: E402
from object_stream import make_synthetic_stream  # noqa: E402


def print_status(sess):
    st = sess.status()
    print(f"\r objects {st['total_objects']:6d} | dropped frames {sum(st['dropped_frames'].values())} "
          f"| queue full {sum(st['queue_overflow'].values())} | backlog {st['write_backlog']:4d} "
          f"| {st['MB_written']:.0f} MB {st['MB_per_s']:.1f} MB/s   ", end="", flush=True)
    for w in st["warnings"]:
        print(f"\n !! {w}")


def run_simulation(n, args):
    frames, truth = make_synthetic_stream(n_objects=n, frame_h=200, seed=4)
    cfg = CaptureConfig(product="SIM", class_label=args.class_label, cameras=("FRONT",), out_root=args.out,
                        min_size_mm=0.5, max_size_mm=50, mm_per_line=0.35)
    sess = CaptureSession(cfg)
    sess.start()
    sess.learn_background("FRONT", [np.full((32, 4096, 3), (160, 90, 30), np.uint8)])
    ts = 0
    for f in frames:
        sess.push_frame("FRONT", f, {"cam_ts": ts})
        ts += 200
        time.sleep(0.02)
        print_status(sess)
    c = sess.stop()
    print(f"\nSIMULATION: {n} objects in -> {c.objects['FRONT']} crops out, dropped frames {c.dropped_frames['FRONT']}, "
          f"queue overflow {c.queue_overflow['FRONT']}\nFolder: {sess.dir}")


def run_live(args):
    import mvsdk
    roles = {}
    with open(os.path.join(ROOT, "camera_config.json")) as f:
        cam_cfg = json.load(f)
    for role, d in cam_cfg.items():
        roles[str(d.get("sn", "")).strip()] = role
    want = ("FRONT", "REAR") if args.camera == "BOTH" else (args.camera,)
    cams = {}
    for dev in mvsdk.CameraEnumerateDevice():
        role = roles.get(dev.GetSn(), dev.GetSn())
        if role in want:
            h = mvsdk.CameraInit(dev, -1, -1)        # camera's own saved settings, nothing changed
            cap = mvsdk.CameraGetCapability(h)
            buf = mvsdk.CameraAlignMalloc(cap.sResolutionRange.iWidthMax * cap.sResolutionRange.iHeightMax * 3, 16)
            cams[role] = (h, buf)
    if not cams:
        sys.exit("No cameras found - is the sorter program closed?")
    c0 = cam_cfg.get(want[0], {})
    cfg = CaptureConfig.from_preset(args.product, class_label=args.class_label, cameras=tuple(cams), out_root=args.out,
                                    save_strips=not args.no_strips, x_range=(int(c0.get("start_pixel", 0)),
                                                                             int(c0.get("end_pixel", 4096))),
                                    mm_per_line=args.speed * 1000.0 / args.line_rate if args.line_rate else 0.0)
    sess = CaptureSession(cfg)
    sess.start()
    stop = threading.Event()
    bg = {r: [] for r in cams}
    t_bg_end = time.time() + 1.0

    def grab(role, h, buf):
        import cv2
        mvsdk.CameraPlay(h)
        learned = False
        while not stop.is_set():
            try:
                raw, head = mvsdk.CameraGetImageBuffer(h, 200)
            except mvsdk.CameraException:
                continue
            mvsdk.CameraImageProcessEx(h, raw, buf, head, mvsdk.CAMERA_MEDIA_TYPE_BGR8, 0)
            mvsdk.CameraReleaseImageBuffer(h, raw)
            n = head.iWidth * head.iHeight * 3
            frame = np.frombuffer((mvsdk.c_ubyte * n).from_address(buf), np.uint8).reshape(head.iHeight, head.iWidth, 3)
            if role == "REAR":
                frame = cv2.flip(frame, 1)
            if time.time() < t_bg_end:
                bg[role].append(frame.copy())
                continue
            if not learned:
                sess.learn_background(role, bg[role] or [frame.copy()])
                learned = True
            sess.push_frame(role, frame, {"cam_ts": head.uiTimeStamp})

    threads = [threading.Thread(target=grab, args=(r, h, b), daemon=True) for r, (h, b) in cams.items()]
    for t in threads:
        t.start()
    time.sleep(1.2)
    print("READY - start the feeder now")
    t_end = time.time() + args.seconds
    try:
        while time.time() < t_end:
            print_status(sess)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    stop.set()
    for t in threads:
        t.join(2)
    for h, buf in cams.values():
        mvsdk.CameraUnInit(h)
        mvsdk.CameraAlignFree(buf)
    c = sess.stop()
    print(f"\nDONE: objects {c.objects}, dropped frames {c.dropped_frames}, queue overflow {c.queue_overflow}, "
          f"files {c.files_written}\nFolder: {sess.dir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", type=int, metavar="N")
    ap.add_argument("--product", default="CASHEWS")
    ap.add_argument("--class", dest="class_label", default="UNLABELLED")
    ap.add_argument("--camera", choices=["FRONT", "REAR", "BOTH"], default="FRONT")
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--speed", type=float, default=3.5, help="m/s, for the true crop shape")
    ap.add_argument("--line-rate", type=float, default=0, help="lines/s (from the camera probe); 0 = no shape correction")
    ap.add_argument("--no-strips", action="store_true")
    ap.add_argument("--out", default=os.path.join(ROOT, "dataset_capture", "continuous"))
    args = ap.parse_args()
    if args.simulate:
        run_simulation(args.simulate, args)
    else:
        run_live(args)


if __name__ == "__main__":
    main()
