# Phase 1 — Ejector Firmware, Safety Watchdog and Test Tools

*Builds on `ANALYSIS.md` and `TIMING_DESIGN.md`. Everything here can be undone: the old firmware folders are untouched, and every change is in git.*

---

## 1. What changed (in simple words)

| # | Problem before | What Phase 1 does |
|---|---|---|
| 1 | **Missed rejects.** When ~16 or more valves had to fire at once, the text command `FIRE_MULTI:1,2,3…` got too long, the ESP32 cut it off and threw the **whole** command away. | The PC now sends a **fixed 24-byte binary message** with a 64-bit "valve mask". 1 valve or 63 valves is the same size, so nothing can be cut off. Every message has a **checksum (CRC)**, a **sequence number** and gets an **ACK** back. Errors are counted, not hidden. |
| 2 | If the PC froze or the USB cable fell out, valves in HOLD stayed ON, and the feeder and main air kept running. | **500 ms watchdog on both ESP32s.** The PC sends a "heartbeat" every 100 ms (ejector) and 200 ms (relays). After 500 ms of silence: **all valves OFF** (outputs disabled in hardware), and **feeder, main air and wiper OFF**. Things stay off until you press **START** again. |
| 3 | A crossed valve wire meant re-soldering. | **Remap table** in the ESP32 (saved in its flash memory): "channel 12 → output 13". |
| 4 | Testing showed only "channel N". | **Test mode on the ESP32:** single channel (N repeats) and sweep FROM..TO with adjustable pulse and gap. Every step prints **CH → OUT → CHIP / Q pin**, so you know exactly which 74HC595 chip and pin to check. |
| 5 | **GPIO 12** (the latch wire) is an ESP32 boot pin. If it is pulled high at power-up, the ESP32 can refuse to start. | New pins (no boot pins) and the ESP32's hardware SPI. |
| 6 | At power-up, 74HC595 outputs are **random** for a moment, so random valves can fire. | Outputs stay disabled (OE pin HIGH, held by a 10 kΩ resistor) until the ESP32 has written zeros into every chip. |
| 7 | **50 ms blast bug.** A defect seen by the front camera was re-fired as a 50 ms "long object" blast when the rear camera's frame arrived, which blew away ~175 mm of good product. | Each frame fires only what **that** camera saw. A long object (the same camera sees the channel in consecutive frames) gets a pulse of exactly *frame time + normal pulse* (e.g. 20 + 4 = 24 ms), never the fixed 50 ms. **Proved:** the old code gave `4 ms + 50 ms` for one defect; the new code gives one `4 ms` blast. |
| 8 | *Found during testing:* on Python 3.10, `time.sleep(1 ms)` really sleeps **~17 ms** (measured), so blasts could fire up to ~25 ms late (~90 mm). | The program now asks Windows for 1 ms timer resolution at start-up (measured afterwards: 1.0 ms). |

**Keeps working with old firmware:** the PC detects which firmware is on the ESP32. With the old firmware it falls back to text mode (and splits long commands so they are never cut off). You can flash the old sketch back at any time.

---

## 2. Files

| File | New / changed | Purpose |
|---|---|---|
| `ESP32_63CH_Ejector_v2_0/ESP32_63CH_Ejector_v2_0.ino` | **new** | Ejector firmware v2.0 (binary protocol, watchdog, remap, test mode, safe pins, 921,600 baud) |
| `ESP32_8CH_ModbusRelay_v2_0/ESP32_8CH_ModbusRelay_v2_0.ino` | **new** | Relay firmware v2.0 (same pins as `_NEW`, plus heartbeat watchdog) |
| `ejector_link.py` | **new** | PC side of the protocol (used by the sorter and the testers) |
| `LAMTON_LINE_NEW_ESP32.py` | changed | Uses `ejector_link`, relay heartbeat, safety alarm, 50 ms fix, 1 ms timer, in-app tester improvements |
| `gui_esp32_ejector_tester.py` | rewritten | Standalone tester for the new protocol (still works with old firmware) |
| `RUN_EJECTOR_TESTER.bat` | changed | Now starts the tester from this folder |
| `tools/test_ejector_protocol.py`, `tools/test_ejector_link_sim.py`, `tools/test_fusion_fix.py` | **new** | Tests that run without hardware |
| `tools/camera_probe.py`, `tools/camera_restore.py` | **new** | Read-only camera report and backup, plus restore |
| `ESP32_63CH_ShiftRegister/`, `ESP32_8CH_ModbusRelay_NEW/` | **untouched** | Your working firmware, for rollback |

