"""
tools/test_capture_timing_sim.py - simulation test for capture_timing.py (no camera, no ESP32, no Qt).

    python tools/test_capture_timing_sim.py        -> must print  ALL TESTS PASSED
"""
import os
import random
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import capture_timing as ct  # noqa: E402

FAILS = []


def check(name, cond, info=""):
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name} {info}")
    if not cond:
        FAILS.append(name)


CFG = dict(ct.DEFAULTS)
CFG["stats_print_s"] = 0
LP = 1.0 / 8842.0


def test_clock_jitter_and_wrap():
    print("1. CaptureClock: PC delay spikes and 32-bit timestamp wrap must not move the capture time")
    clk = ct.CaptureClock(CFG)
    rnd = random.Random(1)
    H = 64
    frame_s = H * LP
    cam0 = 0xFFFFFFFF - 50          # wraps after a few frames
    true_end0 = 100.0
    transfer = 0.004
    worst = 0.0
    lp_est = None
    for i in range(400):
        true_end = true_end0 + i * frame_s
        cam_raw = (cam0 + int(round(i * frame_s * 10000))) & 0xFFFFFFFF
        spike = rnd.choice([0, 0, 0, 0.0005, 0.002, 0.015])     # PC / network jitter
        meta = {"t_arrive": true_end + transfer + spike, "cam_ts": cam_raw, "height": H, "rows_used": H}
        fe, lp_est = clk.update("FRONT", meta)
        if i > 20:
            err = abs((fe - transfer) - true_end)
            worst = max(worst, err)
    check("capture time error < 0.15 ms despite 15 ms spikes", worst < 0.00015, f"(worst {worst*1000:.3f} ms)")
    check("line period from camera clock ~113.1 us", abs(lp_est - LP) < 2e-6, f"({lp_est*1e6:.2f} us)")

    clk2 = ct.CaptureClock(CFG)
    fe, lp = clk2.update("REAR", {"t_arrive": 5.0, "cam_ts": None, "height": 64})
    check("no camera timestamp -> falls back to arrival time", fe == 5.0 and abs(lp - LP) < 1e-9)


def test_plan_blasts():
    print("2. plan_blasts: row -> time, object length -> blast length")
    b = ct.plan_blasts([22], {22: (40, 47)}, 64, 1000.0, LP, 22.0, 3.0, CFG)
    ch, t_on, t_off = b[0]
    exp_on = 1000.0 - (63 - 40) * LP + 0.022
    check("ON time = row time + DELAY", abs(t_on - exp_on) < 1e-9, f"(ON at +{(t_on-1000)*1000:.3f} ms)")
    check("blast = 8 lines + 3 ms PULSE", abs((t_off - t_on) - (8 * LP + 0.003)) < 1e-9,
          f"({(t_off-t_on)*1000:.3f} ms)")
    CFG0 = dict(CFG, continue_margin_ms=0.0)      # without the 3 Nov "object continues" hold
    b2 = ct.plan_blasts([5], {}, 64, 0.0, LP, 10.0, 0.0, CFG0)
    check("no row info -> whole frame used", abs((b2[0][2] - b2[0][1]) - 64 * LP) < 1e-9)
    b3 = ct.plan_blasts([5], {5: (0, 63)}, 64, 0.0, 0.01, 10.0, 5.0, CFG0)
    check("blast capped at max_pulse_ms", abs((b3[0][2] - b3[0][1]) - 0.2) < 1e-9)
    b4 = ct.plan_blasts([5], {5: (60, 63)}, 64, 0.0, LP, 10.0, 3.0, dict(CFG, continue_margin_ms=8.0))
    check("object touching the last row -> open until the next frame can arrive",
          abs(b4[0][2] - (64 * LP + 0.008)) < 1e-9, f"(OFF at +{b4[0][2]*1000:.2f} ms)")
    # earlier row must fire earlier
    b4 = ct.plan_blasts([1, 2], {1: (0, 0), 2: (63, 63)}, 64, 0.0, LP, 10.0, 1.0, CFG)
    check("row 0 fires 63 lines before row 63", abs((b4[1][1] - b4[0][1]) - 63 * LP) < 1e-9)


