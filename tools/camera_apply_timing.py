"""
camera_apply_timing.py - set line rate / exposure / frame height / trigger mode (Phase 3).

THE ONLY TOOL THAT CHANGES THESE CAMERA SETTINGS. It never runs automatically.
  - Without --apply it only shows what it WOULD do (dry run).
  - With --apply it FIRST saves a backup of every camera's settings to
    camera_backups/<date_time>_before_apply/<ROLE>_<SN>.config  (undo with tools/camera_restore.py),
    then changes the settings and reads them back.
Close the sorter program first.

Examples
    python tools\\camera_apply_timing.py --product CASHEWS                      (dry run)
    python tools\\camera_apply_timing.py --recipe "W320 WHOLE - GRADE A" --apply
    python tools\\camera_apply_timing.py --line-rate 15000 --exposure 60 --height 32 --apply
    python tools\\camera_apply_timing.py --trigger LINE --trigger-input 0 --apply   (only after the probe
                                                    showed ENCODER/line trigger support and the driver is wired)

Values come from (first found): command line -> recipe "camera_timing" in recipes.json -> product_presets.json.

EXPOSURE LIMIT: a line-scan camera must finish exposing one line before the next starts:
    max exposure ~ 95 % of the line period.  10 kHz -> 95 us, 15 kHz -> 63 us, 20 kHz -> 47 us.
Shorter exposure = darker image: light must be brighter by (old exposure / new exposure), or use gain (adds noise).
"""

import argparse
import datetime
import json
import os
import sys
from ctypes import byref, c_int

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))


def max_exposure_us(line_rate_hz):
    return round(0.95 * 1e6 / line_rate_hz - 1.0, 1)


def exposure_table(current_exposure_us=100.0):
    rows = []
    for rate in (8000, 10000, 12000, 15000, 18000, 20000):
        mx = max_exposure_us(rate)
        rows.append((rate, round(1e6 / rate, 1), mx, round(current_exposure_us / mx, 2) if mx < current_exposure_us else 1.0))
    return rows


def resolve_values(args):
    vals = {"line_rate_hz": None, "exposure_us": None, "frame_height": None}
    src = {}
    try:
        with open(os.path.join(ROOT, "product_presets.json")) as f:
            presets = json.load(f)["products"]
    except Exception:
        presets = {}
    if args.recipe:
        with open(os.path.join(ROOT, "recipes.json"), encoding="utf-8") as f:
            rec = json.load(f).get("schemes", {}).get(args.recipe)
        if rec is None:
            sys.exit(f"Recipe not found: {args.recipe}")
        ct = rec.get("camera_timing", {})
        for k in vals:
            if ct.get(k) is not None:
                vals[k], src[k] = ct[k], f"recipe '{args.recipe}'"
        prod = ct.get("product") or args.product
    else:
        prod = args.product
    if prod and prod in presets:
        p = presets[prod]
        for k, pk in (("line_rate_hz", "line_rate_hz"), ("frame_height", "frame_height")):
            if vals[k] is None and p.get(pk) is not None:
                vals[k], src[k] = p[pk], f"preset {prod}"
    for k, a in (("line_rate_hz", args.line_rate), ("exposure_us", args.exposure), ("frame_height", args.height)):
        if a is not None:
            vals[k], src[k] = a, "command line"
    if vals["exposure_us"] is None and vals["line_rate_hz"]:
        vals["exposure_us"], src["exposure_us"] = max_exposure_us(vals["line_rate_hz"]), "max for this line rate"
    return vals, src


