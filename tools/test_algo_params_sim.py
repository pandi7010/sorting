"""
tools/test_algo_params_sim.py - do SENSITIVITY / PURITY / SCALE (HSV, GRAY) and SMALL / BIG LIMIT (SIZE) from the
algorithm settings dialog really change what the sorter rejects?  Real MainDashboard.process_frame_for_defects on
simulated 64-line frames. A = switch OFF (today), B = program_settings_fix_enabled ON (4 Nov 2026).

    python tools/test_algo_params_sim.py      -> must print  ALL TESTS PASSED
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
        "chute_roles": {"1": "PRIMARY"}, "wave_mode": "GREEN"}
BGS = {"camera_thresh": {"FRONT": {"lower": 120, "upper": 180, "band_mode": False}},
       "thresh_lower": 120, "thresh_upper": 180, "wave_mode": "GREEN"}
rng = np.random.default_rng(2)

def frame(colour, sizes):
    """grey 150 chute, square objects of the given widths in channels 5, 15, 25, 35, 45"""
    f = np.full((64, 4096, 3), 150, np.float32)
    f += rng.normal(0, 3, f.shape)
    for ch, w in zip((5, 15, 25, 35, 45), sizes):
        x = int(SP + (ch - 0.5) * CW)
        h = max(2, min(60, w))
        f[30 - h // 2:30 - h // 2 + h, x - w // 2:x - w // 2 + w] = colour
    return np.clip(f, 0, 255).astype(np.uint8)

def run(algo, cfg, fr, fix):
    m.features._cache = dict(m.features.DEFAULTS, program_settings_fix_enabled=fix)
    d = NS(bg_settings=dict(BGS), thresholds={}, program_data={}, current_program="RICE")
    return sorted(MD.process_frame_for_defects(d, fr, dict(CAMS), [algo], {algo: dict(cfg)}, "FRONT")["channels"])

DARK = frame((70, 70, 70), (2, 4, 8, 16, 32))
YEL = frame((30, 200, 220), (2, 4, 8, 16, 32))
BIGF = frame((70, 70, 70), (10, 30, 45, 60, 90))
GRAY = {"channel": "GREEN", "mode": "DARK DEFECT", "sens": 50, "scale": 6, "purity": 1}
HSV = {"x1": 50, "y1": 50, "x2": 200, "y2": 200, "hsv_mode": "SORT OUT", "sens": 100, "scale": 6, "purity": 1}

def g(**kw): return dict(GRAY, **kw)
def h(**kw): return dict(HSV, **kw)

print("A. switch OFF (machine today): what the dialog values do in the sorter")
check("GRAY PURITY does nothing (1 vs 60 same)", run("GRAY A", g(purity=1), DARK, False) == run("GRAY A", g(purity=60), DARK, False))
check("HSV PURITY does nothing", run("HSV COLOR", h(purity=1), YEL, False) == run("HSV COLOR", h(purity=60), YEL, False))
check("HSV SENSITIVITY 0 still fires (dialog preview says 0 = off)", len(run("HSV COLOR", h(sens=0), YEL, False)) > 0)
_o = run("SIZE: BIG", {"small": 1000, "big": 2300}, BIGF, False)
check("old SIZE: BIG counts dark pixels inside ONE channel (<= 64 px), not the object (info)", True, str(_o))
check("SCALE works (32 -> small objects ignored)", run("GRAY A", g(scale=32), DARK, False) == [25, 35, 45])

print("B. switch program_settings_fix_enabled ON")
check("GRAY SENSITIVITY 0 = off", run("GRAY A", g(sens=0), DARK, True) == [])
check("GRAY PURITY 30: tiny objects (2, 4 px) ignored", run("GRAY A", g(purity=30), DARK, True) == [25, 35, 45],
      str(run("GRAY A", g(purity=30), DARK, True)))
check("GRAY PURITY 100 = never", run("GRAY A", g(purity=100), DARK, True) == [])
check("HSV SENSITIVITY 0 = off", run("HSV COLOR", h(sens=0), YEL, True) == [])
check("HSV SENSITIVITY 50 finds all 5 yellow objects", run("HSV COLOR", h(sens=50), YEL, True) == [5, 15, 25, 35, 45])
check("HSV PURITY 30: tiny objects ignored", run("HSV COLOR", h(purity=30), YEL, True) == [25, 35, 45])
check("HSV SCALE 16: 2 px object ignored", run("HSV COLOR", h(scale=16), YEL, True) == [15, 25, 35, 45])
b1000 = run("SIZE: BIG", {"small": 1000, "big": 1000}, BIGF, True)
b2300 = run("SIZE: BIG", {"small": 1000, "big": 2300}, BIGF, True)
check("SIZE BIG 2300 (~54 px): only the 60 and 90 px objects", 15 not in b2300 and 25 not in b2300 and 35 in b2300 and 45 in b2300, str(b2300))
check("SIZE BIG 1000 (~36 px): also the 45 px object", 25 in b1000 and 15 not in b1000, str(b1000))
s1000 = run("SIZE: SHORT", {"small": 1000, "big": 2300}, BIGF, True)
check("SIZE SHORT 1000 (~36 px): only the 10 and 30 px objects", s1000 == [5, 15], str(s1000))

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
