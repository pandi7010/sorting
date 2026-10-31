class ESP32Controller:
    def __init__(self, main_dashboard=None):
        self.main_dashboard = main_dashboard
        self._connected = False
        self.connection_type = None  # "MODBUS_SERIAL", "SERIAL", or "LAN"
        self.active_port = None
        self.serial_conn = None
        self.socket_conn = None
        self.server = None
        self.hr_registers = None
        self.modbus_thread = None
        self.server_ready_event = threading.Event()
        self.lock = threading.Lock()
        self.read_thread = None
        self.running = False
        self.wiper_active = False
        self.last_poll_time = 0
        self.poll_count = 0
        self._state_changed = False
        self.ejector_serial = None
        self.ejector_port = None
        
        # --- NUVOTON UART VIBRATOR ---
        self.vibrator_serial = None
        self.vibrator_port = None

        self.current_valves = [0] * 64
        self.ejector_lock = threading.Lock()

    def is_ejector_connected(self):
        return bool(self.is_relay_board_connected() or (self.ejector_serial and hasattr(self.ejector_serial, 'is_open') and self.ejector_serial.is_open))

    def is_modbus_connected(self):
        """Legacy compatibility - now checks simple serial relay board connection"""
        return self.is_relay_board_connected()

    def is_relay_board_connected(self):
        """Check if 8-channel relay board is connected via Modbus RTU or simple serial"""
        return bool(
            (hasattr(self, 'modbus_client') and self.modbus_client and getattr(self.modbus_client, 'connected', False)) or
            (self.connection_type == "SERIAL_RELAY" and self.serial_conn and self.serial_conn.is_open)
        )

    @property
    def connected(self):
        return bool(self._connected or self.is_ejector_connected() or self.is_relay_board_connected())

    @connected.setter
    def connected(self, value):
        self._connected = bool(value)

    def connect(self, port_or_ip, baudrate=9600):
        self.disconnect()
        # If this port is occupied by ejector or vibrator, release it first so Modbus can access it
        if getattr(self, 'ejector_port', None) == port_or_ip:
            self.disconnect_ejector()
        if getattr(self, 'vibrator_port', None) == port_or_ip:
            if self.vibrator_serial:
                try:
                    self.vibrator_serial.close()
                except Exception:
                    pass
                self.vibrator_serial = None
                self.vibrator_port = None
        try:
            # Check if port_or_ip is an IP address
            is_ip = False
            parts = port_or_ip.split('.')
            if len(parts) == 4 and all(p.isdigit() for p in parts):
                is_ip = True

            if is_ip or ":" in port_or_ip:
                # LAN Mode (TCP Socket)
                ip = port_or_ip
                port = 5000
                if ":" in port_or_ip:
                    ip, port_str = port_or_ip.split(":")
                    port = int(port_str)
                self.socket_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket_conn.settimeout(3.0)
                self.socket_conn.connect((ip, port))
                self.socket_conn.settimeout(None)  # set back to blocking
                self.connection_type = "LAN"
                self.active_port = port_or_ip
                self.connected = True
                self.running = True
                self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
                self.read_thread.start()
                print(f"ESP32 Connected via {self.connection_type} to {port_or_ip}")
                return True
            else:
                # Modbus Master Mode - ESP32 8-CH Relay Board
                from pymodbus.client import ModbusSerialClient
                import serial
                
                # DTR/RTS Reset Prevention Patch for PySerial
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

                print(f"[RELAY] Connecting to ESP32 Modbus Relay Board on {port_or_ip}...")
                self.modbus_client = ModbusSerialClient(
                    port=port_or_ip,
                    baudrate=9600,
                    stopbits=1,
                    bytesize=8,
                    parity='N',
                    framer='rtu',
                    timeout=1.0
                )
                
                if self.modbus_client.connect():
                    print(f"[OK] ESP32 Modbus Relay Board connected on {port_or_ip}!")
                    # Force reset DTR/RTS just in case
                    if hasattr(self.modbus_client, 'socket') and hasattr(self.modbus_client.socket, 'setDTR'):
                        self.modbus_client.socket.setDTR(False)
                        self.modbus_client.socket.setRTS(False)
                else:
                    print(f"[ERROR] Failed to open Modbus port {port_or_ip}")
                    return False
                
                self.connection_type = "SERIAL_RELAY"
                self.active_port = port_or_ip
                self.connected = True
                self.running = True
                self._relay_states = [0] * 8  # Track relay states locally
                
                print(f"[OK] ESP32 Modbus Relay Board READY on {port_or_ip} (9600 8N1)")
                return True

        except Exception as e:
            print(f"ESP32 Connection failed: {e}")
            self.connected = False
            self.active_port = None
            return False

    def disconnect(self):
        self.running = False
        if hasattr(self, 'modbus_client') and self.modbus_client:
            self.modbus_client.close()
            self.modbus_client = None
        self.connected = False
        self.active_port = None
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except:
                pass
            self.serial_conn = None
        if self.socket_conn:
            try:
                self.socket_conn.close()
            except:
                pass
            self.socket_conn = None
        self.connection_type = None

    def connect_vibrator(self, port):
        """Connects dedicated USB to UART (MAX232) for Nuvoton Vibrator IC"""
        import serial
        try:
            if self.vibrator_serial and self.vibrator_serial.is_open:
                try:
                    self.vibrator_serial.close()
                except Exception:
                    pass
            self.vibrator_serial = serial.Serial(port, 9600, timeout=1)
            self.vibrator_port = port
            print(f"[NUVOTON] Vibrator Controller connected on {port} at 9600 baud.")
            return True
        except Exception as e:
            print(f"[NUVOTON] Failed to connect vibrator on {port}: {e}")
            self.vibrator_serial = None
            return False
            
    def is_vibrator_connected(self):
        return bool(self.is_relay_board_connected() or (self.vibrator_serial and getattr(self.vibrator_serial, 'is_open', False)))

    def connect_ejector(self, port="COM23", baudrate=115200):
        """Connects dedicated USB Serial to ESP32 (63-Channel Shift Register Ejector)"""
        try:
            if self.ejector_serial:
                try:
                    self.ejector_serial.close()
                except Exception:
                    pass
                self.ejector_serial = None

            print(f"[EJECTOR] Attempting ESP32 30-pin USB on {port} (115200 baud)...")
            self.ejector_serial = serial.Serial(
                port=port,
                baudrate=baudrate,
                timeout=0.1,
                write_timeout=0.1,
                rtscts=False,
                dsrdtr=False
            )
            # Prevent hardware auto-reset lock on ESP32
            self.ejector_serial.dtr = False
            self.ejector_serial.rts = False
            
            # ESP32 takes ~1.5 to 2.0 seconds to fully boot after serial port opening
            time.sleep(2.0)
            self.ejector_serial.reset_input_buffer()
            self.ejector_serial.reset_output_buffer()
            # Send initial reset command to clear all 63 shift register channels
            try:
                self.ejector_serial.write(b"SA:0:0:0:0:0:0:0:0\n")
            except Exception:
                pass
            self.ejector_port = port
            print(f"[OK] ESP32 30-pin (63-CH Ejector Shift Register) successfully connected on {port}!")
            return True
        except Exception as e:
            print(f"[EJECTOR] ESP32 note on {port}: {e}")
            self.ejector_serial = None
            self.ejector_port = None
            return False

    def disconnect_ejector(self):
        if self.ejector_serial:
            try:
                self.ejector_serial.close()
            except Exception:
                pass
            self.ejector_serial = None
            self.ejector_port = None

    def send_command(self, cmd):
        sent = False
        payload = (cmd + "\n").encode('utf-8')
        # 1. Send to 30-pin ESP32 (Ejector Serial)
        if self.ejector_serial and hasattr(self.ejector_serial, 'is_open') and self.ejector_serial.is_open:
            try:
                self.ejector_serial.write(payload)
                sent = True
            except Exception as e:
                print(f"[ESP32] Ejector serial write error: {e}")
                self.disconnect_ejector()
        # 2. Send to serial_conn or socket_conn if present (LAN mode)
        with self.lock:
            if self.connection_type == "LAN" and self.socket_conn:
                try:
                    self.socket_conn.sendall(payload)
                    sent = True
                except Exception:
                    pass
        return sent

    def _read_loop(self):
        buffer = ""
        while self.running and self.connected:
            try:
                if self.connection_type == "SERIAL" and self.serial_conn:
                    data = self.serial_conn.readline().decode('utf-8', errors='ignore')
                    if data:
                        self._handle_response(data.strip())
                elif self.connection_type == "LAN" and self.socket_conn:
                    data = self.socket_conn.recv(1024).decode('utf-8', errors='ignore')
                    if data:
                        buffer += data
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            self._handle_response(line.strip())
                    else:
                        self.disconnect()
                        break
            except Exception as e:
                print(f"ESP32 Read Error: {e}")
                self.disconnect()
                break
            time.sleep(0.01)

    def _handle_response(self, response):
        if not response:
            return
        if not (response.startswith("P1:") or response.startswith("P2:") or response.startswith("ALARM:PRESSURE_OK")):
            print(f"ESP32 Message: {response}")
        # Ignore pressure warnings if physical alarm reporting is disabled (e.g. switches are not connected)
        if response.startswith("ALARM:PRESSURE_LOW") or response.startswith("ALARM:PRESSURE_OK"):
            if not self.main_dashboard or not self.main_dashboard.thresholds.get("ENABLE_PRESSURE_ALARM", False):
                return

        if response.startswith("ALARM:PRESSURE_LOW"):
            if self.main_dashboard and not self.main_dashboard.alarm_active:
                self.main_dashboard.alarm_active = True
                self.main_dashboard.lbl_alarm.setVisible(True)
        elif response.startswith("ALARM:PRESSURE_OK"):
            if self.main_dashboard and self.main_dashboard.alarm_active:
                self.main_dashboard.alarm_active = False
                self.main_dashboard.lbl_alarm.setVisible(False)

    def set_relay(self, relay_id, state):
        """Set relay via Modbus RTU (Address = relay_id - 1, Value = 1/0) or serial command."""
        val = 1 if state else 0
        if 1 <= relay_id <= 8:
            idx = relay_id - 1
            # 1. Send via Modbus client if connected
            if hasattr(self, 'modbus_client') and self.modbus_client and getattr(self.modbus_client, 'connected', False):
                try:
                    try:
                        self.modbus_client.write_register(address=idx, value=val, slave=1)
                    except TypeError:
                        self.modbus_client.write_register(address=idx, value=val, device_id=1)
                except Exception as e:
                    print(f"[RELAY ERROR] Modbus write failed for Relay {relay_id}: {e}")

            # 2. Also send simple serial/LAN command as fallback
            cmd = f"R:{relay_id}:{val}"
            self.send_command(cmd)

            # Track state locally
            if hasattr(self, '_relay_states'):
                self._relay_states[idx] = val
            print(f"[RELAY] Set Relay {relay_id} = {val}")

    def get_relay(self, relay_id):
        """Get current relay state (tracked locally)"""
        if hasattr(self, '_relay_states') and 1 <= relay_id <= 8:
            return self._relay_states[relay_id - 1]
        return 0

    def set_feeder(self, state):
        """Helper alias for vibrator / feeder state"""
        self.set_vibrator(1, 127 if state else 0)

    def set_ejector(self, valve_id, state):
        if state:
            self.fire_single_ejector(valve_id, 20)

    def fire_single_ejector(self, channel_num, pulse_ms=20):
        """Fires a single ejector channel (1 to 63) directly on ESP32 via USB Serial"""
        cmd = f"EJ:{channel_num}:{pulse_ms}\n"
        if self.ejector_serial and hasattr(self.ejector_serial, 'is_open') and self.ejector_serial.is_open:
            try:
                self.ejector_serial.write(cmd.encode('utf-8'))
                print(f"[EJECTOR] Fired Channel {channel_num} (Pulse: {pulse_ms}ms) on {self.ejector_port}")
            except Exception as e:
                print(f"[EJECTOR] Serial write error on ESP32: {e}")
                self.disconnect_ejector()

    def stop_all_ejectors(self):
        """Immediately turns all ejectors OFF by sending CLEAR to ESP32."""
        with self.ejector_lock:
            self.current_valves = [0] * 64
            self._last_valves_had_active = False

        if self.ejector_serial and hasattr(self.ejector_serial, 'is_open') and self.ejector_serial.is_open:
            try:
                self.ejector_serial.reset_output_buffer()
                self.ejector_serial.write(b"SA:0:0:0:0:0:0:0:0\n")
            except Exception:
                self.disconnect_ejector()

    def fire_ejector_channels(self, channel_list, pulse_ms=25):
        """Fires a list of 1-based channel numbers instantly. ESP32 handles the microsecond pulse auto-shutoff."""
        if not channel_list:
            return
            
        byte_data = [0] * 8
        any_active = False
        for ch in channel_list:
            if 1 <= ch <= 64:
                idx = ch - 1
                byte_data[idx // 8] |= (1 << (idx % 8))
                any_active = True

        if not any_active:
            return
            
        # Append pulse_ms directly to the SA command! (e.g. SA:1:0:0:0:0:0:0:0:1.5)
        cmd = "SA:" + ":".join(str(b) for b in byte_data) + f":{pulse_ms}"
        
        if self.ejector_serial and hasattr(self.ejector_serial, 'is_open') and self.ejector_serial.is_open:
            try:
                with self.ejector_lock:
                    self.ejector_serial.write((cmd + "\n").encode('utf-8'))
                    self.ejector_serial.flush()
            except Exception as e:
                print(f"[EJECTOR] Serial write error on ESP32: {e}")
                self.disconnect_ejector()
                
        # Also ensure Relay 2 (Main Air) is ON if we fired something
        if self.get_relay(2) != 1:
            self.set_relay(2, 1)

    def set_all_ejectors(self, valve_array):
        any_active = any(valve_array) if valve_array else False

        # Avoid spamming serial/registers when machine is idle
        if not hasattr(self, '_last_valves_had_active'):
            self._last_valves_had_active = False

        if not any_active and not self._last_valves_had_active:
            return

        self._last_valves_had_active = any_active

        # If any valve fires, ensure Relay 2 (Main Air) is ON
        if any_active:
            if self.get_relay(2) != 1:
                self.set_relay(2, 1)

        # Update Modbus registers 9..12 for valve status if Modbus server is active
        if self.connection_type == "MODBUS_SERIAL" and self.hr_registers is not None:
            try:
                for reg_i in range(4):
                    reg_val = 0
                    for bit_i in range(16):
                        idx = reg_i * 16 + bit_i
                        if idx < len(valve_array) and valve_array[idx]:
                            reg_val |= (1 << bit_i)
                    if len(self.hr_registers) > (8 + reg_i):
                        self.hr_registers[8 + reg_i] = reg_val
            except Exception:
                pass

        # Send shift register command (SA:b0:b1:b2:b3:b4:b5:b6:b7)
        byte_data = [0] * 8
        for i, val in enumerate(valve_array):
            if val:
                byte_idx = i // 8
                bit_idx = i % 8
                if byte_idx < 8:
                    byte_data[byte_idx] |= (1 << bit_idx)
        cmd = "SA:" + ":".join(str(b) for b in byte_data)

        # 1. Direct hardware send to 30-pin ESP32 (63-Channel Shift Register Ejector)
        if self.ejector_serial and hasattr(self.ejector_serial, 'is_open') and self.ejector_serial.is_open:
            try:
                self.ejector_serial.write((cmd + "\n").encode('utf-8'))
            except Exception as e:
                print(f"[EJECTOR] Serial write error on ESP32: {e}")
                self.disconnect_ejector()

        # 2. Also send over primary connection ONLY IF we don't have a dedicated ejector serial
        if not self.ejector_serial or not hasattr(self.ejector_serial, 'is_open') or not self.ejector_serial.is_open:
            if self.connection_type in ("SERIAL", "LAN"):
                self.send_command(cmd)

    def set_vibrator(self, vibrator_id, pwm_value):
        state = 1 if pwm_value > 0 else 0
        # Relay 1 is Vibrator
        self.set_relay(1, state)
        
        # Send Dedicated Nuvoton Command if connected
        if self.is_vibrator_connected():
            try:
                prefix = "086"
                if vibrator_id == 2: prefix = "093"
                elif vibrator_id == 3: prefix = "100"
                
                # Send exact 6-character ASCII string (e.g., "086100") matching Docklight
                val_str = f"{int(pwm_value):03d}"
                cmd = f"{prefix}{val_str}"
                
                self.vibrator_serial.write(cmd.encode('utf-8'))
                print(f"[NUVOTON] Sent: {cmd}")
            except Exception as e:
                print(f"[NUVOTON] Write error: {e}. Attempting reconnect...")
                # Try to auto-reconnect if it was unplugged
                if hasattr(self, 'vibrator_port') and self.vibrator_port:
                    if self.connect_vibrator(self.vibrator_port):
                        try:
                            self.vibrator_serial.write(cmd.encode('utf-8'))
                            print(f"[NUVOTON] Sent (after reconnect): {cmd}")
                        except Exception as e2:
                            print(f"[NUVOTON] Reconnect write failed: {e2}")
                
        # Fallback to old behavior for backward compatibility
        self.send_command(f"V:{vibrator_id}:{pwm_value}")

    def set_led(self, led_id, pwm_value):
        state = 1 if pwm_value > 0 else 0
        mapping = {
            1: 4,   # WHITE FRONT TOP -> Relay 4 (Reg 40004)
            2: 6,   # WHITE FRONT BOTTOM -> Relay 6 (Reg 40006)
            3: 5,   # WHITE BACK TOP -> Relay 5 (Reg 40005)
            4: 7,   # WHITE BACK BOTTOM -> Relay 7 (Reg 40007)
            7: 8,   # BLUE FRONT -> Relay 8 (Background LED)
            10: 8,  # BLUE BACK -> Relay 8 (Background LED)
        }
        if led_id in mapping:
            self.set_relay(mapping[led_id], state)
        self.send_command(f"L:{led_id}:{pwm_value}")

    def set_wiper(self, state):
        self.wiper_active = bool(state)
        val = 1 if state else 0
        # Relay 3 is Wiper
        self.set_relay(3, val)
        self.send_command(f"W:{state}")

    def set_pressure_polarity(self, pnp):
        self.send_command("PP:PNP" if pnp else "PP:NPN")
class EjectorTestWindow(QDialog):
    def __init__(self, esp32_handler, parent=None, scheme_name=None):
        super().__init__(parent)
        self.arduino = esp32_handler
        if scheme_name:
            self.scheme_name = scheme_name
        else:
            self.scheme_name = getattr(parent, 'selected_program', getattr(parent, 'current_program', None))
        if not self.scheme_name:
            self.scheme_name = RecipeManager.get_active_scheme_name()

        self.setWindowTitle(f"Ejector Diagnostics (ESP32) - [{self.scheme_name}]")
        self.resize(900, 650)
        self.setStyleSheet(STYLESHEET)

        self.firing = False
        self.auto_running = False
        self.current_chute = 1

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(30, 30, 30, 30)

        header_layout = QHBoxLayout()
        lbl_title = QLabel(f"EJECTOR BOARD (63-CH) - [{self.scheme_name}]")
        lbl_title.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
        header_layout.addWidget(lbl_title)
        header_layout.addStretch()

        # Ejector Port Selector & Connect/Disconnect
        header_layout.addWidget(QLabel("Ejector Port:"))
        self.combo_ej_port = QComboBox()
        self.combo_ej_port.setMinimumWidth(110)
        self.refresh_ej_ports()
        header_layout.addWidget(self.combo_ej_port)

        self.btn_ej_connect = QPushButton("🔌 CONNECT")
        self.btn_ej_connect.setFixedHeight(34)
        self.btn_ej_connect.setStyleSheet("background-color: #00897b; color: white; font-weight: bold; font-size: 12px; padding: 4px 10px; border-radius: 4px;")
        self.btn_ej_connect.clicked.connect(self.toggle_ej_connection)
        header_layout.addWidget(self.btn_ej_connect)

        self.lbl_status = QLabel("DISCONNECTED")
        self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #ff1744;")
        header_layout.addWidget(self.lbl_status)
        main_layout.addLayout(header_layout)

        # Auto-connect COM24 if not yet connected
        if self.arduino:
            is_conn = bool(getattr(self.arduino, 'ejector_serial', None) and self.arduino.ejector_serial.is_open)
            if not is_conn:
                port = self.combo_ej_port.currentText() or "COM24"
                self.arduino.connect_ejector(port, baudrate=115200)
        self.update_status_indicator()

        chute_grp = QGroupBox("CHUTE SELECTION")
        chute_layout = QHBoxLayout(chute_grp)
        btn_prev = QPushButton("<< PREV CHUTE")
        btn_prev.setFixedSize(150, 50)
        btn_prev.clicked.connect(self.prev_chute)
        self.lbl_chute = QLabel("CHUTE 1")
        self.lbl_chute.setAlignment(Qt.AlignCenter)
        self.lbl_chute.setStyleSheet("font-size: 32px; font-weight: bold; color: #ffd700;")
        btn_next = QPushButton("NEXT CHUTE >>")
        btn_next.setFixedSize(150, 50)
        btn_next.clicked.connect(self.next_chute)
        chute_layout.addWidget(btn_prev)
        chute_layout.addWidget(self.lbl_chute)
        chute_layout.addWidget(btn_next)
        main_layout.addWidget(chute_grp)

        valve_grp = QGroupBox("VALVE SELECTION (1 - 63)")
        valve_layout = QVBoxLayout(valve_grp)
        info_layout = QHBoxLayout()
        self.lbl_valve_num = QLabel("EJECTOR: 1")
        self.lbl_valve_num.setStyleSheet("font-size: 40px; font-weight: bold; color: white;")
        self.lbl_addr = QLabel("CHANNEL: CH-01")
        self.lbl_addr.setStyleSheet("font-size: 20px; color: #aaa;")
        info_layout.addWidget(self.lbl_valve_num)
        info_layout.addStretch()
        info_layout.addWidget(self.lbl_addr)
        valve_layout.addLayout(info_layout)

        self.slider_ctrl = QSlider(Qt.Horizontal)
        self.slider_ctrl.setRange(1, 63)
        self.slider_ctrl.setValue(1)
        self.slider_ctrl.setStyleSheet("""
            QSlider::groove:horizontal { height: 10px; background: #333; border-radius: 5px; }
            QSlider::handle:horizontal { background: #00bcd4; width: 30px; margin: -10px 0; border-radius: 5px; }
        """)
        self.slider_ctrl.valueChanged.connect(self.update_valve_display)
        valve_layout.addWidget(self.slider_ctrl)

        nav_layout = QHBoxLayout()
        btn_v_prev = QPushButton("< PREV VALVE")
        btn_v_prev.clicked.connect(lambda: self.slider_ctrl.setValue(self.slider_ctrl.value() - 1))
        btn_v_next = QPushButton("NEXT VALVE >")
        btn_v_next.clicked.connect(lambda: self.slider_ctrl.setValue(self.slider_ctrl.value() + 1))
        nav_layout.addWidget(btn_v_prev)
        nav_layout.addWidget(btn_v_next)
        valve_layout.addLayout(nav_layout)
        main_layout.addWidget(valve_grp)

        import json, os
        ejector_cfg = {"pulse": 10.0, "delay": 500.0, "gap": 500.0}
        self.ejector_cfg_path = os.path.join(os.path.dirname(__file__), "ejector_config.json")
        if os.path.exists(self.ejector_cfg_path):
            try:
                with open(self.ejector_cfg_path, "r") as f:
                    ejector_cfg.update(json.load(f))
            except: pass
        scheme_data = RecipeManager.get_scheme(self.scheme_name)
        if scheme_data and isinstance(scheme_data, dict) and "ejector" in scheme_data:
            ejector_cfg.update(scheme_data["ejector"])

        ctrl_layout = QHBoxLayout()
        timing_grp = QGroupBox("TIMING (ms)")
        t_layout = QFormLayout(timing_grp)
        
        self.btn_pulse = QPushButton(str(ejector_cfg.get("pulse", 10.0)))
        self.btn_pulse.setFixedHeight(40)
        self.btn_pulse.setStyleSheet("background: #333; color: #ffeb3b; font-weight: bold; font-size: 16px;")
        self.btn_pulse.clicked.connect(lambda: Keypad(self.btn_pulse.text(), lambda v: self.btn_pulse.setText(str(float(v))) if v.replace('.','',1).isdigit() else None, self).exec_())

        self.btn_delay = QPushButton(str(ejector_cfg.get("delay", 500.0)))
        self.btn_delay.setFixedHeight(40)
        self.btn_delay.setStyleSheet("background: #333; color: #00e5ff; font-weight: bold; font-size: 16px;")
        self.btn_delay.clicked.connect(lambda: Keypad(self.btn_delay.text(), lambda v: self.btn_delay.setText(str(float(v))) if v.replace('.','',1).isdigit() else None, self).exec_())

        self.btn_gap = QPushButton(str(ejector_cfg.get("gap", 2.0)))
        self.btn_gap.setFixedHeight(40)
        self.btn_gap.setStyleSheet("background: #333; color: #ff9800; font-weight: bold; font-size: 16px;")
        self.btn_gap.clicked.connect(lambda: Keypad(self.btn_gap.text(), lambda v: self.btn_gap.setText(str(float(v))) if v.replace('.','',1).isdigit() else None, self).exec_())

        t_layout.addRow("PULSE (ms):", self.btn_pulse)
        t_layout.addRow("PULSE GAP (ms):", self.btn_gap)
        t_layout.addRow("DELAY TIME (ms):", self.btn_delay)
        
        btn_save_timing = QPushButton("💾 SAVE SETTINGS")
        btn_save_timing.setStyleSheet("background-color: #00e676; color: black; font-weight: bold;")
        btn_save_timing.clicked.connect(self.save_ejector_cfg)
        t_layout.addRow(btn_save_timing)

        ctrl_layout.addWidget(timing_grp, 1)

        self.btn_fire = QPushButton("🔥 MANUAL FIRE")
        self.btn_fire.setSizePolicy(1, 1)
        self.btn_fire.setStyleSheet("background-color: #2e7d32; font-size: 18px; border: none;")
        self.btn_fire.clicked.connect(self.toggle_manual_fire)
        self.btn_auto = QPushButton("⚡ AUTO SEQ (1-63)")
        self.btn_auto.setSizePolicy(1, 1)
        self.btn_auto.setStyleSheet("background-color: #1565c0; font-size: 18px; border: none;")
        self.btn_auto.clicked.connect(self.toggle_auto_seq)
        ctrl_layout.addWidget(self.btn_fire, 2)
        ctrl_layout.addWidget(self.btn_auto, 2)
        main_layout.addLayout(ctrl_layout)

        btn_close = QPushButton("CLOSE DIAGNOSTICS")
        btn_close.setFixedHeight(50)
        btn_close.setStyleSheet("background-color: #444; border: 1px solid #666;")
        btn_close.clicked.connect(self.close_cleanly)
        main_layout.addWidget(btn_close)

    def refresh_ej_ports(self):
        self.combo_ej_port.clear()
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()
        
        curr_port = getattr(self.arduino, 'ejector_port', 'COM24') if self.arduino else 'COM24'
        if not curr_port:
            curr_port = 'COM24'
            
        sel_idx = 0
        for i, p in enumerate(ports):
            self.combo_ej_port.addItem(p.device)
            if p.device == curr_port:
                sel_idx = i
        if not ports:
            self.combo_ej_port.addItem(curr_port)
        else:
            self.combo_ej_port.setCurrentIndex(sel_idx)

    @property
    def connected(self):
        return bool(self.arduino and getattr(self.arduino, 'ejector_serial', None) and self.arduino.ejector_serial.is_open)

    @connected.setter
    def connected(self, value):
        pass

    def update_status_indicator(self):
        is_conn = self.connected
        port = getattr(self.arduino, 'ejector_port', 'COM23') if is_conn else ''
        if is_conn:
            self.lbl_status.setText(f"CONNECTED ({port})")
            self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #00e676;")
            self.btn_ej_connect.setText("DISCONNECT")
            self.btn_ej_connect.setStyleSheet("background-color: #c62828; color: white; font-weight: bold; font-size: 12px; padding: 4px 10px; border-radius: 4px;")
        else:
            self.lbl_status.setText("DISCONNECTED")
            self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #ff1744;")
            self.btn_ej_connect.setText("🔌 CONNECT")
            self.btn_ej_connect.setStyleSheet("background-color: #00897b; color: white; font-weight: bold; font-size: 12px; padding: 4px 10px; border-radius: 4px;")

    def toggle_ej_connection(self):
        if self.arduino:
            is_conn = bool(getattr(self.arduino, 'ejector_serial', None) and self.arduino.ejector_serial.is_open)
            if is_conn:
                self.arduino.disconnect_ejector()
            else:
                port = self.combo_ej_port.currentText()
                self.arduino.connect_ejector(port, baudrate=115200)
            self.update_status_indicator()

    # --- MISSING FUNCTION ADDED HERE ---
    def save_ejector_cfg(self):
        import json
        try:
            pulse = float(self.btn_pulse.text())
            delay = int(self.btn_delay.text())
            cfg = {"pulse": pulse, "delay": delay}
            RecipeManager.update_scheme_field(self.scheme_name, "ejector", cfg)
            
            from PyQt5.QtWidgets import QApplication
            for w in QApplication.topLevelWidgets():
                if hasattr(w, 'program_data') and isinstance(getattr(w, 'program_data', None), dict):
                    if self.scheme_name in w.program_data:
                        w.program_data[self.scheme_name]["ejector"] = cfg
            with open(self.ejector_cfg_path, "w") as f:
                json.dump(cfg, f, indent=4)
            QMessageBox.information(self, "Saved", f"Ejector Timings Saved for [{self.scheme_name}]!\nPulse: {pulse} ms, Delay: {delay} ms")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save: {e}")

    def get_address(self, val):
        return f"CH-{val:02d}"

    def prev_chute(self):
        if self.current_chute > 1: self.current_chute -= 1; self.update_chute_ui()

    def next_chute(self):
        if self.current_chute < 10: self.current_chute += 1; self.update_chute_ui()

    def update_chute_ui(self):
        self.stop_all()
        self.lbl_chute.setText(f"CHUTE {self.current_chute}")
        self.update_valve_display(self.slider_ctrl.value())

    def update_valve_display(self, val):
        addr = self.get_address(val)
        self.lbl_valve_num.setText(f"EJECTOR: {val}")
        self.lbl_addr.setText(f"ADDRESS: {addr}")

    def stop_all(self):
        self.firing = False
        self.auto_running = False
        if hasattr(self, 'test_timer') and self.test_timer.isActive():
            self.test_timer.stop()
        self.btn_fire.setText("🔥 MANUAL FIRE")
        self.btn_fire.setStyleSheet("background-color: #2e7d32; font-size: 18px; border: none;")
        self.btn_auto.setText("⚡ AUTO SEQ (1-63)")
        self.btn_auto.setStyleSheet("background-color: #1565c0; font-size: 18px; border: none;")
        self.btn_fire.setEnabled(True)
        self.btn_auto.setEnabled(True)

    def _init_timer(self):
        if not hasattr(self, 'test_timer'):
            from PyQt5.QtCore import QTimer
            self.test_timer = QTimer(self)
            self.test_timer.timeout.connect(self._on_timer_tick)

    def toggle_manual_fire(self):
        if self.firing:
            self.stop_all()
        else:
            self._init_timer()
            self.firing = True
            self.btn_fire.setText("STOP FIRING")
            self.btn_fire.setStyleSheet("background-color: #c62828; font-size: 18px; border: none;")
            self.btn_auto.setEnabled(False)
            delay = max(0, int(float(self.btn_gap.text())))
            self.pulse_esp32(self.slider_ctrl.value())
            self.test_timer.start(delay)

    def toggle_auto_seq(self):
        if self.auto_running:
            self.stop_all()
        else:
            self._init_timer()
            self.auto_running = True
            self.btn_auto.setText("STOP SEQUENCE")
            self.btn_auto.setStyleSheet("background-color: #c62828; font-size: 18px; border: none;")
            self.btn_fire.setEnabled(False)
            delay = max(0, int(float(self.btn_delay.text())))
            self.slider_ctrl.setValue(1)
            self.pulse_esp32(1)
            self.slider_ctrl.setValue(2)
            self.test_timer.start(delay)

    def _on_timer_tick(self):
        try:
            if not self.connected:
                self.stop_all()
                return

            if self.firing:
                delay = max(0, int(float(self.btn_gap.text())))
                if self.test_timer.interval() != delay:
                    self.test_timer.setInterval(delay)
                vid = self.slider_ctrl.value()
                self.pulse_esp32(vid)
            elif self.auto_running:
                delay = max(0, int(float(self.btn_delay.text())))
                if self.test_timer.interval() != delay:
                    self.test_timer.setInterval(delay)
                vid = self.slider_ctrl.value()
                self.pulse_esp32(vid)
                next_vid = vid + 1
                if next_vid > 63:
                    self.slider_ctrl.setValue(1)
                    self.stop_all()
                else:
                    self.slider_ctrl.setValue(next_vid)
        except Exception as e:
            print(f"[EJECTOR] Timer error: {e}")
            self.stop_all()

    def pulse_esp32(self, valve_id):
        pulse_ms = float(self.btn_pulse.text())
        try:
            if self.arduino:
                if hasattr(self.arduino, 'fire_single_ejector'):
                    self.arduino.fire_single_ejector(valve_id, pulse_ms)
                elif hasattr(self.arduino, 'fire_ejector_channels'):
                    self.arduino.fire_ejector_channels([valve_id], pulse_ms)
                else:
                    self.arduino.set_ejector(valve_id, True)
        except Exception as e:
            print(f"[EJECTOR] Pulse error: {e}")

    def close_cleanly(self):
        self.stop_all()
        self.reject()

    def closeEvent(self, event):
        self.close_cleanly()
        super().closeEvent(event)
