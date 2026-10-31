# LAMTON V2 — புதிய கணினியில் sorter நிறுவுதல்

இரண்டு MindVision line-scan camera-கள், ejector ESP32 (63 valve-கள், 74HC595), relay ESP32
(8 relay-கள்) மற்றும் feeder board இணைக்கப்பட்ட கணினிக்கு. Package version: `VERSION.txt` பார்க்கவும்.

**முக்கிய விதிகள்**
- அந்த கணினியில் உள்ள **பழைய sorter program-ஐ ஒருபோதும் மேலெழுதவோ அழிக்கவோ கூடாது.** V2 தனி folder-இல்: `C:\LAMTON_V2`.
- **முதலில் camera settings backup** (படி 7). Backup-க்குப் பிறகு, tools மூலம் மட்டுமே camera settings மாற்றவும்.
- பழைய firmware file-களை வைத்திருக்கவும்: `firmware\ROLLBACK_OLD\`.
- முதல் இயக்கம்: **main air மூடப்பட்டு, chute காலியாக** இருக்க வேண்டும்.

---

## 0. Package-இல் என்ன உள்ளது

| Folder / file | என்ன |
|---|---|
| `app\` | sorter program (கடைசியாக commit செய்த version). **EJECTOR TEST PANEL** button, recipes, configs, `best.pt` (YOLO), CNN models, `MVCAMSDK_X64.dll` உட்பட |
| `firmware\` | ejector v2.0 (பழைய wiring, 16 அக் 2026 அன்று upload செய்த file), relay v2.0, `ROLLBACK_OLD\` (இரண்டு board-களின் பழைய firmware) |
| `ejector_test_kit\` | தனியாக இயங்கும் 63-valve test panel (பயன்படுத்தும் போது sorter மூடப்பட்டிருக்க வேண்டும்) |
| `tools\` | `CAMERA_PROBE`, `CAMERA_RESTORE`, `BALL_SPEED`, `LATENCY_REPORT`, `DISK_SPEED_TEST` (`app\tools`-க்கான launcher-கள்) |
| `drivers\` | MindVision Camera Platform Setup 2.1.10.195, CP210x driver, Arduino IDE 2.3.10, Python 3.12.10 |
| `INSTALL.bat` / `START_SORTER.bat` / `SYSTEM_CHECK.bat` | நிறுவுதல் / தொடங்குதல் / படிக்க-மட்டும் சோதனை |
| `requirements.txt`, `requirements_cnn.txt`, `constraints.txt` | Python package-களின் சரியான versions (sorter + CNN, CPU மட்டும்) |
| `cnn\` | CNN code (`training.py`), பயிற்சி பெற்ற models, ImageNet தொடக்க weights, `RUN_CNN_TRAIN.bat`, `check_cnn.py` (datasets இல்லை) |
| `UPGRADE_TO_GPU.bat` / `.md` | பின்னர்: CPU PyTorch-ஐ பொருந்தும் CUDA build-ஆக மாற்றும் |
| `MAKE_OFFLINE_WHEELS.bat` | internet இல்லாமல் நிறுவ தயாரிப்பு (படி 5b) |

வேண்டுமென்றே சேர்க்கப்படாதவை: licence key generator மற்றும் அதன் வாடிக்கையாளர் database,
development scripts, logs, சேகரித்த datasets, build folder-கள்.

---

## 1. Windows அமைப்புகள் (ஒரு முறை)
- Power plan **High performance**. கணினி ஒருபோதும் sleep ஆகக் கூடாது. **USB selective suspend** OFF.
- Device Manager → ஒவ்வொரு network adapter → Power Management: "Allow the computer to turn off this device" டிக்கை எடுக்கவும்.
- Antivirus: `C:\LAMTON_V2` மற்றும் capture folder-ஐ விலக்கவும் (exclude).
- Program disk-இல் **20 GB-க்கு மேல் காலி இடம்** வைக்கவும். 1 GB-க்கு கீழ் போனால் capture எழுதுவதை நிறுத்தும்.

## 2. MindVision camera SDK + network (GigE camera-கள்)
1. `drivers\MindVision Camera Platform Setup(2.1.10.195_202604021438).exe`-ஐ administrator-ஆக இயக்கவும்.
   - **இதே version 2.1.10.195**-ஐ நிறுவவும். `app\MVCAMSDK_X64.dll`-உம் அதே version.
   - இந்த installer-க்கு digital signature இல்லை. Development கணினியில் உள்ள அதே file தான்.
2. **Program camera DLL-ஐ எப்படிக் கண்டுபிடிக்கிறது:** `app\mvsdk.py` முதலில் `app` folder-இல் உள்ள
   `MVCAMSDK_X64.dll`-ஐ load செய்யும். அது முடியாவிட்டால், SDK installer `C:\Windows\System32`-இல் வைத்த
   copy-ஐ Windows கண்டுபிடிக்கும். DLL, camera plug-in-களை (`GiGeCamera_X64.Interface`, ...) installer
   registry-இல் (`DeviceInterfacePath`) எழுதிய SDK folder-இலிருந்து load செய்யும். எனவே program **எந்த**
   folder-இலிருந்தும் இயங்கும், ஆனால் **SDK நிறுவிய பிறகு மட்டுமே**. SYSTEM_CHECK இரண்டு DLL version-களையும் ஒப்பிடும்.
3. ஒவ்வொரு camera-வுக்கும் தனி network port, **camera-வின் subnet-இல் நிலையான (fixed) IP**
   (`app\camera_config.json`: FRONT 192.168.3.206, REAR 192.168.1.101):
   - camera port 1: PC IP **192.168.3.10**, mask 255.255.255.0
   - camera port 2: PC IP **192.168.1.10**, mask 255.255.255.0

   Program camera-களை IP மூலம் அல்ல, **serial number** மூலம் கண்டுபிடிக்கும். ஆனால் ஒவ்வொரு camera-வையும் அடைய முடிய வேண்டும்.
4. ஒவ்வொரு camera adapter-க்கும் Properties → Advanced-இல் அமைக்கவும்:
   - **Jumbo Packet 9014**
   - **Receive Buffers = maximum**
   - **Energy-Efficient Ethernet off**
5. Windows Firewall: இந்த network-களில் program-ஐயும் MVDCP-ஐயும் அனுமதிக்கவும்.
6. **MVDCP** (MindVision tool) திறக்கவும். இரண்டு camera-களும் தெரிய வேண்டும். பிறகு MVDCP-ஐ மூடவும்.

## 3. CP210x USB driver (ESP32 board-கள்)
- `drivers\CP210x_Universal_Windows_Driver.zip`-ஐ unzip செய்து, `silabser.inf` மேல் right-click → **Install**.
- Board-களை **ஒவ்வொன்றாக** இணைக்கவும். Device Manager → Ports-இல் "Silicon Labs CP210x … (COMxx)" தெரியும். எண்களைக் குறித்துக்கொள்ளவும்:
  - ejector ESP32 = COM__
  - relay ESP32 = COM__
  - feeder (Nuvoton) board = COM__
- CH340 / CH9102 chip உள்ள board-க்கு WCH driver தேவை.

## 4. Package-ஐ copy செய்தல்
முழு folder-ஐயும் **`C:\LAMTON_V2`**-க்கு copy செய்யவும். USB drive-இலிருந்து இயக்க வேண்டாம் (மெதுவாக இருக்கும், drive letter மாறும்).
```
robocopy D:\LAMTON_V2_DEPLOY C:\LAMTON_V2 /E
```
`C:\LAMTON_V2` ஏற்கனவே இருந்தால் புதிய பெயர் கொடுக்கவும் (உதா: `C:\LAMTON_V2b`). பழைய installation மேல் ஒருபோதும் copy செய்யக் கூடாது.

## 5. Python + packages: `INSTALL.bat`
**a) Internet உடன் (வழக்கமான முறை)**
1. Python 3.10–3.13 (64-bit) இல்லையென்றால்: `drivers\python-3.12.10-amd64.exe` இயக்கி,
   **"Add python.exe to PATH"** டிக் செய்யவும். Python 3.14, numpy 2.2.6-க்கு மிகவும் புதியது.
2. **`C:\LAMTON_V2\INSTALL.bat`** double-click செய்யவும். அது Python-ஐக் கண்டுபிடித்து, `C:\LAMTON_V2\.venv`
   உருவாக்கி, சரியான versions-ஐ நிறுவும். System Python-ஐயோ பழைய program-ஐயோ மாற்றாது.
3. INSTALL.bat-இன் படி 4, **CNN packages-ஐ CPU மட்டும்** நிறுவும் (சுமார் 350 MB): PyTorch 2.14.0, torchvision 0.29.1,
   ultralytics 8.4.163, ONNX. இவை CNN project-இன் versions. **GPU தேர்வு இல்லை**, CUDA package இல்லை.
   CNN inference மற்றும் CNN training இரண்டும் CPU-வில் ஓடும். இந்தப் படி தோல்வியடைந்தாலும் sorter தொடங்கி
   sorting செய்யும் (colour / HSV / contour); CNN மட்டும் இருக்காது. பிறகு `INSTALL.bat`-ஐ மீண்டும் இயக்கவும்.
4. Folder பெயர்: folder path 90 எழுத்துகளுக்கு மேல் இருந்தால் INSTALL.bat நின்று, `C:\LAMTON_V2` போன்ற குறுகிய
   folder-ஐப் பயன்படுத்தச் சொல்லும் (Windows-இன் 260 எழுத்து வரம்பு PyQt5 நிறுவலை உடைக்கும்).
5. **பின்னர் GPU:** `UPGRADE_TO_GPU_TAMIL.md` பார்க்கவும். ஒரே படி: `UPGRADE_TO_GPU.bat`.

**b) Internet இல்லாமல் (offline)**
1. **Internet உள்ள ஏதாவது ஒரு கணினியில்** `MAKE_OFFLINE_WHEELS.bat 3.12` இயக்கவும் (அல்லது `3.11`, `3.10`, `3.13`).
   Python 3.12 / 64-bit Windows-க்கான எல்லா packages-ஐயும் `wheels\`-இல் download செய்யும் (CPU AI-உடன் சுமார் 1.3 GB).
2. Package-ஐ **`wheels\` உட்பட** புதிய கணினிக்கு copy செய்யவும். `drivers\python-3.12.10-amd64.exe` நிறுவவும்.
3. `INSTALL.bat` இயக்கவும். அது `wheels\`-ஐப் பார்த்து **internet இல்லாமல்** நிறுவும்.

## 6. `SYSTEM_CHECK.bat` (படிக்க மட்டும்)
முதலில் sorter-ஐ மூடவும். இது PASS / WARN / FAIL காட்டும்:
- Python மற்றும் packages;
- MindVision SDK மற்றும் DLL;
- **இரண்டு camera-களும்** (model, serial number, IP);
- disk காலி இடம் மற்றும் எழுதும் வேகம்;
- **ஒவ்வொரு ESP32 board-உம்** (port, firmware version);
- GPU;
- **licence**.

இது எதையும் மாற்றாது:
- camera-கள் பட்டியலிடப்படும் மட்டும்;
- ejector-க்கு text PING மட்டும்;
- relay board-க்கு Modbus *read* மட்டும்;
- feeder board-க்கு எதுவும் அனுப்பப்படாது;
- disk test ஒரு தற்காலிக file எழுதி உடனே அழிக்கும்.

Production-க்கு முன் எல்லா **FAIL**-களையும் சரி செய்யவும்.

## 7. முதலில் camera settings backup
```
tools\CAMERA_PROBE.bat
```
இது படிக்க மட்டும். `app\camera_backups\<தேதி>\` எழுதும்: ஒவ்வொரு camera-வுக்கும் ஒரு MindVision `.config`
file, ஒரு `.json` copy, மற்றும் `probe_report.txt`. அந்த folder-ஐ USB drive-க்கும் copy செய்யவும்.

பின்னர் restore செய்ய (path, `app`-ஐப் பொறுத்தது, அல்லது முழு path கொடுக்கவும்):
`tools\CAMERA_RESTORE.bat camera_backups\<தேதி>\FRONT_<SN>.config`

## 8. Firmware
- **Ejector ESP32:**
  - SYSTEM_CHECK **"ejector firmware v2.3"** காட்டினால், எதுவும் செய்ய வேண்டாம். நீங்கள் 16 அக் 2026 அன்று upload செய்த அதே board.
  - இல்லையென்றால் `firmware\ESP32_63CH_Ejector_v2_3` upload செய்யவும். இது PIN_PROFILE 1, பழைய wiring.
- **Relay ESP32: v2.0 தேவையில்லை.** Program relay firmware version-ஐப் படிக்கும் (Modbus register 11).
  பழைய firmware என்றால் "Old relay firmware (no watchdog)" காட்டி, முன்பு போலவே வேலை செய்யும்.
  v2.0 **பின்னர் பரிந்துரைக்கப்படுகிறது**: PC உறைந்தால் feeder, main air, wiper-ஐ 0.5 வினாடிக்குள் OFF செய்யும்.
  ஆனால் அது **இன்னும் உண்மையான board-இல் ஓடவில்லை**. முதலில் பழைய relay firmware-உடன் தொடங்கவும்.
  பிறகு `firmware\ESP32_8CH_ModbusRelay_v2_0` upload செய்து T6 சோதனை செய்யவும் (`app\PHASE1_GUIDE.md`).
- **Upload படிகள்:**
  1. `drivers\arduino-ide_2.3.10_Windows_64bit.exe` நிறுவவும்.
  2. Boards Manager → **esp32 by Espressif 3.3.12** (C:-இல் சுமார் 9 GB தேவை).
  3. Board **ESP32 Dev Module**, **Upload Speed 115200**, அந்த board-இன் COM port. முதலில் sorter-ஐ மூடவும்.
  4. Ejector மட்டும்: upload செய்யும் போது 74HC595 ribbon cable-ஐ கழற்றவும்.
  5. "Connecting…"-இல் நின்றால் **BOOT** button-ஐ அழுத்திப் பிடிக்கவும்.
  6. வெற்றி = "Hash of data verified". Serial Monitor: ejector v2.0-க்கு **115200**, பழைய ejector firmware-க்கு **115200**, relay-க்கு **9600**.

  விவரம்: `firmware\README.txt`.

## 9. அமைப்புகள்: COM ports மற்றும் camera serial numbers
**COM port எண்களை நீங்கள் எழுத வேண்டியதில்லை.** Program, ejector / relay board-களை எந்த COM எண்ணிலும் அவற்றின் உண்மையான பதிலால் கண்டுபிடிக்கும்:
- ஒவ்வொரு தொடக்கத்திலும்: சேமித்த port பதில் தந்தால் அதைப் பயன்படுத்தும்; இல்லையென்றால் எல்லா COM port-களிலும் தேடி, கிடைத்த port-ஐ தெளிவான செய்தியுடன் பயன்படுத்தி `system_config.json`-இல் சேமிக்கும்.
- **ENGINEER** login -> footer-இல் **COM PORTS**: ejector / relay / feeder-க்கு ஒவ்வொன்றுக்கும் dropdown (port பெயர் + USB chip + விளக்கம்), **Disabled** தேர்வு, **Refresh**, **AUTO DETECT**, **Test**, **SAVE AND CONNECT**.
- "Disabled" என்ற (அல்லது `modbus_enabled = false`) device-க்கு port திறக்கவோ சோதிக்கவோ படாது. ஒரு port ஒரே நேரத்தில் இரண்டு device-க்கு ஒருபோதும் கொடுக்கப்படாது.
- **Feeder (Nuvoton) board எந்த பதிலும் தராது**, அதனால் தானாகக் கண்டுபிடிக்க முடியாது: அதன் port-ஐ COM PORTS-இல் கையால் தேர்வு செய்யவும்.
- `SYSTEM_CHECK.bat` பகுதி 5: ஒவ்வொரு port-உம் என்ன பதில் தருகிறது மற்றும் sorter என்ன செய்யும் என்பதைக் காட்டும்.

`C:\LAMTON_V2\app\camera_config.json`-ஐத் திருத்தவும்: ஒவ்வொரு camera-வின் serial number-ஐ `"FRONT": {"sn": …}`, `"REAR": {"sn": …}`-இல் வைக்கவும்.
SYSTEM_CHECK கண்டுபிடிக்கும் serial number-களைப் பட்டியலிடும்.

குறிப்பு: ஒவ்வொரு தொடக்கத்திலும் program `app\ejector_config.json`-ஐ தானே மீண்டும் எழுதும். அது சாதாரணம்.

## 10. Licence: புதிய கணினியில் என்ன நடக்கும்
**இரண்டு பூட்டுகள்** உள்ளன:

| பூட்டு | எதைச் சோதிக்கும் | புதிய கணினியில் |
|---|---|---|
| **1. தொடக்க hardware lock**, `app\LAMTON_LINE_NEW_ESP32.py` வரிகள் 13520–13521 | `ALLOWED_UUID = "03000200-0400-0500-0006-000700080009"` **மற்றும்** camera-கள் `075002923007`, `076011622362` இரண்டும் இணைந்திருத்தல் | UUID வேறு என்றால் → **"Access Denied (Hardware Mismatch)"**, program மூடிவிடும் |
| **2. Licence key**, `app\tcm_license.dat` | UUID-இன் முதல் 4 hex எழுத்துகள் + காலாவதி தேதி + signature | UUID வேறு என்றால் → "HARDWARE MISMATCH": START தடுக்கப்படும் |

நீங்கள் செய்ய வேண்டியது (manufacturer):
1. `SYSTEM_CHECK.bat` → பகுதி 7, **"this PC's UUID: …"** காட்டும்.
2. அது `03000200-0400-0500-0006-000700080009`-இலிருந்து வேறுபட்டால், `C:\LAMTON_V2\app\LAMTON_LINE_NEW_ESP32.py`-இல்
   **வரி 13520**-ஐ புதிய UUID-ஆக மாற்றவும். Camera மாற்றப்பட்டிருந்தால், வரி 13521-இல் `ALLOWED_CAMERAS`-ஐயும்,
   `camera_config.json`-இல் `sn`-ஐயும் மாற்றவும்.
3. Manufacturer கணினியில், **LICENCE_BOSS** மூலம் புதிய UUID-க்கு key உருவாக்கவும் (வேண்டுமென்றே இந்த package-இல் இல்லை).
4. Sorter தொடங்கி → **INFO** → **🔑 MANAGE / UPDATE LICENSE** → key-ஐ உள்ளிடவும். அது `tcm_license.dat` எழுதும்.
5. SYSTEM_CHECK பகுதி 7: எல்லா வரிகளும் PASS ஆக வேண்டும் ("the sorter WILL start on this PC").

குறிப்பு: `03000200-0400-0500-0006-000700080009` ஒரு பொதுவான factory UUID போலத் தெரிகிறது. சில motherboard-கள்
இதே மதிப்பைக் காட்டும்; அப்போது மாற்றம் இல்லாமலேயே இரண்டு பூட்டுகளும் திறக்கும்.

## 11. பாதுகாப்பான முதல் இயக்கம்
1. **Main air மூடப்பட்டு** (manual valve), **chute காலி**, feeder காலி, nozzle அருகில் யாரும் இல்லை.
2. `START_SORTER.bat`. Main window திறக்கும். **இப்போது START SYSTEM அழுத்த வேண்டாம்.**
3. **ENGINEER**-ஆக login செய்யவும். OPERATOR-க்கு **EJECTOR TEST PANEL** button பூட்டப்பட்டிருக்கும்.
4. **EJECTOR TEST PANEL**. அது sorter-இன் சொந்த ejector இணைப்பையே பயன்படுத்தும். அது திறந்திருக்கும் வரை sorting ejection நிறுத்தப்படும்.
   - Badge **"v2.3 BINARY · ARMED"**; counters `crc_err 0`, `len_err 0`.
   - Channel 1, 32, 63-ஐ click செய்யவும். Drive-board LED-கள் எரியும்.
   - AUTO SEQUENCE 1–63, pulse 30 ms, gap 300 ms. Valve bar வழியே நடந்து பார்க்கவும்: வரிசை 1 → 63 ஆக இருக்க வேண்டும்.
     ஒரு channel தவறான valve-ஐ fire செய்தால்: **CHANNEL REMAP** → swap → **Save to ESP32**.
   - STOP ALL / Esc உடனே நிறுத்தும்.
5. **Air-ஐத் திறக்கவும்.** AUTO SEQUENCE 1–63, **pulse 4 ms**, gap 200 ms. ஒவ்வொரு valve-உம் click சத்தம் கொடுக்க வேண்டும்.
6. Panel-ஐ மூடவும் → தெரிந்த product-உடன் **குறைந்த feed**-இல் START SYSTEM.
   - Reject bin-ஐச் சோதிக்கவும்: அதில் நல்ல product குறைவாக இருக்க வேண்டும்.
   - Counters-ஐச் சோதிக்கவும்: `no-ack`, `crc` 0-க்கு அருகில் இருக்க வேண்டும்.
7. அதன் பிறகே feed-ஐ அதிகரிக்கவும். V2 ஒரு முழு shift ஓடும் வரை பழைய program-ஐத் தொட வேண்டாம்.

## 12. பழைய நிலைக்குத் திரும்புதல் (Rollback)
- **Program:** V2-ஐ மூடவும். பழைய program-ஐ அதன் சொந்த folder-இலிருந்து முன்பு போலவே தொடங்கவும். அதில் எதுவும் மாற்றப்படவில்லை.
- **Firmware:** `firmware\ROLLBACK_OLD\ESP32_63CH_ShiftRegister` (ejector) அல்லது
  `firmware\ROLLBACK_OLD\ESP32_8CH_ModbusRelay_NEW` (relay) upload செய்யவும். இரண்டு program-களும் பழைய firmware-ஐ தானே கண்டுபிடிக்கும்.
- **Camera settings:** படி 7-இன் backup-உடன் `tools\CAMERA_RESTORE.bat`.

## 13. இன்னும் உண்மையான இயந்திரத்தில் சோதிக்க வேண்டியவை (development கணினியில் சோதிக்கப்படவில்லை)
- camera-கள்: பட்டியல், settings backup, live படங்கள், line rate. Development கணினியில் camera-கள் இல்லை;
- புதிய கணினியின் UUID மற்றும் இரண்டு camera-களுடன் hardware lock மற்றும் licence;
- program வழியாக relay ESP32 (பழைய மற்றும் v2.0) மற்றும் feeder board;
- 74HC595 board மற்றும் drive-board LED-கள், valve வரிசை 1–63, air-உடன் உண்மையான valve-கள்;
- GPU மாற்றம் (`UPGRADE_TO_GPU.bat`): development கணினியில் NVIDIA GPU இல்லை; புதிய கணினியின் CPU-வில் CNN training வேகம்;
- புதிய கணினியின் capture disk வேகம்;
- product-உடன் முழு sorting இயக்கம்.
