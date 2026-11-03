# Progress Log

---

## 3 Nov 2026 (evening 2) - valves stop after 4-5 min while the LED bar still shows detections

- Cause found in ejector_link.py: `_reader` sets mode = None after ONE serial read error and exits; `is_open` is then
  False forever and evaluate_fusion_and_fire silently stops writing (no reconnect, nothing on screen).
- Change: a 1 s timer in the main window shows the link on the LED line (ESP32 OK / LINK LOST - valves NOT driven)
  always; switch `ejector_auto_reconnect` (default OFF) reopens the same COM port in a background thread (every 3 s),
  then resets the held-valve set and re-arms the esp.ino watchdog. Test `tools/test_ejector_link_watch_sim.py`.
  test_delay_dwell_sim now counts valve edges (ON bytes are re-sent for the watchdog) and allows 0.5 ms jitter.
- Still to check on the machine: is it the USB link (LINK LOST shown) or the board (OK shown, valves dead)?

## 3 Nov 2026 (evening) - timed mode: wire held in front of the camera chatters ("pat pat")

- Machine: timed mode ON, DELAY 13 (nozzle 47 mm), DWELL 3, frame 64 lines / 12.5 ms. A held wire makes the valve
  chatter at the frame rate. Cause (reproduced in simulation): the whole frame is late (DELAY 13 < frame 12.5 ms +
  4-9 ms transfer/processing), so every blast starts "now" and ends before the next frame reaches the PC -> OFF/ON
  each frame. Second bug: a merged long blast sent ON only once, so the esp.ino 250 ms watchdog would close it.
- Fix in capture_timing.py: (1) `continue_margin_ms` (8): an object that touches the last row keeps the valve open
  until the next frame can arrive (next blast merges); (2) the scheduler re-sends ON for open channels every 100 ms.
  Test `tools/test_timed_long_object_sim.py` (+ updated test_capture_timing_sim). Simulation only.

## 3 Nov 2026 - main-screen DELAY / DWELL row; machine detection modes set over AnyDesk

