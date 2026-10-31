# நாளைய வேலைப் பட்டியல் (Hardware நாள்)

கீழே உள்ள படிகளை **இதே வரிசையில்** செய்யுங்கள். ஒவ்வொரு படிக்குப் பிறகும் முடிவை எழுதுங்கள் (அல்லது photo / screenshot எடுங்கள்). "**அனுப்ப வேண்டியது**" என்ற வரி எனக்கு என்ன வேண்டும் என்று சொல்லும்.
**படி 8 (T8) வரை Main air வால்வை மூடி வைக்கவும், chute-ல் product இருக்கக் கூடாது.** ஏதாவது தவறு நடந்தால் → **பகுதி R (பழைய நிலைக்குத் திரும்புதல்)**.

---

## படி 0 — எந்த PC, எந்த folder (5 நிமிடம்)
1. Sorter program, PC-யின் motherboard ID-ஐ (licence) சரிபார்க்கும். Development PC-ல் **"Access Denied"** என்று வரும் — அது சாதாரணம். **Machine PC-ஐ** (வழக்கமாக sorter ஓடும் PC) பயன்படுத்துங்கள்.
2. Machine PC-ல் இப்போது வேலை செய்யும் `D:\LAMTON_V1` folder-ஐ **மாற்ற வேண்டாம் (overwrite செய்யக் கூடாது)**. புதிய version-ஐ **புதிய folder**-க்கு copy செய்யுங்கள், உதாரணம்: `D:\LAMTON_V2_TEST`
   (எங்கிருந்து: இந்த project folder, அல்லது USB disk-ல் உள்ள backup `D:\LAMTON_BACKUP_2026-10-15`).
3. புதிய folder-ல் command window திறக்கவும் (மேலே address bar-ல் click செய்து `cmd` என்று type செய்து Enter). கீழே உள்ள commands எல்லாம் அங்கே type செய்யவும்.
4. Python சரிபார்க்க: `python --version` → 3.10 முதல் 3.12 வரை இருக்க வேண்டும்.

**அனுப்ப வேண்டியது:** எந்த PC, எந்த folder.

---

