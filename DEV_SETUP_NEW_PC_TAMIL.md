# வேறொரு கணினியில் development அமைப்பு

இந்த folder, LAMTON sorter project-இன் **முழு git clone** (எல்லா history-உடன்), 16 அக் 2026 அன்று உருவாக்கியது.
அதில் உள்ளவை: sorter program, `CLAUDE.md`, `PROGRESS_LOG.md`, எல்லா docs, firmware source-கள் (`firmware\`-இல் திறக்கத் தயாரான
copy-யும்), tests உள்ள `tools\`, package builder `deploy\`.

**வேண்டுமென்றே சேர்க்கப்படாதவை:** `dev_unlock.json` (developer-mode unlock), `.venv`, logs, scratch கோப்புகள், datasets, camera backups,
அந்த கணினியின் COM-port சோதனை அமைப்புகள். `system_config.json` அசல் கோப்பு; COM port-கள் **auto detect** மூலம் கண்டுபிடிக்கப்படும்.

## 1. கருவிகளை நிறுவுதல் (ஒருமுறை)
| கருவி | ஏன் |
|---|---|
| **Python 3.12** (64-bit, python.org; "Add python.exe to PATH" டிக்) | sorter-ஐ இயக்க. Python 3.10 - 3.13 சரி; 3.14 வேலை செய்யாது (numpy). |
| **Git for Windows** | folder ஒரு git repository (history, commits) |
| **Claude Code** (claude.com/claude-code) **அல்லது Google Antigravity** (antigravity.google) | வேலையைத் தொடரும் AI agent |
| MindVision camera SDK 2.1.10.195 + CP210x USB driver | இந்த கணினியில் camera / ESP32 இருந்தால் மட்டும் |
| Arduino IDE 2.x + "esp32 by Espressif" board package **3.3.12** | firmware upload செய்ய மட்டும் |

## 2. குறுகிய path-இல் வைக்கவும்
Folder-ஐ **`C:\LAMTON_DEV`**-க்கு copy செய்யவும் (நீண்ட path-இல் packages உடைந்துவிடும்). அங்கே terminal திறந்து:
```
git status
git log --oneline -5
```
"dubious ownership" என்றால்: `git config --global --add safe.directory C:/LAMTON_DEV`.
உங்கள் commit-களுக்கு: `git config user.name "உங்கள் பெயர்"` மற்றும் `git config user.email "you@example.com"`.

## 3. .venv உருவாக்கி packages நிறுவுதல் (internet தேவை)
```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r deploy\requirements.txt -r deploy\requirements_cnn.txt -c deploy\constraints.txt
```
(`requirements_cnn.txt` = PyTorch CPU, torchvision, ultralytics, ONNX. இவை இல்லாமலும் sorter தொடங்கும்.)

## 4. Sorter, test panel, tests தொடங்குதல்
```
.venv\Scripts\python LAMTON_LINE_NEW_ESP32.py            sorter
.venv\Scripts\python ejector_test_panel.py               63-channel test panel (ejector-ஐ தானே கண்டுபிடிக்கும்)
.venv\Scripts\python tools\system_check.py               இந்த கணினியின் படிக்க-மட்டும் PASS/FAIL சோதனை
.venv\Scripts\python tools\test_port_detect_sim.py       ஒவ்வொரு tools\test_*.py-உம் ALL TESTS PASSED காட்ட வேண்டும் (hardware இல்லாமல்)
```
- **Licence:** பதிவு செய்யப்படாத கணினியில் sorter "Access Denied" காட்டும். வழிகள்: (அ) `deploy\INSTALL_NEW_PC.md` பகுதி 10-இல் உள்ள உண்மையான
  licence படிகள், அல்லது (ஆ) **உங்கள் சொந்த கணினியில் developer mode**: இந்த கணினியின் UUID-ஐ `LAMTON_LINE_NEW_ESP32.py`-இல் `DEV_UUIDS`-இல் சேர்த்து,
  பிறகு `.venv\Scripts\python tools\make_dev_unlock.py`. `dev_unlock.json`-ஐ ஒருபோதும் commit செய்யவோ copy செய்யவோ கூடாது
  (git அதைப் புறக்கணிக்கும்; package builder அதை மறுக்கும்).
- **COM ports:** எதையும் அமைக்க வேண்டாம். தொடக்கத்தில் ஒவ்வொரு device-உம் சேமித்த port பதில் தந்தால் அதைப் பயன்படுத்தும்; இல்லையென்றால் எல்லா port-களிலும் தேடும்.
  பின்னர் மாற்ற: footer button **COM PORTS** (ENGINEER login): dropdown, Refresh, Auto detect, Test, "Disabled".
- `LAMTON_LINE_NEW_ESP32.py` Windows line ending (CRLF) பயன்படுத்துகிறது: script-ஆல் திருத்தும்போது அதை மாற்ற வேண்டாம்.

## 5. Ejector firmware upload (Arduino IDE)
1. Sorter, test panel, Serial Monitor எல்லாவற்றையும் மூடவும்.
2. **`firmware\ESP32_63CH_Ejector_v2_3\ESP32_63CH_Ejector_v2_3.ino`** திறக்கவும் (தயாரான copy; folder பெயர் = file பெயர்).
   `PIN_PROFILE 3` (வரி 99) = DATA GPIO 23, CLOCK 18, LATCH 19, /OE GND-உடன். பழைய wiring 14/13/12/19-க்கு **1** என மாற்றவும்.
3. Tools: Board **ESP32 Dev Module**, Port = ESP32-இன் port, **Upload Speed 115200**. "Connecting..." என்றே நின்றால் **BOOT** பிடிக்கவும்.
4. வெற்றி: "Hash of data verified". Serial Monitor **115200**, EN அழுத்தினால்:
   `LAMBTON EJECTOR v2.3 READY | 115200 baud | pins: DATA 23 CLK 18 LATCH 19 OE -1 | PROFILE 3 ...`. Serial Monitor-ஐ மூடவும்.
5. `.venv\Scripts\python tools\ejector_hw_check.py` எந்த COM port-இலும் board-ஐக் கண்டுபிடித்து firmware / profile / ID காட்டும்.
   நீல LED idle-இல் அணைந்திருக்கும்; valve pulse நடக்கும் போது மட்டுமே எரியும்.

## 6. AI agent-இடம் முதலில் சொல்ல வேண்டியது
Folder-ஐ Claude Code-இல் (folder-இல் `claude`) அல்லது Antigravity-இல் (File > Open Folder) திறந்து, இதை முதல் செய்தியாக ஒட்டவும்:

> முதலில் `CLAUDE.md` மற்றும் `PROGRESS_LOG.md` படி (மேலே உள்ள புதிய பகுதி மற்றும் அதன் "next step"). பிறகு தற்போதைய நிலையை 10 வரிகளில் சுருக்கி,
> **hardware-இல் சோதிக்கப்படாதவை** எவை என்று பட்டியலிட்டு, என் அடுத்த கட்டளைக்குக் காத்திரு.
> `CLAUDE.md`-இல் உள்ள நிலையான விதிகளைப் பின்பற்று: வேலை செய்யும் firmware-ஐ மேலெழுதாதே (புதிய versioned folder), புதிய features இயல்பாக OFF,
> licence-ஐ ஒருபோதும் பலவீனப்படுத்தாதே, COM எண்களை code-இல் எழுதாதே, `LAMTON_LINE_NEW_ESP32.py`-இல் CRLF வை, `D:\CNN\CNN` படிக்க மட்டும்.
> என்னிடம் கேட்காமல் valve-களை fire செய்யாதே, relay-களை switch செய்யாதே, camera settings-ஐ மாற்றாதே. விளக்கங்களை குறுகியதாகவும் எளிமையாகவும் வை.

**இந்த கணினி** பற்றியும் சொல்லவும்: எந்த board-கள் இணைக்கப்பட்டுள்ளன, camera-கள் இணைந்துள்ளனவா, எதைச் சோதிக்க விரும்புகிறீர்கள்.

## 7. 16 அக் 2026 நிலை (விவரம்: `PROGRESS_LOG.md`)
- **Hardware-இல் சோதிக்கப்படாதவை:** ejector firmware **v2.3**, COM-port auto detect, COM PORTS dialog, 115200 baud timing,
  `PIN_PROFILE 3` wiring (23/18/19; LED எரியவில்லை என்றால் profile 1 முயற்சிக்கவும்), relay v2.0 firmware, camera tools, புதிய கணினியில் licence.
- பழைய கணினியில் உள்ள `D:\LAMTON_V2_DEPLOY*` packages காலாவதியானவை (பழைய firmware / port logic): உண்மையான சோதனைக்குப் பிறகு
  `.venv\Scripts\python deploy\make_deploy_package.py <புதிய folder>` மூலம் மீண்டும் உருவாக்கவும் (சுத்தமான `git status` தேவை).
- தொடங்காதவை: CNN GOOD/BAD capture + training திட்டம் (`PROGRESS_LOG.md` "Open items"), `APP_BUILD_PLAN.md`.
