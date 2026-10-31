# LAMTON 63-Channel High-Speed Optical Line-Scan Colour Sorter

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PyQt5-green.svg)](https://riverbankcomputing.com/software/pyqt/)
[![Computer Vision](https://img.shields.io/badge/Vision-OpenCV%20%7C%20PyTorch%20%7C%20YOLOv8-orange.svg)](https://opencv.org/)
[![Hardware](https://img.shields.io/badge/Hardware-ESP32%20%7C%2074HC595%20%7C%20MindVision%20GigE-red.svg)](https://espressif.com/)

An industrial-grade, real-time optical colour sorting machine control system. Powered by dual high-speed GigE line-scan cameras, cascaded shift-register solenoid control on ESP32 microcontrollers, and modern computer vision with deep learning classification.

---

## 🌟 Key Features

- **Dual MindVision GigE 4096-Pixel Line-Scan Cameras**:
  - Independent **FRONT** and **REAR** continuous line-scan inspection.
  - Software ROI cropping and hardware RGB electronic gain balancing.
- **63-Channel High-Speed Pneumatic Ejector System**:
  - 63 high-speed 24V matrix solenoid valves with sub-millisecond response.
  - Driven by **ESP32 Dev Module** over high-speed serial with 8× cascaded **74HC595** shift registers.
  - Stateful anti-flicker continuous hold and instantaneous single-line trigger protocol.
  - Multi-channel boundary firing: detects boundary defects and engages adjacent valves to ensure zero defect escape.
- **Background Tuning & Live Oscilloscope**:
  - Real-time oscilloscope signal graph displaying live Red, Green, and Blue waveforms.
  - Interactive Upper (White) and Lower (Black) threshold sliders with Band-Pass in-band / out-of-band rejection modes.
  - Interactive START and END pixel boundary drag handles.
  - Direct camera hardware RGB electronic gain adjustment (`BLUE REF`, `GREEN REF`, `RED REF`) with persistence to `camera_config.json`.
- **Intelligent Sorting & AI Inspection**:
  - Real-time OpenCV contour, area, aspect ratio, and HSV color analysis.
  - Optional CPU-friendly Deep Learning classification (PyTorch MobileNetV2 / YOLOv8) for complex defect grading (e.g., cashew grading, insect spots, discoloration).
- **Industrial Automation & Peripheral Control**:
  - 8-Channel Modbus RTU Relay Controller (Relay 1: Feeder, Relay 2: Main Air, Relay 3: Wiper, Relays 4–8: LED Illumination).
  - Nuvoton UART Vibratory Feeder PWM speed control.
  - Automatic COM port detection via `port_detect.py` (no hard-coded COM ports).
- **Multi-Commodity Recipe Management**:
  - Independent recipes with lighting, sensitivity, pulse timings, and algorithm parameters for **RICE**, **DHALL**, **PLASTICS**, **CASHEW**, and more.

---

## 📐 System Architecture & Specifications

| Parameter | Specification |
| :--- | :--- |
| **Inspection Chute Width** | 330 mm waterfall chute |
| **Total Sorting Channels** | 63 Ejector Channels |
| **Active Pixel Resolution** | 4027 pixels (START: 19, END: 4046) |
| **Resolution per Channel** | ~63.92 pixels per ejector channel (~64 px/channel) |
| **Nozzle Distance** | 35–55 mm below scan line |
| **Ejector Response Time** | ~1 ms valve latency; 20–25 ms pulse width |
| **Microcontrollers** | ESP32 (Ejector 63-CH) + ESP32 (8-CH Modbus Relay) |
| **Camera Interface** | Dual GigE MindVision Line Scan (4096 px) |
| **Host OS** | Windows 10 / 11 (64-bit) with 1 ms multimedia timer |

---

## 🔌 Hardware Wiring & Pin Mapping

### 1. ESP32 63-CH Ejector Board (74HC595 Shift Registers)
8× 74HC595 shift registers cascaded in series (8 ICs × 8 outputs = 64 outputs, Channels 1 to 63 used for sorting):

```
ESP32 Dev Module         74HC595 (First IC) Pin
---------------------------------------------------------------------
GPIO 14 (DATA)       ->  Pin 14 (DS / SER)
GPIO 13 (CLOCK)      ->  Pin 11 (SH_CP / SRCLK) [Parallel to all 8 ICs]
GPIO 12 (LATCH)      ->  Pin 12 (ST_CP / RCLK)  [Parallel to all 8 ICs]
GPIO 19 (OE)         ->  Pin 13 (OE) [Active LOW or tied to GND]
3.3V / 5V            ->  Pin 10 (MR / SRCLR) [Tied to VCC]
GND                  ->  Pin 8  (GND) [Common Ground with Driver Board]
```

**Shift Register Cascading:**
- `IC 1 (Pin 9 / Q7S)` ➔ `IC 2 (Pin 14 / DS)` ➔ ... ➔ `IC 8 (Pin 14 / DS)`
- **Valve Channels:**
  - IC 1: CH 1 – 8
  - IC 2: CH 9 – 16
  - IC 3: CH 17 – 24
  - IC 4: CH 25 – 32
  - IC 5: CH 33 – 40
  - IC 6: CH 41 – 48
  - IC 7: CH 49 – 56
  - IC 8: CH 57 – 63 (CH 64 spare)

### 2. Ejector Byte Protocol
Communication between PC and ESP32 Ejector Controller operates at `115200` baud:
- **Turn Channel ON**: Send single byte `CH` (e.g. `22` = Channel 22 ON)
- **Turn Channel OFF**: Send single byte `CH + 100` (e.g. `122` = Channel 22 OFF)
- **Turn ALL Channels OFF**: Send byte `0` or command `ALL_OFF\n`

---

## 📂 Project Structure

```
d:/lambton-main/
├── LAMTON_LINE_NEW_ESP32.py     # Main application entry point & vision HMI
├── ejector_link.py              # High-speed ESP32 ejector protocol link
├── esp32_logic.py               # ESP32 controller & Modbus relay logic
├── port_detect.py               # Auto-detects COM ports for ejectors, relays, feeders
├── port_settings_ui.py          # COM port configuration interface
├── ejector_test_panel.py        # Dedicated 63-channel valve testing & diagnosis tool
├── mvsdk.py                     # MindVision Line-Scan Camera SDK wrapper
├── MVCAMSDK_X64.dll             # MindVision 64-bit native camera library
├── camera_config.json           # FRONT & REAR camera calibration (exposure, gains, bounds)
├── bg_config.json               # Background thresholds and band-pass settings
├── recipes.json                 # Commodity presets (Rice, Dhall, Cashew, Plastics)
├── light_config.json            # LED lighting brightness & channel states
├── START_LAMTON_SORTER.bat      # One-click launcher for the sorting system
├── RUN_EJECTOR_TEST_PANEL.bat   # Launcher for standalone ejector diagnostic panel
├── firmware/                    # Production ESP32 Arduino firmware sketches
│   ├── ESP32_63CH_Ejector_TEXT_v3/
│   └── ESP32_63CH_Ejector_v2_3/
├── deploy/                      # Deployment scripts & offline dependency wheels
└── tools/                       # Hardware diagnostic, calibration, and simulation tools
```

---

## 🚀 Getting Started

### Prerequisites
1. **Operating System**: Windows 10 / 11 64-bit.
2. **Python**: Python 3.10 to 3.13.
3. **MindVision Camera Drivers**: Install MindVision GigE camera software driver suite.

### Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/pandi7010/sorting.git
   cd sorting
   ```

2. Create and activate a Python virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\activate
   ```

3. Install required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Running the Application

- **To start the Full Optical Sorter**:
  Double-click `START_LAMTON_SORTER.bat` or run:
  ```bash
  python LAMTON_LINE_NEW_ESP32.py
  ```

- **To run the Standalone 63-Channel Ejector Diagnostic Tool**:
  Double-click `RUN_EJECTOR_TEST_PANEL.bat` or run:
  ```bash
  python ejector_test_panel.py
  ```

---

## 🛠 Calibration & Tuning Guide

1. **Camera Alignment**: Open **Camera Setup** to verify FRONT and REAR GigE connections, set exposure time, and confirm ROI width.
2. **Background Tuning**: Open **Program Params -> Background Tuning (ESP32)**:
   - Adjust `WHITE (HI)` and `BLACK (LO)` thresholds to isolate good commodity from background.
   - Adjust `BLUE REF`, `GREEN REF`, and `RED REF` to balance camera hardware RGB electronic gains.
   - Adjust `START` and `END` draggable handles to align pixel boundaries with chute edges.
3. **Ejector Testing**:
   - Click individual channel boxes (1–63) on the top indicator bar to fire test pulses.
   - Sweep a test object (needle or wire) across the chute: verify that only the matching 64-pixel channel activates and immediately turns off when cleared.

---

## 📄 License & Ownership
Copyright © 2026. All rights reserved.
Developed for industrial optical sorting automation and high-speed vision inspection.
