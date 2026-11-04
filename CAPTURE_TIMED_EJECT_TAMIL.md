# Capture-timed ejection: eppadi ON pannradhu, eppadi DELAY set pannradhu

*2 Nov 2026. Switch: `features_config.json` → `"capture_timed_eject_enabled"` (default **false**). ESP_BYTE firmware (`esp/esp.ino`) ku mattum.*

## Pazhaya muraiyila enna problem

Camera defect paatha udane valve ON, adutha frame la illa na OFF. Recipe la irukkura **DELAY, PULSE use aagave illa**. Valve eppo open aagudhu nu PC evvalavu busy ah irukku nu dhaan decide pannudhu. Test la ore defect ku ovvoru run la −4 ms, −9 ms, −11 ms, −17 ms nu maarudhu (3.5 m/s la 14–60 mm thappu).

## Pudhu muraiyila

1. Camera-oda own clock (0.1 ms) vechu ovvoru line **eppo scan aachu** nu kandupidikkudhu. PC/network delay spike edhuvum adha maathaadhu.
2. Defect frame-la **endha row** la irukku nu paakkudhu.
3. Valve ON = andha row scan aana time **+ recipe DELAY**.
4. Valve open ah irukkura time = grain neelam (lines) **+ recipe PULSE**.
5. PC romba late aagi window mudinjiduchu na blast **drop** pannum (MISSED nu count aagum). Thappaana grain ah adikkaadhu.

Test result: ON time error **0.06 ms** (unga PC la, simulation).

## ⚠️ ON pannradhukku munnaadi

**Recipe DELAY oda artham maarudhu.** Ippo DELAY = "camera line la irundhu nozzle varaikkum grain poga evvalavu nerum".

Kanakku: `DELAY (ms) ≈ nozzle distance (mm) ÷ speed (m/s) − 1 ms (valve response)`

| Nozzle distance | 3.5 m/s la DELAY |
|---|---|
| 38 mm (ippo) | ~10 ms |
| 60 mm | ~16 ms |
| 80 mm | ~22 ms |
| 100 mm | ~28 ms |

**PC ku minimum ~14–17 ms venum** (64-line frame 7.2 ms + transfer + processing + USB). Appo **ippo irukkura 38 mm la (DELAY ~10 ms) neraiya blasts MISSED aagum.** Adhanaala:

- Nozzle ah **70–80 mm ku move pannina apram** dhaan idha ON pannunga, **illa**
- 38 mm laye test panna, console la LATE/MISSED count ah paarunga. Adhu ungalukku "PC evvalavu late" nu exact ah sollum.

## ON pannradhu eppadi

1. Sorter ah close pannunga.
2. `features_config.json` la `"capture_timed_eject_enabled": true` nu maathunga.
3. Recipe la **DELAY** ah mela table padi set pannunga. **PULSE** 2–4 ms la start pannunga.
4. Sorter start pannunga. Console la ippadi varum:
   ```
   [TIMED EJECT] capture-timed ejection ACTIVE (recipe DELAY = camera line -> nozzle)
   [TIMED EJECT] blasts 120 | on time 118 | LATE 2 | MISSED 0 | merged 5 | worst late 1.3 ms
   ```
5. **MISSED > 0** na: DELAY romba kammi (illa PC slow). Nozzle ah keezha move pannunga, illa frame height kammi pannunga.
6. Pazhaya maadhiri vara: `false` nu maathi restart. Vera edhuvum maaradhu.

## DELAY fine-tune (test object vechu)

1. Oru karuppu bead / dyed rice 30 thadavai podunga.
2. Bead reject bin la vizhalai na: DELAY ah **+1 ms** maathi thirumba try. Innum mosam aanaa **−1 ms** side try.
3. Ellaa bead um reject aagura range kandupidinga (udhaa 18–22 ms). Adhoda **naduvula** irukkura value ah final ah vechukonga.

## Extra settings: `capture_timing_config.json`

| Setting | Default | Artham |
|---|---|---|
| `rear_extra_delay_ms` | 0 | REAR camera line FRONT line ah vida mela/keezha irundhaa, REAR ku extra delay |
| `late_tolerance_ms` | 1.0 | Ivvalavu late varaikkum "on time" nu edukkum |
| `max_pulse_ms` | 200 | Oru blast-oda maximum neelam (esp.ino watchdog 250 ms la anyway off pannum) |
| `stats_print_s` | 5 | Ovvoru 5 sec ku counter print. 0 = print illa |
| `default_line_rate_hz` | 8842 | Camera timestamp illaatti use aagum (probe la measure pannadhu) |
| `gil_switch_interval_ms` | 0.5 | Python thread switch. Idhu illaatti valve 5 ms varaikkum late aagalaam |

