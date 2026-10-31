import sys
import serial
import serial.tools.list_ports
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QSlider, QGroupBox, QComboBox, QFormLayout, 
                             QMessageBox, QDialog, QLineEdit, QGridLayout)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont

class Keypad(QDialog):
    def __init__(self, current_val, callback, parent=None):
        super().__init__(parent)
        self.callback = callback
        self.setWindowTitle("Enter Value")
        self.setFixedSize(300, 400)
        self.setStyleSheet("background-color: #222; color: white;")
        
        layout = QVBoxLayout(self)
        
        self.display = QLineEdit(str(current_val))
        self.display.setReadOnly(True)
        self.display.setAlignment(Qt.AlignRight)
        self.display.setStyleSheet("font-size: 24px; padding: 10px; background: #111; border: 1px solid #555;")
        layout.addWidget(self.display)
        
        grid = QGridLayout()
        buttons = [
            ('7', 0, 0), ('8', 0, 1), ('9', 0, 2),
            ('4', 1, 0), ('5', 1, 1), ('6', 1, 2),
            ('1', 2, 0), ('2', 2, 1), ('3', 2, 2),
            ('0', 3, 0), ('.', 3, 1), ('C', 3, 2)
        ]
        
        for btn_text, row, col in buttons:
            btn = QPushButton(btn_text)
            btn.setFixedSize(80, 60)
            btn.setStyleSheet("QPushButton { font-size: 20px; font-weight: bold; background-color: #333; border: none; border-radius: 5px; } "
                              "QPushButton:pressed { background-color: #555; }")
            btn.clicked.connect(lambda checked, t=btn_text: self.on_btn_click(t))
            grid.addWidget(btn, row, col)
            
        layout.addLayout(grid)
        
        btn_ok = QPushButton("ENTER")
        btn_ok.setFixedHeight(50)
        btn_ok.setStyleSheet("background-color: #00e676; color: black; font-weight: bold; font-size: 20px; border-radius: 5px;")
        btn_ok.clicked.connect(self.on_ok)
        layout.addWidget(btn_ok)

    def on_btn_click(self, text):
        if text == 'C':
            self.display.setText("")
        else:
            if text == '.' and '.' in self.display.text():
                return
            self.display.setText(self.display.text() + text)
            
    def on_ok(self):
        val = self.display.text()
        if val:
            self.callback(val)
        self.accept()

