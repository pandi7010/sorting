"""
capture_timing.py - capture-timed ejection for the ESP_BYTE ejector firmware (esp/esp.ino).

SWITCH: features_config.json -> "capture_timed_eject_enabled" (OFF by default).
When OFF nothing in this file is used and the sorter behaves exactly as before.

WHY (simple):
    The old ESP_BYTE path switches a valve ON as soon as the PC has found a defect and OFF when the
    next frame no longer shows it. The recipe DELAY and PULSE are ignored, and how late the valve opens
    depends on how busy Windows / Python / USB are at that moment (jitter = wrong blast position).

WHAT THIS DOES:
    1. Every frame carries the camera's own timestamp (0.1 ms clock inside the camera). CaptureClock turns
       it into PC time using the SMALLEST (camera -> PC) offset seen recently, so PC/network delay spikes
       do not move the result.
    2. Each defect keeps its first and last ROW in the frame. Row r was scanned at
            t_row(r) = frame_end - (rows_used - 1 - r) * line_period
    3. The valve opens at   t_row(first_row) + DELAY            (DELAY = time from camera line to nozzle)
       and stays open for   (object length in lines) * line_period + PULSE.
    4. TimedValveScheduler (own thread) sends the ON / OFF bytes at those times. Overlapping blasts on
       the same channel are merged (one long blast, no chattering).
    5. If the PC is so late that the whole blast window has already passed, the blast is DROPPED and
       counted as MISSED (it would only hit the wrong grain). If only the start is late, it fires at once
       for the rest of the window and is counted as LATE. The counters tell you if DELAY is too short
       for this PC (= move the nozzles further down or make frames shorter).

Example: line rate 8842 lines/s (0.113 ms/line), 64-line frame, DELAY 22 ms, PULSE 3 ms.
    A defect in rows 40..47 of a frame that ended at t=1000.000 s:
        t_row(40) = 1000.000 - (63-40)*0.000113 = 0.997400 s  ->  valve ON at 1000.0196 s
        blast = 8 lines * 0.113 ms + 3 ms = 3.9 ms
"""

import heapq
import json
import os
import sys
import threading
import time
from collections import deque

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(ROOT, "capture_timing_config.json")

