"""
tools/test_program_settings_audit.py - cross-check: does every PROGRAM SETTINGS algorithm do what its
settings dialog says, with the machine's real settings (GREEN source, FRONT LO 0 / HI 75, start 19 / end 4046)?

Runs the REAL MainDashboard.process_frame_for_defects on simulated 64-line frames (no camera, no ESP32):
  EMPTY  = only the background (must fire 0 channels)
  RICE   = good white rice grains (for "eject everything" a product-detecting algorithm must fire them)
  DARK   = rice + one black grain (a dark defect)
Prints a table and a verdict per algorithm. Switches = as on the machine (only background_graph_algo_enabled).

    python tools/test_program_settings_audit.py      -> must print  ALL TESTS PASSED
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


H, SP, EP = 64, 19, 4046
CW = (EP - SP) / 63
BG_LEVEL = (40, 50, 40)          # B, G, R of the empty chute (green below HI 75)
CAMS = {"start_pixel": SP, "end_pixel": EP, "num_channels": 63, "total_chutes": 1,
        "chute_roles": {"1": "PRIMARY"}, "wave_mode": "GREEN"}
BGSET = {"camera_thresh": {"FRONT": {"lower": 0, "upper": 75, "band_mode": False}},
         "thresh_lower": 0, "thresh_upper": 75, "band_mode": False, "wave_mode": "GREEN"}

rng = np.random.default_rng(1)


def frame_empty():
    f = np.zeros((H, 4096, 3), np.float32)
    f[:] = BG_LEVEL
    f += rng.normal(0, 3, f.shape)
    return np.clip(f, 0, 255).astype(np.uint8)


def ch_x(ch):                    # centre pixel of channel ch (1-based)
    return int(SP + (ch - 0.5) * CW)


def add_grain(f, ch, colour, w=40, rows=(10, 50)):
    x = ch_x(ch)
    f[rows[0]:rows[1], x - w // 2:x + w // 2] = colour
    return f


def frame_rice(chs=(5, 20, 40, 60)):
    f = frame_empty()
    for c in chs:
        add_grain(f, c, (200, 205, 205))
    return f


def frame_dark():
    f = frame_rice((5, 20, 40, 60))
    add_grain(f, 30, (20, 20, 20))
    return f


def frame_yellow():
    f = frame_rice((5, 20, 40, 60))
    add_grain(f, 30, (20, 200, 220))       # BGR yellow grain
    return f


def make_dash(feat):
    m.features._cache = dict(m.features.DEFAULTS, **feat)
    d = NS(bg_settings=dict(BGSET), thresholds={"HSV": 85, "CHROME": 75, "SIZE": 90, "DEEP_LEARNING": 95,
                                                 "GRAY_CONTRAST": 50, "GRAY_THRESH": 128},
           program_data={}, current_program="RICE")
    return d


def run(d, algo, cfg, frame):
    dec = MD.process_frame_for_defects(d, frame, dict(CAMS), [algo], {algo: dict(cfg)}, "FRONT")
    return sorted(dec.get("channels", []))


# default settings exactly as each dialog saves them (AlgoSettingsDialog.save_and_close)
ALGOS = {
    "BACKGROUND (GRAPH)": {},
    "GRAY A (DARK)": ("GRAY A", {"channel": "GREEN", "mode": "DARK DEFECT", "sens": "50", "scale": "6", "purity": "1"}),
    "GRAY A (LIGHT)": ("GRAY A", {"channel": "GREEN", "mode": "LIGHT DEFECT", "sens": "50", "scale": "6", "purity": "1"}),
    "GRAY B (DARK)": ("GRAY B", {"channel": "GREEN", "mode": "DARK DEFECT", "sens": "50", "scale": "6", "purity": "1"}),
    "SIZE: BIG": ("SIZE: BIG", {"small": "1000", "big": "2300"}),
    "SIZE: SHORT": ("SIZE: SHORT", {"small": "1000", "big": "2300"}),
    "HSV COLOR (SORT OUT)": ("HSV COLOR", {"x1": "50", "y1": "50", "x2": "200", "y2": "200",
                                           "hsv_mode": "SORT OUT", "sens": "100", "scale": "6", "purity": "1"}),
    "HSV COLOR (SORT IN)": ("HSV COLOR", {"x1": "50", "y1": "50", "x2": "200", "y2": "200",
                                          "hsv_mode": "SORT IN", "sens": "100", "scale": "6", "purity": "1"}),
    "DISCOLOUR A R>G": ("DISCOLOUR A", {"combo": "RED > GREEN", "sens": "75", "scale": "16", "purity": "1"}),
    "DISCOLOUR A B>Y": ("DISCOLOUR A", {"combo": "BLUE > YELLOW", "sens": "75", "scale": "16", "purity": "1"}),
    "DISCOLOUR A YELLOW": ("DISCOLOUR A", {"combo": "YELLOW DISCOLOUR", "sens": "75", "scale": "16", "purity": "1"}),
}

print("\n=== PROGRAM SETTINGS AUDIT (machine settings: GREEN, LO 0 / HI 75) ===")
results = {}
for feat_name, feat in (("MACHINE NOW", {"background_graph_algo_enabled": True}),
                        ("+ detection_fixes + runtime_sliders", {"background_graph_algo_enabled": True,
                                                                  "detection_fixes_enabled": True,
                                                                  "runtime_sliders_enabled": True}),
                        ("+ program_settings_fix", {"background_graph_algo_enabled": True,
                                                    "program_settings_fix_enabled": True})):
    print(f"\n--- switches: {feat_name} ---")
    print(f"  {'ALGORITHM':24s} {'EMPTY':>6s} {'RICE':>6s} {'DARK':>6s} {'YELLOW':>6s}")
    d = make_dash(feat)
    fe, fr, fd, fy = frame_empty(), frame_rice(), frame_dark(), frame_yellow()
    for label, spec in ALGOS.items():
        algo, cfg = ("BACKGROUND (GRAPH)", {}) if label.startswith("BACKGROUND") else spec
        r = [run(d, algo, cfg, f) for f in (fe, fr, fd, fy)]
        results[(feat_name, label)] = r
        print(f"  {label:24s} {len(r[0]):6d} {len(r[1]):6d} {len(r[2]):6d} {len(r[3]):6d}")

M = "MACHINE NOW"
# what the machine uses today must be correct
check("BACKGROUND (GRAPH): empty chute fires 0", len(results[(M, "BACKGROUND (GRAPH)")][0]) == 0)
check("BACKGROUND (GRAPH): fires exactly the 4 rice channels",
      results[(M, "BACKGROUND (GRAPH)")][1] == [5, 20, 40, 60], str(results[(M, "BACKGROUND (GRAPH)")][1]))
# findings (documented, behaviour of the current code)
r_si = results[(M, "HSV COLOR (SORT IN)")]
print(f"\n  FINDING: HSV SORT IN on an EMPTY chute fires {len(r_si[0])} channels "
      f"(the background is 'not the good colour' too)")
r_d1, r_d2 = results[(M, "DISCOLOUR A R>G")], results[(M, "DISCOLOUR A B>Y")]
print(f"  FINDING: DISCOLOUR 'RED > GREEN' vs 'BLUE > YELLOW' give the same result: {r_d1 == r_d2} "
      f"(the combo setting is never read by the sorter)")
r_gd = results[(M, "GRAY A (DARK)")]
print(f"  FINDING: GRAY DARK with LO 0 finds the black grain: {30 in r_gd[2]} (channels {r_gd[2]})")
r_sb = results[(M, "SIZE: BIG")]
print(f"  FINDING: SIZE: BIG (old logic) fires on rice: {len(r_sb[1])}, empty: {len(r_sb[0])}")

F = "+ program_settings_fix"
print("\n  --- with program_settings_fix_enabled ---")
for lab in ALGOS:
    check(f"{lab}: empty chute fires 0 (fix ON)", len(results[(F, lab)][0]) == 0, str(results[(F, lab)][0]))
check("HSV SORT OUT (yellow box): only the yellow grain (ch 30)", results[(F, "HSV COLOR (SORT OUT)")][3] == [30],
      str(results[(F, "HSV COLOR (SORT OUT)")][3]))
check("HSV SORT IN: white rice (outside the box) rejected, background not", results[(F, "HSV COLOR (SORT IN)")][1] == [5, 20, 40, 60],
      str(results[(F, "HSV COLOR (SORT IN)")][1]))
check("DISCOLOUR YELLOW: only the yellow grain (ch 30)", results[(F, "DISCOLOUR A YELLOW")][3] == [30],
      str(results[(F, "DISCOLOUR A YELLOW")][3]))
check("DISCOLOUR YELLOW: white rice alone -> 0", results[(F, "DISCOLOUR A YELLOW")][1] == [])
check("DISCOLOUR combo is used (R>G differs from YELLOW)",
      results[(F, "DISCOLOUR A R>G")] != results[(F, "DISCOLOUR A YELLOW")])
check("switch OFF: old behaviour unchanged (SORT IN still 63 on empty)", len(results[(M, "HSV COLOR (SORT IN)")][0]) == 63)

if fails:
    print("\nFAILED:", fails)
    sys.exit(1)
print("\nALL TESTS PASSED")
