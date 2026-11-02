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
