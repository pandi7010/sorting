=====================================================================
 LAMTON 63-சேனல் EJECTOR TEST KIT                       16 அக் 2026
=====================================================================
எந்த Windows கணினியிலும் EJECTOR ESP32-ஐ flash செய்து சோதிக்க
தேவையான அனைத்தும் இந்த folder-இல் உள்ளன.
E1-E12 சோதனைகளுக்கு camera, sorter program, 24 V எதுவும் தேவையில்லை.

உள்ளடக்கம்
  RUN_EJECTOR_TEST_PANEL.bat      test panel-ஐ தொடங்கும் (Python-ஐ தானே கண்டுபிடிக்கும்)
  ejector_test_panel.py           test panel (window)
  ejector_link.py                 ESP32 உடன் தொடர்பு (v2.x binary protocol, 115200 baud)
  tools\ejector_sim.py            பயிற்சிக்கான போலி ESP32 (--sim)
  requirements_test_panel.txt     Python packages (சரியான versions)
  firmware\ESP32_63CH_Ejector_v2_3\ESP32_63CH_Ejector_v2_3.ino
                                  புதிய firmware v2.1, PIN_PROFILE 1 (பழைய wiring:
                                  GPIO 14 data, 13 clock, 12 latch, 19 OE).
                                  16 அக் 2026 அன்று upload செய்த அதே file.
  firmware\ESP32_63CH_ShiftRegister\ESP32_63CH_ShiftRegister.ino
                                  பழைய firmware = ROLLBACK (திரும்பப் போக)
                                  (flash backup எதுவும் இல்லை)

---------------------------------------------------------------------
1. USB DRIVER (CP210x)
---------------------------------------------------------------------
ESP32 board-இல் CP2102 USB chip உள்ளது.
  - ESP32-ஐ இணைக்கவும். Device Manager திறக்கவும் (Win + X -> Device Manager).
  - சரி:   "Ports (COM & LPT)"-இல் "Silicon Labs CP210x USB to UART Bridge (COMxx)" தெரியும்.
  - தவறு:  "Other devices"-இல் "CP2102 USB to UART Bridge Controller" மஞ்சள் "!" உடன் தெரியும்.
    தீர்வு: அதன் மேல் right-click -> Update driver -> Search automatically.
    அல்லது silabs.com-இல் "CP210x Universal Windows Driver" download செய்து,
    zip-ஐ EXTRACT செய்து, silabser.inf மேல் right-click -> Install.
    பிறகு ESP32-ஐ கழற்றி மீண்டும் இணைக்கவும்.
  (சில board-களில் CH340 chip இருக்கும் - அப்போது CH340 driver நிறுவவும்.)

---------------------------------------------------------------------
2. ARDUINO IDE (firmware upload செய்ய மட்டும்)
---------------------------------------------------------------------
  Board package : Tools -> Board -> Boards Manager -> "esp32 by Espressif Systems"
                  version 3.3.12 (16 அக் 2026 upload-க்கு பயன்படுத்தியது;
                  3.0.7-இலும் 0 errors உடன் compile ஆகும்). வேறு library தேவையில்லை.
                  கவனம்: இதற்கு C:-இல் சுமார் 9 GB இடம் தேவை - பிறகு குறைந்தது 5 GB காலியாக வைக்கவும்.
  Board         : Tools -> Board -> esp32 -> ESP32 Dev Module
  Port          : Tools -> Port -> COMxx (படி 1-இல் உள்ள Silicon Labs port)
  Upload Speed  : 115200
  திறக்கவும்    : firmware\ESP32_63CH_Ejector_v2_3\ESP32_63CH_Ejector_v2_3.ino
  Upload        : Upload (->) அழுத்தவும்.
                  "Connecting....." என்றே நின்றால்: BOOT button-ஐ அழுத்திப் பிடித்துக்
                  கொள்ளுங்கள்; "Writing at ... (x %)" வந்ததும் விடுங்கள்.
                  வெற்றி = "Hash of data verified".
  முக்கியம்     : upload செய்யும் போது 74HC595 board-இன் ribbon cable-ஐ கழற்றவும்
                  (GPIO 12 ஒரு boot pin; board இணைந்திருந்தால் upload நின்றுவிடலாம்).
  Serial Monitor: 115200 baud (ejector v2.3-க்கும் பழைய firmware-க்கும்).
                  panel-ஐ தொடங்கும் முன் Serial Monitor-ஐ மூடவும்.

