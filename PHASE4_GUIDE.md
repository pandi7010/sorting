# Phase 4 — Continuous dataset capture for CNN training

**Status:** complete; **tested in simulation only** (needs cameras + product for real data).
**Default:** OFF. The button appears only when `features_config.json` → `"continuous_capture_button": true`.

## 1. What it does (simple)
While the feeder runs, every object (cashew, almond, dhall…) is cut out of the continuous camera strip
**exactly once and whole**, even when it lies across the cut between two camera frames.
Example: feed 500 cashews → 500 crop images.

## 2. CNN project study (`D:\CNN\CNN`, read only — nothing was changed there)
| Finding | Consequence for capture |
|---|---|
| Training: `CNN_Experiment\train_cnn.py` uses `torchvision.datasets.ImageFolder` → one folder per class | crops are saved as `crops/<CLASS>/*.png`; train with `--dataset <session>\crops` |
| Model input 224 × 224 (MobileNetV2) | `crop_size` = 224 for every product |
| The grader app prepares crops by padding to a square with `BORDER_REPLICATE`, then resizing to 224 | capture does exactly the same |
| Classes (`cashew_class_mapping.json`): GOOD_CASHEW, TESTA, UPW, DW1, DW2, WHITE_DW, SW, BROWN, REJECTION, OIL, PKW1, PKW2, TESTA SMALL | offered in the class list (you can type any other name) |
| Images: .jpg/.jpeg/.png | crops are PNG (lossless) |
| The CNN project is for an **area camera** (different machine) | line-scan crops are first stretched to the true shape (pixels are 0.085 mm across but ~0.35 mm along the flow at 10 kHz / 3.5 m/s) |

## 3. Output of one session
```
dataset_capture\continuous\<PRODUCT>_<date_time>\
    crops\<CLASS>\FRONT_L000123456_000042.png     224 x 224, ready for training
    raw\<CLASS>\...                               (optional) unscaled crops
    strips\FRONT\strip_FRONT_L000000000-000002047.jpg   the continuous strip, 2048 lines per file
    objects.csv     id, camera, class, file, first/last line, left/right pixel, width mm, length mm, flags
    session.json    settings + final counters (objects, dropped frames, files, warnings)
```

## 4. How it never drops or duplicates (and how you can see it)
- The camera thread only **copies** the frame into a queue (< 1 ms). Cutting out objects runs in a separate thread per camera; writing files runs in 2 writer threads.
- **Dropped frames** are detected from the camera's own timestamps (a missing frame = a jump in time) → shown live, must stay **0**.
- **Queue full** (PC too slow) is counted separately → must stay **0**.
- **DISK TOO SLOW** warning if the write queue is more than half full.
- **Disk space guard:** below 3 GB free, strip images switch off (crops continue); below 1 GB, writing stops.
- Objects across a frame cut are joined; finished objects are erased from memory so they cannot be found twice.

## 5. How to use
**A. Inside the sorter (feeder from the sorter, ejection off)** — `"continuous_capture_button": true`, restart:
1. CONTINUOUS CAPTURE → product preset, class label, camera, output folder (use a big, fast disk).
2. Feeder OFF → **LEARN BACKGROUND**.
3. Tick **Ejection OFF** → START CAPTURE → start the feeder with ONE class of product.
4. Watch OBJECTS go up and DROPPED FRAMES stay 0 → STOP CAPTURE.

**B. Without the sorter** (sorter closed; feeder switched by hand or with `relay.py`):
```
python tools\continuous_capture.py --product CASHEWS --class GOOD_CASHEW --camera FRONT --seconds 120 --line-rate 10000
```

⚠ If the camera frame is taller than `roi_height` in `camera_config.json` (200), the sorter only uses the middle rows and the window shows a red warning. Lines in between are not captured. Set `roi_height` ≥ the camera frame height for complete strips (the probe tells you the frame height).

## 6. Tests (simulation, this PC)
| Test | Result |
|---|---|
| `tools/test_object_stream.py` | 500 in → 500 out, 0 duplicates, exact sizes, frame heights 200 / 32 / 16; 8 ms per 200-line frame (a frame comes every 20 ms at 10 kHz) |
| `tools/test_capture_sim.py` | **2 cameras × 500 objects at real speed (10 000 lines/s): 500 + 500 crops, 0 duplicates, 0 missed, 0 dropped frames, 0 queue overflow**, crops 224 × 224, strips cover every line; a lost frame is reported as exactly 1 dropped frame, still no duplicates |
| `tools/test_capture_ui_sim.py` | capture window: background learned; 300 in → 300 crops; ejection off during capture, back on after |
| `tools/continuous_capture.py --simulate 100` | 100 in → 100 out, 0 dropped |

## 7. Disk speed on THIS PC (`tools/disk_speed_test.py`, 16 Oct 2026)
| Disk | Type | Sequential write | Small crop files | Free |
|---|---|---|---|---|
| C: | NVMe SSD 119 GB | **176 MB/s** | 6083 files/s | **7.4 GB only** |
| D: | **external USB HDD** (Toshiba 932 GB) | **35 MB/s** | 200 files/s | 654 GB |

What capture needs (2 cameras): JPG strips 25 MB/s at 10 kHz, 38 MB/s at 15 kHz, 51 MB/s at 20 kHz. PNG strips are about 6× more. Crops only: < 10 MB/s.

**Verdict for this PC:**
- C: is fast enough for JPG strips and crops, but **too full**: about 3 minutes of strips (2 cameras, 15 kHz) would fill it.
- D: (USB HDD) is **too slow** for strips and only just enough for crops of one camera.
- **Recommendation:** capture crops only on this PC (strips off), or add an internal **NVMe SSD ≥ 1 TB** (≥ 1000 MB/s) for strip capture. Run `tools\disk_speed_test.py` on the machine PC too; it may be a different computer.
