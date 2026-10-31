"""
Phase 3 tests (no hardware).

    python tools\\test_line_sync_sim.py

1. REGRESSION: with all switches OFF, process_frame_for_defects gives exactly the same channels as the
   ORIGINAL code (git baseline f705b02) on random frames for GRAY dark/light, SIZE, HSV and FALLBACK.
2. Row tracking ON: same channels as OFF, plus correct first/last rows of a planted defect.
3. Channel calibration: uniform calibration points reproduce the old channel edges; channels unchanged.
4. D (lines to the nozzle) maths, and frame -> line numbering with a dropped frame and timestamp wrap.
5. Simulated ESP32 v3.0: FIRE at L + D on time -> fired at the right line; too late -> LATE counted.
"""
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.chdir(ROOT)                      # HSV algorithm reads hsv.png from the working folder
import features  # noqa: E402
import channel_map  # noqa: E402
import line_sync  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures += 1


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    os.chdir(ROOT)
    return m


# ---------------------------------------------------------------- load current + ORIGINAL code
git = r"C:\Program Files\Git\cmd\git.exe"
orig_src = subprocess.run([git, "show", "f705b02:LAMTON_LINE_NEW_ESP32.py"], capture_output=True, cwd=ROOT).stdout
tmpdir = tempfile.mkdtemp(prefix="lamton_orig_")
orig_path = os.path.join(tmpdir, "orig_main.py")
with open(orig_path, "wb") as f:
    f.write(orig_src)
cur = load_module("cur_main", os.path.join(ROOT, "LAMTON_LINE_NEW_ESP32.py"))
orig = load_module("orig_main", orig_path)


def fake_self():
    return SimpleNamespace(bg_settings={"camera_thresh": {"FRONT": {"lower": 94, "upper": 248}},
                                        "wave_mode": "BLUE"},
                           program_data={}, current_program="")


CAM = {"start_pixel": 100, "end_pixel": 3981, "num_channels": 63, "wave_mode": "BLUE",
       "total_chutes": 1, "chute_roles": {"1": "PRIMARY"}}
ALGOS = [
    (["GRAY A"], {"GRAY A": {"channel": "GREEN", "mode": "DARK DEFECT", "sens": 40, "purity": 20}}),
    (["GRAY B"], {"GRAY B": {"channel": "RED", "mode": "LIGHT DEFECT", "sens": 60, "purity": 10, "scale": 6}}),
    (["SIZE: BIG"], {"SIZE: BIG": {"small": 1000, "big": 2300, "size_mode": "REJECT BOTH"}}),
    (["HSV COLOR"], {"HSV COLOR": {"x1": 50, "y1": 50, "x2": 200, "y2": 200, "sens": 60}}),
    (["FALLBACK X"], {}),
    (["GRAY A", "HSV COLOR"], {"GRAY A": {"channel": "GREEN", "mode": "DARK DEFECT", "sens": 40, "purity": 20},
                               "HSV COLOR": {"x1": 50, "y1": 50, "x2": 200, "y2": 200, "sens": 60}}),
]


def random_frame(rng, h=200):
    f = np.empty((h, 4096, 3), np.uint8)
    f[:] = (150, 90, 40)                                   # blue-ish background
    f += rng.integers(0, 8, size=f.shape, dtype=np.uint8)
    for _ in range(rng.integers(3, 25)):                   # products and defects
        y, x = rng.integers(0, h - 20), rng.integers(100, 3950)
        hh, ww = rng.integers(3, 40), rng.integers(3, 120)
        f[y:y + hh, x:x + ww] = rng.integers(0, 256, size=3)
    return f


def set_features(**kw):
    cfg = dict(features.DEFAULTS)
    cfg.update(kw)
    features._cache = cfg


# ---------------------------------------------------------------- 1. regression vs original
set_features()
rng = np.random.default_rng(11)
mismatch = 0
total = 0
for algos, settings in ALGOS:
    for i in range(25):
        fr = random_frame(rng)
        a = orig.MainDashboard.process_frame_for_defects(fake_self(), fr, dict(CAM), algos, settings, "FRONT")
        b = cur.MainDashboard.process_frame_for_defects(fake_self(), fr, dict(CAM), algos, settings, "FRONT")
        total += 1
        if a["channels"] != b["channels"] or a["best_class"] != b["best_class"]:
            mismatch += 1
check(f"switches OFF: identical channels to ORIGINAL code ({total} random frames, 6 algorithm sets)",
      mismatch == 0, f"{mismatch} mismatches")

