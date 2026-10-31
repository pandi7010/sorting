# LAMTON_V1 — Project Analysis & Microcontroller Migration Study

*Analysis only. No project files were changed; this is the only new file.*
*Date: 15 Oct 2026*

---

## Status update (16 Oct 2026)

*The text below is the original 15 Oct analysis and is kept unchanged. Several appendix items are now fixed:*

| Appendix item | Status |
|---|---|
| 1 `FIRE_MULTI` truncation (`char temp[64]`) | Fixed by the v2.x ejector firmware and the **binary protocol** (`ejector_link.py`): channels travel as a bitmask, no text list. |
| 2 No watchdog on the ejector | Fixed in v2.x: the board switches all outputs off when the PC is silent (v2.3: silent 500 ms watchdog). |
| 3 Lost relay safety (relay `_NEW` firmware) | Fixed in `ESP32_8CH_ModbusRelay_v2_0` (feeder, main air, wiper OFF within 0.5 s). **Recommended, but not yet run on a real board** (only compiled). |
| 5 115200 baud, 190-byte text line | Fixed by the binary protocol (a 63-channel FIRE is a few bytes). v2.3 runs at 115200 baud and a FIRE is about 2 ms on the wire (see `TIMING_DESIGN.md`). |
| 9 `start_repeat()` parameters | Fixed by the binary protocol (repeat/gap/count are real fields). |
| 11 `MAIN_LAUNCHER.py` imports missing `main1` | Fixed in this copy: it now starts `LAMTON_LINE_NEW_ESP32.py` with `runpy`. |

Not hardware-tested: v2.3 firmware, relay v2.0 firmware (see `PROGRESS_LOG.md`).

---

## Contents

