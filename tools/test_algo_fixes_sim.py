"""
tools/test_algo_fixes_sim.py - switches background_graph_algo_enabled, runtime_sliders_enabled, hsv_box_fix_enabled.
Runs the REAL MainDashboard.process_frame_for_defects / BackgroundGraphWindow.evaluate_ejectors_instant on a
simulated rice curtain (no camera, no ESP32). The HSV box test needs the real PyQt5 (skipped otherwise).

    python tools/test_algo_fixes_sim.py        -> must print  ALL TESTS PASSED
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
BG = m.BackgroundGraphWindow
fails = []


def check(n, c, i=""):
    print(f"  [{'OK  ' if c else 'FAIL'}] {n} {i}")
    if not c:
        fails.append(n)


H, SP, EP = 64, 19, 4046
CW = (EP - SP) / 63
CAMS = {"start_pixel": SP, "end_pixel": EP, "num_channels": 63, "total_chutes": 1,
        "chute_roles": {"1": "PRIMARY"}, "wave_mode": "BLUE"}


def bgset(lo=68, hi=221, band=False):
    return {"camera_thresh": {"FRONT": {"lower": lo, "upper": hi, "band_mode": band}},
            "thresh_lower": lo, "thresh_upper": hi, "band_mode": band, "wave_mode": "BLUE"}


def setf(**kw):
    m.features._cache = dict(m.features.DEFAULTS, **kw)


def run(f, algos, aset=None, bg=None, thr=None):
    d = NS(bg_settings=bg or bgset(), program_data={}, thresholds=thr or {})
    return set(MD.process_frame_for_defects(d, f, CAMS, algos, aset or {}, "FRONT")["channels"])


def graph(f, bg=None):
    bg = bg or bgset()
    ct = bg["camera_thresh"]["FRONT"]

    class L:
        is_open = True
        mode = "ESP_BYTE"

        def _write(self, b):
            return True
    g = NS(local_system_running=True, arduino=NS(connected=True, ejector_link=L()), eject_lines=H,
           wave_mode="BLUE", thresh_lower=ct["lower"], thresh_upper=ct["upper"], band_mode=ct["band_mode"],
           start_pixel=SP, end_pixel=EP, num_channels=63, hw_active_channels=set(), eject_delay_ms=0,
           eject_blow_ms=0, _tall_frame_warned=True)
    g._timed_params = lambda: BG._timed_params(g)
    BG.evaluate_ejectors_instant(g, f)
    return set(g.hw_active_channels)


def curtain(bgv, grain, seed=1, n=80):
    rng = np.random.default_rng(seed)
    f = np.full((H, 4096, 3), bgv, np.uint8)
    truth = set()
    for _ in range(n):
        x = int(rng.integers(SP, EP - 30)); y = int(rng.integers(0, H - 8))
        w = int(rng.integers(20, 32)); h = int(rng.integers(6, 12))
        f[y:y + h, x:x + w] = grain
        for c in range(int((x - SP) / CW), int((x + w - 1 - SP) / CW) + 1):
            truth.add(c + 1)
    return f, truth


print("1. BACKGROUND (GRAPH) algorithm = what the Background Graph window rejects")
setf(background_graph_algo_enabled=True)
for title, bgv, gr in (("white rice on mid background", 140, 235), ("dark product on bright background", 200, 40)):
    f, truth = curtain(bgv, gr)
    got = run(f, ["BACKGROUND (GRAPH)"])
    check(f"{title}: all {len(truth)} product channels, no empty channel", got == truth,
          f"(got {len(got & truth)}, extra {len(got - truth)})")
    check(f"{title}: identical to the Background Graph window", got == graph(f))
f = np.full((H, 4096, 3), 140, np.uint8); f[10:20, 1380:1400] = 120         # product INSIDE the band 100..130
band = bgset(100, 130, True)
check("BAND mode: product inside the band is rejected", run(f, ["BACKGROUND (GRAPH)"], bg=band) == {22},
      str(run(f, ["BACKGROUND (GRAPH)"], bg=band)))
setf(background_graph_algo_enabled=False)
f, truth = curtain(140, 235)
check("switch OFF: name falls to the old FALLBACK code (unchanged behaviour)",
      run(f, ["BACKGROUND (GRAPH)"]) == run(f, ["SOMETHING ELSE"]))

print("2. RUNTIME TUNING sliders")
f = np.full((H, 4096, 3), 200, np.uint8); f[40:48, 1380:1400, :] = 90         # medium-dark spot (gray 90)
A = {"GRAY A": {"channel": "GREEN", "mode": "DARK DEFECT", "sens": 50, "scale": 6}}
B = {"GRAY B": {"channel": "GREEN", "mode": "DARK DEFECT", "sens": 50, "scale": 6}}
setf(runtime_sliders_enabled=False)
check("switch OFF: sliders ignored (GRAY B threshold 150 does nothing)",
      run(f, ["GRAY B"], B, thr={"GRAY_THRESH": 150}) == run(f, ["GRAY B"], B, thr={"GRAY_THRESH": 20}))
setf(runtime_sliders_enabled=True)
check("GRAY B: THRESHOLD 150 -> spot (90) rejected", run(f, ["GRAY B"], B, thr={"GRAY_THRESH": 150}) == {22})
check("GRAY B: THRESHOLD 60 -> spot (90) kept", run(f, ["GRAY B"], B, bg=bgset(20, 250), thr={"GRAY_THRESH": 60}) == set(),
      str(run(f, ["GRAY B"], B, bg=bgset(20, 250), thr={"GRAY_THRESH": 60})))
lo_bg = bgset(20, 250)
g_hi = run(f, ["GRAY A"], A, bg=lo_bg, thr={"GRAY_CONTRAST": 100})
g_lo = run(f, ["GRAY A"], A, bg=lo_bg, thr={"GRAY_CONTRAST": 0})
check("GRAY A: CONTRAST slider changes the result (100 vs 0)", g_hi != g_lo, f"(100 -> {g_hi}, 0 -> {g_lo})")
import cv2
_edge = cv2.cvtColor(np.uint8([[[30, 170, 230]]]), cv2.COLOR_HSV2BGR)[0, 0]   # just outside the box (S 170 < 195)
hsvf = np.full((H, 4096, 3), 128, np.uint8); hsvf[40:48, 1380:1400] = _edge
HS = {"HSV COLOR": {"x1": 50, "y1": 50, "x2": 200, "y2": 200, "hsv_mode": "SORT OUT"}}
h100 = run(hsvf, ["HSV COLOR"], HS, thr={"HSV": 100}); h0 = run(hsvf, ["HSV COLOR"], HS, thr={"HSV": 0})
check("HSV SENSITIVITY slider changes the result (100 vs 0)", h100 != h0, f"(100 -> {h100}, 0 -> {h0})")

print("3. SIZE measures the whole object (switch runtime_sliders_enabled)")
f = np.full((H, 4096, 3), 140, np.uint8)
x0 = int(round(SP + 22 * CW))                      # 80-px object across the ch22 | ch23 border (40 + 40 px)
f[10:30, x0 - 40:x0 + 40] = 235
f[10:30, 2500:2530] = 235                         # normal 30-px grain in one channel
setf(runtime_sliders_enabled=False)
check("switch OFF: 80-px object split 40+40 is NOT big (old per-channel count)", run(f, ["SIZE: BIG"], bg=bgset()) == set())
setf(runtime_sliders_enabled=True)
got = run(f, ["SIZE: BIG"], thr={"SIZE": 60})
check("SIZE 60 px: 80-px object -> its 2 channels, 30-px grain ignored", got == {22, 23}, str(got))
check("SIZE 90 px: nothing (80 < 90)", run(f, ["SIZE: BIG"], thr={"SIZE": 90}) == set())
check("white (bright) objects are measured too (old SIZE saw only dark)", 22 in got)

print("4. HSV colour box mapping (switch hsv_box_fix_enabled)")
try:
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QPixmap
    from PyQt5.QtCore import QPoint, Qt
    real_qt = hasattr(QApplication, "instance") and callable(getattr(QPixmap, "scaled", None)) and QPoint(1, 2).y() == 2
except Exception:
    real_qt = False
if not real_qt:
    print("  [SKIP] real PyQt5 not installed here (runs on the Windows PC)")
else:
    app = QApplication.instance() or QApplication(sys.argv)
    got = []
    lbl = m.InteractiveHSVLabel(on_box_selected=lambda x1, y1, x2, y2, fin: got.append((x1, y1, x2, y2)))
    lbl.setAlignment(Qt.AlignCenter)
    lbl.resize(500, 480)
    pm = QPixmap(565, 500)
    lbl.setPixmap(pm.scaled(440, 420, Qt.KeepAspectRatio))           # 440 x 389, centred in 500 x 480
    shown = lbl.pixmap()
    ox, oy = (500 - shown.width()) / 2.0, (480 - shown.height()) / 2.0

    def sel(p1, p2):
        got.clear()
        lbl.start_pos, lbl.current_pos = QPoint(*p1), QPoint(*p2)
        lbl.update_selection(final=True)
        return got[-1]
    corner = (int(ox) + 1, int(oy) + 1)
    centre = (int(ox + shown.width() / 2), int(oy + shown.height() / 2))
    setf(hsv_box_fix_enabled=False)
    old = sel(corner, centre)
    setf(hsv_box_fix_enabled=True)
    new = sel(corner, centre)
    print(f"     image {shown.width()}x{shown.height()} at offset ({ox:.0f},{oy:.0f}); drag from image corner to image centre")
    print(f"     switch OFF -> {old}   switch ON -> {new}   (should be about (0, 0, 282, 250))")
    check("switch ON: image corner -> (0,0), image centre -> (282,250) +-3",
          new[0] <= 3 and new[1] <= 3 and abs(new[2] - 282) <= 3 and abs(new[3] - 250) <= 3)
    check("switch OFF: old mapping is shifted", old != new)

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