# ---------------------------------------------------------------- 2. row tracking
set_features(row_tracking_enabled=True)
mismatch = 0
for algos, settings in ALGOS:
    for i in range(10):
        fr = random_frame(rng)
        set_features()
        a = cur.MainDashboard.process_frame_for_defects(fake_self(), fr, dict(CAM), algos, settings, "FRONT")
        set_features(row_tracking_enabled=True)
        b = cur.MainDashboard.process_frame_for_defects(fake_self(), fr, dict(CAM), algos, settings, "FRONT")
        if a["channels"] != b["channels"]:
            mismatch += 1
check("row tracking ON does not change which channels fire", mismatch == 0, mismatch)
fr = np.empty((200, 4096, 3), np.uint8)
fr[:] = (220, 220, 220)                                  # bright product/background so only the spot is 'dark'
w = (3981 - 100) / 63
x0 = int(100 + 20 * w + 10)                             # inside channel 21
fr[57:66, x0:x0 + 8] = (10, 10, 10)                     # dark spot rows 57..65
d = cur.MainDashboard.process_frame_for_defects(fake_self(), fr, dict(CAM), *ALGOS[0], "FRONT")
check("planted dark spot -> channel 21", d["channels"] == [21], d["channels"])
check("row extents of the spot = rows 57..65", d.get("row_extents", {}).get(21) == (57, 65), d.get("row_extents"))

# ---------------------------------------------------------------- 3. calibration
pts = {c: 100 + (c - 0.5) * w for c in (1, 16, 32, 48, 63)}
edges, coef = channel_map.boundaries_from_points(pts)
uniform = [100 + k * w for k in range(64)]
check("calibration from uniform points = old channel edges (+/-0.5 px)",
      max(abs(a - b) for a, b in zip(edges, uniform)) < 0.5, round(max(abs(a - b) for a, b in zip(edges, uniform)), 3))
cal_file = os.path.join(tmpdir, "cal.json")
with open(cal_file, "w") as f:
    json.dump({"FRONT": {"boundaries": edges}}, f)
channel_map.CAL_FILE = cal_file
channel_map._cal = None
set_features(channel_calibration_enabled=True)
mismatch = 0
rng2 = np.random.default_rng(3)
for i in range(30):
    fr = random_frame(rng2)
    set_features()
    a = cur.MainDashboard.process_frame_for_defects(fake_self(), fr, dict(CAM), *ALGOS[0], "FRONT")
    set_features(channel_calibration_enabled=True)
    b = cur.MainDashboard.process_frame_for_defects(fake_self(), fr, dict(CAM), *ALGOS[0], "FRONT")
    mismatch += a["channels"] != b["channels"]
check("uniform calibration ON gives the same channels", mismatch == 0, mismatch)
set_features(boundary_margin_px=40)
fr = np.empty((200, 4096, 3), np.uint8)
fr[:] = (220, 220, 220)
edge = int(round(100 + 20 * w))                         # boundary between channel 20 and 21
fr[50:60, edge - 2:edge + 2] = (10, 10, 10)
d = cur.MainDashboard.process_frame_for_defects(fake_self(), fr, dict(CAM), *ALGOS[0], "FRONT")
check("boundary margin: spot on the 20|21 edge fires both", d["channels"] == [20, 21], d["channels"])
set_features()

# ---------------------------------------------------------------- 4. maths
check("D for 55 mm, 3.5 m/s, 15 kHz, 1 ms valve = 221 lines", line_sync.d_lines(55, 3.5, 15000, 1.0) == 221,
      line_sync.d_lines(55, 3.5, 15000, 1.0))
cfg = dict(line_sync.DEFAULT_CONFIG, nozzle_distance_mm=55, speed_mps=3.5, line_rate_hz=15000, rear_offset_mm=3.0)
check("rear offset 3 mm -> D rear smaller", line_sync.compute_d(cfg)[1] < line_sync.compute_d(cfg)[0],
      line_sync.compute_d(cfg))
cfg_bad = {"nozzle_distance_mm": 90}
tracker = line_sync.FrameLineTracker(32, 10000)          # 3.2 ms per frame = 32 units of 0.1 ms
ts = 2 ** 32 - 100                                       # timestamp wraps after a few frames
bases = []
for k in range(10):
    if k == 5:
        continue                                        # dropped frame
    bases.append(tracker.frame_base_line((ts + k * 32) % 2 ** 32))
check("frame -> line numbering survives a dropped frame and timestamp wrap",
      bases == [0, 32, 64, 96, 128, 192, 224, 256, 288] and tracker.dropped == 1, (bases, tracker.dropped))

# ---------------------------------------------------------------- 5. simulated ESP32 v3.0
import ejector_link as el  # noqa: E402

