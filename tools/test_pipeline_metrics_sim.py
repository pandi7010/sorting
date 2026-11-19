"""
tools/test_pipeline_metrics_sim.py - 18 Nov 2026: LOST FRAMES / BACKLOG / EJECT (SENT/DET) / NOT SENT rows.
Real functions (_frame_metrics, evaluate_fusion_and_fire, _follow_delay_dwell_fire, _metric_texts) with a fake
camera, a fake SDK lost counter and a fake ESP32 link.

    python tools/test_pipeline_metrics_sim.py      -> must print  ALL TESTS PASSED
STILL NEEDS THE REAL MACHINE: the SDK lost-frame counter and real backlog numbers.
"""
import os, sys, time, types, collections, threading
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
MD, F = m.MainDashboard, m.features

fails = []
def check(n, c, i=""):
    print(f"  [{'OK  ' if c else 'FAIL'}] {n} {i}")
    if not c:
        fails.append(n)


print("1. lost frames + backlog (camera thread helper)")
check("switch pipeline_metrics_enabled ON by default (user's request)", F.DEFAULTS.get("pipeline_metrics_enabled") is True)
w = NS(period_ms=None, _prev_cam_ts=None)
ts, t = 1000000, 100.0


def feed(n, step_ts=129, step_t=0.0129, extra_t=0.0):
    global ts, t
    for _ in range(n):
        m._frame_metrics(w, ts, t + extra_t)
        w._prev_cam_ts = ts
        w.period_ms = 12.9 if w.period_ms is None else 0.9 * w.period_ms + 0.1 * 12.9
        ts += step_ts; t += step_t


feed(50)
check("steady frames: no gaps, lag ~0", getattr(w, 'm_gap_frames', 0) == 0 and w.m_lag_ms < 0.2,
      f"gaps {getattr(w, 'm_gap_frames', 0)} lag {w.m_lag_ms:.2f}")
ts += 129 * 3; t += 0.0129 * 3                 # camera made 3 frames the PC never got
feed(5)
check("3 frames missing in the timestamps -> gap 3", w.m_gap_frames == 3, str(w.m_gap_frames))
w.m_lag_max = 0.0
feed(30, extra_t=0.006)                         # frames now reach the PC 6 ms later (waiting in the buffer)
check("frames 6 ms late -> backlog ~6 ms", 4.5 < w.m_lag_ms < 6.5 and w.m_lag_max > 5.5,
      f"avg {w.m_lag_ms:.1f} max {w.m_lag_max:.1f}")


print("2. detected -> sent / not sent (follow mode)")
F._cache = dict(F.DEFAULTS, follow_delay_dwell_enabled=True, follow_hold_long_objects=False)   # 19 Nov: busy rule


class FakeLink:
    mode = "ESP_BYTE"
    def __init__(s): s.counters = collections.Counter(); s.text_lines = []
    @property
    def is_open(s): return True
    def _write(s, b): return True


d = NS(system_running=True, wire_test_mode_active=False, _frame_interval_ms={"FRONT": 12.5}, _last_frame_t={})
d.esp32 = NS(ejector_link=FakeLink(), ejector_test_active=False)
d._dwell = 10.0
d.get_ejector_config = lambda: (0.0, d._dwell)
for n in ("evaluate_fusion_and_fire", "_forget_old_channels", "_follow_delay_dwell_fire", "_capture_timed_fire",
          "_ej_metrics", "_metric_texts"):
    setattr(d, n, (lambda fn: (lambda *a, **k: fn(d, *a, **k)))(getattr(MD, n)))


def frame(chs):
    d._last_frame_t["FRONT"] = time.perf_counter()
    d._last_front_decision = {"valid": bool(chs), "channels": list(chs)}
    d.evaluate_fusion_and_fire("FRONT")


frame([]); time.sleep(0.02)
frame([5]); frame([])                           # defect 1 -> sent
time.sleep(0.03)
frame([7]); frame([]); frame([7])               # defect 2 sent, defect 3 in ch7 while its 10 ms blast still runs -> busy
time.sleep(0.03)
d._dwell = 0.0; d._timed_cfg_t = 0.0
frame([]); frame([9])                           # defect 4 with DWELL 0 -> not sent
mm = d._ej_metrics()
check("detected 4", mm["detected"] == 4, str(dict(mm)))
check("sent 2", mm["sent"] == 2, str(dict(mm)))
check("not sent: busy 1, dwell 0 1", mm["busy"] == 1 and mm["dwell 0"] == 1, str(dict(mm)))
d._timed_valves.stop()

print("3. texts on the main screen")
d.camera_worker_front = NS(camera_handle=1, m_gap_frames=2, m_lag_ms=0.4, m_lag_max=1.1, period_ms=12.9)
d.camera_worker_rear = None
m.mvsdk = NS(CameraGetFrameStatistic=lambda h: NS(iLost=3, iTotal=1000))
tx = d._metric_texts()
check("LOST FRAMES: 'F 3 +2 gap   R --' in red", tx[0][0] == "F 3 +2 gap   R --" and tx[0][1] == "#ff1744", repr(tx[0]))
check("BACKLOG: 'F 0.4/1.1   R -- ms'", tx[1][0].startswith("F 0.4/1.1   R -- ms"), repr(tx[1]))
check("EJECT: '2 / 4' orange", tx[2] == ("2 / 4", "#ffa726"), repr(tx[2]))
check("NOT SENT: '2  (busy 1, dwell 0 1)'", tx[3][0] == "2  (busy 1, dwell 0 1)", repr(tx[3]))
d.esp32.ejector_link.counters["write_errors"] = 4
check("usb write errors shown in red", "usb err 4" in d._metric_texts()[3][0] and d._metric_texts()[3][1] == "#ff1744")
check("backlog max resets after each read", d.camera_worker_front.m_lag_max == 0.0)

print("4. switch OFF -> nothing counted")
F._cache = dict(F.DEFAULTS, follow_delay_dwell_enabled=True, pipeline_metrics_enabled=False)
d._ejm = collections.Counter(); d._m_prev_active = set(); d._dwell = 10.0; d._timed_cfg_t = 0.0
frame([]); frame([11])
check("no counters with the switch OFF", sum(d._ej_metrics().values()) == 0, str(dict(d._ej_metrics())))
if getattr(d, '_timed_valves', None):
    d._timed_valves.stop()

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
