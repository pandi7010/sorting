"""
=============================================================================
LAMBTON OPTICAL SORTER - ESP32 63-CHANNEL EJECTOR DIAGNOSTIC GUI  (Phase 1)
Works with:
  - ESP32_63CH_Ejector_v2_0 (binary protocol, watchdog, remap)  <- recommended
  - old ESP32_63CH_ShiftRegister firmware (legacy text mode, auto-detected)

Features:
- Port selector & Connect/Disconnect (firmware auto-detect)
- 63 channel buttons: click = fire that valve once (pulse from settings)
- SINGLE TEST: selected channel, N repeats with pulse + gap (runs on the ESP32)
- SWEEP TEST: channel FROM..TO with pulse + gap; every step shows CH -> OUT -> CHIP/Q
- Remap table: swap / disable channels, save to ESP32 flash, reset to default
- Watchdog test: pauses the PC heartbeat to prove all valves switch off after 500 ms
- Live counters: ACKs, errors, round-trip time, ESP32 state
- Console with all text from the ESP32 + manual text commands (HELP lists them)

Run:  python gui_esp32_ejector_tester.py
Close the sorter program first (only one program can open the COM port).
=============================================================================
"""

import sys
import time
import html

import serial.tools.list_ports
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QPushButton, QLabel, QComboBox, QSpinBox, QDoubleSpinBox,
    QTextEdit, QLineEdit, QGroupBox, QFrame
)
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt5.QtGui import QTextCursor

from ejector_link import EjectorLink, NUM_CHANNELS


class LinkSignals(QObject):
    """Moves link callbacks (reader thread) onto the GUI thread."""
    text = pyqtSignal(str)
    event = pyqtSignal(str, int)


class EjectorDiagnosticApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lambton Optical Sorter - ESP32 63-CH Ejector Diagnostics (v2.0)")
        self.resize(1150, 900)
        self.setMinimumSize(980, 760)

        self.link = None
        self.signals = LinkSignals()
        self.signals.text.connect(self.on_rx_line)
        self.signals.event.connect(self.on_event)
        self.channel_buttons = {}
        self.selected_channel = 1

        self.init_ui()
        self.refresh_ports()

        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self.update_stats)
        self.stats_timer.start(500)

    # =========================================================================
    # UI
    # =========================================================================
    def init_ui(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #0b1118; }
            QWidget { color: #e0e6ed; font-family: 'Segoe UI', Arial, sans-serif; }
            QGroupBox { border: 1px solid #1f2d3d; border-radius: 8px; margin-top: 12px;
                        font-weight: bold; font-size: 13px; color: #00e676; padding-top: 15px; }
            QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px 0 6px; }
            QPushButton { background-color: #1a2634; border: 1px solid #2d3e52; border-radius: 6px;
                          padding: 6px 12px; font-weight: bold; color: #ffffff; }
            QPushButton:hover { background-color: #27384c; border-color: #00b0ff; }
            QPushButton:pressed { background-color: #0091ea; }
            QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background-color: #131c26; border: 1px solid #2d3e52;
                          border-radius: 5px; padding: 5px 8px; color: #ffffff; font-weight: bold; }
            QTextEdit { background-color: #070c12; border: 1px solid #1a2634; border-radius: 6px;
                        font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; color: #a0aec0; }
        """)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(15, 10, 15, 10)

        # ---- 1. header / connection ----
        header = QFrame()
        header.setStyleSheet("background-color: #121c27; border-radius: 8px; padding: 6px;")
        h = QHBoxLayout(header)
        h.setContentsMargins(10, 6, 10, 6)
        title = QLabel("LAMBTON OPTICAL SORTER")
        title.setStyleSheet("font-size: 17px; font-weight: 900; color: #00e676; letter-spacing: 1px;")
        sub = QLabel("|  63-Channel Ejector Diagnostics")
        sub.setStyleSheet("font-size: 13px; color: #90a4ae;")
        h.addWidget(title)
        h.addWidget(sub)
        h.addStretch()
        h.addWidget(QLabel("COM Port:"))
        self.combo_ports = QComboBox()
        self.combo_ports.setMinimumWidth(240)
        h.addWidget(self.combo_ports)
        btn_refresh = QPushButton("🔄")
        btn_refresh.setToolTip("Refresh COM ports")
        btn_refresh.clicked.connect(self.refresh_ports)
        h.addWidget(btn_refresh)
        self.btn_connect = QPushButton("⚡ CONNECT")
        self.btn_connect.clicked.connect(self.toggle_connect)
        h.addWidget(self.btn_connect)
        self.status_badge = QLabel("● DISCONNECTED")
        h.addWidget(self.status_badge)
        main_layout.addWidget(header)
        self.set_connected_ui(False)

        # ---- 2. quick actions ----
        actions = QGroupBox("QUICK ACTIONS & SAFETY")
        a = QHBoxLayout(actions)
        a.setSpacing(10)
        btn_status = QPushButton("📡 STATUS / PING")
        btn_status.setStyleSheet("QPushButton { background-color: #0091ea; } QPushButton:hover { background-color: #40c4ff; }")
        btn_status.clicked.connect(self.send_status)
        a.addWidget(btn_status)
        btn_arm = QPushButton("✅ ARM (clear trip)")
        btn_arm.clicked.connect(self.send_arm)
        a.addWidget(btn_arm)
        self.btn_wdt = QPushButton("⏱ WATCHDOG TEST")
        self.btn_wdt.setToolTip("Holds the selected channel ON, then stops the PC heartbeat for 1.5 s.\n"
                                "The ESP32 must switch the valve OFF after 500 ms and report WDT_TRIP.")
        self.btn_wdt.setStyleSheet("QPushButton { background-color: #ff6f00; } QPushButton:hover { background-color: #ffa000; }")
        self.btn_wdt.clicked.connect(self.watchdog_test)
        a.addWidget(self.btn_wdt)
        a.addStretch()
        btn_off = QPushButton("🛑 ALL VALVES OFF")
        btn_off.setStyleSheet("QPushButton { background-color: #d50000; font-weight: 900; padding: 6px 18px; }"
                              "QPushButton:hover { background-color: #ff1744; }")
        btn_off.clicked.connect(self.send_clear_all)
        a.addWidget(btn_off)
        main_layout.addWidget(actions)

        # ---- 3. test settings ----
        tests = QGroupBox("TEST MODE (runs on the ESP32 - exact timing)")
        t = QHBoxLayout(tests)
        t.setSpacing(8)
        t.addWidget(QLabel("Pulse:"))
        self.spin_pulse = QDoubleSpinBox()
        self.spin_pulse.setRange(0.2, 200.0)
        self.spin_pulse.setDecimals(1)
        self.spin_pulse.setValue(5.0)
        self.spin_pulse.setSuffix(" ms")
        t.addWidget(self.spin_pulse)
        t.addWidget(QLabel("Gap:"))
        self.spin_gap = QDoubleSpinBox()
        self.spin_gap.setRange(1.0, 5000.0)
        self.spin_gap.setDecimals(0)
        self.spin_gap.setValue(100.0)
        self.spin_gap.setSuffix(" ms")
        t.addWidget(self.spin_gap)
        t.addWidget(QLabel("Repeat:"))
        self.spin_repeat = QSpinBox()
        self.spin_repeat.setRange(0, 10000)
        self.spin_repeat.setValue(10)
        self.spin_repeat.setToolTip("0 = continuous until STOP")
        t.addWidget(self.spin_repeat)
        btn_single = QPushButton("🎯 SINGLE TEST (selected CH)")
        btn_single.clicked.connect(self.single_test)
        t.addWidget(btn_single)
        t.addSpacing(15)
        t.addWidget(QLabel("Sweep:"))
        self.spin_from = QSpinBox()
        self.spin_from.setRange(1, NUM_CHANNELS)
        self.spin_from.setValue(1)
        t.addWidget(self.spin_from)
        t.addWidget(QLabel("to"))
        self.spin_to = QSpinBox()
        self.spin_to.setRange(1, NUM_CHANNELS)
        self.spin_to.setValue(NUM_CHANNELS)
        t.addWidget(self.spin_to)
        btn_sweep = QPushButton("🔄 SWEEP TEST")
        btn_sweep.setStyleSheet("QPushButton { background-color: #7c4dff; } QPushButton:hover { background-color: #b388ff; }")
        btn_sweep.clicked.connect(self.sweep_test)
        t.addWidget(btn_sweep)
        btn_stop = QPushButton("⏹ STOP TEST")
        btn_stop.clicked.connect(self.stop_test)
        t.addWidget(btn_stop)
        main_layout.addWidget(tests)

        # ---- 4. channel matrix ----
        matrix = QGroupBox("63-CHANNEL VALVE MATRIX (click = select + fire once)")
        mv = QVBoxLayout(matrix)
        grid = QGridLayout()
        grid.setSpacing(4)
        cols = 9
        for ch in range(1, NUM_CHANNELS + 1):
            btn = QPushButton(f"{ch:02d}")
            btn.setFixedSize(90, 30)
            btn.clicked.connect(lambda checked, channel=ch: self.fire_single_valve(channel))
            self.channel_buttons[ch] = btn
            self.style_channel_button(ch, active=False)
            grid.addWidget(btn, (ch - 1) // cols, (ch - 1) % cols)
        mv.addLayout(grid)
        self.lbl_selected = QLabel("Selected: CH 1")
        self.lbl_selected.setStyleSheet("font-size: 15px; font-weight: bold; color: #00e5ff; padding: 2px;")
        mv.addWidget(self.lbl_selected)
        main_layout.addWidget(matrix)

        # ---- 5. remap ----
        remap = QGroupBox("CHANNEL REMAP (fix crossed wires in software)")
        r = QHBoxLayout(remap)
        r.addWidget(QLabel("Swap CH"))
        self.spin_swap_a = QSpinBox()
        self.spin_swap_a.setRange(1, NUM_CHANNELS)
        r.addWidget(self.spin_swap_a)
        r.addWidget(QLabel("↔ CH"))
        self.spin_swap_b = QSpinBox()
        self.spin_swap_b.setRange(1, NUM_CHANNELS)
        self.spin_swap_b.setValue(2)
        r.addWidget(self.spin_swap_b)
        btn_swap = QPushButton("SWAP")
        btn_swap.clicked.connect(self.remap_swap)
        r.addWidget(btn_swap)
        btn_disable = QPushButton("DISABLE selected CH")
        btn_disable.clicked.connect(self.remap_disable_selected)
        r.addWidget(btn_disable)
        btn_show = QPushButton("SHOW TABLE")
        btn_show.clicked.connect(self.remap_show)
        r.addWidget(btn_show)
        r.addStretch()
        btn_save = QPushButton("💾 SAVE TO ESP32")
        btn_save.clicked.connect(self.remap_save)
        r.addWidget(btn_save)
        btn_default = QPushButton("RESET 1:1")
        btn_default.clicked.connect(self.remap_default)
        r.addWidget(btn_default)
        main_layout.addWidget(remap)

        # ---- 6. stats ----
        self.lbl_stats = QLabel("LINK: OFFLINE")
        self.lbl_stats.setStyleSheet("font-family: Consolas, monospace; font-size: 12px; color: #b0bec5;")
        main_layout.addWidget(self.lbl_stats)
        self.lbl_esp_counters = QLabel("ESP32 counters: -")
        self.lbl_esp_counters.setStyleSheet("font-family: Consolas, monospace; font-size: 12px; color: #b0bec5;")
        main_layout.addWidget(self.lbl_esp_counters)

        # ---- 7. console ----
        console_group = QGroupBox("ESP32 CONSOLE (text from the ESP32 + events)")
        cl = QVBoxLayout(console_group)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(170)
        cl.addWidget(self.console)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Text command:"))
        self.txt_cmd = QLineEdit()
        self.txt_cmd.setPlaceholderText("e.g. HELP, STATUS, MAP?, FIRE:5:10, TEST:1:63:5:100  (Enter to send)")
        self.txt_cmd.returnPressed.connect(self.send_custom_cmd)
        bar.addWidget(self.txt_cmd)
        btn_send = QPushButton("Send")
        btn_send.clicked.connect(self.send_custom_cmd)
        bar.addWidget(btn_send)
        btn_clear_log = QPushButton("Clear Log")
        btn_clear_log.clicked.connect(self.console.clear)
        bar.addWidget(btn_clear_log)
        cl.addLayout(bar)
        main_layout.addWidget(console_group)

    def style_channel_button(self, ch, active):
        btn = self.channel_buttons[ch]
        if active:
            btn.setStyleSheet("QPushButton { background-color: #ff1744; color: #ffffff; border: 1px solid #ffffff;"
                              " border-radius: 4px; font-size: 11px; font-weight: 900; }")
        elif ch == self.selected_channel:
            btn.setStyleSheet("QPushButton { background-color: #004d61; border: 2px solid #00e5ff; border-radius: 4px;"
                              " color: #ffffff; font-size: 11px; font-weight: bold; }")
        else:
            btn.setStyleSheet("QPushButton { background-color: #16202c; border: 1px solid #27374a; border-radius: 4px;"
                              " color: #90caf9; font-size: 11px; font-weight: bold; }"
                              "QPushButton:hover { background-color: #00b0ff; color: #000000; border-color: #ffffff; }")

    def set_connected_ui(self, connected, text=""):
        if connected:
            self.status_badge.setText(f"● {text}")
            self.status_badge.setStyleSheet("color: #00e676; font-weight: bold; font-size: 12px; padding: 4px 8px;")
            self.btn_connect.setText("✖ DISCONNECT")
            self.btn_connect.setStyleSheet("QPushButton { background-color: #d50000; font-weight: 900; padding: 6px 16px; }")
        else:
            self.status_badge.setText("● DISCONNECTED")
            self.status_badge.setStyleSheet("color: #ff5252; font-weight: bold; font-size: 12px; padding: 4px 8px;")
            self.btn_connect.setText("⚡ CONNECT")
            self.btn_connect.setStyleSheet("QPushButton { background-color: #00c853; color: #000000; font-weight: 900;"
                                           " padding: 6px 16px; } QPushButton:hover { background-color: #69f0ae; }")

    # =========================================================================
    # CONNECTION
    # =========================================================================
    def refresh_ports(self):
        self.combo_ports.clear()
        ports = list(serial.tools.list_ports.comports())
        preferred = 0
        for i, p in enumerate(ports):
            desc = p.description or ""
            if any(k in desc for k in ("CH9102", "CP210", "CH340", "USB-SERIAL", "USB Serial")):
                label = f"{p.device} - {desc} [ESP32?]"
                preferred = i
            else:
                label = f"{p.device} - {desc}"
            self.combo_ports.addItem(label, p.device)
        if ports:
            self.combo_ports.setCurrentIndex(preferred)

    def toggle_connect(self):
        if self.link and self.link.is_open:
            self.disconnect_link()
        else:
            self.connect_link()

    def connect_link(self):
        port = self.combo_ports.currentData()
        if not port:
            self.log("No COM port selected!", "#ff5252")
            return
        self.log(f"Connecting to {port} (auto-detecting firmware, up to 5 s)...", "#90a4ae")
        QApplication.processEvents()
        self.link = EjectorLink(port, on_event=lambda n, a: self.signals.event.emit(n, a),
                                on_text=lambda line: self.signals.text.emit(line))
        mode = self.link.connect()
        if not mode:
            self.log(f"No ejector firmware answered on {port}. Wrong port, or the sorter program is using it.", "#ff5252")
            for line in list(self.link.text_lines)[-3:]:
                self.log(line, "#ff8a80")
            self.link = None
            self.set_connected_ui(False)
            return
        if mode == "BINARY":
            self.set_connected_ui(True, f"v{self.link.fw_version} BINARY ({port})")
            self.log(f"Connected: firmware v{self.link.fw_version}, binary protocol, 500 ms watchdog ARMED.", "#00e676")
        else:
            self.set_connected_ui(True, f"OLD FIRMWARE - LEGACY ({port})")
            self.log("Connected to OLD firmware (text mode). Remap, watchdog and exact tests need ESP32_63CH_Ejector_v2_0.",
                     "#ffb74d")
        self.select_channel(self.selected_channel)

    def disconnect_link(self):
        if self.link:
            self.link.close(disarm=True)
        self.link = None
        self.set_connected_ui(False)
        self.log("Disconnected (board DISARMED - no watchdog alarm).", "#90a4ae")

    def need_link(self, binary=False):
        if not (self.link and self.link.is_open):
            self.log("Not connected!", "#ff5252")
            return False
        if binary and self.link.mode != "BINARY":
            self.log("This needs firmware ESP32_63CH_Ejector_v2_0.", "#ffb74d")
            return False
        return True

    # =========================================================================
    # ACTIONS
    # =========================================================================
    def select_channel(self, ch):
        old = self.selected_channel
        self.selected_channel = ch
        self.style_channel_button(old, False)
        self.style_channel_button(ch, False)
        info = self.link.channel_info(ch) if self.link else f"CH {ch}"
        self.lbl_selected.setText(f"Selected: {info}")

    def fire_single_valve(self, ch):
        self.select_channel(ch)
        if not self.need_link():
            return
        pulse = self.spin_pulse.value()
        self.link.fire([ch], pulse)
        self.log(f"[TX] FIRE {self.link.channel_info(ch)}  pulse {pulse:.1f} ms", "#00b0ff")
        self.flash_button(ch, int(max(pulse, 120)))

    def single_test(self):
        if not self.need_link():
            return
        ch, p, g, n = self.selected_channel, self.spin_pulse.value(), self.spin_gap.value(), self.spin_repeat.value()
        self.link.test_single(ch, p, g, repeat=n)
        self.log(f"[TX] SINGLE TEST CH {ch}: pulse {p:.1f} ms, gap {g:.0f} ms, "
                 f"{'continuous' if n == 0 else f'{n} times'}", "#00b0ff")

    def sweep_test(self):
        if not self.need_link():
            return
        a, b = self.spin_from.value(), self.spin_to.value()
        if a > b:
            a, b = b, a
        p, g = self.spin_pulse.value(), self.spin_gap.value()
        self.link.test_sweep(a, b, p, g)
        self.log(f"[TX] SWEEP CH {a}..{b}: pulse {p:.1f} ms, gap {g:.0f} ms", "#b388ff")

    def stop_test(self):
        if self.need_link():
            self.link.test_stop()
            self.link.stop_all()
            self.log("[TX] STOP TEST + ALL OFF", "#ffb74d")

    def send_clear_all(self):
        if self.need_link():
            self.link.test_stop()
            self.link.stop_all()
            self.log("[TX] ALL VALVES OFF", "#ff5252")

    def send_status(self):
        if not self.need_link():
            return
        if self.link.mode == "BINARY":
            st = self.link.request_status()
            if st:
                self.log("[RX] STATUS " + " ".join(f"{k}={v}" for k, v in st.items()), "#00e676")
            else:
                self.log("No STATUS reply!", "#ff5252")
        else:
            self.link.send_text("PING")
            self.link.send_text("STATUS")

    def send_arm(self):
        if self.need_link(binary=True):
            self.link.arm()
            self.log("[TX] ARM - watchdog active, valves enabled.", "#00e676")

    def watchdog_test(self):
        if not self.need_link(binary=True):
            return
        ch = self.selected_channel
        self.log(f"[WATCHDOG TEST] HOLD CH {ch} ON, then stop heartbeat for 1.5 s. "
                 f"Expect: valve OFF after ~0.5 s and EVENT WDT_TRIP.", "#ffa000")
        self.link.hold(ch, True)
        self.wdt_test_t0 = time.perf_counter()
        QTimer.singleShot(300, self._wdt_pause)

    def _wdt_pause(self):
        self.link.heartbeat_paused = True
        self.wdt_pause_t = time.perf_counter()
        QTimer.singleShot(1500, self._wdt_resume)

    def _wdt_resume(self):
        if not self.link:
            return
        self.link.heartbeat_paused = False
        tripped = self.link.esp_state == "TRIPPED" or self.link.tripped_event.is_set()
        if tripped:
            self.log("[WATCHDOG TEST] PASS - ESP32 tripped (all valves OFF). Press ARM to continue.", "#00e676")
        else:
            self.log("[WATCHDOG TEST] FAIL - no trip reported! Check firmware version.", "#ff5252")

    def remap_swap(self):
        if not self.need_link(binary=True):
            return
        a, b = self.spin_swap_a.value(), self.spin_swap_b.value()
        table = self.link.request_remap()
        table[a - 1], table[b - 1] = table[b - 1], table[a - 1]
        self.link.set_remap(table)
        self.log(f"[REMAP] swapped CH {a} <-> CH {b} (not saved yet - press SAVE TO ESP32)", "#ffb74d")
        self.select_channel(self.selected_channel)

    def remap_disable_selected(self):
        if not self.need_link(binary=True):
            return
        table = self.link.request_remap()
        table[self.selected_channel - 1] = None
        self.link.set_remap(table)
        self.log(f"[REMAP] CH {self.selected_channel} DISABLED (not saved yet)", "#ffb74d")
        self.select_channel(self.selected_channel)

    def remap_show(self):
        if not self.need_link(binary=True):
            return
        self.link.request_remap()
        changed = [self.link.channel_info(ch) for ch in range(1, NUM_CHANNELS + 1)
                   if self.link.remap[ch - 1] != ch - 1]
        self.log("[REMAP] table: " + ("all channels 1:1 (default)" if not changed else "; ".join(changed)), "#00e5ff")

    def remap_save(self):
        if self.need_link(binary=True):
            self.link.save_remap()
            self.log("[REMAP] saved to ESP32 flash (survives power-off).", "#00e676")

    def remap_default(self):
        if self.need_link(binary=True):
            self.link.default_remap()
            self.log("[REMAP] reset to 1:1 (press SAVE TO ESP32 to keep it).", "#ffb74d")
            self.select_channel(self.selected_channel)

    def send_custom_cmd(self):
        cmd = self.txt_cmd.text().strip()
        if not cmd or not self.need_link():
            return
        self.link.send_text(cmd)
        self.log(f"[TX text] {cmd}", "#00b0ff")
        self.txt_cmd.clear()

    # =========================================================================
    # RX / UI UPDATES
    # =========================================================================
    def on_rx_line(self, line):
        color = "#00e676" if line.startswith("ACK") else "#b388ff" if line.startswith("[TEST]") else \
            "#ff5252" if line.startswith(("ERR", "EVENT:WDT")) else "#eceff1"
        self.log(f"[ESP32] {line}", color)

    def on_event(self, name, arg):
        if name == "TEST_STEP":
            ch = arg & 0xFF
            if ch in self.channel_buttons:
                self.flash_button(ch, 150)
        elif name == "WDT_TRIP":
            self.log("[EVENT] WDT_TRIP - watchdog switched ALL valves OFF. Press ARM to continue.", "#ff5252")
        elif name == "TEST_DONE":
            self.log("[EVENT] TEST DONE", "#b388ff")
        elif name == "HOLD_TIMEOUT":
            self.log(f"[EVENT] HOLD released automatically after 10 s (output {arg})", "#ffb74d")
        elif name != "ARMED":
            self.log(f"[EVENT] {name} {arg}", "#90a4ae")

    def flash_button(self, ch, ms):
        self.style_channel_button(ch, True)
        QTimer.singleShot(ms, lambda: self.style_channel_button(ch, False))

    def update_stats(self):
        if not self.link:
            self.lbl_stats.setText("LINK: OFFLINE")
            return
        if not self.link.is_open and self.link.mode is None:
            self.log("Connection lost.", "#ff5252")
            self.link = None
            self.set_connected_ui(False)
            return
        self.lbl_stats.setText("LINK: " + self.link.stats_text())
        st = self.link.status
        if st:
            self.lbl_esp_counters.setText(
                "ESP32 counters: " + "  ".join(f"{k}={st.get(k)}" for k in
                                               ("rx_ok", "crc_err", "len_err", "seq_gaps", "fired", "busy", "wdt_trips")))
        if self.link.mode == "BINARY" and not getattr(self, "_status_poll", False):
            self._status_poll = True                      # refresh ESP32 counters every 2 s (non-blocking)
            QTimer.singleShot(2000, self._poll_status)

    def _poll_status(self):
        self._status_poll = False
        # no polling during the watchdog test: any message would reset the ESP32 watchdog
        if self.link and self.link.mode == "BINARY" and not self.link.heartbeat_paused:
            self.link.request_status(timeout=0.05)

    def log(self, text, color="#a0aec0"):
        ts = time.strftime("%H:%M:%S")
        self.console.append(f'<span style="color:#546e7a">{ts}</span> '
                            f'<span style="color:{color}">{html.escape(text)}</span>')
        self.console.moveCursor(QTextCursor.End)

    def closeEvent(self, event):
        if self.link:
            self.link.close(disarm=True)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = EjectorDiagnosticApp()
    win.show()
    sys.exit(app.exec_())