## படி 1 — Camera probe (sorter program மூடியிருக்க வேண்டும்) (5 நிமிடம்)
```
python tools\camera_probe.py
```
- இது camera settings-ஐ **படிக்க மட்டுமே** செய்யும், எதையும் மாற்றாது. எல்லா settings-ன் **backup** `camera_backups\<தேதி_நேரம்>\` folder-ல் சேமிக்கப்படும்.
- விருப்பம் (முதல் run-க்குப் பிறகு): `python tools\camera_probe.py --probe-max-rate` (3 வினாடி அதிகபட்ச line rate-ஐ அளந்து, பிறகு பழைய மதிப்பையே திரும்ப வைத்து சரிபார்க்கும்).

**அனுப்ப வேண்டியது:** `camera_backups\<தேதி_நேரம்>\probe_report.txt` file (அல்லது கடைசியில் வரும் SUMMARY-ன் photo).
**தோல்வி என்றால்:** "Cameras found: 0" → sorter இன்னும் ஓடுகிறதா, camera power, network cable சரிபார்க்கவும். "Could not open camera" → sorter / MVDCP-ஐ மூடவும்.

---

## படி 2 — Drive board-ல் /OE pin சோதனை (power OFF, multimeter) (5 நிமிடம்)
1. எல்லா power-ஐயும் OFF செய்யுங்கள் (24 V மற்றும் 5 V). ESP32 USB-ஐ கழற்றுங்கள்.
2. Multimeter-ஐ **continuity / beep** (அல்லது Ω) mode-ல் வையுங்கள்.
3. ஒரு 74HC595 chip-ஐ (16 pin) கண்டுபிடியுங்கள். **Pin 13 = /OE** (chip-ன் notch பக்கத்திலிருந்து: இடது பக்கம் மேலிருந்து கீழ் pin 1–8, வலது பக்கம் கீழிருந்து மேல் pin 9–16).
4. **Pin 13 ↔ GND (pin 8)** அளக்கவும்:
   - **Beep / 0 Ω** → board-ல் /OE நேரடியாக GND-க்கு இணைக்கப்பட்டுள்ளது (power-up பாதுகாப்பு இப்போது வேலை செய்யாது).
   - **Beep இல்லை** → **pin 13 ↔ ESP32 GPIO 19 wire** (பழைய OE wire) அளக்கவும். Beep = ESP32-உடன் இணைந்துள்ளது (நல்லது).
5. இன்னொரு 74HC595 chip-லும் மீண்டும் செய்யவும்.

**அனுப்ப வேண்டியது:** "pin 13 – GND: beep / beep இல்லை; pin 13 – GPIO 19 wire: beep / beep இல்லை".

---

## படி 3 — Phase 1 firmware upload (PIN_PROFILE 1 = பழைய wiring, wiring மாற்றம் தேவையில்லை) (20 நிமிடம்)
**முதலில் ejector ESP32:**
1. Arduino IDE → **File → Open** → (புதிய folder-ல்) `ESP32_63CH_Ejector_v2_0\ESP32_63CH_Ejector_v2_0.ino`.
2. `#define PIN_PROFILE        2` என்ற வரியைக் கண்டுபிடித்து **2-ஐ 1 ஆக** மாற்றுங்கள். File → Save. (Profile 1 = இப்போதைய wiring GPIO 14/13/12/19.)
3. **Tools → Board → esp32 → "ESP32 Dev Module"**. ("esp32" இல்லையென்றால்: Tools → Board → Boards Manager → "esp32" தேடி → "esp32 by Espressif Systems" version 2.0.17 அல்லது 3.0.x install செய்யவும்.)
4. Tools settings: **Upload Speed 921600**, CPU Frequency 240 MHz, Flash Size 4MB, Partition Scheme "Default 4MB with spiffs", **Port = ejector COM port** (வழக்கமாக COM24).
5. **Upload** (→) அழுத்தவும். "Connecting…." என்று நின்றால், upload தொடங்கும் வரை ESP32-ல் உள்ள **BOOT** button-ஐ அழுத்திப் பிடிக்கவும்.
6. **Tools → Serial Monitor**, **921600 baud**, "Both NL & CR". ESP32-ன் **EN** button அழுத்தினால் இது வர வேண்டும்:
   `LAMBTON EJECTOR v2.0 READY | 921600 baud | ... LEGACY PINS (GPIO12 boot risk)`. `HELP` என்று type செய்து பாருங்கள். **பிறகு Serial Monitor-ஐ மூடவும்.**

**பிறகு relay ESP32:**
7. File → Open → `ESP32_8CH_ModbusRelay_v2_0\ESP32_8CH_ModbusRelay_v2_0.ino` (எதையும் மாற்ற வேண்டாம், pins பழையவையே) → Board "ESP32 Dev Module" → **Port = relay COM port** (வழக்கமாக COM13) → Upload.

**அனுப்ப வேண்டியது:** "ejector banner OK / relay upload OK" (அல்லது error message).
**தோல்வி என்றால்:** பகுதி R1.

---

## படி 4 — Phase 1 சோதனைகள் T0–T8 (விவரம்: PHASE1_GUIDE.md பகுதி 5)
**T0–T7 வரை Main air மூடியிருக்க வேண்டும், chute காலியாக இருக்க வேண்டும்.** Drive board LED-களைப் பாருங்கள்.

