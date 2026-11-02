"""
tools/test_channel_switch_sim.py - switch clean_channel_switch_enabled (no camera, no ESP32).
A 4-px needle moves 1 px per frame across the channel 1 | channel 2 boundary (pixel 82.9). Runs the REAL
main sorting path (process_frame_for_defects + evaluate_fusion_and_fire, ESP_BYTE) and the REAL Background
Graph window path (evaluate_ejectors_instant) and records every byte sent to the ESP32.

    python tools/test_channel_switch_sim.py        -> must print  ALL TESTS PASSED
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
H = 64
fails = []


def check(n, c, i=""):
    print(f"  [{'OK  ' if c else 'FAIL'}] {n} {i}")
    if not c:
        fails.append(n)


class Link:
    is_open = True
    mode = "ESP_BYTE"

    def __init__(self):
        self.w = []

    def _write(self, b):
        self.w.append(bytes(b))
        return True


def needle(x0, width=4):
    f = np.full((H, 4096, 3), 200, np.uint8)
    f[:, x0:x0 + width, :] = 20
    return f


CAMS = {"start_pixel": 19, "end_pixel": 4046, "num_channels": 63, "total_chutes": 1,
        "chute_roles": {"1": "PRIMARY"}, "wave_mode": "BLUE"}
ASET = {"GRAY A": {"channel": "GREEN", "mode": "DARK DEFECT", "sens": 50, "scale": 6}}
XS = list(range(76, 90))


def setf(on):
    m.features._cache = dict(m.features.DEFAULTS, clean_channel_switch_enabled=on, channel_min_px=2)


def main_path(on):
    setf(on)
    link = Link()
    d = NS(bg_settings={}, wire_test_mode_active=False, system_running=True, capture_ejection_off=False,
           operating_led_bar=None, esp32=NS(ejector_link=link, ejector_test_active=False), _frame_meta={},
           _last_front_decision=None, _last_rear_decision=None, get_ejector_config=lambda: (10.0, 3.0))
    states, writes = [], []
    for x in XS:
        link.w.clear()
        d._last_front_decision = MD.process_frame_for_defects(d, needle(x), CAMS, ["GRAY A"], ASET, "FRONT")
        MD.evaluate_fusion_and_fire(d, "FRONT")
        states.append(tuple(sorted(d._live_hw_active)))
        writes.append([w for w in link.w if w != b"W"])
    return states, writes


def graph_path(on):
    setf(on)
    link = Link()
    g = NS(local_system_running=True, arduino=NS(connected=True, ejector_link=link), eject_lines=2,
           wave_mode="BLUE", thresh_lower=68, thresh_upper=230, band_mode=False, start_pixel=19, end_pixel=4046,
           num_channels=63, hw_active_channels=set(), eject_delay_ms=0, eject_blow_ms=0, _tall_frame_warned=True)
    g._timed_params = lambda: BG._timed_params(g)
    states, writes = [], []
    for x in XS:
        link.w.clear()
        BG.evaluate_ejectors_instant(g, needle(x))
        states.append(tuple(sorted(g.hw_active_channels)))
        writes.append([w for w in link.w if w != b"W"])
    return states, writes


def show(name, states):
    print("   " + name + ": " + " ".join(f"{x}:{''.join(str(c) for c in s) or '-'}" for x, s in zip(XS, states)))


g_off, gw_off = graph_path(False)
m_off, _ = main_path(False)
m_on, mw_on = main_path(True)
g_on, gw_on = graph_path(True)
print("needle start pixel : valves ON")
show("graph window       ", g_off)
show("main path, switch OFF", m_off)
show("main path, switch ON ", m_on)

print("1. switch OFF = old behaviour")
check("main path keeps ch1 ON with 1 edge pixel (3 frames with both ON)", sum(1 for s in m_off if s == (1, 2)) == 3)
print("2. switch ON")
check("main path switches exactly like the Background Graph window", m_on == g_off, str(m_on))
check("ch1 OFF as soon as < 2 px remain in it", m_on[XS.index(82)] == (2,), str(m_on[XS.index(82)]))
check("graph window behaviour unchanged by the switch", g_on == g_off)
sw = [w for ws in mw_on for w in ws if any(b > 100 for b in w) and any(b < 100 for b in w)]
allw = [w for ws in mw_on for w in ws]
check("every write puts OFF bytes before ON bytes",
      all(all(b > 100 for b in w[:sum(1 for x in w if x > 100)]) for w in allw), str(allw))
print("3. needle jumps a whole channel (ch1 -> ch2 in one frame): one write, OFF then ON")
setf(True)
link = Link()
d = NS(bg_settings={}, wire_test_mode_active=False, system_running=True, capture_ejection_off=False,
       operating_led_bar=None, esp32=NS(ejector_link=link, ejector_test_active=False), _frame_meta={},
       _last_front_decision=None, _last_rear_decision=None, get_ejector_config=lambda: (10.0, 3.0))
for x in (40, 110):
    link.w.clear()
    d._last_front_decision = MD.process_frame_for_defects(d, needle(x), CAMS, ["GRAY A"], ASET, "FRONT")
    MD.evaluate_fusion_and_fire(d, "FRONT")
w = [x for x in link.w if x != b"W"]
check("single write b'\\x65\\x02' (OFF1, ON2)", w == [bytes([101, 2])], str(w))
g = NS(local_system_running=True, arduino=NS(connected=True, ejector_link=Link()), eject_lines=2, wave_mode="BLUE",
       thresh_lower=68, thresh_upper=230, band_mode=False, start_pixel=19, end_pixel=4046, num_channels=63,
       hw_active_channels=set(), eject_delay_ms=0, eject_blow_ms=0, _tall_frame_warned=True)
g._timed_params = lambda: BG._timed_params(g)
for x in (40, 110):
    g.arduino.ejector_link.w.clear()
    BG.evaluate_ejectors_instant(g, needle(x))
w = [x for x in g.arduino.ejector_link.w if x != b"W"]
check("graph window: single write (OFF1, ON2)", w == [bytes([101, 2])], str(w))

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
