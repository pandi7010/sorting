# 🌾 DLT COLOUR SORTER (63-CHANNEL)
## 📖 OFFICIAL COMPREHENSIVE ENGINEERING & USER MANUAL (MASTER MANUAL - ENGLISH)

---

> [!IMPORTANT]
> **Official Notice:** This manual is the comprehensive engineering, configuration, and operation guide for the **LAMBTON 63-Channel Optical & AI Color Sorter**. It explains every single screen, button, procedure, mathematical formula, camera white calibration, pixel division, algorithm tuning (Gray A, Green Dark, Blue Light, Discolour, HSV, Size), ejector pulse and delay timings, wiper controls, remote WiFi access, and factory security passwords.

---

## 📑 TABLE OF CONTENTS
1. [System Architecture & Machine Specifications](#1-system-architecture--machine-specifications)
2. [Main Dashboard & Desktop / WiFi Remote Access](#2-main-dashboard--desktop--wifi-remote-access)
3. [Program & Recipe Manager: File Operations & Single-Save Workflow](#3-program--recipe-manager)
4. [Deep Dive: 8 Defect Detection Algorithms & Parameter Tuning](#4-deep-dive-8-defect-detection-algorithms)
   - [GRAY A & GRAY B (Green Dark / Blue Light)](#41-gray-a--gray-b)
   - [DISCOLOUR (Color Combinations)](#42-discolour-color-combinations)
   - [HSV COLOR SORTING (Interactive Color Map)](#43-hsv-color-sorting)
   - [SIZE SORTING (Small / Big Limits)](#44-size-sorting)
5. [Camera Setup, WHITE CALIBRATION & PIXEL DIVISION](#5-camera-setup-white-calibration--pixel-division)
6. [Ejectors, PULSE DURATION & DELAY TIME Physics (Channels 1 - 63)](#6-ejectors-pulse-duration--delay-time-physics)
7. [Automated Wiper & Glass Dust Cleaning System](#7-automated-wiper--glass-dust-cleaning-system)
8. [Lighting & Multi-Channel Illumination Setup](#8-lighting--multi-channel-illumination-setup)
9. [Background Waveform Scope Tuning (63 Channels Calibration)](#9-background-waveform-scope-tuning)
10. [AI Image Capture, Model Training & Test Console](#10-ai-image-capture-model-training--test-console)
11. [Factory Configuration, Modbus Relays & Passwords](#11-factory-configuration-modbus-relays--passwords)
12. [Pneumatic Air Pressure & Industrial Troubleshooting](#12-pneumatic-air-pressure--industrial-troubleshooting)

---

## 1. System Architecture & Machine Specifications
The **LAMBTON 63-Channel Sorter** combines high-frequency Line-Scan computer vision, real-time ESP32 embedded control, and ultra-fast pneumatic micro-ejection to separate grains, seeds, pulses, nuts, and industrial plastics.

- **Ejector Channels**: 63 independent high-speed pneumatic solenoid valves.
- **Optical Sensors**: Dual 4096-pixel high-speed Line-Scan cameras (Front & Rear chutes).
- **Processing Engine**: Low-latency OpenCV image processing augmented by PyTorch YOLO (`best.pt`) and Keras deep neural networks.

---

## 2. Main Dashboard & Desktop / WiFi Remote Access

![Main Dashboard](main_dashboard_clean.png)

### 🔘 Dashboard Controls & Functionalities:
- **▶ START SYSTEM**: Energizes sorting pipeline: cameras acquire continuous scan lines, algorithms evaluate grain defects, and pneumatic ejectors deflect rejects into the reject hopper.
- **⏹ STOP SYSTEM**: Safely stops sorting operations, shuts down valves, and halts vibratory chutes.
- **PROGRAM SELECTION (Dropdown)**: Instantly switches active commodity schemes (e.g. `RICE`, `DHALL`, `PEANUT`, `CORN`).
- **PROGRAM MANAGER**: Opens the Master Recipe Management window.
- **DESKTOP OPTIONS**:
  - The software runs in full-screen touch-optimized kiosk mode. Operators can minimize or exit to Windows desktop via the top navigation controls or `Alt + Tab` / `Ctrl + Shift + Esc`.
- **WIFI REMOTE ACCESS**:
  - The ESP32 communication subsystem supports WiFi TCP/IP networking (Default IP: **`192.168.1.177`**).
  - Automation engineers can securely connect to the industrial PC via **AnyDesk** or **VNC Viewer** over the factory WiFi network for remote diagnostics, parameter tuning, and model deployment without on-site visits.

---

## 3. Program & Recipe Manager

![Program Manager](pm_dialog_one_button.png)

### 🔘 Recipe File Operations:
- **NEW**: Creates a new commodity recipe profile with custom naming via the virtual keyboard.
- **COPY**: Clones the selected recipe into a new profile (e.g., `RICE_PREMIUM_GRADE`).
- **RENAME**: Renames an existing recipe profile.
- **DELETE**: Removes an obsolete recipe profile from disk.
- **BACKUP**: Stores a clean snapshot of all recipes into the machine registry.
- **RECOVER**: Restores all recipes from the registry snapshot in case of operator misconfiguration.

### 🔘 Consolidated Single Action Button:
- **`[ 💾 SAVE & LOAD RECIPE ]`**:
  - Previously, operators were required to click "Save Algorithms to Recipe" and then click "Load & Run".
  - This has been unified into a single glowing green button: **`[ 💾 SAVE & LOAD RECIPE ]`**.
  - Clicking this button writes all active algorithms, sensitivity parameters (`⚙`), and camera modes into `recipes.json`, and instantly loads and arms the recipe in the sorter runtime!

### 🔘 Dual Camera Mode:
- **`🔗 BOTH SAME (ONE-TIME SET)`**:
  - Toggling an algorithm or adjusting sensitivity parameters applies the change to **both Front and Rear cameras simultaneously**, eliminating repetitive setup.
- **`🔀 INDIVIDUAL (FRONT / REAR)`**:
  - Unlinks cameras for independent inspection rules (e.g., shape sorting on front, aggressive discolouration on rear).
- **`💾 COPY FRONT ➔ REAR`**:
  - One-click duplication of all Front camera parameters to the Rear camera.

---

## 4. Deep Dive: 8 Defect Detection Algorithms

Clicking the **`⚙` (Gear Icon)** next to each algorithm opens its parameter tuning dialog.

### 4.1. GRAY A & GRAY B
Monochrome contrast thresholding for surface blemishes and spot defects.
- **CHANNEL**:
  - `GREEN`: Optimal for rice, seeds, and light-toned commodities.
  - `BLUE`: Optimal for yellow grains, lentils, and pulses.
  - `RED`: Optimal for dark beans and blackened grains.
- **DEFECT MODE**:
  - **DARK DEFECT (Green Dark / Red Dark)**: Rejects black spots, insect bites, fungus spots, and charcoal tips.
  - **LIGHT DEFECT (Blue Light / White Defect)**: Rejects chalky rice, pale immature kernels, and light stones in dark grains.
- **SENSITIVITY (1 - 100)**: Higher values detect faint, minor blemishes; lower values reject only deep, conspicuous defects.
- **SCALE % (16%)**: Internal image processing scale factor (16% resolution optimizes CPU throughput while preserving defect edges).
- **PURITY (Min Pixel, 1 - 10)**: Minimum pixel cluster area required to trigger ejection, filtering out harmless airborne dust specks.

### 4.2. DISCOLOUR (Color Combinations)
- **COLOR COMBINATIONS**:
  - `RED > GREEN`: Rejects reddish/brownish discoloration.
  - `GREEN > RED`: Rejects immature green kernels.
  - `BLUE > YELLOW`: Rejects pale yellow shifts in pulses.
  - `DARK > LIGHT`: General color difference rejection.
- **SENSITIVITY (1 - 100)**: Dynamic threshold for spectral color difference.

### 4.3. HSV COLOR SORTING
Hue-Saturation-Value color space segmentation matching human visual perception.
- **COLOR MAP COORDINATES (X1, Y1, X2, Y2)**:
  - Interactive bounding box on the 2D color chromaticity map selecting target color hues.
- **SORTING LOGIC**:
  - **SORT OUT**: Grains with colors inside the selected box are flagged as defects and rejected.
  - **SORT IN**: Only grains with colors inside the selected box are accepted; all others are rejected.

### 4.4. SIZE SORTING
Morphological contour dimension classification.
- **SMALL LIMIT (Pixels, e.g. 1000)**: Rejects broken kernels, fine debris, and undersized grain tips.
- **BIG LIMIT (Pixels, e.g. 2300)**: Rejects oversized grain clusters, unhusked heads, stones, and sticks.

---

## 5. Camera Setup, WHITE CALIBRATION & PIXEL DIVISION

![Camera Setup](camera_setup_clean.png)

### 🔘 Calibration Controls:
- **⚙ APPLY WHITE CALIBRATION**:
  - **Procedure**: Place a clean white calibration plate in front of the camera chute and press this button.
  - **Function**: The camera firmware automatically calculates Red, Green, and Blue gain multipliers to balance color channels ($R=G=B$) under current lighting, preventing artificial color cast.
- **PIXEL DIVISION (Mathematical Chute Mapping)**:
  - Total Line-Scan sensor resolution = **4096 Pixels**.
  - **START PIX (e.g. 100)**: Left boundary of the sorting chute on the sensor.
  - **END PIX (e.g. 4000)**: Right boundary of the sorting chute on the sensor.
  - **CHANNELS (63)**: Total active width = $4000 - 100 = 3900$ pixels.
  - **Pixel Width per Channel**: $3900 \div 63 pprox 61.9$ pixels per ejector nozzle!
  - When a defect centroid is detected at pixel 250: $(250 - 100) \div 61.9 = 2.42$ -> Channel 3 pneumatic valve fires with pinpoint accuracy!
- **CHUTE ROLES**: Primary, Secondary (Re-sorting), Third, Final, Disabled.
- **ROI SETTINGS**: ROI Width (4096), Capture Height (512), Inspection Height (100).
- **EXPOSURE & GAINS**: Sensor shutter time (µs) and electronic amplification.
- **💾 SAVE CONFIGURATION**: Commits camera bounds to `camera_config.json`.

---

## 6. Ejectors, PULSE DURATION & DELAY TIME Physics

![Ejector Test](ejector_test_clean.png)

### 🔘 Timing Calibration & Physics:
- **PULSE DURATION (ms)**:
  - Duration for which the pneumatic valve remains open to blast compressed air.
  - **Recommended**: 15ms to 25ms. (Too low = insufficient force; too high = excessive air waste and collateral rejection of good grains).
- **DELAY TIME SET (ms - Flight Delay)**:
  - **Physical Law**: As grains fall freely past the camera inspection line down to the ejector nozzle line, distance fallen is governed by: $s = ut + rac{1}{2}gt^2$.
  - **Recommended**: 80ms to 180ms depending on camera-to-nozzle vertical distance (typically 15cm to 25cm).
  - Accurate delay time ensures the air blast strikes the defective kernel dead-center in flight!
- **🔥 MANUAL FIRE**: Tests individual valves (Channels 1 to 63) with single-shot air pulses.
- **⚡ AUTO SEQ (1 - 63)**: Sequentially cycles through all 63 valves to verify pneumatic response.

---

## 7. Automated Wiper & Glass Dust Cleaning System

An automated motor-driven wiper sweeps the optical glass window to clear dust and flour build-up.

### 🔘 Wiper Settings:
- **Forward Time (ms)**: Sweep travel time from left to right (Default: 1500 ms).
- **Reverse Time (ms)**: Return travel time from right to left (Default: 1500 ms).
- **Auto-Clean Interval (Mins)**: Frequency of automatic cleaning cycles (Recommended: 6 to 10 minutes).
- **Ejector during Wiper**: Toggles whether pneumatic valves remain active or pause during the wiper pass.
- **MANUAL TEST**: Directly commands the wiper motor forward and reverse for mechanical testing.

---

## 8. Lighting & Multi-Channel Illumination Setup

![Light Setup](light_setup_clean.png)

- **TOP WHITE LED (Front / Back)**: Direct surface illumination highlighting grain grain-body texture and surface defects.
- **BOTTOM WHITE LED (Front / Back)**: Background illumination providing sharp grain silhouettes and transparency detection.
- **PWM SLIDERS (0 - 100%)**: Smooth dimming controls for individual illumination zones.

---

## 9. Background Waveform Scope Tuning

![Background Tuning](bg_dialog_63ch_fixed.png)

- **63-Channel Master Scope**: The line-scan scope accurately maps the full 4096-pixel sensor width, visualizing `START: 100` and `END: 4000` boundaries with all 63 channels mapped.
- **WHITE (HI) Threshold Slider**: Upper brightness ejection limit.
- **BLACK (LO) Threshold Slider**: Lower darkness ejection limit.
- **BAND PASS (ON / OFF)**: Restricts defect detection to a defined band envelope.
- **Top LED Bar (1 - 63)**: Green indicates accepted grain; Red indicates active valve trigger.

---

## 10. AI Image Capture, Model Training & Test Console

![AI Console](ai_console_clean.png)

### 🔘 Deep Learning Features:
1. **Dataset Creator UI**:
   - Captures high-resolution grain images directly into `dataset_capture/<program_name>`.
   - Organizes good and defective grains for model fine-tuning and transfer learning.
2. **AI Model Test Console**:
   - **Supported Architectures**: Ultralytics YOLO (`best.pt`), TensorFlow / Keras (`.keras`, `.h5`), and ONNX (`.onnx`).
   - **5 Commodity Presets**: 1. Peanut, 2. Dall, 3. Donkeyholes, 4. Corn, 5. Rice.
   - **Efficiency Metrics**: Real-time evaluation of Inference Latency (ms) and Processing Throughput (FPS).
   - **Accuracy Metrics**: Real-time confidence score (e.g. 98.5%), bounding box overlays, and defect detection table.
   - **📊 BENCHMARK DATASET**: Automated batch validation across full image directories.

---

## 11. Factory Configuration, Modbus Relays & Passwords

Security access levels protect sensitive calibration parameters:

### 🔘 User Roles & Access Passwords:
| User Role | Access Password | Permissions & Capabilities |
| :--- | :--- | :--- |
| **OPERATOR** | *No Password* | Start/Stop system, select saved recipes, execute wiper sweep. |
| **ENGINEER** | **`1199`** | Algorithm sensitivity (`⚙`), camera exposure & gains, ejector timings, background scope thresholds. |
| **MANUFACTURER (ADMIN)** | **`11*99`** | Factory machine serial number, hardware licensing, Modbus relay mapping, firmware configuration. |

### 🔘 Modbus RTU 8-Channel Relay Controller:
- **Relay 1**: Vibratory Feeder Speed Controller (Reg 40001)
- **Relay 2**: Wiper Motor Power Supply (Reg 40002)
- **Relay 3**: Ejector Main Pneumatic Air Solenoid (Reg 40003)
- **Relay 4 - 7**: Multi-zone White LED Drivers (Reg 40004 - 40007)
- **Relay 8**: Background Optical Illumination (Reg 40008)

---

## 12. Pneumatic Air Pressure & Industrial Troubleshooting

### 🔘 Pneumatic Air Pressure Standards:
- The sorting machine requires clean, dry compressed air at **6.0 to 8.0 Bar** (0.6 to 0.8 MPa).
- An integrated pressure sensor alerts operators if line pressure drops below 5.0 Bar.
- The moisture separator filter bowl must be drained daily before startup.

### 🔘 Comprehensive Troubleshooting Matrix:
| Symptom | Root Cause | Corrective Action |
| :--- | :--- | :--- |
| **ESP32 DISCONNECTED** shown in red | Loose USB cable or network IP unreachable | Reseat USB cable. In Background Tuning, reconnect to IP: `192.168.1.177`. |
| **No air blast from ejector valves** | Low line pressure or Relay 3 de-energized | Ensure compressor is at 6-8 Bar. Check Relay Manager to ensure Relay 3 is active. |
| **Over-rejection (Good grain rejected)** | Sensitivity too high or incorrect delay time | In Program Manager, click `⚙` on active algorithms to reduce sensitivity. Verify delay time in Ejector Setup. |
| **Under-rejection (Defects escaping)** | Threshold boundaries too loose or nozzle clogged | In Background Tuning, tighten White/Black sliders. Run Manual Fire on clogged channel. |
| **Image is dark or discolored** | Dust on glass or white balance drift | Execute Wiper Clean. In Camera Setup, run `APPLY WHITE CALIBRATION` on white plate. |
