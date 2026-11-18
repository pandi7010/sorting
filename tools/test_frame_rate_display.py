"""
tools/test_frame_rate_display.py - 18 Nov 2026: FRONT / BACK FRAME RATE rows on the main-screen status block.
Real MainDashboard._frame_rate_text / _update_frame_rate_labels with fake camera workers and labels.

    python tools/test_frame_rate_display.py      -> must print  ALL TESTS PASSED
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
from types import SimpleNamespace as NS
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


class Lbl:
    def __init__(s): s.text = ""; s.style = ""
    def setText(s, t): s.text = t
    def setStyleSheet(s, st): s.style = st


now = time.perf_counter()
d = NS(camera_worker_front=NS(period_ms=12.8, last_meta={"t_arrive": now, "height": 64, "rows_used": 64}),
       camera_worker_rear=NS(period_ms=12.6, last_meta={"t_arrive": now - 5.0, "height": 64}),
       lbl_status_fps_front=Lbl(), lbl_status_fps_rear=Lbl())
d._frame_rate_text = lambda w: MD._frame_rate_text(d, w)

print("frame rate rows")
check("switch frame_rate_display ON by default (user's request)", m.features.DEFAULTS.get("frame_rate_display") is True)
txt = MD._frame_rate_text(d, d.camera_worker_front)
check("12.8 ms between frames, 64 lines -> 78.1 fps, 5,000 lines/s", txt == "78.1 fps  (5,000 lines/s)", repr(txt))
MD._update_frame_rate_labels(d)
check("FRONT label shows it in green", d.lbl_status_fps_front.text == txt and "00e676" in d.lbl_status_fps_front.style)
check("REAR: no frame for 5 s -> '-- (no frames)'", d.lbl_status_fps_rear.text == "-- (no frames)",
      d.lbl_status_fps_rear.text)
d.camera_worker_rear = None
MD._update_frame_rate_labels(d)
check("camera missing -> '-- (no frames)', no crash", d.lbl_status_fps_rear.text == "-- (no frames)")
d.camera_worker_front = NS(period_ms=None, last_meta={})
MD._update_frame_rate_labels(d)
check("no timestamps yet -> '-- (no frames)'", d.lbl_status_fps_front.text == "-- (no frames)")

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