## Innum real hardware la test pannanum

- Simulation la mattum dhaan test aagirukku (camera, ESP32 illaama).
- Camera timestamp unit 0.1 ms nu probe la confirm aagirukku. Real stream la `worst late` evvalavu varudhu nu paakkanum.
- Windows la timing Linux test ah vida konjam mosamaa irukkalaam. `LATE` counter dhaan idha sollum.

## Tests

```
python tools\test_capture_timing_sim.py
python tools\test_capture_timed_eject_sim.py
```
Rendum **ALL TESTS PASSED** nu print pannanum.

---

# Innoru switch: `detection_fixes_enabled` (default false)

Idhu timing illa, **defect kandupidikkira logic** ku. ON pannaa:

| Pazhaya bug | ON pannaa |
|---|---|
| Oru dust / hot pixel kooda valve ah open pannum | Oru channel la **≥ 4 defect pixels** (`min_defect_area_px`) irundhaa dhaan |
| GRAY la GREEN select pannaalum, scene red-dark ah irundhaa silent ah BLUE ku maarum | Neenga select panna colour dhaan |
| GRAY background = ovvoru column-oda darkest pixel average. Product adhigam irundhaa background keezha poyi **defect miss aagum** | Background = median (product kammiyaa irukkura normal case la correct) |
| SIZE: camera threshold illa na threshold 0, so SIZE eppovume fire aagadhu | Default threshold 68 |

Test: `python tools\test_detection_fixes_sim.py` ALL TESTS PASSED nu varanum. (OFF la ovvoru pazhaya bug um varudhu, ON la fix aagudhu.)

**Eppadi try pannradhu:** `features_config.json` la `"detection_fixes_enabled": true`, restart. Reject bin la nalla grain kammiyaa vizhudha, kettadhu miss aagudha nu paarunga. Chinna defects (3 mm seeds) miss aanaa `min_defect_area_px` ah 4 la irundhu 2 ku kammi pannunga.

---

# Moonaavadhu switch: `clean_channel_switch_enabled` (default false)

**Problem:** object ch1 la irundhu ch2 ku move aanaalum, ch1 la **1 pixel** mattum irundhaa kooda main sorting path ch1 valve ah ON laye vechudhu (simulation la 3 frames extra). Background Graph window la 2 pixel filter irundhadhaala adhu sariyaa switch aachu.

**ON pannaa:**
- Oru channel ku **minimum 2 pixel columns** venum (`channel_min_px`), Background Graph maadhiriye.
- Pazhaya channel **OFF** um pudhu channel **ON** um **ore message** la pogum, OFF mudhalla.

**Innum oru kaaranam (calibration):** FRONT and REAR camera rendum ore needle position ku ore channel sollanum. REAR-oda START/END pixel FRONT oda match aagaatti, REAR innum "ch1" nu sollum varaikkum ch1 ON laye nikkum. Check panra murai: needle ah oru channel boundary la vechu, Background Graph la FRONT view um REAR view um ore channel number kaattudha nu paarunga. Illa na REAR START/END ah nudge pannunga.

Test: `python tools\test_channel_switch_sim.py`

---

# "Ellaam adikkanum" test ku: 3 pudhu switches (default false)

**Netru silathu mattum ON aanadhukku kaaranam:** RICE program la `SIZE: BIG` irundhuchu. Adhu **dark** objects ah mattum dhaan paakkum, adhuvum oru channel kulla ~3.8 mm akalam irundhaa mattum. Oru rice grain ~2.5 mm, so grains overlap aana idathula mattum ON aachu. HSV default box orange/yellow ah mattum pidikkum, vellai rice ah illa.

**1. `background_graph_algo_enabled`** → Program Manager la **BACKGROUND (GRAPH)** nu pudhu algorithm varum.
- Background Graph window la set panna **lower / upper / band** values ah dhaan use pannum (FRONT, REAR thani thani).
- **Graph la light aagura ellaam sorting la um adikkum.** "Ellaam adikkanum" test ku idhu dhaan correct.
- Settings dialog illa, click panninaa select aagidum.

**2. `runtime_sliders_enabled`** → main screen **RUNTIME TUNING** sliders ippo nijamaave velai seiyum.

