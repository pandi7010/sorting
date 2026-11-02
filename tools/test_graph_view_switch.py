"""
tools/test_graph_view_switch.py - Background Graph window: VIEW FRONT <-> REAR must show ONE camera only.

Bug found on the machine 2 Nov 2026: after pressing VIEW the old camera stayed connected, so both cameras'
frames reached the window (alternating backgrounds, LEDs evaluated with the wrong camera's bounds).
Uses real PyQt5 signals (no camera, no window shown). Needs PyQt5 - prints SKIPPED otherwise.

    python tools/test_graph_view_switch.py        -> must print  ALL TESTS PASSED
"""
import os
import sys
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt5.QtCore import QObject, pyqtSignal
    from PyQt5.QtWidgets import QApplication
except Exception:
    print("SKIPPED: real PyQt5 not installed")
    print("ALL TESTS PASSED")
    sys.exit(0)

import importlib.util  # noqa: E402
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


app = QApplication.instance() or QApplication(sys.argv)
F, R = Worker(), Worker()
BG = m.BackgroundGraphWindow
g = BG.__new__(BG)                 # only the camera-switching code is exercised (no dialog is built)
got = []
g.update_live_frame = lambda f: got.append(f)
g.front_worker, g.rear_worker, g.camera_worker = F, R, F
g.camera_view = "FRONT"
g.camera_thresh = {"FRONT": {"lower": 68, "upper": 221, "band_mode": False, "delay_ms": 0, "blow_ms": 0},
                   "REAR": {"lower": 60, "upper": 164, "band_mode": False}}
for name in ("slider_lower", "btn_slider_low_val", "slider_upper", "btn_slider_up_val", "btn_delay_val",
             "btn_blow_val", "btn_view", "lbl_bounds_info", "btn_adj_start_val", "btn_adj_end_val",
             "btn_ref_r", "btn_ref_g", "btn_ref_b"):
    setattr(g, name, MagicMock())
g.apply_band_mode_ui = lambda: None
g.hw_active_channels = set()
g.hw_channel_on_time = {}
g.arduino = None


def frames():
    got.clear()
    F.frame_ready.emit("F")
    R.frame_ready.emit("R")
    return list(got)


print("Background Graph window: which camera feeds the view")
BG.load_camera_bounds(g)                       # as in __init__
check("opened (FRONT): only FRONT frames", frames() == ["F"], str(got))
BG.load_camera_bounds(g)                       # called again (e.g. after camera setup): still one connection
check("load_camera_bounds twice: still exactly one FRONT frame", frames() == ["F"], str(got))
for i in range(6):
    BG.toggle_view(g)
    want = ["R"] if g.camera_view == "REAR" else ["F"]
    check(f"toggle {i + 1} -> {g.camera_view}: only {want[0]} frames, once", frames() == want, str(got))
BG._detach_camera_workers(g)
check("close: no camera feeds the window any more", frames() == [], str(got))

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
