"""
tools/test_delay_pipeline_sim.py - 18 Nov 2026: the whole DELAY / DWELL path, end to end, with the REAL methods:
  main-screen keypad (_dd_set) -> recipe "ejector" saved -> get_ejector_config() -> _follow_delay_dwell_fire
  -> TimedValveScheduler -> bytes to the ESP32 (fake link, every byte with its time).
recipes.json is kept in memory (the real file is not touched); ejector_config.json is backed up and restored.

    python tools/test_delay_pipeline_sim.py      -> must print  ALL TESTS PASSED
STILL NEEDS THE REAL MACHINE: USB + ESP32 + valve times (about 1-2 ms more).
"""
import os, sys, time, types, collections, threading, shutil, copy
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
MD, F, RM = m.MainDashboard, m.features, m.RecipeManager

fails = []
def check(n, c, i=""):
    print(f"  [{'OK  ' if c else 'FAIL'}] {n} {i}")
    if not c:
        fails.append(n)


# recipes in memory
DB = {"active_scheme": "RICE", "schemes": {"RICE": {"ejector": {"delay": 300.0, "pulse": 175.0}}}}
RM.load_all = staticmethod(lambda: copy.deepcopy(DB))
def _save(data):
    DB.clear(); DB.update(copy.deepcopy(data))
RM.save_all = staticmethod(_save)

EJ = os.path.join(ROOT, "ejector_config.json")
EJ_BYTES = open(EJ, "rb").read() if os.path.exists(EJ) else None      # restored byte for byte at the end


class FakeLink:
    mode = "ESP_BYTE"
    def __init__(s): s.log = []; s.counters = collections.Counter(); s.text_lines = []; s._l = threading.Lock()
    @property
    def is_open(s): return True
    def _write(s, b):
        with s._l:
            s.log.append((time.perf_counter(), bytes(b)))
        return True
    def events(s, ch):
        out = []
        with s._l:
            items = list(s.log)
        for t, b in items:
            for x in b:
                if x == ch: out.append((t, "ON"))
                elif x == ch + 100: out.append((t, "OFF"))
        return out


def mk():
    d = NS(current_program="RICE", program_data={"RICE": {}}, system_running=True, wire_test_mode_active=False,
           _frame_interval_ms={"FRONT": 12.5}, _last_frame_t={})
    d.esp32 = NS(ejector_link=FakeLink(), ejector_test_active=False)
    for n in ("_dd_set", "_dd_values", "_dd_refresh", "get_ejector_config", "evaluate_fusion_and_fire",
              "_forget_old_channels", "_follow_delay_dwell_fire", "_capture_timed_fire"):
        setattr(d, n, (lambda fn: (lambda *a, **k: fn(d, *a, **k)))(getattr(MD, n)))
    return d


def frame(d, chs):
    d._last_frame_t["FRONT"] = time.perf_counter()
    d._last_front_decision = {"valid": bool(chs), "channels": list(chs)}
    d.evaluate_fusion_and_fire("FRONT")


def one_object(d, ch=20):
    for _ in range(4):                       # empty frames (valve thread starts, recipe read)
        frame(d, []); time.sleep(0.0125)
    t0 = time.perf_counter(); frame(d, [ch])
    for _ in range(25):
        time.sleep(0.0125); frame(d, [])
    return t0, d.esp32.ejector_link.events(ch)


try:
    print("1. keypad -> recipe -> get_ejector_config")
    F._cache = dict(F.DEFAULTS, follow_delay_dwell_enabled=True)
    d = mk()
    check("before: recipe says 300 / 175", d.get_ejector_config() == (300.0, 175.0), str(d.get_ejector_config()))
    d._dd_set("delay", 7.5); d._dd_set("pulse", 12)
    check("keypad DELAY 7.5, DWELL 12 -> saved in the recipe", DB["schemes"]["RICE"]["ejector"] == {"delay": 7.5, "pulse": 12.0},
          str(DB["schemes"]["RICE"]["ejector"]))
    check("get_ejector_config reads 7.5 / 12", d.get_ejector_config() == (7.5, 12.0))

    print("2. follow mode: valve times on the ESP32 link")
    t0, ev = one_object(d)
    ok = len(ev) == 2 and ev[0][1] == "ON" and ev[1][1] == "OFF"
    check("one ON + one OFF", ok, str([(round((t - t0) * 1000, 1), s) for t, s in ev]))
    if ok:
        on_ms, blow_ms = (ev[0][0] - t0) * 1000, (ev[1][0] - ev[0][0]) * 1000
        check(f"ON {on_ms:.1f} ms after the frame (DELAY 7.5)", abs(on_ms - 7.5) < 1.5)
        check(f"blows {blow_ms:.1f} ms (DWELL 12)", abs(blow_ms - 12.0) < 1.5)
    d._timed_valves.stop()

    print("3. change while running: new DELAY used within one frame")
    d = mk()
    for _ in range(4):
        frame(d, []); time.sleep(0.0125)
    d._dd_set("delay", 25)                    # operator changes it on the keypad
    t0 = time.perf_counter(); frame(d, [21])
    for _ in range(30):
        time.sleep(0.0125); frame(d, [])
    ev = d.esp32.ejector_link.events(21)
    on_ms = (ev[0][0] - t0) * 1000 if ev else -1
    check(f"DELAY 25 used at once: ON {on_ms:.1f} ms", ev and abs(on_ms - 25.0) < 1.5)
    d._timed_valves.stop()

    print("4. follow switch OFF (level-follow): DELAY / DWELL are NOT used")
    F._cache = dict(F.DEFAULTS, follow_delay_dwell_enabled=False)
    d = mk(); d._dd_set("delay", 50); d._dd_set("pulse", 100)
    t0, ev = one_object(d, 22)
    on_ms = (ev[0][0] - t0) * 1000 if ev else -1
    check(f"ON at once ({on_ms:.1f} ms), not after 50 ms", ev and on_ms < 2.0)
finally:
    if EJ_BYTES is not None:
        with open(EJ, "wb") as f:
            f.write(EJ_BYTES)

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