def main():
    ap = argparse.ArgumentParser(description="Apply line rate / exposure / frame height / trigger (with backup)")
    ap.add_argument("--recipe")
    ap.add_argument("--product", help="RICE, PULSES, DHALL, PLASTICS, ALMONDS, CASHEWS, SEEDS_3MM")
    ap.add_argument("--line-rate", type=int)
    ap.add_argument("--exposure", type=float, help="microseconds")
    ap.add_argument("--height", type=int, help="lines per frame (e.g. 16, 32, 64)")
    ap.add_argument("--trigger", choices=["NONE", "LINE", "FRAME"], default=None,
                    help="LINE = encoder/line trigger (mode 3), FRAME = external trigger per frame (mode 2), "
                         "NONE = free-run (mode 0). Omit to leave the trigger unchanged.")
    ap.add_argument("--trigger-input", type=int, default=0, help="camera input index (see camera probe)")
    ap.add_argument("--camera", choices=["FRONT", "REAR", "BOTH"], default="BOTH")
    ap.add_argument("--apply", action="store_true", help="really change the camera (backup is saved first)")
    ap.add_argument("--no-config-update", action="store_true",
                    help="do not write the new exposure into camera_config.json")
    args = ap.parse_args()

    vals, src = resolve_values(args)
    print("Requested values:")
    for k, v in vals.items():
        print(f"   {k:14s}: {v}   ({src.get(k, 'unchanged')})")
    if vals["line_rate_hz"] and vals["exposure_us"] and vals["exposure_us"] > max_exposure_us(vals["line_rate_hz"]):
        sys.exit(f"Exposure {vals['exposure_us']} us is too long for {vals['line_rate_hz']} lines/s "
                 f"(max {max_exposure_us(vals['line_rate_hz'])} us).")
    print("\nExposure limits (vs FRONT's current 100 us):")
    print("   line rate | line period | max exposure | light needed (x brighter)")
    for rate, per, mx, factor in exposure_table(100.0):
        print(f"   {rate:9d} | {per:8.1f} us | {mx:9.1f} us | {factor:.2f}x")
    if args.trigger:
        print(f"\nTrigger: {args.trigger} on camera input {args.trigger_input}")
    if not args.apply:
        print("\nDRY RUN - nothing changed. Add --apply to change the cameras (a backup is saved first).")
        return

    import mvsdk
    roles = {}
    with open(os.path.join(ROOT, "camera_config.json")) as f:
        cam_cfg = json.load(f)
    for role, d in cam_cfg.items():
        roles[str(d.get("sn", "")).strip()] = role
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(ROOT, "camera_backups", f"{stamp}_before_apply")
    os.makedirs(backup_dir, exist_ok=True)
    devs = mvsdk.CameraEnumerateDevice()
    if not devs:
        sys.exit("No cameras found (sorter closed? cameras powered?)")
    changed_roles = []
    for dev in devs:
        sn = dev.GetSn()
        role = roles.get(sn, sn)
        if args.camera != "BOTH" and role != args.camera:
            continue
        h = mvsdk.CameraInit(dev, -1, -1)
        try:
            # ---- 1. BACKUP FIRST ----
            bfile = os.path.join(backup_dir, f"{role}_{sn}.config")
            err = mvsdk.CameraSaveParameterToFile(h, bfile)
            if err != 0:
                print(f"[{role}] BACKUP FAILED ({err}) - NOTHING CHANGED on this camera.")
                continue
            print(f"\n[{role}] backup saved: {bfile}")
            # ---- 2. CHANGE ----
            if vals["exposure_us"]:
                mvsdk.CameraSetExposureTime(h, float(vals["exposure_us"]))
            if vals["line_rate_hz"]:
                print(f"[{role}] line rate -> {vals['line_rate_hz']}: err {mvsdk._sdk.CameraSetFrameRate(h, int(vals['line_rate_hz']))}")
            if vals["frame_height"]:
                res = mvsdk.CameraGetImageResolution(h)
                err = mvsdk.CameraSetImageResolutionEx(h, 0xFF, 0, 0, res.iHOffsetFOV, 0, res.iWidth,
                                                       int(vals["frame_height"]), 0, 0)
                print(f"[{role}] frame height -> {vals['frame_height']}: err {err}")
            if args.trigger == "NONE":
                mvsdk.CameraSetTriggerMode(h, 0)
            elif args.trigger == "FRAME":
                mvsdk.CameraSetInPutIOMode(h, args.trigger_input, mvsdk.IOMODE_TRIG_INPUT)
                mvsdk.CameraSetTriggerMode(h, 2)
                mvsdk.CameraSetTriggerCount(h, 1)
                mvsdk.CameraSetExtTrigSignalType(h, mvsdk.EXT_TRIG_LEADING_EDGE)
            elif args.trigger == "LINE":
                print(f"[{role}] input {args.trigger_input} -> ENCODER input: err "
                      f"{mvsdk._sdk.CameraSetInPutIOMode(h, args.trigger_input, 5)}")   # IOMODE_ROTARYENC_INPUT = 5
                print(f"[{role}] encoder freq 1:1: err {mvsdk._sdk.CameraSetRotaryEncFreq(h, 1, 1)}")
                print(f"[{role}] trigger mode 3 (encoder): err {mvsdk._sdk.CameraSetTriggerMode(h, 3)}")
            # ---- 3. READ BACK ----
            rate = c_int()
            mvsdk._sdk.CameraGetFrameRate(h, byref(rate))
            res = mvsdk.CameraGetImageResolution(h)
            print(f"[{role}] NOW: line rate setting {rate.value}, exposure {mvsdk.CameraGetExposureTime(h):.1f} us, "
                  f"frame {res.iWidth} x {res.iHeight}, trigger mode {mvsdk.CameraGetTriggerMode(h)}")
            changed_roles.append(role)
        finally:
            mvsdk.CameraUnInit(h)        # the SDK keeps these as the camera's 'last used' settings on this PC
    if changed_roles and vals["exposure_us"] and not args.no_config_update:
        # the sorter re-applies the exposure from camera_config.json at start-up -> keep them in step
        bak = os.path.join(backup_dir, "camera_config.json")
        with open(bak, "w") as f:
            json.dump(cam_cfg, f, indent=4)
        for role in changed_roles:
            if role in cam_cfg:
                cam_cfg[role]["exposure"] = vals["exposure_us"]
                if vals["frame_height"]:
                    cam_cfg[role]["roi_height"] = int(vals["frame_height"])
        with open(os.path.join(ROOT, "camera_config.json"), "w") as f:
            json.dump(cam_cfg, f, indent=4)
        print(f"\ncamera_config.json updated (old copy: {bak})")
    print(f"\nTo undo: python tools\\camera_restore.py {backup_dir}\\<ROLE>_<SN>.config  "
          f"(and copy {backup_dir}\\camera_config.json back)")


if __name__ == "__main__":
    main()