Both new sketches compile with **0 warnings** on ESP32 Arduino core **2.0.17 and 3.0.7**. Board setting: **"ESP32 Dev Module"**.

---

## 3. Wiring changes (ejector ESP32 only)

### 3.1 Why a level shifter

Your drive board has **5 V logic inputs**, and the ESP32 outputs **3.3 V**. A 5 V 74HC595 needs at least ~3.5 V to be *sure* a signal is "high", so 3.3 V is out of spec. It usually works on the bench, and fails with long wires and solenoid noise.

**Choice: one 74HCT245 buffer chip** between the ESP32 and the drive board. Why this rather than replacing all eight 595s with 74HCT595:

- It is one cheap chip, and the drive board stays untouched.
- "HCT" inputs accept anything above 2.0 V as high, so 3.3 V is safe. Its outputs are full 5 V with strong drive, which is good for cable runs.
- Swapping in 74HCT595s also works, but only if the chips are in sockets, and the ESP32 would still drive the cable directly.

### 3.2 New connections

| Signal | Old ESP32 pin | **New ESP32 pin** | → 74HCT245 | → 74HC595 (all 8 chips unless noted) |
|---|---|---|---|---|
| DATA (SER / DS) | GPIO 14 | **GPIO 23** | A1 (pin 2) → B1 (pin 18) | chip #1 pin 14 only |
| CLOCK (SRCLK / SH_CP) | GPIO 13 | **GPIO 18** | A2 (pin 3) → B2 (pin 17) | pin 11 |
| LATCH (RCLK / ST_CP) | GPIO 12 ⚠ | **GPIO 21** | A3 (pin 4) → B3 (pin 16) | pin 12 |
| OUTPUT ENABLE (/OE) | GPIO 19 | **GPIO 22** | A4 (pin 5) → B4 (pin 15) | pin 13 |
| Status LED | GPIO 2 | GPIO 2 | — | — |
| **Timing debug** (new) | — | **GPIO 27** | — | logic analyser probe (HIGH while any valve is ON) |
| **PC OK** (new, optional) | — | **GPIO 32** | — | to relay board, see 3.4 |

74HCT245 power and control pins:

| 74HCT245 pin | Connect to |
|---|---|
| 20 (VCC) | **+5 V** (same 5 V as the drive board logic), plus a **100 nF capacitor** from pin 20 to GND, close to the chip |
| 10 (GND) | GND |
| 1 (DIR) | +5 V (direction A → B) |
| 19 (/OE of the 245 itself) | GND (buffer always on) |
| 6, 7, 8, 9 (A5–A8, unused) | GND |

**Power-up safety resistor:** put **10 kΩ from the GPIO 22 wire to 3.3 V**, on the ESP32 side of the 245. While the ESP32 is booting its pins float. The resistor holds /OE high, so all 595 outputs stay off. The firmware writes zeros first and only then pulls /OE low.

> ⚠ **Check your drive board:** /OE (pin 13) of the 74HC595s must actually be connected to this wire. If the board ties pin 13 permanently to GND, the power-up protection cannot work. Tell me, and we'll use the 595 "MR" (pin 10) clear input instead.

**Common ground:** ESP32 GND, 74HCT245 GND, drive board logic GND and the 24 V supply 0 V must all be connected. Keep the 24 V valve current off the thin logic ground wire: use a separate thick return to the 24 V supply.

**Cable:** keep the ESP32 → drive board cable short (< 50 cm if possible). Put a GND wire next to the CLOCK wire, e.g. in a ribbon cable with ground every second wire. The firmware uses 2 MHz SPI, which is conservative.

### 3.3 Before rewiring (temporary)

You can test v2.0 **on the old wiring** by changing line `#define PIN_PROFILE 2` to `1` in the sketch. It then uses GPIO 14/13/12/19 exactly as before. The GPIO 12 boot risk remains in that mode, so use it only for testing.

### 3.4 Optional hardware interlock (relay board)

Ejector ESP32 **GPIO 32** → **1 kΩ** → relay ESP32 **GPIO 34**, plus **10 kΩ from GPIO 34 to GND**, and connect the two boards' GND. Then set `#define USE_EJECTOR_OK_INPUT 1` in the relay sketch. If the ejector board trips, the feeder and air also stop, even if the relay board's own USB link is still alive. This is optional; the software watchdog alone already meets the requirement.

### 3.5 Camera trigger driver (needed in Phase 3, not now)

