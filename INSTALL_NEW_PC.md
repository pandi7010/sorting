# Installing the LAMTON sorter on a new PC

*Phase 6 — prepared 16 Oct 2026. The build itself was NOT run tonight (see the end of this file).*

## 0. Read this first: the licence (what happens on a new PC)
The program has **two independent locks**:

| Lock | Where | What it checks | On a new PC |
|---|---|---|---|
| **1. Hardware lock** (at program start) | `LAMTON_LINE_NEW_ESP32.py`, bottom: `ALLOWED_UUID = "03000200-0400-0500-0006-000700080009"`, `ALLOWED_CAMERAS = ["075002923007", "076011622362"]` | motherboard UUID **and** both camera serial numbers | Different UUID → **"Access Denied (Hardware Mismatch)" and the program closes.** A replaced camera does the same. |
| **2. Licence key** (`tcm_license.dat`) | `LicenseManager` | key = first 4 hex of the UUID + expiry days + signature of the full UUID | Different UUID → **"HARDWARE MISMATCH"**: START and camera connection are blocked |

- Current key `0300-3327-C04F-E90A` → valid until **18 Sep 2027**, for UUID `03000200-…`.
- That UUID looks like a **generic factory default** that some motherboards report. A new PC with the same kind of board could pass by coincidence; a different board will not.
- This development PC has UUID `90001563-B3AE-…`, so the sorter refuses to start here (expected).

**To move to a new PC (manufacturer step):**
1. On the new PC: `powershell (Get-CimInstance Win32_ComputerSystemProduct).UUID` → note the UUID.
2. Manufacturer: change `ALLOWED_UUID` in `LAMTON_LINE_NEW_ESP32.py` (and `ALLOWED_CAMERAS` if a camera changed) and rebuild. In `LICENCE_BOSS.py`, generate a new key for that UUID.
3. On the new PC: enter the key in the licence window (it writes `tcm_license.dat`).

Nothing in the licence was changed in Phases 1–4. ⚠ The signature salt and the LICENCE_BOSS passwords are plain text in the source code, so anyone with the source can make keys. Keep the source private.

## 1. Recommended PC
| Part | Recommendation | Why |
|---|---|---|
| CPU | 6+ cores (i5/i7 12th gen or newer) | 2 cameras + detection + capture threads |
| RAM | 16 GB (32 GB for training) | frame queues, YOLO/CNN |
| System disk | NVMe SSD 512 GB+ | |
| **Capture disk** | **separate internal NVMe SSD ≥ 1 TB, ≥ 1000 MB/s** | strips need 25–50 MB/s continuous (2 cameras, JPG) and more with PNG. A USB hard disk (35 MB/s measured here) is too slow. |
| Network | **one Gigabit port per camera** (Intel i210/i350 recommended) | each camera streams up to ~120 MB/s |
| USB | 3 free ports (ejector ESP32, relay ESP32, Nuvoton feeder) | |
| GPU (optional) | NVIDIA, 6 GB+ | YOLO/CNN training; not needed for sorting |
| Windows | 10/11 64-bit | |

Check the disk with `python tools\disk_speed_test.py --dirs <capture folder>`.

## 2. Windows settings
- Power plan **High performance**; never sleep; turn off USB selective suspend (Power options → Advanced).
- Device Manager → each network adapter → Power Management: untick "Allow the computer to turn off this device".
- Windows Update: set active hours to cover the production shift.
- Antivirus: exclude the program folder and the capture folder (scanning every crop slows writing).