| Slider | Enna maathum |
|---|---|
| HSV SENSITIVITY | HSV / DISCOLOUR sensitivity |
| GRAY A: CONTRAST | GRAY A sensitivity |
| GRAY B: THRESHOLD | GRAY B ku absolute threshold (0–255) |
| SIZE (PIXELS) | SIZE: BIG: **full object width** pixels la (1 mm ≈ 12 px). Channels thaandi alakkum, bright um dark um |
| AI THRESHOLD | AI confidence |

Switch ON la sliders, algorithm settings ah override pannum. AI PURITY, CHROME RATIO innum edhuvum pannaadhu.

**3. `hsv_box_fix_enabled`** → HSV colour map la neenga box podra idam dhaan save aagum (pazhaya code ~35 px shift aachu).

**Test procedure:**
1. `features_config.json` la `"background_graph_algo_enabled": true`, restart.
2. Program Manager → RICE → matha algorithms ah remove pannunga, **BACKGROUND (GRAPH)** mattum select pannunga, save.
3. Feeder ah **kammiyaana speed** la ON pannunga (air pressure ku). Rice vizhura ellaa channels um ON aaganum, kaali channels off ah irukkanum.

Test: `python tools\test_algo_fixes_sim.py`

# Program Settings cross-check (2 Nov 2026): `program_settings_fix_enabled` (default false)

Program Settings la set panra ovvoru algorithm um real sorting code la check panniyaachu (`python tools\test_program_settings_audit.py`).

| Algorithm | Ippo enna nadakkudhu |
|---|---|
| BACKGROUND (GRAPH) | Correct. Kaali chute = 0, rice irukkura channels mattum |
| HSV SORT OUT | Correct (box colour mattum adikkum) |
| **HSV SORT IN** | **Thappu**: kaali chute la kooda 63 um adikkum. Switch ON pannaa sari aagum |
| **DISCOLOUR A/B** | **Thappu**: Color Combo (RED > GREEN...) ah sorter padikkave illa. Switch ON pannaa sari aagum |
| GRAY DARK | LO 0 la black grain theriyaadhu. `detection_fixes_enabled` ON pannaa theriyum |
| SIZE BIG/SHORT | White rice ah paakkaadhu. `runtime_sliders_enabled` venum |
| AI DETECTION | Machine PC la torch work aagala, so edhuvum pannaadhu |

Example: HSV SORT IN = "indha colour nalladhu, matha ellaam reject". Pazhaya code background um "matha colour" nu nenachu 63 valve um open pannum. Switch ON la, **product pixel** (graph LO/HI thaandura pixel) mattum dhaan paakkum.

ON panna: `features_config.json` la `"program_settings_fix_enabled": true`, restart.

# DELAY / DWELL (2 Nov 2026)

- **PULSE ippo DWELL nu kaattum.** DWELL = valve evvalavu neram open ah irukkum. EJECTORS dialog, Background Graph window (pazhaya BLOW), Algorithm dialog firing test: moonu idathulayum DWELL. (`recipes.json` la key innum `"pulse"` dhaan, pazhaya recipes apdiye work aagum.)
- Program Settings → EJECTORS → DELAY / DWELL → SAVE: sorting **restart illaama 0.5 s kulla** use aagum.
- Aana **`capture_timed_eject_enabled: true` irundhaa mattum dhaan**. OFF la sorter DELAY / DWELL ah use pannaadhu. Dialog la ippo "TIMED EJECT: ON / OFF" nu kaattum.
- Valve open aagura neram = grain camera line la paatha neram + **DELAY**. Valve open ah irukkura neram = grain neelam + **DWELL** (kuraincha pakkam 1 ms).
- Example: DELAY 22, DWELL 3, grain 8 lines (0.9 ms) → grain paatha 22 ms kazhichu valve open, 3.9 ms open ah irukkum.
- DELAY romba kammi (2 ms maadhiri) na camera → PC ku ~4 ms aagum, so valve late ah dhaan open aagum. Console la LATE count varum. DELAY ah 15 ms ku mela vechu start pannunga (nozzle 70–80 mm na ~22 ms).
- Background Graph window la irukkura DELAY / DWELL andha window oda test firing ku mattum. Sorting ku EJECTORS dialog la set pannunga.

Test: `python tools\test_delay_dwell_sim.py` (DELAY 15/22/40/60, DWELL 2/3/5/10, sorting nadakkum podhe DELAY maathuradhu, DELAY 2 → LATE, DWELL 0 → 1 ms). Simulation la ellam ±0.6 ms kulla correct. Real valve la innum test pannanum.

