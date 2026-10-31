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
