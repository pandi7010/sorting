"""
tools/test_follow_hold_sim.py - 19 Nov 2026 (user's requirement): follow mode with follow_hold_long_objects.
  1. a wire (defect reaches the last line of every frame) -> ONE continuous blast, from DELAY until shortly after
     the wire is gone (no pat-pat)
  2. a particle inside one frame -> exactly DELAY, then DWELL
  3. a particle cut by the frame border (end of frame 1 + start of frame 2) -> one joined blast
  4. switch OFF -> the old behaviour (repeated DWELL blasts for a wire)
Real MainDashboard methods with a fake ESP32 link (records every byte with its time). No hardware.
  1. switch auto_wiper_enabled (OFF by the user's decision): no automatic 8 s sorting pause; interval 0 safe
  2. switch ejector_memory_clear_enabled (ON by the user's decision): a stopped camera's valve is closed,
     valves the ESP32 watchdog closed are forgotten, ESP32 restarts are detected
  3. switch follow_delay_dwell_enabled: DELAY and DWELL really move / lengthen the blast

    python tools/test_follow_hold_sim.py      -> must print  ALL TESTS PASSED
STILL NEEDS THE REAL MACHINE: real wire / particles, air timing.
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
_cwd = os.getcwd()
spec = importlib.util.spec_from_file_location("lamton_main", os.path.join(ROOT, "LAMTON_LINE_NEW_ESP32.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
os.chdir(_cwd)
MD = m.MainDashboard
F = m.features

fails = []
def check(n, c, i=""):
    print(f"  [{'OK  ' if c else 'FAIL'}] {n} {i}")
    if not c:
        fails.append(n)


def feats(**kw):
    F._cache = dict(F.DEFAULTS, **kw)


class FakeLink:
    mode = "ESP_BYTE"
    def __init__(s):
        s.log = []; s.counters = collections.Counter(); s.text_lines = []; s._l = threading.Lock()
    @property
    def is_open(s): return True
    def _write(s, b):
        with s._l:
            s.log.append((time.perf_counter(), bytes(b)))
        return True
    def events(s):
        """[(t, ch, 'ON'/'OFF')] without 'W' and refresh repeats"""
        out, on = [], set()
        with s._l:
            items = list(s.log)
        for t, b in items:
            for x in b:
                if 1 <= x <= 63 and x not in on:
                    on.add(x); out.append((t, x, "ON"))
                elif 101 <= x <= 163 and (x - 100) in on:
                    on.discard(x - 100); out.append((t, x - 100, "OFF"))
        return out


class Timer:
    def __init__(s): s.started = None
    def stop(s): s.started = None
    def start(s, ms): s.started = ms


def mk(delay=0.0, dwell=0.0):
    d = NS()
    d.esp32 = NS(ejector_link=FakeLink(), ejector_port="COM24", ejector_test_active=False)
    d.system_running = True
    d.wire_test_mode_active = False
    d.auto_clean_timer = Timer()
    d.wiper_settings = {'fwd': 1500, 'rev': 1500, 'interval': 6, 'ejector_on': False}
    d.get_ejector_config = lambda: (float(delay), float(dwell))
    d._frame_interval_ms = {"FRONT": 12.5, "REAR": 12.5}
    d._last_frame_t = {}
    for name in ("restart_auto_wiper_timer", "evaluate_fusion_and_fire", "_forget_old_channels",
                 "_follow_delay_dwell_fire", "_capture_timed_fire", "_ejector_link_watch"):
        setattr(d, name, (lambda fn: (lambda *a, **k: fn(d, *a, **k)))(getattr(MD, name)))
    return d


def frame(d, source, chs):
    """what process_live_frame does: remember the frame time, store the decision, fire"""
    d._last_frame_t[source] = time.perf_counter()
    dec = {"valid": bool(chs), "channels": list(chs)}
    if source == "FRONT":
        d._last_front_decision = dec
    else:
        d._last_rear_decision = dec
    d.evaluate_fusion_and_fire(source)


def sleep_until(t):
    while time.perf_counter() < t:
        time.sleep(0.0005)



def run(frames, delay=5.0, dwell=10.0, hold=True, ch=12):
    """frames: list of row-extent tuples (or None = channel not seen) for consecutive 12.5 ms frames."""
    feats(follow_delay_dwell_enabled=True, follow_hold_long_objects=hold)
    d = mk(delay, dwell); lk = d.esp32.ejector_link
    d._frame_meta = {}
    for _ in range(3):
        d._frame_meta["FRONT"] = {"rows_used": 64, "row_extents": {}}
        frame(d, "FRONT", []); time.sleep(0.0125)
    t0 = time.perf_counter()
    seen = []
    for i, rr in enumerate(frames + [None] * 10):
        sleep_until(t0 + i * 0.0125)
        d._frame_meta["FRONT"] = {"rows_used": 64, "row_extents": ({ch: rr} if rr else {})}
        if rr:
            seen.append(time.perf_counter())
        frame(d, "FRONT", [ch] if rr else [])
    sleep_until(time.perf_counter() + 0.3)
    d._timed_valves.stop()
    return [e for e in lk.events() if e[1] == ch], seen


print("1. wire held for 20 frames (250 ms): one continuous blast")
ev, seen = run([(0, 63)] * 20)
ons = [e for e in ev if e[2] == "ON"]; offs = [e for e in ev if e[2] == "OFF"]
check("exactly one ON and one OFF (no pat-pat)", len(ons) == 1 and len(offs) == 1, str(len(ev)))
if ons and offs:
    on_ms = (ons[0][0] - seen[0]) * 1000; off_ms = (offs[0][0] - seen[-1]) * 1000
    check(f"   ON {on_ms:.1f} ms after the first frame (DELAY 5)", abs(on_ms - 5.0) < 2.0)
    check(f"   OFF {off_ms:.1f} ms after the last frame (DELAY 5 + frame 12.5 + margin 8 = 25.5)", abs(off_ms - 25.5) < 3.0)
    check(f"   valve ON the whole time ({(offs[0][0]-ons[0][0])*1000:.0f} ms)", (offs[0][0] - ons[0][0]) > 0.25)

print("2. particle inside one frame: exactly DELAY + DWELL")
ev, seen = run([(20, 30)])
ons = [e for e in ev if e[2] == "ON"]; offs = [e for e in ev if e[2] == "OFF"]
ok = len(ons) == 1 and len(offs) == 1
check("one blast", ok, str(ev))
if ok:
    check(f"   ON {(ons[0][0]-seen[0])*1000:.1f} ms (DELAY 5), blows {(offs[0][0]-ons[0][0])*1000:.1f} ms (DWELL 10)",
          abs((ons[0][0]-seen[0])*1000 - 5) < 2 and abs((offs[0][0]-ons[0][0])*1000 - 10) < 2)

print("3. particle cut by the frame border: one joined blast")
ev, seen = run([(55, 63), (0, 6)])
ons = [e for e in ev if e[2] == "ON"]; offs = [e for e in ev if e[2] == "OFF"]
ok = len(ons) == 1 and len(offs) == 1
check("one ON, one OFF", ok, str(ev))
if ok:
    check(f"   OFF {(offs[0][0]-seen[-1])*1000:.1f} ms after the 2nd frame (DELAY 5 + DWELL 10 = 15)",
          abs((offs[0][0]-seen[-1])*1000 - 15) < 2.5)

print("4. two separate particles in the same channel, 2 frames apart: two blasts")
ev, seen = run([(10, 20), None, (10, 20)])
check("two ONs", len([e for e in ev if e[2] == "ON"]) == 2, str(ev))

print("5. DWELL 0 still means no blow, also for a wire")
ev, seen = run([(0, 63)] * 5, dwell=0.0)
check("no valve byte", not ev, str(ev))

print("6. switch OFF: old behaviour (wire gives repeated blasts)")
ev, seen = run([(0, 63)] * 10, hold=False)
check("more than one blast", len([e for e in ev if e[2] == "ON"]) > 1, str(len(ev)))

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
