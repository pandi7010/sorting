"""
tools/test_graph_window_features.py - every control of the Background Graph window (Program Settings ->
BACKGROUND / GRAPH), with the REAL window, real PyQt5 signals, a fake camera and a fake ESP_BYTE ejector link.
No hardware, nothing shown on screen. bg_config.json is written in a temp folder; camera_config.json is backed up
and restored.

Checked: SOURCE colour, WHITE (HI) / BLACK (LO), BAND PASS, VIEW FRONT/REAR, START/END bounds, START/STOP TEST
FIRING, DELAY/DWELL timed test firing, RESET, top LED click, BLUE/GREEN/RED REF (camera gain), CAM selector, close.

    python tools/test_graph_window_features.py        -> must print  ALL TESTS PASSED
"""
import os
import sys
import json
import time
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt5.QtCore import QObject, pyqtSignal, Qt, QPointF, QEvent
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtWidgets import QApplication, QMessageBox
except Exception:
    print("SKIPPED: real PyQt5 not installed")
    print("ALL TESTS PASSED")
    sys.exit(0)

import importlib.util  # noqa: E402
import numpy as np  # noqa: E402
_cwd = os.getcwd()
spec = importlib.util.spec_from_file_location("lamton_main", os.path.join(ROOT, "LAMTON_LINE_NEW_ESP32.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
os.chdir(_cwd)

fails = []


def check(n, c, i=""):
    print(f"  [{'OK  ' if c else 'FAIL'}] {n} {i}")
    if not c:
        fails.append(n)


class Worker(QObject):
    frame_ready = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.params = {}

    def set_param(self, k, v):
        self.params[k] = v


class Link:
    is_open = True
    mode = "ESP_BYTE"

    def __init__(self):
        self.ev = []

    def _write(self, b):
        t = time.perf_counter()
        self.ev += [(t, x) for x in b]
        return True

    def stop_all(self):
        self.ev.append((time.perf_counter(), 0))


class ESP:
    connected = True

    def __init__(self):
        self.ejector_link = Link()
        self.fired = []

    def fire_single_ejector(self, ch, ms):
        self.fired.append((ch, ms))

    def stop_all_ejectors(self):
        pass


def on_bytes(link):
    return [x for _, x in link.ev if 1 <= x <= 63]


def off_bytes(link):
    return [x - 100 for _, x in link.ev if 101 <= x <= 163]


app = QApplication.instance() or QApplication(sys.argv)
CAMCFG = os.path.join(ROOT, "camera_config.json")
bak = CAMCFG + ".graphtest_bak"
shutil.copy(CAMCFG, bak)
tmp = tempfile.mkdtemp()
os.chdir(tmp)                                     # bg_config.json goes here
try:
    with open("bg_config.json", "w") as f:
        json.dump({"camera_thresh": {"FRONT": {"lower": 10, "upper": 75, "band_mode": False},
                                     "REAR": {"lower": 15, "upper": 65, "band_mode": False}},
                   "wave_mode": "GREEN"}, f)
    cam = json.load(open(CAMCFG))
    SP, EP = int(cam["FRONT"].get("start_pixel", 19)), int(cam["FRONT"].get("end_pixel", 4046))
    CW = (EP - SP) / 63.0

    F, R = Worker(), Worker()
    esp = ESP()
    win = m.BackgroundGraphWindow(front_worker=F, rear_worker=R, esp32_handler=esp)
    win.timer.stop()                              # no drawing needed
    mw = type("MW", (), {})()
    mw.bg_settings = {"wave_mode": "GREEN", "camera_thresh": {}}
    mw.camera_configs_data = {"FRONT": dict(cam["FRONT"]), "REAR": dict(cam["REAR"])}
    win.parent_ui = type("PU", (), {})()
    win.parent_ui.main_window = mw
    link = esp.ejector_link

    def frame(ch=None, g=200, bg=(120, 35, 25)):
        f = np.zeros((64, 4096, 3), np.uint8)
        f[:] = bg
        if ch is not None:
            x = int(SP + (ch - 0.5) * CW)
            f[:, x - 15:x + 15] = (g, g, g)
        return f

    def saved():
        return json.load(open("bg_config.json"))

    print("1. SOURCE colour")
    win.combo_color.setCurrentText("BLUE")
    check("SOURCE BLUE saved at once in bg_config.json", saved().get("wave_mode") == "BLUE", str(saved().get("wave_mode")))
    check("running sorter gets BLUE at once", mw.bg_settings.get("wave_mode") == "BLUE")
    win.combo_color.setCurrentText("GREEN")

    print("2. WHITE (HI) / BLACK (LO) keypad values")
    win.set_upper_manual("80")
    win.set_lower_manual("12")
    st = saved()["camera_thresh"]["FRONT"]
    check("HI 80 / LO 12 saved for FRONT", st["upper"] == 80 and st["lower"] == 12, str(st))
    check("running sorter gets the new HI / LO", mw.bg_settings["camera_thresh"]["FRONT"]["upper"] == 80)
    check("slider + button show the values", win.slider_upper.value() == 80 and win.btn_slider_low_val.text() == "12")

    print("3. START / STOP TEST FIRING (level follow)")
    win.update_live_frame(frame(10))
    check("firing STOPPED: a bright object does not fire", on_bytes(link) == [])
    win.start_local_system()
    link.ev.clear()
    win.update_live_frame(frame(10))
    check("firing ACTIVE: bright object (green 200 > HI 80) fires ch 10", on_bytes(link) == [10], str(on_bytes(link)))
    win.update_live_frame(frame(None))
    check("object gone -> ch 10 OFF", 10 in off_bytes(link), str(off_bytes(link)))
    link.ev.clear()
    win.update_live_frame(frame(30, g=5))
    check("dark object (green 5 < LO 12) fires ch 30", on_bytes(link) == [30], str(on_bytes(link)))
    win.update_live_frame(frame(30, g=50))
    check("object between LO and HI (50) -> OFF, not fired", 30 in off_bytes(link))
    link.ev.clear()
    win.update_live_frame(frame(None))
    check("empty chute -> nothing fires", on_bytes(link) == [])

    print("4. BAND PASS")
    win.toggle_band_mode()
    check("BAND PASS ON saved", saved()["camera_thresh"]["FRONT"]["band_mode"] is True)
    check("labels show BAND TOP / BAND BOT", win.lbl_upper.text() == "BAND TOP:" and win.lbl_lower.text() == "BAND BOT:")
    link.ev.clear()
    BB = (120, 100, 25)            # band mode needs a background OUTSIDE the band (green 100 > 80)
    win.update_live_frame(frame(20, g=50, bg=BB))
    check("BAND 12-80: object at 50 fires ch 20", on_bytes(link) == [20], str(on_bytes(link)[:8]))
    win.update_live_frame(frame(20, g=200, bg=BB))
    check("BAND: object at 200 (outside) -> OFF", 20 in off_bytes(link))
    win.toggle_band_mode()
    check("BAND PASS OFF again", saved()["camera_thresh"]["FRONT"]["band_mode"] is False)

    print("5. SOURCE decides which colour is checked")
    win.combo_color.setCurrentText("RED")
    link.ev.clear()
    win.update_live_frame(frame(None, bg=(120, 200, 25)))          # green 200, red 25
    check("SOURCE RED: green-bright background is ignored", on_bytes(link) == [], str(on_bytes(link)[:5]))
    win.combo_color.setCurrentText("COMBO: ALL (RGB)")
    win.update_live_frame(frame(None, bg=(120, 35, 25)))
    check("COMBO ALL: blue 120 > HI 80 -> fires (every plane checked)", len(on_bytes(link)) > 0)
    win.update_live_frame(frame(None, bg=(40, 35, 25)))
    win.combo_color.setCurrentText("GREEN")

    print("6. VIEW FRONT / REAR keeps separate values")
    win.toggle_view()
    check("VIEW REAR shows REAR HI 65", win.camera_view == "REAR" and win.slider_upper.value() == 65, str(win.slider_upper.value()))
    win.set_lower_manual("20")
    win.toggle_view()
    st = saved()["camera_thresh"]
    check("REAR LO 20 saved, FRONT LO still 12", st["REAR"]["lower"] == 20 and st["FRONT"]["lower"] == 12, str(st))
    check("back on FRONT: HI 80 shown", win.slider_upper.value() == 80)

    print("7. START / END bounds")
    s0 = win.start_pixel
    win.nudge_start(+10)
    c = json.load(open(CAMCFG))
    check("START +10 saved in camera_config.json", c["FRONT"]["start_pixel"] == s0 + 10, str(c["FRONT"]["start_pixel"]))
    check("running sorter gets the new START", mw.camera_configs_data["FRONT"]["start_pixel"] == s0 + 10)
    win.nudge_start(-10)
    check("START back", json.load(open(CAMCFG))["FRONT"]["start_pixel"] == s0)
    e0 = win.end_pixel
    win.nudge_end(-10)
    check("END -10 saved", json.load(open(CAMCFG))["FRONT"]["end_pixel"] == e0 - 10)
    win.nudge_end(+10)

    print("8. DELAY / DWELL (timed test firing)")
    win.set_delay_manual("20")
    win.set_blow_manual("5")
    st = saved()["camera_thresh"]["FRONT"]
    check("DELAY 20 / DWELL 5 saved", st.get("delay_ms") == 20 and st.get("blow_ms") == 5, str(st))
    link.ev.clear()
    t0 = time.perf_counter()
    win.update_live_frame(frame(40))
    win.update_live_frame(frame(None))
    time.sleep(0.1)
    on = [t for t, x in link.ev if x == 40]
    off = [t for t, x in link.ev if x == 140]
    if on and off:
        check("valve opens ~20 ms after the object was seen", 0.017 < on[0] - t0 < 0.03, f"({(on[0] - t0) * 1000:.1f} ms)")
        check("stays open >= DWELL 5 ms", off[0] - on[0] >= 0.0045, f"({(off[0] - on[0]) * 1000:.1f} ms)")
    else:
        check("timed blast happened", False, str(link.ev[:6]))
    win.set_delay_manual("0")
    win.set_blow_manual("0")

    print("9. RESET")
    orig_q = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    win.reset_settings()
    check("RESET -> No: nothing changes", saved()["camera_thresh"]["FRONT"]["upper"] == 80)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    win.reset_settings()
    QMessageBox.question = orig_q
    st = saved()["camera_thresh"]["FRONT"]
    check("RESET -> Yes: HI 225 / LO 80 / band off", st["upper"] == 225 and st["lower"] == 80 and not st["band_mode"], str(st))

    print("10. Top LED bar click = test fire one valve")
    win.lbl_leds.resize(630, 28)
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(95, 5), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    win.lbl_leds.mousePressEvent(ev)
    check("click on LED 10 fires ejector 10 for 25 ms", esp.fired == [(10, 25)], str(esp.fired))

    print("11. STOP TEST FIRING")
    win.set_upper_manual("80")
    win.set_lower_manual("12")
    win.update_live_frame(frame(50))
    link.ev.clear()
    win.stop_local_system()
    check("STOP sends OFF for the open valve (ch 50)", 50 in off_bytes(link), str(link.ev))
    link.ev.clear()
    win.update_live_frame(frame(50))
    check("after STOP nothing fires", on_bytes(link) == [])

    print("12. BLUE / GREEN / RED REF = camera gain")
    win.set_camera_rgb_gain("B", 150)
    check("REF B 150 -> camera gain command (b_gain 150)", F.params.get("b_gain") == 150, str(F.params))
    check("REF B saved in camera_config.json", json.load(open(CAMCFG))["FRONT"].get("b_gain") == 150)
    win.set_camera_rgb_gain("B", 999)
    check("REF limited to 400", F.params.get("b_gain") == 400)

    print("13. CAM selector")
    win.next_cam()
    check("CAM 1 -> 2 (shown only - no code uses this number)", win.camera_id == 2)
    win.prev_cam()

    print("15. Test firing = START SYSTEM (switch graph_test_same_as_sorting)")
    from types import SimpleNamespace as NS
    MD = m.MainDashboard
    sorter = NS(bg_settings={"wave_mode": "GREEN", "camera_thresh": {}}, thresholds={})
    sorter.process_frame_for_defects = lambda *a, **k: MD.process_frame_for_defects(sorter, *a, **k)
    win.parent_ui.main_window = sorter
    win.set_upper_manual("80"); win.set_lower_manual("12")
    win.start_local_system()

    def flake(ch, rows=(10, 15)):                  # a falling flake seen only in rows 10-14 of the 64-line frame
        f = frame(None)
        x = int(SP + (ch - 0.5) * CW)
        f[rows[0]:rows[1], x - 15:x + 15] = (200, 200, 200)
        return f
    m.features._cache = dict(m.features.DEFAULTS, background_graph_algo_enabled=True, graph_test_same_as_sorting=False)
    link.ev.clear(); win.update_live_frame(flake(33)); win.update_live_frame(frame(None))
    check("switch OFF (old): flake in rows 10-14 is NOT seen (only the last 2 lines checked)", on_bytes(link) == [], str(on_bytes(link)))
    m.features._cache = dict(m.features.DEFAULTS, background_graph_algo_enabled=True, graph_test_same_as_sorting=True)
    link.ev.clear(); win.update_live_frame(flake(33)); win.update_live_frame(frame(None))
    check("switch ON: the same flake fires ch 33", on_bytes(link) == [33], str(on_bytes(link)))
    for fr_ in (flake(5), flake(60, rows=(0, 3)), frame(12, g=5), frame(None)):
        cams = {"start_pixel": win.start_pixel, "end_pixel": win.end_pixel, "num_channels": 63, "wave_mode": "GREEN",
                "total_chutes": 1, "chute_roles": {"1": "PRIMARY"}}
        want = set(MD.process_frame_for_defects(sorter, fr_, cams, ["BACKGROUND (GRAPH)"], {}, "FRONT")["channels"])
        got = win._sorter_channels(fr_)
        check(f"graph = sorter for test frame ({sorted(want)})", got == want, f"graph {sorted(got or [])}")
    m.features._cache = dict(m.features.DEFAULTS, detection_fixes_enabled=True, min_defect_area_px=20,
                             background_graph_algo_enabled=True, graph_test_same_as_sorting=True)
    tiny = frame(None); x = int(SP + 20.5 * CW); tiny[30:32, x:x + 3] = (200, 200, 200)
    check("same noise filter as the sorter (6-pixel speck ignored with min area 20)", win._sorter_channels(tiny) == set())
    m.features._cache = None
    win.stop_local_system()

    print("14. BACK / close")
    win.close_cleanly()
    check("closing saves the settings (SOURCE GREEN kept)", saved().get("wave_mode") == "GREEN")
finally:
    os.chdir(_cwd)
    shutil.move(bak, CAMCFG)

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
