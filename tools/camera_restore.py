"""
camera_restore.py - put a camera back to settings saved by camera_probe.py.

This WRITES to the camera. Use it only to undo a change.

Usage (close the sorter program first):
    python tools\\camera_restore.py camera_backups\\20261016_101500\\FRONT_076011622362.config
    python tools\\camera_restore.py camera_backups\\20261016_101500\\FRONT_076011622362.config --save-in-camera

The serial number in the file name decides which camera is restored.
--save-in-camera also stores the restored settings in the camera's own memory (parameter group 0),
so they survive a power cycle even without this PC.
"""

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mvsdk  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config_file")
    ap.add_argument("--save-in-camera", action="store_true")
    args = ap.parse_args()

    path = os.path.abspath(args.config_file)
    m = re.search(r"_(\w+)\.config$", os.path.basename(path))
    if not os.path.exists(path) or not m:
        sys.exit(f"File not found or name not like ROLE_SERIAL.config: {path}")
    sn = m.group(1)

    dev = next((d for d in mvsdk.CameraEnumerateDevice() if d.GetSn() == sn), None)
    if dev is None:
        sys.exit(f"Camera with serial {sn} not found.")

    h = mvsdk.CameraInit(dev, -1, -1)
    try:
        err = mvsdk.CameraReadParameterFromFile(h, path)
        if err != 0:
            sys.exit(f"Restore failed: {err} {mvsdk.CameraGetErrorString(err)}")
        print(f"Restored settings from {path} to camera {sn}.")
        if args.save_in_camera:
            err = mvsdk.CameraSaveParameter(h, 0)
            print("Saved into camera memory group 0." if err == 0 else f"Save in camera failed: {err}")
    finally:
        mvsdk.CameraUnInit(h)  # SDK also stores these as the 'last used' settings on this PC


if __name__ == "__main__":
    main()
