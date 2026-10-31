# LAMTON_V1 — 63-channel line-scan colour sorter

## The machine
- Optical colour sorter, **63 channels**, waterfall chute **330 mm** wide.
- **Front + rear MindVision 4096-px colour line-scan cameras** (GigE), found by serial number (`camera_config.json`: FRONT 076011622362, REAR 075002923007). Model, line rate and trigger support are **unknown** until `tools/camera_probe.py` is run on the machine.
- **63 matrix valves**, 1 ms response, 24 V; drive board with **5 V logic inputs**.
- **Ejector ESP32** → 8 × 74HC595 → drive board (USB). **Relay ESP32**: 8 relays, Modbus RTU 9600 — relay 1 feeder, 2 main air, 3 wiper, 4–8 lights. Feeder speed also via a Nuvoton UART board. COM numbers differ per PC (machine: ejector COM24, relay COM13, feeder COM7) and are found by `port_detect.py` - never hard-code one.
- Nozzles are now **35–40 mm** below the scan line; plan to move them to **55 mm** after measuring latency/speed.
- Products: rice, pulses, dhall (insect holes), plastics, almonds, cashews (grades + defects), seeds down to 3 mm. Target 99.9 %.
- CNN training project: `D:\CNN\CNN` (**read only**; ImageFolder, 224 × 224, MobileNetV2).
- The **development PC is not the machine PC**: UUID `90001563-B3AE-…`; the sorter runs here only in **DEVELOPER MODE** (`dev_unlock.json`, not in git). Ejector ESP32 (CP2102) when plugged in; no cameras. D: is a USB HDD; `D:\LAMTON_V1` = untouched copy of the original.
- The new machine PC has **no GPU** (maybe later). Real-time sorting = OpenCV contour + HSV; **CNN required on CPU** (PyTorch MobileNetV2). CNN project `D:\CNN\CNN` is a separate app: use only `app	raining.py`; **never** `cashew_cnn_grader.py` (other customer's lock) or `run_grader.bat` (kills all python.exe).

## Key files
- **Entry point:** `LAMTON_LINE_NEW_ESP32.py` (`START_LAMTON_SORTER.bat`). `MAIN_LAUNCHER.py` is broken (missing `main1`).
- `ejector_link.py` — PC side of the binary ejector protocol (115200; v2.0-v2.2 at 921600 still found; old text firmware told apart by protocol).
- **`port_detect.py`** (+ `port_settings_ui.py` = COM PORTS dialog) — finds ejector / relay by their real replies on ANY COM port; **no COM number is hard-coded anywhere**; feeder is silent (never probed). Used by the sorter start-up, the test panel, the kit, SYSTEM_CHECK.
- `ejector_test_panel.py` (`RUN_EJECTOR_TEST_PANEL.bat`, `--sim`) — reusable 63-ch test widget, standalone or embedded via `link_provider`; integrated in the sorter as footer button EJECTOR TEST PANEL (switch `ejector_test_panel_button`, ON by the user's decision). Simulator: `tools/ejector_sim.py`.
- `features.py` + `features_config.json` — ON/OFF switches for everything after Phase 1 (**all OFF** except `ejector_test_panel_button`).
- Phase 2: `timing_log.py`, `tools/latency_report.py`, `tools/ball_speed.py`, `object_stream.py`.
- Phase 3 (disabled): `line_sync.py`, `line_sync_config.json`, `channel_map.py`, `tools/camera_apply_timing.py`, `tools/channel_calibration.py`, `tools/recipe_camera_timing.py`.
- Phase 4: `capture_engine.py`, `capture_ui.py`, `tools/continuous_capture.py`, `tools/disk_speed_test.py`; `product_presets.json`.
- Firmware: **working originals** `ESP32_63CH_ShiftRegister/`, `ESP32_8CH_ModbusRelay_NEW/`; **current ejector = `ESP32_63CH_Ejector_v2_3/` (115200 baud, PIN_PROFILE 3 default = DATA 23/CLOCK 18/LATCH 19, no OE; LED only during pulses; auto-arm; silent watchdog; IDENTIFY) - NOT hardware-tested**; `_v2_2` (PROFILE 3, 921600), `_v2_1` (PROFILE 1 = old wiring 14/13/12/19, 921600), `_v2_0` = superseded; `ESP32_63CH_Ejector_v2_1_DIAG/` = measuring tool, `ESP32_63CH_Ejector_v3_0/` = line sync (disabled); relay: `ESP32_8CH_ModbusRelay_NEW/` proven, `ESP32_8CH_ModbusRelay_v2_0/` recommended later (never run on a board); obsolete: `ESP32_63CH_LATEST/`, `OLD_ESP32_CODE/`, `ESP32_8CH_ModbusRelay/` (Modbus master), `ESP32_Relay_PinFinder/` (tool).
- Developer mode: `DeveloperMode` + `DEV_UUIDS` in the sorter, `tools/make_dev_unlock.py`; needs file + matching UUID + UUID in `DEV_UUIDS`. Licence lock: `ALLOWED_UUID`/`ALLOWED_CAMERAS` near the end of the sorter; key generator `LICENCE_BOSS.py` is never shipped.
- Deployment: `deploy/make_deploy_package.py <new folder>` (from committed HEAD, never overwrites; refuses dev_unlock files) + templates in `deploy/`; **package v2 = `D:\LAMTON_V2_DEPLOY_v2`** (CPU-only CNN, tested via INSTALL/SYSTEM_CHECK/START_SORTER from a copy). CNN: `cnn/` (training.py, models, ImageNet weights, check_cnn.py), `deploy/UPGRADE_TO_GPU.*`; `tools/system_check.py` (read-only PASS/FAIL, also `SYSTEM_CHECK.bat`); icons in `assets/`.
- Camera SDK: `mvsdk.py` + `MVCAMSDK_X64.dll` (2.1.10.195, = SDK installer in Downloads; the SDK must be installed on any PC); full C API in `C:\Program Files (x86)\Mindvision\Demo\VC++\Include\CameraApi.h`.
- Tests (no hardware): `tools/test_*.py` — every one must print **ALL TESTS PASSED**.

## Standing rules (from the user)
1. **Never overwrite working firmware** — new firmware goes in new, versioned folders. Never delete existing files.
2. **Back up camera settings before any camera change**; camera changes only via a button/script the user runs — never automatic.
3. **New features OFF by default** (switch in `features_config.json`), so the machine behaves like the last confirmed phase. Exception by the user: `ejector_test_panel_button` ON.
4. **Commit each phase separately** with a clear message.
5. Test with simulation where possible and **mark clearly what still needs real hardware**.
6. The user is **learning**: explain simply, with examples. No questions while the user is away — choose the safest option and log it.
7. Licence: never weaken it for customer PCs; never touch the licence generator.
8. CNN must stay optional and never block the colour/HSV/contour ejection path; don't change the real-time ejection path without approval.

## Practical notes
- Git: `C:\Program Files\Git\cmd\git.exe` (may not be on PATH). Ignored: `build/ dist/ dataset_capture/ logs/ .venv_build/`.
- `LAMTON_LINE_NEW_ESP32.py` uses **CRLF**; keep it when editing with scripts.
- Starting the main window in tests rewrites `ejector_config.json` (original start-up behaviour) — restore it afterwards.
- ESP32 compile: the arduino-cli scratchpad toolchain was **deleted** (16 Oct); use Arduino IDE (board package 3.3.12, used for the upload) or reinstall arduino-cli. Long paths: map the scratchpad with `subst W:` (PyQt5/venv and ESP32 builds exceed 260 chars otherwise).
- Bash heredocs collapse `\` → `\`: write patch scripts / .bat files with the Write tool, not heredocs. The sorter's start-up also rewrites `system_config.json` here (relay probe) — restore with git after tests.
- Sorter needs Python 3.10–3.13 (numpy 2.2.6). The sorter imports **torch first** (before cv2/PyQt5; otherwise WinError 1114) - keep that order; torch is optional. CNN = torch 2.14.0 / torchvision 0.29.1 CPU.
- The PowerShell tool refuses `Remove-Item` on some paths (e.g. `C:\LAMTON_V2_TEST`); `rm -rf` in bash worked for a user-requested delete. Test harnesses: start the sorter via `runpy` with `QT_QPA_PLATFORM=offscreen`; don't import PyQt5 before it (changes the torch import order).

## Documents
`ANALYSIS.md` (overview) · `TIMING_DESIGN.md` (latency analysis, redesign) · `PHASE1_GUIDE.md` (firmware, wiring, tests T0–T8) · `PHASE2_GUIDE.md` · `PHASE3_GUIDE.md` · `PHASE4_GUIDE.md` · `INSTALL_NEW_PC.md` (old exe/Phase 6 guide) · `deploy/INSTALL_NEW_PC.md` (+ Tamil, package guide) · `MERGE_PLAN.md` (done) · **`TOMORROW_CHECKLIST.md`** (+ `_TAMIL`) · **`PROGRESS_LOG.md`** (status, commits, decisions, open questions, next step).
