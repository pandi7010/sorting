=====================================================================
 LAMTON 63-CHANNEL EJECTOR TEST KIT                     16 Oct 2026
=====================================================================
Everything needed to flash and test the EJECTOR ESP32 on any Windows PC.
No camera, no sorter program, no 24 V needed for tests E1-E12.

CONTENTS
  RUN_EJECTOR_TEST_PANEL.bat      start the test panel (finds Python itself)
  ejector_test_panel.py           the test panel (window)
  ejector_link.py                 communication with the ESP32 (v2.x binary protocol, 115200 baud)
  tools\ejector_sim.py            simulated ESP32 for practice (--sim)
  requirements_test_panel.txt     Python packages with exact versions
  firmware\ESP32_63CH_Ejector_v2_3\ESP32_63CH_Ejector_v2_3.ino
                                  firmware v2.3, 115200 baud. PIN_PROFILE 3 (default) = DATA GPIO 23, CLOCK 18,
                                  LATCH 19, 74HC595 /OE tied to GND (no OE pin). For the old wiring
                                  14/13/12/19 set PIN_PROFILE 1 in line 99 before uploading.
  firmware\ESP32_63CH_ShiftRegister\ESP32_63CH_ShiftRegister.ino
                                  OLD firmware = ROLLBACK (no flash backup exists)

---------------------------------------------------------------------
1. USB DRIVER (CP210x)
---------------------------------------------------------------------
The ESP32 board uses a CP2102 USB chip.
  - Plug in the ESP32. Open Device Manager (Win + X -> Device Manager).
  - OK:      "Ports (COM & LPT)" shows  "Silicon Labs CP210x USB to UART Bridge (COMxx)".
  - Not OK:  "Other devices" shows "CP2102 USB to UART Bridge Controller" with a yellow "!".
    Fix: right-click it -> Update driver -> Search automatically (Windows Update).
    Or download "CP210x Universal Windows Driver" from silabs.com, EXTRACT the zip,
    right-click silabser.inf -> Install, then unplug and re-plug the ESP32.
  (Some boards use a CH340 chip instead - then install the CH340 driver.)

---------------------------------------------------------------------
2. ARDUINO IDE (only to upload firmware)
---------------------------------------------------------------------
  Board package : Tools -> Board -> Boards Manager -> "esp32 by Espressif Systems"
                  version 3.3.12 (used for the upload on 16 Oct 2026;
                  3.0.7 also compiles with 0 errors). No extra libraries needed.
                  Note: it needs about 9 GB on C: - keep at least 5 GB free afterwards.
  Board         : Tools -> Board -> esp32 -> ESP32 Dev Module
  Port          : Tools -> Port -> COMxx (the Silicon Labs port from step 1)
  Upload Speed  : 115200
  Open          : firmware\ESP32_63CH_Ejector_v2_3\ESP32_63CH_Ejector_v2_3.ino
  Upload        : click Upload (->).
                  If it stays at "Connecting.....": press and HOLD the BOOT button
                  until "Writing at ... (x %)" appears, then release.
                  Success = "Hash of data verified".
  IMPORTANT     : unplug the ribbon cable to the 74HC595 board while uploading
                  (GPIO 12 is a boot pin; the board can block the upload).
  Serial Monitor: 115200 baud (ejector v2.3 and the OLD firmware).
                  CLOSE the Serial Monitor before starting the panel.

---------------------------------------------------------------------
3. START THE TEST PANEL
---------------------------------------------------------------------
  1. Close the SORTER PROGRAM if it is running on this PC and uses the same COM
     port, and close the Arduino Serial Monitor. Only ONE program can use a COM port.
  2. Double-click RUN_EJECTOR_TEST_PANEL.bat
       - It finds Python by itself and checks PyQt5 + pyserial.
       - "Python was not found": install Python 3.10+ from python.org,
         tick "Add python.exe to PATH", run the .bat again.
       - "PyQt5 and/or pyserial are not installed": run the pip command it shows,
         e.g.   python -m pip install -r requirements_test_panel.txt
  3. Practice without an ESP32:  RUN_EJECTOR_TEST_PANEL.bat --sim
     Pre-select a port:          RUN_EJECTOR_TEST_PANEL.bat --port PORTNAME

  CHOOSING THE COM PORT
  - AUTO DETECT: the panel asks every COM port who it is when it starts (button "Auto detect"):
    the ejector is found on ANY port number. Otherwise pick it in the list:
  - In the panel: COM Port list -> pick "COMxx - Silicon Labs CP210x ..."
    (the panel pre-selects a USB port automatically). Press "Refresh" after plugging in.
  - Not sure which one? Unplug the ESP32, press Refresh, plug in again, press Refresh:
    the port that appears is the ESP32.
  - Press CONNECT. Up to 5 seconds. Badge must show:  "v2.1 BINARY . ARMED" (green).
    "OLD FIRMWARE . TEXT MODE" = old firmware is still on the board (works, but
    no watchdog/counters/progress).
    "No ejector firmware answered" = wrong port, port busy (sorter / Serial Monitor
    open), or no firmware.

  SAFETY: STOP ALL (big red button) or the Esc key stops everything at once.
  All valves go OFF when you disconnect, close the window, or on any error.

