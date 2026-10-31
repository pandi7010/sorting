# LAMTON_V1 — Ejection Timing Analysis & Line-Synchronous Redesign

*Phase A (analysis only). No existing files were modified. This builds on `ANALYSIS.md` and does not repeat it.*
*Date: 15 Oct 2026*

**How to read the numbers.** Every number is tagged with where it came from:

| Tag | Meaning |
|---|---|
| **[Code]** | Read directly from this project's code or config files. |
| **[Calc]** | Calculated from [Code] values plus the system facts you gave me. |
| **[Est]** | My engineering estimate. **Must be measured on the machine** (Section 9 tells you how). |
| **[Ask]** | Unknown. Listed in the questions at the end. |

---

## Contents

0. [Summary](#0-summary)
1. [Full pipeline: one defect from camera to valve](#1-full-pipeline-one-defect-from-camera-to-valve)
2. [Latency budget for 35 mm](#2-latency-budget-for-35-mm)
3. [How the current code calculates delay and position](#3-how-the-current-code-calculates-delay-and-position)
4. [Pixel-to-channel mapping and front/rear fusion](#4-pixel-to-channel-mapping-and-frontrear-fusion)
5. [New architecture: ESP32 owns the line clock](#5-new-architecture-esp32-owns-the-line-clock)
6. [New binary PC → ESP32 protocol](#6-new-binary-pc--esp32-protocol)
7. [Safety watchdog (500 ms)](#7-safety-watchdog-500-ms)
8. [ESP32 vs Arduino Mega 2560 for the ejector role](#8-esp32-vs-arduino-mega-2560-for-the-ejector-role)
9. [Timing measurement points to add](#9-timing-measurement-points-to-add)
10. [Step-by-step implementation plan (Phase B)](#10-step-by-step-implementation-plan-phase-b)
11. [Questions for you](#11-questions-for-you)

---

## 0. Summary

1. **35 mm is not achievable with the current design.** A defect needs roughly **17–72 ms [Est]** to get from the camera to the valve. At 3–4 m/s the product reaches the nozzle in **8.75–11.7 ms [Calc]**. The current design is **2–8× too slow**.
2. **Even the redesigned PC-based system cannot guarantee 35 mm.** That would need the *worst-case* PC delay below about 6 ms, every time. Windows + Python cannot promise that. A realistic ejector distance for a PC-based design is **about 80–120 mm** (worked out in Section 2.4). Keeping 35 mm would need hardware-level decisions (FPGA) or a much slower product.
3. **The most valuable change is still the line-clock redesign (Section 5).** The ESP32 counts camera lines and fires at "line L + D", so variable PC/USB delay no longer moves the blast position. Firing becomes accurate whenever the PC's answer arrives in time. When it arrives too late, the ESP32 **counts it as LATE** instead of firing in the wrong place. That gives you a real number for choosing the ejector distance.
4. **New problems found in this pass** (beyond ANALYSIS.md):
   - **Likely 50 ms blasts on almost every defect** when both cameras run. A detection from one camera is reused when the other camera's frame arrives, which counts as a "long object". See 4.4.
   - **The software crop may throw away product.** If the camera sends frames taller than 200 lines, the code keeps only the middle 200, and the rest is never inspected (4.5).
   - **The YOLO decision comes more than 1.28 s late**, so it cannot be used for live ejection (1.2, step 7).
   - **Row position is thrown away.** The 200 lines are collapsed into one row, so *where along the flow* the defect was is lost. That alone causes ±35 mm of uncertainty (Section 3).
   - **The standalone tester `gui_esp32_ejector_tester.py` doesn't work with the current firmware.** It sends the old `EJ:`/`CLEAR`/`TEST` commands, which `ESP32_63CH_ShiftRegister.ino` rejects. Only `PING` works.
   - **GPIO 12 is used as LATCH, but it is an ESP32 boot "strapping" pin.** If it is pulled high at power-up, the ESP32 can fail to boot (Section 8).

---

## 1. Full pipeline: one defect from camera to valve

### 1.1 Some basic numbers first

| Quantity | Value | Source |
|---|---|---|
| Chute width | 330 mm | you |
| Active pixels, FRONT | 100 → 3981 = 3881 px | [Code] `camera_config.json` |
| Active pixels, REAR | 195 → 3897 = 3702 px | [Code] |
| Pixel size across the chute | 330 / 3881 ≈ **0.085 mm** | [Calc] |
| Channel width | 330 / 63 ≈ **5.24 mm** ≈ 61.6 px (FRONT), 58.8 px (REAR) | [Calc] |
| Product speed | 3–4 m/s (3.5 typical) | you |
| Distance scan line → nozzle | 35 mm | you |
| Time to travel 35 mm | 11.7 ms @3 m/s, **10.0 ms @3.5 m/s**, 8.75 ms @4 m/s | [Calc] |
| Frame height used | 200 lines (centre crop) | [Code] `roi_height` |
| Line rate | **unknown, never set by the code** (the camera uses its saved settings) | [Ask] |
| Exposure | FRONT 100 µs, REAR 65 µs | [Code] |

**Line rate clue.** On a line-scan camera, each line's exposure must fit inside one line period. FRONT exposure is 100 µs, so FRONT can run **at most about 10,000 lines/s (10 kHz)** [Calc]. I use **10 kHz** as the example below. Replace it with your real value.

**Example: what one "frame" means physically.** At 10 kHz, 200 lines take **20 ms**. At 3.5 m/s the product moves **70 mm** in 20 ms. So a single frame covers a 70 mm tall strip of falling product, which is **twice the scan-to-nozzle distance**. By the time the frame is complete, the product in its first line has already passed the nozzles.

**Free-fall check (gravity).** Over 10 ms, gravity adds only ½ × 9.81 × 0.010² ≈ **0.5 mm** of extra travel. You can treat speed as constant over 35 mm.

### 1.2 Step-by-step trace (current design)

Take one dark spot on a cashew, in channel 20, seen by the FRONT camera.

| # | Step | What happens (code location) | Time | Fixed or varies? |
|---|---|---|---|---|
| 1 | **Line exposure** | Camera exposes the line containing the spot. | 0.1 ms [Code] | Fixed |
| 2 | **Frame filling** | The camera free-runs (`CameraSetTriggerMode(h,0)`). The spot's line waits until the 200-line frame is complete. A spot in the first row waits about 20 ms; one in the last row waits 0 ms. | **0–20 ms** [Calc @10 kHz] | Fixed *per row*, but the row is random → acts like **±10 ms jitter** |
| 3 | **Readout + GigE transfer** | Image packets travel to the PC network card. The SDK hands over the frame only when it is complete. | 1–8 ms [Est] | Varies |
| 4 | **SDK hand-off + colour conversion** | `CameraGetImageBuffer` → `CameraImageProcess` (raw → BGR) → `CameraReleaseImageBuffer` (line ≈8749). | 2–6 ms [Est] | Varies |
| 5 | **Crop, flip, copy** | Centre-crop to 200 rows, `cv2.flip` for REAR, `np.ascontiguousarray`. | ≈1 ms [Est] | Varies a little |
| 6 | **Defect detection** | `process_live_frame` → `process_frame_for_defects` (≈10883). Runs **in the camera thread** (`Qt.DirectConnection`, line 10602). Converts three 200×4096 colour planes to float64 (≈20 MB of data), then min/max/thresholds, HSV, and a per-channel Python loop. | **5–20 ms** [Est] | **Varies a lot**: depends on algorithms, product load, and the other camera competing for Python's lock (the GIL) |
| 7 | **YOLO (if enabled)** | Takes **only row 5** of each frame and stacks **64 frames** before running once (≈11145). With a 20 ms frame, the first answer comes **≥1.28 s** after the first row was seen, plus 30–150 ms inference on the CPU [Est]. Inference also **blocks the camera thread**. | ≥ 1,300 ms | **Unusable for live ejection** |
| 8 | **Fusion** | `evaluate_fusion_and_fire` ORs this result with the *last* REAR result (≈11258). | < 0.5 ms [Est] | Small |
| 9 | **Scheduling + configured delay** | `EjectScheduler.schedule_fire`: `fire_time = now + delay_ms`. Active recipe: `delay = 5 ms`, `pulse = 4 ms` [Code]. "now" is **after** steps 2–8. | **+5 ms** | Fixed number, added to a varying start time |
| 10 | **Scheduler wake-up** | Python thread sleeps 1 ms at a time, then busy-waits the last 2 ms (≈1202). It may wait for the GIL while camera threads run NumPy. | 0–2 ms typical, **spikes 5–20 ms** [Est] | **Jitter** |
| 11 | **Serial send** | `FIRE_MULTI:20:4\n` → Windows USB driver → USB (1 ms frames) → CH9102/CP210x chip → UART at 115200. 16 bytes ≈ 1.4 ms on the wire [Calc]. | 1–3 ms typical, spikes 10+ ms [Est] | Jitter |
| 12 | **ESP32 receive + parse** | Bytes collected until `\n`, parsed, put in the 256-entry queue, taken out in the same `loop()` pass, unless that channel is still busy (then it **waits** for the previous pulse to end). | < 0.1 ms, or up to 1 pulse (4 ms) if busy [Code] | Mostly fixed |
| 13 | **Shift register update** | `shiftOut()` of 64 bits by `digitalWrite` + latch. | ≈ 0.02–0.1 ms [Est] | Fixed |
| 14 | **Drive board + valve opening** | Transistor/MOSFET switches; the solenoid valve opens and air reaches the product. | **1–3 ms** [Ask] | Fixed per valve type |
| 15 | **Pulse end** | `millis()` check: 1 ms resolution, so a "4 ms" pulse is really 3–4 ms [Code]. | — | ±1 ms on length |

### 1.3 Adding it up

| Case | Total from line capture to air on product |
|---|---|
| Best case (spot in the last row, everything fast) | 0 + 1 + 2 + 1 + 5 + 5 + 0 + 1 + 0.1 + 0.1 + 1 ≈ **17 ms** [Est] |
| Typical (middle row) | ≈ 10 + 4 + 4 + 1 + 10 + 5 + 1 + 2 + 0.1 + 0.1 + 2 ≈ **39 ms** [Est] |
| Bad case (first row, slow processing, small spikes) | ≈ 20 + 8 + 6 + 1 + 20 + 5 + 2 + 3 + 4 + 0.1 + 3 ≈ **72 ms** [Est] |
| Time available for 35 mm | **8.75–11.7 ms** [Calc] |

At 3.5 m/s, 17 ms means the product is already **60 mm** below the scan line, and 72 ms means **250 mm**. The nozzle is at 35 mm.

> **So how does the machine eject anything today?** Possible explanations, to check with measurements:
> - (a) The fusion bug in 4.4 makes most pulses **50 ms** long, a 175 mm "wall of air" that hits the defect **and** lots of good product.
> - (b) The real line rate is much higher, or the real speed much lower, than assumed.
> - (c) The delay was tuned by trial and error to an average, so it works "sometimes".
>
> A drop test (Section 9) will tell us which.

---

## 2. Latency budget for 35 mm

### 2.1 The rule

```
  time the product needs to reach the nozzle  ≥  (time to decide)  +  (valve opening time)  + margin
       distance / speed                           worst case, not average
```

**Example.** 35 mm at 3.5 m/s = 10.0 ms. If the valve needs 2 ms to open, the decision must reach the ESP32 within **8 ms**, *every single time*. An answer that arrives at 9 ms is a missed defect.

### 2.2 Classifying each step

| Step | Fixed / Jitter / Risky | Can it be reduced? |
|---|---|---|
| Line exposure | Fixed, tiny | — |
| **Frame filling (200 lines)** | Fixed per row, **large** (0–20 ms) | **Yes**: smaller frames (32–64 lines) → 3.2–6.4 ms |
| GigE transfer | Jitter, small | A little (packet size, dedicated network card) |
| SDK + colour conversion | Jitter | A little (convert only the needed columns/planes) |
| **Python detection** | **Risky**: big jitter, GIL contention between the two cameras | Yes: uint8 maths (not float64), fewer copies, smaller frames. Still not hard real-time |
| YOLO | **Unusable** for live ejection | Only usable offline / for quality reports |
| Scheduler on Windows | **Risky**: rare spikes of 10–20 ms that can't be predicted | **Removed** in the new design (the ESP32 does the timing) |
| USB serial | Jitter, occasional spikes | Higher baud rate and shorter messages help. Spikes remain, but in the new design they only matter if they make the message LATE |
| ESP32 + shift register | Fixed, tiny | Hardware SPI makes it even smaller |
| Valve opening | Fixed | Depends on the valve; can be compensated in D |

### 2.3 Is 35 mm achievable?

- **Current design: no.** The typical total (≈39 ms) is about 4× the budget.
- **Redesign (Section 5) with 32-line frames:**

  | Step | Typical | Worst |
  |---|---|---|
  | Frame filling | 3.2 ms | 3.2 ms |
  | Transfer + SDK | 3 ms | 8 ms |
  | Processing | 3 ms | 10 ms |
  | USB | 1.5 ms | 10 ms |
  | **Total** | **≈11 ms** | **≈31 ms** [Est] |

  The typical case already exceeds the ≈8 ms budget, and the worst case is far beyond it. **35 mm would still miss many defects.** The new design would at least *count* them as LATE.
- **To truly keep 35 mm**, the yes/no decision would have to be made in hardware next to the camera (an FPGA vision board, or a camera with built-in processing). The PC would then only send settings. That is a different project.

### 2.4 Realistic ejector distance

Distance needed = speed × (worst-case latency + valve time) + margin.

| Worst-case latency + valve | @3 m/s | @3.5 m/s | @4 m/s |
|---|---|---|---|
| 10 ms | 30 mm | 35 mm | 40 mm |
| 15 ms | 45 mm | 53 mm | 60 mm |
| 20 ms | 60 mm | 70 mm | 80 mm |
| **25 ms** | **75 mm** | **88 mm** | **100 mm** |
| 30 ms | 90 mm | 105 mm | 120 mm |
| 40 ms | 120 mm | 140 mm | 160 mm |

**Recommendation:** after the redesign, plan for about **80–120 mm** (the 25–30 ms row), and confirm it with the LATE counter from the new firmware. Move the nozzle bar down only after measuring.

**Trade-off:** the further the nozzles are below the camera, the more the product drifts sideways and spins in free fall, so aim gets worse. That is why commercial sorters with hardware processing keep nozzles close. Measure hit accuracy at each distance you try.

---

## 3. How the current code calculates delay and position

### 3.1 Delay

```python
# get_ejector_config()  → (delay_ms, pulse_ms) from the active recipe
# W320 WHOLE - GRADE A: delay 5.0, pulse 4.0   [Code: recipes.json]
# RICE 80/15, DHALL 120/25, PLASTICS 100/3, GOOD 100/20

# EjectScheduler.schedule_fire()
fire_time = time.perf_counter() + delay_ms / 1000      # "now" = AFTER processing finished
```

- The delay is counted from **the moment processing finishes**. It is not counted from when the line was captured.
- Everything before that moment is **not compensated**: frame filling (0–20 ms), transfer, colour conversion, detection. This hidden part changes from frame to frame, so the blast position moves around even if the delay setting is perfect.
- The recipes range from 5 ms to 120 ms. This suggests each one was tuned by trial and error to absorb an unknown, changing processing time.

### 3.2 Position along the flow (vertical)

The code does **not** know *where in the frame* the defect was:

```python
b_vals_min = np.min(b, axis=0)            # 200 rows → 1 row
algo_trigger = np.any(raw_def_mask, axis=0)  # 200 rows → 1 row
```

Collapsing 200 rows into one is a clever trick for speed. But it throws away the row number, which is the defect's position along the flow. At 10 kHz and 3.5 m/s, the 200 rows span 70 mm. The firing time is the same no matter where in that 70 mm the defect was, which gives **±35 mm of position error** [Calc] even with perfect timing.

### 3.3 Position across the chute (which valve)

`channel = (pixel − start_pixel) ÷ pixels_per_channel`. Details are in Section 4.

### 3.4 Python or ESP32?

| Job | Where | Resolution |
|---|---|---|
| Delay before firing | **Python** (Windows) | `perf_counter` is sub-microsecond, but thread wake-up is ≈1 ms with spikes |
| Firing moment | **ESP32 fires as soon as the command arrives**. It has no idea of time or line position | — |
| Pulse length | ESP32, `millis()` | **1 ms** (a 4 ms pulse can be 3–4 ms) |

### 3.5 How accurate can Windows timing be?

- **Typical:** ±1–2 ms. Python 3.11+ uses Windows high-resolution timers for `time.sleep`, and the last 2 ms is a busy-wait.
- **Rare but certain:** spikes of **5–20 ms or more** [Est]. Causes include the two camera threads holding the Python lock (GIL) while doing NumPy work, garbage collection, `print()` calls on the hot path (the console is slow on Windows), antivirus, Windows Update, and USB driver scheduling.
- **Simple example:** if the delay is right 999 times out of 1000 but late by 15 ms once, then at 3.5 m/s that one blast lands 52 mm low. At 1,000 defects per minute, that is one miss every minute **before** counting all the other errors above. Windows cannot promise a worst case, so the timing must move to the ESP32.

---

## 4. Pixel-to-channel mapping and front/rear fusion

### 4.1 How 4096 pixels become 63 channels

```
pixels_per_channel = (end_pixel − start_pixel) / num_channels     # per camera
FRONT: (3981 − 100) / 63 = 61.6 px   → channel k covers pixels 100+61.6k … 100+61.6(k+1)
REAR : (3897 − 195) / 63 = 58.8 px
```

- Your "about 65 px" is 4096/63. In practice the code uses only the area between `start_pixel` and `end_pixel`, so it is 58.8–61.6 px [Code].
- Channels are **equal width**. There is no correction for lens distortion or perspective. Near the chute edges, one pixel can cover a slightly different width of chute than in the centre, so the outer channels may be shifted by a few pixels [Est].
- The REAR image is mirrored (`cv2.flip(frame, 1)`) so that left/right matches the FRONT camera [Code].
- Multi-chute support exists (`total_chutes`, `chute_roles`), but you use 1 chute.

### 4.2 Calibration that exists today

| Tool | What it does | What it lacks |
|---|---|---|
| `start_pixel` / `end_pixel` per camera | Sets the outer edges (in the camera setup / background graph screens) | Only 2 numbers per camera, no per-channel correction |
| **Wire test mode** | Stretch a wire; after 5 stable frames the matching valve is held ON (`HOLD_ON`). You see whether the right nozzle blows | Visual only; the result is not saved as a correction table |

### 4.3 Boundary defects (a spot exactly on the line between two channels)

| Algorithm | Behaviour | Problem? |
|---|---|---|
| GRAY dark, HSV, FALLBACK | `np.any` per channel slice → **both** neighbouring channels fire | OK (safe), uses a little more air |
| GRAY light | Needs at least `min_def_px` pixels **in one slice**. A 6-px spot split 3+3 may reach the limit in **neither** slice | **Missed defects** at boundaries |
| SIZE | Measures width **per slice**. A 30 px grain split 15+15 looks like two small grains | **Wrong BIG/SMALL decision** at boundaries |

A cashew is about 15–20 mm wide, which is 3–4 channels, so it nearly always sits across a boundary. SIZE sorting is affected the most.

### 4.4 Front + rear fusion, and the "50 ms pulse" bug

```python
# every time EITHER camera delivers a frame:
all_channels = last_front_decision.channels ∪ last_rear_decision.channels
```

**Problem 1: stale results are reused.** The two cameras free-run and are **not synchronised**. Each new frame from one camera is combined with the *previous* result of the other camera, which may be up to one frame (≈20 ms) old.

**Problem 2: this triggers the long-object rule.** Walk through it:

1. FRONT frame sees a spot in channel 20 → evaluate → channel 20 count = 1 → normal 4 ms pulse scheduled.
2. REAR frame arrives (REAR saw nothing) → evaluate → `last_front_decision` **still contains channel 20** → count = 2 → `needs_overlap = True`.
3. The code then uses `actual_pulse = max(4, 50) = 50 ms` and `force_update=True`.

**Result:** with both cameras running, almost every single defect gets a second command with a **50 ms** pulse [Code-derived; confirm by logging]. At 3.5 m/s that blast covers **175 mm** of product, so good cashews next to the defect are rejected and air consumption rises.

**Problem 3: no vertical offset.** The front and rear scan lines may not sit at exactly the same height on the chute. The code applies the **same delay** to both cameras. Even 3 mm of height difference is about 1 ms at 3.5 m/s [Calc]. The new design gives each camera its own delay `D_front` / `D_rear` (Section 5.6).

### 4.5 Possible blind gaps from the software crop

`GigECameraWorker.run()` keeps the **middle** `roi_h = 200` rows of whatever frame height the camera sends. The code never sets the camera's frame height; it uses whatever was last saved in the camera. If the camera sends, say, 512-line frames, then **312 of every 512 lines (61%) are thrown away without being inspected**. Defects in those lines are never seen. **[Ask]**: what frame height is set in the camera? (It is printed as `FrameHead.iHeight`; Section 9 shows how to log it.)

---

## 5. New architecture: ESP32 owns the line clock

### 5.1 The idea, in one example

Think of a **ticket number on every line**.

1. The ESP32 ticks a precise clock 10,000 times a second. Each tick tells both cameras "take one line now", and the ESP32 adds 1 to its **line counter**.
2. The PC receives images, finds a defect in **line 123,456**, and sends: *"FRONT camera, line 123,456, channels 20+21, length 70 lines."*
3. The ESP32 knows the nozzle is **D = 80 lines** below the scan line (Section 5.6), so it opens valves 20+21 exactly at **line 123,536**.

It **doesn't matter** whether the PC's message took 3 ms or 7 ms to arrive. If it arrives **before** line 123,536, the blast is in exactly the right place. If it arrives **after**, the ESP32 does not fire in the wrong place. It counts a **LATE** event and reports it.

```
 time ─────────────────────────────────────────────────────────────►
 ESP32 line counter:  123456 ........................ 123536 (= L + D)
                         │  camera captures line          │ valves open
                         ▼                                ▼
 PC:                  [frame fills][transfer][process][USB]──► FIRE msg must arrive before here
                                    ← this part may vary freely →
```

### 5.2 Block diagram

```
                    ┌────────────────────── ESP32 #1 (ejector) ──────────────────────┐
                    │ Core 1 (real-time loop, no Wi-Fi, no serial):                    │
                    │   hardware timer → LINE CLOCK 10 kHz ──► trigger driver ──► FRONT cam trig in
                    │                   line_counter++                     └──► REAR  cam trig in
                    │   each line: open/close channels whose window starts/ends now   │
                    │   → hardware SPI → 74HCT595 ×8 → drive board → 63 valves         │
                    │ Core 0: USB serial, binary protocol, watchdog, status counters  │
                    └───────────────▲───────────────────────────────────────────────┘
                                    │ USB serial 921,600 baud, binary frames + ACK
 ┌──────────────── PC ─────────────┴────────────────────────────────────────────────┐
 │ camera thread: frame k (N=32..64 lines) → base line = line_of_frame(k)            │
 │ detection: per channel, FIRST and LAST defect row → line = base + row             │
 │ send FIRE(cam, line, 64-bit mask, length) — no waiting, no Python timers          │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

### 5.3 What the camera SDK supports (checked in `mvsdk.py` and the code)

| Need | Found in `mvsdk.py`? | Notes |
|---|---|---|
| External **hardware trigger** | ✅ `CameraSetTriggerMode(h, 2)` (0 = continuous, 1 = software, 2 = hardware) | The code currently uses **0** (free-run) everywhere |
| Trigger edge | ✅ `CameraSetExtTrigSignalType` (`EXT_TRIG_LEADING_EDGE` = 0 …) | |
| Trigger debounce / delay | ✅ `CameraSetExtTrigJitterTime`, `CameraSetExtTrigDelayTime`, `CameraSetTriggerDelayTime` | |
| Frames per trigger | ✅ `CameraSetTriggerCount` | |
| Which trigger types this camera supports | ✅ `CameraGetExtTrigCapability` (bitmask), `CameraGetCapability().iTriggerDesc` | **Use these to probe your cameras** (Phase 5 of the plan) |
| Camera IO pins | ✅ `CameraSetInPutIOMode` (`IOMODE_TRIG_INPUT`), `CameraSetOutPutIOMode` (`IOMODE_STROBE_OUTPUT`) | |
| Strobe output (camera → ESP32) | ✅ `CameraSetStrobeMode/DelayTime/PulseWidth/Polarity` | |
| Frame height | ✅ `CameraSetImageResolution` | Not used by the code today |
| Camera timestamp | ✅ `tSdkFrameHead.uiTimeStamp` (0.1 ms units) | **Not used by the code today** |
| **Per-line trigger ("line trigger" / encoder mode)** | ❌ **No function in `mvsdk.py`** | This wrapper is MindVision's general SDK. Line-scan-specific settings may exist only in MindVision's MVDCP tool, the camera manual, or `CameraSpecialControl`. **[Ask]** model + manual |

So there are three ways to sync camera and ESP32, from best to simplest:

| Plan | How | Camera changes | Pros | Cons |
|---|---|---|---|---|
| **A. Line trigger** | ESP32 pulse = 1 line. Camera returns a frame every N pulses. | Line-trigger mode on (model-specific) + `TriggerMode 2` + frame height N | Exact line numbers, fixed line rate, both cameras perfectly in step, no gaps | Needs line-trigger support **[Ask]** |
| **B. Frame trigger** | ESP32 sends 1 pulse every N line periods; the camera captures N lines at its own internal line rate. | `TriggerMode 2`, `TriggerCount 1`, frame height N, internal line rate set slightly faster than the ESP32 clock | Uses only functions confirmed in `mvsdk.py`; both cameras start each frame together | Tiny gap between frames (a few lines); line position inside a frame depends on the camera's internal rate |
| **C. Free-run + strobe** | Camera keeps free-running; its STROBE output pulses at each frame start → ESP32 input timestamps it. | `TriggerMode 0` (unchanged), strobe output enabled, frame height N | Smallest camera change, no gaps | Front and rear stay unsynchronised; ESP32 must measure the line period; strobe-per-frame behaviour must be confirmed |

**Recommendation:** first run the **probe** (Phase 5). Use **Plan A** if line trigger exists, otherwise **Plan B**. Plan C is a fallback.

### 5.4 Camera settings that would change

| Setting | Now | New |
|---|---|---|
| Trigger mode | 0 (continuous) | **2 (hardware)**, Plans A/B |
| Trigger input | not configured | `CameraSetInPutIOMode(h, 0, IOMODE_TRIG_INPUT)`, rising edge, debounce minimal |
| Frame height | camera's saved value, then cropped in software to 200 | **Set in the camera to N = 64** (later try 32). **Remove the software crop** |
| Exposure | 100 / 65 µs | Must be **shorter than the line period** (e.g. ≤ 90 µs at 10 kHz) |
| Line rate | camera's saved value | Plan A: set by the ESP32. Plan B: camera internal rate a few % faster than the ESP32 clock |
| Persistence | — | Save the settings into the camera (`CameraSaveParameter`) *and* apply them from code at start-up |
| Electrical | — | Camera trigger inputs are usually **opto-isolated, 5–24 V** **[Ask]**. The ESP32's 3.3 V pin needs a small driver (transistor or 74HCT buffer at 5 V) to drive both cameras |

### 5.5 How the PC knows the line number of each row

- The ESP32 starts the line counter at 0 when the PC sends **START_CLOCK**, after both cameras are armed.
- Frame k (counting from 0) covers lines **k × N … k × N + N − 1** (Plan A).
- **Dropped frames** would break the count. Guard against them with the camera timestamp: `k = round((ts_k − ts_0) / (N × line_period))`. If k jumps by more than 1, the PC logs a dropped frame and stays in sync.
- Each `HEARTBEAT` reply from the ESP32 includes its current line number, so the PC can also check that it isn't drifting.

**Example.** N = 64, frame k = 1,929 → base line = 123,456. A spot appears first in row 0 and last in row 20 → line = 123,456, length = 21 lines.

### 5.6 How the ESP32 decides when to fire

```
D_cam      = round(travel_time × line_rate) − valve_open_lines     (one value per camera)
start_line = line + D_cam − lead_margin
end_line   = line + D_cam + max(length, min_pulse_lines) + tail_margin
```

**Example** (35 mm, 3.5 m/s, 10 kHz, valve 2 ms):
- Travel time = 10 ms = 100 lines; valve = 20 lines → **D = 80 lines**.
- A cashew spot 21 lines long, with margins of 5 lines each → valve open from line L+75 to L+106 = **3.1 ms**.

**One "window" per channel** (start_line, end_line) replaces today's queue. If a new request overlaps or touches the current window, the window is simply **extended**. That handles long objects and front/rear duplicates naturally, with no 50 ms padding hack. A small pending list (4 entries per channel) holds requests that start later.

**Late check:** if `now_line > start_line + late_tolerance`, don't fire; count LATE. If it's only slightly late (within tolerance), open immediately for the remaining part of the window.

**Counter wrap:** a 32-bit counter at 10 kHz wraps after about 119 hours. Always compare with `(int32_t)(a − b) > 0`. That works correctly across the wrap.

**Real-time loop on core 1:** polls the hardware timer. On each new line it compares 63 windows (≈1–2 µs at 240 MHz), and if the mask changed, sends 64 bits over hardware SPI (≈6.4 µs at 10 MHz). USB serial and parsing run on core 0, so they can never delay a valve.

---

## 6. New binary PC → ESP32 protocol

### 6.1 Why binary (and how it fixes the truncation bug)

- **The bug today:** `FIRE_MULTI:1,2,3,…:4` grows with the number of channels. The firmware copies it into `char temp[64]`, the `:pulse` part gets cut off, and the whole command is silently rejected.
- **The fix:** a **fixed-size 8-byte (64-bit) mask**. Firing 1 channel or all 63 channels produces **exactly the same message length** (23 bytes), so there is nothing that can overflow.
- The receiver knows each message type's exact length, and it **rejects** any frame with the wrong length or a bad checksum and counts it. Nothing is silently truncated.

### 6.2 Frame layout

| Byte(s) | Field | Notes |
|---|---|---|
| 0 | `0xA5` | Start marker 1 |
| 1 | `0x5A` | Start marker 2 |
| 2 | `LEN` | Number of bytes from TYPE to the end of PAYLOAD. Max 40; larger values are rejected |
| 3 | `TYPE` | Message type (table below) |
| 4–5 | `SEQ` | Sequence number 0–65535, little-endian, +1 for each message |
| 6 … | `PAYLOAD` | Depends on TYPE |
| last 2 | `CRC16` | CRC-16/CCITT (poly 0x1021, init 0xFFFF) over bytes 2 … end of payload, little-endian |

**Why a CRC and not a simple sum?** A simple sum misses common errors such as two swapped bytes. CRC-16 catches all 1- and 2-bit errors and all burst errors up to 16 bits. Electrical noise from solenoids is exactly the kind of thing that causes those.

**Text still works.** `0xA5` never appears in normal text, so the firmware can tell binary frames apart from text lines like `PING`. This keeps the ejector testers working.

### 6.3 Message types

**PC → ESP32**

| TYPE | Name | Payload | Total size |
|---|---|---|---|
| 0x01 | HEARTBEAT | `flags` u8 (bit0 = system running, bit1 = front cam alive, bit2 = rear cam alive) | 9 B |
| 0x10 | **FIRE** | `cam` u8 (0 = front, 1 = rear) · `line` u32 · `mask` 8 bytes (bit 0 = channel 1) · `length_lines` u16 | **23 B** |
| 0x11 | FIRE_NOW (testing only) | `mask` 8 B · `pulse_us` u32 | 20 B |
| 0x20 | CONFIG | `line_period_us` u16 · `D_front` u16 · `D_rear` u16 · `lead` u8 · `tail` u8 · `min_pulse_lines` u16 · `max_pulse_lines` u16 · `late_tol` u8 | 21 B |
| 0x30 | CMD | `code` u8 (1 = STOP_ALL, 2 = START_CLOCK, 3 = STOP_CLOCK, 4 = ARM, 5 = HOLD_ON, 6 = HOLD_OFF, 7 = TEST_SWEEP) · `arg` u16 | 11 B |
| 0x40 | STATUS_REQ | — | 8 B |

*Total size = 2 start bytes + 1 LEN + LEN bytes + 2 CRC bytes.*

**ESP32 → PC**

| TYPE | Name | Payload |
|---|---|---|
| 0x80 | **ACK** | `seq_acked` u16 · `result` u8 (0 = OK, 1 = LATE, 2 = PARTIAL_LATE, 3 = QUEUE_FULL, 4 = BAD_PARAM, 5 = NOT_ARMED, 6 = TRIPPED) · `now_line` u32 |
| 0x81 | STATUS | counters u32 each: `rx_ok`, `crc_err`, `len_err`, `unknown_type`, `seq_gaps`, `fired`, `late`, `queue_full`, `wdt_trips` · `state` u8 · `now_line` u32 |
| 0x82 | EVENT | `code` u8 (WDT_TRIP, BOOT, CLOCK_STARTED …) |

### 6.4 Example: fire channels 3 and 17, FRONT camera, line 123,456, 21 lines long

```
mask: channel 3 → bit 2, channel 17 → bit 16  →  0x0000_0000_0001_0004
line: 123456 = 0x0001E240

A5 5A | 12 | 10 | 07 00 | 00 | 40 E2 01 00 | 04 00 01 00 00 00 00 00 | 15 00 | CRClo CRChi
 SOF   LEN  TYPE  SEQ=7  cam   line (LE)      mask (LE, 8 bytes)        len=21
```

At **921,600 baud** this takes **0.25 ms** on the wire. At 115,200 it takes 2.0 ms [Calc].

### 6.5 Acknowledgement and error counting

- The PC **does not wait** for each ACK, because waiting adds latency. A separate reader thread matches ACKs to sequence numbers.
- PC-side counters: `sent`, `acked_ok`, `acked_late`, `no_ack_within_50ms`, `queue_full`, and the ESP32's `crc_err`/`len_err` (read with STATUS every second and shown on the UI).
- **FIRE messages are never re-sent.** A repeat would arrive even later, and the product is gone. Only CONFIG and CMD are re-sent if not ACKed.
- `seq_gaps` on the ESP32 shows messages lost on the way. `no_ack` on the PC shows replies lost on the way back.

### 6.6 Quick fix first (before the binary protocol)

Phase 1 of the plan fixes the text protocol immediately, so production is safe while the binary protocol is developed:
- `rxBuffer` grows to 256 bytes. A line that is too long is **reported** (`ERR:LINE_TOO_LONG`) instead of being silently cut.
- `FIRE_MULTI` is parsed **in place**, with no 64-byte copy.
- A new text command `FM:<16 hex digits>:<pulse_ms>` (fixed 22 characters) is added.

---

## 7. Safety watchdog (500 ms)

### 7.1 Ejector ESP32

```
on every valid message (binary with good CRC, or a known text command):
    last_rx = now
in the real-time loop:
    if state == ARMED and now − last_rx > 500 ms:
        OE pin HIGH            → all 74HC595 outputs off instantly (hardware level)
        clear all windows, holds and queues; send all-zero mask
        state = TRIPPED; blink LED fast; send EVENT(WDT_TRIP) when contact returns
```

- **Stay tripped until the PC sends ARM.** Don't resume on your own: if the PC came back in a confused state, automatic resuming could fire valves wrongly.
- **Firmware hang protection:** enable the ESP32 task watchdog (`esp_task_wdt`, about 1 s). If the firmware itself freezes, the chip reboots.
- **Power-up safety (hardware):** add a **10 kΩ pull-up resistor from OE to the 595 supply**. While the ESP32 boots or resets, its pins float and the 74HC595 outputs can be random, which could fire random valves for a moment. The pull-up keeps all outputs off until the firmware deliberately enables them.
- **HOLD_ON** (wire test) gets its own limit of 10 s maximum.

### 7.2 Relay ESP32 (feeder, main air, wiper)

The PC writes Modbus to this board **only when something changes**, so silence is normal today. That means a watchdog can't work yet. The fix:

- New firmware version accepts **register 8 = heartbeat**. The PC writes an increasing counter there every 200 ms. One write at 9,600 baud ≈ 17 ms round trip, which is easily fast enough.
- If there is no valid write for 500 ms → **relay 1 (feeder), relay 2 (main air) and relay 3 (wiper) OFF**. The lights can stay on.
- **Optional hardware backup:** wire a "PC OK" output from the ejector ESP32 to an input on the relay ESP32, or put a contact in series with the main-air relay coil. Then main air drops even if the relay board's own link is still alive.

### 7.3 PC side

- A **heartbeat thread** sends HEARTBEAT every **100 ms** to the ejector board and the Modbus heartbeat every **200 ms** to the relay board.
- The heartbeat carries "camera alive" flags. If a camera stops delivering frames for more than 500 ms, the **PC itself** stops the feeder. Otherwise unsorted product keeps flowing even though the valves are safe.
- On normal STOP, the PC sends STOP_ALL. The watchdog is a backup, not the normal way to stop.

### 7.4 Test (safe)

Main air **off**. Watch the drive board LEDs or a logic analyser.
1. Send `HOLD_ON` for channel 5.
2. Pull the USB cable.
3. Measure the time until the output goes off: it must be **≤ 500 ms + 1 line**.

Repeat with the PC software killed in Task Manager instead of pulling the cable.

---

## 8. ESP32 vs Arduino Mega 2560 for the ejector role

| Topic | ESP32 | Arduino Mega 2560 |
|---|---|---|
| CPU | 2 × 240 MHz, 32-bit | 1 × 16 MHz, 8-bit |
| **Per-line work at 10 kHz** (check 63 windows every 100 µs) | ≈ 1–2 µs → **2% of one core** | ≈ 150–250 µs → **longer than the 100 µs line period** [Est]. Would need a different, more complex design |
| SPI to 74HC595 | Hardware SPI up to 80 MHz (≈10 MHz realistic over a cable): 64 bits ≈ **6.4 µs** | Hardware SPI max 8 MHz: 64 bits ≈ 8 µs + overhead ≈ 15–20 µs |
| Timers for the line clock | 4 × 64-bit timers, plus LEDC/RMT/PCNT hardware for exact clocks and pulse counting | 16-bit timers; a 10 kHz clock is possible (Timer1) |
| Serial | UART up to 5 Mbaud; USB chips typically 921,600–3,000,000 baud | Max 1 Mbaud; **115,200 has about 2% clock error** at 16 MHz (250k/500k/1M are exact) |
| RAM | ≈ 520 KB | 8 KB (enough for windows + buffers, but tight) |
| Two cores | Yes: real-time loop and communication separated | No |
| Logic level | **3.3 V**. A 74HC595 powered from 5 V needs ≥ 3.5 V for a guaranteed "high", so 3.3 V is **out of spec**. Fix with **74HCT595** (reads 2.0 V as high) or a 74HCT245/74AHCT125 buffer, which also drives long cables better | **5 V**: matches 5 V 74HC595 and drive boards directly |
| Camera trigger output | Needs a driver either way (camera inputs are usually 5–24 V opto) | Same |
| Existing code and wiring | ✅ Already built | ❌ Port everything |
| Boot pin caution | **GPIO 12 (currently LATCH) is a strapping pin.** If pulled high at reset, the ESP32 may not boot. Move LATCH to a safe pin (e.g. GPIO 21) and use VSPI (GPIO 18 = SCK, 23 = MOSI) | None of note |

**Recommendation: ESP32**, with three changes:
1. Hardware SPI on non-strapping pins.
2. **74HCT595** (or an HCT buffer) between the 3.3 V ESP32 and a 5 V drive board.
3. A pull-up resistor on OE.

The Mega's 5 V logic is its only advantage, and a cheap buffer chip gives you the same thing. Its CPU cannot do per-line window timing at 10 kHz.

---

## 9. Timing measurement points to add

Measure before changing the design, and again after each phase. Log to a CSV file in batches (one write per second). **Do not use `print()` per frame**, because console output on Windows is slow and adds its own delays.

### 9.1 PC-side points (all from `time.perf_counter_ns()`)

| ID | Where | What it tells you |
|---|---|---|
| **M1** | Right after `CameraGetImageBuffer` returns: also log `FrameHead.uiTimeStamp`, `iHeight`, `iWidth` | Real frame height (answers 4.5); real line rate = height ÷ Δtimestamp; dropped frames (timestamp jumps) |
| M2 | After `CameraImageProcess` | Colour-conversion cost |
| M3 | Start/end of `process_frame_for_defects` | Detection time; histogram p50 / p99 / max |
| M4 | In `schedule_fire`: scheduled time and target fire time | Delay as configured |
| M5 | `EjectScheduler`: just before `write()` and after `flush()` | Scheduler lateness (actual − target) and USB write time |
| M6 | Per command: channel list, pulse length, `needs_overlap` flag | **Confirms the 50 ms fusion bug (4.4)** |
| M7 | GIL / thread load: count of frames processed per camera per second | Whether processing keeps up with the camera |

### 9.2 ESP32-side points (from `micros()` and the line counter)

| ID | Where | What it tells you |
|---|---|---|
| E1 | Time the last byte of each message arrived | Time spent in USB + UART |
| E2 | Time of the latch pulse (valves really switched) | ESP32 internal delay |
| E3 | Counters: rx_ok, crc_err, len_err, invalid, queue_full, late, wdt_trips | Health; reported in STATUS |
| E4 | **Debug pin**: one spare GPIO goes HIGH at latch-on and LOW at latch-off | Probe with a logic analyser |

### 9.3 Linking PC time and ESP32 time

The PC sends `PING` with its own time `t1`. The ESP32 replies with its `micros()` value, and the PC records the arrival time `t2`.

```
offset = esp_micros − (t1 + t2) / 2
```

This is accurate to about ± half the round-trip time. Repeat it every few seconds and keep the result from the fastest round trip.

### 9.4 Physical measurements

- **Logic analyser** (a cheap 8-channel, 24 MHz USB one is enough). Probe:
  - the ESP32 debug pin (E4),
  - one 74HC595 output,
  - the valve driver output,
  - the camera strobe or trigger, if available.
- **Valve response:** use a microphone or pressure sensor at the nozzle, or a current clamp on the coil, to find the time from electrical ON to air arriving. This gives `valve_open_lines`.
- **Drop test:** drop a single marked dark object (e.g. a painted cashew) many times and film it with a phone at 240 fps. Check whether the blast hit early, late, or on target, and how much good product got blown with it.
- **Reject purity:** weigh good vs bad in the reject bin before and after each phase. This is the real business number.

---

## 10. Step-by-step implementation plan (Phase B)

**Ground rules (from your instructions):**
- `git init` and commit everything first.
- **One phase at a time**, and I wait for your OK after each.
- Existing firmware is **never overwritten**. New firmware goes in **new folders with version names**, e.g. `ESP32_63CH_Ejector_v2_0/`.

**General safe-test procedure (used in every phase):**
1. **Bench, air OFF:** close the main air supply valve or keep relay 2 off. Watch the drive board LEDs, LEDs on the 595 outputs, or a logic analyser.
2. **Air ON, no product:** listen to and watch single valves.
3. **Test objects:** a few marked items dropped by hand.
4. **Product at low feed rate.** Then full production.

**Keep a way back:** before flashing new firmware, note which sketch is on each board. Rolling back = flash the old sketch from its untouched folder.

**Note on testers:** the in-app *Ejector Test window* matches the current firmware. `gui_esp32_ejector_tester.py` sends old commands (`EJ:`, `CLEAR`, `TEST`). The new firmware will accept those as **aliases**, so both testers work.

### Phase 0: Safety net and baseline (no behaviour change)
- `git init`, `.gitignore` for `build/`, `dist/`, `__pycache__/`, then commit the current state.
- New script `tools/probe_cameras.py` (read-only): prints model name, `iHeight`, line period (`CameraGetExposureLineTime`), trigger capability mask, supported trigger modes, and IO counts.
- **Test:** run the probe with the sorter software closed; send me the output.

### Phase 1: Ejector firmware v2.0 — critical fixes (new folder `ESP32_63CH_Ejector_v2_0`)
- Fix the truncation: 256-byte line buffer, in-place parsing, `ERR:LINE_TOO_LONG`, new `FM:<16hex>:<ms>`.
- **500 ms watchdog** (ARMED/TRIPPED states, OE pin HIGH on trip), task watchdog, `HOLD_ON` max 10 s.
- Pulse timing in **µs** instead of ms.
- Legacy aliases `EJ:`, `CLEAR`, `TEST` → both testers work.
- `STATUS` reports all counters.
- Pins unchanged in this phase (so no rewiring yet); pin changes come in Phase 6.
- **Tests (air OFF):**
  1. In-app tester: fire each channel → correct LED.
  2. Send `FIRE_MULTI` with **all 63 channels** → all 63 LEDs flash; `STATUS` shows `invalid=0`.
  3. Old firmware comparison: the same command is ignored (proves the bug and the fix).
  4. Watchdog: `HOLD_ON` → pull USB → off within 500 ms (logic analyser).
  5. Kill the PC app → off within 500 ms.
  6. Power-cycle → no valve flicker at boot (after the OE pull-up is fitted).

### Phase 2: PC heartbeat + instrumentation (Python, small changes)
- New module `hw_link.py`: heartbeat thread (100 ms), STATUS polling, CSV timing logger (M1–M7).
- Minimal edits in the main file: start the heartbeat, add the measurement calls. Main ESP32 commands stay the same.
- **Tests:**
  1. Run 10 minutes with no product → watchdog never trips, CSV is created.
  2. Pause the PC app (debugger or Task Manager "suspend") → watchdog trips; resuming requires ARM.
  3. **Collect baseline numbers** with product (still the old timing design).

### Phase 3: Relay board firmware v2.0 + Modbus heartbeat (new folder `ESP32_8CH_ModbusRelay_v2_0`)
- Register 8 = heartbeat; 500 ms timeout → feeder, main air and wiper OFF; the "tripped" state must be cleared by the PC.
- **Tests:** feeder running (no product) → pull the relay board's USB → feeder and air relays drop within 0.5 s. Also kill the app → same result.

### Phase 4: Fusion fix (Python) — *optional, your decision*
- Use each camera's decision **once** (clear it after fusion), so the long-object rule only counts real consecutive frames from the **same** camera.
- **Test:** logs (M6) show 4 ms pulses instead of 50 ms; drop test; reject-bin purity improves.

### Phase 5: Binary protocol (firmware v2.1 + `hw_link.py`), still time-based
- Implement Section 6 (FIRE_NOW, CONFIG, CMD, STATUS, ACK). Text commands stay available for the testers.
- New tester `tools/binary_ejector_tester.py` (new file) with error counters shown.
- **Tests (air OFF):** 1-channel and 63-channel masks look identical in length. Inject corrupted bytes (the tester has a "corrupt CRC" button) → `crc_err` increases and nothing fires. Run 100,000 messages at 921,600 baud → 0 errors.

### Phase 6: Line clock on the ESP32 (firmware v3.0, `ESP32_63CH_Ejector_v3_0`)
- Hardware-timer line clock, line counter, per-channel windows on core 1, hardware SPI, new pins (no GPIO 12), FIRE(line) with D_front/D_rear, LATE counting.
- Hardware: trigger driver to the cameras **[Ask]** voltages; 74HCT595 or buffer; OE pull-up.
- **Tests:**
  1. Logic analyser: clock is exactly 10.000 kHz, jitter < 1 µs.
  2. FIRE(line = now + 1000) → valve opens exactly 1000 lines later (±1).
  3. FIRE with an old line → LATE count, no fire.
  4. Watchdog still works.

### Phase 7: Cameras on the ESP32 clock (Plan A or B, after the Phase 0 probe)
- Camera settings from 5.4 applied from code; frame height N = 64; remove the software crop; compute frame line numbers using timestamps.
- **Tests:** cameras in front of a still target → images look normal; the frame rate matches the ESP32 clock ÷ N exactly; pause the ESP32 clock → frames stop (proves cameras follow the ESP32).

### Phase 8: PC sends FIRE(line) — the new timing goes live
- Row position kept (first/last defect row per channel); new FIRE path; the Python delay scheduler is retired; the LATE rate is shown on the UI.
- **Tests:** air off → logic analyser shows the valve exactly D lines after the defect line. Then air on → **drop test** → calibrate D_front and D_rear separately (move each in steps of 5 lines until hits are centred). Then the reject-purity test.

### Phase 9: Channel calibration and distance decision
- Wire test produces a saved **per-channel boundary table** per camera. Fix the GRAY-light/SIZE boundary handling (look at neighbouring slices).
- Run production while recording the **LATE %** at your current 35 mm. Use Section 2.4 with *measured* worst-case latency to decide whether to move the nozzle bar.

---

## 11. Questions for you

Please answer as many as you can. "Don't know" is a fine answer, and I'll suggest how to measure it.

1. **Product speed** at the scan line: is 3–4 m/s measured or estimated? What is the chute length and angle?
2. **Camera line rate** actually used (lines/s)? Or open MindVision's MVDCP tool and read it.
3. **Camera model** (from MVDCP, or run `scratch_get_cams.py`), and do you have its **manual**? I especially need: does it support **external line trigger**, what are the **trigger input voltages**, and does the strobe output pulse per frame or per line?
4. **Frame height** set in the camera (lines per frame)? Is it 200, or larger (in which case the crop discards lines, see 4.5)?
5. **Valve type/model** and its **response time** (electrical ON → air at the nozzle), and its minimum reliable pulse length.
6. **Drive board:** what **input voltage** does it expect (3.3 V or 5 V logic)? What **supply voltage** do the 74HC595 chips run on? Are they **74HC595 or 74HCT595**? Is there a pull-up on OE?
7. **Cable length** between the ESP32 and the shift-register/drive board?
8. Which **USB-serial chip** is on each ESP32 (CH9102, CP2102, CH340)? This decides the maximum baud rate.
9. **Vertical distance between the front and rear scan lines** on the chute (0 mm if they look at the same line)?
10. Is the nozzle bar position **adjustable** (could it move to 80–120 mm if the measurements say so)?
11. How does the machine **perform today**: roughly what % of defects are removed, and how much good product ends up in the reject bin? Are YOLO / AI algorithms used in production?
12. Which **recipes** are used in production? The active one (`W320 WHOLE - GRADE A`, 5 ms / 4 ms) only uses GRAY A.
13. Do you have a **logic analyser** or oscilloscope, and a phone that films at 240 fps?
14. For Phase 4 (fusion fix): should I include it, or leave that behaviour unchanged for now?
15. Is it OK to add the small hardware changes (OE pull-up resistor, HCT buffer/74HCT595, camera trigger driver) when we reach those phases?

---

**⏸ Phase A complete. I'll wait for your answers and your "approved" before starting Phase B.**