class EjectorTester(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LAMTON Premium Ejector Tester")
        self.setMinimumSize(800, 600)
        self.setStyleSheet("""
            QMainWindow { background-color: #121212; }
            QLabel { color: #ffffff; font-family: 'Segoe UI', Arial; }
            QGroupBox { 
                border: 2px solid #333; 
                border-radius: 8px; 
                margin-top: 1.5ex; 
                color: #00e5ff; 
                font-weight: bold; 
                font-size: 14px;
            }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; padding: 0 10px; }
        """)

        self.serial_port = None
        self.test_timer = QTimer(self)
        self.test_timer.timeout.connect(self._on_timer_tick)
        
        self.firing = False
        self.auto_running = False

        self.init_ui()
        self.refresh_ports()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # HEADER & CONNECTION
        header_layout = QHBoxLayout()
        title = QLabel("EJECTOR DIAGNOSTICS (STANDALONE)")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #ff9800;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.combo_ports = QComboBox()
        self.combo_ports.setMinimumWidth(100)
        self.combo_ports.setStyleSheet("background: #222; color: white; padding: 5px; font-size: 14px;")
        header_layout.addWidget(self.combo_ports)

        btn_refresh = QPushButton("🔄")
        btn_refresh.setFixedSize(35, 35)
        btn_refresh.setStyleSheet("background: #444; border-radius: 4px;")
        btn_refresh.clicked.connect(self.refresh_ports)
        header_layout.addWidget(btn_refresh)

        self.btn_connect = QPushButton("🔌 CONNECT")
        self.btn_connect.setFixedSize(120, 35)
        self.btn_connect.setStyleSheet("background-color: #00897b; color: white; font-weight: bold; border-radius: 4px;")
        self.btn_connect.clicked.connect(self.toggle_connection)
        header_layout.addWidget(self.btn_connect)

        self.lbl_status = QLabel("DISCONNECTED")
        self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #ff1744;")
        self.lbl_status.setMinimumWidth(150)
        self.lbl_status.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.lbl_status)

        main_layout.addLayout(header_layout)

        # CHANNEL SELECTION
        ch_grp = QGroupBox("CHANNEL SELECTION")
        ch_layout = QVBoxLayout(ch_grp)
        
        self.lbl_channel = QLabel("EJECTOR: 1")
        self.lbl_channel.setStyleSheet("font-size: 40px; font-weight: bold; color: white;")
        self.lbl_channel.setAlignment(Qt.AlignCenter)
        ch_layout.addWidget(self.lbl_channel)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(1)
        self.slider.setMaximum(63)
        self.slider.setValue(1)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal { border: 1px solid #333; height: 12px; background: #222; border-radius: 6px; }
            QSlider::handle:horizontal { background: #00e5ff; border: 1px solid #00e5ff; width: 24px; margin: -6px 0; border-radius: 12px; }
        """)
        self.slider.valueChanged.connect(self.on_slider_change)
        ch_layout.addWidget(self.slider)

        nav_layout = QHBoxLayout()
        btn_prev = QPushButton("< PREV VALVE")
        btn_prev.setFixedHeight(40)
        btn_prev.setStyleSheet("background: #333; color: white; font-weight: bold; border-radius: 5px;")
        btn_prev.clicked.connect(lambda: self.slider.setValue(max(1, self.slider.value() - 1)))
        
        btn_next = QPushButton("NEXT VALVE >")
        btn_next.setFixedHeight(40)
        btn_next.setStyleSheet("background: #333; color: white; font-weight: bold; border-radius: 5px;")
        btn_next.clicked.connect(lambda: self.slider.setValue(min(63, self.slider.value() + 1)))
        
        nav_layout.addWidget(btn_prev)
        nav_layout.addWidget(btn_next)
        ch_layout.addLayout(nav_layout)
        main_layout.addWidget(ch_grp)

        # TIMING AND CONTROLS
        bottom_layout = QHBoxLayout()
        
        # Timing Panel
        timing_grp = QGroupBox("TIMING PARAMETERS (ms)")
        t_form = QFormLayout(timing_grp)
        t_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        t_form.setVerticalSpacing(15)

        self.btn_pulse = self.create_timing_btn("2.0", "#ffeb3b")
        self.btn_gap = self.create_timing_btn("2.0", "#ff9800")
        self.btn_delay = self.create_timing_btn("2.0", "#00e5ff")

        lbl_p = QLabel("PULSE (ON TIME):"); lbl_p.setStyleSheet("font-size: 14px; font-weight: bold;")
        lbl_g = QLabel("PULSE GAP (MANUAL):"); lbl_g.setStyleSheet("font-size: 14px; font-weight: bold;")
        lbl_d = QLabel("DELAY TIME (AUTO):"); lbl_d.setStyleSheet("font-size: 14px; font-weight: bold;")

        t_form.addRow(lbl_p, self.btn_pulse)
        t_form.addRow(lbl_g, self.btn_gap)
        t_form.addRow(lbl_d, self.btn_delay)
        
        bottom_layout.addWidget(timing_grp, 1)

        # Control Panel
        self.btn_fire = QPushButton("🔥 MANUAL FIRE")
        self.btn_fire.setSizePolicy(self.btn_fire.sizePolicy().Expanding, self.btn_fire.sizePolicy().Expanding)
        self.btn_fire.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; font-size: 24px; border-radius: 10px;")
        self.btn_fire.clicked.connect(self.toggle_manual_fire)
        
        self.btn_auto = QPushButton("⚡ AUTO SEQ (1-63)")
        self.btn_auto.setSizePolicy(self.btn_auto.sizePolicy().Expanding, self.btn_auto.sizePolicy().Expanding)
        self.btn_auto.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold; font-size: 24px; border-radius: 10px;")
        self.btn_auto.clicked.connect(self.toggle_auto_seq)

        bottom_layout.addWidget(self.btn_fire, 2)
        bottom_layout.addWidget(self.btn_auto, 2)

        main_layout.addLayout(bottom_layout)

    def create_timing_btn(self, default_val, color):
        btn = QPushButton(default_val)
        btn.setFixedSize(100, 45)
        btn.setStyleSheet(f"background: #222; color: {color}; border: 2px solid #555; border-radius: 5px; font-weight: bold; font-size: 18px;")
        btn.clicked.connect(lambda: Keypad(btn.text(), lambda v: btn.setText(str(float(v))) if v.replace('.','',1).isdigit() else None, self).exec_())
        return btn

    def on_slider_change(self, val):
        self.lbl_channel.setText(f"EJECTOR: {val}")

    def refresh_ports(self):
        self.combo_ports.clear()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if ports:
            self.combo_ports.addItems(ports)
        else:
            self.combo_ports.addItem("NO PORTS")

    def toggle_connection(self):
        if self.serial_port and self.serial_port.is_open:
            self.stop_all()
            self.serial_port.close()
            self.serial_port = None
            self.lbl_status.setText("DISCONNECTED")
            self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #ff1744;")
            self.btn_connect.setText("🔌 CONNECT")
            self.btn_connect.setStyleSheet("background-color: #00897b; color: white; font-weight: bold; border-radius: 4px;")
        else:
            port = self.combo_ports.currentText()
            if port == "NO PORTS": return
            try:
                self.serial_port = serial.Serial(port, 115200, timeout=0)
                self.lbl_status.setText(f"CONNECTED ({port})")
                self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #00e676;")
                self.btn_connect.setText("DISCONNECT")
                self.btn_connect.setStyleSheet("background-color: #c62828; color: white; font-weight: bold; border-radius: 4px;")
            except Exception as e:
                QMessageBox.critical(self, "Connection Error", f"Could not connect to {port}:\n{e}")

    def pulse_esp32(self, valve_id):
        if not (self.serial_port and self.serial_port.is_open):
            return
        pulse_ms = float(self.btn_pulse.text())
        cmd = f"EJ:{valve_id}:{pulse_ms}\n"
        try:
            self.serial_port.write(cmd.encode('utf-8'))
        except Exception as e:
            print(f"Serial Error: {e}")
            self.stop_all()
            self.toggle_connection()

    def stop_all(self):
        self.test_timer.stop()
        self.firing = False
        self.auto_running = False
        
        self.btn_fire.setText("🔥 MANUAL FIRE")
        self.btn_fire.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; font-size: 24px; border-radius: 10px;")
        self.btn_fire.setEnabled(True)
        
        self.btn_auto.setText("⚡ AUTO SEQ (1-63)")
        self.btn_auto.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold; font-size: 24px; border-radius: 10px;")
        self.btn_auto.setEnabled(True)

        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.write(b"CLEAR\n")
            except: pass

    def toggle_manual_fire(self):
        if not (self.serial_port and self.serial_port.is_open):
            QMessageBox.warning(self, "Not Connected", "Please connect to ESP32 first!")
            return
            
        if self.firing:
            self.stop_all()
        else:
            self.firing = True
            self.btn_fire.setText("STOP FIRING")
            self.btn_fire.setStyleSheet("background-color: #b71c1c; color: #ffeb3b; font-weight: bold; font-size: 24px; border: 4px solid #ffeb3b; border-radius: 10px;")
            self.btn_auto.setEnabled(False)
            
            delay = max(0, int(float(self.btn_gap.text())))
            self.pulse_esp32(self.slider.value())
            self.test_timer.start(delay)

    def toggle_auto_seq(self):
        if not (self.serial_port and self.serial_port.is_open):
            QMessageBox.warning(self, "Not Connected", "Please connect to ESP32 first!")
            return
            
        if self.auto_running:
            self.stop_all()
        else:
            self.auto_running = True
            self.btn_auto.setText("STOP SEQUENCE")
            self.btn_auto.setStyleSheet("background-color: #0d47a1; color: #ffeb3b; font-weight: bold; font-size: 24px; border: 4px solid #ffeb3b; border-radius: 10px;")
            self.btn_fire.setEnabled(False)
            
            delay = max(0, int(float(self.btn_delay.text())))
            self.slider.setValue(1)
            self.pulse_esp32(1)
            self.slider.setValue(2)
            self.test_timer.start(delay)

    def _on_timer_tick(self):
        if not (self.serial_port and self.serial_port.is_open):
            self.stop_all()
            return

        if self.firing:
            delay = max(0, int(float(self.btn_gap.text())))
            if self.test_timer.interval() != delay:
                self.test_timer.setInterval(delay)
            self.pulse_esp32(self.slider.value())
            
        elif self.auto_running:
            delay = max(0, int(float(self.btn_delay.text())))
            if self.test_timer.interval() != delay:
                self.test_timer.setInterval(delay)
            
            vid = self.slider.value()
            self.pulse_esp32(vid)
            
            next_vid = vid + 1
            if next_vid > 63:
                self.slider.setValue(1)
                self.stop_all()
            else:
                self.slider.setValue(next_vid)

    def closeEvent(self, event):
        self.stop_all()
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = EjectorTester()
    window.show()
    sys.exit(app.exec_())
