"""
tools/test_particle_speed_sim.py - 18 Nov 2026: PARTICLE SPEED row. Synthetic frames with round particles of a
known size falling at a known speed -> the estimate must come back within 10 %.

    python tools/test_particle_speed_sim.py      -> must print  ALL TESTS PASSED
STILL NEEDS THE REAL MACHINE: real particles (shape!) - check once with 10 mm balls / tools/ball_speed.py.
"""
import os, sys, time, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _stub(names):
    class _Meta(type):
        def __getattr__(cls, n): return _Any
        def __or__(cls, o): return cls
        __ror__ = __or__

    class _Any(metaclass=_Meta):
        def __init__(self, *a, **k): pass
        def __call__(self, *a, **k): return _Any()
        def __getattr__(self, n): return _Any()
        def __or__(self, o): return self
        __ror__ = __or__
        def __bool__(self): return False
    for n in names:
        mo = types.ModuleType(n); mo.__getattr__ = lambda a: _Any; mo.__path__ = []; sys.modules[n] = mo


try:
    import PyQt5.QtWidgets  # noqa: F401
except Exception:
    _stub(["PyQt5", "PyQt5.QtWidgets", "PyQt5.QtCore", "PyQt5.QtGui", "PyQt5.QtSvg"])
    sys.modules["PyQt5.QtCore"].pyqtSignal = lambda *a, **k: None
try:
    import pymodbus.server  # noqa: F401
except Exception:
    _stub(["pymodbus", "pymodbus.server", "pymodbus.simulator", "pymodbus.simulator.simdata",
           "pymodbus.simulator.simdevice", "pymodbus.client", "pymodbus.exceptions", "pymodbus.pdu", "pymodbus.framer"])
try:
    import serial.tools.list_ports  # noqa: F401
except Exception:
    _stub(["serial", "serial.tools", "serial.tools.list_ports"])
    sys.modules["serial"].SerialException = type("SerialException", (Exception,), {})
    sys.modules["serial.tools.list_ports"].comports = lambda: []

import importlib.util
import numpy as np
import cv2
from types import SimpleNamespace as NS
_cwd = os.getcwd()
spec = importlib.util.spec_from_file_location("lamton_main", os.path.join(ROOT, "LAMTON_LINE_NEW_ESP32.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
os.chdir(_cwd)
F = m.features

fails = []
def check(n, c, i=""):
    print(f"  [{'OK  ' if c else 'FAIL'}] {n} {i}")
    if not c:
        fails.append(n)


rng = np.random.default_rng(1)
ROWS, W, PER_MS = 64, 4096, 12.8          # 64 lines per frame, 12.8 ms per frame -> 0.2 ms per line
LINE_S = PER_MS / 1000.0 / ROWS


def frame_with(diam_mm, speed, n=6, length_ratio=1.0):
    f = np.zeros((ROWS, W, 3), np.uint8)
    f[:] = (120, 40, 30)
    f = np.clip(f.astype(np.int16) + rng.integers(-6, 7, f.shape), 0, 255).astype(np.uint8)
    w_px = diam_mm / 0.083
    h_lines = diam_mm * length_ratio / 1000.0 / (speed * LINE_S)
    for i in range(n):
        cx = int(200 + i * 600)
        cv2.ellipse(f, (cx, 32), (int(round(w_px / 2)), int(round(h_lines / 2))), 0, 0, 360, (20, 20, 20), -1)
    return f


def run(diam, speed, ratio=1.0, length_ratio=1.0, frames=80):
    F._cache = dict(F.DEFAULTS, speed_length_to_width=ratio)
    w = NS(period_ms=PER_MS)
    for _ in range(frames):
        m._speed_sample(w, frame_with(diam, speed, length_ratio=length_ratio))
    sp = list(getattr(w, 'm_speeds', []))
    return (float(np.median(sp)) if sp else None), len(sp), w


print("particle speed estimate")
check("switch particle_speed_metric ON by default (user's request)", F.DEFAULTS.get("particle_speed_metric") is True)
for diam, speed in ((6.0, 3.5), (6.0, 2.0), (4.0, 4.5)):
    v, n, _ = run(diam, speed)
    check(f"{diam} mm round particles at {speed} m/s -> {v if v is None else round(v, 2)} m/s (n {n})",
          v is not None and abs(v - speed) / speed < 0.10)
v, n, _ = run(4.0, 3.5, length_ratio=2.5)
check(f"long particles (2.5 x), ratio 1.0 -> too slow ({v:.2f}), as expected", v < 2.0)
v, n, _ = run(4.0, 3.5, ratio=2.5, length_ratio=2.5)
check(f"long particles with speed_length_to_width 2.5 -> {v:.2f} m/s", abs(v - 3.5) / 3.5 < 0.12)
t0 = time.perf_counter()
w = NS(period_ms=PER_MS)
fr = frame_with(6.0, 3.5)
for _ in range(80):
    m._speed_sample(w, fr)
ms = (time.perf_counter() - t0) * 1000.0 / 80
check(f"average cost per frame {ms:.2f} ms (only every 8th frame does work)", ms < 0.5)
t1 = time.perf_counter(); w2 = NS(period_ms=PER_MS, m_spd_n=7); m._speed_sample(w2, fr); one = (time.perf_counter() - t1) * 1000.0
check(f"one working call {one:.1f} ms (must stay well below the 12.8 ms frame time)", one < 4.0)
check("empty chute -> no samples", (lambda w_: ([m._speed_sample(w_, np.full((64, 4096, 3), 120, np.uint8)) for _ in range(16)],
                                                  len(getattr(w_, 'm_speeds', [])))[1])(NS(period_ms=PER_MS)) == 0)

print("text on the main screen")
F._cache = dict(F.DEFAULTS)
_, _, wf = run(6.0, 3.5)
d = NS(camera_worker_front=wf, camera_worker_rear=None, esp32=None)
d._ej_metrics = lambda: m.collections.Counter()
txt = m.MainDashboard._metric_texts(d)[4]
check("row text 'F 3.5x  R -- m/s  (n …/0)' in green", txt[0].startswith("F 3.") and "R -- m/s" in txt[0]
      and txt[1] == "#00e676", repr(txt))

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
