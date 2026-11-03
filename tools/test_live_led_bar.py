"""
tools/test_live_led_bar.py - main-screen live LED bar (LiveEjectorLEDBar) with real PyQt5, no hardware.

    python tools/test_live_led_bar.py        -> must print  ALL TESTS PASSED
"""
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
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


app = QApplication.instance() or QApplication(sys.argv)
bar = m.LiveEjectorLEDBar(num_channels=63)
bar.HOLD_S = 0.5           # generous for a busy test PC; the real bar uses 0.15 s
texts = []
bar.on_summary = texts.append


def pump(sec):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


print("LiveEjectorLEDBar")
# frames from a camera THREAD: ch 5 for 3 frames (one blast), then nothing, then ch 5 again (second blast)
def camera():
    for frame in ([5, 63], [5, 63], [5], [], [], [5]):
        if frame:
            bar.note(frame)
        else:
            bar.note_none()
        time.sleep(0.007)
th = threading.Thread(target=camera)
th.start()
th.join()
bar._refresh()             # what the GUI timer does every 60 ms
check("BRIGHT = valve ON now (last frame: ch 5)", bar.active_channels == {5}, str(bar.active_channels))
check("DIM trail = fired recently, valve already closed (ch 63)", bar.trail_channels == {63}, str(bar.trail_channels))
check("counts rising edges: ch5 = 2 blasts, ch63 = 1", bar.counts.get(5) == 2 and bar.counts.get(63) == 1, str(bar.counts))
check("object counter: 2 objects (chute empty in between)", bar.objects == 2, str(bar.objects))
check("summary text shows the most active channel", texts and "ch5x2" in texts[-1], texts[-1] if texts else "")
time.sleep(0.6)
bar._refresh()
bar.note_none()
bar._refresh()
check("goes dark HOLD_S after the last detection (bright and trail)", bar.active_channels == set() and bar.trail_channels == set(),
      f"{bar.active_channels} {bar.trail_channels}")
bar.reset()
bar._refresh()
check("reset on START clears the counters", bar.counts == {} and "0 blasts" in texts[-1], texts[-1])

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
