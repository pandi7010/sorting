# Phase 3 — Low-latency, line-synchronous firing (PREPARED, DISABLED)

## 0. Status 2 Nov 2026 (read this first)

- **Probe done** (machine PC): MV-GELM44C, 8,842 lines/s, trigger modes include **LineTrig**; inputs 0/1 = **ROTARYENC (RS422)**, input 2 = **opto trigger**; outputs = strobe + GP. So **Plan A works**: ESP32 GPIO 25 -> **AM26LS31** RS-422 driver -> camera encoder input 0 (+/-), *or* GPIO 25 -> TC4427 at 12 V -> opto input 2 with `LineTrig`. Check the camera manual for the maximum input frequency (need ~9 kHz) before choosing.
- The machine runs **`esp/esp.ino`** (byte protocol), wired DATA 13 / CLOCK 14 / LATCH 12. v3.0's legacy profile is DATA 14 / CLOCK 13 (swapped!). Use **`ESP32_63CH_Ejector_v3_1`** (= v3.0 + PIN_PROFILE 3 for this wiring, default). Not compiled / not hardware-tested yet. After the upload check the channel order with the EJECTOR TEST PANEL (v3 has a REMAP table if channels come out in a different order).
- The firmware folders deleted by commit 4f3f333 (31 Oct) were restored from git on 2 Nov (byte-identical).
- Until the line trigger is wired, use **`capture_timed_eject_enabled`** (CAPTURE_TIMED_EJECT_TAMIL.md): same idea (fire at line time + DELAY) but timed on the PC from the camera timestamp.

**Status:** code + firmware written; **simulation tested only**. It stays OFF until the camera probe confirms
trigger support **and** you switch it on. With everything OFF, the sorter runs exactly like Phase 1
(proved: identical decisions to the original code on 150 random frames).

## 1. The idea in one example
The ESP32 makes the camera take a line on every clock tick and counts the ticks (line numbers).
The PC reports: "front camera, defect at line 100 000, channel 21, 5 lines long".
The ESP32 knows the nozzle is D = 221 lines below the camera (55 mm at 3.5 m/s, 15 000 lines/s, minus 1 ms of valve time),
so it opens valve 21 exactly at line 100 221 - 3 (lead) until line 100 221 + 5 + 3 (tail).
**PC and USB delays no longer move the blast.** If the PC's message arrives after line 100 218 (+2 lines of tolerance),
the ESP32 does **not** fire in the wrong place: it counts the command as **LATE**.

## 2. What is prepared

| Piece | File | Default |
|---|---|---|
| Firmware v3.0 = v2.0 + line clock, line counter, FIRE at L + D, LATE counter | `ESP32_63CH_Ejector_v3_0/` | clock OFF → behaves like v2.0 |
| PC protocol: CONFIG_LINE, CLOCK_START/STOP, FIRE_LINE, LINE_STATUS | `ejector_link.py` | only used by line sync |
| Line-sync controller: D per camera, frame → line numbering (camera timestamps, survives dropped frames) | `line_sync.py`, `line_sync_config.json` | **OFF** |
| Defect row position (first/last row per channel) | `LAMTON_LINE_NEW_ESP32.py` | **OFF** (`row_tracking_enabled`) |
| Calibrated pixel → channel map + boundary overlap margin | `channel_map.py`, `tools/channel_calibration.py` | **OFF** (`channel_calibration_enabled`, `boundary_margin_px` 0) |
| Nozzle distance setting 35–70 mm, default **55 mm** | `line_sync_config.json` → `nozzle_distance_mm` (clamped 35–70) | 55 |
| Separate D for front and rear (`rear_offset_mm`) | `line_sync_config.json` | 0 mm |
| Line rate / exposure / frame height per recipe | `tools/recipe_camera_timing.py` (writes `recipes.json` → `camera_timing`, with a backup) | none stored |
| Apply line rate / exposure / frame height / trigger to the cameras | `tools/camera_apply_timing.py` — **dry run unless `--apply`; always backs up first** | never automatic |
| Product presets (sizes, suggested line rate, frame height) | `product_presets.json` | suggestions only |
| Whole-object timing (last line → centre at nozzle) | `tools/latency_report.py` table | — |

## 3. Switching it on (only after tomorrow's probe + Phase 2 measurements)
1. Probe shows the trigger capability (see section 5) → wire the trigger driver (TC4427 or RS-422 driver, `PHASE1_GUIDE.md` 3.5) from ESP32 **GPIO 25** to the camera input.
2. Flash `ESP32_63CH_Ejector_v3_0` (new folder; v2.0 stays untouched). Bench test in Serial Monitor:
   `CLOCK:10000:1:32` → logic analyser on GPIO 25 must show exactly 10.000 kHz; `LINE?` shows the counter; `CLOCK_STOP`.
