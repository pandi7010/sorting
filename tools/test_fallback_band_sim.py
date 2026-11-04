"""
tools/test_fallback_band_sim.py - BAND PASS must also work when the BACKGROUND (GRAPH) switch is off (old FALLBACK
code). Found on the machine 4 Nov 2026: white-only band 45-80 fired ALL 63 valves after the switch went off.

    python tools/test_fallback_band_sim.py      -> must print  ALL TESTS PASSED
"""
import os, sys, types
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

SP, EP = 19, 4046
CW = (EP - SP) / 63
CAMS = {"start_pixel": SP, "end_pixel": EP, "num_channels": 63, "total_chutes": 1,
        "chute_roles": {"1": "PRIMARY"}, "wave_mode": "BLUE"}
rng = np.random.default_rng(3)

def frame(obj_ch=None, blue=65):
    f = np.zeros((64, 4096, 3), np.float32)
    f[:] = (120, 35, 25)                       # machine: blue background ~95-150, green ~35, red ~25
    f += rng.normal(0, 8, f.shape)
    if obj_ch:
        x = int(SP + (obj_ch - 0.5) * CW)
        f[20:40, x - 20:x + 20] = (blue, 55, 30)
    return np.clip(f, 0, 255).astype(np.uint8)

def run(fr, band, switch):
    m.features._cache = dict(m.features.DEFAULTS, background_graph_algo_enabled=switch)
    bg = {"camera_thresh": {"FRONT": {"lower": 45, "upper": 80, "band_mode": band}}, "wave_mode": "BLUE"}
    d = NS(bg_settings=bg, thresholds={}, program_data={}, current_program="RICE")
    return sorted(MD.process_frame_for_defects(d, fr, dict(CAMS), ["BACKGROUND (GRAPH)"], {}, "FRONT")["channels"])

print("White-only band 45-80 on BLUE (machine 4 Nov): BACKGROUND (GRAPH) switch OFF -> old FALLBACK code")
check("switch OFF, empty chute: 0 valves (was ALL 63 - band ignored)", run(frame(), True, False) == [], str(len(run(frame(), True, False))))
check("switch OFF, white flake (blue 65) in ch 30 fires ch 30", 30 in run(frame(30), True, False), str(run(frame(30), True, False)))
check("switch OFF, black flake (blue 10) not fired", 30 not in run(frame(30, blue=10), True, False))
check("switch ON, same results (empty 0)", run(frame(), True, True) == [])
check("switch ON, white flake fires ch 30", 30 in run(frame(30), True, True))
check("band OFF still = old HI/LO behaviour (background 120 > HI 80 -> all fire)", len(run(frame(), False, False)) == 63)

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