1. [What this project does](#1-what-this-project-does)
2. [Every file and its purpose (and the real entry point)](#2-every-file-and-its-purpose)
3. [How the MindVision camera SDK is used](#3-how-the-mindvision-camera-sdk-is-used)
4. [All hardware interactions](#4-all-hardware-interactions)
5. [Dependencies, DLLs and SDK files](#5-dependencies-dlls-and-sdk-files)
6. [What can and cannot run on a microcontroller](#6-what-can-and-cannot-run-on-a-microcontroller)
7. [Comparing the three options](#7-comparing-the-three-options)
8. [Recommendation, architecture and migration plan](#8-recommendation-architecture-and-migration-plan)
9. [Appendix: problems found while reading the code](#appendix-problems-found-while-reading-the-code)

---

## 1. What this project does

**This is the control software for an industrial optical colour sorter.** The machine is branded "LAMBTON / DLT 63‑Channel Colour Sorter". It sorts grains, pulses, cashews, plastics and similar products.

How it works, step by step:

1. A **vibrating feeder** spreads the product into a thin falling curtain.
2. **Two high-speed colour line-scan cameras** (FRONT and REAR) watch the curtain from both sides. A *line-scan* camera captures one very wide row of pixels at a time (4096 pixels wide), thousands of rows per second, like a photocopier scanning.
3. The **PC software (Python)** looks at each batch of rows. It checks each grain's colour, darkness, brightness and size, and can optionally run a YOLO AI model.
4. The camera width is divided into **63 slices**, one per air nozzle. When a bad grain is found in a slice, the PC waits a short **delay** (while the grain falls to the nozzle). Then it tells an **ESP32** to open that nozzle's **solenoid air valve** for a few milliseconds (the **pulse**).
5. The air blast knocks the bad grain into the reject bin. Good grains fall through untouched.
6. A second **ESP32 relay board** switches the machine's power loads: feeder/vibrator, main air valve, glass-cleaning wiper motor and LED lights.
7. Every few minutes a **wiper** cleans the camera windows. Sorting pauses while it runs.

The PC also provides a full-screen touch UI (PyQt5), saves product "recipes" (RICE, DHALL, W320 cashew grade, etc.), runs tools for collecting datasets and training AI models, and enforces a licence/hardware lock.

> **Key point for your migration question:** the project is **already a hybrid design**. The PC does vision and the ESP32s do I/O. Option (c) in Section 7 is not a new idea; it is mostly about improving what already exists.

---

## 2. Every file and its purpose

### Main entry point

| | |
|---|---|
| **Real entry point** | **`LAMTON_LINE_NEW_ESP32.py`** (≈13,300 lines). Launched by `START_LAMTON_SORTER.bat` (`python LAMTON_LINE_NEW_ESP32.py`) and built into the `.exe` by `BUILD_STANDALONE_EXE.bat` / `LAMTON_COLOUR_SORTER.spec`. |
| What it does at start | Checks that the PC's motherboard UUID matches a hard-coded value **and** that both camera serial numbers are present, then opens `MainDashboard` full-screen. |
| `MAIN_LAUNCHER.py` | **Broken / unused.** It imports a module called `main1`, which does not exist in this folder. |

### Python source files

| File | Purpose | Used by the main app? |
|---|---|---|
| `LAMTON_LINE_NEW_ESP32.py` | Whole application in one file: UI, recipes, licensing, camera threads, defect detection, eject scheduler, ESP32 communication, wiper/feeder sequencing, AI dataset/training tools. | **Yes, it is the app** |
| `mvsdk.py` | MindVision's official Python wrapper. Uses `ctypes` to call `MVCAMSDK_X64.dll`. | Yes (imported) |
| `relay.py` | Small standalone PyQt window that toggles the 8 relays over Modbus RTU. The main app imports its class, but the class isn't central. | Imported |
| `camera_logic.py` | An **older copy/fragment** of the camera worker, camera setup dialog and dashboard. It has no imports and cannot run alone. | No |
| `esp32_logic.py` | **Older fragment** of `ESP32Controller`, `EjectorTestWindow` and `EjectorLEDBar`. Uses the old `SA:` / `EJ:` / `CONT_START` protocol. | No |
| `ejector_test_window.py` | Another older fragment of the same ESP32 controller/test window code. | No |
| `gui_esp32_ejector_tester.py` | Standalone diagnostic GUI for the 63‑channel ejector ESP32: 63 buttons, sweep test, ping, live serial console. | No (separate tool) |
| `standalone_ejector_test.py` | Simpler standalone ejector test GUI with a touch keypad. | No (tool) |
| `old_ejector_ui.py` | Old Tkinter ejector test GUI for the `OLD_ESP32_CODE` firmware. | No (legacy) |
| `LICENCE_BOSS.py` | **Manufacturer-side** Tkinter tool that generates licence keys and stores machines in `manufacturer_data_v3.db` (SQLite). | No (vendor tool) |
| `test_esp32_ejector.py` | Command-line test of the ejector ESP32 (PING/EJ/SA/CLEAR/TEST, old protocol). | No |
| `check_esp32_comm.py`, `check_com3.py`, `test_tx.py` | Quick serial-port "is anything alive?" checks (COM3 / COM24). | No |
| `modbus_diag.py` | Listens on COM24 at 9600/115200 baud to see if Modbus bytes arrive. | No |
| `test_relay_modbus.py`, `test_simple.py`, `test_with_reset.py` | Tests for the **old** design, where the PC was a Modbus *server* and the ESP32 polled it. | No (legacy) |
| `test_vibrator.py` | Sends test bytes to the Nuvoton vibrator controller. | No |
| `fix.py`, `patch.py`, `scratch_fix_com.py` | One-off scripts that edited the main file (wiper code, Modbus code, COM port renames). They use hard-coded `d:\LAMTON_V1\...` paths. | No (history) |
| `scratch_get_cams.py` | Lists connected MindVision cameras and their serial numbers. | No (tool) |
| `scratch/test_gray.py` | Small experiment with the gray-threshold maths. | No |

### Microcontroller firmware (`.ino`, Arduino IDE for ESP32)

| Folder | What it is | Matches current PC code? |
|---|---|---|
| `ESP32_63CH_ShiftRegister/` | **Current ejector firmware.** Drives 63 valves through 8 chained 74HC595 shift registers. Has a 256-entry event queue, per-channel pulse timers, and FIRE/FIRE_MULTI/HOLD/CONT/TEST/STOP commands at 115200 baud. | ✅ **Yes** |
| `ESP32_63CH_LATEST/` | Despite the name, an **older** ejector firmware using the `SA:`/`EJ:`/`CLEAR`/`V` protocol. Also has vibrator PWM on GPIO 25. | ❌ Old protocol |
| `OLD_ESP32_CODE/` | Oldest ejector firmware ("TCM VISION"). Uses blocking `delay()`, so it can't fire several valves independently. | ❌ |
| `ESP32_8CH_ModbusRelay_NEW/` | **Current relay firmware.** The ESP32 is a Modbus RTU *slave* (ID 1, 9600 baud), and the PC writes registers 0–7 to switch 8 relays. | ✅ **Yes** |
| `ESP32_8CH_ModbusRelay/` | Older relay firmware where the ESP32 was the Modbus *master* polling the PC. It had a 4‑second safety shut-off that the NEW version **lost**. | ❌ Old design |
| `ESP32_Relay_PinFinder/` | Utility that switches each GPIO on in turn so you can find which pin drives which relay. | Tool |

### Configuration and data files

| File | Purpose |
|---|---|
| `recipes.json` | Product recipes ("schemes"): lighting, enabled algorithms + settings per camera, ejector timing, AI model. Active scheme: `W320 WHOLE - GRADE A`. |
| `camera_config.json` | Per camera (FRONT/REAR): **serial number** (used to find the camera), exposure, gains, colour gains, start/end pixel, ROI height 200 / width 4096, gamma, etc. The `ip` field is stored but not used to connect. |
| `bg_config.json` | Background thresholds per camera (lower/upper) and wave mode (BLUE). |
| `ejector_config.json` | Default ejector timing: pulse 4 ms, delay 5 ms, gap 500 ms. |
| `light_config.json` | Which LED banks are on, plus a "lux" value. Note: lux is **not actually used**, because the lights are only switched on/off through relays. |
| `wiper_config.json` | Wiper forward 1500 ms, reverse 1500 ms, every 6 minutes, ejector air on during wipe. |
| `system_config.json` | Saved COM ports (relay `COM13`, ejector `COM24`, vibrator `COM7`), auto-connect, AI settings. |
| `system_info.json` | Dealer name, machine serial, logo/background paths, pressure alarm enable. |
| `vision_settings.json` | Settings for the AI simulation screen. |
| `tcm_license.dat` | The customer's licence key (checked against the PC's UUID). |
| `manufacturer_data_v3.db` | SQLite database used only by `LICENCE_BOSS.py`. |

### AI models, images, docs, build output

| Item | Purpose |
|---|---|
| `best.pt` (6 MB) | Custom-trained **YOLO** model. This is the one the live "DEEP LEARNING/YOLO" algorithm loads. |
| `yolov8n.pt` (6.5 MB) | Generic pre-trained YOLOv8‑nano, used as a starting point for training. |
| `Cashew_CNN.onnx` (16 MB), `Cashew_CNN_V7_91.17pct.keras` (35 MB) | Cashew classifier models (91% accuracy per the name). Mentioned by the AI test console, but **not loaded by the live sorting loop**. |
| `hsv.png` | The colour-picker map. The HSV/Discolour algorithm reads the user's selected rectangle from it. |
| `logo.png`, `background.png`, `bg2.png`, `app_icon.*`, `up/down_arrow.svg` | UI graphics. |
| `*.html` diagrams (`74HC595_Physical_Pinout`, `ESP32_74HC595_Connection_Guide`, `ESP32_Visual_Diagram`, `ShiftRegister_DaisyChain_Diagram`) | Wiring guides for the ejector board. |
| `DLT_COLOUR_SORTER_USER_GUIDE_*.md/.html/.pdf` | Operator manuals in English and Tamil. |
| `*.bat`, `create_shortcut.vbs` | Start the app, build the exe, set up Windows auto-start, run testers, open the PDFs. Several use hard-coded `d:\` paths. |
| `build/`, `dist/` (≈1 GB) | PyInstaller output: the packaged `LAMTON_COLOUR_SORTER.exe` with Python, PyTorch, OpenCV, etc. bundled. |
| `dataset_capture/` | Captured training images sorted into classes (good, bad, RICE, DHALL, PLASTICS…). |
| `__pycache__/` | Python's compiled cache. Safe to ignore. |

---

## 3. How the MindVision camera SDK is used

### Camera

| Property | Value found in the code/config |
|---|---|
| Type | **Colour line-scan camera, 4096 pixels wide**, connected over **GigE** (Gigabit Ethernet). |
| Exact model | **Not named anywhere in the code.** Cameras are identified by serial number: FRONT `076011622362`, REAR `075002923007`. To get the model, run `scratch_get_cams.py` (prints the "friendly name") or open MindVision's MVDCP demo tool. |
| How many | 2 (FRONT and REAR). The software refuses to start unless **both** are present. |
| How they're found | `CameraEnumerateDevice()` → match serial number from `camera_config.json` → `CameraInit()`. If no serial matches, it falls back to "first unused camera". |

### Settings applied

| Setting | Code | Notes |
|---|---|---|
| **Trigger mode** | `CameraSetTriggerMode(h, 0)` | **0 = continuous / free-run.** The camera streams on its own. There is **no encoder or hardware trigger**, so timing relies on the camera's own line rate. |
| Output format | `CameraSetIspOutFormat(h, CAMERA_MEDIA_TYPE_BGR8)` | 3 bytes per pixel (Blue, Green, Red), converted from the raw sensor data on the PC. |
| Resolution / ROI | Sensor max width × height. The software then **crops** the centre to `roi_width`×`roi_height` = **4096 × 200 lines**. | Each "frame" is a block of 200 scan lines = **≈2.4 MB**. |
| Exposure | `CameraSetExposureTime` (FRONT 100, REAR 65; the SDK unit is microseconds) | |
| Gains | `CameraSetAnalogGain`, `CameraSetGain(r,g,b)` | |
| Image tuning | `CameraSetGamma`, `CameraSetContrast`, `CameraSetSaturation`, `CameraSetSharpness` | |
| White balance | `CameraSetOnceWB` ("one-push", using a white calibration plate) | |
| Frame loop | `CameraGetImageBuffer` → `CameraImageProcess` (raw→BGR) → `CameraReleaseImageBuffer` → NumPy array | Runs in a `QThread` per camera. The REAR image is mirrored (`cv2.flip`). |
| Disconnect handling | After 3 errors (not timeouts) it releases the camera and retries every 5 s. | |

### Image processing (the "sorting brain")

All of this lives in `MainDashboard.process_frame_for_defects()` (about line 10883). It mostly uses **NumPy** maths on the 200×4096 block, with **OpenCV** for colour conversion.

1. **Collapse 200 lines into one profile.** For each of the 4096 columns it takes the **min** and **max** of B, G and R across the 200 lines. A dark speck anywhere in the column shows in the *min*, and a bright one shows in the *max*.
2. **Find objects vs. background.** The background colour is the *median* of the column maxima. Any column differing by more than 25 levels is "object".
3. **Map pixels to nozzles.** Columns between `start_pixel` and `end_pixel` (e.g. 100–3981) are split into 63 equal slices, about **62 pixels per valve**. Multiple chutes are supported.
4. **Run each enabled algorithm** from the recipe:

| Algorithm | What it detects | How |
|---|---|---|
| **GRAY (Dark defect)** | Black/burnt/dark grains, wires, hair | Pixel darker than a fixed limit **or** darker than the average background by a set amount (chosen channel R/G/B). |
| **GRAY (Light defect)** | Chalky/white/bright defects | Pixel brighter than background + delta. Needs a minimum pixel count (scale × purity) to ignore noise. |
| **SIZE** | Too big / too small / both | Counts how many columns in a slice are "object" (width), then compares with limits. |
| **HSV / DISCOLOUR** | A specific colour range (sort out, or "sort in" = keep only that colour) | `cv2.cvtColor(BGR→HSV)` + `cv2.inRange`, with the range taken from the user's rectangle on `hsv.png`. |
| **DEEP LEARNING / YOLO** | Learned classes | Takes row 5 of each frame and stacks 64 frames into an image, then runs `ultralytics.YOLO(best.pt).predict()`. Box positions map to valves. (PyTorch, heavy.) |
| **FALLBACK** | Anything outside the background band | Min below lower threshold or max above upper threshold, per selected colour. |

5. **Fuse FRONT + REAR.** Their valve lists are merged (OR), so either camera can trigger a reject.
6. **Long-object handling.** If a valve stays triggered in consecutive frames (e.g. a stick), the pulse is stretched to ≥ 50 ms and refreshed.
7. **Wire test mode.** For calibration you stretch a wire across the view. After N stable frames the matching valve is held ON (`HOLD_ON`), so you can check the camera→valve mapping.
8. **Schedule the blast.** `EjectScheduler` (a Python thread) keeps a priority queue of "fire valves X at time T". At T it sends `FIRE_MULTI:<channels>:<pulse_ms>` to the ejector ESP32.

The setup screens also use `findContours`, `contourArea`, `boundingRect`, Otsu thresholding, `GaussianBlur` and `morphologyEx` for previews, grain counting and the ROI editor. The live sorting path itself is the NumPy column logic above.

---

## 4. All hardware interactions

### Overview diagram (current system)

```
                         ┌────────────────────────── Windows PC ─────────────────────────┐
  FRONT line-scan cam ───┤ GigE  ─┐                                                       │
  REAR  line-scan cam ───┤ GigE  ─┴─► MVCAMSDK_X64.dll ─► Python vision ─► EjectScheduler │
                         │                                                    │           │
                         │   USB-serial COM24 @115200 (text) ◄────────────────┘           │
                         │   USB-serial COM13 @9600  (Modbus RTU)                         │
                         │   USB-UART   COM7  @9600  (6-char ASCII)                       │
                         └─────────┬───────────────────┬───────────────────┬──────────────┘
                                   │                   │                   │
                     ┌─────────────▼──────┐  ┌─────────▼─────────┐  ┌──────▼───────────┐
                     │ ESP32 #1 EJECTOR   │  │ ESP32 #2 RELAYS   │  │ Nuvoton vibrator │
                     │ 8×74HC595 → 63     │  │ 8 relays:         │  │ controller (speed│
                     │ solenoid valve     │  │ vibrator, air,    │  │ of feeders 1–3)  │
                     │ drivers            │  │ wiper, 5 LED banks│  └──────────────────┘
                     └────────────────────┘  └───────────────────┘
```

### Details

| Device | Link | Protocol | What is sent |
|---|---|---|---|
| **2 × MindVision cameras** | Gigabit Ethernet (GigE) | MindVision SDK (GigE Vision based) | Settings in, image data out |
| **Ejector ESP32** (63 valves) | USB virtual COM port (CH9102/CP210x/CH340 chip), **COM24**, **115200 baud** | Plain-text lines ending in `\n` | `FIRE_MULTI:3,17,40:4`, `FIRE:<ch>:<ms>`, `HOLD_ON:<ch>`, `HOLD_OFF:<ch>`, `CONT:<ch>:<pulse>:<gap>`, `TEST:<s>:<e>:<pulse>:<gap>`, `STOP`, `ALL_OFF`, `STATUS`, `PING`. Replies: `ACK:…`, `BUSY`, `EJECTOR_READY`. |
| **Ejector hardware** | ESP32 GPIO 14 (data), 13 (clock), 12 (latch), 19 (output-enable), 2 (status LED) | Bit-banged shift-register (SPI-like) | 64 output bits → 8 chained **74HC595** → valve driver transistors/MOSFETs → **63 pneumatic solenoid valves** |
| **Relay ESP32** (8 relays) | USB COM port, **COM13**, **9600 baud 8N1** | **Modbus RTU**: PC = master, ESP32 = slave ID 1, function 06 "write single register", addresses 0–7 | 1 = ON / 0 = OFF |
| Relay 1 (GPIO 16) | | | Vibrator / feeder motor |
| Relay 2 (GPIO 17) | | | Main ejector air valve |
| Relay 3 (GPIO 18) | | | Wiper motor |
| Relays 4–7 (GPIO 19, 32, 33, 25) | | | White LED banks: front top, back top, front bottom, back bottom |
| Relay 8 (GPIO 26) | | | Blue background LED |
| **Nuvoton vibrator controller** | USB‑to‑UART (MAX232), **COM7**, **9600 baud** | 6 ASCII characters, e.g. `086100` = feeder 1 at 100 (prefixes `086`/`093`/`100` select feeders 1/2/3) | Feeder vibration amplitude |
| **Pressure switches** | *Expected* as `ALARM:PRESSURE_LOW/OK` messages over a TCP/LAN link to an ESP32 (`192.168.1.177:5000`) | Text over TCP | **No firmware in this folder sends these**, so the feature is not implemented on the hardware side. |
| PLC | — | — | **None.** No PLC communication exists. |
| Encoder / hardware trigger | — | — | **None.** Cameras free-run. |
| PC hardware ID | Windows WMI / registry | `wmic`, PowerShell, `winreg` | Motherboard UUID for licensing |

### Machine sequences (run by PC timers)

- **Start:** relay 2 (air) ON → relay 1 (vibrator) ON → feeder speed sent → wiper timer starts.
- **Wiper every 6 min:** feeder OFF → wait 3 s → wiper relay ON "forward" 1.5 s → "reverse" 1.5 s → wiper OFF → wait 2 s → feeder ON. *(Forward vs reverse is only a text command sent to the ejector ESP32, which ignores it. Physically, relay 3 is simply ON for 3 s. See Appendix.)*
- **Stop:** all valves off, relays 1/2/3 OFF, vibrator speed 0.

---

## 5. Dependencies, DLLs and SDK files

### Python packages (Python 3.12 per the `.bat` files)

| Package | Used for |
|---|---|
| `PyQt5` | Whole GUI, threads, timers |
| `opencv-python` (`cv2`) | Colour conversion, thresholds, contours, resizing, display |
| `numpy` | All the per-pixel sorting maths |
| `pyserial` (`serial`, `serial.tools.list_ports`) | All COM-port communication |
| `pymodbus` (3.x) | Modbus RTU client for the relay board (also imports server/simulator classes left over from the old design) |
| `ultralytics` (→ pulls in **PyTorch** + torchvision) | YOLO deep-learning detection and training |
| `pyinstaller` | Building the standalone `.exe` |
| Standard library | `tkinter` (LICENCE_BOSS, old UI), `sqlite3`, `hashlib`, `json`, `threading`, `heapq`, `socket`, `asyncio`, `subprocess`, `winreg`, `ctypes` (inside mvsdk) |

Keras/TensorFlow and ONNX Runtime are mentioned for model files, but the live code path I traced only loads YOLO.

### External binaries / SDK

| File | Notes |
|---|---|
| `MVCAMSDK_X64.dll` (11.8 MB) | MindVision camera SDK, 64-bit Windows. Loaded by `mvsdk.py` via `windll.LoadLibrary`. |
| MindVision driver/runtime | The GigE filter driver and platform files installed under `C:\Program Files (x86)\Mindvision\` (this folder sits inside that install). The DLL alone is normally not enough on a fresh PC. |
| `mvsdk.py` | Python wrapper. Also contains a **Linux** branch that loads `libMVSDK.so`. This matters for the future (see Section 8). |
| ESP32 firmware toolchain | Arduino IDE + "esp32" board package. Firmware uses only built-in libraries (`Arduino.h`, `SPI.h`). |

---

## 6. What can and cannot run on a microcontroller

First, the specs of the two boards and the size of the data:

| | **ESP32** (WROOM, typical) | **Arduino Mega 2560** | **Your camera data** |
|---|---|---|---|
| CPU | 2 cores, 240 MHz, 32-bit | 1 core, **16 MHz, 8-bit** | — |
| RAM | ≈520 KB (some modules add 4–8 MB slower PSRAM) | **8 KB** | **One scan line = 4096 × 3 = 12 KB.** One 200-line frame ≈ **2.4 MB**. Two cameras. |
| Network | Wi‑Fi; Ethernet only with an extra chip, **max 100 Mbit/s** | None built in | Cameras need **Gigabit** Ethernet. Example: at 5,000 lines/s one camera sends ≈61 MB/s (≈490 Mbit/s). |
| Camera SDK | None from MindVision | None | SDK exists only for Windows/Linux PCs (x86 and ARM) |
| Display | No real GUI | No real GUI | 13" full-screen touch UI |

**Simple way to picture it:** the Arduino Mega cannot hold even **one** scan line in its memory (12 KB > 8 KB). The camera sends thousands of lines per second, from two cameras.

### Breakdown by part of the project

| Part | Microcontroller? | Why |
|---|---|---|
| Receiving GigE camera images | ❌ **No** (both) | No Gigabit Ethernet, no MindVision SDK, nowhere near enough RAM or bandwidth. |
| Camera configuration (exposure, gain, white balance) | ❌ No | Needs the SDK. |
| Per-pixel defect detection on 2 × 4096-px streams | ❌ No | Millions of pixel operations per second, done in NumPy on a multi-GHz CPU. |
| YOLO / CNN AI models | ❌ No | Need hundreds of MB of RAM and PyTorch. (Tiny ESP32 AI exists, but not at this image size or speed.) |
| PyQt5 touch UI, recipe editor, graphs, dataset/training tools | ❌ No | Desktop software. |
| Licensing (Windows UUID) | ❌ No (would need a full redesign) | Windows-specific. |
| **Valve timing (open for X ms, close)** | ✅ **Yes, ideal** | MCUs have microsecond timers and no operating system interrupting them. This is exactly what MCUs are best at. |
| **Delay-to-nozzle scheduling** (fire in 5 ms) | ✅ **Yes, better than the PC** | The PC currently does this in a Python thread on Windows, where timing can drift by a millisecond or more when the CPU is busy. |
| **Driving 63 valves** via shift registers | ✅ Yes (both) | Already done by ESP32 #1. |
| **8 relays** (feeder, air, wiper, lights) | ✅ Yes (both) | Already done by ESP32 #2. |
| **Feeder speed** (PWM) | ✅ Yes | Could replace the separate Nuvoton link. |
| **Wiper sequence** (stop feeder → fwd → rev → resume) | ✅ Yes | Simple timed state machine. |
| **Safety:** all-off if the PC stops talking | ✅ Yes, and **needed** | Currently missing (see Appendix). |
| **Pressure / sensor inputs** | ✅ Yes | Digital inputs. Not wired up today. |

**Conclusion:** the "eyes and brain" (cameras, vision, AI, UI) **must stay on a computer**. The "hands and reflexes" (valves, relays, timing, safety) **belong on a microcontroller**.

---

## 7. Comparing the three options

### (a) Full move to ESP32

| | |
|---|---|
| Feasible? | **No.** |
| Blockers | No Gigabit Ethernet and no MindVision SDK for ESP32. RAM is ~5× too small for one frame. Pixel throughput is 50–100× beyond the CPU. YOLO cannot run. No way to show the UI. |
| What you would have to change | Replace the industrial line-scan cameras with something an ESP32 can read (e.g. an ESP32‑CAM: 2 MP, area-scan, ~10–20 fps). That is far too slow and low-resolution for a 63-channel sorter, so the machine would stop being competitive. |
| Verdict | ❌ Not realistic for this machine. |

### (b) Full move to Arduino Mega 2560

| | |
|---|---|
| Feasible? | **No, and even less than the ESP32.** |
| Blockers | Everything in (a), plus 8 KB RAM (less than one scan line), 16 MHz 8-bit CPU, no networking at all. |
| Good points | 5 V logic, which suits 74HC595 chips and many relay boards. Lots of pins. 4 hardware serial ports. Very robust. |
| Verdict | ❌ Impossible for vision. ✅ Perfectly capable as an **I/O board** inside option (c). |

### (c) Hybrid: PC keeps camera + vision, microcontroller does I/O over serial

| | |
|---|---|
| Feasible? | **Yes, and the machine already works this way.** |
| Good points | Keeps the proven cameras, algorithms, AI and UI. The MCU gives precise, reliable valve timing and safety. Each side does what it is good at. |
| Improvement opportunities | Move **delay timing** from Python into the ESP32. Use a **compact command format** and a **higher baud rate**. Add a **watchdog** (all-off if the PC goes silent). Finish **sensor inputs**, wiper direction and feeder PWM on the MCU. Optionally merge the boards. |
| Downsides | You still need a PC (or a small Linux computer, see below). |
| Verdict | ✅ **Recommended.** |

### Side-by-side

| Criterion | (a) ESP32 only | (b) Mega only | (c) Hybrid |
|---|---|---|---|
| Can read the existing cameras | ❌ | ❌ | ✅ |
| Can run current algorithms | ❌ | ❌ | ✅ |
| Can run YOLO AI | ❌ | ❌ | ✅ |
| Touch UI | ❌ | ❌ | ✅ |
| Precise valve timing | ✅ | ✅ | ✅ (best if timing moves to MCU) |
| Effort | Total rewrite + new cameras | Impossible | Small, step-by-step |
| Risk | Very high | — | Low |

### Should the I/O board in option (c) be ESP32 or Mega?

| | ESP32 | Mega 2560 |
|---|---|---|
| Already written & wired | ✅ Yes | ❌ Would need porting |
| Speed / timers | 240 MHz, µs timers | 16 MHz, 4 µs timer resolution (fine for ms pulses) |
| RAM for event queue | Plenty | 8 KB (enough for a ~256-event queue, but tight) |
| Logic level | 3.3 V (needs care with 5 V parts) | 5 V (matches 74HC595/relays directly) |
| Wi‑Fi for remote status | ✅ | ❌ |
| **Choice** | ✅ **Keep ESP32** | Fine alternative if you prefer 5 V hardware |

---

## 8. Recommendation, architecture and migration plan

### Recommendation

**Choose option (c), the hybrid design, and keep the ESP32.** Then improve it step by step:

1. Vision physically cannot run on either microcontroller. That is a hard limit set by memory, bandwidth and SDK availability, not a coding problem.
2. Your PC + ESP32 split is already the right shape. The best return comes from moving the **time-critical** and **safety-critical** jobs fully onto the ESP32, and fixing a few protocol issues.
3. You keep every algorithm, recipe, AI model and UI screen that works today.

> **If your real goal is to get rid of the Windows PC** (cost, size, boot time), the next step up is a **small Linux computer** (e.g. an industrial x86 mini-PC, or an ARM board like NVIDIA Jetson for the YOLO part), **not** a microcontroller. `mvsdk.py` already has a Linux code path. Confirm with MindVision that your exact camera model has a Linux/ARM SDK. That would be a separate, later project.

### Proposed architecture

```
┌──────────────────────── PC (Python) ─────────────────────────┐
│  Cameras (GigE, MindVision SDK)                              │
│  Vision: NumPy/OpenCV + optional YOLO                        │
│  Decides WHICH valves and WHEN (timestamp of the image)      │
│  UI, recipes, licensing, dataset/AI tools                    │
└───────────────┬──────────────────────────────────────────────┘
                │ USB serial, 921600 baud (or higher)
                │ Short commands, e.g.  F <seq> <delay_us> <8-byte mask> <pulse_us>
                │ Heartbeat every 100–200 ms; status/alarms come back
┌───────────────▼──────────────────────────────────────────────┐
│  ESP32 "machine controller"                                  │
│   • Own clock: schedules each blast at exactly +delay        │
│   • Per-valve pulse timers, overlap/long-object handling     │
│   • 74HC595 chain → 63 valves  (hardware SPI, level-shifted) │
│   • Relays: feeder, main air, wiper fwd/rev, LED banks       │
│   • PWM feeder speed (or keep Nuvoton link)                  │
│   • Inputs: air-pressure switch, e-stop, door switch         │
│   • WATCHDOG: no heartbeat for ~0.5 s → all valves OFF,      │
│     feeder OFF, report fault                                 │
└──────────────────────────────────────────────────────────────┘
```

*Two ESP32 boards (ejector + relays) is also fine. Keep them separate if you want the ejector link to carry nothing but valve traffic. Either way, give **both** boards a watchdog.*

### Step-by-step migration plan

Each phase leaves you with a working machine. Test on the bench (valves disconnected or air off) before production.

**Phase 0 — Make it safe to change things (1 day)**
1. Put the folder under version control (`git init`). The many `fix.py`/`patch.py`/`*_NEW` files show why this is needed.
2. Delete or archive files that are no longer used (`camera_logic.py`, `esp32_logic.py`, `ejector_test_window.py`, `OLD_ESP32_CODE`, `ESP32_63CH_LATEST`, old Modbus tests), so nobody flashes the wrong firmware.
3. Write down which firmware is flashed on which board, plus the real wiring and COM ports.

**Phase 1 — Fix the current ejector link (1–2 days)**
1. Fix the `FIRE_MULTI` buffer bug (Appendix, item 1). It can silently drop ejections when many valves fire together.
2. Replace the comma-list text with a fixed **8-byte channel mask** (a 64-bit number written as 16 hex characters). The command is then the same short length no matter how many valves fire.
3. Raise the baud rate (CP210x/CH9102 USB chips handle 921,600 baud or more). Update PC and firmware together.
4. Have the PC count `ACK`/`BUSY` replies, at least for logging.

**Phase 2 — Move "delay" timing into the ESP32 (2–3 days)**
1. In Python, record the time when each frame arrived (`time.perf_counter()` in the camera thread).
2. Send the ESP32 "fire this mask **in N microseconds**". N = configured delay minus time already spent processing.
3. On the ESP32, keep a small time-sorted queue and fire from its own `micros()` clock. Remove the busy-wait `EjectScheduler` thread from Python.
4. Compare reject accuracy before/after with a known test sample.

**Phase 3 — Add safety (1 day)**
1. PC sends `PING` (or any command) at least every 200 ms while running.
2. Both ESP32s: if nothing arrives for ~500 ms, turn **all valves OFF** (including `HOLD_ON` channels), feeder OFF, and blink an error LED.
3. Also limit any single `HOLD_ON` to a maximum time.
4. Bring back the 4-second safety shut-off from the old relay firmware.

**Phase 4 — Move remaining machine I/O onto the MCU (2–4 days)**
1. Give the wiper a real forward/reverse output (two relays or an H-bridge). Run the whole wipe sequence on the ESP32 when the PC sends `WIPE`.
2. Wire the air-pressure switch to an ESP32 input and send `ALARM:PRESSURE_LOW/OK` over the same serial link. The PC code that handles these messages already exists.
3. Optional: generate feeder PWM on the ESP32 and retire the separate Nuvoton COM port. Keep the Nuvoton board if it is the feeder's power driver.
4. Optional: merge relay control into the same ESP32 and drop Modbus for simple text commands. This means one cable and one COM port.

**Phase 5 — Electrical hardening (as needed)**
1. If the 74HC595 chips run on 5 V, use **74HCT595** or a level shifter between the 3.3 V ESP32 and the 595s. A 3.3 V signal is below the guaranteed "high" level of a 5 V 74HC595, which can cause random wrong outputs. The firmware comment about slowing the clock because of "jumper wire capacitance" may be this problem.
2. Use opto-isolated relay/valve driver boards, a separate supply for the solenoids, and a common ground with flyback diodes on every coil.
3. Use the ESP32's hardware SPI peripheral for the shift registers instead of bit-banging.

**Phase 6 — Clean up the PC side (ongoing)**
1. Split `LAMTON_LINE_NEW_ESP32.py` into modules (camera, vision, hardware link, UI) so each part can be tested on its own.
2. Move COM ports and paths fully into config files, replacing the hard-coded `COM13`/`COM24`/`d:\...` values.
3. Fix `MAIN_LAUNCHER.py` or delete it.

**Later (optional):** move the PC software to Linux on a small industrial computer, if removing Windows is a goal.

---

## Appendix: problems found while reading the code

These came up during analysis. They are listed so you can decide what to fix; **nothing was changed.**

| # | Where | Problem | Impact |
|---|---|---|---|
| 1 | `ESP32_63CH_ShiftRegister.ino`, `FIRE_MULTI` handler | The channel list is copied into `char temp[64]`. If the list plus `:pulse` is longer than 63 characters (about 16–20 two-digit channels firing together), the `:` is cut off and the whole command is rejected as invalid. The PC doesn't wait for an ACK, so nobody notices. | **Missed rejects** during heavy defect bursts. |
| 2 | Ejector firmware | No communication watchdog. Normal pulses end by themselves, but a `HOLD_ON` (wire test) stays ON forever if the PC crashes. | Valve/air waste, possible overheating of coil. |
| 3 | `ESP32_8CH_ModbusRelay_NEW.ino` | The 4 s "PC lost → vibrator/air/wiper OFF" safety from the old relay firmware was removed. | Feeder keeps running if the PC freezes. |
| 4 | Python `EjectScheduler` | Delay timing is done in a Python thread on Windows, competing with heavy NumPy/YOLO work. | Timing drift → reduced accuracy at short delays (config uses 5 ms). |
| 5 | 115200 baud + text channel lists | A 63-channel `FIRE_MULTI` line is about 190 bytes, ≈16 ms on the wire. That is longer than the 5 ms delay. | Late blasts when many valves fire. |
| 6 | Wiper | `WIPER_FWD`/`WIPER_REV` go to the ejector ESP32, whose firmware ignores them. Relay 3 is simply on for 3 s. | No real direction control unless the motor reverses mechanically. |
| 7 | Lights | "lux" values are saved but never applied, because the lights are only switched on/off. RED/GREEN LED banks have no relay. | UI suggests features that don't exist. |
| 8 | Pressure alarm | PC listens for `ALARM:PRESSURE_*` over TCP, but no firmware sends it. | Low-air condition is not detected. |
| 9 | `start_repeat()` | Sends `FIRE:<ch>:<pulse>:<gap>:<count>`, but the firmware reads only `<ch>:<pulse>`, so repeat mode never runs. | Test feature doesn't work as labelled. |
| 10 | Several `.ino` versions with different protocols (`SA:`/`EJ:` vs `FIRE_MULTI`) | Only `ESP32_63CH_ShiftRegister` matches the current PC code. `ESP32_63CH_LATEST` does **not**, despite its name. | Flashing the "LATEST" sketch would break sorting. |
| 11 | `MAIN_LAUNCHER.py` | Imports a missing `main1` module. | Crashes if launched. |
| 12 | Hard-coded values | PC UUID, camera serials, licence salt and passwords (`1234`/`4321` in `LICENCE_BOSS.py`) are in plain source. COM ports and `d:\` paths are scattered across files. | Hard to move to new hardware. Licence scheme is easy to bypass. |
| 13 | 3.3 V ESP32 → 74HC595 | If the 595s are powered from 5 V, a 3.3 V input is below their guaranteed "high" level. | Random/incorrect valve outputs, especially with long wires. |