3. Cameras: `python tools\camera_apply_timing.py --line-rate 10000 --height 32 --trigger LINE --trigger-input <n>` (dry run), then add `--apply` (backup saved first).
4. Measure the speed (Phase 2 ball test) → put it in `line_sync_config.json` → `speed_mps`; set `line_rate_hz` and `frame_height` to the camera values; set `nozzle_distance_mm`.
5. `line_sync_config.json` → `"camera_trigger_confirmed": true`; `features_config.json` → `"line_sync_enabled": true`.
6. Start the sorter → console prints `[LINE SYNC] ...`. It is ACTIVE only after START; otherwise it says why it is OFF.
7. Air OFF first: logic analyser on GPIO 27 (debug) vs a planted defect; then the drop test; adjust `rear_offset_mm`, `lead_lines`, `tail_lines`.
8. Watch the **LATE** counter (`LINE?` in the Serial Monitor, or the link statistics). LATE > 0.1 % → move the nozzle bar further or reduce the frame height.

**Undo at any time:** `"line_sync_enabled": false` → Phase 1 firing; cameras back with `tools\camera_restore.py`.

## 4. Exposure and lighting
A line must be exposed before the next starts: max exposure ≈ 95 % of the line period.

| Line rate | Line period | Max exposure | Light needed vs FRONT 100 µs today |
|---|---|---|---|
| 10 000 /s | 100 µs | 94 µs | 1.06× |
| 12 000 /s | 83 µs | 78 µs | 1.28× |
| 15 000 /s | 67 µs | 62 µs | 1.61× |
| 18 000 /s | 56 µs | 52 µs | 1.93× |
| 20 000 /s | 50 µs | 47 µs | **2.15×** |

REAR is at 65 µs today, so it needs 1.4× at 20 000/s. For dhall insect holes at 20 000 lines/s, plan **about 2× brighter front lighting**. Raising gain instead adds noise, which is bad for small holes.
Along-flow resolution at 3.5 m/s: 0.35 mm/line at 10 kHz, 0.23 mm at 15 kHz, 0.175 mm at 20 kHz (across: 0.085 mm/px).

## 5. What the camera probe decides (send me the output)

| Probe shows | Meaning | What changes |
|---|---|---|
| `trigger_modes_supported` contains an **encoder / ROTARYENC** mode, an input that can be **ROTARYENC_INPUT** | true line trigger (one pulse = one line) | **Plan A** as prepared: `trigger_mode` LINE, `--trigger LINE` |
| encoder input but format **RS422** only | needs a differential driver | AM26LS31 driver; maybe `trigger_mode` QUAD if it counts A/B quadrature |
| only **external trigger** (mode 2) | one pulse = one frame of N lines at the camera's own rate | **Plan B**: `trigger_mode` FRAME, `--trigger FRAME`; the camera line rate must be set a little **faster** than the ESP32's; small gaps between frames |
| input format **OPTO-COUPLER** | 5–24 V input | TC4427 at 12 V (or NPN + pull-up, falling edge) |
| no hardware trigger at all | cameras keep free-running | Plan C: camera STROBE output → ESP32 input (needs a firmware addition; tell me) |

## 6. Whole objects (cashews, almonds)
A whole-object decision (e.g. a CNN on a crop) can start only after the object's **last** line.
Time left until its **centre** reaches the nozzle = (distance − length/2) / speed − valve time.
Example: cashew 25 mm, 55 mm, 3.5 m/s → (55 − 12.5) / 3.5 − 1 = **11.1 ms**. At 40 mm only 6.9 ms.
The latency report prints this for every product.

## 7. Test results (simulation, this PC)
`tools/test_line_sync_sim.py` — all pass:
- switches OFF → **identical channels to the original code** (150 random frames, GRAY dark/light, SIZE, HSV, FALLBACK, combined);
- row tracking ON → same channels; a planted spot at rows 57–65 reported as rows 57–65 in channel 21;
- uniform calibration = old edges (0.0 px difference), same channels; a 40 px margin fires both channels for a spot on the 20|21 edge;
- D = 221 lines for 55 mm / 3.5 m/s / 15 kHz / 1 ms; rear offset gives a smaller rear D;
- frame → line numbering correct across a dropped frame and a camera-timestamp wrap;
- simulated v3.0 ESP32: FIRE_LINE carries the defect's own line; the valve window starts at L + D − lead; a late command is **not fired** and is counted as LATE.

Firmware v3.0 compiles with 0 warnings on ESP32 core 2.0.17 and 3.0.7.

**Needs real hardware:** trigger output timing (logic analyser), camera trigger modes, frame timestamps in trigger mode, LATE rate at 40/55 mm, timer ISR load at 20 kHz.
