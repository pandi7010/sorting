"""
line_sync.py - Phase 3: line-synchronous firing (ESP32 fires at line L + D).

FULLY DISABLED unless ALL of these are true:
  1. features_config.json  "line_sync_enabled": true
  2. line_sync_config.json "camera_trigger_confirmed": true   (set by YOU after the camera probe showed
     line/encoder trigger support AND the cameras were set up with tools/camera_apply_timing.py)
  3. the ejector runs firmware v3.0 (binary protocol)
If anything is missing, the sorter uses the Phase 1 path (PC-timed firing) automatically.

SIMPLE EXAMPLE OF THE MATHS
  nozzle 55 mm below the scan line, product 3.5 m/s, 15 000 lines/s, valve 1 ms:
    travel time = 55 / 3500 = 15.71 ms = 235.7 lines
    valve needs 1 ms = 15 lines earlier
    D = 236 - 15 = 221 lines   -> defect seen at line 100 000 => valve opens at line 100 221.
"""

import json
import os
import threading
import time

import features

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(ROOT, "line_sync_config.json")

DEFAULT_CONFIG = {
    "camera_trigger_confirmed": False,   # YOU set this true after camera setup (see PHASE3_GUIDE.md)
    "nozzle_distance_mm": 55.0,          # allowed 35..70
    "speed_mps": 3.5,                    # from tools/ball_speed.py
    "valve_ms": 1.0,
    "rear_offset_mm": 0.0,               # REAR scan line is this much LOWER (+) / higher (-) than FRONT
    "trigger_mode": "LINE",              # LINE (encoder/line trigger) | FRAME (one trigger per frame) | QUAD
    "line_rate_hz": 10000,               # must match the camera setting
    "frame_height": 32,                  # lines per frame, must match the camera setting
    "lead_lines": 3,                     # open this many lines BEFORE the defect
    "tail_lines": 3,                     # and keep open this many lines AFTER it
    "min_pulse_lines": 10,
    "max_pulse_ms": 50.0,
    "late_tolerance_lines": 2,
}

DISTANCE_MIN_MM, DISTANCE_MAX_MM = 35.0, 70.0


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    cfg["nozzle_distance_mm"] = min(DISTANCE_MAX_MM, max(DISTANCE_MIN_MM, float(cfg["nozzle_distance_mm"])))
    return cfg


def d_lines(distance_mm, speed_mps, line_rate_hz, valve_ms):
    """Distance scan line -> nozzle, in camera lines, minus the valve opening time."""
    travel_s = (distance_mm / 1000.0) / speed_mps
    return max(0, int(round(travel_s * line_rate_hz - valve_ms / 1000.0 * line_rate_hz)))


def compute_d(cfg):
    rate = float(cfg["line_rate_hz"])
    d_front = d_lines(cfg["nozzle_distance_mm"], cfg["speed_mps"], rate, cfg["valve_ms"])
    d_rear = d_lines(cfg["nozzle_distance_mm"] - cfg["rear_offset_mm"], cfg["speed_mps"], rate, cfg["valve_ms"])
    return d_front, d_rear


class FrameLineTracker:
    """Gives the global line number of the first row of every frame.
    Line 0 = first line after CLOCK_START. Frames are numbered from the camera timestamps, so a
    dropped frame does not shift the line numbers of the following frames."""

    def __init__(self, frame_height, line_rate_hz):
        self.h = int(frame_height)
        self.rate = float(line_rate_hz)
        self.reset()

    def reset(self):
        self.ts0 = None
        self.last_k = -1
        self.dropped = 0

    def frame_base_line(self, cam_ts_01ms):
        """cam_ts_01ms: camera frame timestamp in 0.1 ms (32-bit, wraps)."""
        frame_ms = self.h / self.rate * 1000.0
        if self.ts0 is None:
            self.ts0 = cam_ts_01ms
            self.last_k = 0
            return 0
        elapsed_ms = ((cam_ts_01ms - self.ts0) % 2 ** 32) / 10.0
        k = int(round(elapsed_ms / frame_ms))
        if k > self.last_k + 1:
            self.dropped += k - self.last_k - 1
        self.last_k = max(self.last_k, k)
        return k * self.h


