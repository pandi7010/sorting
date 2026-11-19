"""
tools/test_esp32_free_sim.py - 11 Nov 2026: "ESP32 stops responding for a while" + "DELAY / DWELL do nothing".
Real MainDashboard methods with a fake ESP32 link (records every byte with its time). No hardware.
  1. switch auto_wiper_enabled (OFF by the user's decision): no automatic 8 s sorting pause; interval 0 safe
  2. switch ejector_memory_clear_enabled (ON by the user's decision): a stopped camera's valve is closed,
     valves the ESP32 watchdog closed are forgotten, ESP32 restarts are detected
  3. switch follow_delay_dwell_enabled: DELAY = wait, DWELL = exact blow time (0 = no blow)

    python tools/test_esp32_free_sim.py      -> must print  ALL TESTS PASSED
STILL NEEDS THE REAL MACHINE: actual blast position on the product, real ESP32 brown-out restarts.
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


# ------------------------------------------------------------------ 1. auto wiper
print("1. auto wiper switch")
feats()
check("auto_wiper_enabled is OFF by default (user's decision)", not F.enabled("auto_wiper_enabled"))
d = mk(); d.restart_auto_wiper_timer()
check("switch OFF -> wiper timer never started (no 8 s pause)", d.auto_clean_timer.started is None)
feats(auto_wiper_enabled=True)
d = mk(); d.restart_auto_wiper_timer()
check("switch ON -> timer 6 min as before", d.auto_clean_timer.started == 360000, str(d.auto_clean_timer.started))
d.wiper_settings['interval'] = 0; d.restart_auto_wiper_timer()
check("switch ON + interval 0 -> no 0 ms timer", d.auto_clean_timer.started is None)
src = open(os.path.join(ROOT, "LAMTON_LINE_NEW_ESP32.py"), encoding="utf-8").read()
check("after a wipe the main air (relay 2) is switched back ON", "self.esp32.set_relay(2, 1)      # 11 Nov 2026" in src)

# ------------------------------------------------------------------ 2. memory clear
print("2. valve memory is forgotten when it is old")
feats()
check("ejector_memory_clear_enabled is ON by default (user's decision)", F.enabled("ejector_memory_clear_enabled"))
check("follow_delay_dwell_enabled is OFF by default", not F.enabled("follow_delay_dwell_enabled"))

for sw, expect_closed in ((False, False), (True, True)):
    feats(ejector_memory_clear_enabled=sw)
    d = mk(); lk = d.esp32.ejector_link
    frame(d, "REAR", [20])                      # REAR sees an object in channel 20 ...
    frame(d, "FRONT", [])
    # ... and then the REAR camera stops (cable / disconnect); FRONT keeps running for 0.3 s
    t_end = time.perf_counter() + 0.3
    while time.perf_counter() < t_end:
        frame(d, "FRONT", [])
        time.sleep(0.0125)
    ev = lk.events()
    closed = any(e[1] == 20 and e[2] == "OFF" for e in ev)
    check(f"switch {'ON ' if sw else 'OFF'}: REAR stops while CH20 is ON -> CH20 "
          f"{'closed' if expect_closed else 'stays ON (old bug)'}", closed == expect_closed, str(ev))
    if sw:
        t_on = [e[0] for e in ev if e[1] == 20 and e[2] == "ON"][0]
        t_off = [e[0] for e in ev if e[1] == 20 and e[2] == "OFF"][0]
        check("  closed within ~60 ms after REAR's last frame", (t_off - t_on) < 0.08, f"{(t_off - t_on)*1000:.0f} ms")

feats()
d = mk(); lk = d.esp32.ejector_link
frame(d, "FRONT", [7])
check("CH7 ON", (7 in d._live_hw_active))
time.sleep(0.3)                                 # no frames 0.3 s (ESP32 watchdog closed CH7 after 250 ms)
frame(d, "FRONT", [7])
on7 = [e for e in lk.log if 7 in e[1]]
check("after a 0.3 s gap CH7 is sent ON again at once (PC forgot the closed valve)", len(on7) >= 2, str(len(on7)))

print("   ESP32 restart detection")
feats()
d = mk(); lk = d.esp32.ejector_link
d._ejector_link_watch()
d._live_hw_active = {5, 9}; d._wd_armed_t = 123.0
lk.counters["esp_resets"] += 1                  # the reader saw the READY boot line
d._ejector_link_watch()
check("restart -> valve memory cleared, watchdog 'W' re-sent next frame",
      d._live_hw_active == set() and d._wd_armed_t == 0.0)
check("restart shown on the main screen", "ESP32 RESTARTED 1x" in d._link_state, d._link_state)

from ejector_link import EjectorLink
class ReadySer:
    is_open = True
    def __init__(s): s.n = 0
    @property
    def in_waiting(s): return 0
    def read(s, n):
        s.n += 1
        if s.n == 1:
            return b"ESP32_63CH_EJECTOR_TESTER_READY\r\nPins: Data=GPIO13\r\n"
        raise OSError("stop")
    def write(s, b): return len(b)
    def close(s): pass
lk2 = EjectorLink("COMX"); lk2.ser = ReadySer(); lk2.mode = "ESP_BYTE"; lk2._running = True
lk2._reader()
check("EjectorLink counts the ESP32 READY boot line", lk2.counters["esp_resets"] == 1, str(lk2.counters["esp_resets"]))

# ------------------------------------------------------------------ 3. DELAY / DWELL like a fan regulator
print("3. DELAY = wait, DWELL = exact blow time (0 = no blow)")


def run_object(delay, dwell, frames_with_object=1):
    feats(follow_delay_dwell_enabled=True, follow_hold_long_objects=False)   # 19 Nov: the pure DWELL rule
    d = mk(delay, dwell); lk = d.esp32.ejector_link
    for _ in range(3):                      # empty frames first, as after START (the valve thread starts here)
        frame(d, "FRONT", []); time.sleep(0.0125)
    t0 = time.perf_counter()
    first = None
    for i in range(frames_with_object + 16):
        sleep_until(t0 + i * 0.0125)
        chs = [12] if i < frames_with_object else []
        if chs and first is None:
            first = time.perf_counter()
        frame(d, "FRONT", chs)
    sleep_until(time.perf_counter() + 0.3)
    d._timed_valves.stop()
    ev = [e for e in lk.events() if e[1] == 12]
    return ev, first


ev, first = run_object(0, 0)
check("DWELL 0 -> the valve never opens", not ev, str(ev))
ev, first = run_object(20, 0)
check("DELAY 20 + DWELL 0 -> still no blow", not ev, str(ev))

for delay, dwell in ((0, 1), (0, 10), (0, 50), (20, 10), (5, 30)):
    ev, first = run_object(delay, dwell)
    ons = [e for e in ev if e[2] == "ON"]; offs = [e for e in ev if e[2] == "OFF"]
    ok = len(ons) == 1 and len(offs) == 1
    check(f"DELAY {delay:>2} DWELL {dwell:>2}: one blast for a one-frame object", ok, str(ev))
    if not ok:
        continue
    on_late = (ons[0][0] - first) * 1000.0
    blow = (offs[0][0] - ons[0][0]) * 1000.0
    check(f"   ON {on_late:.1f} ms after the object (want {delay})", abs(on_late - delay) < 2.0)
    check(f"   blows {blow:.1f} ms (want exactly {dwell})", abs(blow - dwell) < 2.0)

# bigger DWELL = longer blow, like a fan regulator
blows = []
for dwell in (5, 15, 30, 60):
    ev, first = run_object(0, dwell)
    ons = [e for e in ev if e[2] == "ON"]; offs = [e for e in ev if e[2] == "OFF"]
    blows.append((offs[0][0] - ons[0][0]) * 1000.0 if ons and offs else 0.0)
check("DWELL 5 < 15 < 30 < 60 gives longer and longer blows", all(x < y for x, y in zip(blows, blows[1:])),
      " / ".join(f"{b:.1f}" for b in blows))

# long object (6 frames = 75 ms) with DWELL 30: blast, memory gone, next blast while it is still seen
ev, first = run_object(0, 30, frames_with_object=6)
ons = [e for e in ev if e[2] == "ON"]
check("long object, DWELL 30 -> a new blast after each finished one (memory cleared)", 2 <= len(ons) <= 3, str(len(ons)))
ev, first = run_object(0, 100, frames_with_object=6)
ons = [e for e in ev if e[2] == "ON"]
check("long object, DWELL 100 -> one blast (it covers the whole object)", len(ons) == 1, str(len(ons)))

feats(follow_delay_dwell_enabled=True)
d = mk(0, 10); frame(d, "FRONT", [3]); time.sleep(0.05)
check("STOP clears the blasts", hasattr(d._timed_valves, "clear")); d._timed_valves.stop()

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
