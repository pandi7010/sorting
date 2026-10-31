# LAMTON V2 — installing the sorter on a NEW PC

For a PC with both MindVision line-scan cameras, the ejector ESP32 (63 valves, 74HC595), the relay ESP32
(8 relays) and the feeder board connected. Package version: see `VERSION.txt`.

**Golden rules**
- **Never overwrite or delete the old sorter program** on that PC. V2 goes into its own folder, `C:\LAMTON_V2`.
- **Back up the camera settings first** (step 7). Change camera settings only with the tools, after the backup.
- Firmware: keep the old firmware files. They are in `firmware\ROLLBACK_OLD\`.
- First run with **main air closed and the chute empty**.

---

## 0. What is in the package

| Folder / file | What it is |
|---|---|
| `app\` | the sorter program (latest committed version). It includes the **EJECTOR TEST PANEL** button, recipes, configs, `best.pt` (YOLO), CNN models, `MVCAMSDK_X64.dll` |
| `firmware\` | ejector v2.0 (old wiring, the file uploaded on 16 Oct 2026), relay v2.0, `ROLLBACK_OLD\` (original firmware of both boards) |
| `ejector_test_kit\` | standalone 63-valve test panel (the sorter must be closed while you use it) |
| `tools\` | `CAMERA_PROBE`, `CAMERA_RESTORE`, `BALL_SPEED`, `LATENCY_REPORT`, `DISK_SPEED_TEST` (launchers for `app\tools`) |
| `drivers\` | MindVision Camera Platform Setup 2.1.10.195, CP210x driver, Arduino IDE 2.3.10, Python 3.12.10 |
| `INSTALL.bat` / `START_SORTER.bat` / `SYSTEM_CHECK.bat` | install / start / read-only check |
| `requirements.txt`, `requirements_cnn.txt`, `constraints.txt` | exact Python package versions (sorter + CNN, CPU only) |
| `cnn\` | CNN code (`training.py`), trained models, ImageNet starting weights, `RUN_CNN_TRAIN.bat`, `check_cnn.py` (no datasets) |
| `UPGRADE_TO_GPU.bat` / `.md` | later: replaces the CPU PyTorch by the matching CUDA build |
| `MAKE_OFFLINE_WHEELS.bat` | prepares an offline install (step 5b) |

Not included on purpose: the licence key generator and its customer database, development scripts,
logs, captured datasets, and build folders.

---

## 1. Windows settings (once)
- Power plan **High performance**. The PC must never sleep. Turn **USB selective suspend** off.
- Device Manager → each network adapter → Power Management: untick "Allow the computer to turn off this device".
- Antivirus: exclude `C:\LAMTON_V2` and the dataset capture folder.
- Keep **20 GB or more free** on the program disk. The capture tool stops writing below 1 GB.

## 2. MindVision camera SDK + network (GigE cameras)
1. Run `drivers\MindVision Camera Platform Setup(2.1.10.195_202604021438).exe` (as administrator).
   - Install **this exact version, 2.1.10.195**. `app\MVCAMSDK_X64.dll` is the same version.
   - The installer is not digitally signed. It is the same file as on the development PC.
2. **How the program finds the camera DLL:** `app\mvsdk.py` loads `MVCAMSDK_X64.dll` from the `app`
   folder first. If that fails, Windows finds the copy that the SDK installer put in
   `C:\Windows\System32`. The DLL loads the camera plug-ins (`GiGeCamera_X64.Interface`, ...) from the
   SDK folder that the installer writes into the registry (`DeviceInterfacePath`). So the program
   works from **any** folder, but **only after the SDK is installed**. SYSTEM_CHECK compares both DLL versions.
3. Give each camera its own network port with a **fixed IP in the camera's subnet**
   (`app\camera_config.json`: FRONT 192.168.3.206, REAR 192.168.1.101):
   - camera port 1: PC IP **192.168.3.10**, mask 255.255.255.0
   - camera port 2: PC IP **192.168.1.10**, mask 255.255.255.0

   The program finds the cameras by **serial number**, not by IP, but each camera must be reachable.
4. For each camera adapter, open Properties → Advanced and set:
   - **Jumbo Packet 9014**
   - **Receive Buffers = maximum**
   - **Energy-Efficient Ethernet off**
5. Windows Firewall: allow the program and MVDCP on these networks.
6. Open **MVDCP** (MindVision tool). Both cameras must be visible. Close MVDCP again.

## 3. CP210x USB driver (ESP32 boards)
- Unzip `drivers\CP210x_Universal_Windows_Driver.zip`, right-click `silabser.inf` and choose **Install**.
- Plug in the boards **one at a time**. Device Manager → Ports shows "Silicon Labs CP210x … (COMxx)". Write down the numbers:
  - ejector ESP32 = COM__
  - relay ESP32 = COM__
  - feeder (Nuvoton) board = COM__
- A board with a CH340 or CH9102 chip needs the WCH driver instead.

## 4. Copy the package
Copy the whole folder to **`C:\LAMTON_V2`**. Don't run it from the USB drive (slow, and the drive letter changes).
```
robocopy D:\LAMTON_V2_DEPLOY C:\LAMTON_V2 /E
```
If `C:\LAMTON_V2` already exists, choose a new name (for example `C:\LAMTON_V2b`). Never copy over an
older installation.

## 5. Python + packages: `INSTALL.bat`
**a) With internet (normal)**
1. If there is no Python 3.10–3.13 (64-bit): run `drivers\python-3.12.10-amd64.exe` and tick
   **"Add python.exe to PATH"**. Python 3.14 is too new for numpy 2.2.6.
2. Double-click **`C:\LAMTON_V2\INSTALL.bat`**. It finds Python, creates `C:\LAMTON_V2\.venv` and
   installs the exact versions. It doesn't change the system Python or the old program.
3. Step 4 of INSTALL.bat installs the **CNN packages, CPU only** (about 350 MB): PyTorch 2.14.0, torchvision 0.29.1,
   ultralytics 8.4.163, ONNX. These are the versions of the CNN project. There is **no GPU choice** and no CUDA package.
   CNN inference and CNN training both run on the CPU. If this step fails, the sorter still starts and sorts
   (colour / HSV / contour); only the CNN is unavailable. Run `INSTALL.bat` again later.
4. Folder name: if the folder path is longer than 90 characters, INSTALL.bat stops and asks you to use a short
   folder such as `C:\LAMTON_V2` (Windows 260-character limit breaks the PyQt5 install).
5. **GPU later:** see `UPGRADE_TO_GPU.md`. One step: `UPGRADE_TO_GPU.bat`.

**b) Without internet (offline)**
1. On **any PC with internet**, run `MAKE_OFFLINE_WHEELS.bat 3.12` (or `3.11`, `3.10`, `3.13`).
   It downloads all packages for that Python / 64-bit Windows into `wheels\` (about 350 MB).
2. Copy the package **including `wheels\`** to the new PC. Install `drivers\python-3.12.10-amd64.exe`.
3. Run `INSTALL.bat`. It sees `wheels\` and installs **without internet**.

## 6. `SYSTEM_CHECK.bat` (read-only)
Close the sorter first. The check prints PASS / WARN / FAIL for:
- Python and packages;
- the MindVision SDK and DLL;
- **both cameras** (model, serial number, IP);
- free disk space and write speed;
- **every ESP32 board** (port and firmware version);
- the GPU;
- the **licence**.

It changes nothing:
- cameras are only listed;
- the ejector gets a text PING;
- the relay board gets a Modbus *read*;
- the feeder board is not contacted;
- the disk test writes one temporary file and deletes it.

Fix every **FAIL** before production.

## 7. Back up the camera settings FIRST
```
tools\CAMERA_PROBE.bat
```
This is read-only. It writes `app\camera_backups\<date>\`: one MindVision `.config` file per camera,
a `.json` copy, and `probe_report.txt`. Copy that folder to the USB drive as well.

To restore later (the path is relative to `app`, or give the full path):
`tools\CAMERA_RESTORE.bat camera_backups\<date>\FRONT_<SN>.config`

## 8. Firmware
- **Ejector ESP32:**
  - If SYSTEM_CHECK shows **"ejector firmware v2.3"**, nothing needs to be done. This is the same board you uploaded on 16 Oct 2026.
  - Otherwise upload `firmware\ESP32_63CH_Ejector_v2_3`. It is PIN_PROFILE 1, the old wiring.
- **Relay ESP32: v2.0 is NOT required.** The program reads the relay firmware version (Modbus register 11).
  With the old firmware it prints "Old relay firmware (no watchdog)" and works as before. v2.0 is
  **recommended later**: if the PC freezes, it switches feeder, main air and wiper OFF within 0.5 s. But it
  has **never run on a real board yet**. Start with the old relay firmware. Later, upload
  `firmware\ESP32_8CH_ModbusRelay_v2_0` and do test T6 (`app\PHASE1_GUIDE.md`).
- **Upload steps:**
  1. Install `drivers\arduino-ide_2.3.10_Windows_64bit.exe`.
  2. Boards Manager → **esp32 by Espressif 3.3.12** (it needs about 9 GB on C:).
  3. Board **ESP32 Dev Module**, **Upload Speed 115200**, the board's COM port. Close the sorter first.
  4. Ejector only: unplug the 74HC595 ribbon cable while uploading.
  5. If it stays at "Connecting…", hold the **BOOT** button.
  6. Success = "Hash of data verified". Serial Monitor: **115200** for ejector v2.3 and the old ejector firmware, **9600** for the relay.

  Details: `firmware\README.txt`.

## 9. Settings: COM ports and camera serial numbers
**You do not have to type COM numbers.** The program finds the ejector and the relay board on ANY COM port by their real replies:
- At every start: the saved port is used if the board there answers as expected; otherwise all COM ports are searched, the port that
  answers correctly is used, a clear message is shown and the new port is saved in `system_config.json`.
- **ENGINEER login -> footer button COM PORTS:** one dropdown each for ejector, relay and feeder (port name + USB chip + description),
  **Disabled**, **Refresh**, **AUTO DETECT**, **Test** and **SAVE AND CONNECT**.
- A device that is **Disabled** (or `modbus_enabled = false`) never gets a port opened or probed. One port is never given to two devices;
  a port that answered as the ejector never receives relay (Modbus) bytes.
- The **feeder (Nuvoton) board never answers**, so it cannot be detected: choose its port by hand in COM PORTS.
- `SYSTEM_CHECK.bat` section 5 shows what every port answers and what the sorter would use.

Edit `C:\LAMTON_V2\app\camera_config.json`: put each camera's serial number in `"FRONT": {"sn": …}` and `"REAR": {"sn": …}`.
SYSTEM_CHECK lists the serial numbers it finds.

Note: at every start the program rewrites `app\ejector_config.json` itself. That's normal.

## 10. Licence: what happens on a new PC
There are **two locks**:

| Lock | Checks | On a new PC |
|---|---|---|
| **1. Start-up hardware lock**, `app\LAMTON_LINE_NEW_ESP32.py` lines 13520–13521 | `ALLOWED_UUID = "03000200-0400-0500-0006-000700080009"` **and** both cameras `075002923007`, `076011622362` connected | different UUID → **"Access Denied (Hardware Mismatch)"**, and the program closes |
| **2. Licence key**, `app\tcm_license.dat` | first 4 hex digits of the UUID + expiry date + signature | different UUID → "HARDWARE MISMATCH": START is blocked |

What you must do (manufacturer):
1. `SYSTEM_CHECK.bat` → section 7 shows **"this PC's UUID: …"**.
2. If it differs from `03000200-0400-0500-0006-000700080009`, change **line 13520** in
   `C:\LAMTON_V2\app\LAMTON_LINE_NEW_ESP32.py` to the new UUID. If a camera was replaced, also change
   `ALLOWED_CAMERAS` on line 13521 and the `sn` in `camera_config.json`.
3. On the manufacturer PC, generate a key for the new UUID with **LICENCE_BOSS** (deliberately NOT in this package).
4. Start the sorter → **INFO** → **🔑 MANAGE / UPDATE LICENSE** → enter the key. It writes `tcm_license.dat`.
5. SYSTEM_CHECK section 7: every line must be PASS ("the sorter WILL start on this PC").

Note: `03000200-0400-0500-0006-000700080009` looks like a generic factory UUID. Some motherboards report
exactly this value, and then both locks pass without changes.

## 11. First run, safely
1. **Main air closed** (manual valve), **chute empty**, feeder empty, nobody near the nozzles.
2. `START_SORTER.bat`. The main window opens. **Do not press START SYSTEM yet.**
3. Log in as **ENGINEER**. The **EJECTOR TEST PANEL** button is locked for OPERATOR.
4. **EJECTOR TEST PANEL**. It uses the sorter's own ejector connection. Sorting ejections are paused while it is open.
   - Badge **"v2.3 BINARY · ARMED"**; counters `crc_err 0`, `len_err 0`.
   - Click channels 1, 32 and 63. The drive-board LEDs light.
   - AUTO SEQUENCE 1–63, pulse 30 ms, gap 300 ms. Walk along the valve bar: the order must be 1 → 63.
     If a channel fires the wrong valve, use **CHANNEL REMAP** → swap → **Save to ESP32**.
   - STOP ALL / Esc stops at once.
5. **Open the air.** AUTO SEQUENCE 1–63, **pulse 4 ms**, gap 200 ms. Every valve must click.
6. Close the panel → START SYSTEM with a **low feed** of known product.
   - Check the reject bin: little good product in it.
   - Check the counters: `no-ack` and `crc` stay near 0.
7. Only then increase the feed. Keep the old program untouched until V2 has run a full shift.

## 12. Rollback
- **Program:** close V2. Start the old program from its own folder, exactly as before. Nothing of it was changed.
- **Firmware:** upload `firmware\ROLLBACK_OLD\ESP32_63CH_ShiftRegister` (ejector) or
  `firmware\ROLLBACK_OLD\ESP32_8CH_ModbusRelay_NEW` (relay). Both programs detect the old firmware automatically.
- **Camera settings:** `tools\CAMERA_RESTORE.bat` with the backup from step 7.

## 13. Still needs the real machine (not tested on the development PC)
- the cameras: enumeration, settings backup, live images, line rate. There were no cameras on the development PC;
- the hardware lock and licence with the new PC's UUID and the two cameras;
- relay ESP32 (old and v2.0) and the feeder board through the program;
- the 74HC595 board and drive-board LEDs, valve order 1–63, real valves with air;
- the GPU upgrade (`UPGRADE_TO_GPU.bat`): no NVIDIA GPU on the development PC; CNN training speed on the new PC's CPU;
- the disk speed of the new PC's capture disk;
- a full sorting run with product.
