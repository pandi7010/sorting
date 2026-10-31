"""
=============================================================================
LAMBTON OPTICAL SORTER - 63-CHANNEL EJECTOR TEST PANEL
One reusable PyQt5 widget (EjectorTestPanel) for testing the 63 valves.
All communication goes through ejector_link.py (v2.x binary protocol, 115200
baud; the old text firmware is detected automatically as TEXT mode).

Two modes
  standalone : EjectorTestPanel()                       -> opens its own COM port
  embedded   : EjectorTestPanel(link_provider=lambda: self.esp32.ejector_link)
               -> uses the sorter's existing connection; the COM port is never
                  opened twice and the panel never closes the shared link.
               The host calls panel.shutdown() when the panel's dialog/tab closes.
               See MERGE_PLAN.md.

Run alone:
  python ejector_test_panel.py                 (or RUN_EJECTOR_TEST_PANEL.bat)
  python ejector_test_panel.py --port NAME     (pre-select a port, e.g. the name Windows shows in Device Manager)
  Standalone start: the panel looks for the ejector board on ALL COM ports by itself (port_detect: any COM number).
  python ejector_test_panel.py --sim           (simulated ESP32, no hardware)
Close the sorter program first in standalone mode (one program per COM port).

Safety: all valves OFF on STOP ALL / Esc, disconnect, window close and on any
error. Test buttons are disabled while not connected. Slow serial work runs in
a background thread, so the window never freezes.
No camera code.
=============================================================================
"""
import argparse
import os
import queue
import sys
import threading
import time
import traceback

import port_detect                      # same COM port logic as the sorter, SYSTEM_CHECK and the kit
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QPlainTextEdit, QGroupBox, QFrame, QProgressBar, QMessageBox, QShortcut
)
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt5.QtGui import QKeySequence

from ejector_link import EjectorLink, NUM_CHANNELS

# ----------------------------------------------------------------------------- limits
PULSE_MIN_MS, PULSE_MAX_MS, PULSE_DEFAULT_MS = 0.5, 200.0, 10.0    # firmware accepts 0.2 ... 200 ms
GAP_MIN_MS, GAP_MAX_MS, GAP_DEFAULT_MS = 10, 5000, 300
FIRE_ALL_MAX_MS = 50.0          # all 63 valves at once: keep the 24 V supply safe
REPEAT_MAX = 10000
GRID_COLS = 9                   # 9 x 7 = 63 buttons
POLL_MS = 40                    # GUI refresh of link state (TEST_STEP lights, badge)
STATUS_EVERY_MS = 1000          # ESP32 counters request
LIGHT_MIN_S = 0.15              # a lit button stays visible at least this long
LOG_MAX_LINES = 3000

# Same palette as the sorter's own ejector dialog (LAMTON_LINE_NEW_ESP32.py, EjectorTestWindow PREMIUM_QSS)
STYLE = """
    QWidget#EjectorTestPanel { background-color: #1a1a1a; }
    QWidget { color: #eeeeee; font-family: 'Segoe UI', Arial, sans-serif; }
    QGroupBox { border: 1px solid #383838; border-radius: 8px; margin-top: 15px; background-color: #242424;
                padding-top: 15px; font-weight: bold; }
    QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; padding: 0 10px;
                       color: #00e5ff; font-size: 14px; font-weight: bold; }
    QLabel { color: #eeeeee; background: transparent; }
    QPushButton { background-color: #333333; border: 1px solid #555555; border-radius: 6px;
                  padding: 7px 12px; font-weight: bold; color: #ffffff; }
    QPushButton:hover { border-color: #00e5ff; }
    QPushButton:pressed { background-color: #444444; }
    QPushButton:disabled { background-color: #262626; color: #5a5a5a; border-color: #333333; }
    QComboBox, QSpinBox, QDoubleSpinBox { background-color: #2b2b2b; color: white; border: 1px solid #555;
                  border-radius: 4px; padding: 5px; font-size: 13px; font-weight: bold; }
    QPlainTextEdit { background-color: #111111; border: 1px solid #383838; border-radius: 6px;
                     font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; color: #b0b0b0; }
    QProgressBar { background-color: #2b2b2b; border: 1px solid #555; border-radius: 5px;
                   text-align: center; color: #ffffff; font-weight: bold; }
    QProgressBar::chunk { background-color: #007bb5; border-radius: 4px; }
    QCheckBox, QGroupBox::indicator { font-weight: bold; background: transparent; }
    QCheckBox::indicator, QGroupBox::indicator { width: 16px; height: 16px; border: 2px solid #00e5ff;
                         border-radius: 3px; background-color: #2b2b2b; }
    QCheckBox::indicator:checked, QGroupBox::indicator:checked { background-color: #00e5ff; }
    QCheckBox::indicator:disabled { border-color: #555555; }
"""
CH_IDLE = ("QPushButton { background-color: #2b2b2b; border: 1px solid #555555; border-radius: 4px;"
           " padding: 4px; font-size: 13px; }"
           "QPushButton:hover { border-color: #00e5ff; }"
           "QPushButton:disabled { background-color: #222222; color: #4a4a4a; border-color: #333333; }")
CH_LIT = ("QPushButton { background-color: #00e676; color: #000000; border: 2px solid #ffffff;"
          " border-radius: 4px; padding: 4px; font-size: 13px; }")
