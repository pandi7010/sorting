"""
tools/test_detection_fixes_sim.py - switch detection_fixes_enabled (no camera, no ESP32).
Runs the REAL MainDashboard.process_frame_for_defects on synthetic 64-line frames, switch OFF and ON.

    python tools/test_detection_fixes_sim.py        -> must print  ALL TESTS PASSED
"""
import os, sys, time, random, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _stub(names):
    """Only when PyQt5 / pymodbus / pyserial are not installed (e.g. a Linux test box): empty stand-ins."""
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
        m = types.ModuleType(n); m.__getattr__ = lambda a: _Any; m.__path__ = []; sys.modules[n] = m
    return _Any


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
from types import SimpleNamespace as NS
import numpy as np
_cwd = os.getcwd()
spec = importlib.util.spec_from_file_location("lamton_main", os.path.join(ROOT, "LAMTON_LINE_NEW_ESP32.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
os.chdir(_cwd)
MD = m.MainDashboard
fails = []


def check(n, c, i=""):
    print(f"  [{'OK  ' if c else 'FAIL'}] {n} {i}")
    if not c:
        fails.append(n)


def run(frame, algos, aset, fix_on, bg=None):
    m.features._cache = dict(m.features.DEFAULTS, detection_fixes_enabled=fix_on, min_defect_area_px=4)
    d = NS(bg_settings=bg if bg is not None else {}, program_data={})
    cams = {"start_pixel": 19, "end_pixel": 4046, "num_channels": 63, "total_chutes": 1,
            "chute_roles": {"1": "PRIMARY"}, "wave_mode": "BLUE"}
    return MD.process_frame_for_defects(d, frame, cams, algos, aset, "FRONT")["channels"]


H, W = 64, 4096
GRAY = {"GRAY A": {"channel": "GREEN", "mode": "DARK DEFECT", "sens": 50, "scale": 6}}

def bgframe(v=200):
    return np.full((H, W, 3), v, np.uint8)

print("1. single dust / hot pixel")
f = bgframe(); f[10, 1390, :] = 10
check("switch OFF: 1 pixel fires a valve (old behaviour)", run(f, ["GRAY A"], GRAY, False) == [22])
check("switch ON: 1 pixel ignored", run(f, ["GRAY A"], GRAY, True) == [])
f = bgframe(); f[10, 1390:1392, :] = 10; f[11, 1390:1392, :] = 10    # 2x2 = 4 px
check("switch ON: 2x2 defect (4 px) still fires", run(f, ["GRAY A"], GRAY, True) == [22])

print("2. real defect (8 lines x 20 px, ~3 mm grain spot)")
f = bgframe(); f[40:48, 1380:1400, :] = 20
check("switch OFF fires ch22", run(f, ["GRAY A"], GRAY, False) == [22])
check("switch ON fires ch22", run(f, ["GRAY A"], GRAY, True) == [22])

print("3. GRAY uses the chosen colour plane")
# dark-red scene with bright blue (old code switches to BLUE): defect visible only in GREEN
f = np.zeros((H, W, 3), np.uint8); f[:, :, 0] = 150; f[:, :, 1] = 150; f[:, :, 2] = 20     # BGR: B=150 G=150 R=20
f[40:48, 2030:2050, 1] = 10                                                                  # dark in GREEN only
ch = int((2040 - 19) / ((4046 - 19) / 63)) + 1
check("switch OFF: chosen GREEN ignored -> defect missed", run(f, ["GRAY A"], GRAY, False) == [])
check("switch ON: GREEN used -> defect found", run(f, ["GRAY A"], GRAY, True) == [ch], str(run(f, ["GRAY A"], GRAY, True)))

print("4. GRAY background when a lot of product is in view")
# background 120, good grains 100 cover 40 % of the columns, one medium-dark defect 88 (32 below background)
G2 = {"GRAY A": {"channel": "GREEN", "mode": "DARK DEFECT", "sens": 50, "scale": 6}}
bgset = {"thresh_lower": 30}                    # absolute threshold low, so the relative (background) test decides
f = bgframe(120)
for x in range(100, 3900, 120):
    f[5:30, x:x+48, :] = 100
f[40:48, 1380:1400, :] = 88
off = run(f, ["GRAY A"], G2, False, bgset); on = run(f, ["GRAY A"], G2, True, bgset)
check("switch OFF: background pulled down by product -> defect missed (old bug)", off == [], str(off))
check("switch ON: defect found, good grains not rejected", on == [22], str(on))

print("5. SIZE without a camera threshold")
S = {"SIZE": {"small": 1000, "big": 2300, "size_mode": "REJECT BIG"}}
f = bgframe(); f[10:50, 1365:1420, :] = 30      # 55 px wide object in one channel
check("switch OFF: SIZE never fires (threshold 0)", run(f, ["SIZE"], S, False) == [])
check("switch ON: big object found", run(f, ["SIZE"], S, True) == [22], str(run(f, ["SIZE"], S, True)))

print("6. speed")
f = bgframe(); f[40:48, 1380:1400, :] = 20
import time as _t
for on_ in (False, True):
    ts = _t.perf_counter()
    for _ in range(30):
        run(f, ["GRAY A"], GRAY, on_)
    print(f"     switch {'ON ' if on_ else 'OFF'}: {(_t.perf_counter() - ts) / 30 * 1000:.2f} ms per 64-line frame")

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
