"""
tools/test_ejector_link_watch_sim.py - ejector USB link watch (3 Nov 2026, switch ejector_auto_reconnect).
Real MainDashboard._ejector_link_watch / _cam_load_text with a fake ESP32 controller; also shows that the real
EjectorLink stops for good after one serial read error (the cause of "valves stop after a few minutes").

    python tools/test_ejector_link_watch_sim.py      -> must print  ALL TESTS PASSED
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
MD=m.MainDashboard

fails=[]
def check(n,c,i=""):
    print(f"  [{'OK  ' if c else 'FAIL'}] {n} {i}");  (None if c else fails.append(n))


import collections, threading

class FakeLink:
    def __init__(s): s.ok = True; s.counters = collections.Counter(); s.text_lines = ["[LINK] read error: ClearCommError"]
    @property
    def is_open(s): return s.ok

class FakeESP:
    def __init__(s): s.ejector_link = FakeLink(); s.ejector_port = "COM24"; s.calls = 0; s.succeed = True
    def connect_ejector(s, port):
        s.calls += 1
        if s.succeed:
            s.ejector_link = FakeLink()
        return s.succeed

def mk():
    d = NS(esp32=FakeESP(), camera_worker_front=NS(busy_ms=5.0, period_ms=12.5), camera_worker_rear=NS(busy_ms=5.0, period_ms=12.5))
    d._ejector_link_watch = lambda: MD._ejector_link_watch(d)
    d._cam_load_text = lambda: MD._cam_load_text(d)
    return d

def wait_reconnect(d):
    for _ in range(100):
        if not getattr(d, '_link_reconnecting', False):
            return
        time.sleep(0.01)

import time
print("ejector USB link watch")
m.features._cache = dict(m.features.DEFAULTS)
check("switch ejector_auto_reconnect is OFF by default", not m.features.enabled("ejector_auto_reconnect"))
d = mk(); d._ejector_link_watch()
check("healthy link -> 'ESP32 OK' on the LED line", "ESP32 OK" in d._cam_load_text(), d._cam_load_text())
d.esp32.ejector_link.ok = False; d._ejector_link_watch()
check("link dropped -> 'LINK LOST - valves NOT driven' shown", "LINK LOST" in d._cam_load_text(), d._cam_load_text())
check("switch OFF -> no reconnect attempt", d.esp32.calls == 0)

m.features._cache = dict(m.features.DEFAULTS, ejector_auto_reconnect=True)
d = mk(); d._ejector_link_watch()
d.esp32.ejector_link.counters["write_errors"] += 2; d._ejector_link_watch(); wait_reconnect(d)
check("write errors on an 'open' link -> reconnect", d.esp32.calls == 1, str(d.esp32.calls))
check("after reconnect the valve state is reset (board starts all off)", d._live_hw_active == set() and d._wd_armed_t == 0.0)
d._ejector_link_watch()
check("next check -> ESP32 OK (link lost 1x)", "ESP32 OK (link lost 1x)" in d._link_state, d._link_state)

d.esp32.succeed = False; d.esp32.ejector_link.ok = False; d._link_last_try = 0.0
d._ejector_link_watch(); wait_reconnect(d); d._ejector_link_watch(); wait_reconnect(d)
check("failed reconnect is retried at most every 3 s", d.esp32.calls == 2, str(d.esp32.calls))
d._link_last_try = 0.0; d.esp32.succeed = True; d._ejector_link_watch(); wait_reconnect(d); d._ejector_link_watch()
check("port back -> reconnected, OK again", "ESP32 OK" in d._link_state and d.esp32.calls == 3, d._link_state)

print("EjectorLink itself: one read error stops the link (why the valves stopped)")
from ejector_link import EjectorLink
class BadSer:
    is_open = True; in_waiting = 0
    def read(s, n): raise OSError("ClearCommError failed (device disconnected)")
    def write(s, b): return len(b)
    def close(s): pass
lk = EjectorLink("COMX"); lk.ser = BadSer(); lk.mode = "ESP_BYTE"; lk._running = True
lk._reader()
check("after one read error: mode None, is_open False", lk.mode is None and not lk.is_open, str(lk.mode))

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
