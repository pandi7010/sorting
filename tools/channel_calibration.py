"""
channel_calibration.py - make channel_calibration.json (pixel <-> valve map) from a few measurements.

HOW TO MEASURE (per camera, sorter running, feeder OFF, air OFF):
  Hold a thin wire / needle exactly in front of the CENTRE of valve 1, and read its pixel position in
  the background waveform screen. Repeat for valves 16, 32, 48 and 63 (more points = better).
Then:
    python tools\\channel_calibration.py --camera FRONT --points "1:131,16:1052,32:2040,48:3009,63:3950"
    python tools\\channel_calibration.py --show
A quadratic fit through the points corrects lens distortion. Switch it on with
features_config.json "channel_calibration_enabled": true (OFF = old equal-width channels).
"""

import argparse
import datetime
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from channel_map import CAL_FILE, boundaries_from_points  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", choices=["FRONT", "REAR"])
    ap.add_argument("--points", help='"channel:pixel,..." e.g. "1:131,32:2040,63:3950"')
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    cal = {}
    if os.path.exists(CAL_FILE):
        with open(CAL_FILE) as f:
            cal = json.load(f)
    if args.points and args.camera:
        pts = {}
        for item in args.points.split(","):
            c, p = item.split(":")
            pts[int(c)] = float(p)
        if len(pts) < 2:
            sys.exit("Need at least 2 points.")
        edges, coef = boundaries_from_points(pts)
        widths = [edges[i + 1] - edges[i] for i in range(63)]
        if min(widths) <= 0:
            sys.exit("Fit gives non-increasing boundaries - check the points.")
        if os.path.exists(CAL_FILE):
            shutil.copy(CAL_FILE, CAL_FILE + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        cal[args.camera] = {"boundaries": [round(e, 1) for e in edges], "points": pts, "fit": coef,
                            "made": datetime.datetime.now().isoformat(timespec="seconds")}
        with open(CAL_FILE, "w") as f:
            json.dump(cal, f, indent=2)
        print(f"Saved {args.camera}: channel 1 = {edges[0]:.0f}..{edges[1]:.0f} px, channel 63 = "
              f"{edges[62]:.0f}..{edges[63]:.0f} px, width {min(widths):.1f} .. {max(widths):.1f} px")
    if args.show or not args.points:
        for cam, d in cal.items():
            e = d["boundaries"]
            print(f"{cam}: start {e[0]}  end {e[-1]}  points {d.get('points')}")
            print("   ch : " + "  ".join(f"{i + 1}:{e[i]:.0f}-{e[i + 1]:.0f}" for i in range(0, 63, 8)))
        if not cal:
            print("No calibration yet (sorter uses equal-width channels).")


if __name__ == "__main__":
    main()
