# Tomorrow's checklist (hardware day)

Do the steps **in this order**. After each step, write down the result (or take a photo/screenshot) — the "Send me" line says what I need.
**Main air CLOSED and chute EMPTY until step 8 (T8).** If anything goes wrong → **section R (roll back)**.

---

## Step 0 — Which PC, which folder (5 min)
1. The sorter program checks the PC's motherboard ID (licence). On the development PC it will say **"Access Denied"** — that is expected. Use the **machine PC** (the one that normally runs the sorter).
2. On the machine PC, **do not overwrite** the working folder `D:\LAMTON_V1`. Copy the new version to a **new folder**, e.g. `D:\LAMTON_V2_TEST`
   (source: this project folder, or the backup `D:\LAMTON_BACKUP_2026-10-15` on the USB disk).
3. Open a command window in the new folder (click the address bar, type `cmd`, Enter). All commands below are typed there.
4. Check Python: `python --version` → should be 3.10–3.12.

**Send me:** which PC you use and the folder name.

---

## Step 1 — Camera probe (sorter program CLOSED) (5 min)
```
python tools\camera_probe.py
```
- It only **reads** the cameras and saves a **backup** of all their settings in `camera_backups\<date_time>\`.
- Optional, after the first run: `python tools\camera_probe.py --probe-max-rate` (sets line rate to maximum for 3 s to measure it, then puts it back and checks it).

**Send me:** the file `camera_backups\<date_time>\probe_report.txt` (or a photo of the SUMMARY at the end).
**If it fails:** "Cameras found: 0" → sorter still running, camera power, or network cable. "Could not open camera" → close the sorter / MVDCP.

---

## Step 2 — /OE pin check on the drive board (power OFF, multimeter) (5 min)
1. Switch OFF all power (24 V and 5 V). Unplug the ESP32 USB.
2. Multimeter on **continuity / beep** (or Ω).
3. Find a 74HC595 chip (16 pins). **Pin 13 = /OE** (count from the notch: left side pins 1–8 top to bottom, right side 9–16 bottom to top).
4. Measure **pin 13 ↔ GND (pin 8)**:
   - **Beeps / 0 Ω** → /OE is tied to GND on the board (power-up protection cannot work yet).
   - **No beep** → measure **pin 13 ↔ the ESP32 GPIO 19 wire** (the old OE wire). Beep = connected to the ESP32 (good).
5. Repeat on a second 74HC595 chip.

**Send me:** "pin 13 to GND: beep/no beep; pin 13 to GPIO 19 wire: beep/no beep".

---

## Step 3 — Upload the Phase 1 firmware with PIN_PROFILE 1 (old wiring, no rewiring) (20 min)
**First, the ejector ESP32:**
1. Arduino IDE → **File → Open** → `ESP32_63CH_Ejector_v2_0\ESP32_63CH_Ejector_v2_0.ino` (in the new folder).
2. Find the line `#define PIN_PROFILE        2` and change **2 → 1**. File → Save. (Profile 1 = your current wiring GPIO 14/13/12/19.)
3. **Tools → Board → esp32 → "ESP32 Dev Module"**. (If "esp32" is missing: Tools → Board → Boards Manager → search "esp32" → install "esp32 by Espressif Systems", version 2.0.17 or 3.0.x.)
4. Tools settings: **Upload Speed 921600**, CPU Frequency 240 MHz, Flash Size 4MB, Partition Scheme "Default 4MB with spiffs", **Port = the ejector's COM port** (normally COM24).
5. Click **Upload** (→). If it stops at "Connecting….", hold the **BOOT** button on the ESP32 until upload starts.
6. **Tools → Serial Monitor**, set **921600 baud** and "Both NL & CR". Press the ESP32 **EN** button → you must see
   `LAMBTON EJECTOR v2.0 READY | 921600 baud | ... LEGACY PINS (GPIO12 boot risk)`. Type `HELP`. **Close the Serial Monitor.**

**Then the relay ESP32:**
7. File → Open → `ESP32_8CH_ModbusRelay_v2_0\ESP32_8CH_ModbusRelay_v2_0.ino` (no change needed, same pins as before) → Board "ESP32 Dev Module" → **Port = relay COM port** (normally COM13) → Upload.

**Send me:** "ejector banner OK / relay upload OK" (or the error text).
**If it fails:** section R1.

---

## Step 4 — Phase 1 tests T0–T8 (details in PHASE1_GUIDE.md section 5)
**Main air CLOSED, chute EMPTY for T0–T7.** Watch the drive-board LEDs.

