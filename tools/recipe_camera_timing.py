"""
recipe_camera_timing.py - store line rate / exposure / frame height in a product recipe (Phase 3).

Only WRITES recipes.json (a backup copy is made first). It does NOT touch the cameras;
use tools/camera_apply_timing.py --recipe "<name>" --apply for that.

    python tools\\recipe_camera_timing.py --list
    python tools\\recipe_camera_timing.py --recipe "W320 WHOLE - GRADE A" --product CASHEWS
    python tools\\recipe_camera_timing.py --recipe DHALL --line-rate 20000 --exposure 45 --height 32
"""

import argparse
import datetime
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from camera_apply_timing import max_exposure_us  # noqa: E402

RECIPES = os.path.join(ROOT, "recipes.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--recipe")
    ap.add_argument("--product", help="fill missing values from product_presets.json")
    ap.add_argument("--line-rate", type=int)
    ap.add_argument("--exposure", type=float)
    ap.add_argument("--height", type=int)
    args = ap.parse_args()

    with open(RECIPES, encoding="utf-8") as f:
        data = json.load(f)
    schemes = data.get("schemes", {})
    if args.list or not args.recipe:
        for name, s in schemes.items():
            print(f"{name:28s} camera_timing = {s.get('camera_timing', '(none - camera keeps its own settings)')}")
        return
    if args.recipe not in schemes:
        sys.exit(f"Recipe not found: {args.recipe}")
    ct = dict(schemes[args.recipe].get("camera_timing", {}))
    if args.product:
        with open(os.path.join(ROOT, "product_presets.json")) as f:
            p = json.load(f)["products"].get(args.product)
        if not p:
            sys.exit(f"Unknown product {args.product}")
        ct.setdefault("line_rate_hz", p["line_rate_hz"])
        ct.setdefault("frame_height", p["frame_height"])
        ct["product"] = args.product
    if args.line_rate:
        ct["line_rate_hz"] = args.line_rate
    if args.exposure:
        ct["exposure_us"] = args.exposure
    if args.height:
        ct["frame_height"] = args.height
    if ct.get("line_rate_hz"):
        mx = max_exposure_us(ct["line_rate_hz"])
        if ct.get("exposure_us") is None:
            ct["exposure_us"] = mx
        if ct["exposure_us"] > mx:
            sys.exit(f"Exposure {ct['exposure_us']} us > max {mx} us for {ct['line_rate_hz']} lines/s")
    backup = RECIPES + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy(RECIPES, backup)
    schemes[args.recipe]["camera_timing"] = ct
    with open(RECIPES, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    print(f"{args.recipe}: camera_timing = {ct}\n(backup: {backup})")


if __name__ == "__main__":
    main()