src = open(os.path.join(ROOT, "tools", "test_ejector_link_sim.py"), encoding="utf-8").read()
ns = {"__file__": os.path.join(ROOT, "tools", "test_ejector_link_sim.py"), "__name__": "sim"}
exec(src.split("# ---------------------------------------------------------------- 1. link basics")[0], ns)


class SimEsp32V3(ns["SimEsp32"]):
    """Adds the v3.0 line clock model: line counter = time since CLOCK_START x line rate."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.rate, self.D, self.t0, self.running = 10000, (0, 0), None, False
        self.lead = self.tail = self.late_tol = 2
        self.line_fires, self.late = [], 0

    def now_line(self):
        return int((time.time() - self.t0) * self.rate) if self.running else 0

    def _handle(self, t, seq, p):
        if t == el.T_STATUS_REQ:
            vals = [0] * 12
            self._send(el.T_STATUS, struct.pack("<HB", 0x0300, self.state) + struct.pack("<12I", *vals))
            return
        if t == el.T_CONFIG_LINE:
            rate, mode, fl, df, dr, lead, tail, mn, mx, lt = struct.unpack("<IBHHHBBHHB", p)
            self.rate, self.D, self.lead, self.tail, self.late_tol = rate, (df, dr), lead, tail, lt
            self._ack(seq)
            return
        if t == el.T_CMD and p[0] == el.C_CLOCK_START:
            self.t0, self.running = time.time(), True
            self._ack(seq)
            return
        if t == el.T_FIRE_LINE:
            cam, line, mask, length = struct.unpack("<BIQH", p)
            start = line + self.D[cam] - self.lead
            if self.now_line() - start > self.late_tol:
                self.late += 1
                self._ack(seq, 1)
            else:
                self.line_fires.append((cam, line, mask, length, start))
                self._ack(seq)
            return
        super()._handle(t, seq, p)


sims = {}
el.EjectorLink.serial_factory = staticmethod(lambda port, b: sims.setdefault(port, SimEsp32V3(port, b)))
esp = cur.ESP32Controller(None)
esp.connect_ejector("SIMV3")
check("v3.0 firmware detected (supports line sync)", esp.ejector_link.supports_line_sync, esp.ejector_link.fw_version)

set_features(line_sync_enabled=True)
line_sync.load_config = lambda: dict(line_sync.DEFAULT_CONFIG, camera_trigger_confirmed=True,
                                     nozzle_distance_mm=55, speed_mps=3.5, line_rate_hz=10000, frame_height=32)
ls = line_sync.LineSyncController(esp)
check("line sync waits for START", ls.ready_reason().startswith("WAITING"), ls.ready_reason())
ls.on_start()
time.sleep(0.1)
check("line sync ACTIVE after START", ls.active(), ls.ready_reason())
sim = sims["SIMV3"]
d_front = line_sync.d_lines(55, 3.5, 10000, 1.0)
check("ESP32 got D front from the PC", sim.D[0] == d_front, (sim.D, d_front))
# a frame whose first line is 'now' -> plenty of time
t_now_ts = 1000
meta = {"cam_ts": t_now_ts, "crop_y0": 0, "rows_used": 32, "row_extents": {21: (5, 9)}}
ls.trackers["FRONT"].ts0 = t_now_ts - int(sim.now_line() / 32) * 32   # align tracker with the sim clock
base = ls.trackers["FRONT"].frame_base_line(t_now_ts)
ls.fire_decision("FRONT", {"channels": [21]}, meta)
time.sleep(0.1)
ok_fire = [f for f in sim.line_fires if f[2] == 1 << 20]
check("FIRE_LINE sent with the defect's own line (frame base + row 5)", ok_fire and ok_fire[-1][1] == base + 5,
      (ok_fire[-1][1] if ok_fire else None, base + 5))
check("ESP32 opens the valve at line L + D - lead", ok_fire and ok_fire[-1][4] == base + 5 + d_front - line_sync.DEFAULT_CONFIG['lead_lines'])
# an OLD line (far in the past) -> must be LATE, not fired
esp.ejector_link.fire_line(0, max(0, sim.now_line() - 5000), [7], 5)
time.sleep(0.1)
check("late command is counted as LATE and NOT fired", sim.late == 1 and not any(f[2] == 1 << 6 for f in sim.line_fires),
      sim.late)
check("link counted the LATE ACK", esp.ejector_link.counters["ack_late"] == 1, dict(esp.ejector_link.counters))
ls.on_stop()
esp.disconnect_ejector()
set_features()

print("\nALL TESTS PASSED" if failures == 0 else f"\n{failures} TEST(S) FAILED")
sys.stdout.flush()
os._exit(1 if failures else 0)