class Recorder:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def __call__(self, data):
        t = time.perf_counter()
        with self.lock:
            for x in data:
                self.events.append((t, x))
        return True


def test_scheduler_timing():
    print("3. TimedValveScheduler: real-time ON/OFF bytes")
    rec = Recorder()
    s = ct.TimedValveScheduler(rec, CFG)
    s.start()
    now = time.perf_counter()
    s.add([(10, now + 0.030, now + 0.035),      # plain blast
           (20, now + 0.040, now + 0.050),      # two overlapping blasts on ch 20 -> one long blast
           (20, now + 0.045, now + 0.060),
           (30, now + 0.070, now + 0.074),      # two separate blasts on ch 30 -> two blasts
           (30, now + 0.090, now + 0.094)])
    time.sleep(0.15)
    ev = list(rec.events)

    def times(byte):
        return [t for t, x in ev if x == byte]
    on10, off10 = times(10), times(110)
    check("ch10 ON once, OFF once", len(on10) == 1 and len(off10) == 1)
    if on10 and off10:
        check("ch10 ON within 2 ms of plan", abs(on10[0] - (now + 0.030)) < 0.002,
              f"(err {(on10[0]-(now+0.030))*1000:.2f} ms)")
        check("ch10 blast ~5 ms", abs((off10[0] - on10[0]) - 0.005) < 0.002,
              f"({(off10[0]-on10[0])*1000:.2f} ms)")
    on20, off20 = times(20), times(120)
    check("ch20 overlapping blasts merged: 1 ON, 1 OFF", len(on20) == 1 and len(off20) == 1)
    if on20 and off20:
        check("ch20 OFF at the later blast's end (~60 ms)", abs(off20[0] - (now + 0.060)) < 0.002)
    on30, off30 = times(30), times(130)
    check("ch30 separate blasts NOT merged: 2 ON, 2 OFF", len(on30) == 2 and len(off30) == 2)
    if len(off30) == 2:
        check("ch30 first blast OFF at ~74 ms (not held to 94 ms)", abs(off30[0] - (now + 0.074)) < 0.002)
    check("nothing left ON", not s.active_channels())
    check("merge counted", s.stats["merged"] == 1, f"({s.stats['merged']})")

    print("4. LATE and MISSED")
    rec.events.clear()
    now = time.perf_counter()
    s.add([(40, now - 0.010, now - 0.002),      # window already over -> MISSED, no byte
           (41, now - 0.003, now + 0.010)])     # start late, window open -> fires now, LATE
    time.sleep(0.03)
    ev = list(rec.events)
    check("missed blast sends nothing", not any(x in (40, 140) for _, x in ev))
    check("late blast fires immediately", any(x == 41 for _, x in ev) and any(x == 141 for _, x in ev))
    check("counters: 1 missed, 1 late", s.stats["missed"] == 1 and s.stats["late"] == 1, str(s.stats))

    print("5. clear() switches everything off")
    rec.events.clear()
    now = time.perf_counter()
    s.add([(50, now + 0.001, now + 0.100)])
    time.sleep(0.01)
    s.clear()
    time.sleep(0.12)
    ev = list(rec.events)
    check("clear sends OFF for ch50", any(x == 150 for _, x in ev))
    check("no second ON after clear", len([1 for _, x in ev if x == 50]) == 1)
    s.stop()


if __name__ == "__main__":
    print("capture_timing simulation test")
    test_clock_jitter_and_wrap()
    test_plan_blasts()
    test_scheduler_timing()
    print()
    if FAILS:
        print(f"{len(FAILS)} TEST(S) FAILED: {FAILS}")
        sys.exit(1)
    print("ALL TESTS PASSED")
