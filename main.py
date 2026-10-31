"""
TCM ESP32 63-Channel Ejector Controller - Unified All-in-One Application.
Contains both the complete serial hardware driver and the modern PyQt6 desktop GUI.

Standalone Usage:
    python main.py              # Launches the Desktop GUI application
    python main.py COM13        # Interactive console mode
    python main.py COM13 --demo # Runs hardware self-test on CH1-CH8

Integration Usage (embed into your main UI):
    from main import EjectorWidget, Ejector
    
    # In your QMainWindow or QWidget:
    ejector_panel = EjectorWidget()
    my_layout.addWidget(ejector_panel)
"""
import argparse
import sys
import threading
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple

try:
    import winsound
except ImportError:
    winsound = None

import serial
import serial.tools.list_ports

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QSlider, QSpinBox, QComboBox,
    QCheckBox, QTextEdit, QLineEdit, QTabWidget, QFrame, QDialog,
    QFormLayout
)


# =============================================================================
# Hardware Driver Constants & Classes
# =============================================================================
BAUD = 115200
NUM_CH = 63
MAX_PULSE_MS = 5000
MAX_GAP_MS = 60000


class EjectorError(Exception):
    """Raised when the board answers ERR:... or does not answer."""


class Ejector:
    def __init__(self, port: str, baud: int = BAUD, timeout: float = 1.0,
                 boot_wait: float = 2.0, keepalive_s: float = 0.0):
        self.ser = serial.Serial(port, baud, timeout=0.05)
        self._lock = threading.Lock()
        self.timeout = timeout
        self.events: List[str] = []
        self._stop_ka = threading.Event()

        # Opening the port resets most ESP32 boards; wait for boot, drop banner.
        time.sleep(boot_wait)
        self.ser.reset_input_buffer()
        if self.ping() is False:
            raise EjectorError("No PONG from board - check port / baud / firmware")

        if keepalive_s > 0:
            t = threading.Thread(target=self._keepalive, args=(keepalive_s,), daemon=True)
            t.start()

    # ---------------- low level ----------------
    def _read_line(self, deadline: float) -> Optional[str]:
        buf = b""
        while time.monotonic() < deadline:
            c = self.ser.read(1)
            if not c:
                continue
            if c in (b"\n", b"\r"):
                if buf:
                    return buf.decode(errors="replace").strip()
                continue
            buf += c
        return None

    def send(self, cmd, expect_prefix: Optional[str] = None) -> str:
        """cmd may be a str, or a callable returning the str (evaluated under the port lock)."""
        with self._lock:
            if callable(cmd):
                cmd = cmd()
            self.ser.write((cmd.strip() + "\n").encode())
            deadline = time.monotonic() + self.timeout
            while True:
                line = self._read_line(deadline)
                if line is None:
                    raise EjectorError(f"Timeout waiting for reply to {cmd!r}")
                if line.startswith(("DONE:", "WDT_")):
                    self.events.append(line)
                    continue
                break
        if line.startswith("ERR:"):
            raise EjectorError(f"{cmd!r} -> {line}")
        if expect_prefix and not line.startswith(expect_prefix):
            raise EjectorError(f"{cmd!r} -> unexpected reply {line!r}")
        return line

    def _keepalive(self, period: float):
        while not self._stop_ka.wait(period):
            try:
                self.send("PING")
            except Exception:
                pass

    # ---------------- helpers ----------------
    @staticmethod
    def _ch(ch: int) -> int:
        if not 1 <= int(ch) <= NUM_CH:
            raise ValueError(f"channel must be 1..{NUM_CH}, got {ch}")
        return int(ch)

    @staticmethod
    def _ms(v: int, lo: int, hi: int, name: str) -> int:
        if not lo <= int(v) <= hi:
            raise ValueError(f"{name} must be {lo}..{hi}, got {v}")
        return int(v)

    # ---------------- commands ----------------
    def ping(self) -> bool:
        try:
            return self.send("PING") == "PONG"
        except EjectorError:
            return False

    def status(self) -> str:
        return self.send("STATUS")

    def fire(self, ch: int, pulse_ms: int = 5, delay_ms: int = 0) -> str:
        if delay_ms > 0:
            time.sleep(self._ms(delay_ms, 1, 60000, "delay_ms") / 1000.0)
        return self.send(f"FIRE:{self._ch(ch)}:{self._ms(pulse_ms, 1, MAX_PULSE_MS, 'pulse_ms')}",
                         "OK:FIRE")

    def fire_multi(self, channels: Iterable[int], pulse_ms: int = 5, delay_ms: int = 0) -> str:
        if delay_ms > 0:
            time.sleep(self._ms(delay_ms, 1, 60000, "delay_ms") / 1000.0)
        chs = sorted(list(set(self._ch(c) for c in channels)))
        if not chs:
            raise ValueError("no channels given")
        cmd = f"FIRE_MULTI:{','.join(map(str, chs))}:{self._ms(pulse_ms, 1, MAX_PULSE_MS, 'pulse_ms')}"
        if len(cmd) > 250:
            raise ValueError("channel list too long for one command; split it")
        return self.send(cmd, "OK:FIRE_MULTI")

    def repeat(self, ch: int, pulse_ms: int, gap_ms: int, count: int) -> str:
        if count < 1:
            raise ValueError("count must be >= 1")
        return self.send(f"FIRE:{self._ch(ch)}:{self._ms(pulse_ms, 1, MAX_PULSE_MS, 'pulse_ms')}:"
                         f"{self._ms(gap_ms, 0, MAX_GAP_MS, 'gap_ms')}:{int(count)}", "OK:REPEAT")

    def continuous(self, ch: int, pulse_ms: int, gap_ms: int) -> str:
        return self.send(f"CONT:{self._ch(ch)}:{self._ms(pulse_ms, 1, MAX_PULSE_MS, 'pulse_ms')}:"
                         f"{self._ms(gap_ms, 0, MAX_GAP_MS, 'gap_ms')}", "OK:CONT")

    def test_sequence(self, first: int = 1, last: int = NUM_CH,
                      pulse_ms: int = 50, gap_ms: int = 100) -> str:
        return self.send(f"TEST:{self._ch(first)}:{self._ch(last)}:"
                         f"{self._ms(pulse_ms, 1, MAX_PULSE_MS, 'pulse_ms')}:"
                         f"{self._ms(gap_ms, 0, MAX_GAP_MS, 'gap_ms')}", "OK:TEST")

    def sched_at(self, fire_at: float, channels: Iterable[int], pulse_us: int) -> str:
        """
        Timed ejection (needs the SCHED firmware). fire_at is a time.monotonic()
        value. The delay is computed at the moment the bytes are written, minus
        the time the line itself takes on the wire, so port-lock waits and serial
        speed do not shift the ejection.
        """
        chs = sorted(set(self._ch(c) for c in channels))
        if not chs:
            raise ValueError("no channels given")
        pulse_us = self._ms(pulse_us, 300, MAX_PULSE_MS * 1000, "pulse_us")
        csv = ",".join(map(str, chs))
        wire_s = (len(f"SCHED:0000000:{csv}:{pulse_us}") + 1) * 10.0 / max(1, self.ser.baudrate)

        def build() -> str:
            d = int(max(0.0, (fire_at - time.monotonic() - wire_s - 0.0005) * 1e6))
            return f"SCHED:{d}:{csv}:{pulse_us}"

        return self.send(build, "OK:SCHED")

    def sched_clear(self) -> str:
        return self.send("SCHED_CLR", "OK:SCHED_CLR")

    def hold_on(self, ch: int) -> str:
        return self.send(f"HOLD_ON:{self._ch(ch)}", "OK:HOLD_ON")

    def hold_off(self, ch: int) -> str:
        return self.send(f"HOLD_OFF:{self._ch(ch)}", "OK:HOLD_OFF")

    def stop(self) -> str:
        return self.send("STOP", "STOPPED")

    def all_off(self) -> str:
        return self.send("ALL_OFF", "ALL_OFF_OK")

    def wait_done(self, tag: str, timeout: float = 60.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if tag in self.events:
                self.events.remove(tag)
                return True
            with self._lock:
                line = self._read_line(time.monotonic() + 0.1)
            if line:
                self.events.append(line)
        return False

    def close(self):
        self._stop_ka.set()
        try:
            self.stop()
        except Exception:
            pass
        self.ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# =============================================================================
# CLI Console & Demo Functions
# =============================================================================
HELP_TEXT = """commands:
  fire <ch> [ms]               multi <c1,c2,..> [ms]
  repeat <ch> <ms> <gap> <n>   cont <ch> <ms> <gap>
  test <first> <last> <ms> <gap>
  hold <ch>   release <ch>     stop   ping   status   help   quit"""


def console(ej: Ejector):
    print(HELP_TEXT)
    while True:
        try:
            parts = input("ejector> ").split()
        except (EOFError, KeyboardInterrupt):
            break
        if not parts:
            continue
        c, a = parts[0].lower(), parts[1:]
        try:
            if c in ("quit", "exit"):
                break
            elif c == "help":
                print(HELP_TEXT)
            elif c == "ping":
                print("PONG" if ej.ping() else "no reply")
            elif c == "status":
                print(ej.status())
            elif c == "stop":
                print(ej.stop())
            elif c == "fire":
                print(ej.fire(int(a[0]), int(a[1]) if len(a) > 1 else 5))
            elif c == "multi":
                print(ej.fire_multi([int(x) for x in a[0].split(",")], int(a[1]) if len(a) > 1 else 5))
            elif c == "repeat":
                print(ej.repeat(*map(int, a[:4])))
            elif c == "cont":
                print(ej.continuous(*map(int, a[:3])))
            elif c == "test":
                print(ej.test_sequence(*map(int, a[:4])))
            elif c == "hold":
                print(ej.hold_on(int(a[0])))
            elif c == "release":
                print(ej.hold_off(int(a[0])))
            else:
                print("unknown command, type 'help'")
        except (EjectorError, ValueError, IndexError) as e:
            print("error:", e)
        for ev in ej.events:
            print("event:", ev)
        ej.events.clear()


def demo(ej: Ejector):
    print(ej.status())
    print(ej.fire(1, 20)); time.sleep(0.2)
    print(ej.fire_multi([1, 2, 3], 20)); time.sleep(0.2)
    print(ej.repeat(1, 20, 80, 5)); print("repeat done:", ej.wait_done("DONE:REPEAT", 5))
    print(ej.test_sequence(1, 8, 30, 70)); print("test done:", ej.wait_done("DONE:TEST", 10))
    print(ej.hold_on(2)); time.sleep(0.5); print(ej.hold_off(2))
    print(ej.stop())


# =============================================================================
# Modern Cyber-Industrial QSS Stylesheet
# =============================================================================
QSS_STYLE = """
QMainWindow, QDialog, QWidget#centralWidget {
    background-color: #070a12;
    color: #e2e8f0;
    font-family: 'Segoe UI', 'Inter', sans-serif;
}

QFrame.card {
    background-color: #0d1424;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
}

QFrame#headerBar {
    background-color: #0e1628;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 12px;
    padding: 6px;
}

QLabel {
    color: #cbd5e1;
}

QLabel.titleText {
    color: #ffffff;
    font-size: 15px;
    font-weight: 800;
    letter-spacing: 1px;
}

QLabel.badge {
    background-color: rgba(0, 242, 254, 0.12);
    border: 1px solid rgba(0, 242, 254, 0.35);
    color: #00f2fe;
    font-family: 'Consolas', monospace;
    font-weight: 700;
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 5px;
}

/* Combo boxes & Inputs */
QComboBox, QSpinBox, QLineEdit {
    background-color: #111a2e;
    border: 1px solid #1e2c48;
    color: #f8fafc;
    font-family: 'Consolas', monospace;
    font-size: 12px;
    padding: 5px 8px;
    border-radius: 6px;
}

QComboBox:hover, QSpinBox:hover, QLineEdit:hover {
    border-color: #00f2fe;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #0d1424;
    border: 1px solid #1e2c48;
    color: #f8fafc;
    selection-background-color: #00f2fe;
    selection-color: #070a12;
}

/* Push Buttons */
QPushButton {
    background-color: #162238;
    border: 1px solid rgba(255, 255, 255, 0.1);
    color: #cbd5e1;
    font-weight: 600;
    font-size: 12px;
    padding: 6px 12px;
    border-radius: 7px;
}

QPushButton:hover {
    background-color: #1f304f;
    color: #ffffff;
    border-color: rgba(255, 255, 255, 0.25);
}

QPushButton:pressed {
    background-color: #111c30;
}

QPushButton.btnPreset {
    background-color: rgba(255, 255, 255, 0.04);
    border: 1px solid #1e293b;
    color: #94a3b8;
    font-family: 'Consolas', monospace;
    font-size: 11px;
    font-weight: 600;
    padding: 4px 8px;
    border-radius: 5px;
}

QPushButton.btnPreset:hover {
    background-color: rgba(255, 255, 255, 0.1);
    color: #ffffff;
    border-color: #00f2fe;
}

/* Chute Buttons */
QPushButton.btnChute {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(14, 165, 233, 0.18), stop:1 rgba(37, 99, 235, 0.18));
    border: 1px solid rgba(14, 165, 233, 0.55);
    color: #38bdf8;
    font-family: 'Consolas', monospace;
    font-size: 11px;
    font-weight: 800;
    padding: 5px 12px;
    border-radius: 6px;
}

QPushButton.btnChute:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(14, 165, 233, 0.4), stop:1 rgba(37, 99, 235, 0.4));
    color: #ffffff;
    border-color: #38bdf8;
}

QPushButton.btnAddChute {
    background-color: rgba(34, 197, 94, 0.12);
    border: 1px dashed rgba(34, 197, 94, 0.6);
    color: #4ade80;
    font-family: 'Consolas', monospace;
    font-size: 11px;
    font-weight: 700;
    padding: 5px 10px;
    border-radius: 6px;
}

QPushButton.btnAddChute:hover {
    background-color: rgba(34, 197, 94, 0.25);
    border-color: #22c55e;
    color: #ffffff;
}

QPushButton.btnConnect {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0ea5e9, stop:1 #2563eb);
    color: #ffffff;
    font-weight: 700;
    border: 1px solid rgba(255, 255, 255, 0.2);
}

QPushButton.btnConnect:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #38bdf8, stop:1 #3b82f6);
}

QPushButton.btnDisconnect {
    background-color: #1e293b;
    color: #94a3b8;
    border: 1px solid #334155;
}

QPushButton.btnDisconnect:hover {
    background-color: rgba(239, 68, 68, 0.2);
    color: #ef4444;
    border-color: #ef4444;
}

QPushButton.btnEmergency {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #dc2626, stop:1 #991b1b);
    color: #ffffff;
    font-size: 13px;
    font-weight: 900;
    letter-spacing: 0.5px;
    padding: 8px 16px;
    border-radius: 8px;
    border: 1px solid rgba(255, 120, 120, 0.5);
}

QPushButton.btnEmergency:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ef4444, stop:1 #b91c1c);
}

QPushButton.btnFire {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #00f2fe, stop:1 #0284c7);
    color: #04121e;
    font-size: 14px;
    font-weight: 900;
    letter-spacing: 0.5px;
    padding: 12px 20px;
    border-radius: 9px;
    border: 1px solid #38bdf8;
}

QPushButton.btnFire:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #38bdf8, stop:1 #0ea5e9);
}

QPushButton.btnAccentAction {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2563eb, stop:1 #1d4ed8);
    color: #ffffff;
    font-weight: 700;
    padding: 10px;
    border-radius: 7px;
}

QPushButton.btnAccentAction:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3b82f6, stop:1 #2563eb);
}

QPushButton.btnWarnAction {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #d97706, stop:1 #b45309);
    color: #ffffff;
    font-weight: 700;
    padding: 10px;
    border-radius: 7px;
}

QPushButton.btnWarnAction:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f59e0b, stop:1 #d97706);
}

/* Tabs */
QTabWidget::pane {
    background-color: transparent;
    border: none;
}

QTabBar::tab {
    background-color: #0a0f1d;
    border-bottom: 2px solid transparent;
    color: #94a3b8;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
    padding: 8px 14px;
}

QTabBar::tab:hover {
    color: #ffffff;
    background-color: #111a2f;
}

QTabBar::tab:selected {
    color: #00f2fe;
    border-bottom: 2px solid #00f2fe;
    background-color: #111a2f;
}

/* Sliders */
QSlider::groove:horizontal {
    height: 6px;
    background: #1e293b;
    border-radius: 3px;
}

QSlider::sub-page:horizontal {
    background: #00f2fe;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    background: #00f2fe;
    border: 2px solid #ffffff;
    width: 18px;
    margin-top: -6px;
    margin-bottom: -6px;
    border-radius: 9px;
}

/* Terminal */
QTextEdit#terminalWindow {
    background-color: #050811;
    border: 1px solid #141f33;
    border-radius: 8px;
    color: #cbd5e1;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    padding: 8px;
}

QScrollBar:vertical {
    background: #070a12;
    width: 8px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #1e293b;
    min-height: 20px;
    border-radius: 4px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QCheckBox {
    color: #94a3b8;
    font-size: 11px;
    font-weight: 600;
}

QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border-radius: 3px;
    border: 1px solid #334155;
    background: #111827;
}

QCheckBox::indicator:checked {
    background: #00f2fe;
    border-color: #00f2fe;
}
"""

# =============================================================================
# Dedicated Valve Button Styles (Ensures 100% Reliable Rendering in Qt)
# =============================================================================
STYLE_VALVE_IDLE = """
QPushButton {
    background-color: #111827;
    border: 1px solid #1f293d;
    border-radius: 7px;
    color: #64748b;
    font-family: 'Consolas', monospace;
    font-size: 13px;
    font-weight: 700;
    min-height: 44px;
    min-width: 44px;
}
QPushButton:hover {
    background-color: #1a253a;
    border-color: rgba(255, 255, 255, 0.4);
    color: #cbd5e1;
}
"""

STYLE_VALVE_SELECTED = """
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #22c55e, stop:1 #15803d);
    border: 2px solid #86efac;
    border-radius: 7px;
    color: #ffffff;
    font-family: 'Consolas', monospace;
    font-size: 14px;
    font-weight: 900;
    min-height: 44px;
    min-width: 44px;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4ade80, stop:1 #16a34a);
    color: #ffffff;
}
"""

STYLE_VALVE_FIRING = """
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffff22, stop:1 #ff7700);
    border: 3px solid #ffffff;
    border-radius: 7px;
    color: #000000;
    font-family: 'Consolas', monospace;
    font-size: 15px;
    font-weight: 900;
    min-height: 44px;
    min-width: 44px;
}
"""

STYLE_VALVE_HOLDING = """
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f59e0b, stop:1 #b45309);
    border: 2px solid #fde047;
    border-radius: 7px;
    color: #ffffff;
    font-family: 'Consolas', monospace;
    font-size: 14px;
    font-weight: 900;
    min-height: 44px;
    min-width: 44px;
}
"""


# =============================================================================
# Add / Configure Chute Dialog
# =============================================================================
class AddChuteDialog(QDialog):
    """Clean modal dialog allowing users to define new chutes or partitions."""
    def __init__(self, existing_chutes: List[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Add / Configure Chute")
        self.setStyleSheet(QSS_STYLE)
        self.setFixedWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        lbl = QLabel("CONFIGURE NEW CHUTE")
        lbl.setStyleSheet("font-weight: 800; font-size: 13px; color: #00f2fe; letter-spacing: 0.5px;")
        layout.addWidget(lbl)

        form = QFormLayout()
        form.setSpacing(10)

        next_idx = len(existing_chutes) + 1
        self.input_name = QLineEdit(f"Chute {next_idx}")
        form.addRow("Chute Name:", self.input_name)

        self.spin_start = QSpinBox()
        self.spin_start.setRange(1, NUM_CH)
        self.spin_start.setValue(1)
        form.addRow("Start Channel:", self.spin_start)

        self.spin_end = QSpinBox()
        self.spin_end.setRange(1, NUM_CH)
        self.spin_end.setValue(NUM_CH)
        form.addRow("End Channel:", self.spin_end)

        self.spin_start.valueChanged.connect(self._validate_range)
        self.spin_end.valueChanged.connect(self._validate_range)
        layout.addLayout(form)

        self.lbl_info = QLabel(f"Range: CH 1 to CH {NUM_CH} ({NUM_CH} channels)")
        self.lbl_info.setStyleSheet("color: #94a3b8; font-size: 11px; font-family: 'Consolas';")
        layout.addWidget(self.lbl_info)

        btns = QHBoxLayout()
        btns.setSpacing(8)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        btn_save = QPushButton("✓ Add Chute")
        btn_save.setProperty("class", "btnAccentAction")
        btn_save.clicked.connect(self.accept)

        btns.addStretch()
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_save)
        layout.addLayout(btns)

    def _validate_range(self):
        start = self.spin_start.value()
        end = self.spin_end.value()
        if start > end:
            self.spin_end.setValue(start)
            end = start
        count = end - start + 1
        self.lbl_info.setText(f"Range: CH {start} to CH {end} ({count} channel{'s' if count > 1 else ''})")

    def get_chute_data(self) -> Tuple[str, int, int]:
        name = self.input_name.text().strip() or "Chute"
        return name, self.spin_start.value(), self.spin_end.value()


# =============================================================================
# GUI Controller Signals & Main Window
# =============================================================================
class ControllerSignals(QObject):
    log_emitted = pyqtSignal(str, str, str)
    connection_changed = pyqtSignal(bool, bool, str)
    channel_fired = pyqtSignal(int, int)
    channels_multi_fired = pyqtSignal(list, int)
    channel_holding = pyqtSignal(int, bool)
    all_stopped = pyqtSignal()
    ping_received = pyqtSignal(float)


class EjectorWidget(QWidget):
    """
    Self-contained 63-Channel Ejector Controller Widget.
    Can be placed into any existing PyQt6 QMainWindow, QTabWidget, or QLayout.
    """
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setStyleSheet(QSS_STYLE)

        self.ej: Optional[Ejector] = None
        self.port: str = "COM13"
        self.baud: int = BAUD
        self.is_mock: bool = False
        self.is_connected: bool = False
        self.audio_enabled: bool = True
        self.selected_channels: Set[int] = set()
        self.held_channels: Set[int] = set()
        self.valve_buttons = {}
        self.cmd_history = []
        self.cmd_history_idx = -1
        self._sequence_id = 0
        self._active_snake_ch: Optional[int] = None

        # Chute definitions: Dict[name, (start_ch, end_ch)]
        self.chutes: Dict[str, Tuple[int, int]] = {
            "Chute 1": (1, 32),
            "Chute 2": (33, 63)
        }
        self.chute_buttons = {}

        self.lock = threading.RLock()
        self.signals = ControllerSignals()
        self.signals.log_emitted.connect(self._append_log)
        self.signals.connection_changed.connect(self._on_connection_changed)
        self.signals.channel_fired.connect(self._on_channel_fired)
        self.signals.channels_multi_fired.connect(self._on_channels_multi_fired)
        self.signals.channel_holding.connect(self._on_channel_holding)
        self.signals.all_stopped.connect(self._on_all_stopped)
        self.signals.ping_received.connect(self._on_ping_received)

        self._monitor_running = False
        self._monitor_thread: Optional[threading.Thread] = None

        self._setup_ui()
        self._setup_shortcuts()
        self._populate_ports()

        self.ping_timer = QTimer(self)
        self.ping_timer.timeout.connect(self._auto_ping)
        self.ping_timer.start(10000)

        self.signals.log_emitted.emit("SYS", "TCM 63-Channel Controller Desktop UI initialized.", "info")
        QTimer.singleShot(500, self._try_auto_connect)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)

        header = self._create_header_bar()
        main_layout.addWidget(header)

        workspace = QHBoxLayout()
        workspace.setSpacing(14)

        left_panel = self._create_matrix_panel()
        right_panel = self._create_controls_panel()

        workspace.addWidget(left_panel, stretch=6)
        workspace.addWidget(right_panel, stretch=4)
        main_layout.addLayout(workspace)

    def _create_header_bar(self) -> QFrame:
        header = QFrame()
        header.setObjectName("headerBar")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(12)

        title_box = QHBoxLayout()
        title_box.setSpacing(8)

        lbl_title = QLabel("TCM EJECTOR CONTROLLER")
        lbl_title.setProperty("class", "titleText")

        lbl_tech = QLabel("ESP32 63-CH")
        lbl_tech.setProperty("class", "badge")

        self.lbl_status = QLabel("● DISCONNECTED")
        self.lbl_status.setProperty("class", "badge")
        self.lbl_status.setStyleSheet("color: #ef4444; border-color: rgba(239, 68, 68, 0.4);")

        self.btn_ping = QPushButton("⚡ PING")
        self.btn_ping.setProperty("class", "btnPreset")
        self.btn_ping.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ping.clicked.connect(self._action_ping)

        title_box.addWidget(lbl_title)
        title_box.addWidget(lbl_tech)
        title_box.addWidget(self.lbl_status)
        title_box.addWidget(self.btn_ping)
        layout.addLayout(title_box)

        layout.addStretch()

        conn_box = QHBoxLayout()
        conn_box.setSpacing(6)

        conn_box.addWidget(QLabel("PORT:"))
        self.combo_ports = QComboBox()
        self.combo_ports.setMinimumWidth(160)
        conn_box.addWidget(self.combo_ports)

        btn_refresh = QPushButton("⟳")
        btn_refresh.setFixedWidth(28)
        btn_refresh.setToolTip("Refresh COM Ports")
        btn_refresh.clicked.connect(self._populate_ports)
        conn_box.addWidget(btn_refresh)

        conn_box.addWidget(QLabel("BAUD:"))
        self.combo_baud = QComboBox()
        self.combo_baud.addItems(["115200", "57600", "9600"])
        conn_box.addWidget(self.combo_baud)

        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setProperty("class", "btnConnect")
        self.btn_connect.setFixedWidth(100)
        self.btn_connect.clicked.connect(self._toggle_connection)
        conn_box.addWidget(self.btn_connect)
        layout.addLayout(conn_box)

        right_box = QHBoxLayout()
        right_box.setSpacing(8)

        self.btn_audio = QPushButton("🔊")
        self.btn_audio.setFixedWidth(32)
        self.btn_audio.setToolTip("Toggle Mechanical Valve Audio")
        self.btn_audio.clicked.connect(self._toggle_audio)
        right_box.addWidget(self.btn_audio)

        self.chk_mock = QCheckBox("SIMULATOR")
        self.chk_mock.setToolTip("Run in simulation mode without hardware")
        right_box.addWidget(self.chk_mock)

        self.btn_stop_all = QPushButton("🛑 STOP ALL [SPACE]")
        self.btn_stop_all.setProperty("class", "btnEmergency")
        self.btn_stop_all.clicked.connect(self._action_emergency_stop)
        right_box.addWidget(self.btn_stop_all)

        layout.addLayout(right_box)
        return header

    def _create_matrix_panel(self) -> QFrame:
        card = QFrame()
        card.setProperty("class", "card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Row 1: Header + Standard Presets
        hdr = QHBoxLayout()
        hdr_lbl = QLabel("VALVE ARRAY MATRIX")
        hdr_lbl.setStyleSheet("font-weight: 800; font-size: 13px; color: #f8fafc;")
        hdr.addWidget(hdr_lbl)

        self.lbl_selected_count = QLabel("0 / 63 SELECTED")
        self.lbl_selected_count.setProperty("class", "badge")
        hdr.addWidget(self.lbl_selected_count)

        hdr.addStretch()

        presets = [
            ("ALL", self._select_all),
            ("NONE", self._select_none),
            ("INVERT", self._select_invert),
            ("ODDS", self._select_odds),
            ("EVENS", self._select_evens)
        ]
        for name, callback in presets:
            btn = QPushButton(name)
            btn.setProperty("class", "btnPreset")
            btn.clicked.connect(callback)
            hdr.addWidget(btn)

        layout.addLayout(hdr)

        # Row 2: Chute Bar (Chute 1, Chute 2, Chute 1+2, + Add Chute)
        chute_bar = QHBoxLayout()
        chute_bar.setSpacing(6)

        lbl_chute = QLabel("CHUTES:")
        lbl_chute.setStyleSheet("font-weight: 800; font-size: 11px; color: #00f2fe; letter-spacing: 0.5px;")
        chute_bar.addWidget(lbl_chute)

        self.chute_btns_layout = QHBoxLayout()
        self.chute_btns_layout.setSpacing(6)
        chute_bar.addLayout(self.chute_btns_layout)

        self._rebuild_chute_buttons()

        btn_add_chute = QPushButton("+ ADD CHUTE")
        btn_add_chute.setProperty("class", "btnAddChute")
        btn_add_chute.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_chute.setToolTip("Configure and add a new chute range")
        btn_add_chute.clicked.connect(self._action_add_chute_dialog)
        chute_bar.addWidget(btn_add_chute)

        chute_bar.addStretch()
        layout.addLayout(chute_bar)

        # 63-channel Grid
        grid_container = QFrame()
        grid_container.setStyleSheet("background-color: #060a14; border: 1px solid rgba(255,255,255,0.05); border-radius: 9px; padding: 8px;")
        grid = QGridLayout(grid_container)
        grid.setSpacing(6)

        for ch in range(1, NUM_CH + 1):
            btn = QPushButton(f"{ch:02d}")
            btn.setStyleSheet(STYLE_VALVE_IDLE)
            btn.setProperty("state", "idle")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            chute_tag = "Chute 1" if ch <= 32 else "Chute 2"
            btn.setToolTip(f"Valve #{ch:02d} ({chute_tag}) - Click to toggle selection")
            btn.clicked.connect(lambda checked, c=ch: self._on_valve_cell_clicked(c))

            row = (ch - 1) // 9
            col = (ch - 1) % 9
            grid.addWidget(btn, row, col)
            self.valve_buttons[ch] = btn

        layout.addWidget(grid_container)

        # Legend
        legend = QHBoxLayout()
        legend.setSpacing(12)

        def _dot(color, text):
            box = QHBoxLayout()
            box.setSpacing(4)
            d = QLabel("■")
            d.setStyleSheet(f"color: {color}; font-size: 11px;")
            l = QLabel(text)
            l.setStyleSheet("color: #64748b; font-size: 11px;")
            box.addWidget(d)
            box.addWidget(l)
            return box

        legend.addLayout(_dot("#1f293d", "Idle"))
        legend.addLayout(_dot("#22c55e", "Selected (Green Fill)"))
        legend.addLayout(_dot("#ffff22", "Firing Pulse (Gold Flash)"))
        legend.addLayout(_dot("#f59e0b", "Held ON"))
        legend.addStretch()

        tip = QLabel("Click to select • Set Delay Time & Pulse below")
        tip.setStyleSheet("color: #475569; font-size: 10px; font-style: italic;")
        legend.addWidget(tip)

        layout.addLayout(legend)
        return card

    def _rebuild_chute_buttons(self):
        """Rebuilds the interactive chute buttons based on self.chutes."""
        while self.chute_btns_layout.count() > 0:
            item = self.chute_btns_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.chute_buttons.clear()

        # Dynamic Chute Buttons
        for name, (start, end) in list(self.chutes.items()):
            btn = QPushButton(f"{name.upper()} ({start}–{end})")
            btn.setProperty("class", "btnChute")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda chk, s=start, e=end, n=name: self._toggle_chute_selection(s, e, n))
            self.chute_btns_layout.addWidget(btn)
            self.chute_buttons[name] = btn

        # Combined Chute 1 + 2 Button
        btn_combo = QPushButton("CHUTE 1 + 2 (1–63)")
        btn_combo.setProperty("class", "btnChute")
        btn_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_combo.clicked.connect(self._toggle_chute_combo)
        self.chute_btns_layout.addWidget(btn_combo)
        self.chute_buttons["Chute 1 + 2"] = btn_combo

    def _toggle_chute_selection(self, start: int, end: int, name: str):
        chute_set = set(range(start, end + 1))
        if chute_set.issubset(self.selected_channels):
            self.selected_channels.difference_update(chute_set)
            for ch in chute_set:
                if ch not in self.held_channels:
                    self._set_cell_visual(ch, "idle")
            self.signals.log_emitted.emit("SYS", f"Deselected {name} (CH {start}–{end})", "info")
        else:
            self.selected_channels.update(chute_set)
            for ch in chute_set:
                self._set_cell_visual(ch, "selected")
            self.signals.log_emitted.emit("SYS", f"Selected {name} (CH {start}–{end}) [{len(chute_set)} channels]", "success")
        self._update_selection_count()

    def _toggle_chute_combo(self):
        c1 = self.chutes.get("Chute 1", (1, 32))
        c2 = self.chutes.get("Chute 2", (33, 63))
        combo_set = set(range(c1[0], c1[1] + 1)) | set(range(c2[0], c2[1] + 1))

        if combo_set.issubset(self.selected_channels):
            self.selected_channels.difference_update(combo_set)
            for ch in combo_set:
                if ch not in self.held_channels:
                    self._set_cell_visual(ch, "idle")
            self.signals.log_emitted.emit("SYS", "Deselected CHUTE 1 + CHUTE 2", "info")
        else:
            self.selected_channels.update(combo_set)
            for ch in combo_set:
                self._set_cell_visual(ch, "selected")
            self.signals.log_emitted.emit("SYS", f"Selected CHUTE 1 + CHUTE 2 ({len(combo_set)} channels)", "success")
        self._update_selection_count()

    def _action_add_chute_dialog(self):
        dialog = AddChuteDialog(list(self.chutes.keys()), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, start, end = dialog.get_chute_data()
            self.chutes[name] = (start, end)
            self._rebuild_chute_buttons()
            self.signals.log_emitted.emit("SYS", f"Added Chute '{name}' (CH {start}–{end})", "success")
            self._update_test_tab_chutes()

    def _create_controls_panel(self) -> QFrame:
        card = QFrame()
        card.setProperty("class", "card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_pulse(), "PULSE / FIRE")
        self.tabs.addTab(self._tab_repeat(), "CYCLE / REPEAT")
        self.tabs.addTab(self._tab_test(), "TEST SEQUENCE")
        self.tabs.addTab(self._tab_continuous(), "CONTINUOUS")
        self.tabs.addTab(self._tab_hold(), "HOLD VALVE")
        try:
            from sorter_panel import SorterPanel
            self.sorter_panel = SorterPanel(self)
            self.tabs.addTab(self.sorter_panel, "SORTER")
        except Exception as e:                      # sorter is optional (needs numpy + opencv)
            self.sorter_panel = None
            msg = f"Sorter tab unavailable: {e}"
            QTimer.singleShot(0, lambda: self.signals.log_emitted.emit("SYS", msg, "warn"))
        layout.addWidget(self.tabs, stretch=5)

        term_frame = self._create_terminal()
        layout.addWidget(term_frame, stretch=5)
        return card

    def _tab_pulse(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(10)

        # 1. Pulse Duration Setting
        hdr_pulse = QHBoxLayout()
        hdr_pulse.addWidget(QLabel("PULSE DURATION:"))
        self.lbl_pulse_val = QLabel("20 ms")
        self.lbl_pulse_val.setStyleSheet("color: #00f2fe; font-weight: 700; font-family: 'Consolas'; font-size: 14px;")
        hdr_pulse.addStretch()
        hdr_pulse.addWidget(self.lbl_pulse_val)
        layout.addLayout(hdr_pulse)

        self.slider_pulse = QSlider(Qt.Orientation.Horizontal)
        self.slider_pulse.setRange(1, 500)
        self.slider_pulse.setValue(20)
        self.slider_pulse.valueChanged.connect(self._on_pulse_slider_changed)
        layout.addWidget(self.slider_pulse)

        presets_pulse = QHBoxLayout()
        for p in [2, 5, 10, 20, 50, 100]:
            btn = QPushButton(f"{p} ms")
            btn.setProperty("class", "btnPreset")
            btn.clicked.connect(lambda chk, val=p: self.slider_pulse.setValue(val))
            presets_pulse.addWidget(btn)
        layout.addLayout(presets_pulse)

        # 2. Delay Time Setting (Flight / Actuation Delay)
        hdr_delay = QHBoxLayout()
        lbl_delay_title = QLabel("DELAY TIME (DROP / ACTUATION DELAY):")
        lbl_delay_title.setToolTip("Wait time before valve actuation in milliseconds (flight time)")
        hdr_delay.addWidget(lbl_delay_title)
        self.lbl_delay_val = QLabel("0 ms (Instant)")
        self.lbl_delay_val.setStyleSheet("color: #38bdf8; font-weight: 700; font-family: 'Consolas'; font-size: 14px;")
        hdr_delay.addStretch()
        hdr_delay.addWidget(self.lbl_delay_val)
        layout.addLayout(hdr_delay)

        delay_ctrl = QHBoxLayout()
        self.slider_delay = QSlider(Qt.Orientation.Horizontal)
        self.slider_delay.setRange(0, 1000)
        self.slider_delay.setValue(0)
        self.slider_delay.valueChanged.connect(self._on_delay_slider_changed)
        delay_ctrl.addWidget(self.slider_delay, stretch=4)

        self.spin_delay = QSpinBox()
        self.spin_delay.setRange(0, 5000)
        self.spin_delay.setValue(0)
        self.spin_delay.setSuffix(" ms")
        self.spin_delay.setFixedWidth(85)
        self.spin_delay.valueChanged.connect(self._on_delay_spin_changed)
        delay_ctrl.addWidget(self.spin_delay, stretch=1)
        layout.addLayout(delay_ctrl)

        presets_delay = QHBoxLayout()
        for d, txt in [(0, "0 ms"), (10, "10 ms"), (25, "25 ms"), (50, "50 ms"), (100, "100 ms"), (200, "200 ms")]:
            btn = QPushButton(txt)
            btn.setProperty("class", "btnPreset")
            btn.clicked.connect(lambda chk, val=d: self.spin_delay.setValue(val))
            presets_delay.addWidget(btn)
        layout.addLayout(presets_delay)

        # 3. Fire Selected Button
        self.btn_fire_selected = QPushButton("SELECT CHANNELS TO FIRE")
        self.btn_fire_selected.setProperty("class", "btnFire")
        self.btn_fire_selected.setEnabled(False)
        self.btn_fire_selected.clicked.connect(self._action_fire_selected)
        layout.addWidget(self.btn_fire_selected)

        # 4. Quick Single CH Fire
        quick_box = QHBoxLayout()
        quick_box.addWidget(QLabel("Quick Single CH:"))
        self.spin_quick_ch = QSpinBox()
        self.spin_quick_ch.setRange(1, NUM_CH)
        self.spin_quick_ch.setValue(1)
        self.spin_quick_ch.setFixedWidth(60)
        quick_box.addWidget(self.spin_quick_ch)

        btn_quick_fire = QPushButton("Fire CH")
        btn_quick_fire.clicked.connect(lambda: self._action_fire_single(self.spin_quick_ch.value(), self.slider_pulse.value()))
        quick_box.addWidget(btn_quick_fire)
        quick_box.addStretch()
        layout.addLayout(quick_box)

        layout.addStretch()
        return w

    def _tab_repeat(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setSpacing(8)

        grid.addWidget(QLabel("CHANNEL (1-63):"), 0, 0)
        self.spin_repeat_ch = QSpinBox()
        self.spin_repeat_ch.setRange(1, NUM_CH)
        self.spin_repeat_ch.setValue(1)
        grid.addWidget(self.spin_repeat_ch, 0, 1)

        grid.addWidget(QLabel("PULSE (ms):"), 0, 2)
        self.spin_repeat_pulse = QSpinBox()
        self.spin_repeat_pulse.setRange(1, MAX_PULSE_MS)
        self.spin_repeat_pulse.setValue(20)
        grid.addWidget(self.spin_repeat_pulse, 0, 3)

        grid.addWidget(QLabel("DELAY / GAP (ms):"), 1, 0)
        self.spin_repeat_gap = QSpinBox()
        self.spin_repeat_gap.setRange(0, MAX_GAP_MS)
        self.spin_repeat_gap.setValue(80)
        grid.addWidget(self.spin_repeat_gap, 1, 1)

        grid.addWidget(QLabel("COUNT:"), 1, 2)
        self.spin_repeat_count = QSpinBox()
        self.spin_repeat_count.setRange(1, 10000)
        self.spin_repeat_count.setValue(5)
        grid.addWidget(self.spin_repeat_count, 1, 3)

        layout.addLayout(grid)

        self.lbl_repeat_est = QLabel("Estimated duration: ~0.50 s")
        self.lbl_repeat_est.setStyleSheet("color: #f59e0b; font-family: 'Consolas'; font-size: 11px;")
        layout.addWidget(self.lbl_repeat_est)

        for s in (self.spin_repeat_pulse, self.spin_repeat_gap, self.spin_repeat_count):
            s.valueChanged.connect(self._update_repeat_est)

        btn_start_repeat = QPushButton("START REPEAT CYCLE")
        btn_start_repeat.setProperty("class", "btnAccentAction")
        btn_start_repeat.clicked.connect(self._action_start_repeat)
        layout.addWidget(btn_start_repeat)

        layout.addStretch()
        return w

    def _tab_test(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setSpacing(8)

        grid.addWidget(QLabel("FIRST CH:"), 0, 0)
        self.spin_test_first = QSpinBox()
        self.spin_test_first.setRange(1, NUM_CH)
        self.spin_test_first.setValue(1)
        grid.addWidget(self.spin_test_first, 0, 1)

        grid.addWidget(QLabel("LAST CH:"), 0, 2)
        self.spin_test_last = QSpinBox()
        self.spin_test_last.setRange(1, NUM_CH)
        self.spin_test_last.setValue(NUM_CH)
        grid.addWidget(self.spin_test_last, 0, 3)

        grid.addWidget(QLabel("PULSE (ms):"), 1, 0)
        self.spin_test_pulse = QSpinBox()
        self.spin_test_pulse.setRange(1, MAX_PULSE_MS)
        self.spin_test_pulse.setValue(30)
        grid.addWidget(self.spin_test_pulse, 1, 1)

        grid.addWidget(QLabel("DELAY / GAP (ms):"), 1, 2)
        self.spin_test_gap = QSpinBox()
        self.spin_test_gap.setRange(0, MAX_GAP_MS)
        self.spin_test_gap.setValue(70)
        grid.addWidget(self.spin_test_gap, 1, 3)

        layout.addLayout(grid)

        # Chute & Range presets for sequence testing
        self.test_chutes_layout = QHBoxLayout()
        self.test_chutes_layout.setSpacing(6)
        layout.addLayout(self.test_chutes_layout)
        self._update_test_tab_chutes()

        # Step Delay / Speed presets for Snake Game Effect
        speed_box = QHBoxLayout()
        speed_box.addWidget(QLabel("Snake Speed:"))
        for g, txt in [(30, "Fast (30ms)"), (50, "Med (50ms)"), (70, "Normal (70ms)"), (100, "Slow (100ms)")]:
            btn = QPushButton(txt)
            btn.setProperty("class", "btnPreset")
            btn.clicked.connect(lambda chk, val=g: self.spin_test_gap.setValue(val))
            speed_box.addWidget(btn)
        speed_box.addStretch()
        layout.addLayout(speed_box)

        btn_start_test = QPushButton("RUN SEQUENCE TEST (SNAKE EFFECT)")
        btn_start_test.setProperty("class", "btnAccentAction")
        btn_start_test.clicked.connect(self._action_start_test)
        layout.addWidget(btn_start_test)

        layout.addStretch()
        return w

    def _update_test_tab_chutes(self):
        """Populates range selector buttons in the Test Sequence tab."""
        while self.test_chutes_layout.count() > 0:
            item = self.test_chutes_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Standard chute range buttons
        c1 = self.chutes.get("Chute 1", (1, 32))
        c2 = self.chutes.get("Chute 2", (33, 63))

        test_ranges = [
            ("Chute 1 (1–32)", c1[0], c1[1]),
            ("Chute 2 (33–63)", c2[0], c2[1]),
            ("Chute 1+2 (1–63)", 1, NUM_CH),
            ("Demo (1–8)", 1, 8)
        ]
        for label, f, l in test_ranges:
            btn = QPushButton(label)
            btn.setProperty("class", "btnPreset")
            btn.clicked.connect(lambda chk, first=f, last=l: (self.spin_test_first.setValue(first), self.spin_test_last.setValue(last)))
            self.test_chutes_layout.addWidget(btn)

    def _tab_continuous(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(10)

        warn = QLabel("⚠️ Warning: Continuous mode pulses the valve non-stop until STOP ALL.")
        warn.setStyleSheet("background-color: rgba(245, 158, 11, 0.12); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); border-radius: 6px; padding: 6px; font-size: 11px;")
        layout.addWidget(warn)

        grid = QGridLayout()
        grid.setSpacing(8)

        grid.addWidget(QLabel("CHANNEL (1-63):"), 0, 0)
        self.spin_cont_ch = QSpinBox()
        self.spin_cont_ch.setRange(1, NUM_CH)
        self.spin_cont_ch.setValue(1)
        grid.addWidget(self.spin_cont_ch, 0, 1)

        grid.addWidget(QLabel("PULSE (ms):"), 0, 2)
        self.spin_cont_pulse = QSpinBox()
        self.spin_cont_pulse.setRange(1, MAX_PULSE_MS)
        self.spin_cont_pulse.setValue(20)
        grid.addWidget(self.spin_cont_pulse, 0, 3)

        grid.addWidget(QLabel("GAP (ms):"), 1, 0)
        self.spin_cont_gap = QSpinBox()
        self.spin_cont_gap.setRange(0, MAX_GAP_MS)
        self.spin_cont_gap.setValue(80)
        grid.addWidget(self.spin_cont_gap, 1, 1)

        layout.addLayout(grid)

        btn_box = QHBoxLayout()
        btn_start = QPushButton("START CONTINUOUS")
        btn_start.setProperty("class", "btnWarnAction")
        btn_start.clicked.connect(self._action_start_continuous)

        btn_stop = QPushButton("STOP")
        btn_stop.clicked.connect(self._action_emergency_stop)

        btn_box.addWidget(btn_start, stretch=3)
        btn_box.addWidget(btn_stop, stretch=1)
        layout.addLayout(btn_box)

        layout.addStretch()
        return w

    def _tab_hold(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(10)

        warn = QLabel("⚡ Warning: Holding a valve ON causes coil heating. Use for brief tests only.")
        warn.setStyleSheet("background-color: rgba(245, 158, 11, 0.12); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); border-radius: 6px; padding: 6px; font-size: 11px;")
        layout.addWidget(warn)

        row = QHBoxLayout()
        row.addWidget(QLabel("CHANNEL TO HOLD (1-63):"))
        self.spin_hold_ch = QSpinBox()
        self.spin_hold_ch.setRange(1, NUM_CH)
        self.spin_hold_ch.setValue(1)
        row.addWidget(self.spin_hold_ch)
        row.addStretch()
        layout.addLayout(row)

        btn_box = QHBoxLayout()
        btn_hold_on = QPushButton("HOLD VALVE ON")
        btn_hold_on.setProperty("class", "btnWarnAction")
        btn_hold_on.clicked.connect(self._action_hold_on)

        btn_hold_off = QPushButton("RELEASE (OFF)")
        btn_hold_off.clicked.connect(self._action_hold_off)

        btn_box.addWidget(btn_hold_on, stretch=2)
        btn_box.addWidget(btn_hold_off, stretch=2)
        layout.addLayout(btn_box)

        layout.addStretch()
        return w

    def _create_terminal(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("background-color: #060911; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px;")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("HARDWARE TELEMETRY & SERIAL LOG"))
        hdr.addStretch()

        btn_status = QPushButton("STATUS")
        btn_status.setProperty("class", "btnPreset")
        btn_status.clicked.connect(self._action_query_status)
        hdr.addWidget(btn_status)

        btn_clear = QPushButton("CLEAR")
        btn_clear.setProperty("class", "btnPreset")
        btn_clear.clicked.connect(lambda: self.terminal.clear())
        hdr.addWidget(btn_clear)

        self.chk_autoscroll = QCheckBox("AUTO-SCROLL")
        self.chk_autoscroll.setChecked(True)
        hdr.addWidget(self.chk_autoscroll)

        layout.addLayout(hdr)

        self.terminal = QTextEdit()
        self.terminal.setObjectName("terminalWindow")
        self.terminal.setReadOnly(True)
        layout.addWidget(self.terminal)

        cmd_box = QHBoxLayout()
        cmd_box.setSpacing(6)
        lbl_prompt = QLabel(">")
        lbl_prompt.setStyleSheet("color: #00f2fe; font-weight: 800; font-family: 'Consolas';")
        cmd_box.addWidget(lbl_prompt)

        self.input_cmd = QLineEdit()
        self.input_cmd.setPlaceholderText("Send raw command (e.g. STATUS, FIRE:1:10, PING)...")
        self.input_cmd.returnPressed.connect(self._action_send_raw)
        cmd_box.addWidget(self.input_cmd)

        btn_send = QPushButton("SEND")
        btn_send.setProperty("class", "btnPreset")
        btn_send.clicked.connect(self._action_send_raw)
        cmd_box.addWidget(btn_send)

        layout.addLayout(cmd_box)
        return frame

    def _setup_shortcuts(self):
        sc_space = QShortcut(QKeySequence("Space"), self)
        sc_space.activated.connect(self._action_emergency_stop)
        sc_esc = QShortcut(QKeySequence("Escape"), self)
        sc_esc.activated.connect(self._action_emergency_stop)

    def _toggle_audio(self):
        self.audio_enabled = not self.audio_enabled
        self.btn_audio.setText("🔊" if self.audio_enabled else "🔇")

    def _play_valve_sound(self):
        if not self.audio_enabled or winsound is None:
            return
        try:
            threading.Thread(target=lambda: winsound.Beep(950, 25), daemon=True).start()
        except Exception:
            pass

    def _set_cell_visual(self, ch: int, state: str):
        btn = self.valve_buttons.get(ch)
        if not btn:
            return
        if state == "selected":
            btn.setStyleSheet(STYLE_VALVE_SELECTED)
        elif state == "firing":
            btn.setStyleSheet(STYLE_VALVE_FIRING)
        elif state == "holding":
            btn.setStyleSheet(STYLE_VALVE_HOLDING)
        else:
            btn.setStyleSheet(STYLE_VALVE_IDLE)
        btn.setProperty("state", state)
        btn.update()

    def _on_valve_cell_clicked(self, ch: int):
        if ch in self.selected_channels:
            self.selected_channels.remove(ch)
            self._set_cell_visual(ch, "idle")
        else:
            self.selected_channels.add(ch)
            self._set_cell_visual(ch, "selected")
        self._update_selection_count()

    def _update_selection_count(self):
        count = len(self.selected_channels)
        self.lbl_selected_count.setText(f"{count} / {NUM_CH} SELECTED")
        if count == 0:
            self.btn_fire_selected.setText("SELECT CHANNELS TO FIRE")
            self.btn_fire_selected.setEnabled(False)
        else:
            delay = self.spin_delay.value() if hasattr(self, 'spin_delay') else 0
            delay_str = f" [DELAY {delay}ms]" if delay > 0 else ""
            self.btn_fire_selected.setText(f"FIRE SELECTED ({count} CHANNEL{'S' if count > 1 else ''}){delay_str}")
            self.btn_fire_selected.setEnabled(True)

    def _select_all(self):
        self.selected_channels = set(range(1, NUM_CH + 1))
        for ch in range(1, NUM_CH + 1):
            self._set_cell_visual(ch, "selected")
        self._update_selection_count()

    def _select_none(self):
        self.selected_channels.clear()
        for ch in range(1, NUM_CH + 1):
            if ch not in self.held_channels:
                self._set_cell_visual(ch, "idle")
        self._update_selection_count()

    def _select_invert(self):
        new_sel = set()
        for ch in range(1, NUM_CH + 1):
            if ch not in self.selected_channels:
                new_sel.add(ch)
                self._set_cell_visual(ch, "selected")
            else:
                self._set_cell_visual(ch, "idle")
        self.selected_channels = new_sel
        self._update_selection_count()

    def _select_range(self, start: int, end: int):
        self._select_none()
        for ch in range(start, end + 1):
            self.selected_channels.add(ch)
            self._set_cell_visual(ch, "selected")
        self._update_selection_count()

    def _select_odds(self):
        self._select_none()
        for ch in range(1, NUM_CH + 1, 2):
            self.selected_channels.add(ch)
            self._set_cell_visual(ch, "selected")
        self._update_selection_count()

    def _select_evens(self):
        self._select_none()
        for ch in range(2, NUM_CH + 1, 2):
            self.selected_channels.add(ch)
            self._set_cell_visual(ch, "selected")
        self._update_selection_count()

    def _on_pulse_slider_changed(self, val: int):
        self.lbl_pulse_val.setText(f"{val} ms")

    def _on_delay_slider_changed(self, val: int):
        if self.spin_delay.value() != val:
            self.spin_delay.blockSignals(True)
            self.spin_delay.setValue(val)
            self.spin_delay.blockSignals(False)
        self.lbl_delay_val.setText(f"{val} ms (Instant)" if val == 0 else f"{val} ms")
        self._update_selection_count()

    def _on_delay_spin_changed(self, val: int):
        if val <= self.slider_delay.maximum() and self.slider_delay.value() != val:
            self.slider_delay.blockSignals(True)
            self.slider_delay.setValue(val)
            self.slider_delay.blockSignals(False)
        self.lbl_delay_val.setText(f"{val} ms (Instant)" if val == 0 else f"{val} ms")
        self._update_selection_count()

    def _update_repeat_est(self):
        pulse = self.spin_repeat_pulse.value()
        gap = self.spin_repeat_gap.value()
        cnt = self.spin_repeat_count.value()
        total_s = (pulse + gap) * cnt / 1000.0
        self.lbl_repeat_est.setText(f"Estimated duration: ~{total_s:.2f} s")

    def _flash_cell_firing(self, ch: int, duration_ms: int = 300):
        self._set_cell_visual(ch, "firing")
        self._play_valve_sound()
        reset_ms = max(450, min(duration_ms, 2500))
        QTimer.singleShot(reset_ms, lambda: self._revert_cell_state(ch))

    def _revert_cell_state(self, ch: int):
        if ch in self.held_channels:
            self._set_cell_visual(ch, "holding")
        elif ch in self.selected_channels:
            self._set_cell_visual(ch, "selected")
        else:
            self._set_cell_visual(ch, "idle")

    def _on_channel_fired(self, ch: int, pulse_ms: int):
        self._flash_cell_firing(ch, pulse_ms)

    def _on_channels_multi_fired(self, channels: list, pulse_ms: int):
        for ch in channels:
            self._flash_cell_firing(ch, pulse_ms)

    def _on_channel_holding(self, ch: int, is_holding: bool):
        if is_holding:
            self.held_channels.add(ch)
            self._set_cell_visual(ch, "holding")
        else:
            self.held_channels.discard(ch)
            self._set_cell_visual(ch, "selected" if ch in self.selected_channels else "idle")

    def _on_all_stopped(self):
        self.held_channels.clear()
        for ch in range(1, NUM_CH + 1):
            self._set_cell_visual(ch, "selected" if ch in self.selected_channels else "idle")

    def _append_log(self, category: str, message: str, level: str):
        t_str = time.strftime("%H:%M:%S")
        color_map = {
            "SYS": "#94a3b8",
            "TX": "#60a5fa",
            "RX": "#34d399",
            "EVENT": "#fbbf24",
            "ERR": "#f87171"
        }
        tag_color = color_map.get(category, "#cbd5e1")
        msg_color = "#f87171" if level == "error" else ("#34d399" if level == "success" else "#cbd5e1")

        html = f"<span style='color: #475569;'>[{t_str}]</span> " \
               f"<b style='color: {tag_color};'>[{category}]</b> " \
               f"<span style='color: {msg_color};'>{message}</span>"

        self.terminal.append(html)
        if self.chk_autoscroll.isChecked():
            self.terminal.moveCursor(self.terminal.textCursor().MoveOperation.End)

    def _populate_ports(self):
        self.combo_ports.clear()
        ports = list(serial.tools.list_ports.comports())
        target_idx = 0
        for i, p in enumerate(ports):
            desc = p.description or ""
            is_esp = any(k in desc.lower() or k in (p.hwid or "").lower() for k in ["cp210", "ch340", "ftdi", "uart", "esp32", "usb serial"])
            label = f"{p.device} {'★ (ESP32)' if is_esp else ''} - {desc}"
            self.combo_ports.addItem(label, p.device)
            if p.device == "COM13" or is_esp:
                target_idx = i

        if self.combo_ports.count() > 0:
            self.combo_ports.setCurrentIndex(target_idx)
        else:
            self.combo_ports.addItem("No COM ports found", "")

    def _try_auto_connect(self):
        port = self.combo_ports.currentData()
        if port:
            self._connect(port, int(self.combo_baud.currentText()), mock=False)

    def _toggle_connection(self):
        if self.is_connected:
            self._disconnect()
        else:
            port = self.combo_ports.currentData() or "COM13"
            baud = int(self.combo_baud.currentText())
            mock = self.chk_mock.isChecked()
            self._connect(port, baud, mock)

    def _connect(self, port: str, baud: int, mock: bool):
        def _task():
            with self.lock:
                if self.ej:
                    try:
                        self.ej.close()
                    except Exception:
                        pass
                    self.ej = None

                self.port = port
                self.baud = baud
                self.is_mock = mock

                if mock:
                    self.is_connected = True
                    self.signals.log_emitted.emit("SYS", f"Connected in SIMULATOR mode ({port} @ {baud})", "warn")
                    self.signals.connection_changed.emit(True, True, port)
                    return

                try:
                    self.signals.log_emitted.emit("SYS", f"Connecting to {port} @ {baud}...", "info")
                    ej = Ejector(port, baud=baud, timeout=1.5, boot_wait=1.8, keepalive_s=2.0)
                    self.ej = ej
                    self.is_connected = True
                    self.signals.log_emitted.emit("RX", f"Hardware connected! Board ready on {port}", "success")
                    self.signals.connection_changed.emit(True, False, port)

                    self._monitor_running = True
                    self._monitor_thread = threading.Thread(target=self._event_monitor_loop, daemon=True)
                    self._monitor_thread.start()
                except Exception as e:
                    self.ej = None
                    self.is_connected = False
                    err_str = str(e)
                    if isinstance(e, PermissionError) or "Access is denied" in err_str:
                        self.signals.log_emitted.emit("ERR", f"Connection failed: {port} is in use by another process or window. Please close any other open terminals/monitors.", "error")
                    else:
                        self.signals.log_emitted.emit("ERR", f"Connection failed: {err_str}", "error")
                    self.signals.connection_changed.emit(False, False, port)

        threading.Thread(target=_task, daemon=True).start()

    def _disconnect(self):
        def _task():
            self._monitor_running = False
            with self.lock:
                if self.ej:
                    try:
                        self.ej.close()
                    except Exception:
                        pass
                    self.ej = None
                self.is_connected = False
                self.signals.log_emitted.emit("SYS", "Disconnected from controller", "info")
                self.signals.connection_changed.emit(False, False, self.port)

        threading.Thread(target=_task, daemon=True).start()

    def _on_connection_changed(self, connected: bool, is_mock: bool, port: str):
        self.is_connected = connected
        self.is_mock = is_mock
        if connected:
            if is_mock:
                self.lbl_status.setText(f"● SIMULATOR ({port})")
                self.lbl_status.setStyleSheet("color: #f59e0b; border-color: rgba(245, 158, 11, 0.4);")
            else:
                self.lbl_status.setText(f"● CONNECTED ({port})")
                self.lbl_status.setStyleSheet("color: #10b981; border-color: rgba(16, 185, 129, 0.4);")
            self.btn_connect.setText("DISCONNECT")
            self.btn_connect.setProperty("class", "btnDisconnect")
        else:
            self.lbl_status.setText("● DISCONNECTED")
            self.lbl_status.setStyleSheet("color: #ef4444; border-color: rgba(239, 68, 68, 0.4);")
            self.btn_connect.setText("CONNECT")
            self.btn_connect.setProperty("class", "btnConnect")
            self.btn_ping.setText("⚡ PING")

        self.btn_connect.style().unpolish(self.btn_connect)
        self.btn_connect.style().polish(self.btn_connect)

    def _event_monitor_loop(self):
        while self._monitor_running:
            time.sleep(0.05)
            with self.lock:
                ej = self.ej
            if not ej:
                break
            try:
                if ej.events:
                    with self.lock:
                        while ej.events:
                            ev = ej.events.pop(0)
                            self.signals.log_emitted.emit("EVENT", ev, "accent")
            except Exception:
                pass

    def _action_ping(self):
        def _task():
            t0 = time.perf_counter()
            if self.is_mock:
                time.sleep(0.005)
                ms = (time.perf_counter() - t0) * 1000
                self.signals.log_emitted.emit("TX", "PING -> PONG (simulated)", "info")
                self.signals.ping_received.emit(ms)
                return
            with self.lock:
                ej = self.ej
            if not ej:
                self.signals.log_emitted.emit("ERR", "Not connected", "error")
                return
            self.signals.log_emitted.emit("TX", "PING", "info")
            try:
                ok = ej.ping()
                ms = (time.perf_counter() - t0) * 1000
                if ok:
                    self.signals.log_emitted.emit("RX", f"PONG ({ms:.1f} ms)", "success")
                    self.signals.ping_received.emit(ms)
                else:
                    self.signals.log_emitted.emit("ERR", "No reply to PING", "error")
            except Exception as e:
                self.signals.log_emitted.emit("ERR", str(e), "error")

        threading.Thread(target=_task, daemon=True).start()

    def _auto_ping(self):
        if self.is_connected and not self.is_mock:
            self._action_ping()

    def _on_ping_received(self, ms: float):
        self.btn_ping.setText(f"⚡ {ms:.1f} ms")

    def _action_query_status(self):
        def _task():
            if self.is_mock:
                self.signals.log_emitted.emit("RX", "STATUS: MOCK_OK:ALL_IDLE", "info")
                return
            with self.lock:
                ej = self.ej
            if not ej:
                self.signals.log_emitted.emit("ERR", "Not connected", "error")
                return
            try:
                self.signals.log_emitted.emit("TX", "STATUS", "info")
                rep = ej.status()
                self.signals.log_emitted.emit("RX", rep, "info")
            except Exception as e:
                self.signals.log_emitted.emit("ERR", str(e), "error")

        threading.Thread(target=_task, daemon=True).start()

    def _action_fire_single(self, ch: int, pulse_ms: int):
        delay_ms = self.spin_delay.value() if hasattr(self, 'spin_delay') else 0

        def _task():
            if delay_ms > 0:
                self.signals.log_emitted.emit("SYS", f"Delaying {delay_ms} ms before firing CH{ch}...", "info")
                time.sleep(delay_ms / 1000.0)

            self.signals.channel_fired.emit(ch, pulse_ms)
            self.signals.log_emitted.emit("TX", f"FIRE:{ch}:{pulse_ms}" + (f" (delayed {delay_ms}ms)" if delay_ms > 0 else ""), "info")
            if self.is_mock:
                self.signals.log_emitted.emit("RX", f"OK:FIRE:{ch}", "success")
                return
            with self.lock:
                ej = self.ej
            if not ej:
                self.signals.log_emitted.emit("ERR", "Not connected", "error")
                return
            try:
                rep = ej.fire(ch, pulse_ms)
                self.signals.log_emitted.emit("RX", rep, "success")
            except Exception as e:
                self.signals.log_emitted.emit("ERR", str(e), "error")

        threading.Thread(target=_task, daemon=True).start()

    def _action_fire_selected(self):
        chs = sorted(list(self.selected_channels))
        if not chs:
            return
        pulse_ms = self.slider_pulse.value()
        delay_ms = self.spin_delay.value() if hasattr(self, 'spin_delay') else 0

        if delay_ms > 0:
            self.btn_fire_selected.setText(f"⏳ DELAYING {delay_ms}ms BEFORE FIRE...")
            self.btn_fire_selected.setEnabled(False)

        def _task():
            if delay_ms > 0:
                self.signals.log_emitted.emit("SYS", f"Delaying {delay_ms} ms before firing {len(chs)} channels...", "info")
                time.sleep(delay_ms / 1000.0)

            self.signals.channels_multi_fired.emit(chs, pulse_ms)
            QTimer.singleShot(0, self._update_selection_count)

            if len(chs) == 1:
                self.signals.log_emitted.emit("TX", f"FIRE:{chs[0]}:{pulse_ms}" + (f" (delayed {delay_ms}ms)" if delay_ms > 0 else ""), "info")
            else:
                self.signals.log_emitted.emit("TX", f"FIRE_MULTI:[{','.join(map(str, chs))}]:{pulse_ms}" + (f" (delayed {delay_ms}ms)" if delay_ms > 0 else ""), "info")

            if self.is_mock:
                self.signals.log_emitted.emit("RX", "OK:FIRE_MULTI", "success")
                return

            with self.lock:
                ej = self.ej
            if not ej:
                self.signals.log_emitted.emit("ERR", "Not connected", "error")
                return
            try:
                if len(chs) == 1:
                    rep = ej.fire(chs[0], pulse_ms)
                else:
                    rep = ej.fire_multi(chs, pulse_ms)
                self.signals.log_emitted.emit("RX", rep, "success")
            except Exception as e:
                self.signals.log_emitted.emit("ERR", str(e), "error")

        threading.Thread(target=_task, daemon=True).start()

    def _sequence_step_on(self, ch: int, seq_id: int):
        if self._sequence_id != seq_id:
            return
        # Strict Snake Game visual guarantee: extinguish any previously lit valve
        if self._active_snake_ch is not None and self._active_snake_ch != ch:
            self._revert_cell_state(self._active_snake_ch)
        self._active_snake_ch = ch
        self._set_cell_visual(ch, "firing")
        self._play_valve_sound()

    def _sequence_step_off(self, ch: int, seq_id: int):
        if self._sequence_id != seq_id:
            return
        self._revert_cell_state(ch)
        if self._active_snake_ch == ch:
            self._active_snake_ch = None

    def _action_start_repeat(self):
        ch = self.spin_repeat_ch.value()
        pulse = self.spin_repeat_pulse.value()
        gap = self.spin_repeat_gap.value()
        count = self.spin_repeat_count.value()

        self._sequence_id += 1
        seq_id = self._sequence_id
        step_ms = pulse + gap
        on_time_ms = max(15, min(pulse, step_ms - 10 if step_ms > 25 else pulse))

        for i in range(count):
            t_on = i * step_ms
            t_off = t_on + on_time_ms
            QTimer.singleShot(t_on, lambda c=ch, s=seq_id: self._sequence_step_on(c, s))
            QTimer.singleShot(t_off, lambda c=ch, s=seq_id: self._sequence_step_off(c, s))

        def _task():
            self.signals.log_emitted.emit("TX", f"REPEAT CH{ch} {pulse}ms/{gap}ms x{count}", "info")
            if self.is_mock:
                self.signals.log_emitted.emit("RX", f"OK:REPEAT:{ch}", "success")
                time.sleep((pulse + gap) * count / 1000.0)
                self.signals.log_emitted.emit("EVENT", "DONE:REPEAT", "accent")
                return
            with self.lock:
                ej = self.ej
            if not ej:
                self.signals.log_emitted.emit("ERR", "Not connected", "error")
                return
            try:
                rep = ej.repeat(ch, pulse, gap, count)
                self.signals.log_emitted.emit("RX", rep, "success")
            except Exception as e:
                self.signals.log_emitted.emit("ERR", str(e), "error")

        threading.Thread(target=_task, daemon=True).start()

    def _action_start_test(self):
        first = self.spin_test_first.value()
        last = self.spin_test_last.value()
        pulse = self.spin_test_pulse.value()
        gap = self.spin_test_gap.value()

        self._sequence_id += 1
        seq_id = self._sequence_id

        step_ms = pulse + gap
        # Turns OFF strictly before the next channel turns ON (Snake Effect)
        on_time_ms = max(15, min(pulse, step_ms - 10 if step_ms > 25 else pulse))

        for c in range(first, last + 1):
            t_on = (c - first) * step_ms
            t_off = t_on + on_time_ms

            QTimer.singleShot(t_on, lambda ch=c, s=seq_id: self._sequence_step_on(ch, s))
            QTimer.singleShot(t_off, lambda ch=c, s=seq_id: self._sequence_step_off(ch, s))

        def _task():
            self.signals.log_emitted.emit("TX", f"TEST CH{first}..{last} {pulse}ms/{gap}ms (Snake)", "info")
            if self.is_mock:
                self.signals.log_emitted.emit("RX", f"OK:TEST:{first}-{last}", "success")
                time.sleep((pulse + gap) * (last - first + 1) / 1000.0)
                self.signals.log_emitted.emit("EVENT", "DONE:TEST", "accent")
                return
            with self.lock:
                ej = self.ej
            if not ej:
                self.signals.log_emitted.emit("ERR", "Not connected", "error")
                return
            try:
                rep = ej.test_sequence(first, last, pulse, gap)
                self.signals.log_emitted.emit("RX", rep, "success")
            except Exception as e:
                self.signals.log_emitted.emit("ERR", str(e), "error")

        threading.Thread(target=_task, daemon=True).start()

    def _action_start_continuous(self):
        ch = self.spin_cont_ch.value()
        pulse = self.spin_cont_pulse.value()
        gap = self.spin_cont_gap.value()
        self.signals.channel_holding.emit(ch, True)

        def _task():
            self.signals.log_emitted.emit("TX", f"CONT CH{ch} {pulse}ms/{gap}ms", "warn")
            if self.is_mock:
                self.signals.log_emitted.emit("RX", f"OK:CONT:{ch}", "warn")
                return
            with self.lock:
                ej = self.ej
            if not ej:
                self.signals.log_emitted.emit("ERR", "Not connected", "error")
                return
            try:
                rep = ej.continuous(ch, pulse, gap)
                self.signals.log_emitted.emit("RX", rep, "warn")
            except Exception as e:
                self.signals.log_emitted.emit("ERR", str(e), "error")

        threading.Thread(target=_task, daemon=True).start()

    def _action_hold_on(self):
        ch = self.spin_hold_ch.value()
        self.signals.channel_holding.emit(ch, True)

        def _task():
            self.signals.log_emitted.emit("TX", f"HOLD_ON:{ch}", "warn")
            if self.is_mock:
                self.signals.log_emitted.emit("RX", f"OK:HOLD_ON:{ch}", "warn")
                return
            with self.lock:
                ej = self.ej
            if not ej:
                self.signals.log_emitted.emit("ERR", "Not connected", "error")
                return
            try:
                rep = ej.hold_on(ch)
                self.signals.log_emitted.emit("RX", rep, "warn")
            except Exception as e:
                self.signals.log_emitted.emit("ERR", str(e), "error")

        threading.Thread(target=_task, daemon=True).start()

    def _action_hold_off(self):
        ch = self.spin_hold_ch.value()
        self.signals.channel_holding.emit(ch, False)

        def _task():
            self.signals.log_emitted.emit("TX", f"HOLD_OFF:{ch}", "info")
            if self.is_mock:
                self.signals.log_emitted.emit("RX", f"OK:HOLD_OFF:{ch}", "info")
                return
            with self.lock:
                ej = self.ej
            if not ej:
                self.signals.log_emitted.emit("ERR", "Not connected", "error")
                return
            try:
                rep = ej.hold_off(ch)
                self.signals.log_emitted.emit("RX", rep, "info")
            except Exception as e:
                self.signals.log_emitted.emit("ERR", str(e), "error")

        threading.Thread(target=_task, daemon=True).start()

    def _action_emergency_stop(self):
        self._sequence_id += 1
        if getattr(self, "sorter_panel", None) is not None:
            self.sorter_panel.stop()
        if self._active_snake_ch is not None:
            prev = self._active_snake_ch
            self._active_snake_ch = None
            self._revert_cell_state(prev)
        self.signals.all_stopped.emit()

        def _task():
            self.signals.log_emitted.emit("TX", "STOP + ALL_OFF (EMERGENCY)", "warn")
            if self.is_mock:
                self.signals.log_emitted.emit("RX", "STOPPED (simulated)", "warn")
                return
            with self.lock:
                ej = self.ej
            if not ej:
                return
            try:
                ej.stop()
                ej.all_off()
                self.signals.log_emitted.emit("RX", "STOPPED & ALL CHANNELS OFF", "warn")
            except Exception as e:
                self.signals.log_emitted.emit("ERR", str(e), "error")

        threading.Thread(target=_task, daemon=True).start()

    def _action_send_raw(self):
        cmd = self.input_cmd.text().strip()
        if not cmd:
            return
        self.cmd_history.append(cmd)
        self.cmd_history_idx = len(self.cmd_history)
        self.input_cmd.clear()

        def _task():
            self.signals.log_emitted.emit("TX (RAW)", cmd, "info")
            if self.is_mock:
                self.signals.log_emitted.emit("RX (RAW)", f"MOCK_OK:{cmd}", "info")
                return
            with self.lock:
                ej = self.ej
            if not ej:
                self.signals.log_emitted.emit("ERR", "Not connected", "error")
                return
            try:
                rep = ej.send(cmd)
                self.signals.log_emitted.emit("RX (RAW)", rep, "info")
            except Exception as e:
                self.signals.log_emitted.emit("ERR", str(e), "error")

        threading.Thread(target=_task, daemon=True).start()

    def schedule_eject(self, channels: Iterable[int], fire_at: float, pulse_us: int) -> bool:
        """
        Thread-safe timed ejection used by the sorter. fire_at = time.monotonic()
        value when the valves must OPEN. Returns False if there is no hardware.
        """
        chs = sorted(set(int(c) for c in channels if 1 <= int(c) <= NUM_CH))
        if not chs:
            return False
        delay_s = max(0.0, fire_at - time.monotonic())
        pulse_ms = max(1, int(round(pulse_us / 1000.0)))

        def _visual():
            self.signals.channels_multi_fired.emit(chs, pulse_ms)

        if not self.is_mock:
            with self.lock:
                ej = self.ej
            if not ej:
                return False
            ej.sched_at(fire_at, chs, pulse_us)      # raises EjectorError on ERR:
        threading.Timer(delay_s, _visual).start()
        return True

    # ---------------- Public Integration API ----------------
    def select_channels(self, channels: Iterable[int]):
        """Select specific channel numbers (turns them solid green)."""
        self.selected_channels = set(int(c) for c in channels if 1 <= int(c) <= NUM_CH)
        for ch in range(1, NUM_CH + 1):
            self._set_cell_visual(ch, "selected" if ch in self.selected_channels else "idle")
        self._update_selection_count()

    def select_chute(self, chute_number: int):
        """Selects Chute 1 (1-32) or Chute 2 (33-63) or both (12 or 3)."""
        if chute_number == 1:
            self._select_range(1, 32)
        elif chute_number == 2:
            self._select_range(33, 63)
        elif chute_number in (12, 3):
            self._select_range(1, 63)

    def select_all_channels(self):
        """Selects all 63 channels."""
        self._select_all()

    def clear_selection(self):
        """Clears all channel selections."""
        self._select_none()

    def fire_selected_valves(self, pulse_ms: Optional[int] = None, delay_ms: Optional[int] = None):
        """Fires all currently selected channels with optional pulse and delay."""
        if pulse_ms is not None:
            self.slider_pulse.setValue(pulse_ms)
        if delay_ms is not None:
            self.spin_delay.setValue(delay_ms)
        self._action_fire_selected()

    def fire_channel(self, ch: int, pulse_ms: int = 20, delay_ms: int = 0):
        """Fires a single channel with optional delay."""
        if hasattr(self, 'spin_delay') and delay_ms > 0:
            self.spin_delay.setValue(delay_ms)
        self._action_fire_single(ch, pulse_ms)

    def stop_all(self):
        """Emergency stops all valves immediately."""
        self._action_emergency_stop()

    def connect_hardware(self, port: str = "COM13", baud: int = BAUD, mock: bool = False):
        """Initiates connection to hardware."""
        self._connect(port, baud, mock)

    def disconnect_hardware(self):
        """Disconnects serial port cleanly."""
        self._disconnect()

    def cleanup(self):
        """Cleanly releases serial ports, stops timers, and cancels sequences."""
        self._sequence_id += 1
        if hasattr(self, 'ping_timer') and self.ping_timer.isActive():
            self.ping_timer.stop()
        self._monitor_running = False
        with self.lock:
            if self.ej:
                try:
                    self.ej.close()
                except Exception:
                    pass
                self.ej = None
            self.is_connected = False

    def closeEvent(self, event):
        self.cleanup()
        event.accept()


# =============================================================================
# Standalone Main Window Wrapper
# =============================================================================
class EjectorGUI(QMainWindow):
    """
    Main Application Window wrapper hosting EjectorWidget.
    Supports standalone execution and clean shutdown.
    """
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("TCM 63-Channel Ejector Controller - Industrial Dashboard")
        self.resize(1360, 860)
        self.setMinimumSize(1100, 720)
        self.widget = EjectorWidget(self)
        self.setCentralWidget(self.widget)

    def closeEvent(self, event):
        self.widget.cleanup()
        super().closeEvent(event)


# =============================================================================
# Application Entry Point
# =============================================================================
def run_app():
    ap = argparse.ArgumentParser(description="TCM 63-Channel Ejector Controller")
    ap.add_argument("port", nargs="?", default=None, help="serial port (e.g. COM13)")
    ap.add_argument("--demo", action="store_true", help="run short self-test on CH1-CH8")
    ap.add_argument("--console", action="store_true", help="run in terminal interactive console mode")
    args = ap.parse_args()

    # If demo or console explicitly requested with a port, run CLI mode
    if args.demo or args.console:
        port = args.port or "COM13"
        try:
            with Ejector(port) as ej:
                demo(ej) if args.demo else console(ej)
        except (serial.SerialException, EjectorError) as e:
            sys.exit(f"Error: {e}")
        return

    # Default: Launch the full PyQt6 desktop GUI
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS_STYLE)
    win = EjectorGUI()
    if args.port:
        win.widget.combo_ports.setCurrentText(args.port)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_app()