---------------------------------------------------------------------
4. ESP32-ONLY TESTS E1 - E12   (USB only, NO 24 V, ribbon cable unplugged)
---------------------------------------------------------------------
The blue LED on the ESP32 shows what happens:
  ON = a valve is firing (at least 50 ms, so short pulses are visible) | OFF = idle (it never blinks)
  There is no ARM / RE-ARM: any click arms the board by itself.
Write PASS/FAIL after each test.

E1  Driver + port     Plug in. Device Manager shows "Silicon Labs CP210x (COMxx)".
                      Note COMxx.                                          [    ]
E2  Upload v2.3       Arduino IDE, settings of step 2, upload.
                      "Hash of data verified".                             [    ]
E3  Boot message      Serial Monitor 115200, press EN (reset) on the ESP32:
                      LAMBTON EJECTOR v2.3 READY | 115200 baud | pins: DATA 23
                      CLK 18 LATCH 19 OE -1 | PROFILE 3 (guide wiring 23/18/19, /OE tied to GND, shiftOut)
                      Type PING -> ACK:PING PONG:LAMTON_EJECTOR_V2.0
                      (some strange characters before it are normal).
                      CLOSE the Serial Monitor.                            [    ]
E4  Connect           Start the panel, choose COMxx, CONNECT.
                      Badge "v2.3 BINARY . ARMED" green (LED off),
                      counters crc_err 0  len_err 0, "heartbeat OK".       [    ]
E5  One pulse         Pulse 200 ms. Click channel 1.
                      LED on for 0.2 s, button 1 green, "fired" +1,
                      log: CH 1 -> OUT 0 -> CHIP 1, Q0                      [    ]
E6  Single test       Channel 1, Repeat 5, Gap 300, START SINGLE.
                      5 flashes, bar 1/5 ... 5/5 then DONE, fired +5.      [    ]
E7  Auto sequence     Pulse 10, Gap 50, From 1 To 63, START AUTO.
                      Green light walks 1 -> 63 (about 4 s), bar CH x (x/63),
                      DONE, fired +63. Log: CH n -> OUT n-1 -> CHIP, Q.    [    ]
E8  Loop + STOP       From 1 To 5, tick "Loop continuously", START AUTO.
                      Bar shows "loop 2, 3 ...". Press STOP ALL: stops at once.
                      Start again, press Esc: stops at once.               [    ]
E9  FIRE ALL          Pulse 120. FIRE ALL -> message says limited to 50 ms.
                      Answer No: nothing fires. Again, answer Yes:
                      all 63 buttons flash, fired +63.                     [    ]
E10 Silent watchdog   While connected: Task Manager -> end "python" (the panel) = a frozen PC.
                      Within 0.5 s the ESP32 switches all outputs OFF by itself (nothing to see:
                      it is silent, the LED stays off). Start the panel again: the first click
                      fires AT ONCE (no RE-ARM). "watchdog trips" is 1 higher.
                                                                           [    ]
E11 USB lost          While connected, unplug the USB cable.
                      Log: "Connection to the ESP32 LOST", buttons grey.
                      Plug in, Refresh, CONNECT: works again.             [    ]
E12 Remap + close     Tick "CHANNEL REMAP". Read map -> "all channels default".
                      Swap CH 1 with CH 2, click channel 1 ->
                      log "CH 1 -> OUT 1 -> CHIP 1, Q1". Restore default ->
                      "all channels default". (Do NOT press "Save to ESP32" unless
                      you want to keep a change after power-off.)
                      Close the window: valves OFF,
                      board disarmed cleanly.                                   [    ]

Optional, with the 74HC595 board connected (plug the ribbon in AFTER the ESP32
has started): E5/E7 also light the LED of each output: channel n = chip
(n-1)/8+1, pin Q(n-1)%8. Chip 1 is nearest the ESP32.

---------------------------------------------------------------------
5. ROLLBACK TO THE OLD FIRMWARE
---------------------------------------------------------------------
  Arduino IDE, same settings, open
  firmware\ESP32_63CH_ShiftRegister\ESP32_63CH_ShiftRegister.ino and upload.
  Serial Monitor for the old firmware: 115200 baud.
  The sorter and this panel detect the old firmware automatically (TEXT mode).
=====================================================================
