# Phase 2 — Measurement (timing logs, latency report, ball speed)

**Status:** code complete; **tested in simulation only**. Needs cameras + ESP32 to produce real numbers.
**Default:** OFF. Nothing changes until you switch it on.

## What it does (simple)
To decide 40 mm or 55 mm, we need two real numbers:
1. **How long** a defect takes from camera line to valve (latency).
2. **How fast** the product falls (speed).
Distance needed ≈ speed × latency. Example: 3.5 m/s × 15 ms = 52.5 mm.

## Switch on the timing log
Edit `features_config.json` → `"timing_log_enabled": true` → restart the sorter. While it runs it writes
`logs/timing_<date_time>_frames.csv`, `_fires.csv`, `_acks.csv` (a few MB per minute).
Switch it back to `false` when finished.

What is recorded (no effect on sorting; the files are written by a background thread):
- every camera frame: camera timestamp, frame height, arrival time on the PC, processing start/end;
- every ejector command: when it was scheduled, its target time, when it was sent, its sequence number;
- every ESP32 acknowledgement: the ESP32's own clock (micros) → tells us when the ESP32 switched the valve.

## Make the report
```
python tools\latency_report.py
```
It prints and saves `logs/latency_report_<date_time>.md` with:
- the real line rate and frame time per camera, and dropped frames;
- each stage (frame fill, transfer, processing, scheduler, USB + ESP32) as p50/p95/p99/max;
- the **decision latency** (newest and oldest line of a frame);
- a **35/40/55/70 mm table**: OK / TOO SHORT, and the expected position error now (Phase 1) and with Phase 3;
- a whole-object table (cashew, almond…): time from the object's last line to its centre reaching the nozzle.

## Ball speed test (sorter program CLOSED, feeder OFF, air OFF)
```
python tools\ball_speed.py --seconds 60
```
Wait for **READY**, then drop a **10 mm ball** 20–50 times across the chute width. The result (speed per drop, mean, variation) is saved to `logs/ball_speed_<date_time>.json`, and the latency report uses it automatically.
The tool only **reads** the cameras (it converts images for itself; no setting is changed).
Per-drop accuracy is about ±1 line (±3–4 %), so use many drops; the mean is accurate.

## Simulation results (this PC, no hardware)
| Test | Result |
|---|---|
| `tools/test_timing_sim.py` | report recovered injected values: processing 8.72 ms (true ≈ 8.7), transfer variation 1.02 ms (true 1.0), USB 0.34 ms (true 0.4), frame fill 20.0 ms; ESP32 clock drift + 32-bit wrap handled; the real scheduler → simulated ESP32 path logged 20/20 commands, each matched to its ACK |
| `tools/ball_speed.py --simulate` | mean 3.488 m/s vs true 3.484 m/s (0.1 % error); ball width 9.96 mm vs 10 mm |
| `tools/test_object_stream.py` | 500 objects in → 500 out, 0 duplicates, exact sizes, frame heights 200/32/16 |

## Needs real hardware
Real latency numbers; real speed; whether `CameraImageProcessEx` behaves as expected in the ball tool; transfer timing on your network card.