---------------------------------------------------------------------
3. TEST PANEL-ஐ தொடங்குதல்
---------------------------------------------------------------------
  1. இதே கணினியில் SORTER PROGRAM ஓடிக்கொண்டு அதே COM port-ஐ பயன்படுத்தினால்
     அதை மூடவும்; Arduino Serial Monitor-ஐயும் மூடவும்.
     ஒரு COM port-ஐ ஒரே நேரத்தில் ஒரு program மட்டுமே பயன்படுத்த முடியும்.
  2. RUN_EJECTOR_TEST_PANEL.bat மேல் double-click செய்யவும்.
       - அது Python-ஐ தானே கண்டுபிடித்து PyQt5 + pyserial உள்ளதா என சோதிக்கும்.
       - "Python was not found": python.org-இலிருந்து Python 3.10+ நிறுவவும்,
         "Add python.exe to PATH" டிக் செய்யவும், .bat-ஐ மீண்டும் இயக்கவும்.
       - "PyQt5 and/or pyserial are not installed": அது காட்டும் pip கட்டளையை இயக்கவும்,
         உதா:   python -m pip install -r requirements_test_panel.txt
  3. ESP32 இல்லாமல் பயிற்சி:   RUN_EJECTOR_TEST_PANEL.bat --sim
     ஒரு port-ஐ முன்தேர்வு:     RUN_EJECTOR_TEST_PANEL.bat --port PORTNAME

  COM PORT தேர்வு
  - Panel-இல்: COM Port பட்டியல் -> "COMxx - Silicon Labs CP210x ..." தேர்வு செய்யவும்
    (panel ஒரு USB port-ஐ தானே தேர்வு செய்யும்). இணைத்த பிறகு "Refresh" அழுத்தவும்.
  - எது என்று தெரியவில்லையா? ESP32-ஐ கழற்றி Refresh, மீண்டும் இணைத்து Refresh:
    புதிதாக வரும் port தான் ESP32.
  - CONNECT அழுத்தவும் (5 வினாடி வரை). Badge "v2.1 BINARY . ARMED" (பச்சை) காட்ட வேண்டும்.
    "OLD FIRMWARE . TEXT MODE" = board-இல் இன்னும் பழைய firmware (வேலை செய்யும், ஆனால்
    watchdog/counters/progress இல்லை).
    "No ejector firmware answered" = தவறான port, port வேறு program-இல் திறந்துள்ளது
    (sorter / Serial Monitor), அல்லது firmware இல்லை.

  பாதுகாப்பு: STOP ALL (பெரிய சிவப்பு button) அல்லது Esc key எல்லாவற்றையும் உடனே நிறுத்தும்.
  Disconnect, window மூடுதல், அல்லது எந்த error வந்தாலும் எல்லா valve-களும் OFF ஆகும்.

---------------------------------------------------------------------
4. ESP32 மட்டும் சோதனைகள் E1 - E12   (USB மட்டும், 24 V இல்லை, ribbon cable கழற்றப்பட்டது)
---------------------------------------------------------------------
ESP32-இல் உள்ள நீல LED நடப்பதைக் காட்டும்:
  எரியும் = valve fire ஆகிறது (குறைந்தது 50 ms) | அணைந்திருக்கும் = idle (மினுங்குவதே இல்லை)
  ARM / RE-ARM இல்லை: ஒவ்வொரு click-உம் board-ஐ தானே arm செய்யும்.
ஒவ்வொரு சோதனைக்குப் பிறகும் PASS/FAIL எழுதவும்.

E1  Driver + port     இணைக்கவும். Device Manager-இல் "Silicon Labs CP210x (COMxx)".
                      COMxx-ஐ குறித்துக்கொள்ளவும்.                         [    ]
E2  Upload v2.3       Arduino IDE, படி 2 அமைப்புகள், upload.
                      "Hash of data verified".                             [    ]
E3  Boot செய்தி       Serial Monitor 115200, ESP32-இல் EN (reset) அழுத்தவும்:
                      LAMBTON EJECTOR v2.3 READY | 115200 baud | pins: DATA 23
                      CLK 18 LATCH 19 OE -1 | PROFILE 3 (guide wiring 23/18/19, /OE tied to GND, shiftOut)
                      PING என type செய்யவும் -> ACK:PING PONG:LAMTON_EJECTOR_V2.0
                      (முன்னால் சில விசித்திர எழுத்துகள் வருவது சாதாரணம்).
                      Serial Monitor-ஐ மூடவும்.                            [    ]