| சோதனை | என்ன செய்ய வேண்டும் | என்ன நடக்க வேண்டும் | அனுப்ப வேண்டியது |
|---|---|---|---|
| **T0** | `python tools\test_ejector_protocol.py`, `python tools\test_ejector_link_sim.py`, `python tools\test_fusion_fix.py` | ஒவ்வொன்றும் **ALL TESTS PASSED** | pass / fail |
| **T1** | ejector USB-ஐ 5 முறை கழற்றி மாட்டவும்; EN button 5 முறை அழுத்தவும் | power வரும்போது valve LED மின்னக் கூடாது (பழைய wiring-ல் ஒரு சிறிய மின்னல் வரலாம் — குறித்துக் கொள்ளுங்கள்) | மின்னியதா ஆம் / இல்லை |
| **T2** | sorter மூடியிருக்கட்டும் → `RUN_EJECTOR_TESTER.bat` → COM24 → CONNECT → STATUS / PING | badge **"v2.0 BINARY"**, `crc_err=0` | screenshot |
| **T3** | Pulse 30 ms, Gap 300 ms, Sweep 1 முதல் 63 → SWEEP TEST | LED 1→63 வரிசையாக எரிய வேண்டும்; console-ல் `CH n -> OUT n-1 -> CHIP x, Qy` | தவறான channel இருந்தால் அவற்றின் எண் |
| **T4** | PHASE1_GUIDE.md T4-ல் உள்ள நீண்ட `FIRE_MULTI:1,2,...,63:30` வரியை text box-ல் paste செய்து Enter | **63 LED-களும்** ஒன்றாக எரிய வேண்டும், `ACK:FIRE_MULTI` | ஆம் / இல்லை |
| **T5** | channel 5 click → **WATCHDOG TEST**; பிறகு SINGLE TEST Repeat 0, Pulse 200, Gap 10 வைத்து **USB cable-ஐ கழற்றவும்** | 0.5 வினாடிக்குள் LED தானாக அணைய வேண்டும்; `[WATCHDOG TEST] PASS` | pass / fail |
| **T6** | புதிய folder-லிருந்து sorter: `python LAMTON_LINE_NEW_ESP32.py` → console-ல் `Relay firmware v2.0 detected`; START அழுத்தவும் (air மூடியே) → **relay board USB-ஐ கழற்றவும்** | 0.5 வினாடிக்குள் feeder + main air relay OFF; சிவப்பு `SAFETY STOP` banner | pass / fail |
| **T7** | sorter → Ejector Test window | link வரியில் `BINARY fw 2.0`; label-ல் CHIP/Q; REPEAT 5 வைத்து SINGLE PULSE → 5 முறை; AUTO SEQ சரியாகப் பின்தொடர வேண்டும் | pass / fail |
| **T8** | **air ON, product இல்லாமல்**: SWEEP 1–63, Pulse 4 ms, Gap 200 ms (ஒவ்வொரு valve-ம் click சத்தம்); பிறகு **குறைந்த feed-ல் product**, வழக்கமான recipe | reject bin-ல் முன்பை விட நல்ல product குறைவாக இருக்க வேண்டும் (50 ms fix) | உங்கள் கருத்து + reject மாதிரி |

T3-ல் channel தவறாக இருந்தால்: tester-ல் SWAP → SAVE TO ESP32 → மீண்டும் sweep.

---

## படி 5 — Phase 2 அளவீடுகள் (30–45 நிமிடம்)
**A. Ball வேகம்** (sorter மூடியிருக்கட்டும், feeder OFF, air OFF):
```
python tools\ball_speed.py --seconds 90
```
**READY** வந்த பிறகு **10 mm ball**-ஐ chute அகலம் முழுவதும் பரவலாக 30–50 முறை போடுங்கள்.
**அனுப்ப வேண்டியது:** `logs\ball_speed_<தேதி_நேரம்>.json` (அல்லது FRONT, REAR-க்கு காட்டப்படும் mean / variation).

**B. Latency log** (வழக்கமான production):
1. புதிய folder-ல் `features_config.json` திறந்து `"timing_log_enabled": true` என்று மாற்றி save செய்யவும்.
2. Sorter-ஐ start செய்து product-உடன் **10 நிமிடம்** ஓட்டுங்கள் (வழக்கமான recipe), STOP, sorter-ஐ மூடவும்.
3. `python tools\latency_report.py`
4. மீண்டும் `"timing_log_enabled": false` என்று வைக்கவும்.
**அனுப்ப வேண்டியது:** `logs\latency_report_<தேதி_நேரம்>.md` (இதில் 35/40/55/70 mm அட்டவணை இருக்கும்).

