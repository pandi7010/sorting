"""
timing_log.py - Phase 2 timing measurement (OFF unless features_config.json "timing_log_enabled": true).

Writes 3 CSV files per session into  logs/  (one row per event):
    timing_<stamp>_frames.csv  one row per camera frame
        source, frame_idx, cam_ts_01ms, height, crop_y0, rows_used, t_arrive, t_proc_start, t_proc_end, n_channels
    timing_<stamp>_fires.csv   one row per command sent to the ejector
        source, frame_idx, t_arrive, t_schedule, t_target, t_send, seq, channels, pulse_ms, delay_ms, row_first, row_last
    timing_<stamp>_acks.csv    one row per ESP32 acknowledgement (any message, incl. heartbeats)
        seq, result, esp_us, t_sent, t_rx

All t_* values are PC  time.perf_counter()  seconds. esp_us is the ESP32 micros() clock;
tools/latency_report.py converts it to PC time using the fastest round trips.

Simple example of what you learn: if a frame arrives at t_arrive = 10.000 s, processing ends at
10.012 s and the command is sent at 10.017 s, then processing took 12 ms and waiting + sending 5 ms.
"""

import csv
import os
import queue
import threading
import time

import features

ROOT = os.path.dirname(os.path.abspath(__file__))

FRAME_COLS = ["source", "frame_idx", "cam_ts_01ms", "height", "crop_y0", "rows_used",
              "t_arrive", "t_proc_start", "t_proc_end", "n_channels"]
FIRE_COLS = ["source", "frame_idx", "t_arrive", "t_schedule", "t_target", "t_send", "seq",
             "channels", "pulse_ms", "delay_ms", "row_first", "row_last"]
ACK_COLS = ["seq", "result", "esp_us", "t_sent", "t_rx"]


class TimingLogger:
    def __init__(self, enabled=None, log_dir=None):
        self.enabled = features.enabled("timing_log_enabled") if enabled is None else bool(enabled)
        self.log_dir = log_dir or os.path.join(ROOT, "logs")
        self._q = queue.Queue(maxsize=200000)
        self.dropped = 0
        self.stamp = None
        self._thread = None
        self._running = False
        if self.enabled:
            self.start()

    # ------------------------------------------------------------------ control
    def start(self):
        if self._running:
            return
        os.makedirs(self.log_dir, exist_ok=True)
        self.stamp = time.strftime("%Y%m%d_%H%M%S")
        self.enabled = True
        self._running = True
        self._thread = threading.Thread(target=self._writer, name="TimingLogWriter", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None

    def paths(self):
        base = os.path.join(self.log_dir, f"timing_{self.stamp}")
        return base + "_frames.csv", base + "_fires.csv", base + "_acks.csv"

    # ------------------------------------------------------------------ record (called from hot paths: no I/O)
    def _put(self, kind, row):
        try:
            self._q.put_nowait((kind, row))
        except queue.Full:
            self.dropped += 1

    def frame(self, source, frame_idx, cam_ts, height, crop_y0, rows_used, t_arrive, t_proc_start, t_proc_end,
              n_channels):
        if self.enabled:
            self._put("frames", [source, frame_idx, cam_ts, height, crop_y0, rows_used,
                                 f"{t_arrive:.6f}", f"{t_proc_start:.6f}", f"{t_proc_end:.6f}", n_channels])

    def fire(self, meta, t_schedule, t_target, t_send, seq, channels, pulse_ms, delay_ms):
        if self.enabled:
            m = meta or {}
            self._put("fires", [m.get("source", ""), m.get("frame_idx", ""), f"{m.get('t_arrive', 0.0):.6f}",
                                f"{t_schedule:.6f}", f"{t_target:.6f}", f"{t_send:.6f}",
                                "" if seq is None else seq, " ".join(str(c) for c in channels),
                                pulse_ms, delay_ms, m.get("row_first", ""), m.get("row_last", "")])

    def on_ack(self, seq, result, esp_us, t_rx, t_sent):
        """ejector_link ack listener."""
        if self.enabled and t_sent is not None:
            self._put("acks", [seq, result, esp_us, f"{t_sent:.6f}", f"{t_rx:.6f}"])

    # ------------------------------------------------------------------ writer thread
    def _writer(self):
        files = {}
        writers = {}
        cols = {"frames": FRAME_COLS, "fires": FIRE_COLS, "acks": ACK_COLS}
        for kind, path in zip(("frames", "fires", "acks"), self.paths()):
            files[kind] = open(path, "w", newline="", encoding="utf-8")
            writers[kind] = csv.writer(files[kind])
            writers[kind].writerow(cols[kind])
        try:
            while self._running or not self._q.empty():
                deadline = time.time() + 1.0
                while time.time() < deadline:
                    try:
                        kind, row = self._q.get(timeout=0.2)
                    except queue.Empty:
                        if not self._running:
                            break
                        continue
                    writers[kind].writerow(row)
                for f in files.values():
                    f.flush()
        finally:
            for f in files.values():
                f.close()


_logger = None


def get_logger():
    """Process-wide logger (created on first use; disabled unless the feature switch is on)."""
    global _logger
    if _logger is None:
        _logger = TimingLogger()
    return _logger
