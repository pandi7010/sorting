"""
tools/test_timed_long_object_sim.py - capture-timed ejection with a LONG / stationary object (a wire held in front of
the camera). Uses the real capture_timing.plan_blasts + TimedValveScheduler with a fake ESP_BYTE link (no hardware).

  1. object in all 64 rows of every frame  -> one continuous blast, ON re-sent every <=100 ms (esp.ino 250 ms watchdog)
  2. object in only rows 20..40 of every frame, DWELL 3 -> valve chatters at the frame rate ("pat pat")
  3. same, DWELL 12 -> blasts overlap -> continuous again

    python tools/test_timed_long_object_sim.py      -> must print  ALL TESTS PASSED
"""
import os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import capture_timing as ct

fails = []
def check(n, c, i=""):
    print(f"  [{'OK  ' if c else 'FAIL'}] {n} {i}")
    if not c:
        fails.append(n)

ROWS = 64
FRAME_S = 0.0125                 # measured on the machine (CAM LOAD): 64 lines per 12.5 ms
LP = FRAME_S / ROWS
CH = 22

def run(rows, dwell_ms, seconds=0.6, delay_ms=13.0):
    ev = []
    def write(b):
        t = time.perf_counter()
        ev.extend((t, x) for x in b)
    cfg = dict(ct.DEFAULTS) if hasattr(ct, "DEFAULTS") else ct.load_config()
    cfg["stats_print_s"] = 0
    s = ct.TimedValveScheduler(write, cfg, log_fn=lambda *a: None)
    s.start()
    t0 = time.perf_counter() + 0.02
    n = int(seconds / FRAME_S)
    for i in range(n):
        frame_end = t0 + (i + 1) * FRAME_S
        while time.perf_counter() < frame_end + 0.004:      # frame reaches the PC 4 ms after its last line
            pass
        s.add(ct.plan_blasts([CH], {CH: rows}, ROWS, frame_end, LP, delay_ms, dwell_ms, cfg))
    time.sleep(0.1)
    s.stop()
    time.sleep(0.05)
    on = [t for t, x in ev if x == CH]
    off = [t for t, x in ev if x == CH + 100]
    return on, off, t0

print("1. wire in all 64 rows, DWELL 3")
on, off, t0 = run((0, ROWS - 1), 3.0)
offs_during = [t for t in off if t < t0 + 0.6]
check("valve stays open while the wire is there (no OFF in between)", len(offs_during) == 0, f"{len(offs_during)} OFF")
gaps = [b - a for a, b in zip(on, on[1:])]
check("ON re-sent at least every 110 ms (esp.ino watchdog = 250 ms)", on and max(gaps or [0]) < 0.11,
      f"{len(on)} ON bytes, max gap {max(gaps or [0]) * 1000:.0f} ms")

print("1b. switch the fix off (continue_margin_ms 0): the old chatter comes back")
_old = ct.DEFAULTS["continue_margin_ms"]; ct.DEFAULTS["continue_margin_ms"] = 0.0
on, off, t0 = run((0, ROWS - 1), 3.0)
ct.DEFAULTS["continue_margin_ms"] = _old
check("without the fix a held wire chatters at DELAY 13 (frame arrives after the blast ended)", len(off) >= 20, f"{len(off)} OFF")

print("2. wire seen only in rows 20..40, DWELL 3")
on, off, t0 = run((20, 40), 3.0)
check("valve chatters (many ON/OFF) - the 'pat pat' on the machine", len(off) >= 20, f"{len(off)} OFF in 0.6 s")

print("3. wire seen only in rows 20..40, DWELL 12")
on, off, t0 = run((20, 40), 12.0)
offs_during = [t for t in off if t < t0 + 0.6]
check("DWELL 12 bridges the frames -> continuous", len(offs_during) == 0, f"{len(offs_during)} OFF")

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
