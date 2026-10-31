"""
Tests the COM PORTS dialog (port_settings_ui.py) and the sorter's start-up port logic (ESP32Controller +
auto_connect_esp32 rules) with SIMULATED boards on different COM numbers. No hardware, no window, nothing can fire.

    python tools\\test_port_settings_sim.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import ejector_link as el  # noqa: E402
import port_detect as pd  # noqa: E402
from port_sim import World  # noqa: E402
from ejector_sim import SimEsp32  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures += 1


pd.TIMING.update({"ejector": 0.7, "ejector_old": 0.4, "relay": 0.3, "open_settle": 0.0})


# --- the sorter's controller class (no window is created)
cwd = os.getcwd()
spec = importlib.util.spec_from_file_location("lamton_main", os.path.join(ROOT, "LAMTON_LINE_NEW_ESP32.py"))
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)
os.chdir(cwd)

# PyQt5 only AFTER the sorter module (it imports torch first: the other order crashes on Windows)
from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
from PyQt5.QtWidgets import QMessageBox  # noqa: E402

messages = []
QMessageBox.warning = staticmethod(lambda *a, **k: messages.append(a[2] if len(a) > 2 else ""))     # modal boxes cannot be answered offscreen
QMessageBox.critical = staticmethod(lambda *a, **k: messages.append(a[2] if len(a) > 2 else ""))


def wait(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False

el.EjectorLink.serial_factory = staticmethod(lambda port, baud: SimEsp32(port, baud))   # ejector link talks to the simulator on ANY port name

# ---------------------------------------------------------------- 1. controller rules: no substitution, no double use
esp = main.ESP32Controller(None)
check("connect_ejector(None) refused", esp.connect_ejector(None) is False)
check("relay connect('') refused", esp.connect("") is False)
check("ensure_relay: relay disabled -> never connects", (setattr(esp, "relay_enabled", False), setattr(esp, "relay_port_cfg", "COM77"), esp.ensure_relay())[2] is False)
esp.relay_enabled = True
ok = esp.connect_ejector("COM24")
check("ejector connected on COM24 (any number)", ok and esp.ejector_link.mode == "BINARY" and esp.ejector_port == "COM24", esp.ejector_port)
check("relay on the SAME port is refused (no 'use COM13 instead')", esp.connect("COM24") is False and esp.active_port is None)
check("feeder on the ejector port is refused", esp.connect_vibrator("COM24") is False)
esp.disconnect_ejector()
ok = esp.connect_ejector("COM13")
check("the same code works on COM13", ok and esp.ejector_port == "COM13")
esp.disconnect_ejector()

# ---------------------------------------------------------------- 2. the COM PORTS dialog with simulated boards
from port_settings_ui import PortSettingsDialog  # noqa: E402

tmp = tempfile.mkdtemp()
cfg_file = os.path.join(tmp, "system_config.json")
json.dump({"ejector_port": "COM1", "last_port": "COM13", "modbus_enabled": True, "vibrator_port": "COM7", "auto_connect": True},
          open(cfg_file, "w"))                                   # deliberately WRONG saved ports
w = World({"COM13": "ejector23", "COM24": "relay_old", "COM7": "feeder"})
w.install()
esp = main.ESP32Controller(None)
dlg = PortSettingsDialog(esp, None, config_file=cfg_file)
labels = [dlg.combos["ejector"].itemText(i) for i in range(dlg.combos["ejector"].count())]
check("dropdown: first entry 'Disabled', then every port with USB chip + description",
      labels[0] == "Disabled" and any("COM13 [Silicon Labs CP210x]" in t and "USB Serial" in t for t in labels), str(labels[:3]))
dlg.auto_detect()
check("Auto detect finishes", wait(lambda: "Found" in dlg.lbl_note.text() or "Nothing" in dlg.lbl_note.text()), dlg.lbl_note.text())
check("Auto detect selects the ejector on COM13 and the relay on COM24",
      dlg.combos["ejector"].currentData() == "COM13" and dlg.combos["relay"].currentData() == "COM24",
      f"{dlg.combos['ejector'].currentData()} {dlg.combos['relay'].currentData()}")
check("feeder port (silent) was NOT probed and not changed", w.written.get("COM7") is None and dlg.combos["feeder"].currentData() == "COM7")
check("no Modbus byte reached the ejector port", w.modbus_bytes_to("COM13") == [])
dlg.test("ejector")
check("Test (ejector) reports the board", wait(lambda: "COM13" in dlg.status["ejector"].text() and "v2.3" in dlg.status["ejector"].text()), dlg.status["ejector"].text())
dlg.test("feeder")
check("Test (feeder) says it cannot be verified, sends nothing", wait(lambda: "port opens" in dlg.status["feeder"].text()) and w.written.get("COM7") is None, dlg.status["feeder"].text())
dlg.combos["relay"].setCurrentIndex(dlg.combos["ejector"].currentIndex())     # same port for two devices
dlg.save_and_connect()
saved = json.load(open(cfg_file))
check("same port twice is refused (message shown), nothing saved", saved["ejector_port"] == "COM1" and any("two devices" in m for m in messages), str(messages))
dlg.combos["relay"].setCurrentIndex(dlg.combos["relay"].findData("COM24"))
# save: relay connection itself needs a real serial port (pymodbus) -> replace the two real connectors by recorders
calls = []
esp.connect = lambda port, baudrate=9600: calls.append(("relay", port)) or True
esp.connect_vibrator = lambda port: calls.append(("feeder", port)) or True
orig_ej = esp.connect_ejector
esp.connect_ejector = lambda port, baudrate=None: calls.append(("ejector", port)) or orig_ej(port)
dlg.save_and_connect()
saved = json.load(open(cfg_file))
check("Save: ports + enabled flags written to system_config.json",
      saved["ejector_port"] == "COM13" and saved["last_port"] == "COM24" and saved["vibrator_port"] == "COM7"
      and saved["ejector_enabled"] and saved["modbus_enabled"] and saved["vibrator_enabled"], str(saved))
check("Save: each device connected once on its own port", sorted(calls) == [("ejector", "COM13"), ("feeder", "COM7"), ("relay", "COM24")], str(calls))
dlg.combos["relay"].setCurrentIndex(0)                                         # Disabled
dlg.combos["feeder"].setCurrentIndex(0)
dlg.save_and_connect()
saved = json.load(open(cfg_file))
check("Disabled is saved (modbus_enabled false, no port) and the controller is told", saved["modbus_enabled"] is False
      and saved["last_port"] is None and esp.relay_enabled is False and esp.relay_port_cfg is None, str(saved))
esp.disconnect_ejector()

# ---------------------------------------------------------------- 3. start-up logic (resolve) in the three scenarios
def startup(devices, cfg):
    w = World(devices)
    w.install()
    a, m, n = pd.resolve(cfg, progress=lambda x: None)
    return w, a, m, n


w, a, m, n = startup({"COM13": "ejector23", "COM24": "relay_old"}, {"ejector_port": "COM24", "last_port": "COM13"})
check("start-up, saved ports swapped: both found, both saved", a["ejector"].port == "COM13" and a["relay"].port == "COM24"
      and n == {"ejector_port": "COM13", "last_port": "COM24"})
w, a, m, n = startup({"COM24": "ejector23", "COM13": "relay_old"}, {"ejector_port": "COM13", "last_port": "COM24"})
check("start-up, the other way round (ejector COM24, relay COM13)", a["ejector"].port == "COM24" and a["relay"].port == "COM13")
w, a, m, n = startup({"COM13": "ejector23"}, {"ejector_port": "COM24", "last_port": "COM13", "modbus_enabled": False})
check("start-up, ejector alone, relay disabled: nothing opened at 9600", a["ejector"].port == "COM13" and w.opened_at_9600() == set())

print()
print("ALL TESTS PASSED" if failures == 0 else f"{failures} TEST(S) FAILED")
sys.stdout.flush()
os._exit(1 if failures else 0)