The ESP32 must not be wired straight to a camera trigger input. Camera inputs are 5–24 V and opto-isolated, or RS-422 for encoder inputs. Which one your cameras have will show in the **camera probe** output (`INPUT n … formats: OPTO-COUPLER / RS422`).

**A. Opto-coupler input (most common)** — recommended: **TC4427** dual non-inverting MOSFET driver, powered from **12 V**.

```
ESP32 GPIO 25 ──► TC4427 IN A   (3.3 V is enough: its input high threshold is 2.4 V)
TC4427 OUT A ──► 470 Ω ──► FRONT camera TRIGGER IN+
TC4427 IN B  ◄── same GPIO 25
TC4427 OUT B ──► 470 Ω ──► REAR camera TRIGGER IN+
Camera TRIGGER IN- / COM ──► 12 V supply GND (common with ESP32 GND)
TC4427: VDD = 12 V, 1 µF + 100 nF capacitors at VDD, GND = common GND
```

It switches in about 30 ns, so it is fine at 20,000 lines/s. Each camera gets its own driver output.

**Cheaper alternative:** one NPN transistor per camera (BC547 / 2N2222): ESP32 → 1 kΩ → base; emitter → GND; collector → camera IN+, with a 1 kΩ pull-up from IN+ to 12 V. This **inverts** the signal, so the camera is then set to trigger on the **falling edge**.

**B. RS-422 / encoder input** — **AM26LS31** (or SN75174) quad differential line driver at 5 V, driven by ESP32 GPIOs. Its outputs go to camera A+/A− (and B+/B− if the camera wants quadrature A/B signals). Use twisted-pair cable.

Check the camera manual's I/O pinout before connecting anything (pin numbers differ between models).

---

## 4. Flashing

> **UPDATE 16 Oct 2026: the current ejector firmware is `ESP32_63CH_Ejector_v2_3`** (115200 baud, PIN_PROFILE 3, no ARM/RE-ARM, silent watchdog, IDENTIFY; not hardware-tested). The text below describes v2.0 (921600 baud, TRIPPED + ARM); protocol, remap and test modes are unchanged.

1. Arduino IDE → Board **"ESP32 Dev Module"** (any core 2.0.x or 3.x).
2. Open `ESP32_63CH_Ejector_v2_0.ino` → Upload to the ejector ESP32.
3. Open `ESP32_8CH_ModbusRelay_v2_0.ino` → Upload to the relay ESP32.
4. Optional check with Serial Monitor (ejector board): **921,600 baud**, "Newline". You should see `LAMBTON EJECTOR v2.0 READY …`. Type `HELP`. Close the Serial Monitor afterwards (it blocks the COM port).

**Rollback:** upload the old `ESP32_63CH_ShiftRegister.ino` / `ESP32_8CH_ModbusRelay_NEW.ino`. The sorter detects the old firmware and keeps working, without the watchdog.

---

## 5. Test steps (safe order)

> **Before every hardware test:** main air supply **closed** (or relay 2 off), no product on the chute, feeder empty. Watch the drive board LEDs (or LEDs on the 595 outputs).

### T0 — PC tests without hardware (2 minutes)

```
python tools\test_ejector_protocol.py
python tools\test_ejector_link_sim.py
python tools\test_fusion_fix.py
```
Each must end with **ALL TESTS PASSED**.

### T1 — Power-up safety (after rewiring + 10 kΩ resistor)

1. Flash v2.0, then power-cycle the ejector ESP32 **5 times** (unplug/plug USB).
2. ✅ No valve LED may flash at power-up.
3. Press the ESP32 EN (reset) button 5 times. ✅ No flashes.

### T2 — Connect the tester

1. Close the sorter program. Run `RUN_EJECTOR_TESTER.bat` (or `python gui_esp32_ejector_tester.py`).
2. Pick the ejector COM port (COM24) → **CONNECT**.
3. ✅ Badge shows **"v2.0 BINARY (COM24)"**. Console: `Connected: firmware v2.0 … watchdog ARMED`.
4. Press **STATUS / PING**. ✅ `crc_err=0 len_err=0`.

### T3 — Every valve, in order (sweep)

1. Pulse **30 ms**, Gap **300 ms**, Sweep **1 to 63** → **SWEEP TEST**.
2. Walk along the valve bar. ✅ LEDs/valves fire 1 → 63 in order. The console prints each step, e.g. `[TEST] CH 12 -> OUT 11 -> CHIP 2, Q3`.
3. If channel X fires the wrong valve, note which channel fired it, then fix it with **SWAP**, then **SAVE TO ESP32**. Run the sweep again.
4. A dead valve: the console tells you its CHIP and Q pin. Measure that 595 pin with a multimeter or LED while doing a **SINGLE TEST** (Repeat 0 = continuous) on that channel.

