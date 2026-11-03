"""
tools/test_delay_dwell_main_ui.py - DELAY / DWELL row above START SYSTEM (switch main_delay_dwell_controls).
Runs the REAL MainDashboard._dd_* methods with a fake button/label and the recipes kept in memory;
ejector_config.json is backed up and restored.

    python tools/test_delay_dwell_main_ui.py        -> must print  ALL TESTS PASSED
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

import copy, json, shutil
STORE = {"active_scheme": "RICE", "schemes": {"RICE": {"ejector": {"pulse": 10.0, "delay": 2.0, "gap": 5.0, "count": 1}},
                                              "DHALL": {"ejector": {"pulse": 4.0, "delay": 30.0}}}}
m.RecipeManager.load_all = staticmethod(lambda: copy.deepcopy(STORE))
m.RecipeManager.save_all = staticmethod(lambda data: STORE.update(copy.deepcopy(data)))

class Btn:
    def __init__(s): s.t = ""
    def setText(s, t): s.t = t
    def text(s): return s.t
class Lbl(Btn):
    def setStyleSheet(s, css): s.css = css

EJ = os.path.join(ROOT, "ejector_config.json")
bak = EJ + ".test_bak"
if os.path.exists(EJ):
    shutil.copy(EJ, bak)
try:
    d = NS(current_program="RICE", program_data=copy.deepcopy(STORE["schemes"]), _dd_btn={"delay": Btn(), "pulse": Btn()},
           lbl_dd_state=Lbl())
    for n in ("_dd_values", "_dd_refresh", "_dd_set", "_dd_step"):
        setattr(d, n, (lambda fn: (lambda *a: fn(d, *a)))(getattr(MD, n)))
    m.features._cache = dict(m.features.DEFAULTS)

    print("main-screen DELAY / DWELL row")
    check("switch main_delay_dwell_controls is ON by default (user's decision)", m.features.enabled("main_delay_dwell_controls"))
    d._dd_refresh()
    check("shows the recipe values (DELAY 2, DWELL 10)", d._dd_btn["delay"].text()=="2" and d._dd_btn["pulse"].text()=="10",
          f'{d._dd_btn["delay"].text()} / {d._dd_btn["pulse"].text()}')
    check("says TIMED EJECT is OFF (values not used yet)", "OFF" in d.lbl_dd_state.text(), d.lbl_dd_state.text())
    d._dd_set("delay", "22")
    check("keypad 22 -> saved to recipe at once", STORE["schemes"]["RICE"]["ejector"]["delay"] == 22.0, str(STORE["schemes"]["RICE"]["ejector"]))
    check("other recipe keys kept (gap, count)", STORE["schemes"]["RICE"]["ejector"].get("gap") == 5.0)
    check("main window program_data updated too", d.program_data["RICE"]["ejector"]["delay"] == 22.0)
    check("ejector_config.json written", json.load(open(EJ)).get("delay") == 22.0)
    d._dd_step("pulse", -0.5); d._dd_step("pulse", -0.5)
    check("DWELL - - : 10 -> 9", STORE["schemes"]["RICE"]["ejector"]["pulse"] == 9.0, str(STORE["schemes"]["RICE"]["ejector"]["pulse"]))
    d._dd_step("delay", 1)
    check("DELAY + : 22 -> 23, shown on the button", d._dd_btn["delay"].text() == "23")
    d._dd_set("pulse", "-5"); check("DWELL cannot go below 0", STORE["schemes"]["RICE"]["ejector"]["pulse"] == 0.0)
    d._dd_set("delay", "9999"); check("DELAY limited to 500", STORE["schemes"]["RICE"]["ejector"]["delay"] == 500.0)
    d._dd_set("delay", "abc"); check("bad input ignored", STORE["schemes"]["RICE"]["ejector"]["delay"] == 500.0)
    d.get_ejector_config = lambda: MD.get_ejector_config(d)
    check("sorter reads the same values (get_ejector_config)", d.get_ejector_config() == (500.0, 0.0), str(d.get_ejector_config()))
    d.current_program = "DHALL"; d._dd_refresh()
    check("profile change shows that profile's values", d._dd_btn["delay"].text()=="30" and d._dd_btn["pulse"].text()=="4")
    m.features._cache = dict(m.features.DEFAULTS, capture_timed_eject_enabled=True); d._dd_refresh()
    check("TIMED EJECT ON is shown", "ON - used" in d.lbl_dd_state.text(), d.lbl_dd_state.text())
finally:
    if os.path.exists(bak):
        shutil.move(bak, EJ)

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