# LED bar "vaal" (tail) + CAM LOAD (2 Nov 2026)

- Wire ah vegama nagarthinaa LED bar la 10–16 channels "vaal" maadhiri lit aagi irundhuchu. Adhu **display hold** (0.15 s, kannukku theriyanum nu). Valve oda real close illa.
- Ippo: **BRIGHT red = valve ippo ON**, **DIM (dark) red = 0.15 s kulla fire aachu, valve already close**. Text la "VALVES ON NOW: x ch (trail y)".
- Pudhusa **CAM LOAD F a/b ms R c/d ms**: a = ovvoru frame kum PC edukkura neram, b = camera frame idaiveli (~7.2 ms). F + R b ku kitta poana frames queue aagum, appo valve open um close um late aagum. Appo "SLOW: frames queue -> valves late!" nu varum.

# Detection % test: object counter (3 Nov 2026)

Main screen LED line la ippo: `since START: N objects, M blasts`.
- **objects** = chute kaali ah irundhu, apram edhaavadhu theriyum podhu +1. Oru object 2 channels la vizhundhaalum 1 object dhaan.
- **blasts** = ovvoru channel layum valve open aana thadavai.

**Test (90 % kku mela varudhaa):**
1. START SYSTEM (counter 0 aagum). Feeder OFF.
2. 50 objects (ex: 50 black rice) ah **onnu onnu ah**, 1 second idaiveli la, chute la podunga.
3. `objects` 50 kitta varanum. 45 na 90 %, 48 na 96 %.
4. Ore neram rendu objects vizhundhaa (pakkathula), adhu 1 object nu count aagum. So onnu onnu ah dhaan podanum.
5. Reject bin la evvalavu vizhundhuchu nu um enni paarunga. Idhu detection + timing (valve) rendum serndha unmaiyaana result.

# Main screen la DELAY / DWELL (3 Nov 2026)

START SYSTEM button ku mela: `DELAY (ms): [-] [22] [+]    DWELL (ms): [-] [3] [+]    RICE | auto-saved | TIMED EJECT: OFF`
- **[-] / [+]:** DELAY 1 ms step, DWELL 0.5 ms step.
- **Number mela click** pannaa keypad varum.
- **Auto-save:** maathina udane current profile recipe la (`recipes.json` → `ejector`) save aagum. EJECTORS dialog la um adhe values dhaan theriyum. SAVE button theva illa.
- Profile maathinaa (RICE → DHALL), andha profile oda values kaattum.
- **TIMED EJECT: OFF** nu orange la irundha, sorter innum DELAY / DWELL ah use pannaadhu. `capture_timed_eject_enabled: true` pannaa dhaan use aagum (green ah "ON - used" nu varum).
- Switch: `main_delay_dwell_controls` (default true, user ketta maadhiri). Test: `python tools\test_delay_dwell_main_ui.py`

# Timed mode la wire vecchaa "pat pat" (3 Nov 2026)

- Kaaranam: DELAY 13 ms, aana camera frame 12.5 ms + PC ku vara ~4–9 ms. Adutha frame PC ku varradhukkulla munnaadi blast mudinjidum, so valve ON → OFF → ON… frame ku oru thadava ("pat pat").
- Fix (`capture_timing_config.json`: `"continue_margin_ms": 8`): object frame oda kadaisi line varaikkum irundha (adutha frame la um thodarum), valve ah adutha frame varra varaikkum (+8 ms) open ah vechirukkum. Wire / neela object = ore continuous blast. 0 = pazhaya maadhiri.
- Innoru fix: neela blast la ESP32 250 ms watchdog valve ah off pannidum. Ippo ON byte ah 100 ms ku oru thadava thirumba anuppudhu.
- Test: `python tools\test_timed_long_object_sim.py`

# Valves 4–5 minutes kazhichu nikkudhu, LED bar la detect aagudhu (3 Nov 2026)