### T4 — The truncation bug is gone

1. In the tester text box, paste this (all 63 channels in one old-style text command) and press Enter:
   ```
   FIRE_MULTI:1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63:30
   ```
2. ✅ **All 63** valve LEDs flash together, and the console shows `ACK:FIRE_MULTI`. (The old firmware ignores this command completely; that was the bug.)
3. The sorter itself now uses the binary message, which is always 24 bytes.

### T5 — Ejector watchdog

1. Select channel 5 (click it) → press **WATCHDOG TEST**.
2. ✅ The valve LED turns on, then goes OFF by itself about 0.5 s after the heartbeat stops. Console: `[WATCHDOG TEST] PASS`.
3. Press **ARM**. Fire a channel to check it works again.
4. **Cable test:** **SINGLE TEST** on a channel with Repeat **0** (continuous), Pulse 200 ms, Gap 10 ms (valve almost always on). **Pull the USB cable.** ✅ Valve OFF within 0.5 s. The ESP32 LED blinks fast (= TRIPPED).
5. *Optional, exact timing:* logic analyser on **GPIO 27** (debug) plus a USB-disconnect marker. The ON → OFF time must be ≤ 0.5 s.

### T6 — Relay board watchdog (feeder + main air)

1. Flash relay v2.0. Start the sorter program. ✅ Console prints `[RELAY] Relay firmware v2.0 detected -> heartbeat watchdog active`.
2. Air supply still closed. Press **START** (feeder relay and main-air relay turn ON; check the relay LEDs).
3. **Pull the relay board's USB cable.** ✅ Feeder, main air and wiper relays switch **OFF within 0.5 s**. Lights stay as they were.
4. Plug it back in and restart the program. Press START. ✅ It works again.
5. **Frozen PC test:** with START active, end the sorter in **Task Manager** (End task). ✅ Both boards trip: valves off; feeder and air off.
6. In the program: if a board trips while running, sorting **stops** and the red banner shows `⚠ SAFETY STOP: …`. Pressing START clears it.

### T7 — In-app ejector test window

1. Sorter program → Ejector test window. ✅ The second line shows the link: `BINARY fw 2.0 state ARMED | sent … ok …`.
2. Move the slider. ✅ The label shows `CH n -> OUT … -> CHIP …, Q…`.
3. (3 Nov 2026: REPEAT COUNT was removed from the EJECTORS window - no code ever used it. PULSE GAP is now "TEST GAP (ms)" next to the test buttons.)
4. **AUTO SEQ (1-63)**. ✅ The slider follows the real valve (driven by the ESP32's own step reports).
5. **STABLE WIRE TEST** works as before. HOLD now releases itself after 10 s at the latest.

### T8 — Production check (air on)

1. Air on, no product: SWEEP 1–63 with pulse **4 ms**, gap 200 ms. Listen: every valve clicks.
2. Low feed with product, normal recipe. Compare with before:
   - **less good product in the reject bin** (the 50 ms fix);
   - air consumption (compressor duty) should drop.
3. In the in-app tester line, `no-ack` and `crc` should stay at or near 0, and `busy` low.

---

## 6. Notes and decisions I made for you

| Topic | Decision | Why |
|---|---|---|
| Level shifter | 74HCT245 buffer | One chip, no drive-board changes, strong 5 V outputs for the cable |
| Baud rate | 921,600 | 24-byte message = 0.26 ms on the wire (was ~1.7 ms at 115,200). CH9102/CP2102/CH340 chips handle it. |
| Watchdog timeout | 500 ms, heartbeat 100 ms | 5 missed heartbeats before a trip, so no false trips from short Windows hiccups |
| After a trip | stays OFF until **START** | Restarting machinery by itself after a fault is unsafe |
| HOLD maximum | 10 s | Wire test never needs longer; protects coils if forgotten |
| Pulse limits | 0.2 ms … 200 ms | Your valves respond in 1 ms; 200 ms covers the longest object |
| Text commands | still accepted | Serial Monitor and old tools keep working for diagnostics |
| Relay watchdog | only after the first heartbeat | The old `relay.py` tool (no heartbeat) keeps working without false trips |
| Python | keep using the Python 3.12 from `START_LAMTON_SORTER.bat` | 3.11+ has better timers; the 1 ms fix covers older versions anyway |

**Not changed in Phase 1:** camera settings, eject timing method (still PC-timed; that is Phase 3), detection algorithms.
