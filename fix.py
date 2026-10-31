import sys

file_path = r'd:\LAMTON_V1\LAMTON_LINE_NEW_ESP32.py'
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = -1
for i, line in enumerate(lines):
    if 'def manual_wiper_fwd(self):' in line:
        start_idx = i
        break

end_idx = -1
for i, line in enumerate(lines[start_idx:]):
    if 'def mk_sw(self, st, i):' in line:
        end_idx = start_idx + i
        break

correct_block = """    def manual_wiper_fwd(self):
        print("[MANUAL WIPER] Forward (Relay 2 = 1)")
        if hasattr(self.esp32, 'set_relay'):
            self.esp32.set_relay(2, 1)
        if hasattr(self.esp32, 'send_command'):
            self.esp32.send_command("WIPER_FWD")

    def manual_wiper_rev(self):
        print("[MANUAL WIPER] Reverse (Relay 2 = 1)")
        if hasattr(self.esp32, 'set_relay'):
            self.esp32.set_relay(2, 1)
        if hasattr(self.esp32, 'send_command'):
            self.esp32.send_command("WIPER_REV")

    def manual_wiper_off(self):
        print("[MANUAL WIPER] Stop (Relay 2 = 0)")
        if hasattr(self.esp32, 'set_relay'):
            self.esp32.set_relay(2, 0)
        if hasattr(self.esp32, 'send_command'):
            self.esp32.send_command("WIPER_OFF")

    def run_clean_test(self):
        if hasattr(self.main_board, 'execute_auto_wiper_sequence'):
            self.main_board.execute_auto_wiper_sequence(force=True)

# =============================================================================
# 3. VIBRATOR CONFIGURATION
# =============================================================================
class VibratorConfigDialog(QDialog):
    def __init__(self, role, feeder_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vibrator Configuration")
        self.resize(1000, 750)
        self.role = role
        self.feeder_data = feeder_data
        
        try:
            self.setStyleSheet(STYLESHEET)
        except:
            pass

        layout = QVBoxLayout(self)
        header_text = "MANUFACTURER SETUP" if role == "MANUFACTURER" else "OPERATOR CONTROLS"
        header = QLabel(header_text)
        header.setStyleSheet("color: #ffd700; font-size: 20px; font-weight: bold; background: #222; padding: 15px; border-radius: 8px;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)
        
        # Add Nuvoton COM Port Selector
        nuvoton_layout = QHBoxLayout()
        nuvoton_lbl = QLabel("NUVOTON VIBRATOR (9600 Baud):")
        nuvoton_lbl.setStyleSheet("font-weight: bold; color: #00e5ff; font-size: 14px;")
        self.combo_nuvoton_port = QComboBox()
        self.combo_nuvoton_port.setFixedWidth(250)
        
        self.btn_connect_nuvoton = QPushButton("Connect Nuvoton")
        self.btn_connect_nuvoton.clicked.connect(self.connect_nuvoton)
        self.btn_connect_nuvoton.setStyleSheet("background-color: #00e676; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
        
        nuvoton_layout.addWidget(nuvoton_lbl)
        nuvoton_layout.addWidget(self.combo_nuvoton_port)
        nuvoton_layout.addWidget(self.btn_connect_nuvoton)
        nuvoton_layout.addStretch()
        layout.addLayout(nuvoton_layout)
        
        self.refresh_nuvoton_ports()
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: #1a1a1a;")
        self.grid_layout = QGridLayout(scroll_content)
        self.grid_layout.setSpacing(20)

        self.grid_layout.addWidget(QLabel("FEEDER ID"), 0, 0)
        if role == "MANUFACTURER":
            self.grid_layout.addWidget(QLabel("SYSTEM (Enable)"), 0, 1)
            self.grid_layout.addWidget(QLabel("STATUS (On/Off)"), 0, 2)
            self.grid_layout.addWidget(QLabel("INTENSITY"), 0, 3)
        else:
            self.grid_layout.addWidget(QLabel("STATUS (On/Off)"), 0, 1)
            self.grid_layout.addWidget(QLabel("INTENSITY"), 0, 2)
        row_idx = 1
        
        total_chutes = getattr(self.parent(), 'bg_settings', {}).get("total_chutes", 1)
        
        for i in range(total_chutes):
            feeder = self.feeder_data[i]
            if role != "MANUFACTURER" and not feeder['system_enabled']:
                continue
            
            name_text = "PRIMARY"
            if i == 1: name_text = "SECONDARY"
            elif i == 2: name_text = "FINAL"
            
            lbl_name = QLabel(name_text)
            lbl_name.setStyleSheet("font-size: 16px; font-weight: bold; color: #00e5ff;")
            self.grid_layout.addWidget(lbl_name, row_idx, 0)
            if role == "MANUFACTURER":
                chk = QCheckBox("Enable")
                chk.setChecked(feeder['system_enabled'])
                chk.toggled.connect(lambda c, x=i: self.set_sys(x, c))
                self.grid_layout.addWidget(chk, row_idx, 1)
                btn = self.mk_sw(feeder['active'], i)
                self.grid_layout.addWidget(btn, row_idx, 2)
                knob = self.mk_knob(feeder['value'], i)
                self.grid_layout.addWidget(knob, row_idx, 3)
            else:
                btn = self.mk_sw(feeder['active'], i)
                self.grid_layout.addWidget(btn, row_idx, 1)
                knob = self.mk_knob(feeder['value'], i)
                self.grid_layout.addWidget(knob, row_idx, 2)
            row_idx += 1
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        btn_c = QPushButton("SAVE & CLOSE")
        btn_c.setFixedHeight(50)
        btn_c.setStyleSheet("background-color: #00bcd4; color: black; font-weight: bold; font-size: 16px;")
        btn_c.clicked.connect(self.accept)
        layout.addWidget(btn_c)

    def refresh_nuvoton_ports(self):
        import serial.tools.list_ports
        self.combo_nuvoton_port.clear()
        ports = serial.tools.list_ports.comports()
        esp32 = getattr(self.parent(), 'esp32', None)
        current_port = getattr(esp32, 'vibrator_port', None) if esp32 else None
        
        for p in ports:
            display_text = f"{p.device}: {p.description}"
            self.combo_nuvoton_port.addItem(display_text, p.device)
            if current_port and p.device == current_port:
                self.combo_nuvoton_port.setCurrentText(display_text)
                self.btn_connect_nuvoton.setText("✓ Connected")
                self.btn_connect_nuvoton.setStyleSheet("background-color: #37474f; color: #00e676; font-weight: bold; padding: 8px; border-radius: 4px;")
                
    def connect_nuvoton(self):
        port = self.combo_nuvoton_port.currentData()
        esp32 = getattr(self.parent(), 'esp32', None)
        if port and esp32:
            if esp32.connect_vibrator(port):
                self.btn_connect_nuvoton.setText("✓ Connected")
                self.btn_connect_nuvoton.setStyleSheet("background-color: #37474f; color: #00e676; font-weight: bold; padding: 8px; border-radius: 4px;")
                
                # Import QMessageBox at function level if not already available
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.information(self, "Success", f"Connected to Nuvoton on {port} (9600 Baud)")
            else:
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Error", f"Failed to connect to Nuvoton on {port}")
"""

new_lines = lines[:start_idx] + [correct_block + "\n"] + lines[end_idx:]

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Fixed!')
