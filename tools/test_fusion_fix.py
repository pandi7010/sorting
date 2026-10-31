"""
Proves the Phase 1 fix of the "50 ms long-blast" bug (no hardware needed).

    python tools\\test_fusion_fix.py

Replays frame sequences through MainDashboard.evaluate_fusion_and_fire and records
what would be sent to the ejectors.
"""
import os
import sys
import importlib.util
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
cwd = os.getcwd()
spec = importlib.util.spec_from_file_location("lamton_main", os.path.join(ROOT, "LAMTON_LINE_NEW_ESP32.py"))
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)
os.chdir(cwd)

failures = 0


def check(name, cond, detail=""):
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures += 1


def make_dashboard():
    fired = []
    sched = SimpleNamespace(schedule_fire=lambda chs, delay_ms, pulse_ms, force_update=False, meta=None:
                            fired.append((tuple(chs), pulse_ms, force_update)))
    d = SimpleNamespace(
        _last_front_decision=None, _last_rear_decision=None,
        wire_test_mode_active=False, system_running=True,
        get_ejector_config=lambda: (5.0, 4.0),       # delay 5 ms, pulse 4 ms (W320 recipe)
        eject_scheduler=sched, _frame_interval_ms={"FRONT": 20.0, "REAR": 20.0},
    )
    return d, fired


def dec(channels):
    return {"valid": bool(channels), "channels": channels}


def frame(d, source, channels):
    if source == "FRONT":
        d._last_front_decision = dec(channels)
    else:
        d._last_rear_decision = dec(channels)
    main.MainDashboard.evaluate_fusion_and_fire(d, source)


# 1. One defect seen by FRONT, then REAR frames arrive (the bug scenario)
d, fired = make_dashboard()
frame(d, "FRONT", [20])
frame(d, "REAR", [])
frame(d, "FRONT", [])
frame(d, "REAR", [])
print("   blasts:", fired)
check("single defect -> exactly one blast", len(fired) == 1, len(fired))
check("single defect -> normal 4 ms pulse (not 50 ms)", fired and fired[0][1] == 4.0)

# 2. Both cameras see the same defect: each fires once with the normal pulse
d, fired = make_dashboard()
frame(d, "FRONT", [20])
frame(d, "REAR", [20])
frame(d, "FRONT", [])
print("   blasts:", fired)
check("front+rear same defect -> 2 normal pulses, no 50 ms", [f[1] for f in fired] == [4.0, 4.0], fired)

# 3. Real long object: FRONT sees channel 20 in 3 consecutive frames
d, fired = make_dashboard()
for _ in range(3):
    frame(d, "FRONT", [20])
    frame(d, "REAR", [])
print("   blasts:", fired)
check("long object -> first normal, then bridged pulses", [f[1] for f in fired] == [4.0, 24.0, 24.0], fired)
check("bridged pulse = frame interval + pulse (20 + 4 = 24 ms), not 50", max(f[1] for f in fired) == 24.0)

print("\nALL TESTS PASSED" if failures == 0 else f"\n{failures} TEST(S) FAILED")
sys.exit(1 if failures else 0)
