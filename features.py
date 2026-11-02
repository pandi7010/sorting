"""
features.py - ON/OFF switches for every feature added after Phase 1.

All switches except ejector_test_panel_button are OFF by default, so the sorter behaves exactly like Phase 1
until you switch something on in  features_config.json  (restart the program after editing).

    {
      "timing_log_enabled": false,         Phase 2: write timing CSV logs to logs/
      "row_tracking_enabled": false,       Phase 3: keep each defect's line (row) position
      "line_sync_enabled": false,          Phase 3: ESP32 fires at line L + D (needs firmware v3.0
                                                    and camera trigger set up - see line_sync_config.json)
      "channel_calibration_enabled": false Phase 3: use channel_calibration.json pixel boundaries
      "boundary_margin_px": 0,             Phase 3: let channel slices overlap by N pixels
      "continuous_capture_button": false,  Phase 4: show the CONTINUOUS CAPTURE button
      "capture_timed_eject_enabled": false ESP_BYTE firmware: valve opens at (defect line time + recipe DELAY)
                                           for (object length + recipe PULSE) - see capture_timing.py
      "detection_fixes_enabled": false     a channel needs >= min_defect_area_px defect pixels (no 1-pixel
                                           triggers); GRAY keeps the chosen colour; GRAY background = median;
                                           SIZE works without a camera threshold
      "min_defect_area_px": 4
      "clean_channel_switch_enabled": false  a channel needs >= channel_min_px pixel columns (as in the
                                           Background Graph window); old channel OFF + new channel ON in one write
      "channel_min_px": 2
      "ejector_test_panel_button": true    show the EJECTOR TEST PANEL button (ON by default -
                                           the user's decision; it only opens a test dialog)
    }
"""

import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(ROOT, "features_config.json")

DEFAULTS = {
    "timing_log_enabled": False,
    "row_tracking_enabled": False,
    "line_sync_enabled": False,
    "channel_calibration_enabled": False,
    "boundary_margin_px": 0,
    "continuous_capture_button": False,
    "eject_diag_enabled": False,           # print per-channel pixel counts once per second (read-only diagnostic)
    "capture_timed_eject_enabled": False,  # 2 Nov 2026: ESP_BYTE valves fire at line scan time + recipe DELAY
    "detection_fixes_enabled": False,      # 2 Nov 2026: min defect area, GRAY channel/background, SIZE threshold
    "min_defect_area_px": 4,               # used by detection_fixes_enabled: defect pixels needed per channel
    "clean_channel_switch_enabled": False, # 2 Nov 2026: >= channel_min_px columns per channel; OFF+ON in one write
    "channel_min_px": 2,                   # used by clean_channel_switch_enabled (Background Graph uses 2)
    "ejector_test_panel_button": True,     # ON by the user's decision (16 Oct 2026)
}

_cache = None


def load(force=False):
    """Returns the feature dict. Missing file or bad JSON -> all defaults (everything OFF)."""
    global _cache
    if _cache is not None and not force:
        return _cache
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            user = json.load(f)
        for k in DEFAULTS:
            if k in user:
                cfg[k] = user[k]
    except Exception:
        pass
    _cache = cfg
    return cfg


def enabled(name):
    return bool(load().get(name, False))


def value(name, default=None):
    return load().get(name, default)
