"""
port_settings_ui.py - "COM PORTS" settings dialog (ENGINEER login).

For each device (ejector, relay, feeder): a dropdown with every COM port of this PC (port name, USB chip, Windows
description) plus "Disabled"; buttons Refresh, Auto detect, Test, and "Save and connect". The choice is stored in
system_config.json (ejector_port / last_port / vibrator_port + ejector_enabled / modbus_enabled / vibrator_enabled).

Auto detect and Test use port_detect.py: boards are recognised by their REAL replies (ejector v2.3 / old text ejector /
relay). The feeder board never answers, so it is only checked for "port opens". Ports that this program already holds
open are never probed. Nothing is ever fired or switched by these tests.
"""
import json
import os
import threading

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox, QPushButton,
                             QMessageBox, QGroupBox)
from PyQt5.QtCore import QObject, pyqtSignal

import port_detect as pd

STYLE = """
    QDialog { background-color: #1a1a1a; color: #eeeeee; font-family: 'Segoe UI', Arial, sans-serif; }
    QGroupBox { border: 1px solid #383838; border-radius: 8px; margin-top: 14px; background-color: #242424; padding-top: 12px;
                font-weight: bold; }
    QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; padding: 0 10px; color: #00e5ff; }
    QLabel { color: #eeeeee; background: transparent; }
    QPushButton { background-color: #333333; border: 1px solid #555555; border-radius: 6px; padding: 7px 12px;
                  font-weight: bold; color: #ffffff; }
    QPushButton:hover { border-color: #00e5ff; }
    QPushButton:disabled { background-color: #262626; color: #5a5a5a; }
    QComboBox { background-color: #2b2b2b; color: white; border: 1px solid #555; border-radius: 4px; padding: 5px; }
"""
DEVICES = [("ejector", "63-CH EJECTOR (ESP32)", "recognised by its reply: v2.3 binary or the old text firmware"),
           ("relay", "RELAY BOARD (8 relays, Modbus)", "recognised by its Modbus reply (no relay is switched)"),
           ("feeder", "FEEDER (Nuvoton UART board)", "never answers: choose its port by hand; Test only checks that the port opens")]


class _Bus(QObject):
    done = pyqtSignal(str, object)


