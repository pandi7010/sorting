# Development setup on another PC

This folder is a **full git clone** (all history) of the LAMTON sorter project, made on 16 Oct 2026.
Everything is in it: the sorter program, `CLAUDE.md`, `PROGRESS_LOG.md`, all docs, firmware sources (also ready-to-open
copies in `firmware\`), `tools\` with the tests, and the package builder `deploy\`.

**Not in it (on purpose):** `dev_unlock.json` (developer-mode unlock), `.venv`, logs, scratch files, datasets, camera backups,
this PC's COM-port test settings. `system_config.json` is the original one; COM ports are found by **auto detect**.

## 1. Install the tools (once)
| Tool | Why |
|---|---|
| **Python 3.12** (64-bit, from python.org; tick "Add python.exe to PATH") | runs the sorter. Python 3.10 - 3.13 work, 3.14 does not (numpy). |
| **Git for Windows** | the folder is a git repository (history, commits) |
| **Claude Code** (claude.com/claude-code) **or Google Antigravity** (antigravity.google) | the AI agent that continues the work |
| MindVision camera SDK 2.1.10.195 + CP210x USB driver | only if this PC has the cameras / ESP32 boards |
| Arduino IDE 2.x + board package "esp32 by Espressif" **3.3.12** | only to upload firmware |

## 2. Put the folder in a short path
Copy the folder to **`C:\LAMTON_DEV`** (Windows breaks packages inside long paths). Open a terminal there and check:
```
git status
git log --oneline -5
```
If git says "dubious ownership": `git config --global --add safe.directory C:/LAMTON_DEV`.
For your own commits: `git config user.name "Your name"` and `git config user.email "you@example.com"`.

## 3. Create the .venv and install the packages (internet needed)
```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r deploy\requirements.txt -r deploy\requirements_cnn.txt -c deploy\constraints.txt
```
(`requirements_cnn.txt` = PyTorch CPU, torchvision, ultralytics, ONNX. The sorter also starts without them.)

## 4. Start the sorter, the test panel, the tests
```
.venv\Scripts\python LAMTON_LINE_NEW_ESP32.py            the sorter
.venv\Scripts\python ejector_test_panel.py               63-channel test panel (finds the ejector by itself)
.venv\Scripts\python tools\system_check.py               read-only PASS/FAIL check of this PC
.venv\Scripts\python tools\test_port_detect_sim.py       every tools\test_*.py must print ALL TESTS PASSED (no hardware)
```
- **Licence:** on a PC that is not the registered one the sorter shows "Access Denied". Options: (a) the real licence steps in
  `deploy\INSTALL_NEW_PC.md` section 10, or (b) **developer mode on your own PC**: add this PC's UUID to `DEV_UUIDS` in
  `LAMTON_LINE_NEW_ESP32.py`, then `.venv\Scripts\python tools\make_dev_unlock.py`. Never commit or copy `dev_unlock.json`
  (it is git-ignored; the package builder refuses it).
- **COM ports:** nothing to configure. At start-up each device uses its saved port if it answers, otherwise all ports are searched.
  Later changes: footer button **COM PORTS** (ENGINEER login): dropdowns, Refresh, Auto detect, Test, "Disabled".
- `LAMTON_LINE_NEW_ESP32.py` uses Windows line endings (CRLF): keep them when editing with scripts.

## 5. Upload the ejector firmware (Arduino IDE)
1. Close the sorter, the test panel and any Serial Monitor.
2. Open **`firmware\ESP32_63CH_Ejector_v2_3\ESP32_63CH_Ejector_v2_3.ino`** (ready copy; folder name = file name).
   `PIN_PROFILE 3` (line 99) = DATA GPIO 23, CLOCK 18, LATCH 19, /OE tied to GND. For the old wiring 14/13/12/19 set it to **1**.
3. Tools: Board **ESP32 Dev Module**, Port = the ESP32's port, **Upload Speed 115200**. If it hangs at "Connecting...", hold **BOOT**.
4. Success: "Hash of data verified". Serial Monitor **115200**, press EN: you should see
   `LAMBTON EJECTOR v2.3 READY | 115200 baud | pins: DATA 23 CLK 18 LATCH 19 OE -1 | PROFILE 3 ...`. Close the Serial Monitor.
5. `.venv\Scripts\python tools\ejector_hw_check.py` finds the board on any COM port and prints its firmware / profile / ID.
   The blue LED is OFF when idle and lights only while a valve pulse is active.

## 6. What to tell the AI agent first
Open the folder in Claude Code (`claude` in the folder) or Antigravity (File > Open Folder) and paste this as the first message:

> Read `CLAUDE.md` and `PROGRESS_LOG.md` first (the newest section at the top and its "next step"). Then summarise the current
> state in 10 lines, list what is **not hardware-tested**, and wait for my instruction.
> Follow the standing rules in `CLAUDE.md`: never overwrite working firmware (new versioned folders), new features OFF by default,
> never weaken the licence, no hard-coded COM numbers, keep CRLF in `LAMTON_LINE_NEW_ESP32.py`, `D:\CNN\CNN` is read-only.
> Do not fire valves, switch relays or change camera settings without asking me first. Keep explanations short and simple.

Also tell it about **this PC**: which boards are plugged in, whether the cameras are connected, and what you want to test.

## 7. Status on 16 Oct 2026 (see `PROGRESS_LOG.md` for the details)
- **Not hardware-tested:** ejector firmware **v2.3**, the COM-port auto detect, the COM PORTS dialog, 115200 baud timing, the
  `PIN_PROFILE 3` wiring (23/18/19; if no LED lights try profile 1), relay v2.0 firmware, camera tools, licence on the new PC.
- Deployment packages `D:\LAMTON_V2_DEPLOY*` on the old PC are outdated (old firmware / port logic): rebuild with
  `.venv\Scripts\python deploy\make_deploy_package.py <NEW folder>` after the real test (it needs a clean `git status`).
- Not started: CNN GOOD/BAD capture + training plan (see "Open items" in `PROGRESS_LOG.md`), `APP_BUILD_PLAN.md`.