STOP_STYLE = ("QPushButton { background-color: #d50000; font-size: 18px; font-weight: 900; padding: 14px 10px;"
              " border: 2px solid #ff8a80; }"
              "QPushButton:hover { background-color: #ff1744; }")


class _Worker(QObject):
    """One background thread for serial calls that can wait (connect, status, remap).
    Results come back to the GUI thread through the `done` signal."""
    done = pyqtSignal(str, object, object)          # tag, result, error text

    def __init__(self):
        super().__init__()
        self._q = queue.Queue()
        self._busy = set()
        self._alive = True
        self._t = threading.Thread(target=self._run, name="EjectorPanelWorker", daemon=True)
        self._t.start()

    def submit(self, tag, fn, *args, unique=False):
        if unique and tag in self._busy:
            return False
        self._busy.add(tag)
        self._q.put((tag, fn, args))
        return True

    def is_busy(self, tag):
        return tag in self._busy

    def _run(self):
        while self._alive:
            item = self._q.get()
            if item is None:
                break
            tag, fn, args = item
            try:
                res, err = fn(*args), None
            except Exception as exc:                 # reported to the GUI, which then stops all valves
                res, err = None, f"{exc.__class__.__name__}: {exc}"
            self._busy.discard(tag)
            if self._alive:
                self.done.emit(tag, res, err)

    def stop(self):
        self._alive = False
        self._q.put(None)


