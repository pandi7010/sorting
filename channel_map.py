"""
channel_map.py - which camera pixels belong to which valve (Phase 3).

DEFAULT (calibration OFF): exactly the old rule
    channel k covers  start_pixel + k * w  ...  start_pixel + (k+1) * w,   w = (end - start) / 63

CALIBRATED (features_config.json "channel_calibration_enabled": true):
    channel_calibration.json holds 64 pixel boundaries per camera (edge of ch1 .. edge after ch63),
    made with tools/channel_calibration.py from a few measured points (e.g. a wire held in front of
    valves 1, 16, 32, 48 and 63). This corrects lens distortion: outer channels can be a few pixels
    narrower/wider than the centre ones.

BOUNDARY MARGIN (features_config.json "boundary_margin_px", default 0):
    each channel also looks N pixels into its neighbours, so a small spot exactly on the line between
    two channels is seen by both (and SIZE/LIGHT counts are not split in half).
"""

import json
import os

import features

ROOT = os.path.dirname(os.path.abspath(__file__))
CAL_FILE = os.path.join(ROOT, "channel_calibration.json")

_cal = None


def load_calibration(force=False):
    global _cal
    if _cal is not None and not force:
        return _cal
    try:
        with open(CAL_FILE, "r", encoding="utf-8") as f:
            _cal = json.load(f)
    except Exception:
        _cal = {}
    return _cal


def make_bounds_fn(source, start_pixel, pixels_per_channel, num_channels, frame_width):
    """Returns f(v_idx, ch_idx, chute_offset) -> (s_idx, e_idx) for the detection loops."""
    margin = int(features.value("boundary_margin_px", 0) or 0)
    edges = None
    if features.enabled("channel_calibration_enabled"):
        cam = load_calibration().get(source, {})
        e = cam.get("boundaries")
        if isinstance(e, list) and len(e) == num_channels + 1:
            edges = [int(round(x)) for x in e]

    if edges is None and margin == 0:
        def bounds(v_idx, ch_idx, chute_offset):          # identical to the original code
            return (int(chute_offset + ch_idx * pixels_per_channel),
                    int(chute_offset + (ch_idx + 1) * pixels_per_channel))
        return bounds

    def bounds(v_idx, ch_idx, chute_offset):
        if edges is not None and v_idx < num_channels:
            s, e = edges[ch_idx], edges[ch_idx + 1]
        else:
            s = int(chute_offset + ch_idx * pixels_per_channel)
            e = int(chute_offset + (ch_idx + 1) * pixels_per_channel)
        return max(0, s - margin), min(frame_width, e + margin)
    return bounds


def boundaries_from_points(points, num_channels=63):
    """points: {channel_number: pixel_of_valve_centre}. Fits pixel = a + b*c + c2*c^2 (quadratic if
    >= 3 points, else linear) and returns num_channels+1 boundaries at channel edges (c +/- 0.5)."""
    import numpy as np
    chans = np.array(sorted(points), dtype=float)
    pix = np.array([points[c] for c in sorted(points)], dtype=float)
    deg = 2 if len(chans) >= 3 else 1
    coef = np.polyfit(chans, pix, deg)
    edges = np.polyval(coef, np.arange(num_channels + 1) + 0.5)    # edge between ch k and k+1 at k + 0.5
    return [float(x) for x in edges], [float(c) for c in coef]