| Test | Do | Must happen | Send me |
|---|---|---|---|
| **T0** | `python tools\test_ejector_protocol.py`, `python tools\test_ejector_link_sim.py`, `python tools\test_fusion_fix.py` | each prints **ALL TESTS PASSED** | pass/fail |
| **T1** | unplug/plug the ejector USB 5 times; press EN 5 times | no valve LED flashes at power-up (with the old wiring a short flash can happen — note it) | flash yes/no |
| **T2** | sorter CLOSED → `RUN_EJECTOR_TESTER.bat` (or `python gui_esp32_ejector_tester.py`) → choose COM24 → CONNECT → STATUS / PING | badge **"v2.0 BINARY"**, `crc_err=0` | screenshot |
| **T3** | Pulse 30 ms, Gap 300 ms, Sweep 1 to 63 → SWEEP TEST | LEDs 1→63 in order; console shows `CH n -> OUT n-1 -> CHIP x, Qy` | wrong channels (if any) |
| **T4** | paste the long `FIRE_MULTI:1,2,...,63:30` line from PHASE1_GUIDE.md T4 into the text box, Enter | **all 63** LEDs flash, `ACK:FIRE_MULTI` | yes/no |
| **T5** | click channel 5 → **WATCHDOG TEST**; then SINGLE TEST Repeat 0, Pulse 200, Gap 10 and **pull the USB cable** | LED off by itself within 0.5 s; `[WATCHDOG TEST] PASS` | pass/fail |
| **T6** | start the sorter (from the new folder: `python LAMTON_LINE_NEW_ESP32.py`) → console says `Relay firmware v2.0 detected`; press START (air still closed) → **pull the relay board USB** | feeder + main-air relays OFF within 0.5 s; red `SAFETY STOP` banner | pass/fail |
| **T7** | sorter → Ejector Test window | link line shows `BINARY fw 2.0`; label shows CHIP/Q; SINGLE PULSE with REPEAT 5 fires 5 times; AUTO SEQ follows | pass/fail |
| **T8** | **air ON, no product**: SWEEP 1–63, Pulse 4 ms, Gap 200 ms (every valve clicks); then **low feed with product**, normal recipe | less good product in the reject bin than before (50 ms fix) | your impression + reject sample |

If T3 shows wrong channels: SWAP them in the tester → SAVE TO ESP32 → sweep again.

---

## Step 5 — Phase 2 measurements (30–45 min)
**A. Ball speed** (sorter CLOSED, feeder OFF, air OFF):
```
python tools\ball_speed.py --seconds 90
```
Wait for **READY**, then drop a **10 mm ball** 30–50 times, spread across the chute width.
**Send me:** `logs\ball_speed_<date_time>.json` (or the printed mean / variation for FRONT and REAR).

**B. Latency log** (normal production):
1. In the new folder open `features_config.json` and set `"timing_log_enabled": true` → save.
2. Start the sorter, run **10 minutes** with product (normal recipe), STOP, close the sorter.
3. `python tools\latency_report.py`
4. Set `"timing_log_enabled": false` again.
**Send me:** `logs\latency_report_<date_time>.md` (it contains the 35/40/55/70 mm table).

---

## Step 6 — Phase 4 capture test with real product (30 min)
1. `python tools\disk_speed_test.py` → **send me** the verdict lines (is the machine PC's disk fast enough?).
2. `features_config.json` → `"continuous_capture_button": true` → start the sorter → button **CONTINUOUS CAPTURE**.
3. Product **CASHEWS**, class e.g. **GOOD_CASHEW**, camera **FRONT**, output folder on the biggest/fastest disk. If the disk test said "too slow", **untick "Save full strip images"**.
4. Feeder OFF → **LEARN BACKGROUND**. Tick **Ejection OFF** → **START CAPTURE**.
5. Feed a **counted** number of cashews (e.g. exactly 200) slowly → wait until all are through → **STOP CAPTURE**.
6. Check: OBJECTS ≈ 200, **DROPPED FRAMES = 0**, no red warnings. A red line about "roi_height" means the camera frame is taller than 200 lines — tell me.
7. Set `"continuous_capture_button": false` again if you want the old screen back.
**Send me:** the counted number, the OBJECTS number, the `session.json` of the session folder, and 5–10 crop images from `crops\GOOD_CASHEW\`.

---

## R — If something fails: roll back
| Problem | Do this |
|---|---|
| **R1** New ejector firmware does not work | Arduino IDE → open the OLD `ESP32_63CH_ShiftRegister\ESP32_63CH_ShiftRegister.ino` → same board/port → Upload. The program detects the old firmware and keeps sorting (no watchdog). |
| **R2** New relay firmware does not work | Upload the OLD `ESP32_8CH_ModbusRelay_NEW\ESP32_8CH_ModbusRelay_NEW.ino`. |
| **R3** New program does not work | Run the **untouched old folder `D:\LAMTON_V1`** (its `START_LAMTON_SORTER.bat`). Note: the old program needs the **old** firmware (R1 + R2). |
| **R4** A new feature misbehaves | `features_config.json` → set everything to `false`, restart (= Phase 1 behaviour). |
| **R5** Camera settings changed by mistake | `python tools\camera_restore.py camera_backups\<date_time>\FRONT_<SN>.config` (and REAR). |
| **R6** Feeder/air will not switch on after a pull-the-cable test | that is the safety lock: press **START** (or restart the sorter). |

**Never needed tomorrow:** rewiring with the 74HCT245 (later), Phase 3 line sync (stays OFF), camera setting changes.