class EjectorTestPanel(QWidget):
    """63-channel ejector test panel. See the module docstring for the two modes."""

    connection_changed = pyqtSignal(bool)

    def __init__(self, link_provider=None, parent=None, link_factory=None, preferred_port=None, sim_port=False):
        super().__init__(parent)
        self.setObjectName("EjectorTestPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self.embedded = link_provider is not None
        self._link_provider = link_provider
        self._link_factory = link_factory or (lambda port: EjectorLink(port))
        self._preferred_port = preferred_port
        self._sim_port = sim_port                    # adds a 'SIM' entry to the port list
        self._own_link = None
        self._connected = False
        self._disconnecting = False
        self._closed = False
        self._confirm = self._ask_confirm            # tests replace this
        self._run = None                             # active test: dict(kind, total, step0, done0, loops, ...)
        self._lit = {}                               # channel -> time to switch the light off
        self._seen_steps = 0
        self._seen_done = 0
        self._seen_text = 0
        self._last_state = None
        self._last_armed = False
        self._remap = None
        self._last_status = {}
        self._status_t = 0.0

        self._worker = _Worker()
        self._worker.done.connect(self._on_job_done)

        self._build_ui()
        self.refresh_ports()
        self._set_connected_ui(False)
        if not self.embedded and not self._sim_port:
            QTimer.singleShot(400, self.auto_detect)      # standalone: find the ejector on any COM port by itself

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(POLL_MS)

        # Esc = STOP ALL while the focus is anywhere inside the panel. Only ONE Esc shortcut may exist:
        # two active shortcuts on the same key are "ambiguous" in Qt and then NEITHER fires.
        self.esc_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.esc_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.esc_shortcut.activated.connect(self.stop_all)

        self.log("Ejector test panel ready (" + ("EMBEDDED: shared sorter connection" if self.embedded
                                                 else "STANDALONE: choose a COM port and CONNECT") + ").")

    # ========================================================================= UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(12, 8, 12, 8)
        self._test_widgets = []                      # enabled only while connected

        # ---- 1. connection ----
        conn = QFrame()
        conn.setObjectName("connBar")
        conn.setStyleSheet("QFrame#connBar { background-color: #242424; border: 1px solid #383838; border-radius: 8px; }")
        h = QHBoxLayout(conn)
        h.setContentsMargins(10, 6, 10, 6)
        title = QLabel("63-CH EJECTOR TEST")
        title.setStyleSheet("font-size: 16px; font-weight: 900; color: #00e5ff; letter-spacing: 1px;")
        h.addWidget(title)
        h.addStretch()
        self.lbl_port = QLabel("COM Port:")
        h.addWidget(self.lbl_port)
        self.combo_ports = QComboBox()
        self.combo_ports.setMinimumWidth(250)
        h.addWidget(self.combo_ports)
        self.btn_refresh = QPushButton("⟳ Refresh")
        self.btn_refresh.clicked.connect(self.refresh_ports)
        h.addWidget(self.btn_refresh)
        self.btn_detect = QPushButton("🔍 Auto detect")
        self.btn_detect.setToolTip("Find the ejector board on any COM port (nothing is fired).")
        self.btn_detect.clicked.connect(self.auto_detect)
        h.addWidget(self.btn_detect)
        self.btn_connect = QPushButton("⚡ CONNECT")
        self.btn_connect.clicked.connect(self.toggle_connect)
        h.addWidget(self.btn_connect)
        self.lbl_shared = QLabel("Shared connection (sorter program)")
        self.lbl_shared.setStyleSheet("color: #90a4ae; font-weight: bold;")
        h.addWidget(self.lbl_shared)
        for w in (self.lbl_port, self.combo_ports, self.btn_refresh, self.btn_detect, self.btn_connect):
            w.setVisible(not self.embedded)
        self.lbl_shared.setVisible(self.embedded)
        self.badge = QLabel("● DISCONNECTED")
        self.badge.setMinimumWidth(260)
        h.addWidget(self.badge)
        root.addWidget(conn)

        # ---- 2. settings ----
        sets = QGroupBox("PULSE SETTINGS (used by every test)")
        s = QHBoxLayout(sets)
        s.addWidget(QLabel("Pulse (ms):"))
        self.spin_pulse = QDoubleSpinBox()
        self.spin_pulse.setRange(PULSE_MIN_MS, PULSE_MAX_MS)
        self.spin_pulse.setDecimals(1)
        self.spin_pulse.setSingleStep(0.5)
        self.spin_pulse.setValue(PULSE_DEFAULT_MS)
        self.spin_pulse.setToolTip(f"How long the valve is ON ({PULSE_MIN_MS:g} - {PULSE_MAX_MS:g} ms).")
        s.addWidget(self.spin_pulse)
        s.addSpacing(20)
        s.addWidget(QLabel("Gap / delay between pulses (ms):"))
        self.spin_gap = QSpinBox()
        self.spin_gap.setRange(GAP_MIN_MS, GAP_MAX_MS)
        self.spin_gap.setSingleStep(10)
        self.spin_gap.setValue(GAP_DEFAULT_MS)
        self.spin_gap.setToolTip(f"Pause after each pulse in single and auto tests ({GAP_MIN_MS} - {GAP_MAX_MS} ms).")
        s.addWidget(self.spin_gap)
        s.addStretch()
        root.addWidget(sets)

        # ---- 3. grid (left) + tests (right) ----
        mid = QHBoxLayout()
        grid_box = QGroupBox("CHANNELS 1 - 63   (click = one pulse;  lights up while the ESP32 fires it)")
        g = QGridLayout(grid_box)
        g.setSpacing(4)
        self.channel_buttons = {}
        for ch in range(1, NUM_CHANNELS + 1):
            b = QPushButton(str(ch))
            b.setMinimumSize(48, 32)
            b.setStyleSheet(CH_IDLE)
            b.setToolTip(f"CH {ch}")
            b.clicked.connect(lambda _=False, c=ch: self.fire_channel(c))
            g.addWidget(b, (ch - 1) // GRID_COLS, (ch - 1) % GRID_COLS)
            self.channel_buttons[ch] = b
            self._test_widgets.append(b)
        mid.addWidget(grid_box, 3)

        right = QVBoxLayout()
        single = QGroupBox("SINGLE TEST")
        sl = QGridLayout(single)
        sl.addWidget(QLabel("Channel:"), 0, 0)
        self.spin_single_ch = QSpinBox()
        self.spin_single_ch.setRange(1, NUM_CHANNELS)
        sl.addWidget(self.spin_single_ch, 0, 1)
        sl.addWidget(QLabel("Repeat:"), 1, 0)
        self.spin_repeat = QSpinBox()
        self.spin_repeat.setRange(1, REPEAT_MAX)
        self.spin_repeat.setValue(5)
        sl.addWidget(self.spin_repeat, 1, 1)
        self.btn_single = QPushButton("▶ START SINGLE")
        self.btn_single.setStyleSheet("QPushButton { background-color: #007bb5; } QPushButton:hover { background-color: #0091d5; }")
        self.btn_single.clicked.connect(self.start_single)
        sl.addWidget(self.btn_single, 2, 0, 1, 2)
        self._test_widgets += [self.spin_single_ch, self.spin_repeat, self.btn_single]
        right.addWidget(single)

        auto = QGroupBox("AUTO SEQUENCE")
        al = QGridLayout(auto)
        al.addWidget(QLabel("From CH:"), 0, 0)
        self.spin_from = QSpinBox()
        self.spin_from.setRange(1, NUM_CHANNELS)
        al.addWidget(self.spin_from, 0, 1)
        al.addWidget(QLabel("To CH:"), 1, 0)
        self.spin_to = QSpinBox()
        self.spin_to.setRange(1, NUM_CHANNELS)
        self.spin_to.setValue(NUM_CHANNELS)
        al.addWidget(self.spin_to, 1, 1)
        self.chk_loop = QCheckBox("Loop continuously")
        al.addWidget(self.chk_loop, 2, 0, 1, 2)
        self.btn_auto = QPushButton("▶ START AUTO")
        self.btn_auto.setStyleSheet("QPushButton { background-color: #6a1b9a; } QPushButton:hover { background-color: #8e24aa; }")
        self.btn_auto.clicked.connect(self.start_auto)
        al.addWidget(self.btn_auto, 3, 0, 1, 2)
        self._test_widgets += [self.spin_from, self.spin_to, self.chk_loop, self.btn_auto]
        right.addWidget(auto)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("idle")
        right.addWidget(self.progress)

        self.btn_fire_all = QPushButton(f"💥 FIRE ALL 63  (max {FIRE_ALL_MAX_MS:g} ms)")
        self.btn_fire_all.setStyleSheet("QPushButton { background-color: #ff6f00; } QPushButton:hover { background-color: #ffa000; }")
        self.btn_fire_all.setToolTip("Fires all 63 valves together once. Asks for confirmation.\n"
                                     f"The pulse is limited to {FIRE_ALL_MAX_MS:g} ms to protect the 24 V supply.")
        self.btn_fire_all.clicked.connect(self.fire_all)
        right.addWidget(self.btn_fire_all)
        self._test_widgets.append(self.btn_fire_all)

        self.btn_stop = QPushButton("🛑 STOP ALL  (Esc)")
        self.btn_stop.setStyleSheet(STOP_STYLE)
        self.btn_stop.setToolTip("Stops any test and switches all valves OFF immediately. Always enabled.")
        self.btn_stop.clicked.connect(self.stop_all)
        right.addWidget(self.btn_stop)
        right.addStretch()
        mid.addLayout(right, 1)
        root.addLayout(mid)

        # ---- 4. counters ----
        cnt = QFrame()
        cnt.setObjectName("counterBar")
        cnt.setStyleSheet("QFrame#counterBar { background-color: #242424; border: 1px solid #383838; border-radius: 8px; }")
        c = QHBoxLayout(cnt)
        c.setContentsMargins(10, 4, 10, 4)
        self.lbl_counters = QLabel("fired -   crc_err -   len_err -   no-ack -   watchdog trips -")
        self.lbl_counters.setStyleSheet("font-family: Consolas, monospace; font-size: 13px; color: #b0bec5;")
        self.lbl_counters.setToolTip("fired / crc_err / len_err / watchdog trips: counted by the ESP32 since power-up.\n"
                                     "no-ack: messages this PC sent without an answer within 100 ms.")
        c.addWidget(self.lbl_counters)
        c.addStretch()
        self.lbl_heartbeat = QLabel("♥ heartbeat -")
        self.lbl_heartbeat.setStyleSheet("font-family: Consolas, monospace; font-size: 13px; color: #90a4ae;")
        c.addWidget(self.lbl_heartbeat)
        root.addWidget(cnt)

        # ---- 5. remap (optional, collapsed) ----
        self.remap_box = QGroupBox("CHANNEL REMAP (optional - fix crossed wires in software)")
        self.remap_box.setCheckable(True)
        self.remap_box.setChecked(False)
        rl = QVBoxLayout(self.remap_box)
        self.remap_inner = QWidget()
        ri = QHBoxLayout(self.remap_inner)
        ri.setContentsMargins(0, 0, 0, 0)
        self.btn_map_read = QPushButton("📖 Read map")
        self.btn_map_read.clicked.connect(self.read_map)
        ri.addWidget(self.btn_map_read)
        ri.addSpacing(10)
        ri.addWidget(QLabel("Swap CH"))
        self.spin_swap_a = QSpinBox()
        self.spin_swap_a.setRange(1, NUM_CHANNELS)
        ri.addWidget(self.spin_swap_a)
        ri.addWidget(QLabel("with CH"))
        self.spin_swap_b = QSpinBox()
        self.spin_swap_b.setRange(1, NUM_CHANNELS)
        self.spin_swap_b.setValue(2)
        ri.addWidget(self.spin_swap_b)
        self.btn_swap = QPushButton("⇄ Swap")
        self.btn_swap.clicked.connect(self.swap_channels)
        ri.addWidget(self.btn_swap)
        ri.addSpacing(10)
        self.btn_map_save = QPushButton("💾 Save to ESP32")
        self.btn_map_save.setToolTip("Stores the current map in the ESP32 flash (kept after power-off).")
        self.btn_map_save.clicked.connect(self.save_map)
        ri.addWidget(self.btn_map_save)
        self.btn_map_default = QPushButton("↺ Restore default")
        self.btn_map_default.setToolTip("CH n -> OUT n-1 for all channels. Press Save to keep it after power-off.")
        self.btn_map_default.clicked.connect(self.default_map)
        ri.addWidget(self.btn_map_default)
        ri.addStretch()
        rl.addWidget(self.remap_inner)
        self.lbl_map = QLabel("Map not read yet.")
        self.lbl_map.setWordWrap(True)
        self.lbl_map.setStyleSheet("font-family: Consolas, monospace; color: #b0bec5;")
        rl.addWidget(self.lbl_map)
        self.remap_box.toggled.connect(self.remap_inner.setVisible)
        self.remap_box.toggled.connect(self.lbl_map.setVisible)
        self.remap_inner.setVisible(False)
        self.lbl_map.setVisible(False)
        self._remap_widgets = [self.btn_map_read, self.spin_swap_a, self.spin_swap_b, self.btn_swap,
                               self.btn_map_save, self.btn_map_default]
        root.addWidget(self.remap_box)

        # ---- 6. log ----
        log_box = QGroupBox("LOG")
        ll = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LOG_MAX_LINES)
        self.log_view.setMinimumHeight(70)
        ll.addWidget(self.log_view)
        row = QHBoxLayout()
        row.addStretch()
        btn_clear = QPushButton("🧹 Clear log")
        btn_clear.clicked.connect(self.log_view.clear)
        row.addWidget(btn_clear)
        ll.addLayout(row)
        root.addWidget(log_box, 1)

    # ========================================================================= helpers
    def link(self):
        """The EjectorLink in use (shared one in embedded mode), or None."""
        if self.embedded:
            try:
                return self._link_provider()
            except Exception:
                return None
        return self._own_link

    def _open_link(self):
        lk = self.link()
        if lk is None or not lk.is_open:
            return None
        if lk.mode == "BINARY" and lk.esp_state == "TRIPPED":
            lk.arm()                      # only firmware v2.0 - v2.2 can be TRIPPED; v2.3 arms itself on any command
        return lk

    def log(self, text):
        self.log_view.appendPlainText(time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}  {text}")

    def _ask_confirm(self, title, text):
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def _guard(self, what, fn):
        """Runs a button action; on any error: log it and switch all valves off."""
        try:
            return fn()
        except Exception as exc:
            self.log(f"ERROR in {what}: {exc.__class__.__name__}: {exc}")
            traceback.print_exc()
            self._emergency_off()
            return None

    def _emergency_off(self):
        self._run = None
        lk = self.link()
        if lk is not None and lk.is_open:
            try:
                lk.test_stop()
                lk.stop_all()
                self.log("All valves OFF (safety).")
            except Exception as exc:
                self.log(f"Could not send STOP: {exc}")

    def _light(self, ch, seconds):
        b = self.channel_buttons.get(ch)
        if b:
            b.setStyleSheet(CH_LIT)
            self._lit[ch] = max(self._lit.get(ch, 0), time.time() + max(seconds, LIGHT_MIN_S))

    def _clear_lights(self):
        for ch in list(self._lit):
            self.channel_buttons[ch].setStyleSheet(CH_IDLE)
        self._lit.clear()

    def _pulse_ms(self):
        return float(self.spin_pulse.value())

    def _gap_ms(self):
        return float(self.spin_gap.value())

    # ========================================================================= connection
    def refresh_ports(self):
        cur = self.combo_ports.currentData() or self._preferred_port
        self.combo_ports.clear()
        best = None
        for p in port_detect.list_ports():
            self.combo_ports.addItem(p.label, p.device)           # "COM13 [Silicon Labs CP210x] - description"
            if best is None and p.chip:
                best = p.device
        if self._sim_port:
            self.combo_ports.addItem("SIM - simulated ESP32 (no hardware)", "SIM")
        for want in (("SIM",) if self._sim_port else ()) + (cur, best):   # simulation never picks real hardware
            i = self.combo_ports.findData(want) if want else -1
            if i >= 0:
                self.combo_ports.setCurrentIndex(i)
                break

    def auto_detect(self):
        """Ask every COM port who it is (text PING / IDENTIFY, 115200 then 921600) and select the ejector board.
        Nothing is fired. Runs in the background; the result arrives in _on_job_done('detect')."""
        if self.embedded or self._sim_port or self._own_link is not None:
            return
        own = self._own_link.port if self._own_link else None

        def job():
            ports = [p for p in port_detect.list_ports() if p.device != own]
            return port_detect.scan(ports, want_relay=False)
        if self._worker.submit("detect", job, unique=True):
            self.log("Auto detect: asking every COM port who it is ...")

    def toggle_connect(self):
        if self.embedded:
            return
        if self._own_link is not None:
            self.disconnect_link()
        else:
            self.connect_link()

    def connect_link(self, port=None):
        port = port or self.combo_ports.currentData()
        if not port:
            self.log("No COM port selected.")
            return
        if self._worker.is_busy("connect"):
            return
        self.btn_connect.setEnabled(False)
        self.btn_connect.setText("… connecting")
        self.log(f"Connecting to {port} (firmware auto-detect, up to 5 s) ...")

        def job():
            lk = self._link_factory(port)
            return lk, lk.connect(), port
        self._worker.submit("connect", job)

    def disconnect_link(self):
        lk = self._own_link
        if lk is None:
            return
        self._disconnecting = True
        self._run = None
        self._safe_off(lk)
        self._own_link = None
        self.log("Disconnected (all valves OFF, ESP32 disarmed cleanly - no watchdog trip).")
        self._worker.submit("close", lk.close, True)

    def _safe_off(self, lk):
        try:
            if lk is not None and lk.is_open:
                lk.test_stop()
                lk.stop_all()
        except Exception:
            pass

    # ========================================================================= tests
    def fire_channel(self, ch):
        def act():
            lk = self._open_link()
            if not lk:
                return
            self._run = None
            pulse = self._pulse_ms()
            lk.fire([ch], pulse)
            self._light(ch, pulse / 1000.0)
            self.log(f"Pulse CH {ch} ({pulse:g} ms)   {lk.channel_info(ch)}")
        self._guard("channel pulse", act)

    def start_single(self):
        def act():
            lk = self._open_link()
            if not lk:
                return
            ch, rep = self.spin_single_ch.value(), self.spin_repeat.value()
            pulse, gap = self._pulse_ms(), self._gap_ms()
            self._start_run("single", total=rep, label=f"Single CH {ch}")
            lk.test_single(ch, pulse, gap, repeat=rep)
            self.log(f"SINGLE TEST CH {ch}: {rep} x {pulse:g} ms, gap {gap:g} ms   {lk.channel_info(ch)}")
        self._guard("single test", act)

    def start_auto(self):
        def act():
            lk = self._open_link()
            if not lk:
                return
            a, b = self.spin_from.value(), self.spin_to.value()
            if a > b:
                self.log(f"AUTO: 'From' ({a}) must not be larger than 'To' ({b}).")
                return
            pulse, gap = self._pulse_ms(), self._gap_ms()
            self._start_run("auto", total=b - a + 1, label=f"Auto CH {a}-{b}", a=a, b=b,
                            loop=self.chk_loop.isChecked())
            lk.test_sweep(a, b, pulse, gap)
            self.log(f"AUTO SEQUENCE CH {a} -> {b}: pulse {pulse:g} ms, gap {gap:g} ms"
                     + (", looping until STOP" if self.chk_loop.isChecked() else ""))
        self._guard("auto sequence", act)

    def _start_run(self, kind, total, label, **extra):
        lk = self.link()
        self._run = dict(kind=kind, total=total, label=label, loops=1,
                         step0=lk.counters.get("event_test_step", 0) if lk else 0,
                         done0=lk.test_done_count if lk else 0, legacy=bool(lk and lk.mode != "BINARY"), **extra)
        self.progress.setRange(0, total)
        self.progress.setValue(0)
        self.progress.setFormat(f"{label}: starting ...")

    def fire_all(self):
        def act():
            lk = self._open_link()
            if not lk:
                return
            pulse = min(self._pulse_ms(), FIRE_ALL_MAX_MS)
            note = (f"\n(Pulse setting {self._pulse_ms():g} ms is limited to {FIRE_ALL_MAX_MS:g} ms for FIRE ALL.)"
                    if self._pulse_ms() > FIRE_ALL_MAX_MS else "")
            if not self._confirm("FIRE ALL 63 VALVES",
                                 f"Fire ALL 63 valves together for {pulse:g} ms?{note}\n\n"
                                 "With air connected this blows hard on the whole chute.\n"
                                 "Make sure nobody is near the nozzles."):
                self.log("FIRE ALL cancelled.")
                return
            self._run = None
            lk.fire(range(1, NUM_CHANNELS + 1), pulse)
            for ch in self.channel_buttons:
                self._light(ch, pulse / 1000.0)
            self.log(f"FIRE ALL 63 channels, {pulse:g} ms.")
        self._guard("FIRE ALL", act)

    def stop_all(self):
        """STOP ALL / Esc: always allowed. Stops the test and switches every valve off."""
        self._run = None
        self._clear_lights()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("stopped")
        lk = self.link()
        if lk is None or not lk.is_open:
            self.log("STOP ALL: not connected (nothing to stop).")
            return
        try:
            lk.test_stop()
            lk.stop_all()
            self.log("STOP ALL: test stopped, all valves OFF.")
        except Exception as exc:
            self.log(f"STOP ALL could not be sent: {exc}")

    # ========================================================================= remap
    def _binary_link(self, what):
        lk = self._open_link()
        if not lk:
            return None
        if lk.mode != "BINARY":
            self.log(f"{what}: needs the v2.x binary firmware (old text firmware has a fixed map).")
            return None
        return lk

    def read_map(self):
        lk = self._binary_link("Read map")
        if lk:
            self._worker.submit("remap_read", lk.request_remap, 1.0, unique=True)

    def swap_channels(self):
        def act():
            lk = self._binary_link("Swap")
            if not lk:
                return
            a, b = self.spin_swap_a.value(), self.spin_swap_b.value()
            if a == b:
                self.log("Swap: choose two different channels.")
                return
            table = list(lk.remap)
            if any(v is None for v in table) and self._remap is None:
                self.log("Swap: press 'Read map' first.")
                return
            table[a - 1], table[b - 1] = table[b - 1], table[a - 1]
            self._run = None
            lk.set_remap(table)
            self._remap = table
            self._show_map(table)
            self.log(f"Swapped CH {a} <-> CH {b}: {lk.channel_info(a)} | {lk.channel_info(b)}  "
                     "(active now, NOT saved - press 'Save to ESP32' to keep it after power-off)")
        self._guard("swap", act)

    def save_map(self):
        def act():
            lk = self._binary_link("Save map")
            if lk:
                lk.save_remap()
                self.log("Map saved to ESP32 flash.")
        self._guard("save map", act)

    def default_map(self):
        lk = self._binary_link("Restore default")
        if lk:
            self._run = None
            self._worker.submit("remap_default", lk.default_remap, unique=True)

    def _show_map(self, table):
        moved = [f"CH{c}->OUT{o}" if o is not None else f"CH{c}->OFF"
                 for c, o in enumerate(table, start=1) if o != c - 1]
        self.lbl_map.setText("Map: all channels default (CH n -> OUT n-1)." if not moved
                             else f"Map: {len(moved)} changed: " + "  ".join(moved))
        lk = self.link()
        for ch, b in self.channel_buttons.items():
            b.setToolTip(lk.channel_info(ch) if lk else f"CH {ch}")

    # ========================================================================= background results
    def _on_job_done(self, tag, res, err):
        if tag == "detect" and not self._closed:
            if err or not isinstance(res, dict):
                self.log(f"Auto detect failed: {err}")
                return
            found = [r for _p, r in sorted(res.items(), key=lambda kv: port_detect._natural(kv[0]))
                     if r.kind in (port_detect.EJECTOR, port_detect.LEGACY)]
            busy = [r.port for r in res.values() if r.kind in (port_detect.BUSY, port_detect.ERROR)]
            if found:
                r = found[0]
                i = self.combo_ports.findData(r.port)
                if i >= 0:
                    self.combo_ports.setCurrentIndex(i)
                self.log(f"Auto detect: EJECTOR found on {r.port} - {r.detail}"
                         + (f"   (also answering: {', '.join(x.port for x in found[1:])})" if len(found) > 1 else ""))
            else:
                self.log("Auto detect: no ejector board found on any free COM port."
                         + (f" In use by another program: {', '.join(busy)}." if busy else ""))
            return
        if self._closed:
            if tag == "connect" and res and res[0] is not None:
                try:
                    res[0].close(disarm=True)
                except Exception:
                    pass
            return
        if err:
            self.log(f"ERROR ({tag}): {err}")
            self._emergency_off()
        if tag == "connect":
            self.btn_connect.setEnabled(True)
            if err or not res:
                self._set_connected_ui(False)
                return
            lk, mode, port = res
            if mode:
                self._own_link = lk
                self._disconnecting = False
                if mode == "BINARY":
                    self.log(f"Connected to {port}: firmware v{lk.fw_version}, BINARY protocol, "
                             "heartbeat every 100 ms, 500 ms watchdog ARMED.")
                    self._remap = list(lk.remap) if all(v is not None for v in lk.remap) else None
                else:
                    self.log(f"Connected to {port}: OLD text firmware (TEXT mode, no watchdog, no counters).")
            else:
                self.log(f"No ejector firmware answered on {port}. Is the port used by another program "
                         "(sorter, Arduino Serial Monitor)?")
                for line in list(lk.text_lines)[-3:]:
                    self.log("   " + line)
                try:
                    lk.close(disarm=False)
                except Exception:
                    pass
                self._set_connected_ui(False)
        elif tag == "status" and isinstance(res, dict) and res:
            self._last_status = res
        elif tag in ("remap_read", "remap_default") and res is not None:
            self._remap = list(res)
            self._show_map(self._remap)
            if tag == "remap_read":
                self.log("Map read from ESP32. " + self.lbl_map.text())
            else:
                self.log("Default map restored (active now, NOT saved - press 'Save to ESP32' to keep it).")

    # ========================================================================= periodic refresh
    def _set_connected_ui(self, on):
        self._connected = on
        for w in self._test_widgets + self._remap_widgets:
            w.setEnabled(on)
        if not self.embedded:
            self.btn_connect.setText("✖ DISCONNECT" if on else "⚡ CONNECT")
            self.btn_connect.setStyleSheet(
                "QPushButton { background-color: #d50000; font-weight: 900; }" if on else
                "QPushButton { background-color: #00c853; color: #000000; font-weight: 900; }"
                "QPushButton:hover { background-color: #69f0ae; }")
            self.combo_ports.setEnabled(not on)
            self.btn_refresh.setEnabled(not on)
            self.btn_detect.setEnabled(not on)
        if not on:
            self._run = None
            self._clear_lights()
            self.badge.setText("● DISCONNECTED" if not self.embedded else "● SORTER NOT CONNECTED TO EJECTOR")
            self.badge.setStyleSheet("color: #ff5252; font-weight: bold; font-size: 13px;")
            self.lbl_heartbeat.setText("♥ heartbeat -")
            self.progress.setFormat("idle")
        self.connection_changed.emit(on)

    def _poll(self):
        try:
            self._poll_inner()
        except Exception as exc:                     # never let the timer die silently
            self.log(f"ERROR in refresh: {exc}")
            self._emergency_off()

    def _poll_inner(self):
        now = time.time()
        for ch, t_off in list(self._lit.items()):
            if now >= t_off:
                self.channel_buttons[ch].setStyleSheet(CH_IDLE)
                del self._lit[ch]

        lk = self.link()
        up = lk is not None and lk.is_open
        if up != self._connected:
            if not up and self._connected and not self._disconnecting:
                self.log("⚠ Connection to the ESP32 LOST (USB cable?). The ESP32 watchdog switches all valves "
                         "OFF by itself within 0.5 s.")
                if not self.embedded and self._own_link is not None:
                    dead, self._own_link = self._own_link, None
                    self._worker.submit("close", dead.close, False)
            self._set_connected_ui(up)
            if up:
                self._seen_steps = lk.counters.get("event_test_step", 0)
                self._seen_done = lk.test_done_count
                self._seen_text = lk.counters.get("text_lines", 0)
                self._last_state = None
        if not up:
            return

        binary = lk.mode == "BINARY"
        state = lk.esp_state if binary else "TEXT MODE"
        if state != self._last_state:
            self._last_state = state
            if state == "DISARMED" and binary and self._last_armed:
                self.log("ESP32 idle: outputs were switched OFF after the PC was silent (500 ms). "
                         "The next click arms it again automatically.")
                self._run = None
                self._clear_lights()
            elif state == "ARMED" and binary:
                self.log("ESP32 ARMED.")
            self._last_armed = (state == "ARMED")
        tripped = state == "TRIPPED"             # only firmware v2.0 - v2.2; the panel re-arms it by itself on the next click
        if binary:
            shown = {"ARMED": "ARMED", "DISARMED": "IDLE (arms itself on the next click)",
                     "TRIPPED": "TRIPPED (old firmware, re-arms on the next click)"}.get(state, state or "?")
            self.badge.setText(f"● v{lk.fw_version} BINARY · {shown}")
            col = "#ffd740" if tripped else "#00e676" if state in ("ARMED", "DISARMED") else "#ffd740"
        else:
            self.badge.setText("● OLD FIRMWARE · TEXT MODE (no watchdog)")
            col = "#ffd740"
        self.badge.setStyleSheet(f"color: {col}; font-weight: bold; font-size: 13px;")

        # ESP32 text lines (e.g. "[TEST] CH 5 -> OUT 4 -> CHIP 1, Q4") into the log
        n_text = lk.counters.get("text_lines", 0)
        if n_text < self._seen_text:
            self._seen_text = n_text
        new_text = n_text - self._seen_text
        if new_text:
            lines = list(lk.text_lines)[-min(new_text, 50):]
            for line in lines:
                if not line.startswith("[LINK]"):
                    self.log("ESP32: " + line)
            self._seen_text = n_text

        # TEST_STEP events -> light the active channel + progress
        steps = lk.counters.get("event_test_step", 0)
        if steps < self._seen_steps:                 # counters were reset by the host
            self._seen_steps = steps
        if steps > self._seen_steps:
            self._seen_steps = steps
            if lk.last_test_step:
                ch = lk.last_test_step[0]
                self._light(ch, self._pulse_ms() / 1000.0)
        done = lk.test_done_count
        if done < self._seen_done:
            self._seen_done = done
        run = self._run
        if run:
            if run["legacy"]:
                self.progress.setFormat(f"{run['label']}: running (old firmware sends no progress)")
            else:
                n = steps - run["step0"]
                pos = n - (run["loops"] - 1) * run["total"] if run["kind"] == "auto" else n
                cur = lk.last_test_step[0] if lk.last_test_step else "-"
                self.progress.setValue(max(0, min(pos, run["total"])))
                loop_txt = f" · loop {run['loops']}" if run.get("loop") else ""
                self.progress.setFormat(f"{run['label']}: CH {cur}  ({max(0, min(pos, run['total']))}"
                                        f"/{run['total']}){loop_txt}")
                if done > run["done0"] + (run["loops"] - 1):
                    if run["kind"] == "auto" and run.get("loop") and self.chk_loop.isChecked():
                        run["loops"] += 1
                        lk.test_sweep(run["a"], run["b"], self._pulse_ms(), self._gap_ms())
                    else:
                        self.progress.setValue(run["total"])
                        self.progress.setFormat(f"{run['label']}: DONE")
                        self.log(f"{run['label']}: DONE" + (f" ({run['loops']} loops)" if run["loops"] > 1 else ""))
                        self._run = None
        self._seen_done = done

        # counters (ESP32 STATUS every second, in the background)
        if binary and now - self._status_t > STATUS_EVERY_MS / 1000.0:
            self._status_t = now
            self._worker.submit("status", lk.request_status, 0.5, unique=True)
        st = self._last_status if binary else {}

        def v(k):
            return st.get(k, "-")
        self.lbl_counters.setText(f"fired {v('fired')}   crc_err {v('crc_err')}   len_err {v('len_err')}   "
                                  f"no-ack {lk.counters.get('no_ack', 0)}   watchdog trips {v('wdt_trips')}")
        if binary:
            hb_ok = not tripped and not lk.heartbeat_paused
            self.lbl_heartbeat.setText(f"♥ heartbeat {'OK' if hb_ok else 'STOPPED'} · "
                                       f"{lk.counters.get('ack_ok', 0)} ACKs")
            self.lbl_heartbeat.setStyleSheet("font-family: Consolas, monospace; font-size: 13px; color: "
                                             + ("#00e676;" if hb_ok else "#ff5252;"))
        else:
            self.lbl_heartbeat.setText("♥ no heartbeat (old firmware)")

    # ========================================================================= shutdown
    def shutdown(self):
        """All valves OFF; standalone: close the own COM port. Embedded: the shared link stays open.
        Call it when the window/dialog/tab that holds the panel closes."""
        if self._closed:
            return
        self._closed = True
        self._poll_timer.stop()
        self._run = None
        lk = self.link()
        self._safe_off(lk)
        if not self.embedded and self._own_link is not None:
            try:
                self._own_link.close(disarm=True)
            except Exception:
                pass
            self._own_link = None
        self._worker.stop()


class EjectorTestWindow(QMainWindow):
    """Standalone window around the panel (python ejector_test_panel.py)."""

    def __init__(self, sim=False, port=None):
        super().__init__()
        self.setWindowTitle("Lambton Optical Sorter - 63-CH Ejector Test Panel" + ("  [SIMULATION]" if sim else ""))
        self.resize(1250, 900)
        factory = None
        if sim:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
            from ejector_sim import SimEsp32

            def factory(p):
                lk = EjectorLink(p)
                lk.serial_factory = SimEsp32.factory
                return lk
        self.panel = EjectorTestPanel(link_factory=factory, preferred_port=port, sim_port=sim)
        self.setCentralWidget(self.panel)
        self.setStyleSheet("QMainWindow { background-color: #1a1a1a; }")
        self.panel.esc_shortcut.setContext(Qt.WindowShortcut)   # Esc anywhere in this window

    def closeEvent(self, ev):
        self.panel.shutdown()
        super().closeEvent(ev)


def main():
    ap = argparse.ArgumentParser(description="63-channel ejector test panel")
    ap.add_argument("--sim", action="store_true", help="use a simulated ESP32 (no hardware)")
    ap.add_argument("--port", help="pre-select this COM port (the name Windows shows)")
    args = ap.parse_args()
    app = QApplication(sys.argv)
    win = EjectorTestWindow(sim=args.sim, port=args.port)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
