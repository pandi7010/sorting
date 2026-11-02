"""
tools/test_delay_dwell_sim.py - do DELAY and DWELL set in PROGRAM SETTINGS -> EJECTORS really work?
Saves the values the way the EJECTORS dialog does (RecipeManager.update_scheme_field, in memory - the real
recipes.json is not touched), then runs the REAL get_ejector_config / process_frame_for_defects /
evaluate_fusion_and_fire / _capture_timed_fire with simulated 64-line frames and a fake ESP_BYTE link.
Needs switch capture_timed_eject_enabled (the test turns it on in memory only).

    python tools/test_delay_dwell_sim.py        -> must print  ALL TESTS PASSED
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

# recipes.json kept in memory (the real file is not touched); the EJECTORS dialog saves through the same call
import copy
STORE = {"active_scheme": "RICE", "schemes": {"RICE": {"algorithms": {"FRONT": ["GRAY A"]}, "ejector": {"pulse": 10.0, "delay": 2.0}}}}
m.RecipeManager.load_all = staticmethod(lambda: copy.deepcopy(STORE))
m.RecipeManager.save_all = staticmethod(lambda data: STORE.update(copy.deepcopy(data)))

def set_recipe(delay, dwell):
    """exactly what EjectorTestWindow.save_ejector_cfg does with DELAY / DWELL"""
    m.RecipeManager.update_scheme_field("RICE", "ejector", {"pulse": float(dwell), "delay": float(delay), "gap": 5.0, "count": 1})

def make():
    m.features._cache=dict(m.features.DEFAULTS, capture_timed_eject_enabled=True)
    link=Link()
    d=NS(bg_settings={}, wire_test_mode_active=False, system_running=True, capture_ejection_off=False,
         operating_led_bar=None, esp32=NS(ejector_link=link, ejector_test_active=False),
         _frame_meta={}, _last_front_decision=None, _last_rear_decision=None, current_program="RICE")
    d.get_ejector_config=lambda: MD.get_ejector_config(d)          # REAL recipe reader
    d._capture_timed_fire=lambda *a: MD._capture_timed_fire(d,*a)
    return d, link

cams={"start_pixel":19,"end_pixel":4046,"num_channels":63,"total_chutes":1,"chute_roles":{"1":"PRIMARY"},"wave_mode":"BLUE"}
algos=["GRAY A"]; aset={"GRAY A":{"channel":"GREEN","mode":"DARK DEFECT","sens":50,"scale":6}}
def mkframe(defect):
    f=np.full((H,4096,3),200,np.uint8)
    if defect: f[40:48,1380:1400,:]=20          # 8-line grain, channel 22
    return f

TB = time.perf_counter() - 100.0     # camera clock zero (one clock for every run, like a real camera)

def run(d, link, defect_frames, nframes=30, delay_for=None):
    t0=time.perf_counter()+0.02; exp={}
    for i in range(nframes):
        true_end=t0+i*H*LP
        while time.perf_counter()<true_end+0.004: pass
        meta={"t_arrive":time.perf_counter(),"cam_ts":int(round((true_end-TB)*10000)),"height":H,"rows_used":H,"source":"FRONT"}
        dec=MD.process_frame_for_defects(d,mkframe(i in defect_frames),cams,algos,aset,"FRONT")
        d._frame_meta["FRONT"]=meta; d._last_front_decision=dec
        MD.evaluate_fusion_and_fire(d,"FRONT")
        if i in defect_frames:
            exp[i]=true_end+0.004-(63-40)*LP
        if delay_for and i in delay_for:
            delay_for[i]()
    time.sleep(0.2)
    return exp

print("1. DELAY / DWELL saved in the recipe really move and stretch the blast (camera -> recipe -> valve)")
for delay, dwell in ((15, 2), (22, 3), (40, 5), (60, 10)):
    set_recipe(delay, dwell)
    d, link = make()
    exp = run(d, link, {10})
    on=[t for t,x in link.ev if x==22]; off=[t for t,x in link.ev if x==122]
    ok = len(on)==1 and len(off)==1
    check(f"DELAY {delay} DWELL {dwell}: one ON + one OFF", ok, f"{len(on)}/{len(off)}")
    if ok:
        err=(on[0]-(exp[10]+delay/1000.0))*1000
        dur=(off[0]-on[0])*1000; want=8*LP*1000+dwell
        check(f"DELAY {delay}: ON at line time + {delay} ms", abs(err)<1.5, f"(error {err:+.2f} ms)")
        check(f"DWELL {dwell}: valve open {want:.1f} ms (grain 0.9 + dwell)", abs(dur-want)<1.5, f"(measured {dur:.2f} ms)")

print("2. change DELAY while sorting -> used within 0.5 s, no restart")
set_recipe(20, 3)
d, link = make()
exp1 = run(d, link, {10})
set_recipe(45, 3)                     # operator presses SAVE in the EJECTORS dialog
time.sleep(0.6)
n1 = len(link.ev)
exp2 = run(d, link, {10})
on=[t for t,x in link.ev if x==22]
if len(on)==2:
    e1=(on[0]-(exp1[10]+0.020))*1000; e2=(on[1]-(exp2[10]+0.045))*1000
    check("first grain uses DELAY 20", abs(e1)<1.5, f"({e1:+.2f} ms)")
    check("grain after the change uses DELAY 45 (same sorting run)", abs(e2)<1.5, f"({e2:+.2f} ms)")
else:
    check("two blasts", False, str(len(on)))

print("3. DELAY too small for the camera -> PC time: fired late, counted (not silent)")
set_recipe(2, 10)
d, link = make()
run(d, link, {10})
st=d._timed_valves.stats
check("DELAY 2 ms is counted LATE/MISSED", st["late"]+st["missed"]>=1, str(st))

print("4. DWELL 0 -> still a valid blast (minimum 1 ms)")
set_recipe(22, 0)
d, link = make(); run(d, link, {10})
on=[t for t,x in link.ev if x==22]; off=[t for t,x in link.ev if x==122]
if on and off:
    dur=(off[0]-on[0])*1000
    check("DWELL 0: valve open >= 1 ms", dur>=0.9, f"({dur:.2f} ms)")
else:
    check("DWELL 0 fired", False)

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