class LineSyncController:
    """Used by MainDashboard. Does nothing unless active() is True."""

    def __init__(self, esp32_controller):
        self.esp32 = esp32_controller
        self.cfg = load_config()
        self.trackers = {}
        self.running = False
        self.stats = {"sent": 0, "no_rows": 0, "not_ready": 0}
        self.lock = threading.Lock()

    def link(self):
        return getattr(self.esp32, "ejector_link", None)

    def ready_reason(self):
        """Why line sync is (not) active - shown in logs/UI."""
        if not features.enabled("line_sync_enabled"):
            return "OFF (features_config.json line_sync_enabled = false)"
        if not self.cfg.get("camera_trigger_confirmed"):
            return "OFF (line_sync_config.json camera_trigger_confirmed = false)"
        lk = self.link()
        if not (lk and lk.supports_line_sync):
            return "OFF (ejector firmware v3.0 not connected)"
        if not self.running:
            return "WAITING (press START)"
        return "ACTIVE"

    def active(self):
        return self.ready_reason() == "ACTIVE"

    def on_start(self):
        """Called by START. Sends CONFIG_LINE + CLOCK_START only if everything is ready."""
        self.cfg = load_config()
        if self.ready_reason().startswith("OFF"):
            print(f"[LINE SYNC] {self.ready_reason()}")
            return False
        d_front, d_rear = compute_d(self.cfg)
        rate = int(self.cfg["line_rate_hz"])
        max_lines = max(int(self.cfg["min_pulse_lines"]), int(self.cfg["max_pulse_ms"] / 1000.0 * rate))
        lk = self.link()
        lk.configure_line(rate, self.cfg["trigger_mode"], int(self.cfg["frame_height"]), d_front, d_rear,
                          lead=self.cfg["lead_lines"], tail=self.cfg["tail_lines"],
                          min_lines=self.cfg["min_pulse_lines"], max_lines=max_lines,
                          late_tol=self.cfg["late_tolerance_lines"])
        self.trackers = {s: FrameLineTracker(self.cfg["frame_height"], rate) for s in ("FRONT", "REAR")}
        lk.clock_start()
        self.running = True
        print(f"[LINE SYNC] ACTIVE: {rate} lines/s, D front {d_front} / rear {d_rear} lines "
              f"({self.cfg['nozzle_distance_mm']} mm at {self.cfg['speed_mps']} m/s)")
        return True

    def on_stop(self):
        if self.running:
            lk = self.link()
            if lk and lk.supports_line_sync:
                lk.clock_stop()
        self.running = False

    def fire_decision(self, source, decision, meta):
        """Send FIRE_LINE for every channel of this frame's decision.
        Returns True if handled (caller then skips the Phase 1 path)."""
        lk = self.link()
        if not (lk and meta and "cam_ts" in meta):
            self.stats["not_ready"] += 1
            return False
        tracker = self.trackers.get(source)
        if tracker is None:
            return False
        base = tracker.frame_base_line(meta["cam_ts"]) + int(meta.get("crop_y0", 0))
        extents = meta.get("row_extents") or {}
        cam = 0 if source == "FRONT" else 1
        rows_used = int(meta.get("rows_used", self.cfg["frame_height"]))
        # group channels with the same (first, last) rows into one message
        groups = {}
        for ch in decision.get("channels", []):
            ext = extents.get(ch)
            if ext is None:
                self.stats["no_rows"] += 1
                ext = (0, rows_used - 1)          # unknown row -> cover the whole frame
            groups.setdefault(tuple(ext), []).append(ch)
        for (first, last), chs in groups.items():
            lk.fire_line(cam, base + first, chs, last - first + 1)
            self.stats["sent"] += 1
        return True