## 3. MindVision camera SDK + network
1. Install the **MindVision platform / SDK** (the same installer that created `C:\Program Files (x86)\Mindvision\`; SDK version on this PC: 2.1.10.195). It installs the GigE driver and the MVDCP tool.
2. Give each camera its own network port with a **fixed IP** in the camera's subnet. `camera_config.json` has FRONT 192.168.3.206 and REAR 192.168.1.101, so for example: port 1 = 192.168.3.10 / 255.255.255.0, port 2 = 192.168.1.10 / 255.255.255.0. The program finds cameras by **serial number**, not IP, but each camera must be reachable.
3. Network adapter → Advanced: **Jumbo packet 9014**, **Receive buffers = maximum**, Interrupt moderation **on** (adaptive), **Energy Efficient Ethernet off**, Flow control on.
4. Windows Firewall: allow the program and MVDCP on these private networks (or turn the firewall off on the two camera ports only).
5. Open MVDCP → both cameras visible → close MVDCP. Then run `python tools\camera_probe.py` (backup + report).

## 4. ESP32 boards (USB serial)
1. Install the USB-serial driver for the chip on each ESP32 board: **CH9102 / CH343** → WCH CH343SER; **CP2102** → Silicon Labs CP210x VCP; **CH340** → WCH CH341SER.
2. Plug in one board at a time. Device Manager → Ports → note the COM number. To fix a number: right-click → Properties → Port Settings → Advanced → COM Port Number.
3. Put the numbers in `system_config.json`: `"ejector_port"` (ejector ESP32), `"last_port"` (relay ESP32), `"vibrator_port"` (Nuvoton).
4. Test: `python gui_esp32_ejector_tester.py` → CONNECT → "v2.0 BINARY".

## 5. Program — option A: Python (recommended while still changing things)
1. Install **Python 3.12 64-bit** (tick "Add to PATH").
2. Copy the whole program folder (see section 7).
3. `cd` into the folder → `python -m pip install -r requirements.txt`
   (GPU: install the CUDA build of torch/torchvision first, from pytorch.org, matching your NVIDIA driver).
4. Run the offline tests: every `python tools\test_*.py` must print **ALL TESTS PASSED**.
5. Start with `START_LAMTON_SORTER.bat`. Auto-start: `SETUP_AUTOSTART.bat` (for the exe) or `create_shortcut.vbs` (edit its path first).

## 6. Program — option B: standalone .exe
On a build PC (Python 3.12 + about 5 GB free): run **`BUILD_STANDALONE_EXE_v2.bat`**. It makes a clean `.venv_build`, installs `requirements.txt`, runs 2 offline tests, then PyInstaller with **all Phase 1–4 modules, config files and `MVCAMSDK_X64.dll`**.
Copy `dist\LAMTON_COLOUR_SORTER\` to the machine. Settings files are inside `dist\LAMTON_COLOUR_SORTER\_internal\`.
The old `BUILD_STANDALONE_EXE.bat` is unchanged. It does **not** include the new modules' config files, so use v2.

## 7. Files to move (settings, recipes, data)
| File / folder | What it is |
|---|---|
| `recipes.json` | all product recipes (algorithms, sensitivity, ejector timing, camera timing) |
| `camera_config.json`, `bg_config.json` | camera serials, exposure, gains, chute pixels, background thresholds |
| `ejector_config.json`, `light_config.json`, `wiper_config.json`, `system_config.json`, `system_info.json` | machine settings, COM ports, branding |
| `features_config.json`, `line_sync_config.json`, `product_presets.json` | Phase 2–4 switches and settings (all OFF by default) |
| `channel_calibration.json` (if made) | pixel ↔ valve calibration |
| `tcm_license.dat` | licence (must be re-issued for a new UUID, see section 0) |
| `best.pt` and other models | YOLO / CNN models |
| `camera_backups\` | camera settings backups (keep!) |
| `dataset_capture\` | captured images (large; copy only if needed) |
| `logs\` | timing logs and reports (optional) |

The camera's own settings (line rate, frame height, trigger) live in the camera / MindVision's files. After moving, run `tools\camera_probe.py` and compare with the old report. Restore from `camera_backups\` with `tools\camera_restore.py` if needed.

## 8. Final check on the new PC
1. `tools\camera_probe.py` → both cameras, same settings as before.
2. Ejector tester → CONNECT → SWEEP 1–63 (air off).
3. Sorter starts (licence OK) → relays OK → START with air off → STOP.
4. `tools\disk_speed_test.py` on the capture disk.

## Not done tonight
The standalone build was **not run**: this PC has only 7.4 GB free on C: and torch alone needs several GB. `BUILD_STANDALONE_EXE_v2.bat` is written but **untested**. Run it on a PC with ≥ 5 GB free.