- New row above START SYSTEM (switch `main_delay_dwell_controls`, ON by the user's decision): DELAY / DWELL with -/+ and
  keypad, auto-saved to the current recipe `ejector` (+ program_data + ejector_config.json), shows TIMED EJECT ON/OFF
  (values are only used with `capture_timed_eject_enabled`). Profile change refreshes the values. Test
  `tools/test_delay_dwell_main_ui.py`; real main window built offscreen on the dev PC (row present, values 2 / 10).
- 3 Nov 16:18: the EJECTORS-window change below was REVERTED on the user's request (back to b75049d: PULSE GAP and REPEAT COUNT shown again).
- EJECTORS window (user's decision): REPEAT COUNT removed from the screen (no code used it; saved value kept);
  PULSE GAP shown as "TEST GAP (ms)" above the test buttons (only MANUAL FIRE / AUTO SEQ use it). Window built
  offscreen on the dev PC: OK.
- Main-screen object counter ("N objects, M blasts") for detection-rate tests (drop objects one by one).
- Machine (set over AnyDesk in the Background / Graph window): background is bright BLUE (~95-150), green ~25-50,
  red ~20-35; a white object DIPS in blue (~65) and rises a little in green (~55) - the front light is weak, so white
  is not "bright". Modes used: all objects = GREEN HI 75/65, LO 10/15; black only = GREEN HI 255, LO 10/15;
  white only (current) = FRONT BLUE BAND 45-80, REAR off (HI 255 / LO 0). GREEN band 50-90 fired ch 3-6 nonstop in
  the 64-line main path (background hump ~48-50) - do not use. Real fix for colour sorting: brighter front light,
  less/neutral background, balanced gains.

## 2 Nov 2026 (evening 3) - LED bar tail vs real valve; CAM LOAD readout

- User: moving a wire left/right leaves a long "tail" of lit channels (seen on AnyDesk: 11-16 channels lit for one wire).
  Cause: the main-screen LED bar kept every channel lit 0.15 s (HOLD_S) after its last detection; the real ESP_BYTE
  path switches the valve OFF on the next frame without the wire (<= 1 frame = 7.2 ms + processing).
- Change (display only, valve path untouched): BRIGHT = valve ON now (last frame), DIM = trail (fired in the last 0.15 s);
  text "VALVES ON NOW: n ch (trail m)". New "CAM LOAD F busy/period R busy/period" from the camera workers (convert + crop +
  sorting decision per frame vs camera frame period from uiTimeStamp); "SLOW: frames queue" when F + R > 0.9 x period,
  because then frames queue and valves open AND close late. Real valve close time still to be measured (slow-motion).
- Windows test run: all pass except the 3 known old failures (fusion_fix, line_sync_sim, port_detect_sim).

## 2 Nov 2026 (evening 2) - PULSE renamed DWELL; DELAY / DWELL checked (`tools/test_delay_dwell_sim.py`)

- Labels only (recipe key stays `"pulse"`): EJECTORS dialog "DWELL (ms)", Background Graph "DWELL (ms)" (was BLOW),
  algorithm dialog firing test "Dwell (ms)", save message "Delay / Dwell". EJECTORS dialog now shows
  "TIMED EJECT: ON/OFF" because the sorter uses DELAY / DWELL only with `capture_timed_eject_enabled`.
- Test with the REAL recipe reader (`get_ejector_config`, recipes kept in memory) + timed path: DELAY 15/22/40/60 ->
  ON at line time + DELAY (error <= 0.13 ms); DWELL 2/3/5/10 -> open = grain + DWELL; DELAY changed while sorting is
  used on the next grain (0.5 s re-read, no restart); DELAY 2 ms -> counted LATE (camera -> PC ~4 ms); DWELL 0 -> 1 ms.
- Dev-PC recipes have DELAY 2 / DWELL 10 for every scheme: too small a DELAY for timed mode - set ~22 ms before turning it on.
  Simulation only.

## 2 Nov 2026 (evening) - Program Settings cross-check (`tools/test_program_settings_audit.py`)

Every PROGRAM SETTINGS algorithm was run through the REAL `process_frame_for_defects` with the machine's settings
(GREEN source, FRONT LO 0 / HI 75, start 19 / end 4046) on 4 simulated frames: EMPTY, white RICE, RICE + black grain,
RICE + yellow grain. Settings path checked in code: dialog `save_and_close` -> `recipes.json` schemes[..].algorithms /
algo_settings / camera_mode -> `main_window.program_data` updated at once (no restart) -> `process_live_frame`
(SAME = REAR uses the FRONT list + settings, own start/end) -> `process_frame_for_defects`.

| Algorithm (switches as on the machine) | Result |
|---|---|
| BACKGROUND (GRAPH) | **correct**: empty 0, exactly the rice channels (the machine uses this) |
| GRAY A/B LIGHT | fires bright product (rice) - like "eject all" |
| GRAY A/B DARK | with LO 0 it does **not** see a black grain on a dark background (threshold comes from LO). `detection_fixes_enabled` (median background) finds it |
| SIZE: BIG / SHORT | old logic: dark-only, per-channel -> never fires on white rice. Needs `runtime_sliders_enabled` (object width) |
| HSV SORT OUT | works (default box = yellow: only the yellow grain) |
| **HSV SORT IN** | **BUG: fires all 63 channels even on an EMPTY chute** (background is "not the good colour") |
| **DISCOLOUR A/B** | **BUG: Color Combo (RED>GREEN, YELLOW...) is never read**; sorter used a fixed HSV box |
| AI DETECTION | needs YOLO + torch; torch fails on the machine PC -> does nothing (other algorithms continue) |
| GRAY B + `runtime_sliders_enabled` | GRAY B THRESHOLD is absolute: default 128 > background (50) -> all 63 fire. Set it BELOW the background |
| Recipe EJECTOR DELAY / PULSE | used only with `capture_timed_eject_enabled` |

**Fix (switch `program_settings_fix_enabled`, default OFF = old behaviour):** HSV (SORT IN / OUT) and DISCOLOUR only judge
product pixels (= what the Background Graph thresholds call "not background"); DISCOLOUR uses the same Color Combo
formula as its settings-dialog simulation. Test: all 11 algorithm variants fire 0 on an empty chute with the switch ON;
SORT IN rejects only the rice outside the box; DISCOLOUR YELLOW only the yellow grain. Simulation only - needs a real test.

## 2 Nov 2026 — code review + capture-timed ejection for the ESP_BYTE firmware (**SIMULATION ONLY: NOT HARDWARE-TESTED**)

### Facts found today (keep)
- **Ejector ESP32 runs `esp/esp.ino`** (byte protocol, 115200): DATA GPIO13, CLOCK GPIO14, LATCH GPIO12, /OE to GND. README lists DATA/CLOCK the other way round (README is wrong). Watchdog only after the PC sends `W` (250 ms auto-off per channel).
- **Camera probe (machine PC, `D:\lambton-vision\sorting`)**: both MV-GELM44C, **8,842 lines/s** (113 µs/line, limited by 100 µs exposure), height 512 at power-up (the sorter sets 64 at start; not stored in the camera), trigger modes Continuous/Software/FrameTrig/**LineTrig**/CondLineTrig, 2 RS422 encoder inputs, 1 opto trigger input, strobe output. Frame header timestamp unit 0.1 ms.
- At 3.5 m/s: 1 line = 0.4 mm, 64-line frame = 7.2 ms = 25 mm. Camera → valve ≈ 17–26 ms ≈ 60–90 mm, so **the 35–40 mm nozzles cannot be hit on time**.
- **The ESP_BYTE live path (commit 394953a, 1 Nov) ignores the recipe DELAY and PULSE**: valve ON when a frame shows the defect, OFF when the next one doesn't. Simulation: ON moved −4 … −17 ms run to run for the same defect.
- Other review findings (NOT changed, need approval): delay in EjectScheduler counted from end of processing, not capture; 1-pixel noise filter (`min_px = scale/4`); GRAY silently switches to the blue plane; GRAY background = column minimum; HSV hue wrap + SORT IN inverts the background; SIZE `thresh_val = 0` never fires; YOLO stacks row 5 of 64 frames; processing runs in the camera grab thread (backlog when slow); camera_config FRONT/REAR serials differ from CLAUDE.md (lens test pending); **licence lock is OFF on every PC** (`DeveloperMode.is_active()` returns True, `uuid_match = cam_match = True` in `__main__`); `ALLOWED_UUID` is a generic OEM UUID.
- Pre-existing, unrelated: `tools/test_port_detect_sim.py` fails 1 check on the Linux test shell ("legacy text ejector recognised at 115200"); `port_detect.py` not touched.

### Done (not committed yet — the user commits)
- **`capture_timing.py`** (new): `CaptureClock` (camera timestamp → PC time with the smallest offset over 300 frames; line period from the camera clock), `plan_blasts` (ON = row scan time + recipe DELAY, length = object lines + recipe PULSE, 1–200 ms), `TimedValveScheduler` (own thread, ON/OFF bytes at exact times, overlapping blasts merged, LATE/MISSED counters printed every 5 s, 3 ms spin, sets Python switch interval 0.5 ms).
- **`capture_timing_config.json`** (new): settings with defaults.
- **`LAMTON_LINE_NEW_ESP32.py`** (+50 lines): import; row tracking also when the switch is on; ESP_BYTE branch calls `_capture_timed_fire()` only when `capture_timed_eject_enabled`; new method `_capture_timed_fire` (recipe read max 2×/s, `W` watchdog every 5 s, REAR extra delay); start clears/resets; stop switches timed valves off. **Switch OFF = old code path unchanged.**
- **`features.py`**: new default `capture_timed_eject_enabled: False`; fixed `ejector_test_panel_button` default, which was hidden inside a comment on the `eject_diag_enabled` line.
- **`features_config.json`**: `"capture_timed_eject_enabled": false` added.
- Tests: `tools/test_capture_timing_sim.py` (clock jitter/wrap, row math, scheduler, merge, LATE/MISSED, clear) and `tools/test_capture_timed_eject_sim.py` (real `process_frame_for_defects` → `evaluate_fusion_and_fire` → bytes; falls back to empty PyQt5/pymodbus/serial stand-ins when not installed). Both ALL TESTS PASSED on the Linux shell: ON error 0.06 ms, blast 3.91 ms (plan 3.90).
- Guide: `CAPTURE_TIMED_EJECT_TAMIL.md`. Backup of the changed files: `backups/20261102_before_capture_timing/` (+ `backups/` in `.gitignore`).

### Done later the same day (user: "do what is needed")
- **Switch `detection_fixes_enabled`** (OFF) + `min_defect_area_px` (4) in `LAMTON_LINE_NEW_ESP32.py` / `features.py` / `features_config.json`: a channel needs >= 4 defect pixels (counted over all rows) for GRAY and HSV; GRAY keeps the chosen colour plane (no silent switch to BLUE); GRAY background for the relative test = median of the inspected area (old: mean of each column's darkest pixel, which drops when product is dense -> defects missed); SIZE uses `thresh_lower` (68) when there is no camera threshold (old: 0 -> never fired). HSV hue wrap / SORT IN background NOT changed (needs a decision on what "background" is). Test `tools/test_detection_fixes_sim.py` ALL TESTS PASSED (OFF reproduces each old bug, ON fixes it); cost +0.2 ms per 64-line frame.
- **Firmware restored**: commit 4f3f333 (31 Oct, "Clean up ...") had deleted all 13 ESP32 sketch folders incl. the working originals and v3.0, plus `firmware_backups/` (rule 1). Restored from `4f3f333^` to the same paths, 15 files, all byte-identical (git hash check). Not committed yet.
- **`ESP32_63CH_Ejector_v3_1/`** (new) = v3.0 + PIN_PROFILE 3 = esp.ino wiring (DATA 13, CLOCK 14, LATCH 12) as default. NOT compiled (no arduino-cli), NOT hardware-tested.
- `PHASE3_GUIDE.md` section 0 (probe result -> Plan A, which firmware), `CLAUDE.md` key-files line.
- Old tests, run with empty PyQt5 stand-ins on the Linux shell: test_timing_sim, test_ejector_link_sim, test_capture_sim, test_ejector_protocol, test_object_stream PASS. **test_fusion_fix: 2 FAIL (long-object "bridged pulse") - same 2 fails with the original file from before today**: the 31 Oct commits removed the bridging; not caused by today's changes.

### Windows run on the development PC (11:30, user handed over the computer)
- `COMPILE_EJECTOR_V3_1.bat` (arduino-cli 1.5.1 inside Arduino IDE, esp32 core 3.3.12, compile only, no upload): **v3.1 compiles** (306,608 bytes = 23 % flash, 24,916 bytes RAM); v3.0 also compiles. Log `logs\compile_v3_1.txt`. Still NOT hardware-tested.
- `RUN_ALL_TESTS.bat` (`.venv` Python 3.11.9, real PyQt5, `QT_QPA_PLATFORM=offscreen`, restores ejector_config.json / system_config.json afterwards). Log `logs\test_run.txt`.
  - First run: `test_capture_timing_sim` **failed on Windows**: valve ON 18 ms late, because Windows sleeps in 15.6 ms steps unless a 1 ms timer is requested (the sorter requests it at start, a stand-alone scheduler did not). **Fixed**: `TimedValveScheduler.run()` calls `timeBeginPeriod(1)` on Windows.
  - Second run: capture_sim, capture_timed_eject_sim (ON error **0.16 ms**), capture_timing_sim (ON error **0.02 ms**, blast 4.99/5 ms), capture_ui_sim, detection_fixes_sim, ejector_link_sim, ejector_protocol, ejector_test_panel_sim, object_stream, port_settings_sim: **PASS**.
  - Pre-existing fails (not from today's changes): test_fusion_fix (2, bridged pulse removed on 31 Oct), test_port_detect_sim (1, legacy text firmware at 115200), test_line_sync_sim (its baseline commit f705b02 no longer exists - the repo history was restarted, 22 commits).
  - Processing time on this PC: **~3.7 ms per 64-line frame** (detection fixes ON or OFF the same). One frame lasts 7.2 ms, so 64 lines is fine; 32-line frames (3.6 ms) would leave no headroom on this PC.
- torch in `.venv` fails with WinError 1114 when the sorter module is imported from a test (known import-order issue); CNN/YOLO disabled in the tests, sorting path unaffected.

### Channel switching (user question 11:53: object moves from one channel to the next -> old valve OFF, new ON at once)
- Mapping (main path and Background Graph): channel = int((pixel - start) / ((end - start) / 63)); FRONT 19..4046 -> 63.92 px per channel, boundary ch1|ch2 at pixel 82.9.
- Simulation, 4-px needle moving 1 px per frame across that boundary: **Background Graph** switches cleanly (ch1 -> ch1+2 for one frame while 2 px are in each -> ch2). **Main sorting path** kept ch1 ON for 3 frames with both valves, because a channel there needs only 1 pixel column (`min_px = scale/4` = 1 for scale 6). With FRONT + REAR, if REAR's start/end are not matched to FRONT, the old valve stays ON as long as REAR still sees the needle in the old channel (6 px offset -> 6 extra frames): the union FRONT|REAR is right for sorting, so this is a **calibration** job (same needle position must give the same channel on both cameras), not a code change.
- **Switch `clean_channel_switch_enabled`** (OFF) + `channel_min_px` (2): main path needs >= 2 pixel columns per channel (same as the graph window); main path and graph window send the old channel OFF and the new channel ON in **one write, OFF first**. Test `tools/test_channel_switch_sim.py` ALL TESTS PASSED (OFF reproduces the 3-frame overlap; ON = identical to the graph window; jump ch1 -> ch2 = single write `OFF1, ON2`).

### "Eject everything" test lit only some channels (user, 1 Nov) - analysis and fixes (12:05-12:20)
- Dev-PC `recipes.json` RICE = `SIZE: BIG` + `HSV COLOR`, `algo_settings` empty (defaults). Machine-PC settings not seen.
- Simulated rice curtain (51-54 of 63 channels with product): Background Graph window 100 %; RICE program **0 % for white rice, 9 of 54 for dark product** (= "only some"). Causes: (1) SIZE: BIG sees only pixels below the LOWER threshold (dark) and needs >= 46 px inside ONE channel (~3.8 mm) - a rice grain is 24-32 px, so only overlapping grains fire; (2) default HSV box = saturated orange/yellow (S >= 151), white rice never inside; (3) GRAY default = DARK DEFECT; (4) **main-screen RUNTIME TUNING sliders were never read by the detection** (only stored in `self.thresholds`); (5) Background Graph (lower + upper + band) and the sorting algorithms use different logic; `CHROME: RED>GREEN` has no own code (falls into the HSV branch, not changed). Also: HSV colour box was mapped through the whole label, not the centred image (offset ~35/47 px of 565x500); if `hsv.png` is missing, HSV matches every colourful pixel (S, V >= 20) - ship hsv.png.
- **Switch `background_graph_algo_enabled`** (OFF): algorithm **BACKGROUND (GRAPH)** in the program-manager list (no settings dialog) = per camera lower/upper/band + wave mode from bg_config.json, every row of the frame, >= 2 px per channel. Simulation: identical to the graph window, all product channels, no empty channel; band mode works. Switch OFF: the name falls to the old FALLBACK code.
- **Switch `runtime_sliders_enabled`** (OFF): HSV SENSITIVITY -> HSV/DISCOLOUR `sens`; GRAY A: CONTRAST -> GRAY A `sens`; GRAY B: THRESHOLD -> GRAY B absolute threshold (relative-to-background test off for GRAY B); AI THRESHOLD -> YOLO confidence; **SIZE (PIXELS) -> SIZE: BIG measures the whole object** (run of neighbouring columns that differ from the background by the graph thresholds, across channel borders; bright and dark; SIZE: SHORT uses `small`/50). Sliders override the per-algorithm values while ON. AI PURITY and CHROME RATIO still unused.
- **Switch `hsv_box_fix_enabled`** (OFF): colour box maps through the shown (scaled, centred) image.
- Test `tools/test_algo_fixes_sim.py` ALL TESTS PASSED on Windows (.venv, real PyQt5, incl. the HSV box: OFF corner -> (35,47), ON -> (1,0)). Full run 12:17: all pass except the 3 known old fails.

### Machine PC session via AnyDesk (12:53-13:20)
- Machine pulled 7fc1964. `RUN_ALL_TESTS.bat` on the machine (system Python 3.12.10): same results as the dev PC (valve timing error 0.02-0.10 ms, processing **3.2 ms per 64-line frame**, the 3 known old fails). **PyTorch does not load on the machine (WinError 1114)** - CNN/YOLO unavailable there, colour sorting unaffected.
- Machine RICE program = SIZE: BIG + HSV COLOR (confirms the "only some channels" analysis). Background Graph FRONT BLOW 0.5 -> **0** (saved on the machine); REAR already 0/0. Thresholds FRONT 68/221 (start 19, end 4046), REAR 60/164 (start 60, end 3981).
- FRONT camera was offline at first (user reconnected it at 13:09; both online afterwards).
- Wire test, Background Graph: wire detected cleanly (dip far below the lower threshold), LEDs follow, all OFF when the wire leaves. Middle: FRONT ch 34 / REAR ch 33-34; left: FRONT ch 5 / REAR ch 4-5. Right edge not checked yet. **These REAR readings are not reliable** - see next point.
- **Bug found and fixed: Background Graph VIEW FRONT<->REAR fed BOTH cameras into the window.** `load_camera_bounds()` connected `frame_ready` on every call, and `toggle_view()` called it before disconnecting the old camera, so the old camera stayed connected (seen as two alternating background shapes in REAR view; LEDs evaluated with the wrong camera's frames). Fix (no switch - tool bug): `_attach_camera_worker()` removes every connection of both cameras and connects exactly one; `toggle_view()` selects the camera before loading its bounds; close/reject/close_cleanly remove all connections. Test `tools/test_graph_view_switch.py` (real PyQt5 signals, 6 toggles) ALL TESTS PASSED on the dev PC. Repeat the FRONT/REAR wire comparison after pulling.

### Machine, 14:21-14:31
- After pulling 1b7e6a6: REAR view shows one camera only (fix confirmed on the machine).
- Wire calibration with 3 wires held still: FRONT 6 / 32 / 58; REAR (END 3981) 6 / 32-33 / 59. **REAR END 3981 -> 4045** (saved on the machine, camera_config.json there): REAR now 6 / 32 / 58 = FRONT.
- **START SYSTEM fired all 63 valves on an empty chute.** Cause: RICE had `SIZE: BIG`; old SIZE compares the mean of B, G, R column minimums with the LOWER threshold (68). With blue + white lights the empty background is B~100, G~40, R~30 -> mean ~57 < 68 -> every column "dark", every channel 64 px >= 46 px "BIG". Simulation: SIZE: BIG 63/63, HSV COLOR 0/63 on an empty chute. Fixed on the machine by switching SIZE: BIG OFF in RICE (now HSV COLOR only); user confirmed no firing on START. (With `runtime_sliders_enabled` SIZE uses the graph thresholds on the wave-mode planes instead.)

### Machine, 14:39-15:16 - rice test with BACKGROUND (GRAPH)
- User set `background_graph_algo_enabled` true and restarted; RICE = BACKGROUND (GRAPH) only (saved via AnyDesk).
- Rice in the graph (FRONT, blue + white lights): BLUE background ~100-120, rice only ~75-90 (mostly above the lower threshold -> only grain edges fired); GREEN background ~45, rice ~85-90; RED ~30 -> ~80. **SOURCE changed BLUE -> GREEN** (graph dropdown; it chooses the camera colour plane, not the lights). Thresholds: FRONT HI 75 / LO 20 (green background up to ~62 at ch 3), REAR HI 65 / LO 15 (green background up to ~48; REAR green gain 80 vs FRONT 100 - check REAR rice level and adjust). Empty chute: no LED. Rice: LEDs fire.
- Background Graph **test firing checks only the newest 2 lines of each 64-line frame** (`eject_lines`): a ~15-line grain is seen with ~25 % probability -> use START SYSTEM (all 64 lines) for product tests, the graph only for wire/threshold work.
- **Bug fixed: graph SAVE pushed the thresholds to the running sorter but not the SOURCE colour** -> after BLUE -> GREEN a running sorter applied the GREEN thresholds (20/75) to the BLUE plane (~120) = all 63 valves. Now `wave_mode` is synced too (test added to `tools/test_graph_view_switch.py`, passes on Windows). On the machine the user restarts the sorter until this is pulled.

### Machine, 15:20-15:41 - live LED bar, empty-chute firing, graph SOURCE reset
- START SYSTEM (RICE = BACKGROUND (GRAPH), GREEN) fired on an "empty" chute. The main screen had no LED bar (footer placeholder was empty) -> **LiveEjectorLEDBar** added (e8514b0). It showed: 254 blasts, only **ch 38-44** (most ch40 x29, ch44 x29). Graph FRONT and REAR green background at ch 38-44 is clean (~35, far below HI) -> not noise. START turns the feeder on automatically, so most likely leftover rice from the vibrator tray (feeder stream lands there) - user to check with an empty tray.
- **Bug fixed: the Background Graph window always opened with SOURCE = BLUE** (hard-coded) and saved that back on close, so after choosing GREEN, re-opening + closing the window wrote BLUE with the GREEN thresholds (20/75) -> BLUE background ~120 > 75 -> all valves. Now the saved wave_mode is loaded and shown. Test in tools/test_graph_view_switch.py (real window). test_live_led_bar.py made robust (no timer dependence).

### Machine, 15:45-15:52 - empty-chute firing solved, first good rice run
- After pulling f430c8b the graph opens with SOURCE GREEN (fix confirmed).
- START SYSTEM still fired with no rice: 884 blasts in 3 s, ch 31-36 and 39-46 (19,537 in total over the run). The graph (newest 2 lines) was clean with the feeder running. Cause: **BLACK (LO) = 20** on GREEN. START SYSTEM checks all 64 lines x ~64 px = ~4,096 samples per channel per frame; ch 30-46 is the darkest part of the background (green ~35), so downward noise crossed 20 almost every frame. Rice is BRIGHT in green, so the lower limit is not needed for this product: **FRONT and REAR LO = 0** (saved). Result: **0 blasts** on the empty chute for ~1 min.
- Rice fed: 44 channels fired (ch 1-49, most ch 21-31 = feeder stream), back to 0 when the rice was gone (5,454 blasts). Still to check: how much rice actually reached the reject bin (timing / nozzle distance).
- Machine settings now: RICE = BACKGROUND (GRAPH); SOURCE GREEN; FRONT HI 75 / LO 0 (start 19, end 4046); REAR HI 65 / LO 0 (start 60, end 4045); BLOW/DELAY 0 in the graph.
- Lesson: thresholds chosen on the graph (2 lines) need margin for START SYSTEM (64 lines). A real fix is a minimum object size (switch `detection_fixes_enabled` + `min_defect_area_px`, e.g. 20-40 px) so single-pixel noise never fires.

### Next step
1. User: lens test (FRONT/REAR), product speed, commit, copy to the machine PC.
2. Move nozzles to 70–80 mm, switch on, set DELAY (~22 ms at 80 mm, 3.5 m/s), read LATE/MISSED counters, fine-tune with a test bead.
3. With approval: noise filter (min area), GRAY/HSV/SIZE fixes, frame height 32, then ESP32 line trigger (camera LineTrig) for line-synchronous firing.
4. Restore the licence lock before any customer delivery.

---

## 16 Oct 2026 (afternoon) — panel integrated, portable paths, system check, developer mode, deployment package (in progress)

### Done and committed
| Commit | What |
|---|---|
| `6ede06c` | **Ejector test panel integrated** in the sorter: footer button EJECTOR TEST PANEL → `open_ejector_test_panel()` (QDialog, shares `self.esp32.ejector_link`, no 2nd COM port; `test_mode` + `ejector_test_active` set while open = sorting ejections paused; `panel.shutdown()` on close). Locked for OPERATOR. Switch `ejector_test_panel_button` **ON by default — the user's decision** (exception to "new features OFF"). Tested in the real main window with the ejector on COM13: 10/10. |
| `1738786` | **Portable paths**: logo/background/icons use the old locations if they exist (existing installs unchanged), else the program folder; icons now in `assets\` (copied from `LAMTON_BV_SORTER_v3\lambton`; the power button icon was missing on this PC before). Helper .bat files prefer the package `..\.venv`. `OPEN_PDF_GUIDES.bat` fixed (was `d:\hambton\…`). **`tools/system_check.py`** (read-only PASS/WARN/FAIL; probe verified not to reset the ejector). |
| `7a01d83` | **Developer mode** (see below) + **optional AI loading** in the sorter (YOLO model loaded once; failure remembered + shown once, no retry every 64 frames; colour/HSV/contour path unchanged). |
| *(this commit)* | `deploy\` folder **work in progress** (see "Deployment package") + this log + CLAUDE.md |

### Developer mode (development PC only)
- Active only if **all three**: `dev_unlock.json` in the program folder, its UUID = this PC's hardware UUID (WMI/CIM, not the editable registry fallback), and that UUID is in `DEV_UUIDS` in `LAMTON_LINE_NEW_ESP32.py` (this PC: `90001563-B3AE-3E3F-5561-B082E25BC651`).
  Reason for the 3rd condition: a file that only stores "this PC's UUID" can be made on any PC → it would have weakened the licence on customer PCs.
- Skips the start-up UUID/camera lock and the licence-key check; starts without cameras (start-up screen "camera OFFLINE"); title, red status bar and start-up screen say **DEVELOPER MODE**.
- `tools/make_dev_unlock.py` (create / `--remove`; refuses on non-developer PCs). `dev_unlock*.json` in `.gitignore`. `make_deploy_package.py` refuses if an unlock file is tracked or would be in the package (tested: REFUSED; normal build contains none). SYSTEM_CHECK: WARN on the developer PC, **FAIL** on any other PC.
- Tests: developer mode start → main window + banner; file renamed → **Access Denied, exit 1** (normal lock unchanged); foreign-UUID file and unregistered PC → ignored, licence HARDWARE MISMATCH; file restored. Licence generator not touched.
- `dev_unlock.json` **exists now on this PC** (not in git).

### Deployment package — IN PROGRESS (not built on D: yet)
- `deploy\`: `make_deploy_package.py` (builds from the committed HEAD into a NEW folder; never overwrites; leaves out `LICENCE_BOSS.py` + `manufacturer_data_v3.db` (key generator + customer database), dev scripts, firmware dirs, `create_shortcut.vbs`/`SETUP_AUTOSTART.bat` (wrong targets), build files; ejector v2.0 PIN_PROFILE 1 checked byte-identical to the uploaded file), `INSTALL.bat`, `START_SORTER.bat`, `SYSTEM_CHECK.bat`, `MAKE_OFFLINE_WHEELS.bat`, `requirements*.txt`, `constraints.txt`, `firmware_README.txt`, `README_FIRST.txt`, `INSTALL_NEW_PC.md` + Tamil, `ejector_test_kit\` files (copied from the Desktop kit — now versioned).
- Trial builds in scratch: 143 files, ~444–471 MB (incl. MindVision setup 190 MB, Arduino IDE 158 MB, Python 3.12.10 27 MB).
- **Superseded by the user's new requirements (not yet done):** the current `INSTALL.bat`/`MAKE_OFFLINE_WHEELS.bat` still offer a GPU choice and treat AI as optional; `requirements_ai.txt` pins torch 2.13.0. See "Next step".

### Facts found today (keep)
- **Relay ESP32 does NOT need v2.0**: the sorter reads Modbus register 11; old firmware → "Old relay firmware (no watchdog)", works as before. v2.0 recommended later (feeder/air/wiper OFF in 0.5 s), **never run on a real board yet**; same pins/logic as the old firmware.
- **MindVision**: `mvsdk.py` loads `MVCAMSDK_X64.dll` from the program folder first, else System32; the DLL loads its camera plug-ins from the SDK folder in the registry (`DeviceInterfacePath`) → the SDK installer is required. Bundled DLL = installed SDK = **2.1.10.195**; installer in Downloads (not digitally signed; identical copy on D:).
- **Licence on a new PC**: lock 1 = `ALLOWED_UUID` (line ~13520) + both cameras; lock 2 = key prefix = first 4 hex of the UUID. New UUID → change `ALLOWED_UUID`, make a key with LICENCE_BOSS (not shipped), enter it under INFO → MANAGE / UPDATE LICENSE. The licence window is only reachable after lock 1 passes.
- **COM ports** in `system_config.json`: `ejector_port`, `last_port` (relay), `vibrator_port`. If `last_port` is wrong the sorter tries every other port for the relay (COM13 first) → set ports before the first start.
- **Python**: packages need 3.10–3.13 (numpy 2.2.6 has no 3.14 build). 3.12.10 = last 3.12 Windows installer. All 40 packages resolve as Windows wheels for 3.10–3.13 (dry-run).
- **PyTorch indexes**: torch 2.13.0 exists for cpu/cu126/cu130 (no cu128); torch 2.14.0 for cpu/cu126/cu130. RTX 50xx (Blackwell) needs cu128+ → **cu130** for new GPUs.
- **torch after PyQt5** fails with `WinError 1114` in the system Python 3.10 of this PC; in a clean venv not yet re-tested (first attempt failed because of the long scratch path).
- **Windows path limit**: a venv inside a long folder path fails to install PyQt5 ("No such file or directory") → INSTALL.bat must use a short folder (C:\LAMTON_V2) — check still to be added.
- **CNN project `D:\CNN\CNN`** (read only): a separate app "TCM Vision Cashew Grading" (`app\cashew_cnn_grader.py`) with **its own hardware lock for another customer's PC**; its `run_grader.bat` **kills all python.exe processes** → never ship/use them. CNN core = `app\training.py` (MobileNetV2, PyTorch; train/load/predict/evaluate + CLI). Environment there: Python 3.12.10, **torch 2.14.0 / torchvision 0.29.1** (cu130 build), ultralytics 8.4.163, numpy 2.5.2. Models in `app\saved_models`: `cashew_color_cnn.pth` (11 grades), `cashew_color_cnn_new.pth` and `cnn_Default_20261006_235341.pth` (GOOD/TESTA, only 72 images), `best.pt` (YOLO). `CNN_Experiment\train_cnn.py` is broken (`parser` undefined). Training downloads ImageNet MobileNetV2 weights on first use → ship them for offline PCs.
- A clean test venv with torch 2.14.0 + torchvision 0.29.1 (CPU), ultralytics 8.4.163, onnx/onnxruntime 1.23.2 exists at `W:\vt` (W: = subst of the session scratchpad).

### Incidents today
- The session scratchpad **and** last night's scratchpad (arduino-cli + ESP32 core 3.0.7) were **deleted from the Temp folder by someone/something else** (not by my commands). Lost: arduino-cli toolchain (use the Arduino IDE 3.3.12 instead, or reinstall), downloaded Python installer (re-download), trial builds. `Q:` subst now points to a missing folder. Project and D: unaffected.

### Open items (user requests, in this order)
1. **Deployment package update (user's message, 7 points)**: CPU-only, CNN required: torch **2.14.0 + torchvision 0.29.1 CPU** in the normal install (no GPU choice in INSTALL/MAKE_OFFLINE); sorter must work if CNN fails (done in `7a01d83`); `cnn\` folder = `training.py` + models + ImageNet weights + launchers (no grader app, no datasets); CPU speed: PyTorch vs ONNX Runtime vs OpenCV DNN per crop (recommendation only, no change to the ejection path); `UPGRADE_TO_GPU.bat/.md/_TAMIL.md` (cu130 for RTX 50xx, cu126 for older drivers); regenerate `constraints.txt` + offline check 3.10–3.13; package + installed size; INSTALL.bat long-path check; then build **D:\LAMTON_V2_DEPLOY**, run INSTALL/SYSTEM_CHECK/START_SORTER from it, final summary.
2. **CNN plan for the sorter** (user's message, after 1): binary GOOD/BAD per product (bad_* folders merged for training, kept on disk); capture with product + class selection into `dataset\<product>\<class>\`, new bad_ types from the UI, live counts, crop size for 3 mm seeds … cashew; training tool (Tamil/English, min-image warnings, session-aware split, versioned models); evaluation report (confusion matrix, recall per bad_ type, good wrongly rejected, which classes need images); BAD threshold per product in the recipe; ONNX export + timing; models linked to recipes; CNN optional and OFF by default, never blocking colour/HSV/contour ejection. Base: `app\training.py` only.
3. Hardware still to test: cameras (probe/backup), relay board + feeder through the program, 74HC595 board/valves (T3–T8), relay v2.0 (T6), machine-PC disk, licence on the new PC.

## 16 Oct 2026 (night 5) — development folder for another PC: `D:\LAMBTON_V3_16_10_26_DEV`

- **`D:\LAMBTON_V3_16_10_26_DEV`** = `git clone` (full history, 37 commits, 32 of them from 16 Oct) of this project at the latest commit, **about 243 MB** (`.git` 111 MB, 172 tracked files). Contains `CLAUDE.md`, `PROGRESS_LOG.md`, all docs, all firmware sources (`ESP32_*` folders incl. `ESP32_63CH_Ejector_v2_3`, PIN_PROFILE 3 default), `tools\` with all tests, `deploy\` builder and templates, `cnn\`, and a ready-to-open Arduino copy `firmware\ESP32_63CH_Ejector_v2_3\` (+ `firmware\README.txt`).
- **Not inside:** `dev_unlock*.json` (checked: 0 files), `.venv`, logs, `dataset_capture`, `firmware_backups`, camera backups, temp files, this PC's COM13 test config (`system_config.json` = the original tracked file: ejector COM24, relay COM13, feeder COM7 saved; the new start-up logic searches for the boards when those ports do not answer). The clone's `origin` remote (pointing to this PC's folder) was removed.
- **Privacy note:** the licence key generator `LICENCE_BOSS.py` and its customer database `manufacturer_data_v3.db` are tracked in git, so they are inside the dev folder (the customer packages do NOT contain them). Keep the dev folder private.
- Guides: `DEV_SETUP_NEW_PC.md` + `DEV_SETUP_NEW_PC_TAMIL.md` (tools to install, short path `C:\LAMTON_DEV`, `.venv` + exact pip command, how to start the sorter / panel / tests, developer mode on the new PC, Arduino upload steps, the first message to give Claude Code or Google Antigravity, status).
- Same status as the entry below: **v2.3 and the COM-port auto detect are NOT hardware-tested** (real test planned on the other system, ejector on COM24 there). Packages `D:\LAMTON_V2_DEPLOY*` are outdated; rebuild after the real test. A trial package build in my scratch folder (`...\scratchpad\trial_v23`, 486 MB, Temp folder) was not deleted (the delete command was blocked by the safety check): it can be removed by hand.

---

## 16 Oct 2026 (night 4) — ejector firmware v2.3 + COM-port auto detect  (**SIMULATION + COMPILE ONLY: NOT HARDWARE-TESTED**)

**Status: NOT HARDWARE-TESTED** - the user skipped the real-ESP32 test on this PC; the real test is planned on the other system (ejector on COM24 there). Everything below was verified only with simulated boards (11 test files ALL TESTS PASSED), compile (0 errors, 0 warnings, PIN_PROFILE 1, 2, 3) and a sorter smoke start with no COM ports visible.

**Firmware `ESP32_63CH_Ejector_v2_3`** (from v2.2; ready copy `Documents\Arduino\ESP32_63CH_Ejector_v2_3\`, default **PIN_PROFILE 3** = DATA 23, CLOCK 18, LATCH 19, no OE pin, shiftOut MSB first / chip 8 first):
- **115200 baud** (was 921600). FIRE message on the wire ~2.1 ms instead of 0.26 ms (+1.8 ms per fire; TIMING_DESIGN.md) - the user decided this.
- Blue LED: OFF when idle; ON only while a valve output is active, at least 50 ms.
- **Auto-arm**: any valid binary command arms the board (IDENTIFY does not). No TRIPPED state, no ARM / RE-ARM needed.
- **Silent watchdog**: PC quiet 500 ms -> only `clearAll()` (shift zeros), `wdt_trips` counted, no event, nothing refused; next command re-arms. Pulses are timed by the ESP32.
- **IDENTIFY** (binary 0x43 -> 0x85: "EJECTOR", firmware, pin profile, board ID from the MAC) and text PING reply `ACK:PING PONG:LAMTON_EJECTOR_V2.3 P3 ID:...`.
- Power-up warning (profile 3, /OE tied to GND): outputs undefined ~0.3 s after boot until zeros are shifted: keep valve air / 24 V off while powering up.

**PC side**
- `ejector_link.py`: BINARY_BAUD 115200; `connect()` detects by PROTOCOL: text PING first (v2.x answers `PONG:LAMTON_EJECTOR_V...`, the OLD firmware only `ACK:PING`, so the two can never be confused and binary bytes are never sent to the old firmware); fallback probe at 921600 finds v2.0-v2.2 (reported as OLDER); `identify()`.
- **`port_detect.py`** (new, no Qt): finds the ejector (v2.3 / old text / older v2.x) and the relay (Modbus 9600: read register 11, or write to the non-existent register 200 -> echo/exception; no relay is switched) on ANY COM port by real replies; never sends Modbus to a port that answered as the ejector; one port never serves two devices; **the feeder (Nuvoton) board never answers: it is never probed, only "port opens" is checked**; disabled devices are never opened or probed; `resolve()` = start-up logic (saved port -> verify -> auto detect -> clear messages -> save).
- Sorter: **no hard-coded COM number and no port substitution any more** (removed the "if relay == ejector port use COM13" rule that hijacked the ejector port); `ESP32Controller.connect()/connect_ejector()/connect_vibrator()` refuse a port used by another device; `ensure_relay()` reconnects only on the configured port and only if enabled; start-up splash no longer opens ports; `auto_connect_esp32` rewritten on `port_detect`; non-modal info window for port/AI notices (a non-modal QMessageBox crashed on the offscreen platform); new footer button **COM PORTS** (`port_settings_ui.py`, ENGINEER login): dropdown per device (port + USB chip + description) incl. Disabled, Refresh, Auto detect, Test, Save and connect -> `system_config.json` (ejector_port / last_port / vibrator_port + ejector_enabled / modbus_enabled / vibrator_enabled).
- Test panel: RE-ARM button and TRIPPED banner removed (status badge only); standalone start runs Auto detect; labels with USB chip. `tools/system_check.py` section 5 = what every port answers + what the sorter would use (expects firmware v2.3). `tools/ejector_hw_check.py` port optional (auto detect). Builder/kit/docs/package templates -> v2.3 (+ `port_detect.py` in the kit).
- Tests added/changed: `tools/test_port_detect_sim.py`, `tools/test_port_settings_sim.py` (dialog + start-up scenarios: ejector COM13 + relay COM24, swapped, other way round, ejector alone, relay disabled, feeder untouched), `tools/port_sim.py` (fake boards), link/panel/timing/line-sync tests adapted to v2.3 behaviour. Measured by a test: idle link keeps sending heartbeats (board stays armed); after silence the next click fires at once without re-arm.
- Found by a test: an empty port list passed to `scan()` meant "scan everything" (would have probed the feeder port) - fixed.
- `system_config.json`: this PC's COM13 test config was discarded; the **original tracked file is in place**; my backup copy was deleted.

**NOT hardware-tested (needs the real system):** v2.3 firmware on the board (LED behaviour, auto-arm, silent watchdog, IDENTIFY, 115200 timing/latency, all 63 outputs with PROFILE 3 wiring); port detection with the real ejector, relay (old firmware echo / v2.0 read), feeder; the COM PORTS dialog and start-up messages in the real GUI on Windows; sorter + ejector at 115200 under production load; **the wiring itself (23/18/19 vs 14/13/12) is unconfirmed - if no LED lights with PROFILE 3, try PIN_PROFILE 1**.
**Known limits / open:** feeder cannot be detected; probing the old relay firmware toggles its status LED only; `gui_esp32_ejector_tester.py` still has ARM / WATCHDOG TEST buttons (v2.0-v2.2 semantics, not updated); the sorter keeps its WDT_TRIP safety handler for v2.0-v2.2 boards. **Packages `D:\LAMTON_V2_DEPLOY*` (v1-v3) are outdated** (v2.0/v2.1 firmware, 921600, old port logic) - rebuild after the real test. Next step: upload v2.3 on the other system, run `tools\system_check.py` and the test panel there, then rebuild the package.

---

## 16 Oct 2026 (late night 3) — sorter UI started on this PC with the ejector on COM13 (real results)

- Step 1 of the v2.2 valve test: board on COM13 runs **v2.2**, boot line `DATA 23 CLK 18 LATCH 19 OE -1 | PROFILE 3 (guide wiring 23/18/19, /OE tied to GND, shiftOut)`, state ARMED, crc_err 0, len_err 0, nothing fired. Steps 2-6 (valve tests) **not done yet**.
- `system_config.json` was changed for this PC only (ejector_port COM13, modbus_enabled false, last_port null, vibrator_port null); backup `system_config.json.backup_before_COM13_test` (untracked). **Restore:** `copy /Y system_config.json.backup_before_COM13_test system_config.json` or `git checkout -- system_config.json`. Do not commit the changed file.
- `START_LAMTON_SORTER.bat` started the sorter in DEVELOPER MODE (no cameras) and it connected the ejector (v2.2, binary). **BUG found:** although `modbus_enabled` is false, another start-up path called `ESP32Controller.connect(port)`; `connect()` has hard-coded code "if the Modbus port == ejector port: force COM13" (written for the machine: ejector COM24, relay COM13). On this PC (ejector = COM13) it **closed the ejector link and opened COM13 as the "relay board"** at 9600 baud, sent relay Modbus frames to the ejector ESP32 and then the ejector reconnect failed ("Access is denied"). The UI check was stopped; the sorter processes were killed. Ejector afterwards: v2.2 OK, ARMED, `wdt_trips 1` (the kill), **`fired 1`** (one output activation happened during the sorter session - cause unknown; 24 V state at the time unknown).
- **Not fixed (user said: change nothing else).** Proposed fix: in `ESP32Controller.connect()` never substitute "COM13"; if `port == ejector_port` refuse and print a message; relay auto-connect callers (lines ~6875, 6901, 6916, 11671, 12087, 12242, 12950, 13076) must respect `modbus_enabled == false`. On the real machine (ejector COM24, relay COM13) the hard-coded rule is harmless.
- Footer button is named **EJECTOR TEST PANEL** (not "63-CH EJECTOR TEST"), next to SIMULATE ALARM; locked for OPERATOR (log in as ENGINEER).

---

## 16 Oct 2026 (late night 2) — ejector v2.2 (PIN_PROFILE 3 = Connection-Guide wiring 23/18/19)

User statement: PCB wired DATA GPIO23 -> pin 14, CLOCK GPIO18 -> pin 11, LATCH GPIO19 -> pin 12, pin 13 (/OE) tied to GND, MR + VCC to 5 V, common GND (wires checked on the ESP32 pins).
`ESP32_63CH_Ejector_v2_2` (from v2.1): **new PIN_PROFILE 3 = default**: bit-banged `shiftOut` (MSB first, chip 8 first, latch LOW/shift/HIGH), **no OE GPIO** (`HAS_OE 0`, nothing driven), no GPIO 27/32, no SPI. Watchdog TRIP / STOP = `clearAll()` shifts 8 zero bytes and fire commands are refused until ARM. FW_VERSION 0x0202. Profiles 1 and 2 unchanged (same binary size as v2.1). Compiled 0 warnings for profiles 1, 2, 3. Ready copy: `Documents\Arduino\ESP32_63CH_Ejector_v2_2\` (PROFILE 3 is the default in the file). `tools/system_check.py` now expects ejector firmware **v2.2** (v2.1/v2.0 -> WARN "older"). All 9 test files pass.
**Not verified on hardware.** The deployment package (v3) still contains v2.1 and the docs still name v2.1 as current - update them only after v2.2 is confirmed on the PCB.
**Search result:** no firmware in LAMTON_V1 or `D:\LAMBTON_V6_FINAL` ever drove a 74HC595 with DATA 23 / CLOCK 18 / LATCH 19. Only `ESP32_74HC595_Connection_Guide.html` and `ShiftRegister_DaisyChain_Diagram.html` show 23/18/19 (documents). v2.0/v2.1/v3.0 PROFILE 2 uses 23/18 but LATCH 21 and OE 22. GPIO 19 is /OE in the old firmware (ShiftRegister, LATEST, v2.x PROFILE 1) and a relay output in the relay firmware (other board).
**Contradiction to resolve:** the old firmware drives 14/13/12 and (per earlier user reports) lit the LEDs on this PCB; that cannot be if the wires are on 23/18/19. First check on the board: boot line of v2.2 must read `PROFILE 3 (guide wiring 23/18/19 ...)`; if no LED lights, check the wires on GPIO 14/13/12 instead (PIN_PROFILE 1, firmware v2.1).
**Power-up warning (profile 3):** with /OE tied to GND the 595 outputs are undefined until zeros are shifted (~0.3 s after boot): keep valve air / 24 V off while powering up.

---

## 16 Oct 2026 (late night) — LED test on the real PCB, step by step (real results only)

| Step | Result |
|---|---|
| 1. Identify the board (COM13, CP2102, nothing uploaded, no valve fired) | **The board runs firmware v2.0, NOT v2.1.** Boot line: `LAMBTON EJECTOR v2.0 READY | 921600 baud | pins: DATA 14 CLK 13 LATCH 12 OE 19 | LEGACY PINS (GPIO12 boot risk)`; PING reply `LAMTON_EJECTOR_V2.0`; no answer at 115200 (so not the old firmware). => v2.1 is **not on the board** (never uploaded, or v2.0 uploaded again). Pins = PROFILE 1 (correct). Consequence: any "v2.1" LED statement made earlier cannot be attributed to v2.1 hardware-wise unless this board had it before; v2.1 is still UNCONFIRMED. |
| 2.-5. LED tests | pending (waiting for the user) |

---

## 16 Oct 2026 (night 2) — V6_FINAL reference comparison; package v3 NOT hardware-verified

**WARNING: `D:\LAMTON_V2_DEPLOY_v3` is NOT hardware-verified.** It was built (commit `710ac3c`, ejector firmware v2.1 PIN_PROFILE 1) before v2.1 was confirmed on the PCB. The user's reports conflict (earlier: "v2.1 works, all 63 LEDs correct"; a later message asks why v2.0/v2.1 did not drive the PCB; the latest test results were left blank). **Treat v2.1 as UNCONFIRMED until the user confirms** (test: v2.1 + test panel, status ARMED, LEDs 1-63; and old ShiftRegister + V6 UI). The v3 package INSTALL/SYSTEM_CHECK/START test was started and interrupted (session ended) - **not completed**; the test copy `C:\LAMTON_V2_TEST` was deleted. `D:\LAMTON_V2_DEPLOY` and `_v2` are older (v2.0 PROFILE 1, which failed on the PCB).

**Read-only comparison with `D:\LAMBTON_V6_FINAL`** (never modified; not used as code base):
- Its `ESP32_63CH_ShiftRegister`, `ESP32_63CH_LATEST`, `OLD_ESP32_CODE`, `ESP32_8CH_ModbusRelay_NEW`, the three wiring guides (`ESP32_74HC595_Connection_Guide`, `74HC595_Physical_Pinout`, `ESP32_Visual_Diagram`), `esp32_logic.py`, `ejector_test_window.py` and `system_config.json` are **byte-identical (SHA-256) to the copies in LAMTON_V1**. Only `ejector_config.json` differs (pulse 10 / delay 500 vs our rewritten runtime file). So V6 gives no new hardware information beyond what the old firmware already is.
- Wiring per `ESP32_Visual_Diagram` + firmware: DATA GPIO14, CLOCK GPIO13, LATCH GPIO12, 74HC595 pin 13 /OE **hard-wired to GND** (firmware also drives GPIO19 as OE, harmless), pin 10 SRCLR to 5 V. **`ESP32_74HC595_Connection_Guide.html` shows GPIO 23/18/19 (generic example): it matches no firmware and not the PCB** (the old firmware cannot work on a PCB wired 23/18/19). PIN_PROFILE 1 = exactly the V6 pins; v2.1 PROFILE 1 drives them with the same shiftOut code and start-up order. **No `ESP32_63CH_Ejector_v2_2` was created**: it would be identical to v2.1 for these pins.
- V6 ejector test commands (text, 115200): `EJ:<ch>:<ms>` and `SA:b0..b7[:ms]` (channel idx -> byte idx//8, bit idx%8) = the remap default mapping; v2.x supports both text commands, so the commands are not the difference.
- Why v2.0 failed is **not proven**. v2.0 vs old: hand-written bit-bang with 1 us delays, GPIO 27/32 driven as outputs, /OE HIGH during boot, binary 921600. With /OE tied to GND the OE order cannot matter; remaining suspects = slower clock or the GPIO 27/32 outputs. v2.1 removed all three.

**Unfinished from the same request list (not started/finished):** package v3 test from `C:\LAMTON_V2_TEST`; development copy `D:\LAMTON_V2_DEV` (git clone + `DEV_SETUP_NEW_PC.md` + Tamil); `APP_BUILD_PLAN.md` (PyInstaller + Inno Setup). Facts gathered for the plan: the sorter uses `__file__` 28 times (main) + 1 each in 8 modules and `os.chdir(script dir)` at start (frozen exe needs `sys.executable` base); it writes config files next to itself (`system_config.json`, `vision_settings.json`, `wiper_config.json`, `tcm_license.dat`, recipes) -> install into a writable folder (e.g. `C:\LAMTON_V2`, not Program Files); the old `LAMTON_COLOUR_SORTER.spec` exists.

---

## 16 Oct 2026 (night) — ejector v2.1 (old-style shiftOut for PIN_PROFILE 1)

User result: the PCB works with the OLD firmware; v2.0 PIN_PROFILE 1 has a working PC link but wrong/missing outputs. **Cause not identified by reading** (bit/chip order, latch, OE polarity were already identical). `ESP32_63CH_Ejector_v2_1` removes every remaining difference for PROFILE 1: `shiftOut(MSBFIRST)` copied from the old firmware (no 1 µs delays), old start-up order (OE LOW at once, then 8 zero bytes), GPIO 27 / 32 not configured, no SPI. Rest = v2.0 (binary protocol, watchdog incl. OE HIGH on TRIP, remap, test, 921600). FW_VERSION 0x0201. Compiles 0 warnings for PROFILE 1 and 2. SPI check: ESP32 core uses its default pins (HSPI MISO = GPIO12, VSPI MISO = GPIO19) only when ALL `SPI.begin` arguments are -1; v2.0 never calls `SPI.begin` in PROFILE 1 and passes MISO -1 in PROFILE 2 -> GPIO 12 was never claimed. Ready copy (PROFILE 1): `Documents\Arduino\ESP32_63CH_Ejector_v2_1`. **Not yet tested on the board; not in the deployment package yet** (package still has v2.0 PROFILE 1). If v2.1 works: switch package + docs to v2.1; if not: the DIAG firmware measurements are the next step.

---

## 16 Oct 2026 (evening) — ejector on the 74HC595 board: outputs do not respond

User report: v2.0 (PIN_PROFILE 1) connected to the 595 board, no output; measurement values were NOT supplied (blanks), old-firmware result not supplied.
**Code comparison old `ESP32_63CH_ShiftRegister` vs v2.0 PROFILE 1: no functional difference found** — pins (DATA14/CLK13/LATCH12/OE19), OE polarity (LOW = enabled; both LOW in normal operation), latch order (LOW → shift → HIGH), bit order (MSB first), chip order (chip 8 first), ACTIVE_LOW false, channel→bit mapping: all identical. No SPI in profile 1 (bit-bang). Differences that exist: v2.0 clock is slower (1 µs delays, ~300 kHz vs fast shiftOut); v2.0 drives GPIO 27 (debug) and GPIO 32 (PC OK) as outputs (old: not); OE is HIGH for the first ms after boot, LOW after setup; v2.0 sets OE HIGH on a watchdog TRIP until RE-ARM; v2.0 only writes the registers when the output mask changes (same as old). So the cause is probably **hardware/levels** (3.3 V ESP32 signals into 74HC595 at 5 V: VIH needs ~3.5 V; check GPIO 27/32 wiring; /OE wiring; MR; VCC/GND), not the v2.0 code.
New: `ESP32_63CH_Ejector_v2_1_DIAG` (separate folder, compiles 0 warnings, copy in `Documents\Arduino`): old output code (shiftOut), 115200 text commands to hold each pin / all outputs / one channel / walk, for multimeter measurements. Not production firmware. **Next:** user uploads DIAG, reports the measurements; then decide the real fix (likely 74HCT245 level shifter as planned).

---

## 16 Oct 2026 (late afternoon) — CPU-only CNN package v2 built and tested

**Package: `D:\LAMTON_V2_DEPLOY_v2`** — commit `9fafc1d`, 156 files, **512.9 MB** (359 MB drivers, 92 MB app, 40 MB cnn). The old `D:\LAMTON_V2_DEPLOY` is untouched (superseded, can be deleted by the user). No `dev_unlock*.json` inside (builder refuses; checked).
Installed size on the new PC (measured): `.venv` **1,188 MB**, whole `C:\LAMTON_V2` ≈ **1.7 GB** (without drivers it is 0.2 GB less; drivers can be deleted after installing). Offline wheels (Python 3.12): ≈ 350 MB.

| Commit | What |
|---|---|
| `c09956a` | CPU-only CNN: `cnn\` folder (training.py from the CNN project — no lock; 3 trained models; ImageNet MobileNetV2 weights for offline training; `RUN_CNN_TRAIN.bat`; `check_cnn.py`), `requirements_cnn.txt` (torch 2.14.0 / torchvision 0.29.1 / ultralytics 8.4.163 / onnx + onnxruntime 1.23.2 = the CNN project's versions, CPU build), `constraints.txt` regenerated (49 pins; resolves for Python 3.10, 3.11, 3.12, 3.13), INSTALL.bat without GPU choice + CNN step (failure = warning) + **long-path check** (>90 chars → stop), MAKE_OFFLINE_WHEELS.bat without gpu, `UPGRADE_TO_GPU.bat/.py/.md/_TAMIL.md` |
| `baea656` | builder: `cnn\` at package root, CRLF text files |
| `9fafc1d` | **`import torch` first in the sorter** (Windows DLL order: torch after cv2/PyQt5 fails with WinError 1114 — reproduced in a clean venv). Optional: any failure only prints `[AI] PyTorch not available`; YOLO branch checks it. SYSTEM_CHECK tests the same order. |

**Tests (all from `D:\LAMTON_V2_DEPLOY_v2` copied to `C:\LAMTON_V2_TEST`; test copy deleted afterwards)**
- **INSTALL.bat: PASS** (exit 0; `.venv` created; required packages OK; CNN packages OK; CNN self-test OK, 9 ms/crop CPU). Second run: `.venv` reused, PASS.
- **SYSTEM_CHECK.bat: PASS 16 / WARN 1 / FAIL 8.** The 8 FAIL are this PC's missing hardware only: 2 cameras, COM24 + COM13 settings, start-up hardware lock x2, licence key, "Access Denied" summary. WARN = disk 19.8 GB (<20).
- **START_SORTER.bat:** without `dev_unlock.json` → **Access Denied, exit code 1** (customer behaviour, correct); after `tools\make_dev_unlock.py` (from the package) → sorter process **running after 30 s**; developer-mode window title + red status bar confirmed; unlock file removed → locked again.
- CPU training works offline (smoke test, 2 classes, 2 epochs); sorter starts with torch/ultralytics blocked; all 9 `tools/test_*.py` pass.
- Developer mode (item 2 of the request) was already complete in `7a01d83`; re-verified inside the package as above.

**Not done / still to do**
- CPU speed comparison PyTorch vs ONNX Runtime vs OpenCV DNN (only PyTorch measured: ~8–10 ms per 224x224 crop, 6 threads) — recommendation not written yet.
- **CNN GOOD/BAD plan** (capture per product/class, training GUI, evaluation report, thresholds, ONNX export, recipe link) — next session, as asked.
- `UPGRADE_TO_GPU`: choice logic (driver → cu130/cu126, RTX 50 needs ≥580) and dry run tested; the real upgrade needs an NVIDIA GPU.
- Python 3.12 / offline wheels install not run (resolve-only for 3.10–3.13; install tested on 3.10).
- The relay v2.0 firmware has never run on a board; `cnn\models` GOOD/TESTA models were trained on only 72 images (test models).
- Hardware (new PC): cameras (probe + backup first), relay board + feeder via the program, 74HC595/valves T3–T8, licence (new `ALLOWED_UUID` + key), COM ports in `system_config.json`, disk speed, first run with air closed.
- `C:\LAMTON_V2_TEST` was removed with `rm`; PowerShell's Remove-Item refuses that path (tool protection).

### Exact next step (next session)
Start the **CNN GOOD/BAD plan** (item 2 in "Open items"): use `cnn\training.py` as base; add the ONNX/OpenCV DNN timing comparison first (small), then capture (product + class selection → `dataset\<product>\<class>\`), training GUI, evaluation report. Keep CNN optional/OFF and out of the ejection path. Rebuild the package into a NEW folder afterwards (`deploy\make_deploy_package.py`).

---

### Package built (usage limit reached, 16 Oct 2026 ~14:10) — SUPERSEDED by the v2 package above
`D:\LAMTON_V2_DEPLOY` built from commit `0cfe750` with `deploy\make_deploy_package.py`: 144 files, 471 MB, no `dev_unlock*.json` inside (checked).
**It is the OLD (pre-CPU-CNN) version and NOT tested from the USB drive:** INSTALL.bat/MAKE_OFFLINE_WHEELS.bat still have the GPU choice, `requirements_ai.txt` still pins torch 2.13.0, no `cnn\` folder, no UPGRADE_TO_GPU files, `constraints.txt` not regenerated for torch 2.14.0. SYSTEM_CHECK.bat / START_SORTER.bat / INSTALL.bat were never run from the package; the long-path check for the venv is not added.
**Unfinished:** the 7-point CPU-only CNN update (below), then rebuild into a NEW folder (the builder never overwrites; rename or delete the old one first), test from the package; the CNN GOOD/BAD plan; real-hardware tests.

### Exact next step
Continue open item 1: update `deploy\requirements_ai.txt` → CNN requirements (torch 2.14.0 / torchvision 0.29.1 CPU, ultralytics 8.4.163) and remove the GPU choice from `INSTALL.bat` / `MAKE_OFFLINE_WHEELS.bat`; in `W:\vt` re-test "torch after PyQt5" and the sorter start; then build `cnn\` and run the CPU speed comparison.

---

## 16 Oct 2026 — first real hardware: ejector ESP32 on the development PC (camera not connected)

| Item | Result |
|---|---|
| Board | 30-pin ESP32 DevKit V1 = **EJECTOR** board, USB chip **CP2102** |
| Driver | CP210x driver was missing (Device Manager error 28); user installed it 11:19 |
| Port | **COM13** on the development PC (on the machine PC the ejector is normally COM24 — COM numbers are per PC) |
| Chip (esptool 4.6 `flash_id`) | **ESP32-D0WD-V3 rev v3.1**, dual core 240 MHz, 40 MHz crystal, **4 MB flash**, MAC `58:2a:bd:74:27:84` |
| Flash backup | **NOT made — skipped by the user's decision.** Attempts: 460800 baud hung (no data); 115200 baud stopped at 75 % (`Packet content transfer stopped`) because the USB link dropped out (COM13 vanished, then came back). No partial file kept. |
| **Rollback** | **the old source `ESP32_63CH_ShiftRegister/ESP32_63CH_ShiftRegister.ino`** (user's decision): upload it with Arduino IDE to return to the original firmware. |
| Upload method | **user uploads with Arduino IDE** from `C:\Users\USER\Documents\Arduino\ESP32_63CH_Ejector_v2_0\` — a copy of the v2.0 sketch with `#define PIN_PROFILE 1` (legacy pins GPIO 14/13/12/19) on line 56. The project original is unchanged (still 2). |
| Compile check of that copy | arduino-cli, ESP32 Dev Module, **esp32 core 3.0.7**: 0 errors, 0 warnings (`--warnings all`); 307,129 bytes (23 %); binary says "LEGACY PINS". No extra libraries (SPI, Preferences, esp_task_wdt are part of the core). |
| New tool | `tools/ejector_hw_check.py COM13 [--channel N]` — T2 from the command line (firmware detect, crc_err/len_err) + single-channel test |
| UI compatibility check of that copy | **0 mismatches**: all 22 message/command codes, baud, CRC, payload sizes (fire 16, test 13, config 12, cmd 3), 12 status fields match `ejector_link.py`. Main program + `gui_esp32_ejector_tester.py` talk to the ejector only through `EjectorLink`. Old stand-alone tools (`esp32_logic.py`, `ejector_test_window.py`, `standalone_ejector_test.py`, `check_esp32_comm.py`) open 115200 themselves → **do not work with v2.0** (not used by the sorter). |
| Upload (user, Arduino IDE) | **OK** on COM13, hash verified, **esp32 board package 3.3.12** (not 3.0.7 that I compiled with). Serial Monitor 921600: `LAMBTON EJECTOR v2.0 READY \| 921600 baud \| pins: DATA 14 CLK 13 LATCH 12 OE 19 \| LEGACY PINS (GPIO12 boot risk)` |
| **T2** (`ejector_hw_check.py COM13`) | **PASS** — `v2.0 BINARY (COM13)`, state ARMED, crc_err=0, len_err=0, ACK round trip **1.5 ms median / 2.6 ms max** |
| **Single-channel test** (`--channel 1`, 3 × 30 ms, gap 500 ms) | **PASS** — `CH 1 -> OUT 0 -> CHIP 1, Q0`, fired +3, events TEST_STEP ×3 + TEST_DONE, steps 530 ms apart on the ESP32 clock. Only USB connected (no 595 board, no 24 V): visible = ESP32 blue LED. |
| Watchdog (by accident) | my timing test paused the heartbeat too long → board **TRIPPED 14×** (safe state, outputs off), then re-armed normally by the next connect → watchdog trip + re-arm work. Counters on the board count since power-up (wdt_trips 15, fired 24 at the end). |

**New: 63-channel Ejector Test Panel (separate, not merged into the sorter yet)**

| Item | Result |
|---|---|
| Files | `ejector_test_panel.py` (class `EjectorTestPanel` + standalone window), `RUN_EJECTOR_TEST_PANEL.bat` (`--port COM13`, `--sim`), `tools/ejector_sim.py` (reusable simulated v2.0 ESP32), `tools/test_ejector_test_panel_sim.py`, `MERGE_PLAN.md` |
| Features | COM dropdown + Refresh, Connect/Disconnect, badge (fw, BINARY/TEXT, ARMED/TRIPPED), RE-ARM, red TRIPPED banner; 9×7 channel grid (click = 1 pulse, lit by TEST_STEP events); pulse 0.5–200 ms, gap 10–5000 ms; single test (channel, repeat); auto sequence from/to + loop + progress; FIRE ALL (confirm, max 50 ms); STOP ALL (always on, Esc); counters fired/crc_err/len_err/no-ack/watchdog trips + heartbeat; remap read/swap/save/default; timestamped log + Clear |
| Design decisions | communicates only via `ejector_link.py`; embedded mode takes `link_provider` (a function, survives sorter reconnects) and only **reads** link state, so the sorter's `on_event` safety callback is never replaced; connect/status/remap in a worker thread, STOP is a direct write; one Esc shortcut only (two = "ambiguous" in Qt = neither fires); grey palette of the sorter's ejector dialog |
| Simulation test | **57/57 PASS** (`tools/test_ejector_test_panel_sim.py`, off-screen) incl. error → valves off, lost USB, window close, embedded mode (no 2nd port, shutdown keeps link open) |
| Real board COM13 (USB only, no 24 V) | **ALL PASS**: CH1 click +1, single CH1×3 +3, auto 1–63 +63 in 1.62 s, loop + STOP (ESP32 fired count frozen), Esc, FIRE ALL 10 ms +63, watchdog trip shown + RE-ARM, remap read/swap/default **in RAM only (not saved — board flash unchanged)**, clean disconnect; crc_err 0, len_err 0, no-ack 0 |
| Sizes | embedded 916 × 970 px, standalone 1220 × 970 px minimum |
| Not done | merging into `LAMTON_LINE_NEW_ESP32.py` (plan in `MERGE_PLAN.md`, switch `ejector_test_panel_button` OFF by default); remap **Save** tested only in simulation |

**Disk space incident + file check (16 Oct, afternoon)**

| Item | Result |
|---|---|
| Cause | C: dropped to **69 MB free** (morning: 8.1 GB): Arduino IDE board package 3.3.12 (`Arduino15`, 9.0 GB) + my arduino-cli set-up in the temp folder (5.9 GB). Effect: capture disk guard (< 1 GB) stopped writing → `test_capture_sim` / `test_capture_ui_sim` failed (code was fine). |
| User cleanup | user deleted some `.bin` files → **14 GB free** |
| Tracked files | `git ls-files --deleted`: **none missing** |
| Untracked files | compared with the full backup `D:\LAMTON_BACKUP_2026-10-15` (4639 files incl. ignored ones): **0 missing**. Present: `best.pt` (YOLO, loads: DetectionModel, 15 classes), `tcm_license.dat`, `MVCAMSDK_X64.dll` (loads), `recipes.json` + all JSON configs (parse OK), Arduino sketch copy, D:\LAMTON_V1 original. Never existed (not losses): `MVCAMSDK.dll` (32-bit only), `background.jpg` (points to another folder; original has none either), `channel_calibration.json` (made by the Phase 3 tool), `session.json` (written per capture run). |
| Outside the project | `arduino-cli.exe` + `LICENSE.txt` in my temp tool folder were gone (not matched by my delete commands) → **restored from `cli.zip`**, compile check OK again (307,129 bytes) |
| Cleanup done (approved) | deleted `Arduino15\staging` (IDE download zips), my compiler download zips, 14 old build folders → **18 GB free**. Kept: installed IDE core 3.3.12, my arduino-cli core 3.0.7 (`data\`) |
| Tests | **all 9 test files ALL TESTS PASSED** (capture tests pass again) |
| Finding (not fixed) | `import torch` **after** PyQt5 fails on this PC with `WinError 1114` (`torch\lib\c10.dll`); torch 2.13.0 + `best.pt` load fine on their own. The sorter imports `ultralytics` late (after PyQt5, ~line 11285), so its YOLO mode may fail on THIS PC. Known Windows torch/Qt DLL order issue; usual fix = `import torch` before PyQt5 at program start. Check on the machine PC before changing anything. |

**Ejector Test Kit on the Desktop (for testing on another PC)**

| Item | Result |
|---|---|
| Location | `C:\Users\USER\Desktop\EJECTOR_TEST_KIT` — **9 files, 147,915 bytes (≈ 148 KB)** |
| Contents | `firmware\ESP32_63CH_Ejector_v2_0\*.ino` (= the uploaded Arduino copy, PIN_PROFILE 1), `firmware\ESP32_63CH_ShiftRegister\*.ino` (rollback), `ejector_test_panel.py`, `ejector_link.py`, `tools\ejector_sim.py` (all SHA-256 identical to the sources), `RUN_EJECTOR_TEST_PANEL.bat`, `requirements_test_panel.txt` (PyQt5 5.15.11, PyQt5-Qt5 5.15.2, PyQt5_sip 12.19.0, pyserial 3.5), `README.txt`, `README_TAMIL.txt` (driver, Arduino IDE 3.3.12 / ESP32 Dev Module / 115200 / Serial Monitor 921600, start, COM port, tests **E1–E12**, rollback) |
| Kit `.bat` | no fixed path; tries `python`, `py -3`, `python3`, then every Python from `py -0p`; uses the **first one that has PyQt5 + pyserial** (this PC has 3.10 and 3.14); skips the Microsoft Store placeholder; prints the exact pip command if packages are missing. Tested: normal PATH → 3.10; first Python without packages → falls back to `py -3`; only `py -0p` → found (path with `*` marker); no packages → pip message; no Python → install message |
| Kit test | started from the kit folder: panel + simulator OK, **0 modules loaded from the project folder**; `.bat --sim` keeps the panel running; real ESP32 COM13 from the kit: `v2.0 BINARY · ARMED`, CH1 click fired 390 → 391, crc_err 0 |
| Project folder | unchanged (only copied). The kit's `.bat`/READMEs/requirements exist **only on the Desktop** (not in git) |

**Found and fixed (PC side, `ejector_link.py`):** the status request sent while connecting was tracked for an ACK, but the firmware answers it with STATUS, not ACK → a permanent false "no-ack 1" in the tester/sorter line. Now sent untracked. Sim tests (protocol, link, timing, line sync) still ALL TESTS PASSED; on hardware no-ack = 0.

**Found, NOT fixed — ESP32 → PC reply delay at a test start:** the ACK of a real TEST command is sent by the ESP32 1.6 ms after the command (first valve pulse at 1.7 ms — **firing is not delayed**), but reaches the PC only 35–117 ms later, together with the next heartbeat ACK (the end of that burst waits in a buffer until the next bytes push it out). Text replies of 15–450 bytes, `test_stop`, `stop_all`, heartbeats: no delay (1–7 ms). Effects: the TEST ACK can be counted as "no-ack" (PC waits 100 ms); the Phase 2 latency report could see such outliers. Not yet known whether ESP32 core 3.3.12 UART driver or CP2102/Windows is responsible. **Next:** measure the same with FIRE commands during the latency test (T8/Phase 2); optionally re-upload with core 3.0.7 and compare.

---


**Watch-outs noted today**
- The USB link dropped once during a 6-minute read. Use a rear USB port and a good short cable; an upload that is cut off leaves the board without firmware (always recoverable by uploading again — the ESP32 boot ROM cannot be erased).
- Firmware v2.0 talks at **921,600 baud**: the Serial Monitor must be set to 921600 to read `LAMBTON EJECTOR v2.0 READY … LEGACY PINS`. At 115200 only the chip's own ROM boot text (`rst:… boot:…`) is readable.
- Legacy profile keeps the latch on GPIO 12 (boot strapping pin): if the 595 board pulls it high at reset, upload/boot can fail → disconnect the ribbon cable while uploading.

---

## 15 Oct 2026 (session continued overnight into 16 Oct, unattended from Part 1 onwards)

### Phase status

| Phase | Status | Notes |
|---|---|---|
| Analysis (`ANALYSIS.md`) | **done** | hybrid PC + ESP32 recommended |
| Timing design (`TIMING_DESIGN.md`) | **done** | 35 mm not reachable with the PC-timed design |
| Camera probe / backup / restore tools | **done — waiting for hardware** | smoke-tested with 0 cameras |
| **Phase 1** ejector firmware v2.0, relay v2.0, binary protocol, 500 ms watchdog, remap + ESP32 test mode, safe pins, 50 ms fix, 1 ms timer, testers | **done — simulation tested only — waiting for hardware** | firmware compiles (0 warnings, cores 2.0.17 + 3.0.7) |
| **Phase 2** measurement: timing logs, latency report, 10 mm ball speed tool | **done — simulation tested only — waiting for hardware** | OFF by default (`timing_log_enabled`) |
| **Phase 3** line-synchronous firing | **prepared, DISABLED — simulation tested only — waiting for camera probe** | v3.0 firmware, `line_sync.py`, row tracking, calibration, nozzle 35–70 mm (55 default), per-recipe camera timing tools |
| **Phase 4** continuous dataset capture | **done — simulation tested only — waiting for hardware** | button OFF by default (`continuous_capture_button`) |
| **Phase 5** real-time CNN | **skipped** (user's decision) | needs real captured data (Phase 4) |
| **Phase 6** new-PC install | **done except the build run** | `requirements.txt`, `BUILD_STANDALONE_EXE_v2.bat` (**not run**: 7.4 GB free on C:), `INSTALL_NEW_PC.md` |
| `TOMORROW_CHECKLIST.md` + Tamil | **done** | |

### Git commits

| Hash | Meaning |
|---|---|
| `f705b02` | Baseline: original working state (Phase 0). Roll back everything: `git checkout f705b02 -- .` |
| `8c7645c` | Read-only camera probe/backup tool + restore tool |
| `aa90edb` | Phase 1 (binary protocol, watchdog both boards, remap/test mode, safe pins, 50 ms fix, 1 ms timer, testers) |
| `4aa362e` | Session log (first version) |
| `ebd03e1` | Phase 2: timing logs, latency report, ball speed tool, exactly-once object engine |
| `a50e8eb` | Phase 3 (prepared, disabled): firmware v3.0, line sync, row tracking, calibration, camera timing tools |
| `add4830` | Phase 4: continuous capture (engine, window, standalone tool, disk test) |
| `0cc1e28` | Phase 6: requirements, build v2 script, INSTALL_NEW_PC.md |
| `aa05f46` | Restore `ejector_config.json` (rewritten by the program's own start-up during my off-screen test) |
| `17e8153` | Tomorrow's checklist (English + Tamil) |
| *(this commit)* | Session log 15 Oct 2026 |

### Test results tonight (all on the development PC, no hardware)
| Test file | Result |
|---|---|
| `test_ejector_protocol.py` | PASS — CRC, 24-byte FIRE for 1 or 63 channels, parser with noise, legacy splitting |
| `test_ejector_link_sim.py` | PASS — simulated v2.0 ESP32: detect, fire, ACK, heartbeat, watchdog trip, re-arm, legacy fallback |
| `test_fusion_fix.py` | PASS — one defect = one 4 ms blast (original code gave 4 ms + 50 ms) |
| `test_object_stream.py` | PASS — 500 in / 500 out, 0 duplicates, exact sizes at frame heights 200/32/16 |
| `test_timing_sim.py` | PASS — report recovers injected delays; ESP32 clock wrap/drift handled; 20/20 commands matched to ACKs |
| `test_line_sync_sim.py` | PASS — **switches OFF: identical channels to the ORIGINAL code on 150 random frames**; row positions; calibration; D maths; dropped-frame line numbering; LATE not fired |
| `test_capture_sim.py` | PASS — **2 cameras × 500 objects at real speed: 500 + 500 crops, 0 duplicates, 0 dropped frames, 0 queue overflow** |
| `test_capture_ui_sim.py` | PASS — capture window: 300 in / 300 out, ejection off during capture |
| main window off-screen | starts with switches OFF (no new button, no line sync) and ON (button shown; line sync still OFF because `camera_trigger_confirmed` = false); safety banner works |
| `ball_speed.py --simulate` | mean 3.488 vs true 3.484 m/s |
| `disk_speed_test.py` | C: NVMe 176 MB/s but only 7.4 GB free; D: USB HDD 35 MB/s (too slow for strips) |

### Decisions made tonight (and why)

| Decision | Why |
|---|---|
| One switch file `features_config.json`, everything OFF; missing/bad file = all OFF | the machine runs exactly like Phase 1 if anything fails |
| Line sync needs THREE things: switch ON + `camera_trigger_confirmed` + firmware v3.0; otherwise falls back to Phase 1 firing | no accidental activation before the probe |
| Camera-changing code only in `tools/camera_apply_timing.py`: dry run by default, `--apply` saves a backup first | "camera changes only by button/script, after backup" |
| `camera_apply_timing --apply` also writes the new exposure into `camera_config.json` (with a backup copy) | the sorter re-applies `camera_config.json` exposure at every start; otherwise the change would be undone |
| v3.0 firmware: a late FIRE_LINE is **not fired**, only counted (tolerance 2 lines) | firing late hits good product |
| Line numbers from camera **timestamps** (not frame counting) | a dropped frame does not shift later line numbers |
| Firmware v3.0 = v2.0 + line clock, in a new folder; clock OFF by default | v2.0 and the old firmware stay untouched |
| Calibration = quadratic fit through a few wire positions | measuring all 63 valves is too slow; lens distortion is smooth |
| Background for capture/ball tool learned with the **feeder off** | the automatic fallback can lock onto product when > 50 % of the first frame is product (found in testing) |
| Noise filter applied on the joined strip, not per frame | per-frame filtering erased thin slivers at frame cuts (found in testing) |
| Crops: replicate-pad to square + 224 × 224, aspect corrected along the flow | identical to the CNN project's inference preparation; line-scan pixels are not square |
| Capture default output inside the project folder, with a disk guard (strips off < 3 GB, stop < 1 GB) | C: has only 7.4 GB free; never fill the system disk |
| Checklist: run the new version from a **new folder** on the machine PC (`D:\LAMTON_V2_TEST`); keep `D:\LAMTON_V1` | never overwrite the working program; instant rollback |
| Tomorrow uses `PIN_PROFILE 1` (old wiring) | no rewiring before the firmware is proven |
| Build not run tonight | 7.4 GB free on C:; torch build needs several GB |
| `D:\CNN\CNN` read only (only listings + 3 files read) | user rule |

(Phase 1 decisions — protocol, watchdog timing, 74HCT245, pins, 1 ms timer — are in the first log version, commit `4aa362e`, and in `PHASE1_GUIDE.md` section 6.)

### Open questions for the user
1. **Camera probe output** (`probe_report.txt`): model, frame height, line rate, exposure range, trigger modes, IO modes/formats.
2. **/OE pin** (74HC595 pin 13): tied to GND or connected to the ESP32?
3. **Which PC is the machine PC?** The development PC fails the licence lock (UUID `90001563-…` vs `03000200-…`).
4. Measured **product speed** (ball test) and **latency report** → 40 mm or 55 mm.
5. Machine PC disk: `disk_speed_test.py` result; size of the capture disk.
6. ESP32 USB chip types and Arduino ESP32 core version installed.
7. Results of T0–T8.
8. Is the camera frame taller than 200 lines? (The sorter only uses the middle 200 → lines lost; the capture window warns.)

### Known risks / what may break tomorrow
- **Nothing has run on real hardware yet.** Possible issues: 921,600 baud on the USB chip (fallback: lower `SERIAL_BAUD` and `BINARY_BAUD`); SPI/bit-bang on the existing cable; flash save of the remap table.
- **Development PC cannot run the sorter** (licence hard-lock). Tools work everywhere.
- If /OE is tied to GND, power-up protection does not work until rewired.
- The relay v2.0 refuses feeder/air ON after a trip until START: operators must know.
- `tools/ball_speed.py` and `tools/continuous_capture.py` use `CameraImageProcessEx` — not verified on the real camera.
- The latency report's transfer stage assumes a minimum of 1 ms (only the variation is measurable).
- Phase 3 timer interrupt load at 20 kHz, trigger driver wiring, camera trigger modes: untested.
- The program's start-up rewrites `ejector_config.json` without `gap`/`count` (original behaviour, harmless).
- Capture on a small/slow disk: strips switch off automatically; crops continue.
- C: on the development PC is almost full (7.4 GB).

### Exact first step tomorrow
Open **`TOMORROW_CHECKLIST.md`** (or `_TAMIL`) and start at **Step 0** (choose the machine PC, copy the new version into a new folder), then **Step 1: `python tools\camera_probe.py`** with the sorter closed, and send the `probe_report.txt`.

### Backup
The whole project folder (incl. `.git`) was copied to `D:\LAMTON_BACKUP_2026-10-15` (USB disk): 4831 files, 1.38 GB, 0 failed;
verified: git HEAD `e6a75eb`, clean, `git fsck` OK. Re-synced after the final summary commit. `D:\LAMTON_V1` was not touched.

### FINAL SUMMARY (16 Oct 2026, ~00:35)

**Ready (code written, committed, documented):**
- Phase 1: ejector firmware v2.0 + relay firmware v2.0 (new folders), binary protocol, 500 ms watchdog on both boards, remap + ESP32 test mode, safe pins + power-up protection, 50 ms long-blast fix, 1 ms Windows timer, both testers.
- Phase 2: timing logs (OFF), latency report with the 35/40/55/70 mm decision table, 10 mm ball speed tool.
- Phase 3 (DISABLED): firmware v3.0 line clock + FIRE at L + D + LATE counter, line-sync controller, row tracking, channel calibration + margin, nozzle distance 35–70 mm (55 default), per-recipe camera timing tools (dry run / backup first).
- Phase 4: continuous capture (button OFF), exactly-once crops in the CNN project's format, strips, live counters, disk guard, standalone tool, disk speed test.
- Phase 6: `requirements.txt`, `BUILD_STANDALONE_EXE_v2.bat`, `INSTALL_NEW_PC.md` with the licence explanation.
- `TOMORROW_CHECKLIST.md` + Tamil version; `CLAUDE.md`; this log; backup on D:.

**Tested only in simulation (no camera, no ESP32 connected):**
- all PC-side code: 8 test files pass; the main window starts with switches OFF and ON;
- all firmware only **compiled** (0 warnings on cores 2.0.17 and 3.0.7), never run on a board;
- with all switches OFF, detection is identical to the original code (150 random frames).

**Waiting for hardware:**
- camera probe (model, frame height, line rate, trigger support) → decides Phase 3 Plan A/B/C;
- /OE pin check; Phase 1 tests T0–T8 on the machine;
- real latency + ball speed → 40 mm or 55 mm;
- real capture with counted product; machine-PC disk speed;
- standalone .exe build (not run: low disk space here).

**Not started / skipped:** Phase 5 (real-time CNN) — skipped by the user, needs captured data.

---

## 31 Oct 2026 - CNN plan step 1: CPU timing comparison (tools/cnn_timing.py)

- New read-only tool `tools/cnn_timing.py` (PyTorch vs ONNX Runtime vs OpenCV DNN, one 224x224 MobileNetV2 crop, CPU). Nothing in the sorter or the ejection path changed.
- Result on this PC (10 threads, random weights, 100 runs): PyTorch 14.5 ms, **ONNX Runtime 2.8 ms (~5x faster)**, OpenCV DNN 8.5 ms. Recommendation: use ONNX Runtime for real-time CNN on the CPU-only machine PC; keep PyTorch for training. Machine-PC CPU will differ - re-run there.
- Needs `onnx` + `onnxruntime` (installed into this PC's Python 3.12.10; not yet in `deploy\requirements*.txt`).
- Next: capture with product + class selection (`dataset\<product>\<class>\`), training GUI, evaluation report, BAD threshold per recipe, ONNX export + recipe link.