DEFAULTS = {
    "default_line_rate_hz": 8842.0,   # measured with tools/camera_probe.py on 2 Nov 2026 (both cameras)
    "camera_ts_unit_ms": 0.1,         # MindVision frame header uiTimeStamp unit
    "offset_window_frames": 300,      # how many frames the "smallest offset" is taken over
    "late_tolerance_ms": 1.0,         # starting this much late still counts as on time
    "min_pulse_ms": 1.0,
    "max_pulse_ms": 200.0,            # esp.ino watchdog switches a channel off after 250 ms anyway
    "rear_extra_delay_ms": 0.0,       # REAR camera line higher/lower than FRONT line -> extra delay for REAR
    "use_camera_timestamp": True,     # False = use the PC arrival time (more jitter; for testing)
    "stats_print_s": 5.0,             # print ON/LATE/MISSED counters every N seconds (0 = never)
    "spin_ms": 3.0,                   # last N ms before a valve event: busy-wait (Windows sleep is ~1-2 ms)
    "gil_switch_interval_ms": 0.5,    # Python lets another thread run after this long (default 5 ms = valve
                                      # can be 5 ms late while a camera thread runs Python code). 0 = unchanged
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            user = json.load(f)
        for k in DEFAULTS:
            if k in user:
                cfg[k] = user[k]
    except Exception:
        pass
    return cfg


class CaptureClock:
    """Converts camera frame timestamps to PC perf_counter time, per camera ("FRONT" / "REAR")."""

    def __init__(self, cfg=None):
        self.cfg = cfg or load_config()
        self._state = {}
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self._state = {}

    def update(self, source, meta):
        """meta: dict with t_arrive (perf_counter s), cam_ts (raw 32-bit), height, rows_used.
        Returns (frame_end_pc_s, line_period_s)."""
        t_arrive = float(meta.get("t_arrive", time.perf_counter()))
        height = int(meta.get("height") or meta.get("rows_used") or 1)
        cam_ts = meta.get("cam_ts", None)
        unit_s = float(self.cfg["camera_ts_unit_ms"]) / 1000.0
        default_lp = 1.0 / max(1.0, float(self.cfg["default_line_rate_hz"]))

        with self._lock:
            st = self._state.setdefault(source, {"last_raw": None, "unwrapped": 0, "offsets": deque(),
                                                 "line_period": None})
            if not self.cfg.get("use_camera_timestamp", True) or cam_ts in (None, ""):
                return t_arrive, st["line_period"] or default_lp
            raw = int(cam_ts) & 0xFFFFFFFF
            if st["last_raw"] is None:
                st["unwrapped"] = raw
            else:
                d = (raw - st["last_raw"]) & 0xFFFFFFFF
                st["unwrapped"] += d
                dt_s = d * unit_s
                # line period from the camera clock (frame interval / lines), smoothed
                if 0 < dt_s < 1.0 and height > 0:
                    lp = dt_s / height
                    if 0.2 * default_lp < lp < 5.0 * default_lp:
                        st["line_period"] = lp if st["line_period"] is None else 0.9 * st["line_period"] + 0.1 * lp
            st["last_raw"] = raw
            cam_s = st["unwrapped"] * unit_s
            offs = st["offsets"]
            offs.append(t_arrive - cam_s)
            while len(offs) > int(self.cfg["offset_window_frames"]):
                offs.popleft()
            frame_end = cam_s + min(offs)
            return frame_end, st["line_period"] or default_lp


def plan_blasts(channels, row_extents, rows_used, frame_end, line_period, delay_ms, pulse_ms, cfg):
    """Returns [(channel, t_on, t_off)] in perf_counter seconds."""
    out = []
    rows_used = max(1, int(rows_used))
    min_p = float(cfg["min_pulse_ms"]) / 1000.0
    max_p = float(cfg["max_pulse_ms"]) / 1000.0
    for ch in channels:
        r0, r1 = (row_extents or {}).get(ch, (0, rows_used - 1))
        r0 = max(0, min(rows_used - 1, int(r0)))
        r1 = max(r0, min(rows_used - 1, int(r1)))
        t_first = frame_end - (rows_used - 1 - r0) * line_period
        t_on = t_first + delay_ms / 1000.0
        dur = (r1 - r0 + 1) * line_period + pulse_ms / 1000.0
        dur = max(min_p, min(max_p, dur))
        out.append((int(ch), t_on, t_on + dur))
    return out


class TimedValveScheduler(threading.Thread):
    """Sends esp.ino bytes at exact times: ch = ON, ch+100 = OFF. write_fn(bytes) does the serial write."""

    def __init__(self, write_fn, cfg=None, clock=time.perf_counter, log_fn=print):
        super().__init__(daemon=True)
        self.write_fn = write_fn
        self.cfg = cfg or load_config()
        self.clock = clock
        self.log = log_fn
        self._heap = []            # (time, seq, kind, ch)  kind: 1 = ON, 0 = OFF check
        self._seq = 0
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._off_at = {}          # ch -> planned OFF time (merged)
        self._on_hw = set()        # channels ON in hardware
        self.running = True
        self.stats = {"blasts": 0, "on_time": 0, "late": 0, "missed": 0, "merged": 0, "max_late_ms": 0.0}
        self._last_stats_t = clock()

    # ---------------------------------------------------------------- API (camera threads)
    def add(self, blasts):
        now = self.clock()
        tol = float(self.cfg["late_tolerance_ms"]) / 1000.0
        with self._lock:
            for ch, t_on, t_off in blasts:
                if not (1 <= ch <= 63):
                    continue
                self.stats["blasts"] += 1
                if t_off <= now:
                    self.stats["missed"] += 1
                    self.stats["max_late_ms"] = max(self.stats["max_late_ms"], (now - t_on) * 1000.0)
                    continue
                if t_on < now - tol:
                    self.stats["late"] += 1
                    self.stats["max_late_ms"] = max(self.stats["max_late_ms"], (now - t_on) * 1000.0)
                    t_on = now
                else:
                    self.stats["on_time"] += 1
                self._push(t_on, 1, ch, t_off)       # ON event carries its own OFF time
        self._wake.set()

    def clear(self, send_off=True):
        with self._lock:
            chs = sorted(self._on_hw)
            self._heap.clear()
            self._off_at.clear()
            self._on_hw.clear()
        if send_off and chs:
            self.write_fn(bytes([c + 100 for c in chs]))

    def stop(self):
        self.running = False
        self._wake.set()

    def active_channels(self):
        with self._lock:
            return set(self._on_hw)

    # ---------------------------------------------------------------- internals
    def _push(self, t, kind, ch, t_off=None):
        self._seq += 1
        heapq.heappush(self._heap, (t, self._seq, kind, ch, t_off))

    def run(self):
        gi = float(self.cfg.get("gil_switch_interval_ms", 0) or 0)
        if gi > 0:
            sys.setswitchinterval(gi / 1000.0)
        if sys.platform == "win32":
            # Windows sleeps in 15.6 ms steps unless a 1 ms timer is requested (the sorter does this at start;
            # do it here too so the scheduler is accurate on its own - measured 18 ms late without it)
            try:
                import ctypes
                ctypes.windll.winmm.timeBeginPeriod(1)
            except Exception:
                pass
        while self.running:
            to_on, to_off = [], []
            with self._lock:
                now = self.clock()
                while self._heap and self._heap[0][0] <= now:
                    t, _s, kind, ch, t_off = heapq.heappop(self._heap)
                    if kind == 1:
                        if ch in self._on_hw:
                            # still blowing from an earlier blast: extend it (one long blast, no chatter)
                            self.stats["merged"] += 1
                            self._off_at[ch] = max(self._off_at.get(ch, 0.0), t_off)
                        else:
                            self._off_at[ch] = t_off
                            self._on_hw.add(ch)
                            to_on.append(ch)
                        self._push(t_off, 0, ch)
                    else:
                        if ch in self._on_hw and now >= self._off_at.get(ch, 0.0) - 1e-6:
                            self._on_hw.discard(ch)
                            self._off_at.pop(ch, None)
                            to_off.append(ch)
                nxt = self._heap[0][0] if self._heap else None
            if to_on:
                self.write_fn(bytes(sorted(to_on)))
            if to_off:
                self.write_fn(bytes([c + 100 for c in sorted(to_off)]))
            self._maybe_print_stats()
            if nxt is None:
                self._wake.wait(0.01)
                self._wake.clear()
            else:
                wait = nxt - self.clock()
                spin = float(self.cfg.get("spin_ms", 3.0)) / 1000.0
                if wait > spin:
                    self._wake.wait(min(wait - spin, 0.01))   # sleep, wake 'spin' early
                    self._wake.clear()
                # last 'spin' ms: busy-wait for precision

    def _maybe_print_stats(self):
        every = float(self.cfg.get("stats_print_s", 0) or 0)
        if every <= 0:
            return
        now = self.clock()
        if now - self._last_stats_t >= every:
            self._last_stats_t = now
            s = self.stats
            if s["blasts"]:
                self.log(f"[TIMED EJECT] blasts {s['blasts']} | on time {s['on_time']} | LATE {s['late']} | "
                         f"MISSED {s['missed']} | merged {s['merged']} | worst late {s['max_late_ms']:.1f} ms")
