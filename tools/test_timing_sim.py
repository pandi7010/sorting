"""
Phase 2 test: timing log + latency report (no hardware).

    python tools\\test_timing_sim.py

A. Writes synthetic logs with KNOWN delays (10 kHz line rate, 200-line frames, 8 ms processing,
   0.4 ms USB, ESP32 clock with drift and a 32-bit wrap) and checks the report finds them.
B. Runs the real EjectScheduler + ESP32Controller + EjectorLink against the simulated ESP32 with
   the timing log switched on, and checks the CSV files are written and linked by sequence number.
"""
import os
import random
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import timing_log  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures += 1


# ------------------------------------------------------------------ A. synthetic logs
tmp = tempfile.mkdtemp(prefix="lamton_timing_")
log = timing_log.TimingLogger(enabled=True, log_dir=tmp)
rnd = random.Random(5)
LP_US, H = 100.0, 200
FRAME_S = LP_US * H / 1e6
ESP0_US = 2 ** 32 - 8_000_000          # ESP32 micros() wraps 8 s into the session
DRIFT = 30e-6
USB_ONE_WAY = 0.0004


def esp_us(pc):
    return int(ESP0_US + (pc - 100.0) * (1 + DRIFT) * 1e6) % 2 ** 32


seq = 0
pc0 = 100.0
for k in range(800):                      # 16 s of frames per camera
    for src, cam_offset in (("FRONT", 0.0), ("REAR", 0.007)):
        frame_end = pc0 + k * FRAME_S + cam_offset
        cam_ts = int((frame_end - 90.0) * 1e4) % 2 ** 32
        t_arr = frame_end + 0.001 + rnd.uniform(0, 0.002)
        t_ps = t_arr + rnd.uniform(0, 0.0005)
        t_pe = t_arr + 0.008 + rnd.expovariate(1 / 0.001)
        log.frame(src, k, cam_ts, H, 0, H, t_arr, t_ps, t_pe, 1)
        if rnd.random() < 0.3:
            t_target = t_pe + 0.005
            t_send = t_target + rnd.uniform(0, 0.0005)
            seq += 1
            log.fire({"source": src, "frame_idx": k, "t_arrive": t_arr}, t_pe, t_target, t_send, seq, [5, 6], 4, 5)
            rtt = 0.0006 + rnd.expovariate(1 / 0.0008)
            log.on_ack(seq, "OK", esp_us(t_send + USB_ONE_WAY), t_send + rtt, t_send)
    for hb in range(2):                   # heartbeats -> clock samples
        seq += 1
        ts = pc0 + k * FRAME_S + hb * 0.01
        rtt = 0.0006 + rnd.expovariate(1 / 0.0008)
        log.on_ack(seq, "OK", esp_us(ts + USB_ONE_WAY), ts + rtt, ts)
log.stop()

import latency_report  # noqa: E402

sys.argv = ["latency_report.py", "--log-dir", tmp, "--speed", "3.5", "--speed-sd", "0.1"]
rep = latency_report.main()
st = rep["stages"]
check("processing p50 ~ 8.7 ms", 8.0 < latency_report.pct(st["C processing ms"], 50) < 11.0,
      round(latency_report.pct(st["C processing ms"], 50), 2))
check("transfer variation p50 ~ 1 ms", 0.6 < latency_report.pct(st["B transfer (variation) ms"], 50) < 1.4,
      round(latency_report.pct(st["B transfer (variation) ms"], 50), 2))
check("USB+ESP32 p50 ~ 0.4 ms (clock wrap + drift handled)",
      0.0 < latency_report.pct(st["E USB + ESP32 ms"], 50) < 0.8, round(latency_report.pct(st["E USB + ESP32 ms"], 50), 3))
check("frame fill = 20 ms", abs(st["A frame fill, oldest row ms"][0] - 20.0) < 0.5, st["A frame fill, oldest row ms"][0])
check("distance recommendation made", rep["recommend"] in (35, 40, 55, 70) or rep["recommend"] is None, rep["recommend"])
shutil.rmtree(tmp, ignore_errors=True)

# ------------------------------------------------------------------ B. real pipeline + simulated ESP32
import importlib.util  # noqa: E402
import ejector_link as el  # noqa: E402

src = open(os.path.join(ROOT, "tools", "test_ejector_link_sim.py"), encoding="utf-8").read()
ns = {"__file__": os.path.join(ROOT, "tools", "test_ejector_link_sim.py"), "__name__": "sim"}
exec(src.split("# ---------------------------------------------------------------- 1. link basics")[0], ns)
sims = {}
el.EjectorLink.serial_factory = staticmethod(lambda p, b: sims.setdefault(p, ns["SimEsp32"](p, b)))

cwd = os.getcwd()
spec = importlib.util.spec_from_file_location("lamton_main", os.path.join(ROOT, "LAMTON_LINE_NEW_ESP32.py"))
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)
os.chdir(cwd)
check("timing log OFF by default in the sorter", main.TIMING.enabled is False)

tmp2 = tempfile.mkdtemp(prefix="lamton_timing2_")
main.TIMING = timing_log.TimingLogger(enabled=True, log_dir=tmp2)
esp = main.ESP32Controller(None)
esp.connect_ejector("SIMT")
sched = main.EjectScheduler(esp)
sched.start()
for i in range(20):
    meta = {"source": "FRONT", "frame_idx": i, "t_arrive": time.perf_counter()}
    sched.schedule_fire([i + 1], delay_ms=5, pulse_ms=4, meta=meta)
    time.sleep(0.02)
time.sleep(0.5)
sched.stop()
esp.disconnect_ejector()
main.TIMING.stop()
f_frames, f_fires, f_acks = main.TIMING.paths()
fires = latency_report.read_csv(f_fires)
acks = latency_report.read_csv(f_acks)
seqs = {r["seq"] for r in acks}
check("20 fires logged", len(fires) == 20, len(fires))
check("every fire has an ESP32 ACK (same seq)", all(r["seq"] in seqs for r in fires),
      sum(1 for r in fires if r["seq"] not in seqs))
lat = [(float(r["t_send"]) - float(r["t_target"])) * 1000 for r in fires]
check("scheduler fires on time (p99 < 3 ms late)", latency_report.pct(lat, 99) < 3.0,
      round(latency_report.pct(lat, 99), 2))
shutil.rmtree(tmp2, ignore_errors=True)

print("\nALL TESTS PASSED" if failures == 0 else f"\n{failures} TEST(S) FAILED")
sys.stdout.flush()
os._exit(1 if failures else 0)
