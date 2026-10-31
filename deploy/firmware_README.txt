=====================================================================
 LAMTON V2 - ESP32 FIRMWARE
=====================================================================
Arduino IDE settings for ALL boards:
  Board package : esp32 by Espressif Systems, version 3.3.12 (3.0.7 also compiles, 0 errors)
  Board         : ESP32 Dev Module          Upload Speed: 115200
  No extra libraries. Folder name and .ino name must stay the same (Arduino rule).
  If upload stays at "Connecting....": press and HOLD the BOOT button until "Writing at ..." appears.

EJECTOR board (63 valves, 74HC595 shift registers) ------------------------------------------
  ESP32_63CH_Ejector_v2_3\ESP32_63CH_Ejector_v2_3.ino            <- USE THIS (current)
      v2.3, 115200 baud, PIN_PROFILE 3 (default): DATA GPIO 23, CLOCK 18, LATCH 19, 74HC595 pin 13 (/OE) tied to
      GND (no OE pin), pin 10 (MR) and VCC to 5 V. Old wiring 14/13/12/19: change PIN_PROFILE to 1 (line 99).
      Behaviour: blue LED only on while a valve pulse is active (min 50 ms); any valid command arms the board
      (no ARM / RE-ARM); silent 500 ms watchdog (outputs OFF, nothing refused); IDENTIFY reply (device EJECTOR,
      firmware, pin profile, board ID) so the PC finds the right COM port by itself - ANY COM number.
      Serial Monitor 115200: "LAMBTON EJECTOR v2.3 READY | 115200 baud | pins: DATA 23 CLK 18 LATCH 19 OE -1 | PROFILE 3 ..."
      NOT HARDWARE-TESTED yet (simulation + compile only).
      Power-up warning (profile 3, /OE to GND): outputs are undefined until zeros are shifted (~0.3 s after boot):
      keep valve air / 24 V off while powering up.
  ROLLBACK_OLD\ESP32_63CH_Ejector_v2_2 / _v2_1 / _v2_0   earlier v2.x (921600 baud, TRIPPED + RE-ARM). v2.1 = PIN_PROFILE 1
      (old wiring 14/13/12/19, old-style shiftOut), v2.2 = PIN_PROFILE 3. Found by the PC at 921600 and reported as OLDER.
  ROLLBACK_OLD\ESP32_63CH_ShiftRegister\ESP32_63CH_ShiftRegister.ino
      the original working firmware (text protocol, 115200 baud, no watchdog).
      The sorter detects it automatically (legacy mode) - told apart from v2.3 by protocol, not by baud rate.

RELAY board (8 relays, Modbus RTU 9600: 1 feeder, 2 main air, 3 wiper, 4-8 lights) ---------
  Does the relay board NEED v2.0 for this program?   NO.
      The sorter reads the relay firmware version (Modbus register 11). With the OLD firmware it
      prints "Old relay firmware (no watchdog)" and works exactly as before.
  v2.0 is RECOMMENDED for safety (not required): if the PC freezes or the USB cable is pulled,
      v2.0 switches feeder, main air and wiper OFF within 0.5 s (lights stay).
      NOTE: relay v2.0 has only been COMPILED - it has never run on a real board yet.
      Recommended order: first run the new PC with the OLD relay firmware (proven), then upload
      v2.0 and do test T6 (PHASE1_GUIDE.md in app\): START -> pull the relay USB cable ->
      feeder/air/wiper must switch OFF within 0.5 s. After a trip, press START to re-arm.
  ESP32_8CH_ModbusRelay_v2_0\ESP32_8CH_ModbusRelay_v2_0.ino       (optional, recommended later)
      same relay pins (16,17,18,19,32,33,25,26) and same relay logic as the old firmware.
  ROLLBACK_OLD\ESP32_8CH_ModbusRelay_NEW\ESP32_8CH_ModbusRelay_NEW.ino
      the original working relay firmware.

Upload steps (any board)
  1. Close the sorter (it holds the COM ports). Note the board's COM port (Device Manager).
  2. Arduino IDE: File -> Open the .ino, Tools -> Board / Port / Upload Speed as above, Upload.
  3. Success = "Hash of data verified". Then SYSTEM_CHECK.bat shows the firmware version.
=====================================================================
