"""
tools/test_capture_timed_eject_sim.py - end-to-end simulation of capture-timed ejection (no camera, no ESP32).
Runs the REAL MainDashboard.process_frame_for_defects / evaluate_fusion_and_fire / _capture_timed_fire
with simulated 64-line frames (8842 lines/s) and a fake ESP_BYTE link that records every byte with its time.

    python tools/test_capture_timed_eject_sim.py        -> must print  ALL TESTS PASSED
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
LP=1/8842.0; H=64
fails=[]
def check(n,c,i=""):
    print(f"  [{'OK  ' if c else 'FAIL'}] {n} {i}");  (None if c else fails.append(n))

class Link:
    is_open=True; mode="ESP_BYTE"
    def __init__(s): s.ev=[]
    def _write(s,b): t=time.perf_counter(); s.ev += [(t,x) for x in b]; return True

def make(feature_on):
    m.features._cache=dict(m.features.DEFAULTS, capture_timed_eject_enabled=feature_on)
    link=Link()
    d=NS(bg_settings={}, wire_test_mode_active=False, system_running=True, capture_ejection_off=False,
         operating_led_bar=None, esp32=NS(ejector_link=link, ejector_test_active=False),
         _frame_meta={}, _last_front_decision=None, _last_rear_decision=None,
         get_ejector_config=lambda:(22.0,3.0))
    d._capture_timed_fire=lambda *a: MD._capture_timed_fire(d,*a)
    return d, link

cams={"start_pixel":19,"end_pixel":4046,"num_channels":63,"total_chutes":1,"chute_roles":{"1":"PRIMARY"},"wave_mode":"BLUE"}
algos=["GRAY A"]; aset={"GRAY A":{"channel":"GREEN","mode":"DARK DEFECT","sens":50,"scale":6}}
def mkframe(defect):
    f=np.full((H,4096,3),200,np.uint8)
    if defect: f[40:48,1380:1400,:]=20
    return f

def run(feature_on, defect_frame=10, nframes=30):
    d,link=make(feature_on)
    rnd=random.Random(3); t0=time.perf_counter()+0.02; cam0=123456
    expect=None; proc=[]
    for i in range(nframes):
        true_end=t0+i*H*LP
        while time.perf_counter()<true_end+0.004: pass       # frame arrives 4 ms after capture end
        time.sleep(rnd.choice([0,0,0,0.001,0.006]))           # PC jitter
        meta={"t_arrive":time.perf_counter(),"cam_ts":cam0+int(round(i*H*LP*10000)),"height":H,"rows_used":H,"source":"FRONT"}
        fr=mkframe(i==defect_frame)
        ts=time.perf_counter(); dec=MD.process_frame_for_defects(d,fr,cams,algos,aset,"FRONT"); proc.append(time.perf_counter()-ts)
        d._frame_meta["FRONT"]=meta; d._last_front_decision=dec
        t_eval=time.perf_counter()
        MD.evaluate_fusion_and_fire(d,"FRONT")
        if i==defect_frame:
            expect=true_end+0.004-(63-40)*LP+0.022; dec_seen=dec; d._t_eval=t_eval
    time.sleep(0.1)
    return d,link,expect,dec_seen,proc

print("A. switch OFF -> old behaviour (ON byte immediately, recipe delay ignored)")
d,link,exp,dec,proc=run(False)
on=[t for t,x in link.ev if x==22]
check("channel 22 detected", 22 in dec["channels"], str(dec["channels"]))
check("old path: no row tracking", "row_extents" not in dec)
check("old path: ON the moment the frame is evaluated (recipe DELAY not used)", bool(on) and abs(on[0]-d._t_eval) < 0.002,
      f"(ON {(on[0]-exp)*1000:+.1f} ms vs the correct time; depends only on PC speed)" if on else "")

print("B. switch ON -> capture-timed")
d,link,exp,dec,proc=run(True)
check("row extents rows 40..47", dec.get("row_extents",{}).get(22)==(40,47), str(dec.get("row_extents")))
on=[t for t,x in link.ev if x==22]; off=[t for t,x in link.ev if x==122]
check("exactly one ON and one OFF on ch22", len(on)==1 and len(off)==1, f"{len(on)}/{len(off)}")
if on:
    check("ON within 1.5 ms of (line time + DELAY)", abs(on[0]-exp)<0.0015, f"(err {(on[0]-exp)*1000:.2f} ms)")
if on and off:
    check("blast = 8 lines + 3 ms = 3.9 ms", abs((off[0]-on[0])-(8*LP+0.003))<0.0015, f"({(off[0]-on[0])*1000:.2f} ms)")
check("watchdog W armed", any(x==ord('W') for _,x in link.ev))
check("no other channel fired", all(x in (22,122,ord('W')) for _,x in link.ev), str(set(x for _,x in link.ev)))
st=d._timed_valves.stats; check("counted on time, no late/missed", st["on_time"]==1 and st["late"]==0 and st["missed"]==0, str(st))
print(f"   processing time per 64-line frame: median {sorted(proc)[len(proc)//2]*1000:.1f} ms (this PC)")

print("C. stop clears queued blasts")
d,link=make(True)
d._timed_valves=None
meta={"t_arrive":time.perf_counter(),"cam_ts":1000,"height":H,"rows_used":H}
d._frame_meta["FRONT"]=meta
dec=MD.process_frame_for_defects(d,mkframe(True),cams,algos,aset,"FRONT"); d._last_front_decision=dec
d.get_ejector_config=lambda:(200.0,3.0)     # far in future
MD.evaluate_fusion_and_fire(d,"FRONT")
d._timed_valves.clear(); time.sleep(0.3)
check("blast cleared before firing", not any(x==22 for _,x in link.ev))
print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