- Kaaranam: ESP32 USB link la ore oru read error (USB glitch / valve noise) vandhaa, software link ah "closed" nu vechidum. Apram sorter valves ku edhuvum anuppaadhu, aana LED bar la detection theriyum. Thirumba connect aagaadhu.
- Ippo LED line kadaisila: `ESP32 OK` / `ESP32 OK (link lost 2x)` / **`ESP32 LINK LOST - valves NOT driven!`** nu eppovum kaattum.
- Switch `"ejector_auto_reconnect": true` → link poana, adhe COM port ah background la thirumba open pannum (3 s ku oru try). Console la `[EJECTOR LINK] LOST` / `reconnected` varum.
- LED line "ESP32 OK" nu kaattiyum valves adikkala na → ESP32 board pakkam (power drop, reset, noise): board LED, USB cable (short + ferrite), 24 V GND separate ah check pannunga.
- Test: `python tools\test_ejector_link_watch_sim.py`

# SENSITIVITY / PURITY / SCALE / SIZE nijamaave work aagudhaa (4 Nov 2026)

Test: `python tools\test_algo_params_sim.py`. Switch OFF (ippo machine) la:

| Setting | GRAY | HSV | SIZE |
|---|---|---|---|
| SENSITIVITY | ✅ work (0 = off) | ⚠️ 0 la kooda adikkum | – |
| PURITY | ❌ effect illa (DARK mode) | ❌ effect illa | – |
| SCALE | ✅ chinna objects ignore | ✅ | – |
| SMALL / BIG LIMIT | – | – | ❌ ore channel kulla (max 64 px) mattum alakkum |

`"program_settings_fix_enabled": true` pottaa: PURITY = minimum defect area (dialog preview maadhiriye: 1→2 px, 30→~19 px, 60→45 px, 100 = never), HSV SENSITIVITY 0 = off, SIZE = muzhu object agalam (BIG 2300 ≈ 54 px, SMALL 1000 ≈ 36 px). Idhe switch HSV / DISCOLOUR ah product pixel ku mattum vechukkum (3 Nov).

# Background Graph window: ellaa controls um check (4 Nov 2026)

Test: `python tools\test_graph_window_features.py` (real window, fake camera + fake ESP32). Ellam ✅:
SOURCE (ippo maathina udane save + sorter ku poagum), WHITE (HI) / BLACK (LO), BAND PASS, VIEW FRONT / REAR (thani thani values), START / END, START / STOP TEST FIRING, DELAY / DWELL (21 ms / 5 ms measured), RESET (No = edhuvum maaraadhu), mela LED click (andha valve 25 ms), BLUE / GREEN / RED REF (camera gain, max 400, camera_config.json la save), BACK (save).

Gavanikka:
- **BAND PASS:** background band kulla irundhaa ellaa 63 um adikkum. Band ah background ah vittu vekkanum.
- **Test firing** kadaisi **2 lines** mattum paakkum. Sorting (START SYSTEM) 64 lines um paakkum, so sorting la konjam adhigam fire aagalam.
- **CAM 1–10** number edhuvum pannaadhu (code la use illa).
- **CONNECT / DISCONNECT ESP32** button relay board ah dhaan connect / disconnect pannum, ejector ah illa.
- **REF** maathinaa camera gain udane maarum, backup illa. Maathura munnaadi pazhaya value ah note pannunga.
- **4 Nov: graph test firing = START SYSTEM.** Switch `"graph_test_same_as_sorting": true` (default ON, user ketta maadhiri). Graph test firing ippo sorting oda detection ah ye (BACKGROUND (GRAPH), **64 lines um**, adhe noise filter) andha window la irukkura camera / HI / LO / SOURCE / START-END vechu use pannum. Pazhaya 2-lines check ku `false` podunga (wire position test ku).

# Machine settings files ini git la illa (4 Nov 2026)

`bg_config.json`, `camera_config.json`, `ejector_config.json`, `features_config.json`, `recipes.json`, `system_config.json`, `wiper_config.json`, `light_config.json`, `vision_settings.json`: ovvoru PC kum sondham. Git ivangala thodaadhu, so **pull la conflict varaadhu, `git stash` theva illa**. Pudhu PC la file illa na, app start aagum podhu `config_defaults/` la irundhu oru thadava copy pannum.

**Machine la ORU THADAVA mattum (indha change ah pull pannum podhu):**
```
mkdir $env:TEMP\lamcfg -Force
Copy-Item bg_config.json,camera_config.json,ejector_config.json,features_config.json,recipes.json,system_config.json,wiper_config.json,light_config.json,vision_settings.json $env:TEMP\lamcfg\
git stash
git pull
git stash drop
Copy-Item $env:TEMP\lamcfg\*.json . -Force
git status
```
`git status` la indha files varave koodaadhu. Inimel update ku **`git pull` mattum** podhum.