---

## படி 6 — Phase 4 capture சோதனை உண்மையான product-உடன் (30 நிமிடம்)
1. `python tools\disk_speed_test.py` → verdict வரிகளை **அனுப்புங்கள்** (machine PC disk போதுமான வேகமா?).
2. `features_config.json` → `"continuous_capture_button": true` → sorter start → **CONTINUOUS CAPTURE** button.
3. Product **CASHEWS**, class உதாரணம் **GOOD_CASHEW**, camera **FRONT**, output folder பெரிய / வேகமான disk-ல். Disk test "too slow" என்றால் **"Save full strip images" tick-ஐ எடுத்துவிடுங்கள்**.
4. Feeder OFF → **LEARN BACKGROUND**. **Ejection OFF** tick → **START CAPTURE**.
5. **எண்ணிய** cashew-களை (உதாரணம் சரியாக 200) மெதுவாக feed செய்யுங்கள் → எல்லாம் போன பிறகு → **STOP CAPTURE**.
6. சரிபார்க்க: OBJECTS ≈ 200, **DROPPED FRAMES = 0**, சிவப்பு எச்சரிக்கை இல்லை. "roi_height" பற்றி சிவப்பு வரி வந்தால் camera frame 200 line-ஐ விட பெரியது — எனக்குச் சொல்லுங்கள்.
7. பழைய screen வேண்டுமென்றால் மீண்டும் `"continuous_capture_button": false`.
**அனுப்ப வேண்டியது:** நீங்கள் எண்ணிய எண்ணிக்கை, OBJECTS எண்ணிக்கை, session folder-ல் உள்ள `session.json`, `crops\GOOD_CASHEW\`-லிருந்து 5–10 படங்கள்.

---

## R — ஏதாவது தோல்வியடைந்தால்: பழைய நிலைக்குத் திரும்புதல்
| பிரச்சனை | செய்ய வேண்டியது |
|---|---|
| **R1** புதிய ejector firmware வேலை செய்யவில்லை | Arduino IDE → பழைய `ESP32_63CH_ShiftRegister\ESP32_63CH_ShiftRegister.ino` திறந்து → அதே board / port → Upload. Program பழைய firmware-ஐ தானாகக் கண்டுபிடித்து sorting தொடரும் (watchdog இல்லாமல்). |
| **R2** புதிய relay firmware வேலை செய்யவில்லை | பழைய `ESP32_8CH_ModbusRelay_NEW\ESP32_8CH_ModbusRelay_NEW.ino` upload செய்யவும். |
| **R3** புதிய program வேலை செய்யவில்லை | **மாற்றப்படாத பழைய folder `D:\LAMTON_V1`**-ஐ ஓட்டுங்கள் (அதன் `START_LAMTON_SORTER.bat`). குறிப்பு: பழைய program-க்கு **பழைய** firmware வேண்டும் (R1 + R2). |
| **R4** புதிய feature சரியாக இல்லை | `features_config.json` → எல்லாவற்றையும் `false` ஆக்கி restart (= Phase 1 நிலை). |
| **R5** Camera settings தவறுதலாக மாறிவிட்டது | `python tools\camera_restore.py camera_backups\<தேதி_நேரம்>\FRONT_<SN>.config` (REAR-க்கும்). |
| **R6** Cable கழற்றும் சோதனைக்குப் பிறகு feeder / air on ஆகவில்லை | அது பாதுகாப்பு lock: **START** அழுத்துங்கள் (அல்லது sorter-ஐ restart செய்யுங்கள்). |

**நாளை தேவையில்லாதவை:** 74HCT245 wiring மாற்றம் (பிறகு), Phase 3 line sync (OFF ஆகவே இருக்கும்), camera settings மாற்றம்.
