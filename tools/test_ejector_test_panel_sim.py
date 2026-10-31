"""
Tests ejector_test_panel.py against the SIMULATED v2.3 ESP32 (tools/ejector_sim.py). No hardware, no window.

    python tools\\test_ejector_test_panel_sim.py

Covers: connect/disconnect, buttons disabled while offline, channel click, single test, auto sequence
(+ loop + STOP), FIRE ALL (confirm + 50 ms limit), STOP ALL + Esc, counters, silent watchdog (idle + auto-arm, no RE-ARM),
remap read/swap/save/default, error -> valves off, lost connection, window close, embedded mode.
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PyQt5.QtWidgets import QApplication  # noqa: E402
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402

import ejector_link as el  # noqa: E402
from ejector_sim import SimEsp32  # noqa: E402
import ejector_test_panel as etp  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures += 1


app = QApplication.instance() or QApplication(sys.argv)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


def sim_factory(port):
    lk = el.EjectorLink(port)
    lk.serial_factory = SimEsp32.factory
    return lk


def log_text(panel):
    return panel.log_view.toPlainText()


# ------------------------------------------------------------------ 0. no camera code
src = open(os.path.join(ROOT, "ejector_test_panel.py"), encoding="utf-8").read().lower()
check("no camera imports/code", not any(k in src for k in ("mvsdk", "import cv2", "camera_logic", "cameraapi")))

# ------------------------------------------------------------------ 1. standalone, offline state
win = etp.EjectorTestWindow(sim=True)
win.show()
p = win.panel
p._confirm = lambda title, text: confirm_answer[0]
confirm_answer = [False]
check("offline: channel buttons disabled", not any(b.isEnabled() for b in p.channel_buttons.values()))
check("offline: single/auto/fire-all/re-arm disabled",
      not any(w.isEnabled() for w in (p.btn_single, p.btn_auto, p.btn_fire_all)))
check("no RE-ARM button and no TRIPPED banner any more", not hasattr(p, "btn_arm") and not hasattr(p, "banner"))
check("offline: STOP ALL enabled", p.btn_stop.isEnabled())
check("SIM port listed and selected", p.combo_ports.currentData() == "SIM")

# ------------------------------------------------------------------ 2. connect
QTest.mouseClick(p.btn_connect, Qt.LeftButton)
check("connect (background thread) -> connected", wait(lambda: p._connected, 6))
sim = SimEsp32.last
check("badge shows v2.3 BINARY ARMED", wait(lambda: "v2.3 BINARY" in p.badge.text() and "ARMED" in p.badge.text()),
      p.badge.text())
check("online: buttons enabled", all(b.isEnabled() for b in p.channel_buttons.values()) and p.btn_auto.isEnabled())
check("button text DISCONNECT", "DISCONNECT" in p.btn_connect.text())

# ------------------------------------------------------------------ 3. click channel = one pulse
p.spin_pulse.setValue(10)
n0 = len(sim.fire_log)
QTest.mouseClick(p.channel_buttons[5], Qt.LeftButton)
check("click CH 5 -> FIRE [5] 10 ms", wait(lambda: len(sim.fire_log) > n0) and sim.fire_log[-1][1:] == ([5], 10.0),
      str(sim.fire_log[-1:]))
check("CH 5 button lit", 5 in p._lit)
check("light goes off again", wait(lambda: 5 not in p._lit, 1.0))

# ------------------------------------------------------------------ 4. single test
p.spin_gap.setValue(20)
p.spin_pulse.setValue(5)
p.spin_single_ch.setValue(7)
p.spin_repeat.setValue(3)
n0 = len(sim.fire_log)
lit_seen = set()
QTest.mouseClick(p.btn_single, Qt.LeftButton)
ok = wait(lambda: (lit_seen.update(p._lit) or True) and "Single CH 7: DONE" in log_text(p), 3)
fires = sim.fire_log[n0:]
check("single CH 7 x3 -> 3 pulses on CH 7", ok and [f[1] for f in fires] == [[7]] * 3, str(fires))
check("single test lit CH 7 (TEST_STEP events)", 7 in lit_seen)
check("progress shows DONE 3/3", p.progress.value() == 3 and "DONE" in p.progress.format(), p.progress.format())

# ------------------------------------------------------------------ 5. auto sequence 1..10
p.spin_from.setValue(1)
p.spin_to.setValue(10)
p.chk_loop.setChecked(False)
p.spin_gap.setValue(10)
p.spin_pulse.setValue(2)
n0 = len(sim.fire_log)
lit_seen = set()
QTest.mouseClick(p.btn_auto, Qt.LeftButton)
ok = wait(lambda: (lit_seen.update(p._lit) or True) and "Auto CH 1-10: DONE" in log_text(p), 4)
check("auto 1..10 -> channels 1..10 in order", ok and [f[1][0] for f in sim.fire_log[n0:]] == list(range(1, 11)))
check("auto lit several channels", len(lit_seen) >= 3, str(sorted(lit_seen)))

# from > to is refused
p.spin_from.setValue(9)
p.spin_to.setValue(3)
n0 = len(sim.commands)
QTest.mouseClick(p.btn_auto, Qt.LeftButton)
pump(0.1)
check("auto from>to refused", "must not be larger" in log_text(p) and "TEST2" not in sim.commands[n0:])

# ------------------------------------------------------------------ 6. auto loop + STOP ALL
p.spin_from.setValue(1)
p.spin_to.setValue(3)
p.chk_loop.setChecked(True)
QTest.mouseClick(p.btn_auto, Qt.LeftButton)
check("loop runs 3+ rounds", wait(lambda: p._run is not None and p._run["loops"] >= 3, 4),
      p.progress.format())
check("progress shows loop count", "loop" in p.progress.format(), p.progress.format())
n_stop = sim.commands.count("STOP_ALL")
QTest.mouseClick(p.btn_stop, Qt.LeftButton)
pump(0.1)
n_after = len(sim.fire_log)
pump(0.3)
check("STOP ALL -> TEST stop + STOP_ALL sent", sim.commands.count("STOP_ALL") == n_stop + 1 and "TEST0" in sim.commands)
check("STOP ALL -> firing stops", len(sim.fire_log) == n_after, f"{n_after} -> {len(sim.fire_log)}")
check("STOP ALL -> run cleared", p._run is None and "stopped" in p.progress.format())
p.chk_loop.setChecked(False)

# ------------------------------------------------------------------ 7. Esc = STOP ALL
win.activateWindow()
p.spin_gap.setFocus()
pump(0.05)
n_stop = sim.commands.count("STOP_ALL")
QTest.keyClick(p.spin_gap, Qt.Key_Escape)
check("Esc key -> STOP_ALL", wait(lambda: sim.commands.count("STOP_ALL") == n_stop + 1, 1))

# ------------------------------------------------------------------ 8. FIRE ALL
p.spin_pulse.setValue(120)
n0 = len(sim.fire_log)
confirm_answer[0] = False
QTest.mouseClick(p.btn_fire_all, Qt.LeftButton)
pump(0.1)
check("FIRE ALL cancelled -> nothing fired", len(sim.fire_log) == n0 and "cancelled" in log_text(p))
confirm_answer[0] = True
QTest.mouseClick(p.btn_fire_all, Qt.LeftButton)
ok = wait(lambda: len(sim.fire_log) > n0)
check("FIRE ALL -> 63 channels, pulse limited to 50 ms", ok and sim.fire_log[-1][1] == list(range(1, 64))
      and sim.fire_log[-1][2] == 50.0, str(sim.fire_log[-1][2:]) if ok else "")

# ------------------------------------------------------------------ 9. counters
check("counters show ESP32 fired count", wait(lambda: f"fired {sim.counters['fired']} " in p.lbl_counters.text(), 2.5),
      p.lbl_counters.text())
check("counters show crc_err 0 / no-ack", "crc_err 0" in p.lbl_counters.text() and "no-ack" in p.lbl_counters.text())
check("heartbeat OK", "heartbeat OK" in p.lbl_heartbeat.text(), p.lbl_heartbeat.text())

# ------------------------------------------------------------------ 10. silent watchdog: idle, then the next click fires at once
lk = p.link()
trips0 = sim.counters["wdt_trips"]
p._poll_timer.stop()                                         # real silence = NO command at all: stop the 1 s STATUS polling too
p._status_t = time.time() + 60
lk.heartbeat_paused = True                                   # the PC goes silent


def refresh_badge():
    p._poll_inner()
    return True


check("silence -> the board switched its outputs off and is idle (it tells nobody: silent)",
      wait(lambda: sim.state == 0, 3), str(sim.state))
check("silence -> counted once by the board, no event, nothing refused",
      sim.counters["wdt_trips"] == trips0 + 1 and not any(c.startswith("REJECTED") for c in sim.commands), str(sim.counters["wdt_trips"] - trips0))
check("no TRIPPED anywhere in the panel", "TRIPPED" not in p.badge.text() and "TRIPPED" not in log_text(p))
lk.heartbeat_paused = False
n0 = len(sim.fire_log)
p._poll_timer.start(etp.POLL_MS)
p._status_t = 0
QTest.mouseClick(p.channel_buttons[9], Qt.LeftButton)        # NO RE-ARM: the click itself arms the board
check("click after the silence fires immediately", wait(lambda: len(sim.fire_log) > n0 and sim.fire_log[-1][1] == [9], 2),
      str(sim.fire_log[-1:]))
check("board armed again by that click", wait(lambda: sim.state == 1 and "ARMED" in p.badge.text(), 2), p.badge.text())

# ------------------------------------------------------------------ 11. remap
p.remap_box.setChecked(True)
check("remap section opens", p.remap_inner.isVisibleTo(p))
QTest.mouseClick(p.btn_map_read, Qt.LeftButton)
check("read map -> all default", wait(lambda: "all channels default" in p.lbl_map.text(), 2), p.lbl_map.text())
p.spin_swap_a.setValue(1)
p.spin_swap_b.setValue(2)
QTest.mouseClick(p.btn_swap, Qt.LeftButton)
check("swap 1<->2 sent to ESP32", wait(lambda: sim.remap[:2] == [1, 0], 1), str(sim.remap[:3]))
check("swap shown + tooltip", "CH1->OUT1" in p.lbl_map.text() and "OUT 1" in p.channel_buttons[1].toolTip())
check("swap not saved yet", sim.saved_remap[:2] == [0, 1])
QTest.mouseClick(p.btn_map_save, Qt.LeftButton)
check("save -> ESP32 flash", wait(lambda: sim.saved_remap[:2] == [1, 0], 1))
QTest.mouseClick(p.btn_map_default, Qt.LeftButton)
check("restore default", wait(lambda: sim.remap == list(range(63)) and "all channels default" in p.lbl_map.text(), 2))

# ------------------------------------------------------------------ 12. error -> valves off
real_fire = lk.fire
lk.fire = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated write failure"))
n_stop = sim.commands.count("STOP_ALL")
QTest.mouseClick(p.channel_buttons[3], Qt.LeftButton)
pump(0.1)
check("error in a button -> logged + STOP_ALL", "ERROR in channel pulse" in log_text(p)
      and sim.commands.count("STOP_ALL") == n_stop + 1)
lk.fire = real_fire

# ------------------------------------------------------------------ 13. disconnect
QTest.mouseClick(p.btn_connect, Qt.LeftButton)
check("disconnect -> buttons disabled", wait(lambda: not p._connected and not p.btn_fire_all.isEnabled(), 2))
check("disconnect -> STOP_ALL then DISARM (clean, no trip)", wait(lambda: sim.commands[-2:] == ["STOP_ALL", "DISARM"], 2),
      str(sim.commands[-4:]))
check("disconnect not reported as LOST", "LOST" not in log_text(p))

# ------------------------------------------------------------------ 14. connection lost
QTest.mouseClick(p.btn_connect, Qt.LeftButton)
check("reconnect", wait(lambda: p._connected, 6))
sim2 = SimEsp32.last


def broken(n=1):
    raise OSError("USB device removed")


sim2.read = broken
check("lost connection detected + logged", wait(lambda: not p._connected and "LOST" in log_text(p), 2))
check("lost: buttons disabled", not p.btn_single.isEnabled())

# ------------------------------------------------------------------ 15. window close -> valves off
QTest.mouseClick(p.btn_connect, Qt.LeftButton)
check("connect again", wait(lambda: p._connected, 6))
sim3 = SimEsp32.last
win.close()
pump(0.2)
check("close window -> STOP_ALL + DISARM", "STOP_ALL" in sim3.commands[-3:] and sim3.commands[-1] == "DISARM",
      str(sim3.commands[-4:]))
check("close window -> COM port closed", not sim3.is_open)

# ------------------------------------------------------------------ 16. embedded mode (shared link)
host_events = []
host_link = el.EjectorLink("SIM")
host_link.serial_factory = SimEsp32.factory
host_link.on_event = lambda n, a: host_events.append(n)
host_link.connect()
hsim = SimEsp32.last
holder = {"link": None}
emb = etp.EjectorTestPanel(link_provider=lambda: holder["link"])
emb._confirm = lambda t, x: True
emb.show()
pump(0.1)
check("embedded: no COM controls", not emb.combo_ports.isVisible() and not emb.btn_connect.isVisible()
      and emb.lbl_shared.isVisible())
check("embedded without sorter link: disabled", not emb.btn_single.isEnabled() and "NOT CONNECTED" in emb.badge.text())
holder["link"] = host_link
check("embedded: picks up the shared link", wait(lambda: emb._connected and "ARMED" in emb.badge.text(), 2))
check("embedded: did not open a 2nd port", SimEsp32.last is hsim)
emb.spin_single_ch.setValue(4)
emb.spin_repeat.setValue(2)
emb.spin_gap.setValue(10)
emb.spin_pulse.setValue(2)
QTest.mouseClick(emb.btn_single, Qt.LeftButton)
check("embedded: single test runs", wait(lambda: "Single CH 4: DONE" in log_text(emb), 2))
check("embedded: host's on_event callback untouched", host_link.on_event is not None and "TEST_STEP" in host_events)
emb.shutdown()
pump(0.1)
check("embedded shutdown -> STOP_ALL, no DISARM, link stays open",
      hsim.commands[-1] == "STOP_ALL" and "DISARM" not in hsim.commands and host_link.is_open, str(hsim.commands[-3:]))
host_link.close()

print()
print("ALL TESTS PASSED" if failures == 0 else f"{failures} TEST(S) FAILED")
sys.exit(1 if failures else 0)