E4  Connect           Panel-ஐ தொடங்கி, COMxx தேர்வு, CONNECT.
                      Badge "v2.3 BINARY . ARMED" பச்சை (LED அணைந்திருக்கும்),
                      counters crc_err 0  len_err 0, "heartbeat OK".       [    ]
E5  ஒரு pulse         Pulse 200 ms. Channel 1-ஐ click செய்யவும்.
                      LED 0.2 வினாடி எரியும், button 1 பச்சை, "fired" +1,
                      log: CH 1 -> OUT 0 -> CHIP 1, Q0                      [    ]
E6  Single test       Channel 1, Repeat 5, Gap 300, START SINGLE.
                      5 மினுக்கங்கள், bar 1/5 ... 5/5 பிறகு DONE, fired +5. [    ]
E7  Auto sequence     Pulse 10, Gap 50, From 1 To 63, START AUTO.
                      பச்சை ஒளி 1 -> 63 நகரும் (சுமார் 4 வினாடி), bar CH x (x/63),
                      DONE, fired +63. Log: CH n -> OUT n-1 -> CHIP, Q.    [    ]
E8  Loop + STOP       From 1 To 5, "Loop continuously" டிக், START AUTO.
                      Bar "loop 2, 3 ..." காட்டும். STOP ALL அழுத்தவும்: உடனே நிற்கும்.
                      மீண்டும் தொடங்கி Esc அழுத்தவும்: உடனே நிற்கும்.     [    ]
E9  FIRE ALL          Pulse 120. FIRE ALL -> 50 ms-க்கு குறைக்கப்படும் என்று செய்தி.
                      No சொன்னால்: எதுவும் fire ஆகாது. மீண்டும், Yes:
                      63 button-களும் மினுங்கும், fired +63.               [    ]
E10 Silent watchdog  இணைப்பில் இருக்கும்போது: Task Manager -> "python" (panel) -ஐ End task = PC உறைந்த நிலை.
                      0.5 வினாடிக்குள் ESP32 எல்லா output-களையும் தானே OFF செய்யும் (அமைதியாக:
                      LED அணைந்தே இருக்கும்). Panel-ஐ மீண்டும் தொடங்கவும்: முதல் click உடனே
                      fire ஆகும் (RE-ARM வேண்டாம்). "watchdog trips" 1 அதிகம்.
                                                                           [    ]
E11 USB துண்டிப்பு    இணைப்பில் இருக்கும்போது USB cable-ஐ கழற்றவும்.
                      Log: "Connection to the ESP32 LOST", button-கள் சாம்பல் நிறம்.
                      மீண்டும் இணைத்து, Refresh, CONNECT: மீண்டும் வேலை செய்யும். [    ]
E12 Remap + மூடுதல்   "CHANNEL REMAP" டிக். Read map -> "all channels default".
                      CH 1-ஐ CH 2-உடன் Swap, channel 1-ஐ click ->
                      log "CH 1 -> OUT 1 -> CHIP 1, Q1". Restore default ->
                      "all channels default". (மாற்றம் power-off பிறகும் இருக்க வேண்டும்
                      என்றால் மட்டுமே "Save to ESP32" அழுத்தவும்.)
                      Window-ஐ மூடவும்: valve-கள் OFF,
                      board சுத்தமாக disarm.                                    [    ]

விருப்பம் - 74HC595 board இணைத்து (ESP32 தொடங்கிய பிறகு ribbon-ஐ இணைக்கவும்):
E5/E7 ஒவ்வொரு output-இன் LED-ஐயும் எரிய வைக்கும்: channel n = chip (n-1)/8+1,
pin Q(n-1)%8. Chip 1 என்பது ESP32-க்கு அருகில் உள்ளது.

---------------------------------------------------------------------
5. பழைய FIRMWARE-க்கு திரும்புதல் (ROLLBACK)
---------------------------------------------------------------------
  Arduino IDE, அதே அமைப்புகள்,
  firmware\ESP32_63CH_ShiftRegister\ESP32_63CH_ShiftRegister.ino திறந்து upload செய்யவும்.
  பழைய firmware-க்கு Serial Monitor: 115200 baud.
  Sorter-உம் இந்த panel-உம் பழைய firmware-ஐ தானே கண்டுபிடிக்கும் (TEXT mode).
=====================================================================