class PortSettingsDialog(QDialog):
    def __init__(self, esp32, parent=None, config_file="system_config.json"):
        super().__init__(parent)
        self.esp32, self.config_file = esp32, config_file
        self.setWindowTitle("COM PORTS - ejector / relay / feeder")
        self.resize(900, 520)
        self.setStyleSheet(STYLE)
        self.bus = _Bus()
        self.bus.done.connect(self._on_done)
        self.cfg = self._load()
        self.combos, self.status, self.test_btns = {}, {}, {}

        root = QVBoxLayout(self)
        info = QLabel("Choose the port of each device, or press AUTO DETECT. A device set to 'Disabled' is never opened or probed. "
                      "Ports already used by this program are not probed.")
        info.setWordWrap(True)
        root.addWidget(info)
        for dev, title, hint in DEVICES:
            box = QGroupBox(title)
            g = QGridLayout(box)
            combo = QComboBox()
            combo.setMinimumWidth(480)
            g.addWidget(combo, 0, 0)
            tb = QPushButton("Test")
            tb.clicked.connect(lambda _=False, d=dev: self.test(d))
            g.addWidget(tb, 0, 1)
            st = QLabel(hint)
            st.setWordWrap(True)
            st.setStyleSheet("color: #90a4ae;")
            g.addWidget(st, 1, 0, 1, 2)
            self.combos[dev], self.status[dev], self.test_btns[dev] = combo, st, tb
            root.addWidget(box)
        row = QHBoxLayout()
        self.btn_refresh = QPushButton("⟳ Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_auto = QPushButton("🔍 AUTO DETECT")
        self.btn_auto.setStyleSheet("QPushButton { background-color: #007bb5; }")
        self.btn_auto.clicked.connect(self.auto_detect)
        self.btn_save = QPushButton("💾 SAVE AND CONNECT")
        self.btn_save.setStyleSheet("QPushButton { background-color: #2e7d32; }")
        self.btn_save.clicked.connect(self.save_and_connect)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        for b in (self.btn_refresh, self.btn_auto, self.btn_save):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(btn_close)
        root.addLayout(row)
        self.lbl_note = QLabel("")
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet("color: #ffd740;")
        root.addWidget(self.lbl_note)
        self.refresh()

    # ------------------------------------------------------------------ config + helpers
    def _load(self):
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _ejector_up(self):
        lk = getattr(self.esp32, "ejector_link", None)          # the real link (is_ejector_connected() is also true for the relay)
        return bool(lk and lk.is_open)

    def _owned(self):
        """{port: device} for ports THIS program holds open right now (never probed)."""
        e = self.esp32
        owned = {}
        if self._ejector_up() and getattr(e, "ejector_port", None):
            owned[e.ejector_port] = "ejector"
        if e.is_relay_board_connected() and getattr(e, "active_port", None):
            owned[e.active_port] = "relay"
        if getattr(e, "vibrator_serial", None) and getattr(e.vibrator_serial, "is_open", False) and getattr(e, "vibrator_port", None):
            owned[e.vibrator_port] = "feeder"
        return owned

    def refresh(self):
        self.cfg = self._load()
        ports = pd.list_ports()
        owned = self._owned()
        for dev, _t, _h in DEVICES:
            combo = self.combos[dev]
            combo.blockSignals(True)
            keep = combo.currentData() if combo.count() else None
            combo.clear()
            combo.addItem("Disabled", None)
            for p in ports:
                tag = f"   (in use here by the {owned[p.device]})" if p.device in owned else ""
                combo.addItem(p.label + tag, p.device)
            saved = self.cfg.get(pd.CFG_PORT[dev])
            enabled = pd.is_enabled(self.cfg, dev)
            want = keep if keep is not None else (saved if enabled else None)
            idx = combo.findData(want) if want else 0
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        self.lbl_note.setText(f"{len(ports)} COM port(s) found." if ports else "No COM ports found on this PC.")

    def _busy(self, on):
        for b in (self.btn_refresh, self.btn_auto, self.btn_save, *self.test_btns.values()):
            b.setEnabled(not on)

    # ------------------------------------------------------------------ test / auto detect (worker thread)
    def test(self, dev):
        port = self.combos[dev].currentData()
        if not port:
            self._say(dev, "Disabled - nothing is opened or probed.", "#b0bec5")
            return
        owned = self._owned()
        if owned.get(port) == dev:                              # we hold it: report what this program knows
            e = self.esp32
            if dev == "ejector":
                lk = e.ejector_link
                ident = getattr(lk, "identity", {}) or {}
                txt = (f"CONNECTED here: {lk.mode} firmware v{lk.fw_version or '?'}"
                       + (f", pin profile {ident.get('profile')}, board {ident.get('id')}" if ident else "") + f", {lk.baud} baud")
            else:
                txt = f"CONNECTED here on {port}"
            self._say(dev, "✔ " + txt, "#00e676")
            return
        if port in owned:
            self._say(dev, f"✖ {port} is in use here by the {owned[port]} - choose another port.", "#ff5252")
            return
        self._say(dev, "testing ... (a few seconds)", "#ffd740")
        self._busy(True)
        threading.Thread(target=lambda: self.bus.done.emit("test:" + dev, pd.verify(dev, port)), daemon=True).start()

    def auto_detect(self):
        owned = self._owned()
        skip = set(owned)
        feeder_port = self.combos["feeder"].currentData()
        if feeder_port:
            skip.add(feeder_port)                              # the feeder is silent: never send probe bytes to it
        ports = [p for p in pd.list_ports() if p.device not in skip]
        self.lbl_note.setText("Auto detect: probing " + (", ".join(p.device for p in ports) or "(no free ports)") + " ...")
        self._busy(True)
        want_relay = self.combos["relay"].currentData() is not None or True
        threading.Thread(target=lambda: self.bus.done.emit("scan", pd.scan(ports, want_relay=want_relay)), daemon=True).start()

    def _on_done(self, tag, payload):
        self._busy(False)
        if tag.startswith("test:"):
            dev, r = tag[5:], payload
            good = (dev == "ejector" and r.kind in (pd.EJECTOR, pd.LEGACY)) or (dev == "relay" and r.kind == pd.RELAY) \
                or (dev == "feeder" and pd.feeder_ok(r))
            self._say(dev, ("✔ " if good else "✖ ") + f"{r.port}: {r.detail}", "#00e676" if good else "#ff5252")
            return
        found = payload
        lines = []
        for dev in ("ejector", "relay"):
            kinds = (pd.EJECTOR, pd.LEGACY) if dev == "ejector" else (pd.RELAY,)
            cands = [r for _p, r in sorted(found.items(), key=lambda kv: pd._natural(kv[0])) if r.kind in kinds]
            if cands and self._owned().get(self.combos[dev].currentData()) != dev:
                r = cands[0]
                i = self.combos[dev].findData(r.port)
                if i >= 0:
                    self.combos[dev].setCurrentIndex(i)
                self._say(dev, f"✔ found on {r.port}: {r.detail}", "#00e676")
                lines.append(f"{dev}: {r.port}")
            elif not cands and self._owned().get(self.combos[dev].currentData()) != dev:
                self._say(dev, "✖ not found on any free COM port", "#ff5252")
        silent = [p for p, r in found.items() if r.kind == pd.SILENT]
        busy = [p for p, r in found.items() if r.kind in (pd.BUSY, pd.ERROR)]
        note = ("Found: " + ", ".join(lines) if lines else "Nothing new found.")
        if silent:
            note += "   Silent ports (feeder board or unknown device): " + ", ".join(sorted(silent, key=pd._natural))
        if busy:
            note += "   In use by another program: " + ", ".join(sorted(busy, key=pd._natural))
        self.lbl_note.setText(note + "   Press SAVE AND CONNECT to use it.")

    def _say(self, dev, text, colour):
        self.status[dev].setText(text)
        self.status[dev].setStyleSheet(f"color: {colour};")

    # ------------------------------------------------------------------ save + connect
    def save_and_connect(self):
        choice = {dev: self.combos[dev].currentData() for dev, _t, _h in DEVICES}
        used = [p for p in choice.values() if p]
        if len(used) != len(set(used)):
            QMessageBox.warning(self, "Same port twice", "One COM port cannot be used for two devices. Choose different ports.")
            return
        e = self.esp32
        cfg = self._load()
        for dev, port in choice.items():
            cfg[pd.CFG_ENABLED[dev]] = bool(port)
            if port:
                cfg[pd.CFG_PORT[dev]] = port
            elif dev != "ejector":
                cfg[pd.CFG_PORT[dev]] = None
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4)
        except Exception as exc:
            QMessageBox.critical(self, "Not saved", f"Could not write {self.config_file}: {exc}")
            return
        results = []
        # disconnect what changed, then connect (one port is never open for two devices)
        if choice["ejector"] != getattr(e, "ejector_port", None) or not choice["ejector"]:
            e.disconnect_ejector()
        e.relay_enabled, e.relay_port_cfg = bool(choice["relay"]), choice["relay"]
        if choice["relay"] != getattr(e, "active_port", None) or not choice["relay"]:
            e.disconnect()
        if choice["feeder"] != getattr(e, "vibrator_port", None) or not choice["feeder"]:
            try:
                if e.vibrator_serial:
                    e.vibrator_serial.close()
            except Exception:
                pass
            e.vibrator_serial, e.vibrator_port = None, None
        if choice["ejector"] and not self._ejector_up():
            results.append(f"ejector {choice['ejector']}: " + ("connected" if e.connect_ejector(choice["ejector"]) else "NOT connected"))
        if choice["relay"] and not e.is_relay_board_connected():
            results.append(f"relay {choice['relay']}: " + ("connected" if e.connect(choice["relay"]) else "NOT connected"))
        if choice["feeder"] and not (e.vibrator_serial and getattr(e.vibrator_serial, "is_open", False)):
            results.append(f"feeder {choice['feeder']}: " + ("connected" if e.connect_vibrator(choice["feeder"]) else "NOT connected"))
        for dev, port in choice.items():
            if not port:
                results.append(f"{dev}: disabled")
        self.lbl_note.setText("Saved. " + "; ".join(results))
        self.refresh()
