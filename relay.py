import sys
import serial.tools.list_ports
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QComboBox, 
                             QGroupBox, QGridLayout)
from PyQt5.QtCore import Qt
from pymodbus.client import ModbusSerialClient

class RelayController(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESP32 Relay Controller (Modbus MASTER Mode)")
        self.setFixedSize(520, 420)
        
        self.client = None
        self.is_connected = False
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 1. Connection Group
        conn_group = QGroupBox("Modbus Master Client (9600 Baud, 8N1, Target Slave ID: 1)")
        conn_layout = QHBoxLayout()
        
        self.port_combo = QComboBox()
        self.refresh_ports()
        
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_ports)
        
        self.connect_btn = QPushButton("▶ Connect to ESP32")
        self.connect_btn.setStyleSheet("font-weight: bold; background-color: #2E7D32; color: white; padding: 6px 14px;")
        self.connect_btn.clicked.connect(self.toggle_connection)
        
        conn_layout.addWidget(QLabel("COM Port:"))
        conn_layout.addWidget(self.port_combo)
        conn_layout.addWidget(refresh_btn)
        conn_layout.addWidget(self.connect_btn)
        conn_group.setLayout(conn_layout)
        main_layout.addWidget(conn_group)

        # Architecture Info Label
        info_label = QLabel("PC = MASTER (Sends Commands) | ESP32 Board = SLAVE (Executes Commands)")
        info_label.setAlignment(Qt.AlignCenter)
        info_label.setStyleSheet("color: #2196F3; font-size: 11px; font-weight: bold;")
        main_layout.addWidget(info_label)

        # 2. Relay Buttons Group
        self.relay_group = QGroupBox("Relay Controls (Click to send Modbus Write command to ESP32)")
        self.relay_group.setEnabled(False)
        relay_layout = QGridLayout()

        self.relay_names = [
            "Relay 1\n(Vibrator)",
            "Relay 2\n(Ejector)",
            "Relay 3\n(Wiper)",
            "Relay 4\n(White LED 1)",
            "Relay 5\n(White LED 2)",
            "Relay 6\n(White LED 3)",
            "Relay 7\n(White LED 4)",
            "Relay 8\n(Blue LED)"
        ]

        self.relay_buttons = []
        for i in range(8):
            title = self.relay_names[i]
            btn = QPushButton(f"{title}\nReg 4000{i+1}\nOFF")
            btn.setCheckable(True)
            btn.setFixedSize(115, 68)
            btn.clicked.connect(lambda checked, idx=i: self.toggle_relay(idx, checked))
            self.update_button_style(btn, False)
            
            row = i // 4
            col = i % 4
            relay_layout.addWidget(btn, row, col)
            self.relay_buttons.append(btn)

        self.relay_group.setLayout(relay_layout)
        main_layout.addWidget(self.relay_group)

        # 3. Status Label
        self.status_label = QLabel("Disconnected — Select COM port and Connect")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")
        main_layout.addWidget(self.status_label)

    def refresh_ports(self):
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        selected_idx = 0
        for idx, port in enumerate(ports):
            desc = port.description
            tag = ""
            if "CH9102" in desc or "CP210" in desc or "CH340" in desc or "1A86" in port.hwid:
                tag = " [ESP32]"
            elif "0403:6001" in port.hwid or "FTDI" in desc.upper() or "USB SERIAL PORT" in desc.upper():
                tag = " [RS485 ADAPTER]"
            
            display_text = f"{port.device}: {desc}{tag}"
            self.port_combo.addItem(display_text, port.device)

            if tag == " [ESP32]":
                selected_idx = idx

        if ports:
            self.port_combo.setCurrentIndex(selected_idx)
        else:
            self.port_combo.addItem("No COM ports found", "")

    def toggle_connection(self):
        if self.is_connected:
            self.disconnect()
        else:
            self.connect_to_esp32()

    def connect_to_esp32(self):
        port = self.port_combo.currentData()
        if not port:
            port_text = self.port_combo.currentText()
            port = port_text.split(":")[0].strip() if ":" in port_text else port_text
        
        if not port or "No COM" in port:
            self.status_label.setText("Error: No port selected")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            return

        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("Connecting...")
        QApplication.processEvents()

        # DTR/RTS Reset Prevention Patch for PySerial
        import serial
        original_serial_init = serial.Serial.__init__
        def custom_serial_init(self, *args, **kwargs):
            p = kwargs.pop("port", None)
            if not p and len(args) > 0:
                p = args[0]
                args = (None,) + args[1:]
            original_serial_init(self, *args, **kwargs)
            self.setDTR(False)
            self.setRTS(False)
            if p:
                self.port = p
                self.open()
        serial.Serial.__init__ = custom_serial_init

        try:
            self.client = ModbusSerialClient(
                port=port,
                baudrate=9600,
                stopbits=1,
                bytesize=8,
                parity='N',
                framer='rtu',
                timeout=1.0
            )
            
            if self.client.connect():
                self.is_connected = True
                self.connect_btn.setText("⏹ Disconnect")
                self.connect_btn.setStyleSheet("font-weight: bold; background-color: #C62828; color: white; padding: 6px 14px;")
                self.status_label.setText(f"✓ Connected to ESP32 on {port}")
                self.status_label.setStyleSheet("color: green; font-weight: bold;")
                self.relay_group.setEnabled(True)
            else:
                raise Exception("Failed to open port")
                
        except Exception as e:
            self.is_connected = False
            self.status_label.setText(f"Connection Error: {e}")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            self.connect_btn.setText("▶ Connect to ESP32")
            self.connect_btn.setStyleSheet("font-weight: bold; background-color: #2E7D32; color: white; padding: 6px 14px;")
            
        self.connect_btn.setEnabled(True)

    def disconnect(self):
        self.is_connected = False
        if self.client:
            self.client.close()
            self.client = None
            
        self.connect_btn.setText("▶ Connect to ESP32")
        self.connect_btn.setStyleSheet("font-weight: bold; background-color: #2E7D32; color: white; padding: 6px 14px;")
        self.status_label.setText("Disconnected")
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")
        self.relay_group.setEnabled(False)
        
        # Reset buttons to OFF
        for btn in self.relay_buttons:
            btn.blockSignals(True)
            btn.setChecked(False)
            self.update_button_style(btn, False)
            btn.blockSignals(False)

    def toggle_relay(self, index, is_on):
        if not self.is_connected or not self.client:
            return
            
        btn = self.relay_buttons[index]
        value = 1 if is_on else 0
        
        try:
            # Send Modbus Write Single Register (Function 06)
            # In pymodbus 3.15+, 'slave' was renamed to 'slave' or 'device_id'. We pass slave for older versions and device_id for newer.
            try:
                result = self.client.write_register(address=index, value=value, slave=1)
            except TypeError:
                result = self.client.write_register(address=index, value=value, device_id=1)
            
            if result.isError():
                raise Exception(f"Modbus Response Error: {result}")
                
            print(f"[SUCCESS] Sent Command: Relay {index+1} (Reg 4000{index+1}) = {value}")
            self.update_button_style(btn, is_on)
            val_text = "ON" if is_on else "OFF"
            self.status_label.setText(f"✓ Relay {index+1} turned {val_text}")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
        except Exception as e:
            print(f"[ERROR] {e}")
            self.status_label.setText(f"Communication Error: {e}")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            btn.blockSignals(True)
            btn.setChecked(not is_on)
            btn.blockSignals(False)

    def update_button_style(self, btn, is_on):
        text = btn.text()
        if is_on:
            btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; border-radius: 6px;")
            if "\nOFF" in text:
                btn.setText(text.replace("\nOFF", "\nON"))
        else:
            btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; border-radius: 6px;")
            if "\nON" in text:
                btn.setText(text.replace("\nON", "\nOFF"))

    def closeEvent(self, event):
        if self.is_connected:
            self.disconnect()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = RelayController()
    window.show()
    sys.exit(app.exec_())
