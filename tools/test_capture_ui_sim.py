"""
Phase 4 test: the CONTINUOUS CAPTURE window with simulated camera workers (no hardware, off-screen).

    python tools\\test_capture_ui_sim.py

Fake camera workers emit frames from their own thread (like GigECameraWorker). Checks: background
learning, capture of 300 objects exactly once, ejection switched off during capture and back on after.
"""
import os
import sys
import tempfile
import threading
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
from PyQt5.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from object_stream import make_synthetic_stream  # noqa: E402
import capture_ui  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures += 1


class FakeWorker(QObject):
    frame_ready = pyqtSignal(np.ndarray)

    def __init__(self):
        super().__init__()
        self.last_meta = None
        self.idx = 0

    def emit_frame(self, f, ts):
        self.idx += 1
        self.last_meta = {"frame_idx": self.idx, "cam_ts": ts, "height": f.shape[0], "crop_y0": 0,
                          "rows_used": f.shape[0], "t_arrive": time.perf_counter()}
        self.frame_ready.emit(f)


app = QApplication(sys.argv)
mw = QWidget()
mw.camera_worker_front = FakeWorker()
mw.camera_worker_rear = FakeWorker()
mw.camera_configs_data = {"FRONT": {"start_pixel": 0, "end_pixel": 4096}}
mw._frame_interval_ms = {"FRONT": 20.0}
mw.capture_ejection_off = False


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.01)


dlg = capture_ui.ContinuousCaptureDialog(mw)
tmp = tempfile.mkdtemp(prefix="lamton_capui_")
dlg.txt_out.setText(tmp)
dlg.cmb_product.setCurrentText("CASHEWS")
dlg.cmb_class.setCurrentText("GOOD_CASHEW")
dlg.cmb_cam.setCurrentText("FRONT")
dlg.chk_strips.setChecked(False)
dlg.txt_speed.setText("1.0")      # 0.1 mm/line -> synthetic shapes become 15-30 mm long (cashew-like)

bg = np.full((200, 4096, 3), (160, 90, 30), np.uint8)
dlg.learn_background()
stop = threading.Event()
th = threading.Thread(target=lambda: [mw.camera_worker_front.emit_frame(bg, i * 200) or time.sleep(0.02)
                                      for i in range(60) if not stop.is_set()])
th.start()
pump(1.6)
stop.set()
th.join()
check("background learned", len(dlg._bg_frames.get("FRONT", [])) > 10, len(dlg._bg_frames.get("FRONT", [])))

# cashew-sized synthetic objects: 100-150 px wide = 8.5-12.8 mm (generator spaces objects 160 px apart)
frames, truth = make_synthetic_stream(n_objects=300, frame_h=200, seed=9, sizes=(100, 150),
                                      lines_per_obj_gap=(10, 40))
dlg.start_capture()
check("ejection switched OFF during capture", mw.capture_ejection_off is True)
th = threading.Thread(target=lambda: [mw.camera_worker_front.emit_frame(f, 100000 + i * 200) or time.sleep(0.02)
                                      for i, f in enumerate(frames)])
th.start()
while th.is_alive():
    pump(0.1)
sess = dlg.session
dlg.stop_capture()
st = sess.status()
print('   size-rejected:', st['rejected_size'])
check("ejection back ON after capture", mw.capture_ejection_off is False)
check("300 objects in -> 300 captured", st["total_objects"] == 300, st["total_objects"])
check("0 dropped frames", sum(st["dropped_frames"].values()) == 0 and sum(st["queue_overflow"].values()) == 0)
out = [d for d in os.listdir(tmp)]
crops = os.listdir(os.path.join(tmp, out[0], "crops", "GOOD_CASHEW"))
check("300 crop files in crops/GOOD_CASHEW", len(crops) == 300, len(crops))
check("status text shows DONE", "DONE" in dlg.lbl_status.text(), dlg.lbl_status.text()[:80])

print("\nALL TESTS PASSED" if failures == 0 else f"\n{failures} TEST(S) FAILED")
sys.stdout.flush()
os._exit(1 if failures else 0)
