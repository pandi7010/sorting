class GigECameraWorker(QThread):
    frame_ready = pyqtSignal(np.ndarray)
    connection_status = pyqtSignal(bool)

    def __init__(self, is_rear=False):
        super().__init__()
        self.running = False
        self.ip_address = ""
        self.params = {}
        self.camera_handle = None
        self.rgb_buffer = None  # ADDED: Dedicated buffer for processed RGB data
        self.latest_frame = None
        self.v_stretch = 1.0
        self.is_rear = is_rear

        self.roi_w = 4096
        self.roi_h = 512

    def connect_camera(self, ip_address):
        self.ip_address = ip_address
        try:
            print(f"Connecting to GigE Camera at {self.ip_address}...")
            # Note: The actual handle is passed from CameraSetupDialog
            self.connection_status.emit(True)
            if not self.running:
                self.running = True
                self.start()
        except Exception as e:
            print(f"Connection Failed: {e}")
            self.connection_status.emit(False)

    def disconnect_camera(self):
        self.running = False
        self.wait()
        self.camera_handle = None
        self.connection_status.emit(False)

    def set_param(self, key, value):
        self.params[key] = value
        print(f"Hardware -> {key} set to {value}")
        
        if key == "v_stretch":
            self.v_stretch = max(1.0, float(value))

        # கேமரா கனெக்ட் ஆகியிருந்தால் நேரடியாக மதிப்புகளை செட் செய்ய
        if self.camera_handle is not None and MVSDK_AVAILABLE:
            try:
                if key == "exposure":
                    mvsdk.CameraSetExposureTime(self.camera_handle, float(value))
                elif key == "ana_gain":
                    mvsdk.CameraSetAnalogGain(self.camera_handle, int(value))
                elif key in ["r_gain", "g_gain", "b_gain"]:
                    # RGB Gain - மூன்றையும் ஒன்றாக செட் செய்ய வேண்டும்
                    r = self.params.get("r_gain", 128)
                    g = self.params.get("g_gain", 128)
                    b = self.params.get("b_gain", 128)
                    mvsdk.CameraSetGain(self.camera_handle, int(r), int(g), int(b))
                elif key == "gamma":
                    mvsdk.CameraSetGamma(self.camera_handle, int(value))
                elif key == "contrast":
                    mvsdk.CameraSetContrast(self.camera_handle, int(value))
                elif key == "saturation":
                    mvsdk.CameraSetSaturation(self.camera_handle, int(value))
                elif key == "sharpness":
                    mvsdk.CameraSetSharpness(self.camera_handle, int(value))
            except Exception as e:
                print(f"Camera Hardware Error ({key}): {e}")

    def trigger_white_balance(self):
        print("Hardware -> Triggering One-Push White Balance")
        if self.camera_handle is not None and MVSDK_AVAILABLE:
            try:
                # One-Push White Balance Command
                mvsdk.CameraSetOnceWB(self.camera_handle)
            except Exception as e:
                print(f"White Balance Error: {e}")

    def run(self):
        while self.running:
            if self.camera_handle is not None:
                try:
                    pRawData, FrameHead = mvsdk.CameraGetImageBuffer(self.camera_handle, 2000)

                    # 2. Allocate our RGB buffer ONCE based on MAXIMUM capability
                    if self.rgb_buffer is None:
                        cap = mvsdk.CameraGetCapability(self.camera_handle)
                        buffer_size = cap.sResolutionRange.iWidthMax * cap.sResolutionRange.iHeightMax * 3
                        self.rgb_buffer = mvsdk.CameraAlignMalloc(buffer_size, 16)

                    mvsdk.CameraImageProcess(self.camera_handle, pRawData, self.rgb_buffer, FrameHead)
                    mvsdk.CameraReleaseImageBuffer(self.camera_handle, pRawData)

                    processed_size = FrameHead.iWidth * FrameHead.iHeight * 3
                    frame_data = (mvsdk.c_ubyte * processed_size).from_address(self.rgb_buffer)
                    frame = np.frombuffer(frame_data, dtype=np.uint8)

                    frame = frame.reshape((FrameHead.iHeight, FrameHead.iWidth, 3))

                    # --- 🔥 திருத்தப்பட்ட சாஃப்ட்வேர் க்ராப் (Safe Software ROI) ---
                    h, w = frame.shape[:2]

                    # 1. பூஜ்ஜியம் (0) வருவதைத் தடுத்து, குறைந்தபட்சம் 10 பிக்சல்களாவது இருப்பதை உறுதி செய்தல்
                    safe_roi_h = max(10, min(self.roi_h, h))
                    safe_roi_w = max(10, min(self.roi_w, w))

                    # 2. உயரத்தை (Height) மையமாக வைத்து வெட்டுதல்
                    if h > safe_roi_h:
                        start_y = (h - safe_roi_h) // 2
                        frame = frame[start_y: start_y + safe_roi_h, :, :]

                    # 3. அகலத்தை (Width) மையமாக வைத்து வெட்டுதல்
                    if w > safe_roi_w:
                        start_x = (w - safe_roi_w) // 2
                        frame = frame[:, start_x: start_x + safe_roi_w, :]

                    # ⚠️ 4. மிக முக்கியம்: Memory Alignment (0xC0000409 எரரைத் தடுக்கும் மந்திர வரி)
                    frame = np.ascontiguousarray(frame)

                    if self.is_rear:
                        frame = cv2.flip(frame, 1)

                    self.latest_frame = frame
                    self.frame_ready.emit(frame)

                except mvsdk.CameraException as e:
                    # Timeout எரரை அமைதியாக கடந்துவிட வேண்டும்
                    pass
                except Exception as e:
                    pass
            else:
                self.msleep(100)
class CameraSetupDialog(QDialog):
    def __init__(self, front_worker, rear_worker, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dual Camera Setup & Live Tuning")

        # Full Screen View as requested
        self.setWindowState(Qt.WindowMaximized)

        self.setStyleSheet("background-color: #1a1a1a; color: white; font-family: Segoe UI;")
        self.front_worker = front_worker
        self.rear_worker = rear_worker
        self.workers = {"FRONT": front_worker, "REAR": rear_worker}
        self.worker = self.front_worker

        self.current_cam = "FRONT"
        self.configs = self.load_config()

        main_layout = QHBoxLayout(self)

        # --- LEFT: CONTROLS ---
        left_panel = QWidget()
        left_panel.setFixedWidth(420)
        left_layout = QVBoxLayout(left_panel)

        # 1. Camera Selection (FRONT / REAR) Tabs
        cam_sel_layout = QHBoxLayout()
        self.btn_tab_front = QPushButton("FRONT CAMERA")
        self.btn_tab_rear = QPushButton("REAR CAMERA")
        
        self.btn_tab_front.setFixedHeight(45)
        self.btn_tab_rear.setFixedHeight(45)
        
        self.btn_tab_front.clicked.connect(lambda: self.switch_camera("FRONT"))
        self.btn_tab_rear.clicked.connect(lambda: self.switch_camera("REAR"))
        
        cam_sel_layout.addWidget(self.btn_tab_front)
        cam_sel_layout.addWidget(self.btn_tab_rear)
        left_layout.addLayout(cam_sel_layout)

        # 2. Network & Device Settings
        conn_group = QGroupBox("HARDWARE DEVICE SELECTION")
        conn_layout = QVBoxLayout(conn_group)

        dev_row = QHBoxLayout()
        dev_row.addWidget(QLabel("DEVICE:"))
        self.combo_device = QComboBox()
        self.combo_device.setStyleSheet("background: #333; padding: 5px; color: yellow; font-weight: bold; min-height: 40px;")
        dev_row.addWidget(self.combo_device)

        self.btn_refresh = QPushButton("🔄 SCAN")
        self.btn_refresh.setFixedSize(90, 45)
        self.btn_refresh.setStyleSheet("background: #555; font-weight: bold;")
        self.btn_refresh.clicked.connect(self.scan_cameras)
        dev_row.addWidget(self.btn_refresh)
        conn_layout.addLayout(dev_row)

        # Action Buttons (Combined row)
        action_row = QHBoxLayout()
        self.btn_apply_ip = QPushButton("CONNECT")
        self.btn_apply_ip.setStyleSheet("background: #00bcd4; color: black; font-weight: bold; padding: 12px; font-size: 14px; border-radius: 5px;")
        self.btn_apply_ip.clicked.connect(self.apply_connection)
        
        self.btn_swap = QPushButton("🔄 SWAP")
        self.btn_swap.setStyleSheet("background: #ff9800; color: black; font-weight: bold; padding: 12px; font-size: 14px; border-radius: 5px;")
        self.btn_swap.clicked.connect(self.swap_cameras)
        
        action_row.addWidget(self.btn_apply_ip)
        action_row.addWidget(self.btn_swap)
        conn_layout.addLayout(action_row)
        left_layout.addWidget(conn_group)

        # 3. Hardware Parameters (Grid Layout)
        ctrl_group = QGroupBox("HARDWARE PARAMETERS")
        ctrl_layout = QGridLayout(ctrl_group)
        self.text_inputs = {}

        params = [
            ("exposure", "EXP(µs)", 10, 20000, 0, 0),
            ("ana_gain", "ANA GAIN", 1, 32, 0, 1),
            ("dig_gain", "DIG GAIN", 1, 100, 0, 2),
            ("r_gain", "R GAIN", 1, 255, 1, 0),
            ("g_gain", "G GAIN", 1, 255, 1, 1),
            ("b_gain", "B GAIN", 1, 255, 1, 2),
            ("gamma", "GAMMA", 1, 300, 2, 0),
            ("contrast", "CONTRAST", 0, 200, 2, 1),
            ("saturation", "SATURATION", 0, 200, 2, 2),
            ("sharpness", "SHARPNESS", 0, 100, 3, 0),
            ("v_stretch", "V-STRETCH", 1, 10, 3, 1)
        ]

        for key, label, vmin, vmax, r, c in params:
            box = QVBoxLayout()
            lbl = QLabel(label)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("font-size: 11px;")
            
            btn_val = QPushButton("0")
            btn_val.setFixedSize(110, 45)
            btn_val.setStyleSheet("color: #ffd700; background: #333; font-size: 16px; font-weight: bold; border: 2px solid #555; border-radius: 5px;")
            
            box.addWidget(lbl)
            box.addWidget(btn_val)
            ctrl_layout.addLayout(box, r, c)

            btn_val.clicked.connect(lambda _, k=key, b=btn_val, mn=vmin, mx=vmax: 
                                    self.open_keypad(b.text(), lambda v: self.update_param(k, max(mn, min(mx, int(float(v)))), b)))
            self.text_inputs[key] = btn_val

        left_layout.addWidget(ctrl_group)

        # --- 3.5 PIXEL CROP LIMITS & CHANNELS ---
        pix_group = QGroupBox("PIXEL CROP LIMITS & CHANNELS")
        pix_layout = QHBoxLayout(pix_group)

        # 1. Start Pixel
        pix_layout.addWidget(QLabel("START PIX:"))
        self.btn_start_pix = QPushButton("0")
        self.btn_start_pix.setStyleSheet(
            "background: #333; color: #ff9800; font-weight: bold; font-size: 16px; padding: 8px;")
        self.btn_start_pix.clicked.connect(lambda: self.open_keypad(self.btn_start_pix.text(), self.update_start_pixel))
        pix_layout.addWidget(self.btn_start_pix)

        # 2. End Pixel
        pix_layout.addWidget(QLabel("END PIX:"))
        self.btn_end_pix = QPushButton("4096")
        self.btn_end_pix.setStyleSheet(
            "background: #333; color: #00e5ff; font-weight: bold; font-size: 16px; padding: 8px;")
        self.btn_end_pix.clicked.connect(lambda: self.open_keypad(self.btn_end_pix.text(), self.update_end_pixel))
        pix_layout.addWidget(self.btn_end_pix)

        # 3. Channels (NEW)
        pix_layout.addWidget(QLabel("CHANNELS:"))
        self.btn_channels = QPushButton("63")
        self.btn_channels.setStyleSheet(
            "background: #333; color: #00e676; font-weight: bold; font-size: 16px; padding: 8px;")
        self.btn_channels.clicked.connect(lambda: self.open_keypad(self.btn_channels.text(), self.update_channels))
        pix_layout.addWidget(self.btn_channels)

        left_layout.addWidget(pix_group)
        # --- 3.6 CHUTE CONFIGURATION & GROUPING (NEW) ---
        chute_group = QGroupBox("CHUTE CONFIGURATION & GROUPING")
        chute_layout = QVBoxLayout(chute_group)

        # Total Chutes
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("TOTAL CHUTES:"))
        self.btn_total_chutes = QPushButton("1")
        self.btn_total_chutes.setStyleSheet(
            "background: #333; color: #ffeb3b; font-weight: bold; font-size: 16px; padding: 8px;")
        self.btn_total_chutes.clicked.connect(
            lambda: self.open_keypad(self.btn_total_chutes.text(), self.update_total_chutes))
        row1.addWidget(self.btn_total_chutes)
        chute_layout.addLayout(row1)

        # Navigation & Grouping
        row2 = QHBoxLayout()
        self.btn_prev_chute = QPushButton("◀ PREV")
        self.btn_prev_chute.setStyleSheet("background: #555; font-weight: bold;")
        self.btn_prev_chute.clicked.connect(self.prev_chute)

        self.lbl_current_chute = QLabel("CHUTE 1")
        self.lbl_current_chute.setAlignment(Qt.AlignCenter)
        self.lbl_current_chute.setStyleSheet("font-size: 18px; color: #00bcd4; font-weight: bold;")

        self.btn_next_chute = QPushButton("NEXT ▶")
        self.btn_next_chute.setStyleSheet("background: #555; font-weight: bold;")
        self.btn_next_chute.clicked.connect(self.next_chute)

        row2.addWidget(self.btn_prev_chute)
        row2.addWidget(self.lbl_current_chute)
        row2.addWidget(self.btn_next_chute)
        chute_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("CHUTE ROLE:"))
        self.combo_chute_role = QComboBox()
        self.combo_chute_role.addItems(["PRIMARY", "SECONDARY", "THIRD", "FINAL", "DISABLED"])
        self.combo_chute_role.setStyleSheet("background: #333; color: white; padding: 5px; font-weight: bold;")
        self.combo_chute_role.currentTextChanged.connect(self.update_chute_role)
        row3.addWidget(self.combo_chute_role)
        chute_layout.addLayout(row3)

        left_layout.addWidget(chute_group)

        self.current_chute_idx = 1  # தற்போதைய Chute-ஐ டிராக் செய்ய

        # (உங்கள் பழைய கோடில் CHUTE CONFIGURATION முடிவுக்குப் பிறகு இதைச் சேர்க்கவும்)

        # --- 3.7 ROI (REGION OF INTEREST) SETUP (புதியது) ---
        roi_group = QGroupBox("IMAGE ROI (CROP) SETTINGS")
        roi_layout = QHBoxLayout(roi_group)

        # ROI Width
        roi_layout.addWidget(QLabel("ROI W:"))
        self.btn_roi_w = QPushButton("4096")
        self.btn_roi_w.setStyleSheet(
            "background: #333; color: #00e676; font-weight: bold; font-size: 16px; padding: 6px;")
        self.btn_roi_w.clicked.connect(lambda: self.open_keypad(self.btn_roi_w.text(), self.update_roi_w))
        roi_layout.addWidget(self.btn_roi_w)

        # ROI Height
        roi_layout.addWidget(QLabel("CAPTURE H:"))
        self.btn_roi_h = QPushButton("512")
        self.btn_roi_h.setStyleSheet(
            "background: #333; color: #ff9800; font-weight: bold; font-size: 16px; padding: 6px;")
        self.btn_roi_h.clicked.connect(lambda: self.open_keypad(self.btn_roi_h.text(), self.update_roi_h))
        roi_layout.addWidget(self.btn_roi_h)
        
        # Inspection Height (Live processing)
        roi_layout.addWidget(QLabel("INSPECT H:"))
        init_inspect_h = str(self.parent().thresholds.get("DETECTION_ROI_H", 100)) if self.parent() else "100"
        self.btn_inspect_h = QPushButton(init_inspect_h)
        self.btn_inspect_h.setStyleSheet(
            "background: #333; color: #e040fb; font-weight: bold; font-size: 16px; padding: 6px;")
        self.btn_inspect_h.clicked.connect(lambda: self.open_keypad(self.btn_inspect_h.text(), self.update_inspect_h))
        roi_layout.addWidget(self.btn_inspect_h)

        left_layout.addWidget(roi_group)

        # 4. Action Buttons
        self.btn_wb = QPushButton("⚙ APPLY WHITE CALIBRATION")
        self.btn_wb.setStyleSheet(
            "background: #ff9800; color: black; font-weight: bold; padding: 10px; margin-top: 10px;")
        self.btn_wb.clicked.connect(lambda: self.worker.trigger_white_balance())
        left_layout.addWidget(self.btn_wb)

        self.btn_save = QPushButton("💾 SAVE CONFIGURATION")
        self.btn_save.setStyleSheet(
            "background: #00e676; color: black; font-weight: bold; padding: 10px; margin-top: 10px;")
        self.btn_save.clicked.connect(self.save_config)
        left_layout.addWidget(self.btn_save)

        left_layout.addStretch()
        main_layout.addWidget(left_panel)

        # --- RIGHT: LIVE VIEW & RGB WAVEFORM ---
        right_panel = QFrame()
        right_panel.setStyleSheet("border: 2px solid #444; background: black;")

        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # 1. Live Image View (TOP)
        self.lbl_live_view = QLabel("CONNECTING TO CAMERA...")
        self.lbl_live_view.setAlignment(Qt.AlignCenter)
        self.lbl_live_view.setStyleSheet(
            "font-size: 16px; color: #555; background: black; border-bottom: 2px solid #555;")
        self.lbl_live_view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        right_layout.addWidget(self.lbl_live_view, stretch=4)  # 520px-க்கு நெருக்கமாக வர stretch-ஐ சரிசெய்தல்

        # 2. RGB Waveform Graph (BOTTOM)
        self.lbl_waveform = QLabel("WAVEFORM GRAPH")
        self.lbl_waveform.setAlignment(Qt.AlignCenter)
        self.lbl_waveform.setStyleSheet("background: #2d2d2d;")  # பின்னணியை Gray ஆக மாற்றுதல்
        self.lbl_waveform.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        right_layout.addWidget(self.lbl_waveform, stretch=3)

        main_layout.addWidget(right_panel, stretch=1)
        self.worker.frame_ready.connect(self.update_live_view)

        # Startup actions
        self.scan_cameras()
        self.switch_camera(self.current_cam)

    # --- HELPER FUNCTIONS FOR KEYPAD & PIXELS (These were missing before!) ---
    def open_keypad(self, current_val, callback):
        try:
            try:
                self.worker.frame_ready.disconnect(self.update_live_view)
            except Exception:
                pass

            kp = Keypad(current_val, callback, self)
            kp.exec_()

            try:
                self.worker.frame_ready.connect(self.update_live_view)
            except Exception:
                pass
        except Exception as e:
            print(f"Keypad Error: {e}")

    def update_roi_w(self, val):
        try:
            v = int(float(val))
            v = max(10, v)  # குறைந்தது 10 பிக்சல்களாவது இருக்க வேண்டும்
            self.configs[self.current_cam]["roi_width"] = v
            self.btn_roi_w.setText(str(v))
            self.worker.roi_w = v
        except Exception as e:
            print(f"Error updating ROI Width: {e}")

    def update_roi_h(self, val):
        try:
            v = int(float(val))
            v = max(10, v)  # குறைந்தது 10 பிக்சல்களாவது இருக்க வேண்டும்
            self.configs[self.current_cam]["roi_height"] = v
            self.btn_roi_h.setText(str(v))
            self.worker.roi_h = v
        except Exception as e:
            print(f"Error updating ROI Height: {e}")

    def update_inspect_h(self, val):
        try:
            v = max(10, int(float(val)))
            self.btn_inspect_h.setText(str(v))
            if self.parent():
                self.parent().thresholds["DETECTION_ROI_H"] = v
                if hasattr(self.parent(), "save_settings"):
                    self.parent().save_settings()
        except:
            pass




    def update_channels(self, val):
        try:
            v = int(float(val))
            self.configs[self.current_cam]["num_channels"] = v
            self.btn_channels.setText(str(v))
        except Exception:
            pass

    def update_start_pixel(self, val):
        try:
            v = int(float(val))
            self.configs[self.current_cam]["start_pixel"] = v
            self.btn_start_pix.setText(str(v))
        except Exception:
            pass

    def update_end_pixel(self, val):
        try:
            v = int(float(val))
            self.configs[self.current_cam]["end_pixel"] = v
            self.btn_end_pix.setText(str(v))
        except Exception:
            pass

    def update_total_chutes(self, val):
        try:
            v = int(float(val))
            v = max(1, min(10, v))  # அதிகபட்சம் 10 Chutes
            self.configs[self.current_cam]["total_chutes"] = v
            self.btn_total_chutes.setText(str(v))

            # புதிய Chute-களுக்கு Default Role உருவாக்குதல்
            if "chute_roles" not in self.configs[self.current_cam]:
                self.configs[self.current_cam]["chute_roles"] = {}
            for i in range(1, v + 1):
                if str(i) not in self.configs[self.current_cam]["chute_roles"]:
                    self.configs[self.current_cam]["chute_roles"][str(i)] = "PRIMARY"

            self.current_chute_idx = 1
            self.refresh_chute_ui()
        except Exception:
            pass

    def prev_chute(self):
        total = self.configs[self.current_cam].get("total_chutes", 1)
        if self.current_chute_idx > 1:
            self.current_chute_idx -= 1
            self.refresh_chute_ui()

    def next_chute(self):
        total = self.configs[self.current_cam].get("total_chutes", 1)
        if self.current_chute_idx < total:
            self.current_chute_idx += 1
            self.refresh_chute_ui()

    def refresh_chute_ui(self):
        self.lbl_current_chute.setText(f"CHUTE {self.current_chute_idx}")
        roles = self.configs[self.current_cam].get("chute_roles", {})
        current_role = roles.get(str(self.current_chute_idx), "PRIMARY")

        # Dropdown-ல் சரியான Role-ஐ செட் செய்தல்
        idx = self.combo_chute_role.findText(current_role)
        if idx >= 0:
            self.combo_chute_role.blockSignals(True)
            self.combo_chute_role.setCurrentIndex(idx)
            self.combo_chute_role.blockSignals(False)

    def update_chute_role(self, role_text):
        if "chute_roles" not in self.configs[self.current_cam]:
            self.configs[self.current_cam]["chute_roles"] = {}
        self.configs[self.current_cam]["chute_roles"][str(self.current_chute_idx)] = role_text
        self.save_config()  # உடனுக்குடன் சேமித்தல்

    # -------------------------------------------------------------------------

    def load_config(self):
        default_config = {
            "FRONT": {"sn": "", "exposure": 5000, "ana_gain": 1, "dig_gain": 50, "r_gain": 128, "g_gain": 128,
                      "b_gain": 128, "v_stretch": 1, "start_pixel": 0, "end_pixel": 4096, "num_channels": 63},
            "REAR": {"sn": "", "exposure": 5000, "ana_gain": 1, "dig_gain": 50, "r_gain": 128, "g_gain": 128,
                     "b_gain": 128, "v_stretch": 1, "start_pixel": 0, "end_pixel": 4096, "num_channels": 63}
        }
        # பழையபடி JSON-லிருந்து லோட் செய்யும் லாஜிக்...
        if os.path.exists(CAMERA_CONFIG_FILE):
            try:
                with open(CAMERA_CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    for cam in ["FRONT", "REAR"]:
                        if "start_pixel" not in data[cam]: data[cam]["start_pixel"] = 0
                        if "end_pixel" not in data[cam]: data[cam]["end_pixel"] = 4096
                    return data
            except:
                pass
        return default_config

    def save_config(self):
        with open(CAMERA_CONFIG_FILE, "w") as f:
            json.dump(self.configs, f, indent=4)
        QMessageBox.information(self, "Saved", f"Camera configurations saved successfully!")

    def scan_cameras(self):
        self.combo_device.clear()
        if not MVSDK_AVAILABLE:
            self.combo_device.addItem("NO CAMERA DETECTED (MVSDK NOT FOUND)", "")
            return
        dev_list = mvsdk.CameraEnumerateDevice()
        if not dev_list:
            self.combo_device.addItem("NO CAMERA DETECTED", "")
            return

        for dev in dev_list:
            try:
                sn = dev.acSn.decode('utf-8').strip('\x00')
                name = dev.acFriendlyName.decode('utf-8').strip('\x00')
                self.combo_device.addItem(f"{name} (SN: {sn})", sn)
            except:
                pass

    def swap_cameras(self):
        # Swap entire dictionary objects, not just SN
        temp = self.configs["FRONT"].copy()
        self.configs["FRONT"] = self.configs["REAR"].copy()
        self.configs["REAR"] = temp

        self.save_config()
        self.switch_camera(self.current_cam) # Refresh UI instantly
        QMessageBox.information(self, "Swapped", "Cameras Swapped Successfully! Please restart the software to fully apply.")

    def switch_camera(self, cam_name):
        self.current_cam = cam_name
        
        # Update Tab Focus Aesthetics
        if cam_name == "FRONT":
             self.btn_tab_front.setStyleSheet("background: #00e676; color: black; font-weight: bold; font-size: 16px;")
             self.btn_tab_rear.setStyleSheet("background: #333; color: white; font-weight: bold; font-size: 16px;")
        else:
             self.btn_tab_front.setStyleSheet("background: #333; color: white; font-weight: bold; font-size: 16px;")
             self.btn_tab_rear.setStyleSheet("background: #00e676; color: black; font-weight: bold; font-size: 16px;")
             
        try:
            self.worker.frame_ready.disconnect(self.update_live_view)
        except TypeError:
            pass

        self.worker = self.workers[cam_name]
        self.worker.frame_ready.connect(self.update_live_view)

        cfg = self.configs[cam_name]
        saved_sn = cfg.get("sn", "")

        idx = self.combo_device.findData(saved_sn)
        if idx >= 0: self.combo_device.setCurrentIndex(idx)

        self.btn_start_pix.setText(str(cfg.get("start_pixel", 0)))
        self.btn_end_pix.setText(str(cfg.get("end_pixel", 4096)))
        # --- புதியது ---
        self.btn_roi_w.setText(str(cfg.get("roi_width", 4096)))
        self.btn_roi_h.setText(str(cfg.get("roi_height", 512)))

        # worker-க்கு மதிப்பை அனுப்புதல்
        self.worker.roi_w = cfg.get("roi_width", 4096)
        self.worker.roi_h = cfg.get("roi_height", 512)

        for key, btn in self.text_inputs.items():
            # Get default values for bounds checking in previous config logic
            defaults = {"exposure": 5000, "ana_gain": 1, "dig_gain": 50, "r_gain": 128, "g_gain": 128, "b_gain": 128, "v_stretch": 1}
            val = cfg.get(key, defaults.get(key, 1))
            btn.setText(str(val))
            self.worker.set_param(key, val)

    def update_param(self, key, val, btn):
        btn.setText(str(val))
        self.configs[self.current_cam][key] = val
        self.worker.set_param(key, val)

    def apply_connection(self):
        if not MVSDK_AVAILABLE:
            QMessageBox.warning(self, "Error", "Camera SDK not available.")
            return
        selected_sn = self.combo_device.currentData()
        if not selected_sn: return

        self.configs[self.current_cam]["sn"] = selected_sn
        worker = self.workers[self.current_cam]

        # Cleanup old handle
        if worker.camera_handle is not None:
            try:
                mvsdk.CameraUnInit(worker.camera_handle)
            except:
                pass
            worker.camera_handle = None

        dev_list = mvsdk.CameraEnumerateDevice()
        found_dev = next((d for d in dev_list if d.acSn.decode('utf-8').strip('\x00') == selected_sn), None)

        if not found_dev:
            QMessageBox.warning(self, "Error", "Camera not found!")
            return

        try:
            h = mvsdk.CameraInit(found_dev, -1, -1)
            mvsdk.CameraSetTriggerMode(h, 0)
            mvsdk.CameraSetIspOutFormat(h, mvsdk.CAMERA_MEDIA_TYPE_BGR8)
            mvsdk.CameraPlay(h)
            worker.camera_handle = h
            worker._raw_dev = found_dev
            worker.connect_camera(selected_sn)
            QMessageBox.information(self, "Success", f"{self.current_cam} Connected!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed: {e}")

    def update_live_view(self, frame):
        if frame is None: return
        v_stretch = getattr(self.parent(), 'v_stretch', 3.0) if self.parent() else 3.0
        if v_stretch > 1.0:
            h_f, w_f = frame.shape[:2]
            frame = cv2.resize(frame, (w_f, int(h_f * v_stretch)), interpolation=cv2.INTER_LINEAR)
        h, w, c = frame.shape

        # --- 1. இமேஜ் ரெசல்யூசன் சரிசெய்தல் (Dynamic Scaling without Zooming) ---
        label_w = self.lbl_live_view.width()
        label_h = self.lbl_live_view.height()

        if label_w > 0 and label_h > 0:
            rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
            rgba = np.ascontiguousarray(rgba)
            q_img = QImage(rgba.data, w, h, rgba.strides[0], QImage.Format_RGBA8888)

            # Label-ன் அளவை எடுத்துக்கொண்டு, Aspect Ratio சிறிதும் மாறாமல் (KeepAspectRatio) சுருக்குகிறோம்
            scaled_pixmap = QPixmap.fromImage(q_img).scaled(label_w, label_h, Qt.KeepAspectRatio,
                                                            Qt.SmoothTransformation)
            self.lbl_live_view.setPixmap(scaled_pixmap)

        # --- 2. RGB Waveform கணக்கீடு மற்றும் வரைதல் ---
        graph_w = self.lbl_waveform.width()
        graph_h = self.lbl_waveform.height()

        if graph_w > 0 and graph_h > 0:
            # படத்தின் மையப்பகுதியில் இருந்து சில வரிகளை (Lines) எடுத்து சராசரி காண்கிறோம்
            mid_y = h // 2
            slice_lines = frame[max(0, mid_y - 5): min(h, mid_y + 6), :]  # துல்லியத்திற்காக 10 வரிகளை எடுக்கிறோம்
            avg_line = np.mean(slice_lines, axis=0)

            # 4096 பிக்சல்களை Graph அகலத்திற்கு சுருக்குகிறோம்
            avg_line_resized = cv2.resize(avg_line.reshape(1, -1, 3), (graph_w, 1))[0]

            pix = QPixmap(graph_w, graph_h)
            pix.fill(Qt.black)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)

            # Grid லைன்களை வரைதல் (0 முதல் 255 வரை)
            p.setPen(QPen(QColor("#333"), 1, Qt.DotLine))
            for i in range(0, 256, 50):
                y = graph_h - int((i / 255.0) * graph_h)
                p.drawLine(0, y, graph_w, y)
                p.setPen(QPen(QColor("#555")))
                p.drawText(5, y - 2, str(i))

            # B, G, R மதிப்புகளை தனித்தனி வண்ணங்களில் வரைதல்
            colors = [(0, "#2979ff"), (1, "#00e676"), (2, "#ff1744")]  # B, G, R
            for col_idx, color_hex in colors:
                vals = avg_line_resized[:, col_idx]
                path = QPainterPath()
                p.setPen(QPen(QColor(color_hex), 2))

                start_y = graph_h - ((vals[0] / 255.0) * graph_h)
                path.moveTo(0, start_y)

                for ix, v in enumerate(vals):
                    current_y = graph_h - ((v / 255.0) * graph_h)
                    path.lineTo(ix, current_y)

                p.drawPath(path)

            p.end()
            self.lbl_waveform.setPixmap(pix)

    def update_live_view(self, frame):
        try:
            if frame is None: return
            h, w, c = frame.shape

            # பிக்சல் எல்லைகள்
            start_pix = int(float(self.configs[self.current_cam].get("start_pixel", 0)))
            end_pix = int(float(self.configs[self.current_cam].get("end_pixel", w)))
            start_pix = max(0, min(start_pix, int(w)))
            end_pix = max(0, min(end_pix, int(w)))

            label_w, label_h = self.lbl_live_view.width(), self.lbl_live_view.height()
            graph_w, graph_h = self.lbl_waveform.width(), self.lbl_waveform.height()

            # Graph Data
            if graph_w > 0 and graph_h > 0:
                mid_y = h // 2
                slice_lines = frame[max(0, mid_y - 5): min(h, mid_y + 6), :]
                avg_line = np.mean(slice_lines, axis=0)
                avg_line_resized = cv2.resize(avg_line.reshape(1, -1, 3), (graph_w, 1))[0]

            # 1. Live View-ல் எந்தக் கோடும் இல்லாமல் வெறும் படத்தை மட்டும் காட்டுதல்
            if label_w > 0 and label_h > 0:
                rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
                rgba = np.ascontiguousarray(rgba)
                q_img = QImage(rgba.data, int(w), int(h), rgba.strides[0], QImage.Format_RGBA8888).copy()
                scaled_pixmap = QPixmap.fromImage(q_img).scaled(label_w, label_h, Qt.KeepAspectRatio,
                                                                Qt.SmoothTransformation)
                self.lbl_live_view.setPixmap(scaled_pixmap)

            # 2. RGB Waveform Graph (Ash Gray பின்னணி + கவர்ச்சிகரமான கோடுகள்)
            if graph_w > 0 and graph_h > 0:
                pix = QPixmap(graph_w, graph_h)
                pix.fill(QColor("#404040"))  # Ash Gray background
                p = QPainter(pix)
                p.setRenderHint(QPainter.Antialiasing)

                # Add margin to prevent clipping at the top/bottom
                margin_y = 10
                plot_h = graph_h - 2 * margin_y
                max_y_val = 300.0

                # Grid கோடுகள்
                p.setPen(QPen(QColor("#666"), 1, Qt.DotLine))
                for i in range(0, int(max_y_val) + 1, 50):
                    y = margin_y + plot_h - int((i / max_y_val) * plot_h)
                    p.drawLine(0, y, graph_w, y)
                    p.setPen(QPen(QColor("#ffffff")))
                    p.drawText(5, y - 2, str(i))
                    p.setPen(QPen(QColor("#666"), 1, Qt.DotLine))

                # Waveform வரைதல்
                colors = [(0, "#2979ff"), (1, "#00e676"), (2, "#ff1744")]
                for col_idx, color_hex in colors:
                    vals = avg_line_resized[:, col_idx]
                    path = QPainterPath()
                    p.setPen(QPen(QColor(color_hex), 2))
                    path.moveTo(0, margin_y + plot_h - ((vals[0] / max_y_val) * plot_h))
                    for ix, v in enumerate(vals):
                        path.lineTo(int(ix), int(margin_y + plot_h - ((v / max_y_val) * plot_h)))
                    p.drawPath(path)

                # --- வரைபடத்தில் மட்டும் கோடுகளை வரைதல் (NEW STYLE) ---
                safe_w = max(1, int(w))
                start_x_graph = max(1, int((start_pix / safe_w) * graph_w))
                end_x_graph = min(graph_w - 3, int((end_pix / safe_w) * graph_w))

                # Dotted/Dash Line: Orange (Start) & Pink (End)
                # தடிமனான புள்ளிக் கோடு (Width 3)
                p.setPen(QPen(QColor("#FF9800"), 3, Qt.DashLine))
                p.drawLine(start_x_graph, 0, start_x_graph, graph_h)

                p.setPen(QPen(QColor("#FF00FF"), 3, Qt.DashLine))  # Pink color for End
                p.drawLine(end_x_graph, 0, end_x_graph, graph_h)

                # எண்கள்
                p.setFont(QFont("Arial", 11, QFont.Bold))
                p.setPen(QPen(QColor("#FF9800")))
                p.drawText(start_x_graph + 5, 25, f"START: {start_pix}")
                p.setPen(QPen(QColor("#FF00FF")))
                p.drawText(end_x_graph - 90, 25, f"END: {end_pix}")

                p.end()
                self.lbl_waveform.setPixmap(pix)

        except Exception as e:
            print(f"Update Error: {e}")
class MainDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Industrial Vision HMI v3.0 (Line Scan Ready)")

        # 1920x1080 ரெசல்யூசனுக்கு மாற்றுதல்
        self.setFixedSize(1920, 1080)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.showFullScreen()  # டச் ஸ்கிரீன் பேனலுக்கு இது சிறந்தது
        self.setStyleSheet(STYLESHEET)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        # Initialize system state BEFORE camera connections (DirectConnection needs this!)
        self.system_running = False
        self.wiper_active_now = False

        # Camera Workers
        self.camera_worker_front = GigECameraWorker(is_rear=False)
        self.camera_worker_rear = GigECameraWorker(is_rear=True)

        # Live Frame-ஐ OpenCV-க்கு அனுப்புதல் (Zero-Latency Direct Connection)
        self.camera_worker_front.frame_ready.connect(
            lambda f: self.process_live_frame(f, "FRONT"), Qt.DirectConnection)
        self.camera_worker_rear.frame_ready.connect(
            lambda f: self.process_live_frame(f, "REAR"), Qt.DirectConnection)

        self.auto_connect_cameras()

        # Background Setup
        self.central_widget.setObjectName("CentralBG")

        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        # --- ESP32 Controller ---
        self.esp32 = ESP32Controller(self)

        # State Variables
        self.current_user = "OPERATOR"
        self.ejector_window = None
        self.h_camera = None
        self.system_running = False

        # Default Data from RecipeManager (Multi-Scheme Independent)
        self.recipes_data = RecipeManager.load_all()
        self.program_data = self.recipes_data.get("schemes", {})
        self.current_program = self.recipes_data.get("active_scheme", "RICE")
        curr_scheme_cfg = self.program_data.get(self.current_program, {})
        initial_feeder_speed = curr_scheme_cfg.get("feeder_speed", 50) if isinstance(curr_scheme_cfg, dict) else 50
        self.feeder_data = [{'system_enabled': True, 'active': False, 'value': initial_feeder_speed} for _ in range(10)]

        # Automated Wiper Config: times in MS, interval in MINS
        self.wiper_settings = {'fwd': 1500, 'rev': 1500, 'interval': 6, 'ejector_on': False}
        if os.path.exists("wiper_config.json"):
            try:
                with open("wiper_config.json", "r") as f:
                    self.wiper_settings.update(json.load(f))
            except Exception: 
                pass
                
        self.auto_clean_timer = QTimer(self)
        self.auto_clean_timer.timeout.connect(self.execute_auto_wiper_sequence)

        # Runtime Parameters
        self.thresholds = {
            "HSV": 85, "CHROME": 75, "SIZE": 90, "DEEP_LEARNING": 95,
            "AI_PURITY": 50, "GRAY_CONTRAST": 50, "GRAY_THRESH": 128,
            "DETECTION_ROI_H": 100, # Reduce frame vertically to save processing time
            "ENABLE_PRESSURE_ALARM": False
        }

        # Try loading defaults overwritten by user factory settings
        if os.path.exists("system_info.json"):
            try:
                with open("system_info.json", "r") as f:
                    sys_data = json.load(f)
                    self.thresholds["ENABLE_PRESSURE_ALARM"] = sys_data.get("enable_pressure_alarm", False)
            except Exception:
                pass

        # பிக்சல் அளவுகள்
        self.bg_settings = {
            "num_channels": 63,
            "start_pixel": 0,
            "end_pixel": 4096,
            "camera_thresh": {
                "FRONT": {"lower": 80, "upper": 225, "band_mode": False},
                "REAR": {"lower": 80, "upper": 225, "band_mode": False}
            }
        }
        # Try loading actual saved config
        if os.path.exists(BG_CONFIG_FILE):
            try:
                with open(BG_CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    if "wave_mode" in data:
                        self.bg_settings["wave_mode"] = data["wave_mode"]
                    if "camera_thresh" in data:
                        self.bg_settings["camera_thresh"] = data["camera_thresh"]
                    else:
                        t_l = data.get("thresh_lower", 80)
                        t_u = data.get("thresh_upper", 225)
                        b_m = data.get("band_mode", False)
                        self.bg_settings["camera_thresh"] = {
                            "FRONT": {"lower": t_l, "upper": t_u, "band_mode": b_m},
                            "REAR": {"lower": t_l, "upper": t_u, "band_mode": b_m}
                        }
            except Exception:
                pass

        # The background CSS is now managed dynamically by update_branding()

        # Alarm Blinker
        self.alarm_active = False
        self.alarm_blink_state = False
        self.blink_timer = QTimer()
        self.blink_timer.timeout.connect(self.toggle_alarm_vis)
        self.blink_timer.start(500)

        # Build UI
        self.setup_header()
        self.setup_body()
        self.setup_footer()

        # Connect ESP32 Modbus Server now that UI widgets are constructed
        self.auto_connect_esp32()
        QTimer.singleShot(800, lambda: self.apply_scheme(self.current_program))

        # Clock
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_time)
        self.timer.start(1000)

        self.change_user_role("OPERATOR")
        self.update_side_panel_visualization(self.current_program)

        # Show welcome overlay screen
        self.welcome_overlay = WelcomeOverlay(self)
        self.welcome_overlay.setGeometry(self.rect())
        self.welcome_overlay.show()
        self.welcome_overlay.raise_()

    def closeEvent(self, event):
        # App Close ஆகும்போது Camera Handle-ஐ Release பண்ணுதல்
        for worker in [self.camera_worker_front, self.camera_worker_rear]:
            worker.running = False
            worker.wait(500)
            if worker.camera_handle is not None:
                try:
                    mvsdk.CameraUnInit(worker.camera_handle)
                except:
                    pass
                worker.camera_handle = None
                
        # Release the ESP32 COM port / Modbus Server to prevent PermissionError(13) on next run
        if hasattr(self, 'esp32') and self.esp32.connected:
            try:
                print("[SHUTDOWN] Stopping all relays and vibrator...")
                for i in range(1, 9):
                    self.esp32.set_relay(i, 0)
                self.esp32.set_vibrator(1, 0)
                if hasattr(self.esp32, 'set_all_ejectors'):
                    self.esp32.set_all_ejectors([0]*64) # Turn off all ejectors
                self.esp32.disconnect()
            except Exception as e:
                print(f"Error disconnecting ESP32: {e}")
                
        event.accept()

    # =========================================================================
    # LIVE OPENCV & DEFECT PROCESSING (WITH HAIR DETECTION & CHANNELS)
    # =========================================================================
    def process_live_frame(self, frame, source="FRONT"):
        if not self.system_running:
            return
            
        if getattr(self, 'wiper_active_now', False):
            return
            
        # Check if any sorting algorithms are active for this program
        prog_data = self.program_data.get(getattr(self, 'current_program', ''), {})
        active_algos = []
        algo_settings = {}
        
        if isinstance(prog_data, dict):
            if "algorithms" in prog_data:
                algo_dict = prog_data.get("algorithms", {})
                active_algos = algo_dict.get(source, [])
            else:
                active_algos = prog_data.get(source, [])
            algo_settings = prog_data.get("algo_settings", {}).get(source, {})
        elif isinstance(prog_data, list):
            active_algos = prog_data
            
        if not active_algos:
            return

        # UI மற்றும் Config-ல் உள்ள செட்டிங்ஸை எடுக்கிறோம்
        camera_configs = {
            "start_pixel": self.bg_settings.get("start_pixel", 0),
            "end_pixel": self.bg_settings.get("end_pixel", 4096),
            "num_channels": self.bg_settings.get("num_channels", 63),
            "wave_mode": self.bg_settings.get("wave_mode", "BLUE"),
            "total_chutes": self.bg_settings.get("total_chutes", 1),
            "chute_roles": self.bg_settings.get("chute_roles", {"1": "PRIMARY"})
        }

        # Move Decision Line to the CENTER of the image (mid_y) where the illumination is!
        h = frame.shape[0]
        mid_y = h // 2
        process_frame = frame[max(0, mid_y - 5) : min(h, mid_y + 6), :]
        self.process_frame_for_defects(process_frame, camera_configs, active_algos, algo_settings, source)

    def get_ejector_config(self):
        """ejector_config.json + recipe schema இருந்து pulse/delay/num_pulses/pulse_gap/min_pixels/sustain_ms எடுக்கும்.
        Returns: (delay_ms, pulse_ms, num_pulses, pulse_gap_ms, min_pixels, sustain_ms)"""
        cfg_path = os.path.join(os.path.dirname(__file__), "ejector_config.json")
        
        # --- SMART CACHE FIX ---
        # Check if file was modified recently to invalidate cache instantly
        try:
            current_mtime = os.path.getmtime(cfg_path) if os.path.exists(cfg_path) else 0
        except Exception:
            current_mtime = 0

        cache = getattr(self, '_ejector_config_cache', None)
        if cache and getattr(self, '_ejector_config_mtime', 0) == current_mtime:
            return cache

        delay_ms   = 100.0
        pulse_ms   = 20.0
        num_pulses = 1        # ஒரு object-க்கு எத்தனை தடவை fire வேண்டும்
        pulse_gap  = 12.0     # ஒவ்வோரு pulse-க்கு இடையே gap (ms)
        min_pixels = 2        # இத்தனை pixels இல்லாவிட்டால் noise ஆக ignore
        sustain_ms = 100.0    # Continuous Stable connection - Hold time for noisy wires

        scheme = RecipeManager.get_scheme(getattr(self, 'current_program', 'RICE'))
        if scheme and isinstance(scheme, dict) and "ejector" in scheme:
            ej = scheme["ejector"]
            delay_ms   = float(ej.get("delay",      delay_ms))
            pulse_ms   = float(ej.get("pulse",      pulse_ms))
            num_pulses = int(  ej.get("num_pulses", num_pulses))
            pulse_gap  = float(ej.get("pulse_gap",  pulse_gap))
            min_pixels = int(  ej.get("min_pixels", min_pixels))
            sustain_ms = float(ej.get("sustain_ms", sustain_ms))

        # Fallback: ejector_config.json
        cfg_path = os.path.join(os.path.dirname(__file__), "ejector_config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r") as f:
                    d = json.load(f)
                    delay_ms   = float(d.get("delay",      delay_ms))
                    pulse_ms   = float(d.get("pulse",      pulse_ms))
                    num_pulses = int(  d.get("num_pulses", num_pulses))
                    pulse_gap  = float(d.get("pulse_gap",  pulse_gap))
                    min_pixels = int(  d.get("min_pixels", min_pixels))
                    sustain_ms = float(d.get("sustain_ms", sustain_ms))
            except Exception:
                pass

        num_pulses = max(1, num_pulses)   # குறைந்தது 1
        min_pixels = max(1, min_pixels)   # குறைந்தது 1

        result = (delay_ms, pulse_ms, num_pulses, pulse_gap, min_pixels, sustain_ms)
        self._ejector_config_cache = result
        self._ejector_config_mtime = current_mtime
        return result

    def process_frame_for_defects(self, frame, camera_configs, active_algos, algo_settings, source="FRONT"):
        start_pixel = camera_configs.get("start_pixel", 0)
        end_pixel = camera_configs.get("end_pixel", 4096)
        num_channels = camera_configs.get("num_channels", 63)
        total_chutes = camera_configs.get("total_chutes", 1)
        chute_roles = camera_configs.get("chute_roles", {})

        if num_channels <= 0: num_channels = 1
        if total_chutes <= 0: total_chutes = 1

        total_valid_pixels = end_pixel - start_pixel
        pixels_per_chute = max(1, total_valid_pixels / total_chutes)
        pixels_per_channel = max(1, pixels_per_chute / num_channels)

        total_valves = num_channels * total_chutes
        active_valves = [0] * total_valves
        trigger_condition_met = False

        b = frame[:, :, 0].astype(float)
        g = frame[:, :, 1].astype(float)
        r = frame[:, :, 2].astype(float)
        
        b_vals_min = np.min(b, axis=0)
        g_vals_min = np.min(g, axis=0)
        r_vals_min = np.min(r, axis=0)

        b_vals_max = np.max(b, axis=0)
        g_vals_max = np.max(g, axis=0)
        r_vals_max = np.max(r, axis=0)
        
        trigger_by_channel = np.zeros(num_channels, dtype=bool)

        for algo_name in active_algos:
            cfg = algo_settings.get(algo_name, {})
            
            # --- GRAY ALGORITHM LOGIC ---
            if "GRAY" in algo_name:
                ch = cfg.get("channel", "GREEN")
                mode = cfg.get("mode", "DARK DEFECT")
                try: sens = float(cfg.get("sens", 50))
                except: sens = 50.0
                try: purity = float(cfg.get("purity", 1))
                except: purity = 1.0

                if ch == "RED":
                    plane_min = r_vals_min
                    plane_max = r_vals_max
                elif ch == "BLUE":
                    plane_min = b_vals_min
                    plane_max = b_vals_max
                else:
                    plane_min = g_vals_min
                    plane_max = g_vals_max

                # Safety check: if chosen channel has NO lighting (e.g. RED selected while only BLUE LED is on)
                # Avoid false firing across all channels
                if np.mean(plane_max) < 40 and np.mean(b_vals_max) > 80:
                    plane_min = b_vals_min
                    plane_max = b_vals_max

                algo_trigger = np.zeros(len(plane_min), dtype=bool)
                
                # Defect Detection based purely on the selected channel and sensitivity
                # This ensures the defect detection is completely decoupled from the main background light.
                if sens > 0:
                    if mode == "DARK DEFECT":
                        # Defect threshold based directly on sensitivity
                        defect_threshold = sens
                        algo_trigger = plane_min < defect_threshold
                    else:
                        # Light defect threshold
                        defect_threshold = 255 - sens
                        algo_trigger = plane_max > defect_threshold
                
                # Zero out bounds
                if start_pixel > 0: algo_trigger[:start_pixel] = False
                if end_pixel < len(algo_trigger): algo_trigger[end_pixel:] = False

                # Purity maps to 1-20 pixels threshold
                min_width = max(1, int((purity / 100.0) * 20))
                
                for ch_idx in range(num_channels):
                    s_idx = int(start_pixel + ch_idx * pixels_per_channel)
                    e_idx = int(start_pixel + (ch_idx + 1) * pixels_per_channel)
                    if np.sum(algo_trigger[s_idx:e_idx]) >= min_width:
                        trigger_by_channel[ch_idx] = True

            # --- SIZE LOGIC ---
            elif "SIZE" in algo_name:
                try: small = int(cfg.get("small", 1000))
                except: small = 1000
                try: big = int(cfg.get("big", 2300))
                except: big = 2300
                size_mode = cfg.get("size_mode", "REJECT BIG")

                min_width_big = max(2, int(big / 25.0))
                max_width_small = max(1, int(small / 25.0))

                gray = np.mean([b_vals, g_vals, r_vals], axis=0)
                thresh_val = 0
                if "camera_thresh" in self.bg_settings and source in self.bg_settings["camera_thresh"]:
                    thresh_val = self.bg_settings["camera_thresh"][source]["lower"]
                
                algo_trigger = (gray < thresh_val)
                if start_pixel > 0: algo_trigger[:start_pixel] = False
                if end_pixel < len(algo_trigger): algo_trigger[end_pixel:] = False

                for ch_idx in range(num_channels):
                    s_idx = int(start_pixel + ch_idx * pixels_per_channel)
                    e_idx = int(start_pixel + (ch_idx + 1) * pixels_per_channel)
                    def_width = np.sum(algo_trigger[s_idx:e_idx])
                    
                    is_big = def_width >= min_width_big
                    is_small = def_width > 0 and def_width <= max_width_small
                    
                    if "BIG" in size_mode and is_big:
                        trigger_by_channel[ch_idx] = True
                    elif "SMALL" in size_mode and is_small:
                        trigger_by_channel[ch_idx] = True
                    elif "BOTH" in size_mode and (is_big or is_small):
                        trigger_by_channel[ch_idx] = True

            # --- HSV LOGIC ---
            elif "HSV" in algo_name:
                try: x1 = int(cfg.get("x1", 50))
                except: x1 = 50
                try: y1 = int(cfg.get("y1", 50))
                except: y1 = 50
                try: x2 = int(cfg.get("x2", 200))
                except: x2 = 200
                try: y2 = int(cfg.get("y2", 200))
                except: y2 = 200
                hsv_mode = cfg.get("hsv_mode", "SORT OUT")
                try: sens = float(cfg.get("sens", 100))
                except: sens = 100.0
                try: purity = float(cfg.get("purity", 1))
                except: purity = 1.0
                
                if getattr(self, '_hsv_map_cache', None) is None:
                    hsv_path = next((p for p in ["hsv.png", os.path.join(os.path.dirname(os.path.abspath(__file__)), "hsv.png"), r"d:\lambton\BV_SORTER\hsv.png"] if os.path.exists(p)), None)
                    if hsv_path:
                        img = cv2.imread(hsv_path)
                        if img is not None:
                            self._hsv_map_cache = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
                
                h_min, h_max, s_min, s_max, v_min, v_max = 0, 180, 0, 255, 0, 255
                if getattr(self, '_hsv_map_cache', None) is not None:
                    min_x = max(0, min(565, min(x1, x2)))
                    max_x = max(min_x + 2, min(565, max(x1, x2)))
                    min_y = max(0, min(500, min(y1, y2)))
                    max_y = max(min_y + 2, min(500, max(y1, y2)))
                    roi = self._hsv_map_cache[min_y:max_y, min_x:max_x]
                    if roi.size > 0:
                        h_min, h_max = int(roi[:,:,0].min()), int(roi[:,:,0].max())
                        s_min, s_max = int(roi[:,:,1].min()), int(roi[:,:,1].max())
                        v_min, v_max = int(roi[:,:,2].min()), int(roi[:,:,2].max())
                        
                pad = int((sens / 100.0) * 22)
                l_hsv = np.array([max(0, h_min - pad), max(20, s_min - pad * 2), max(20, v_min - pad * 2)], dtype=np.uint8)
                u_hsv = np.array([min(180, h_max + pad), min(255, s_max + pad * 2), min(255, v_max + pad * 2)], dtype=np.uint8)
                
                mid_row = frame[5:6, :, :]
                mid_hsv = cv2.cvtColor(mid_row.astype(np.uint8), cv2.COLOR_BGR2HSV)[0]
                
                algo_trigger = cv2.inRange(np.expand_dims(mid_hsv, axis=0), l_hsv, u_hsv)[0] > 0
                if hsv_mode == "SORT IN":
                    algo_trigger = ~algo_trigger
                    
                if start_pixel > 0: algo_trigger[:start_pixel] = False
                if end_pixel < len(algo_trigger): algo_trigger[end_pixel:] = False

                min_width = max(1, int((purity / 100.0) * 20))
                for ch_idx in range(num_channels):
                    s_idx = int(start_pixel + ch_idx * pixels_per_channel)
                    e_idx = int(start_pixel + (ch_idx + 1) * pixels_per_channel)
                    if np.sum(algo_trigger[s_idx:e_idx]) >= min_width:
                        trigger_by_channel[ch_idx] = True

            # --- DEEP LEARNING LOGIC (YOLO / CNN) ---
            elif "DEEP LEARNING" in algo_name or "YOLO" in algo_name:
                if getattr(self, '_dl_buffer', None) is None:
                    self._dl_buffer = []
                
                self._dl_buffer.append(frame[5, :, :])
                
                if len(self._dl_buffer) >= 64:
                    dl_img = np.vstack(self._dl_buffer).astype(np.uint8)
                    self._dl_buffer = []
                    
                    try:
                        if getattr(self, '_live_yolo_model', None) is None:
                            model_path = None
                            for p in [self.program_data.get(getattr(self, 'current_program', ''), {}).get('ai_model', {}).get('model_path'), 'best.pt']:
                                if p and os.path.exists(p):
                                    model_path = p
                                    break
                            if model_path:
                                from ultralytics import YOLO
                                self._live_yolo_model = YOLO(model_path)
                        
                        if getattr(self, '_live_yolo_model', None):
                            results = self._live_yolo_model.predict(dl_img, conf=0.5, verbose=False)
                            for r in results:
                                for box in r.boxes:
                                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                                    for ch_idx in range(num_channels):
                                        s_idx = int(start_pixel + ch_idx * pixels_per_channel)
                                        e_idx = int(start_pixel + (ch_idx + 1) * pixels_per_channel)
                                        if x1 <= e_idx and x2 >= s_idx:
                                            trigger_by_channel[ch_idx] = True
                    except Exception as e:
                        pass

            # --- FALLBACK LOGIC ---
            else:
                thresh_val_lower = 0
                thresh_val_upper = 255
                b_mode = False
                if "camera_thresh" in self.bg_settings and source in self.bg_settings["camera_thresh"]:
                    thresh_val_lower = self.bg_settings["camera_thresh"][source].get("lower", 0)
                    thresh_val_upper = self.bg_settings["camera_thresh"][source].get("upper", 255)
                    b_mode = self.bg_settings["camera_thresh"][source].get("band_mode", False)
                
                algo_trigger = np.zeros(len(b_vals), dtype=bool)
                wave_mode = camera_configs.get("wave_mode", "BLUE")
                
                if b_mode:
                    # BAND PASS ON: Fire ONLY on middle spike (In-Band Rejection)
                    if "BLUE" in wave_mode or "ALL" in wave_mode: algo_trigger |= ((b_vals > thresh_val_lower) & (b_vals < thresh_val_upper))
                    if "GREEN" in wave_mode or "ALL" in wave_mode: algo_trigger |= ((g_vals > thresh_val_lower) & (g_vals < thresh_val_upper))
                    if "RED" in wave_mode or "ALL" in wave_mode: algo_trigger |= ((r_vals > thresh_val_lower) & (r_vals < thresh_val_upper))
                else:
                    # BAND PASS OFF: Fire on yellow for dark, cyan for white (Out-of-Band Rejection)
                    if "BLUE" in wave_mode or "ALL" in wave_mode: algo_trigger |= ((b_vals < thresh_val_lower) | (b_vals > thresh_val_upper))
                    if "GREEN" in wave_mode or "ALL" in wave_mode: algo_trigger |= ((g_vals < thresh_val_lower) | (g_vals > thresh_val_upper))
                    if "RED" in wave_mode or "ALL" in wave_mode: algo_trigger |= ((r_vals < thresh_val_lower) | (r_vals > thresh_val_upper))
                
                if start_pixel > 0: algo_trigger[:start_pixel] = False
                if end_pixel < len(algo_trigger): algo_trigger[end_pixel:] = False

                for ch_idx in range(num_channels):
                    s_idx = int(start_pixel + ch_idx * pixels_per_channel)
                    e_idx = int(start_pixel + (ch_idx + 1) * pixels_per_channel)
                    if np.sum(algo_trigger[s_idx:e_idx]) >= 1:
                        trigger_by_channel[ch_idx] = True

        # =====================================================================
        # CONTINUOUS STABLE CONNECTION GUARD (Sustain / Hold Time)
        # மெல்லிய வயர் (wire) போன்றவை noise-ஆல் விட்டுவிட்டு வருவதைத் தவிர்த்து 
        # sustain_ms நேரம் வரை trigger-ஐ நிலை நிறுத்துகிறது.
        # =====================================================================
        import time
        current_time = time.perf_counter()
        if not hasattr(self, '_channel_sustain'):
            self._channel_sustain = {}
        
        _, _, _, _, _, sustain_ms = self.get_ejector_config()
        
        # 1. Update sustain timers for newly triggered channels
        for ch_idx in range(num_channels):
            if trigger_by_channel[ch_idx]:
                self._channel_sustain[ch_idx] = current_time + (sustain_ms / 1000.0)
                
        # 2. Re-apply sustained triggers to bridge noise gaps
        for ch_idx, end_time in list(self._channel_sustain.items()):
            if current_time <= end_time:
                trigger_by_channel[ch_idx] = True
            else:
                del self._channel_sustain[ch_idx]

        # Map to actual chutes and physical valves
        for ch_idx in range(num_channels):
            if trigger_by_channel[ch_idx]:
                for c_idx in range(total_chutes):
                    v_idx = c_idx * num_channels + ch_idx
                    if 0 <= v_idx < total_valves:
                        role = chute_roles.get(str(c_idx + 1), "PRIMARY")
                        if role == "DISABLED":
                            continue
                        active_valves[v_idx] = 1
                        trigger_condition_met = True

        # =====================================================================
        # ESP32-க்கு கமாண்ட் அனுப்புதல் (Safe Threading Timer Delay உடன்)
        # STABLE WIRE: min_pixels guard | LONG OBJECT: multi-pulse support
        # =====================================================================
        if trigger_condition_met and self.system_running:
            triggered_indices = [i for i, val in enumerate(active_valves[:63]) if val == 1]
            delay_ms, pulse_ms, num_pulses, pulse_gap_ms, min_pixels, sustain_ms = self.get_ejector_config()

            # ------------------------------------------------------------------
            # STABLE WIRE NOISE GUARD
            # trigger_by_channel-ல் ஒவ்வோரு channel-க்கும் எத்தனை pixels trigger ஆனன
            # என்று கணக்கிட்டு, min_pixels-க்கு குறைவானது noise ஆக தள்ள்
            # ------------------------------------------------------------------
            stable_indices = []
            for idx in triggered_indices:
                # triggered_indices is 0-based. calculate pixel coverage.
                pixels_per_ch = max(1, (camera_configs.get("end_pixel", 4096) -
                                        camera_configs.get("start_pixel", 0)) /
                                       max(1, camera_configs.get("num_channels", 63)))
                s_px = int(camera_configs.get("start_pixel", 0) + idx * pixels_per_ch)
                e_px = int(s_px + pixels_per_ch)
                # Count triggered pixels in this channel zone
                triggered_px = int(np.sum(trigger_by_channel[idx:idx+1]))  # 0 or 1 per channel
                # Use pixel-level count from algo_trigger if available, else use 1
                algo_px_count = getattr(self, '_last_algo_pixel_counts', {}).get(idx, pixels_per_ch)
                is_sustained = hasattr(self, '_channel_sustain') and (idx in self._channel_sustain)
                
                # If it's a new trigger, it must pass min_pixels. 
                # If it's already sustained from a previous valid trigger, bypass the guard!
                if algo_px_count >= min_pixels or is_sustained:
                    stable_indices.append(idx)

            if not stable_indices:
                # All triggers were noise - skip firing
                return

            def _delayed_fire():
                if not getattr(self, 'system_running', False): return

                # High-Resolution Delay
                if delay_ms > 0:
                    target_time = time.perf_counter() + (delay_ms / 1000.0)
                    while target_time - time.perf_counter() > 0.002:
                        time.sleep(0.001)
                    while time.perf_counter() < target_time:
                        pass

                if not (hasattr(self, 'esp32') and self.esp32):
                    print("[DEBUG LIVE_SORT] ERROR: No esp32 object found in MainDashboard!")
                    return

                # --- SERIAL THROTTLE FIX for TRUE CONTINUOUS FIRING ---
                # ESP32-க்கு ஒரே நேரத்தில் நூற்றுக்கணக்கான கமாண்ட்கள் சென்றால் serial block ஆகி
                # valve விட்டு விட்டு அடிக்கும் (pulsing/stuttering). 
                # இதைத் தவிர்க்க, ஒரு channel-க்கு ஏற்கனவே command சென்று pulse முடியாமல் இருந்தால்,
                # மீண்டும் உடனே அனுப்பாமல் தடுத்து நிறுத்துகிறோம். (Refreshing the ESP32 timer smoothly)
                
                if not hasattr(self, '_last_fire_ts'):
                    self._last_fire_ts = {}
                
                current_t = time.perf_counter()
                channels_to_fire = []
                
                # Throttle interval: Refresh ESP32 timer when it's halfway done
                # This guarantees the valve STAYS OPEN continuously without gaps!
                throttle_interval = (pulse_ms / 1000.0) * 0.4  
                
                for ch in stable_indices:
                    ch_1_based = ch + 1
                    last_t = self._last_fire_ts.get(ch_1_based, 0)
                    if (current_t - last_t) > throttle_interval:
                        channels_to_fire.append(ch_1_based)
                        self._last_fire_ts[ch_1_based] = current_t

                if channels_to_fire:
                    self.esp32.fire_ejector_channels(channels_to_fire, pulse_ms)

            # Thread-ஆக இயக்குவதால் Camera Worker Delay ஆகாது
            threading.Thread(target=_delayed_fire, daemon=True).start()


    # =========================================================================
    # START / STOP SEQUENCE LOGICS
    # =========================================================================
    def on_start_system(self):
        if not LicenseManager.is_license_valid():
            QMessageBox.critical(self, "License Error", "Cannot start system. License is invalid or expired!")
            return

        # If system is already running, toggle to STOP
        if self.system_running:
            self.on_stop_system()
            return

        # எந்த ப்ரோக்ராம் தேர்ந்தெடுக்கப்பட்டுள்ளது எனப் பார்க்கிறோம்
        schemes = self.program_data.get(self.current_program, [])

        # IMAGE CAPTURE மோடில் இருந்தால் கேமரா விண்டோவை திறக்கவா எனக் கேட்கும்
        if any("IMAGE CAPTURE" in s for s in schemes):
            ret = QMessageBox.question(self, "Capture Mode",
                                       f"Current Program '{self.current_program}' contains IMAGE CAPTURE.\n\nOpen Dataset Tool?",
                                       QMessageBox.Yes | QMessageBox.No)
            if ret == QMessageBox.Yes:
                self.open_camera_window()
            else:
                self.start_sorting_sequence()
        else:
            self.start_sorting_sequence()

    def start_sorting_sequence(self):
        self.system_running = True
        print("[START] Sorting sequence initiated.")
        
        # 0. Auto-reconnect Modbus if offline
        if hasattr(self, 'esp32') and self.esp32 and not getattr(self.esp32, 'is_modbus_connected', lambda: False)():
            print("[INFO] Modbus relay server offline. Attempting auto-connection...")
            port = getattr(self.esp32, 'active_port', None) or "COM13"
            self.esp32.connect(port)

        # Removed Ejector ON at startup (Relay 2 is now defect-only)

        btn = self.findChild(QPushButton, "BtnStart")
        if btn:
            btn.setText("STOP SYSTEM")
            btn.setStyleSheet("""
                QPushButton#BtnStart {
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ff1744, stop:1 #b71c1c);
                    color: #ffffff; font-size: 22px; font-weight: 900;
                    border: 3px solid #ff5252; border-radius: 12px; letter-spacing: 2px;
                }
                QPushButton#BtnStart:hover {
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ff5252, stop:1 #ff1744);
                    border: 3px solid #ffffff; color: #ffffff;
                }
                QPushButton#BtnStart:pressed {
                    background-color: #5f091a; border: 3px solid #ff1744; color: #ffffff;
                }
            """)

        # New Sequence: Wiper -> Ejector -> Vibrator
        if hasattr(self, 'esp32') and self.esp32.connected:
            self.esp32.set_relay(3, 1) # Wiper ON
            print("[SEQ] Wiper ON")
            
            def seq_step2():
                self.esp32.set_relay(3, 0) # Wiper OFF
                self.esp32.set_relay(2, 1) # Ejector ON
                print("[SEQ] Wiper OFF, Ejector ON")
                QTimer.singleShot(1000, seq_step3)
                
            def seq_step3():
                self.esp32.set_relay(1, 1) # Vibrator ON
                self.esp32.set_vibrator(1, 50)
                print("[SEQ] Vibrator ON")
                self.turn_on_vibrators()
                
            QTimer.singleShot(1000, seq_step2)
        self.restart_auto_wiper_timer()

    def restart_auto_wiper_timer(self):
        self.auto_clean_timer.stop()
        if self.system_running:
            mins = self.wiper_settings.get('interval', 6)
            self.auto_clean_timer.start(mins * 60000)

    def execute_auto_wiper_sequence(self, force=False):
        if not self.system_running and not force:
            return
            
        print("[AUTO WIPER] Step 1: Stopping feeder immediately...")
        self.wiper_active_now = True
        # Step 1: FIRST STOP THE FEEDER
        self.feeder_data[0]['active'] = False
        if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
            self.esp32.set_relay(1, 0)
            self.esp32.set_feeder(0)
        self.update_feeder_toggle_appearance()

        if hasattr(self, 'btn_wiper'):
            self.btn_wiper.setEnabled(False)
            self.btn_wiper.setText("🧹 PAUSING (3s)...")

        # Step 2: AFTER 3 SECONDS ONLY -> WIPER FORWARD
        print("[AUTO WIPER] Waiting 3 seconds for chute to clear before wiper starts...")
        QTimer.singleShot(3000, self._step2_wiper_fwd)

    def _step2_wiper_fwd(self):
        print("[AUTO WIPER] Step 2: Wiper Forward (Relay 3 = 1)...")
        if hasattr(self, 'btn_wiper'):
            self.btn_wiper.setText("🧹 FWD...")

        if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
            self.esp32.set_relay(3, 1)
            self.esp32.send_command("WIPER_FWD")
            # If ejector during wiper is ON, turn on Ejector Relay 2
            if self.wiper_settings.get('ejector_on', False):
                self.esp32.set_relay(2, 1)
                print("[AUTO WIPER] Ejector active during wipe (Relay 2 = 1)")
            else:
                self.esp32.set_relay(2, 0)

        fwd_time = int(self.wiper_settings.get('fwd', 1500))
        QTimer.singleShot(fwd_time, self._step3_wiper_rev)
        
    def _step3_wiper_rev(self):
        print("[AUTO WIPER] Step 3: Wiper Reverse...")
        if hasattr(self, 'btn_wiper'):
            self.btn_wiper.setText("🧹 REV...")

        if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
            self.esp32.set_relay(3, 1)
            self.esp32.send_command("WIPER_REV")

        rev_time = int(self.wiper_settings.get('rev', 1500))
        QTimer.singleShot(rev_time, self._step4_wiper_stop)
        
    def _step4_wiper_stop(self):
        print("[AUTO WIPER] Step 4: Stop cleaning (Relay 3 = 0). Waiting 2s before resuming feeder...")
        if hasattr(self, 'btn_wiper'):
            self.btn_wiper.setText("🧹 WAITING (2s)...")

        if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
            self.esp32.set_relay(3, 0)
            self.esp32.send_command("WIPER_OFF")
            if not self.system_running:
                self.esp32.set_relay(2, 0)

        # AFTER 2 SECONDS -> TURN ON THE FEEDER
        QTimer.singleShot(2000, self._step5_wiper_resume)
        
    def _step5_wiper_resume(self):
        self.wiper_active_now = False
        if hasattr(self, 'btn_wiper'):
            self.btn_wiper.setText("🧹 CLEAN")
            self.btn_wiper.setEnabled(True)

        if self.system_running:
            print("[AUTO WIPER] Step 5: 2s elapsed, turning ON the feeder (Relay 1 = 1) & Ejector (Relay 2 = 1)...")
            self.feeder_data[0]['active'] = True
            if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
                self.esp32.set_relay(1, 1)
            self.on_master_feeder_val_changed(self.feeder_data[0]['value'])
            self.update_feeder_toggle_appearance()
        else:
            print("[AUTO WIPER] Clean sequence finished.")

    def turn_on_vibrators(self):
        if self.system_running:
            print("[START] 1 Second Completed: Vibrator (Relay 1) ON.")
            self.feeder_data[0]['active'] = True
            if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
                self.esp32.set_relay(1, 1)
            self.on_master_feeder_val_changed(self.feeder_data[0]['value'])
            self.update_feeder_toggle_appearance()

    def on_stop_system(self):
        self.system_running = False
        self.auto_clean_timer.stop()
        
        # Clear UI Ejectors
        if hasattr(self, 'ejector_led_bar'):
            self.ejector_led_bar.clear_leds()
            
        if hasattr(self, 'esp32') and self.esp32:
            if hasattr(self.esp32, 'stop_all_ejectors'):
                self.esp32.stop_all_ejectors()
            if self.esp32.connected:
                self.esp32.set_relay(1, 0)  # Vibrator OFF
                self.esp32.set_relay(2, 0)  # Ejector OFF
                self.esp32.set_relay(3, 0)  # Wiper OFF
                print("[STOP] Feeder (Relay 1) & Ejector (Relay 2) turned OFF.")
        
        btn = self.findChild(QPushButton, "BtnStart")
        if btn:
            btn.setText("START SYSTEM")
            btn.setStyleSheet("""
                QPushButton#BtnStart {
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #00e676, stop:1 #00b0ff);
                    color: #000000; font-size: 22px; font-weight: 900;
                    border: 3px solid #69f0ae; border-radius: 12px; letter-spacing: 2px;
                }
                QPushButton#BtnStart:hover {
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #69f0ae, stop:1 #18ffff);
                    border: 3px solid #ffffff; color: #000000;
                }
                QPushButton#BtnStart:pressed {
                    background-color: #004d40; border: 3px solid #00e676; color: #ffffff;
                }
            """)

        self.feeder_data[0]['active'] = False
        if hasattr(self, 'esp32') and self.esp32.connected:
            self.esp32.set_vibrator(1, 0)
        self.update_feeder_toggle_appearance()
        
        print("System Stopped: Vibrators and Ejectors OFF.")
        # Popup removed: user requested seamless stop without popup dialog

    def enforce_license_lockdown(self):
        self.system_running = False
        self.auto_clean_timer.stop()
        btn = self.findChild(QPushButton, "BtnStart")
        if btn:
            btn.setText("START SYSTEM")
            btn.setStyleSheet("""
                QPushButton#BtnStart {
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #00e676, stop:1 #00b0ff);
                    color: #000000; font-size: 22px; font-weight: 900;
                    border: 3px solid #69f0ae; border-radius: 12px; letter-spacing: 2px;
                }
                QPushButton#BtnStart:hover {
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #69f0ae, stop:1 #18ffff);
                    border: 3px solid #ffffff; color: #000000;
                }
                QPushButton#BtnStart:pressed {
                    background-color: #004d40; border: 3px solid #00e676; color: #ffffff;
                }
            """)
        print("[LOCKDOWN] Enforcing license lockdown: Holding sorting and ejectors process only.")
        # 1. Turn off ejectors and stop vibrator (hold detection & sorting process)
        if hasattr(self, 'esp32') and self.esp32.connected:
            self.esp32.set_all_ejectors([0]*128)
            self.esp32.set_vibrator(1, 0)

    # =========================================================================
    # UI SETUP & HELPER FUNCTIONS
    # =========================================================================
    def auto_connect_cameras(self):
        import json, os
        if not LicenseManager.is_license_valid():
            return

        # Default அளவுகளில் roi_width மற்றும் roi_height சேர்க்கப்பட்டுள்ளது
        configs = {
            "FRONT": {"sn": "", "roi_width": 4096, "roi_height": 512},
            "REAR": {"sn": "", "roi_width": 4096, "roi_height": 512}
        }

        if os.path.exists(os.path.join(os.path.dirname(__file__), "camera_config.json")):
            try:
                with open(os.path.join(os.path.dirname(__file__), "camera_config.json"), "r") as f:
                    configs = json.load(f)
            except Exception as e:
                print(f"[WARN] Error loading camera_config.json: {e}")

        # --- 🔥 புதியது: JSON-ல் இருந்து ROI அளவுகளை எடுத்து Worker-க்கு அனுப்புதல் ---
        self.camera_worker_front.roi_w = configs.get("FRONT", {}).get("roi_width", 4096)
        self.camera_worker_front.roi_h = configs.get("FRONT", {}).get("roi_height", 512)

        self.camera_worker_rear.roi_w = configs.get("REAR", {}).get("roi_width", 4096)
        self.camera_worker_rear.roi_h = configs.get("REAR", {}).get("roi_height", 512)
        # -------------------------------------------------------------------------

        if not MVSDK_AVAILABLE:
            return

        try:
            dev_list = mvsdk.CameraEnumerateDevice()
        except Exception as e:
            import traceback
            print(f"[ERROR] CameraEnumerateDevice failed:\n{traceback.format_exc()}")
            return

        front_sn_target = configs.get("FRONT", {}).get("sn", "").strip()
        rear_sn_target = configs.get("REAR", {}).get("sn", "").strip()
        connected_sn_list = []

        if self.camera_worker_front.camera_handle is None:
            front_found = False
            if front_sn_target:
                for dev in dev_list:
                    try:
                        dev_sn = dev.acSn.decode('utf-8').strip('\x00')
                        if dev_sn == front_sn_target:
                            front_found = True
                            h = mvsdk.CameraInit(dev, -1, -1)
                            mvsdk.CameraSetTriggerMode(h, 0)
                            mvsdk.CameraSetIspOutFormat(h, mvsdk.CAMERA_MEDIA_TYPE_BGR8)
                            mvsdk.CameraPlay(h)
                            self.camera_worker_front.camera_handle = h
                            self.camera_worker_front._raw_dev = dev
                            self.camera_worker_front.connect_camera(dev_sn)
                            
                            # Restore saved hardware settings to worker via set_param
                            for pk in ["exposure", "ana_gain", "dig_gain", "r_gain", "g_gain", "b_gain", "v_stretch"]:
                                if pk in configs.get("FRONT", {}): self.camera_worker_front.set_param(pk, configs["FRONT"][pk])
                                
                            connected_sn_list.append(dev_sn)
                            print(f"[OK] FRONT Camera auto-connected (matched SN): {dev_sn}")
                            break
                    except Exception as e:
                        import traceback
                        print(f"[ERROR] Auto connect FRONT (matched) failed:\n{traceback.format_exc()}")
            if not front_found:
                for dev in dev_list:
                    try:
                        dev_sn = dev.acSn.decode('utf-8').strip('\x00')
                        if dev_sn not in connected_sn_list:
                            front_found = True
                            h = mvsdk.CameraInit(dev, -1, -1)
                            mvsdk.CameraSetTriggerMode(h, 0)
                            mvsdk.CameraSetIspOutFormat(h, mvsdk.CAMERA_MEDIA_TYPE_BGR8)
                            mvsdk.CameraPlay(h)
                            self.camera_worker_front.camera_handle = h
                            self.camera_worker_front._raw_dev = dev
                            self.camera_worker_front.connect_camera(dev_sn)
                            
                            for pk in ["exposure", "ana_gain", "dig_gain", "r_gain", "g_gain", "b_gain", "v_stretch"]:
                                if pk in configs.get("FRONT", {}): self.camera_worker_front.set_param(pk, configs["FRONT"][pk])
                                
                            connected_sn_list.append(dev_sn)
                            print(f"[OK] FRONT Camera auto-connected (fallback SN): {dev_sn}")
                            break
                    except Exception as e:
                        import traceback
                        print(f"[ERROR] Auto connect FRONT (fallback) failed:\n{traceback.format_exc()}")
        elif self.camera_worker_front.camera_handle is not None and getattr(self.camera_worker_front, 'ip_address', None):
            connected_sn_list.append(self.camera_worker_front.ip_address)

        if self.camera_worker_rear.camera_handle is None:
            rear_found = False
            if rear_sn_target:
                for dev in dev_list:
                    try:
                        dev_sn = dev.acSn.decode('utf-8').strip('\x00')
                        if dev_sn in connected_sn_list: continue
                        if dev_sn == rear_sn_target:
                            rear_found = True
                            h = mvsdk.CameraInit(dev, -1, -1)
                            mvsdk.CameraSetTriggerMode(h, 0)
                            mvsdk.CameraSetIspOutFormat(h, mvsdk.CAMERA_MEDIA_TYPE_BGR8)
                            mvsdk.CameraPlay(h)
                            self.camera_worker_rear.camera_handle = h
                            self.camera_worker_rear._raw_dev = dev
                            self.camera_worker_rear.connect_camera(dev_sn)
                            
                            for pk in ["exposure", "ana_gain", "dig_gain", "r_gain", "g_gain", "b_gain", "v_stretch"]:
                                if pk in configs.get("REAR", {}): self.camera_worker_rear.set_param(pk, configs["REAR"][pk])
                                
                            print(f"[OK] REAR Camera auto-connected (matched SN): {dev_sn}")
                            break
                    except Exception as e:
                        import traceback
                        print(f"[ERROR] Auto connect REAR (matched) failed:\n{traceback.format_exc()}")
            if not rear_found:
                for dev in dev_list:
                    try:
                        dev_sn = dev.acSn.decode('utf-8').strip('\x00')
                        if dev_sn not in connected_sn_list:
                            rear_found = True
                            h = mvsdk.CameraInit(dev, -1, -1)
                            mvsdk.CameraSetTriggerMode(h, 0)
                            mvsdk.CameraSetIspOutFormat(h, mvsdk.CAMERA_MEDIA_TYPE_BGR8)
                            mvsdk.CameraPlay(h)
                            self.camera_worker_rear.camera_handle = h
                            self.camera_worker_rear._raw_dev = dev
                            self.camera_worker_rear.connect_camera(dev_sn)
                            
                            for pk in ["exposure", "ana_gain", "dig_gain", "r_gain", "g_gain", "b_gain", "v_stretch"]:
                                if pk in configs.get("REAR", {}): self.camera_worker_rear.set_param(pk, configs["REAR"][pk])
                                
                            print(f"[OK] REAR Camera auto-connected (fallback SN): {dev_sn}")
                            break
                    except Exception as e:
                        import traceback
                        print(f"[ERROR] Auto connect REAR (fallback) failed:\n{traceback.format_exc()}")

    def auto_connect_esp32(self):
        import json, os
        import serial.tools.list_ports
        config_file = "system_config.json"
        
        cfg = {}
        configured_port = None
        auto_connect = True
        if os.path.exists(config_file):
            try:
                with open(config_file, "r") as f:
                    cfg = json.load(f)
                    configured_port = cfg.get("last_port", None)
                    auto_connect = cfg.get("auto_connect", True)
            except Exception as e:
                print(f"[WARN] Error reading system_config.json: {e}")

        # Scan all available COM ports
        available = serial.tools.list_ports.comports()
        avail_devs = [p.device for p in available]
        rs485_ports = []
        other_ports = []
        for p in available:
            desc = (p.description or "").upper()
            hwid = (p.hwid or "").upper()
            if "0403:6001" in hwid or "FTDI" in desc or "USB SERIAL PORT" in desc:
                rs485_ports.append(p.device)
            else:
                other_ports.append(p.device)

        # Build candidate priority list for Modbus Relay Board
        candidate_ports = []
        if configured_port and configured_port in avail_devs:
            candidate_ports.append(configured_port)
        for rp in rs485_ports:
            if rp not in candidate_ports:
                candidate_ports.append(rp)
        for op in other_ports:
            if op not in candidate_ports:
                candidate_ports.append(op)
        if configured_port and configured_port not in candidate_ports:
            candidate_ports.append(configured_port)
        if "COM13" in avail_devs and "COM13" not in candidate_ports:
            candidate_ports.insert(0, "COM13")

        if auto_connect:
            # 1. Connect Modbus RS485 / Serial Relay Board
            modbus_success = False
            modbus_active_port = None
            modbus_enabled = cfg.get("modbus_enabled", True)
            if modbus_enabled and candidate_ports:
                for port in candidate_ports:
                    print(f"Attempting Modbus Server on {port}...")
                    if self.esp32.connect(port):
                        modbus_success = True
                        modbus_active_port = port
                        print(f"[OK] Modbus Relay Server active on {modbus_active_port}")
                        break
            else:
                print("[INFO] Modbus RS485 Relay connection inactive/removed.")

            # 2. Connect 30-pin USB ESP32 (63-Channel Shift Register Ejector on COM24)
            configured_vibrator = cfg.get("vibrator_port", None)
            
            # Hardcoded to COM24 as per user request
            print("Attempting Ejector auto-connect on COM24...")
            if self.esp32.connect_ejector("COM24", baudrate=115200):
                cfg["ejector_port"] = "COM24"
            
            # 3. Connect Vibrator / Nuvoton UART
            if configured_vibrator and configured_vibrator in avail_devs and configured_vibrator != modbus_active_port and configured_vibrator != getattr(self.esp32, 'ejector_port', None):
                print(f"Attempting Vibrator auto-connect on {configured_vibrator}...")
                self.esp32.connect_vibrator(configured_vibrator)

            if modbus_success and modbus_active_port:
                try:
                    cfg["last_port"] = modbus_active_port
                    cfg["modbus_enabled"] = True
                    with open(config_file, "w") as f:
                        json.dump(cfg, f, indent=4)
                except Exception:
                    pass

            QTimer.singleShot(400, self.apply_saved_lighting)
            self.update_esp32_relay_status_ui(modbus_success, modbus_active_port)

    def update_esp32_relay_status_ui(self, connected, port=None):
        if port is None and hasattr(self.esp32, 'active_port'):
            port = self.esp32.active_port or "COM24"

        mod_ok = bool(connected or (hasattr(self.esp32, 'is_modbus_connected') and self.esp32.is_modbus_connected()))
        ej_ok = bool(hasattr(self.esp32, 'is_ejector_connected') and self.esp32.is_ejector_connected())
        ej_port = getattr(self.esp32, 'ejector_port', 'COM23') if ej_ok else None

        if mod_ok and ej_ok:
            txt = f"RELAYS: ONLINE ({port}) | EJECTOR: ONLINE ({ej_port})"
            is_online = True
        elif ej_ok:
            txt = f"ESP32 #2 (63-CH EJECTOR): ONLINE ({ej_port}) | RELAYS: OFFLINE"
            is_online = True
        elif mod_ok:
            txt = f"RELAYS: ONLINE ({port})"
            is_online = True
        else:
            txt = "CONTROLLER: OFFLINE"
            is_online = False

        if hasattr(self, 'lbl_relay_comm_status'):
            self.lbl_relay_comm_status.setText(txt)
            if is_online:
                self.lbl_relay_comm_status.setStyleSheet(
                    "background-color: #102a1c; color: #00e676; border: 2px solid #00e676; "
                    "border-radius: 6px; font-weight: bold; font-size: 12px; padding: 6px 12px;"
                )
            else:
                self.lbl_relay_comm_status.setStyleSheet(
                    "background-color: #261616; color: #ff1744; border: 2px solid #ff1744; "
                    "border-radius: 6px; font-weight: bold; font-size: 12px; padding: 6px 12px;"
                )
        if hasattr(self, 'lbl_status_esp'):
            self.lbl_status_esp.setText(f"ONLINE ({ej_port or port})" if is_online else "OFFLINE")
            self.lbl_status_esp.setStyleSheet(f"color: {'#00e676' if is_online else '#ff1744'}; font-weight: bold; font-size: 11px;")

    def run_startup_relay_sequence(self):
        """Automates relay sequence at startup: LED -> Ejector -> Wiper (forward/back)"""
        print("[STARTUP] Running automatic relay sequence...")
        # 1. Turn ON LED (Relay 4 - Default White Top, or apply saved lighting)
        self.apply_saved_lighting()
        
        # 2. Turn ON Ejector Main Air (Relay 2)
        if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
            self.esp32.set_relay(2, 1)
            print("[STARTUP] Ejector Main Air (Relay 2) turned ON")
            
            # 3. Trigger Auto Wiper Sequence
            print("[STARTUP] Triggering Auto Wiper sequence...")
            self.execute_auto_wiper_sequence(force=True)

    def apply_saved_lighting(self, skip_esp=False):
        """Loads light_config.json and applies exact saved states to all LEDs and Modbus relays."""
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LIGHT_CONFIG_FILE)
        if not os.path.exists(cfg_path):
            self.turn_on_default_leds()
            return

        try:
            with open(cfg_path, "r") as f:
                lcfg = json.load(f)
        except Exception as e:
            print(f"[LIGHTS] Error loading {cfg_path}: {e}")
            self.turn_on_default_leds()
            return

        light_order = [
            "WHITE FRONT TOP", "WHITE FRONT BOTTOM", "WHITE BACK TOP", "WHITE BACK BOTTOM",
            "RED FRONT", "GREEN FRONT", "BLUE FRONT",
            "RED BACK", "GREEN BACK", "BLUE BACK"
        ]

        any_on = False
        for idx, name in enumerate(light_order):
            item = lcfg.get(name, {})
            enabled = item.get("enabled", False)
            val = item.get("lux", 500)
            pwm_val = min(255, val) if enabled else 0
            if enabled:
                any_on = True

            if not skip_esp and hasattr(self, 'esp32') and self.esp32:
                self.esp32.set_led(idx + 1, pwm_val)

        if hasattr(self, 'btn_header_lights'):
            if any_on:
                self.btn_header_lights.setText("LIGHTS: ON")
                self.btn_header_lights.setStyleSheet(
                    "background-color: #1b5e20; color: #ffffff; border: 2px solid #00e676; "
                    "border-radius: 6px; font-weight: bold; font-size: 12px; padding: 6px 12px;"
                )
            else:
                self.btn_header_lights.setText("LIGHTS: OFF")
                self.btn_header_lights.setStyleSheet(
                    "background-color: #37474f; color: #b0bec5; border: 1px solid #78909c; "
                    "border-radius: 6px; font-weight: bold; font-size: 12px; padding: 6px 12px;"
                )

    def turn_on_default_leds(self):
        """Turn on default illumination LEDs (Relays 4, 5, 6, 7) if no config exists"""
        if hasattr(self, 'esp32') and self.esp32 and getattr(self.esp32, 'is_modbus_connected', lambda: self.esp32.connected)():
            print("[STARTUP] Turning ON default illumination LEDs (Relays 4, 5, 6, 7)...")
            for r_id in [4, 5, 6, 7]:
                self.esp32.set_relay(r_id, 1)
            
            if hasattr(self, 'btn_header_lights'):
                self.btn_header_lights.setText("LIGHTS: ON")
                self.btn_header_lights.setStyleSheet(
                    "background-color: #1b5e20; color: #ffffff; border: 2px solid #00e676; "
                    "border-radius: 6px; font-weight: bold; font-size: 12px; padding: 6px 12px;"
                )

    def toggle_all_leds(self):
        """Toggle all illumination LEDs (Relays 4, 5, 6, 7, 8) from UI button"""
        if not hasattr(self, 'esp32') or not self.esp32:
            return

        if not getattr(self.esp32, 'is_modbus_connected', lambda: False)():
            print("[INFO] Modbus relay server offline. Attempting auto-connection on COM4...")
            port = getattr(self.esp32, 'active_port', None) or "COM24"
            if self.esp32.connect(port):
                self.update_esp32_relay_status_ui(True, port)
            else:
                QMessageBox.warning(self, "Relay Board Offline", f"ESP32 Modbus Relay Board ({port}) is offline!\nPlease check the RS485 USB cable or click 'RELAYS' button to connect.")
                return

        current_state = self.esp32.get_relay(4)
        new_state = 0 if current_state else 1

        for r_id in [4, 5, 6, 7, 8]:
            self.esp32.set_relay(r_id, new_state)

        if new_state:
            self.btn_header_lights.setText("LIGHTS: ON")
            self.btn_header_lights.setStyleSheet(
                "background-color: #1b5e20; color: #ffffff; border: 2px solid #00e676; "
                "border-radius: 6px; font-weight: bold; font-size: 12px; padding: 6px 12px;"
            )
        else:
            self.btn_header_lights.setText("LIGHTS: OFF")
            self.btn_header_lights.setStyleSheet(
                "background-color: #37474f; color: #b0bec5; border: 1px solid #78909c; "
                "border-radius: 6px; font-weight: bold; font-size: 12px; padding: 6px 12px;"
            )

    def open_relay_manager_dialog(self):
        dlg = RelayManagerDialog(self.esp32, self)
        dlg.exec_()
        if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
            r4 = self.esp32.get_relay(4)
            if r4:
                self.btn_header_lights.setText("LIGHTS: ON")
                self.btn_header_lights.setStyleSheet(
                    "background-color: #1b5e20; color: #ffffff; border: 2px solid #00e676; "
                    "border-radius: 6px; font-weight: bold; font-size: 12px; padding: 6px 12px;"
                )
            else:
                self.btn_header_lights.setText("LIGHTS: OFF")
                self.btn_header_lights.setStyleSheet(
                    "background-color: #37474f; color: #b0bec5; border: 1px solid #78909c; "
                    "border-radius: 6px; font-weight: bold; font-size: 12px; padding: 6px 12px;"
                )

    def show_relay_controls(self):
        if not hasattr(self, 'relay_window'):
            self.relay_window = RelayController()
        pass # self.relay_window.show()
        self.relay_window.raise_()
        self.relay_window.activateWindow()

    def setup_header(self):
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(90)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 0, 20, 0)

        self.logo_label = QLabel()
        self.logo_label.setFixedSize(150, 80)
        self.logo_label.setScaledContents(True)
        layout.addWidget(self.logo_label, 0, Qt.AlignLeft)
        
        # Load local override branding (if any)
        self.update_branding()

        # ADD RELAY CONTROL BUTTON
        self.btn_relay_controls = QPushButton("  Relay Controls  ")
        self.btn_relay_controls.setStyleSheet("""
            QPushButton {
                background-color: #2196F3; color: white; font-size: 16px; font-weight: bold;
                border-radius: 6px; padding: 10px 20px; margin-left: 20px;
            }
            QPushButton:hover { background-color: #1976D2; }
        """)
        pass # pass # self.btn_relay_controls.clicked.connect(self.show_relay_controls)
        layout.addWidget(self.btn_relay_controls, 0, Qt.AlignLeft)

        layout.addStretch()
        self.lbl_alarm = QLabel("⚠ AIR PRESSURE LOW ⚠")
        self.lbl_alarm.setObjectName("AlarmLabel")
        self.lbl_alarm.setAlignment(Qt.AlignCenter)
        self.lbl_alarm.setVisible(False)
        layout.addWidget(self.lbl_alarm)

        self.lbl_license_warn = QLabel()
        self.lbl_license_warn.setObjectName("LicenseWarnLabel")
        self.lbl_license_warn.setAlignment(Qt.AlignCenter)
        self.lbl_license_warn.setStyleSheet(
            "background-color: #ff9800; color: black; font-weight: bold; font-size: 16px; border-radius: 4px; padding: 5px; margin-left: 10px;"
        )
        self.lbl_license_warn.setVisible(False)
        layout.addWidget(self.lbl_license_warn)
        layout.addStretch()

        right_panel = QHBoxLayout()
        right_panel.setSpacing(10)

        # 1. Real-Time Relay Communication Status Badge
        self.lbl_relay_comm_status = QPushButton("RELAYS: CONNECTING...")
        self.lbl_relay_comm_status.setObjectName("RelayCommStatusBtn")
        self.lbl_relay_comm_status.setCursor(Qt.PointingHandCursor)
        self.lbl_relay_comm_status.setToolTip("Click to open Relay Diagnostics & Port Settings")
        self.lbl_relay_comm_status.setStyleSheet("background-color: #261616; color: #ff1744; border: 2px solid #ff1744; border-radius: 6px; font-weight: bold; font-size: 12px; padding: 6px 12px;")
        self.lbl_relay_comm_status.clicked.connect(self.open_relay_manager_dialog)
        right_panel.addWidget(self.lbl_relay_comm_status)

        # 2. Main Lighting Toggle Button
        self.btn_header_lights = QPushButton("LIGHTS: OFF")
        self.btn_header_lights.setObjectName("HeaderLightsBtn")
        self.btn_header_lights.setCursor(Qt.PointingHandCursor)
        self.btn_header_lights.setToolTip("Toggle Illumination LEDs (Relays 4 to 8)")
        self.btn_header_lights.setStyleSheet("background-color: #37474f; color: #b0bec5; border: 1px solid #78909c; border-radius: 6px; font-weight: bold; font-size: 12px; padding: 6px 12px;")
        self.btn_header_lights.clicked.connect(self.toggle_all_leds)
        right_panel.addWidget(self.btn_header_lights)

        # 3. Dedicated Relay Manager Dialog Button
        self.btn_header_relays = QPushButton("RELAYS")
        self.btn_header_relays.setObjectName("HeaderRelaysBtn")
        self.btn_header_relays.setCursor(Qt.PointingHandCursor)
        self.btn_header_relays.setToolTip("Open ESP32 8-Relay Controller & Diagnostics")
        self.btn_header_relays.setStyleSheet("background-color: #00695c; color: #ffffff; border: 1px solid #00bfa5; border-radius: 6px; font-weight: bold; font-size: 12px; padding: 6px 12px;")
        self.btn_header_relays.clicked.connect(self.open_relay_manager_dialog)
        right_panel.addWidget(self.btn_header_relays)

        # 3b. Dedicated 63-CH Ejector Console Dialog Button

        btn_info = QPushButton("INFO")
        btn_info.setProperty("class", "HeaderBtn")
        btn_info.clicked.connect(self.show_software_info)
        right_panel.addWidget(btn_info)

        self.btn_user = QPushButton("OPERATOR")
        self.btn_user.setObjectName("UserBtn")
        self.btn_user.setCursor(Qt.PointingHandCursor)
        self.btn_user.setStyleSheet(
            "border: 1px solid #555; font-size: 14px; min-width: 120px; color: #aaa; margin-left: 15px;")
        self.btn_user.clicked.connect(self.open_login_window)

        self.date_lbl = QLabel("00/00/0000")
        self.date_lbl.setStyleSheet("font-size: 20px; color: #00bcd4; font-weight: bold; margin-left: 15px;")

        self.clock_lbl = QLabel("00:00:00")
        self.clock_lbl.setStyleSheet("font-size: 26px; color: #00e676; font-weight: bold; margin-left: 15px;")

        right_panel.addWidget(self.btn_user)
        right_panel.addWidget(self.date_lbl)
        right_panel.addWidget(self.clock_lbl)
        layout.addLayout(right_panel)
        self.main_layout.addWidget(header)

    def setup_body(self):
        body_widget = QWidget()
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(20, 15, 20, 15)
        body_layout.setSpacing(15)

        body_layout.addWidget(self.create_long_program_bar())

        content_layout = QHBoxLayout()
        self.left_panel = self.create_left_algo_panel()
        content_layout.addWidget(self.left_panel)
        content_layout.addStretch()
        self.right_panel = self.create_right_vibrator_panel()
        content_layout.addWidget(self.right_panel)

        body_layout.addLayout(content_layout)
        body_layout.addStretch()
        self.main_layout.addWidget(body_widget)

    def create_left_algo_panel(self):
        frame = QFrame()
        frame.setObjectName("SidePanel")
        frame.setFixedWidth(320)
        layout = QVBoxLayout(frame)
        layout.setSpacing(15)
        layout.setContentsMargins(15, 20, 15, 20)

        algo_header = QHBoxLayout()
        algo_title = QLabel("RUNTIME TUNING")
        algo_title.setStyleSheet("color: #888; font-weight: bold; letter-spacing: 1px; font-size: 11px;")
        algo_header.addWidget(algo_title)
        algo_header.addStretch()
        layout.addLayout(algo_header)

        self.hsv_cont, _, _ = self.create_volume_with_entry("HSV SENSITIVITY", "#00e5ff", self.thresholds["HSV"],
                                                            layout, "HSV")
        self.chrome_cont, _, _ = self.create_volume_with_entry("CHROME RATIO", "#ff4081", self.thresholds["CHROME"],
                                                               layout, "CHROME")
        self.size_cont, _, _ = self.create_volume_with_entry("SIZE (PIXELS)", "#ffea00", self.thresholds["SIZE"],
                                                             layout, "SIZE")
        self.dl_cont, _, _ = self.create_volume_with_entry("AI THRESHOLD", "#7c4dff", self.thresholds["DEEP_LEARNING"],
                                                           layout, "DEEP_LEARNING")
        self.ai_purity_cont, _, _ = self.create_volume_with_entry("AI PURITY", "#00bcd4", self.thresholds["AI_PURITY"],
                                                                  layout, "AI_PURITY")

        # GRAYSCALE CONTROLS
        self.gray_a_cont, self.gray_a_bar, self.gray_a_spin = self.create_volume_with_entry("GRAY A: CONTRAST",
                                                                                            "#ffffff", self.thresholds[
                                                                                                "GRAY_CONTRAST"],
                                                                                            layout, "GRAY_CONTRAST")
        self.gray_b_cont, self.gray_b_bar, self.gray_b_spin = self.create_volume_with_entry("GRAY B: THRESHOLD",
                                                                                            "#b0bec5", self.thresholds[
                                                                                                "GRAY_THRESH"], layout,
                                                                                            "GRAY_THRESH")

        layout.addStretch()

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("background-color: #333;")
        layout.addWidget(line)
        
        self.btn_bg_graph = QPushButton("BACKGROUND GRAPH")
        self.btn_bg_graph.setFixedHeight(45)
        self.btn_bg_graph.setStyleSheet("background-color: #4CAF50; border: none; color: black; font-weight: bold;")
        self.btn_bg_graph.clicked.connect(self.open_background_graph)
        layout.addWidget(self.btn_bg_graph)

        self.btn_prog = QPushButton("PROGRAM SETTINGS")
        self.btn_prog.setFixedHeight(45)
        self.btn_prog.setStyleSheet("background-color: #0277bd; border: none; color: white; font-weight: bold;")
        self.btn_prog.clicked.connect(self.open_program_manager)
        layout.addWidget(self.btn_prog)

        return frame

    def open_background_graph(self):
        # We use .show() so it's non-modal and allows the user to interact with the sorter while viewing the graph!
        # Thus vibrators will remain active if the system is ON.
        self.bg_graph_dlg = BackgroundGraphDialog(self.camera_worker_front, self.camera_worker_rear, self)
        self.bg_graph_dlg.show()

    def create_volume_with_entry(self, label_text, color, initial_val, parent_layout, param_key):
        container = QWidget()
        v_layout = QVBoxLayout(container)
        v_layout.setContentsMargins(0, 0, 0, 0)
        v_layout.setSpacing(5)

        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold;")
        v_layout.addWidget(lbl)

        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)

        bar = QProgressBar()
        bar.setRange(0, 255)
        bar.setValue(initial_val)
        bar.setFixedHeight(12)
        bar.setTextVisible(False)
        bar.setStyleSheet(
            f"QProgressBar {{ border: none; background-color: #222; border-radius: 6px; }} QProgressBar::chunk {{ background-color: {color}; border-radius: 6px; }}")

        spin = QSpinBox()
        spin.setRange(0, 255)
        spin.setValue(initial_val)
        spin.setFixedSize(80, 35)
        spin.setStyleSheet(
            f"QSpinBox {{ background-color: #0f0f0f; border: 1px solid {color}; border-radius: 4px; color: white; font-weight: bold; padding: 2px; font-size: 15px; }}")

        from PyQt5.QtWidgets import QLineEdit
        line_edit = spin.findChild(QLineEdit)
        if line_edit:
            KeypadEventFilter(line_edit, lambda s=spin: Keypad(
                s.value(),
                lambda val: s.setValue(int(float(val)) if val.replace('.','',1).isdigit() else s.value()),
                container
            ).exec_())

        spin.valueChanged.connect(bar.setValue)
        spin.valueChanged.connect(lambda v: self.update_runtime_param(param_key, v))

        row_layout.addWidget(bar)
        row_layout.addWidget(spin)
        v_layout.addWidget(row_widget)
        parent_layout.addWidget(container)

        return container, bar, spin

    def update_runtime_param(self, key, value):
        self.thresholds[key] = value
        if self.h_camera:
            try:
                if key == "GRAY_CONTRAST":
                    gain_val = 1 + (value / 8.0)
                    mvsdk.CameraSetAnalogGain(self.h_camera, gain_val)
            except:
                pass

    def create_right_vibrator_panel(self):
        # Main right column container (no SidePanel border itself)
        frame = QFrame()
        frame.setFixedWidth(280)
        layout = QVBoxLayout(frame)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # --- FEEDER CARD ---
        feeder_card = QFrame()
        feeder_card.setObjectName("SidePanel")
        feeder_layout = QVBoxLayout(feeder_card)
        feeder_layout.setSpacing(10)
        feeder_layout.setContentsMargins(15, 10, 15, 10)

        vib_header = QHBoxLayout()
        vib_title = QLabel("FEEDER CONTROL")
        vib_title.setStyleSheet("color: #888; font-weight: bold; letter-spacing: 1px; font-size: 12px;")
        btn_vib_settings = QPushButton("🔧")
        btn_vib_settings.setFixedSize(30, 20)
        btn_vib_settings.setStyleSheet("background: transparent; color: #00bcd4; border: 1px solid #333;")
        btn_vib_settings.clicked.connect(self.open_vibrator_settings)
        vib_header.addWidget(vib_title)
        vib_header.addStretch()
        vib_header.addWidget(btn_vib_settings)
        feeder_layout.addLayout(vib_header)

        # Sync master dial from feeder_data[0]
        self.dial = GlossyKnob(value=self.feeder_data[0]['value'])
        self.dial.setFixedSize(140, 140)
        self.dial.valueChanged.connect(self.on_master_feeder_val_changed)
        self.dial.clicked.connect(self.open_feeder_keypad)
        dial_layout = QHBoxLayout()
        dial_layout.addStretch()
        dial_layout.addWidget(self.dial)
        dial_layout.addStretch()
        feeder_layout.addLayout(dial_layout)

        # --- OLD STYLE ROW: (-)  [ ON/OFF ]  (+) ---
        adjust_layout = QHBoxLayout()
        adjust_layout.setSpacing(10)
        adjust_layout.setContentsMargins(6, 4, 6, 4)

        # Minus button on the left: Round (-)
        btn_minus = QPushButton("－")
        btn_minus.setFixedSize(44, 44)
        btn_minus.setCursor(Qt.PointingHandCursor)
        btn_minus.setAutoRepeat(True)
        btn_minus.setAutoRepeatDelay(350)
        btn_minus.setAutoRepeatInterval(70)
        btn_minus.setToolTip("Decrease Feeder (-1)")
        btn_minus.setStyleSheet("""
            QPushButton {
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.7, fx:0.4, fy:0.4, stop:0 #25220c, stop:1 #121008);
                color: #ffeb3b;
                font-size: 24px;
                font-weight: bold;
                border: 2px solid #ff1744;
                border-radius: 22px;
                padding: 0px;
                padding-bottom: 3px;
            }
            QPushButton:hover {
                background: #ff1744;
                color: #ffffff;
                border-color: #ff8a80;
            }
            QPushButton:pressed {
                background: #b71c1c;
                color: #ffffff;
                border-color: #d50000;
            }
        """)
        btn_minus.clicked.connect(lambda: self.step_feeder(-1))

        # Center [ ON/OFF ] Button (toggles Feeder ON/OFF)
        self.btn_feeder_val = QPushButton("ON/OFF")
        self.btn_feeder_val.setFixedHeight(44)
        self.btn_feeder_val.setCursor(Qt.PointingHandCursor)
        self.btn_feeder_val.setToolTip("Turn Feeder ON / OFF (Click center dial '50' for Keypad)")
        self.btn_feeder_val.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0f1c24, stop:1 #081116);
                color: #00e5ff;
                font-size: 14px;
                font-weight: bold;
                border: 2px solid #00bcd4;
                border-radius: 22px;
                padding: 0 16px;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background: #00bcd4;
                color: #000000;
                border-color: #80deea;
            }
            QPushButton:pressed {
                background: #00838f;
                color: #ffffff;
                border-color: #0097a7;
            }
        """)
        self.btn_feeder_val.clicked.connect(self.toggle_feeder_from_onoff_btn)
        self.feeder_toggle = self.btn_feeder_val

        # Plus button on the right: Round (+)
        btn_plus = QPushButton("＋")
        btn_plus.setFixedSize(44, 44)
        btn_plus.setCursor(Qt.PointingHandCursor)
        btn_plus.setAutoRepeat(True)
        btn_plus.setAutoRepeatDelay(350)
        btn_plus.setAutoRepeatInterval(70)
        btn_plus.setToolTip("Increase Feeder (+1)")
        btn_plus.setStyleSheet("""
            QPushButton {
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.7, fx:0.4, fy:0.4, stop:0 #25220c, stop:1 #121008);
                color: #ffeb3b;
                font-size: 24px;
                font-weight: bold;
                border: 2px solid #00e676;
                border-radius: 22px;
                padding: 0px;
                padding-bottom: 3px;
            }
            QPushButton:hover {
                background: #00e676;
                color: #0a1c10;
                border-color: #b9f6ca;
            }
            QPushButton:pressed {
                background: #007e33;
                color: #ffffff;
                border-color: #00c853;
            }
        """)
        btn_plus.clicked.connect(lambda: self.step_feeder(1))

        adjust_layout.addWidget(btn_minus)
        adjust_layout.addWidget(self.btn_feeder_val, 1)
        adjust_layout.addWidget(btn_plus)
        feeder_layout.addLayout(adjust_layout)
        
        layout.addWidget(feeder_card)

        # Factory Settings & Deep Learning Buttons (middle)
        self.btn_fact = QPushButton("FACTORY SETTINGS")
        self.btn_fact.setFixedHeight(35)
        self.btn_fact.setStyleSheet("""
            QPushButton {
                background-color: #37474f; border: 1px solid #546e7a; color: #eceff1;
                font-weight: bold; font-size: 12px; border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #455a64; border: 1px solid #78909c; color: #ffffff;
            }
        """)
        self.btn_fact.clicked.connect(self.open_factory_settings)
        layout.addWidget(self.btn_fact)

        self.btn_dl = QPushButton("DEEP LEARNING")
        self.btn_dl.setFixedHeight(35)
        self.btn_dl.setStyleSheet("""
            QPushButton {
                background-color: #0d47a1; border: 1px solid #1976d2; color: #ffffff;
                font-weight: bold; font-size: 12px; border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #1565c0; border: 1px solid #42a5f5; color: #ffffff;
            }
        """)
        self.btn_dl.clicked.connect(self.open_deep_learning_setup)
        layout.addWidget(self.btn_dl)

        layout.addSpacing(5)

        # --- SYSTEM LIVE STATUS CARD ---
        status_card = QFrame()
        status_card.setObjectName("SidePanel")
        status_card_layout = QVBoxLayout(status_card)
        status_card_layout.setContentsMargins(10, 10, 10, 10)
        status_card_layout.setSpacing(5)

        status_box = QGroupBox("SYSTEM LIVE STATUS")
        status_box.setStyleSheet("""
            QGroupBox {
                border: none;
                font-weight: bold;
                color: #00bcd4;
                font-size: 12px;
                background-color: transparent;
            }
        """)
        
        from PyQt5.QtWidgets import QFormLayout
        status_layout = QFormLayout(status_box)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(6)
        
        # Labels for live status values
        self.lbl_status_esp = QLabel("OFFLINE")
        self.lbl_status_esp.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 11px;")
        
        self.lbl_status_cam_front = QLabel("DISCONNECTED")
        self.lbl_status_cam_front.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 11px;")
        
        self.lbl_status_cam_rear = QLabel("DISCONNECTED")
        self.lbl_status_cam_rear.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 11px;")
        
        self.lbl_status_feeder = QLabel("OFF")
        self.lbl_status_feeder.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 11px;")
        
        self.lbl_status_ejector = QLabel("OFFLINE")
        self.lbl_status_ejector.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 11px;")
        
        self.lbl_status_wiper = QLabel("INACTIVE")
        self.lbl_status_wiper.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 11px;")
        
        # Helper to style and add form layout rows
        def add_status_row(label_text, value_label):
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #888; font-weight: bold; font-size: 10px;")
            status_layout.addRow(lbl, value_label)
            
        add_status_row("COMM BOARD (ESP32):", self.lbl_status_esp)
        add_status_row("FRONT CAMERA:", self.lbl_status_cam_front)
        add_status_row("BACK CAMERA:", self.lbl_status_cam_rear)
        add_status_row("FEEDER STATUS:", self.lbl_status_feeder)
        add_status_row("EJECTOR STATUS:", self.lbl_status_ejector)
        add_status_row("WIPER STATUS:", self.lbl_status_wiper)
        
        status_card_layout.addWidget(status_box)
        layout.addWidget(status_card)

        self.update_feeder_toggle_appearance()

        layout.addStretch()
        return frame

    def create_long_program_bar(self):
        frame = QFrame()
        frame.setObjectName("ControlPanel")
        frame.setFixedHeight(85)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(15)

        lbl = QLabel("CURRENT PROFILE:")
        lbl.setStyleSheet("color: #ffd700; font-weight: bold; font-size: 15px;")
        layout.addWidget(lbl)

        self.lbl_current_prog_bar = QLabel(self.current_program)
        self.lbl_current_prog_bar.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
        layout.addWidget(self.lbl_current_prog_bar)

        layout.addStretch()

        self.btn_open_lib = QPushButton("PROGRAM LIBRARY")
        self.btn_open_lib.setFixedSize(220, 55)
        self.btn_open_lib.setStyleSheet(
            "background-color: #222; color: #ffd700; border: 2px solid #ffd700; font-size: 16px; font-weight: bold;")
        self.btn_open_lib.clicked.connect(self.open_program_library)
        layout.addWidget(self.btn_open_lib)
        return frame

    def apply_scheme(self, scheme_name):
        if not scheme_name:
            return
        self.current_program = scheme_name
        RecipeManager.set_active_scheme_name(scheme_name)
        if hasattr(self, 'lbl_current_prog_bar'):
            self.lbl_current_prog_bar.setText(scheme_name)

        scheme = RecipeManager.get_scheme(scheme_name)
        if not scheme or not isinstance(scheme, dict):
            return

        # 1. Apply Lighting for this scheme
        scheme_lighting = scheme.get("lighting", {})
        if scheme_lighting:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LIGHT_CONFIG_FILE)
            try:
                with open(cfg_path, "w") as f:
                    json.dump(scheme_lighting, f, indent=4)
            except Exception:
                pass
            self.apply_saved_lighting()

        # 2. Apply Ejector Timings
        ej = scheme.get("ejector", {"pulse": 20, "delay": 100})
        pulse = ej.get("pulse", 20)
        delay = ej.get("delay", 100)
        try:
            with open(os.path.join(os.path.dirname(__file__), "ejector_config.json"), "w") as f:
                json.dump({"pulse": pulse, "delay": delay}, f, indent=4)
            self._ejector_config_cache = None  # Invalidate cache so new values take effect
        except Exception:
            pass

        # 3. Apply Feeder Speed
        feeder_spd = scheme.get("feeder_speed", 50)
        self.feeder_data[0]['value'] = feeder_spd
        if hasattr(self, 'dial'):
            self.dial.blockSignals(True)
            self.dial.setValue(feeder_spd)
            self.dial.blockSignals(False)
        if self.feeder_data[0].get('active', False):
            self.on_master_feeder_val_changed(feeder_spd)

        # 4. Apply AI Model & Confidence
        ai_cfg = scheme.get("ai_model", {})
        if ai_cfg:
            self.thresholds["AI_MODEL_PATH"] = ai_cfg.get("model_path", "")
            self.thresholds["AI_ENABLED"] = ai_cfg.get("enabled", False)
            self.thresholds["DEEP_LEARNING"] = ai_cfg.get("confidence", 95)
            try:
                sys_cfg = {}
                if os.path.exists("system_config.json"):
                    with open("system_config.json", "r") as f:
                        sys_cfg = json.load(f)
                sys_cfg["ai_model_path"] = ai_cfg.get("model_path", "")
                sys_cfg["ai_enabled"] = ai_cfg.get("enabled", False)
                sys_cfg["deep_learning_confidence"] = ai_cfg.get("confidence", 95)
                with open("system_config.json", "w") as f:
                    json.dump(sys_cfg, f, indent=4)
            except Exception:
                pass

        # 5. Apply Thresholds & Side Panel Visualization
        if "thresholds" in scheme and isinstance(scheme["thresholds"], dict):
            for k, v in scheme["thresholds"].items():
                self.thresholds[k] = v
        self.update_side_panel_visualization(scheme_name)

    def open_program_library(self):
        recipes_data = RecipeManager.load_all()
        dlg = ProgramLibraryDialog(recipes_data.get("schemes", {}), self.current_program, self.current_user, self)
        if dlg.exec_() == QDialog.Accepted:
            selected = dlg.selected_program
            recipes_data["schemes"] = dlg.program_data
            recipes_data["active_scheme"] = selected
            RecipeManager.save_all(recipes_data)
            self.program_data = recipes_data["schemes"]
            self.apply_scheme(selected)

    def open_program_manager(self):
        recipes_data = RecipeManager.load_all()
        dlg = ProgramManagerDialog(recipes_data.get("schemes", {}), self.current_program, self.current_user, self)
        if dlg.exec_() == QDialog.Accepted:
            selected = dlg.selected_program
            recipes_data["schemes"] = dlg.program_data
            recipes_data["active_scheme"] = selected
            RecipeManager.save_all(recipes_data)
            self.program_data = recipes_data["schemes"]
            self.apply_scheme(selected)

    def update_side_panel_visualization(self, program_name):
        # 1. டேட்டாவை எடுக்கிறோம்
        prog_data = self.program_data.get(program_name, {"FRONT": [], "REAR": []})

        # 2. FRONT மற்றும் REAR இரண்டிலும் உள்ள அல்காரிதம்களை ஒன்றாக இணைக்கிறோம்
        schemes = []
        if isinstance(prog_data, dict):
            if "algorithms" in prog_data:
                algo_dict = prog_data.get("algorithms", {})
                schemes.extend(algo_dict.get("FRONT", []))
                schemes.extend(algo_dict.get("REAR", []))
            else:
                schemes.extend(prog_data.get("FRONT", []))
                schemes.extend(prog_data.get("REAR", []))
        else:
            schemes = prog_data  # பழைய டேட்டாவாக இருந்தால்

        # 3. இப்போது UI-ஐ அப்டேட் செய்கிறோம் (எந்த கேமராவில் இருந்தாலும் பட்டன் காட்டும்)
        self.hsv_cont.setVisible(any("HSV" in s for s in schemes))
        self.chrome_cont.setVisible(any("DISCOLOUR" in s for s in schemes))
        self.size_cont.setVisible(any("SIZE" in s for s in schemes))

        ai_active = any("AI" in s or "DEEP" in s or "DL" in s for s in schemes)
        self.dl_cont.setVisible(ai_active)
        self.ai_purity_cont.setVisible(ai_active)

        self.gray_a_cont.setVisible(any("GRAY A" in s for s in schemes))
        self.gray_b_cont.setVisible(any("GRAY B" in s for s in schemes))

        # 4. விண்டோவை Refresh செய்கிறோம்
        if hasattr(self, 'left_panel'):
            self.left_panel.update()
        self.update()

    def open_vibrator_settings(self):
        dlg = VibratorConfigDialog(self.current_user, self.feeder_data, self)
        dlg.exec_()

    def open_factory_settings(self):
        dlg = FactorySetupDialog(self.camera_worker_front, self.camera_worker_rear, self)
        dlg.exec_()
        
        # Reload Logo and Background configurations immediately
        self.update_branding()
        
        if LicenseManager.is_license_valid():
            self.auto_connect_cameras()
        else:
            self.enforce_license_lockdown()

    def run_wiper_cycle(self):
        if hasattr(self, 'esp32') and self.esp32 and not getattr(self.esp32, 'is_modbus_connected', lambda: False)():
            port = getattr(self.esp32, 'active_port', None) or "COM3"
            self.esp32.connect(port)

        if not hasattr(self, 'esp32') or not self.esp32.connected:
            QMessageBox.warning(self, "Wiper Error", "ESP32 Relay Board is not connected!")
            return

        self.execute_auto_wiper_sequence(force=True)

    def open_wiper_settings(self):
        dlg = WiperConfigDialog(self.esp32, self)
        dlg.exec_()

    def toggle_feeder_from_onoff_btn(self):
        new_state = not self.feeder_data[0].get('active', False)
        self.toggle_master_feeder(new_state)

    def update_feeder_toggle_appearance(self):
        active = self.feeder_data[0].get('active', False)
        try:
            target_btn = getattr(self, 'btn_feeder_val', None) or getattr(self, 'feeder_toggle', None)
        except (RuntimeError, AttributeError):
            target_btn = None
        if target_btn:
            if active:
                target_btn.setText("ON/OFF")
                target_btn.setStyleSheet("""
                    QPushButton {
                        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #00e676, stop:1 #00c853);
                        color: #000000;
                        font-size: 14px;
                        font-weight: 900;
                        border: 2px solid #b9f6ca;
                        border-radius: 22px;
                        padding: 0 16px;
                        letter-spacing: 1px;
                    }
                    QPushButton:hover {
                        background: #69f0ae;
                        color: #000000;
                        border-color: #ffffff;
                    }
                    QPushButton:pressed {
                        background: #007e33;
                        color: #ffffff;
                        border-color: #00c853;
                    }
                """)
            else:
                target_btn.setText("ON/OFF")
                target_btn.setStyleSheet("""
                    QPushButton {
                        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0f1c24, stop:1 #081116);
                        color: #00e5ff;
                        font-size: 14px;
                        font-weight: bold;
                        border: 2px solid #00bcd4;
                        border-radius: 22px;
                        padding: 0 16px;
                        letter-spacing: 1px;
                    }
                    QPushButton:hover {
                        background: #00bcd4;
                        color: #000000;
                        border-color: #80deea;
                    }
                    QPushButton:pressed {
                        background: #00838f;
                        color: #ffffff;
                        border-color: #0097a7;
                    }
                """)

    def set_feeder_intensity(self, val):
        val = max(0, min(int(val), 100))
        self.feeder_data[0]['value'] = val
        if hasattr(self, 'dial'):
            self.dial.blockSignals(True)
            self.dial.setValue(val)
            self.dial.blockSignals(False)
        if hasattr(self, 'current_program') and self.current_program:
            RecipeManager.update_scheme_field(self.current_program, "feeder_speed", val)
            
            from PyQt5.QtWidgets import QApplication
            for w in QApplication.topLevelWidgets():
                if hasattr(w, 'program_data') and isinstance(getattr(w, 'program_data', None), dict):
                    if self.current_program in w.program_data:
                        w.program_data[self.current_program]["feeder_speed"] = val
                        
        if self.feeder_data[0]['active']:
            pwm = int(val * 2.55)
            if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
                self.esp32.set_relay(1, 1 if val > 0 else 0)
                self.esp32.set_vibrator(1, pwm)

    def step_feeder(self, delta):
        cur = self.feeder_data[0].get('value', 50)
        self.set_feeder_intensity(cur + delta)

    def open_feeder_keypad(self):
        cur_val = str(self.feeder_data[0].get('value', 50))
        def callback(v):
            if v and v.replace('.', '', 1).isdigit():
                val_int = max(0, min(100, int(float(v))))
                self.set_feeder_intensity(val_int)
        Keypad(cur_val, callback, self).exec_()

    def on_master_feeder_val_changed(self, val):
        self.feeder_data[0]['value'] = val
        if hasattr(self, 'current_program') and self.current_program:
            RecipeManager.update_scheme_field(self.current_program, "feeder_speed", val)
            
            from PyQt5.QtWidgets import QApplication
            for w in QApplication.topLevelWidgets():
                if hasattr(w, 'program_data') and isinstance(getattr(w, 'program_data', None), dict):
                    if self.current_program in w.program_data:
                        w.program_data[self.current_program]["feeder_speed"] = val
                        
        if self.feeder_data[0]['active']:
            pwm = int(val * 2.55)
            if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
                self.esp32.set_relay(1, 1 if val > 0 else 0)
                self.esp32.set_vibrator(1, pwm)

    def toggle_master_feeder(self, checked):
        if hasattr(self, 'esp32') and self.esp32 and not getattr(self.esp32, 'is_modbus_connected', lambda: False)():
            port = getattr(self.esp32, 'active_port', None) or "COM13"
            self.esp32.connect(port)

        self.feeder_data[0]['active'] = checked
        self.update_feeder_toggle_appearance()
        if checked:
            # Removed Ejector ON at startup (Relay 2 is now defect-only)
            
            # Sequence: Ejector ON first, then Vibrator
            if hasattr(self, 'esp32') and self.esp32.connected:
                self.esp32.set_relay(2, 1) # Ejector ON
                print("[SEQ] Ejector ON (Feeder button clicked)")
            QTimer.singleShot(1000, self._delayed_feeder_vibrator_on)
        else:
            if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
                self.esp32.set_relay(1, 0)
                self.esp32.set_vibrator(1, 0)
                self.esp32.set_relay(2, 0) # Also turn off Ejector if Feeder is stopped
        self.update_live_status_panel()

    def _delayed_feeder_vibrator_on(self):
        if self.feeder_data[0]['active']:
            dial_val = self.feeder_data[0].get('value', 50)
            if hasattr(self, 'dial') and self.dial.get_value() > 0:
                dial_val = self.dial.get_value()
            if dial_val <= 0:
                dial_val = 50
                self.set_feeder_intensity(50)
            pwm = int(dial_val * 2.55)
            if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
                self.esp32.set_relay(1, 1)
                self.esp32.set_vibrator(1, pwm)
                print("[FEEDER] 1 Second Completed: Vibrator (Relay 1) turned ON.")

    def update_live_status_panel(self):
        # 1. ESP32 BOARD
        if self.esp32 and self.esp32.connected:
            port = getattr(self.esp32, 'active_port', None) or "COM23"
            self.lbl_status_esp.setText(f"ONLINE ({port})")
            self.lbl_status_esp.setStyleSheet("color: #00e676; font-weight: bold; font-size: 11px;")
        else:
            self.lbl_status_esp.setText("OFFLINE")
            self.lbl_status_esp.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 11px;")

        # 2. FRONT CAMERA
        if self.camera_worker_front and self.camera_worker_front.camera_handle is not None:
            self.lbl_status_cam_front.setText("ONLINE")
            self.lbl_status_cam_front.setStyleSheet("color: #00e676; font-weight: bold; font-size: 11px;")
        else:
            self.lbl_status_cam_front.setText("OFFLINE")
            self.lbl_status_cam_front.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 11px;")

        # 3. BACK CAMERA
        if self.camera_worker_rear and self.camera_worker_rear.camera_handle is not None:
            self.lbl_status_cam_rear.setText("ONLINE")
            self.lbl_status_cam_rear.setStyleSheet("color: #00e676; font-weight: bold; font-size: 11px;")
        else:
            self.lbl_status_cam_rear.setText("OFFLINE")
            self.lbl_status_cam_rear.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 11px;")

        # 4. FEEDER STATUS
        if self.feeder_data and self.feeder_data[0]['active']:
            self.lbl_status_feeder.setText("RUNNING")
            self.lbl_status_feeder.setStyleSheet("color: #00e676; font-weight: bold; font-size: 11px;")
        else:
            self.lbl_status_feeder.setText("STOPPED")
            self.lbl_status_feeder.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 11px;")

        # 5. EJECTOR STATUS
        if self.esp32 and self.esp32.connected:
            r2_val = self.esp32.get_relay(2) if hasattr(self.esp32, 'get_relay') else 0
            if r2_val:
                self.lbl_status_ejector.setText("ARMED (ON)")
                self.lbl_status_ejector.setStyleSheet("color: #00e676; font-weight: bold; font-size: 11px;")
            else:
                self.lbl_status_ejector.setText("STANDBY (OFF)")
                self.lbl_status_ejector.setStyleSheet("color: #ffa726; font-weight: bold; font-size: 11px;")
        else:
            self.lbl_status_ejector.setText("OFFLINE")
            self.lbl_status_ejector.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 11px;")

        # 6. WIPER STATUS
        wiper_on = self.esp32.get_relay(3) if (self.esp32 and hasattr(self.esp32, 'get_relay')) else getattr(self.esp32, 'wiper_active', False)
        if self.esp32 and wiper_on:
            self.lbl_status_wiper.setText("RUNNING")
            self.lbl_status_wiper.setStyleSheet("color: #00e676; font-weight: bold; font-size: 11px;")
        else:
            self.lbl_status_wiper.setText("STANDBY")
            self.lbl_status_wiper.setStyleSheet("color: #aaa; font-weight: bold; font-size: 11px;")

    def open_camera_window(self):
        if not LicenseManager.is_license_valid():
            QMessageBox.critical(self, "License Error", "Cannot open Dataset Tool. License is invalid or expired!")
            return
        dlg = DatasetCreatorUI(self.camera_worker_front, self.camera_worker_rear, self.current_program, self)
        dlg.exec_()

    def open_camera_setup(self):
        if self.current_user != "MANUFACTURER":
            QMessageBox.warning(self, "Access Denied", "Camera Setup is restricted to MANUFACTURER role only.")
            return
        if not LicenseManager.is_license_valid():
            QMessageBox.critical(self, "License Error", "Cannot open Camera Setup. License is invalid or expired!")
            return
        dlg = CameraSetupDialog(self.camera_worker_front, self.camera_worker_rear, self)
        dlg.exec_()

    def open_deep_learning_setup(self):
        dlg = DeepLearningSetupDialog(self, self)
        dlg.exec_()

    def show_desktop(self):
        # Directly launch Keypad for password verification
        entered_val = []
        def on_enter_callback(val):
            entered_val.append(val)

        from PyQt5.QtWidgets import QDialog
        kp = Keypad("", on_enter_callback, self, is_password=True)
        kp.setWindowTitle("Desktop Authorization")
        
        if kp.exec_() != QDialog.Accepted or not entered_val or entered_val[0] != "11*99":
            QMessageBox.critical(self, "Authorization Failed", "Incorrect password!")
            return

        # First, minimize the HMI window itself completely to the taskbar
        self.showMinimized()
        
        # Then, minimize other desktop applications to expose the desktop
        import os
        try:
            os.system('powershell -command "(New-Object -ComObject shell.application).minimizeall()"')
        except Exception as e:
            print(f"Error minimizing: {e}")

    def setup_footer(self):
        footer = QWidget()
        footer.setFixedHeight(150)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(20, 5, 20, 15)
        footer_layout.setSpacing(8)
        
        # Upper LED Bar for live ejector statuses
        
        # Lower Buttons Layout
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(15)

        btn_start = QPushButton("START SYSTEM")
        btn_start.setObjectName("BtnStart")
        btn_start.setFixedHeight(70)
        btn_start.setStyleSheet("""
            QPushButton#BtnStart {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #00e676, stop:1 #00b0ff);
                color: #000000; font-size: 22px; font-weight: 900;
                border: 3px solid #69f0ae; border-radius: 12px; letter-spacing: 2px;
            }
            QPushButton#BtnStart:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #69f0ae, stop:1 #18ffff);
                border: 3px solid #ffffff; color: #000000;
            }
            QPushButton#BtnStart:pressed {
                background-color: #004d40; border: 3px solid #00e676; color: #ffffff;
            }
        """)
        btn_start.clicked.connect(self.on_start_system)

        btn_desktop = QPushButton("DESKTOP")
        btn_desktop.setObjectName("BtnDesktop")
        btn_desktop.setProperty("class", "FooterBtn")
        btn_desktop.setFixedWidth(150)
        btn_desktop.clicked.connect(self.show_desktop)

        self.btn_wiper = QPushButton("CLEAN")
        self.btn_wiper.setProperty("class", "SmallFooterBtn")
        self.btn_wiper.setFixedWidth(120)
        self.btn_wiper.clicked.connect(self.open_wiper_settings)

        self.btn_cam = QPushButton("CAMERA SETUP")
        self.btn_cam.setProperty("class", "SmallFooterBtn")
        self.btn_cam.setFixedWidth(140)
        self.btn_cam.setStyleSheet(
            "background-color: #333; color: #00bcd4; border: 1px solid #00bcd4; font-weight: bold;")
        self.btn_cam.clicked.connect(self.open_camera_setup)

        self.btn_alarm_test = QPushButton("SIMULATE ALARM")
        self.btn_alarm_test.setStyleSheet("background-color: #333; color: orange; border: 1px dashed orange;")
        self.btn_alarm_test.clicked.connect(self.toggle_simulation_alarm)

        svg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lambton').replace('\\', '/')
        btn_exit = QPushButton()
        btn_exit.setObjectName("BtnShutdown")
        btn_exit.setFixedSize(70, 70)
        btn_exit.setStyleSheet(f"""
            QPushButton#BtnShutdown {{
                background-color: transparent;
                border: none;
                border-image: url('{svg_dir}/power_button.svg') 0 0 0 0 stretch stretch;
            }}
            QPushButton#BtnShutdown:hover {{
                border: 2px solid #ff5252;
                border-radius: 35px;
            }}
            QPushButton#BtnShutdown:pressed {{
                background-color: rgba(255, 23, 68, 60);
                border: 2px solid white;
                border-radius: 35px;
            }}
        """)
        btn_exit.clicked.connect(self.system_shutdown)

        btn_layout.addWidget(btn_start, stretch=2)
        btn_layout.addWidget(btn_desktop)
        btn_layout.addWidget(self.btn_wiper)
        btn_layout.addWidget(self.btn_cam)
        btn_layout.addWidget(self.btn_alarm_test)
        btn_layout.addSpacing(20)
        btn_layout.addWidget(btn_exit)
        
        footer_layout.addLayout(btn_layout)
        self.main_layout.addWidget(footer)

    def show_info(self):
        dlg = LicenseDialog(self)
        dlg.exec_()

    def save_system(self):
        QMessageBox.information(self, "Save", "System Configuration Saved Successfully.")

    def go_home(self):
        QMessageBox.information(self, "Home", "Dashboard View Refreshed.")

    def system_shutdown(self):
        if QMessageBox.question(self, "Power Off", "SHUT DOWN system?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            import os
            os.system("shutdown /s /t 1")
            self.close()

    def toggle_simulation_alarm(self):
        self.alarm_active = not self.alarm_active
        self.lbl_alarm.setVisible(self.alarm_active)

    def toggle_alarm_vis(self):
        if self.alarm_active:
            self.alarm_blink_state = not self.alarm_blink_state
            self.lbl_alarm.setVisible(self.alarm_blink_state)
            if self.alarm_blink_state:
                self.lbl_alarm.setStyleSheet(
                    "background-color: #ff1744; color: white; font-weight: bold; font-size: 18px; border-radius: 4px; padding: 5px;")
            else:
                self.lbl_alarm.setStyleSheet(
                    "background-color: black; color: #ff1744; font-weight: bold; font-size: 18px; border-radius: 4px; padding: 5px;")
        else:
            self.lbl_alarm.setVisible(False)

            
    def update_time(self):
        current = QDateTime.currentDateTime()
        self.clock_lbl.setText(current.toString("HH:mm:ss"))
        self.date_lbl.setText(current.toString("dd/MM/yyyy"))
        
        # Periodically retry auto-connecting cameras every 5 seconds
        if not hasattr(self, '_cam_reconnect_tick'):
            self._cam_reconnect_tick = 0
        self._cam_reconnect_tick += 1
        if self._cam_reconnect_tick >= 5:
            self._cam_reconnect_tick = 0
            self.auto_connect_cameras()
        
        # 1. Update status indicators
        self.update_live_status_panel()

        # 2. Update license warning if expiring in last 5 days
        remaining = LicenseManager.get_license_days_remaining()
        if 0 <= remaining <= 5:
            self.lbl_license_warn.setText(f"⚠ LICENSE EXPIRES IN {remaining} DAYS! ⚠")
            self.lbl_license_warn.setVisible(True)
        else:
            self.lbl_license_warn.setVisible(False)

    def open_login_window(self):
        dlg = LoginDialog(self)
        if dlg.exec_() == QDialog.Accepted: self.change_user_role(dlg.authenticated_role)

    def show_software_info(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Software Information")
        dlg.setFixedSize(500, 360)
        dlg.setStyleSheet("""
            QDialog {
                background-color: #1a1a1a;
                border: 2px solid #00bcd4;
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-family: 'Segoe UI';
            }
        """)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(25, 20, 25, 20)
        layout.setSpacing(10)

        title = QLabel("LAMTON Kunstwerk AI Sorting System")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #00bcd4;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        version = QLabel("Version: 3.0 (Line Scan & AI)")
        version.setStyleSheet("font-size: 14px; color: #ffd700;")
        version.setAlignment(Qt.AlignCenter)
        layout.addWidget(version)

        # License summary box
        info = LicenseManager.get_license_info()
        status_col = "#00e676" if info["valid"] else "#ff5252"
        lbl_lic = QLabel(f"License: {info['status']} | Expiry: {info['expiry_date']}")
        lbl_lic.setStyleSheet(f"font-size: 13px; color: {status_col}; font-weight: bold; background: #222; padding: 6px; border-radius: 4px;")
        lbl_lic.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_lic)

        btn_lic = QPushButton("🔑 MANAGE / UPDATE LICENSE")
        btn_lic.setStyleSheet("background-color: #ff9800; color: black; font-weight: bold; padding: 8px; border-radius: 4px; font-size: 13px;")
        def _open_lic():
            dlg.accept()
            LicenseDialog(self).exec_()
        btn_lic.clicked.connect(_open_lic)
        layout.addWidget(btn_lic)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: #444; background-color: #444; max-height: 1px;")
        layout.addWidget(divider)

        contact = QLabel("Contact Details:\nEmail: support@lamton-kunstwerk.de\nPhone: +49 (0) 123 456 789")
        contact.setStyleSheet("font-size: 12px; color: #aaa; line-height: 1.4;")
        contact.setAlignment(Qt.AlignCenter)
        layout.addWidget(contact)

        btn_close = QPushButton("OK")
        btn_close.setFixedSize(100, 32)
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #00bcd4;
                color: black;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #00e5ff;
            }
        """)
        btn_close.clicked.connect(dlg.accept)
        layout.addWidget(btn_close, 0, Qt.AlignCenter)

        dlg.exec_()

    def change_user_role(self, role):
        self.current_user = role
        self.btn_user.setText(role)
        if hasattr(self, 'btn_ejector'):
            self.btn_ejector.setEnabled(role != "OPERATOR")
            self.btn_ejector.setText("EJECTORS" if role != "OPERATOR" else "EJECTORS 🔒")
        # Camera Setup is MANUFACTURER-only
        if hasattr(self, 'btn_cam'):
            self.btn_cam.setEnabled(role == "MANUFACTURER")
            self.btn_cam.setText("📷 CAMERA SETUP" if role == "MANUFACTURER" else "📷 CAMERA SETUP 🔒")
        col = "#00bcd4" if role == "ENGINEER" else "#ffd700" if role == "MANUFACTURER" else "#888"
        self.btn_user.setStyleSheet(f"border: 2px solid {col}; color: {col};")

    def show_ejector_window(self, scheme_name=None):
        target_scheme = scheme_name or getattr(self, 'current_program', RecipeManager.get_active_scheme_name())
        dlg = EjectorTestWindow(self.esp32, self, scheme_name=target_scheme)
        dlg.exec_()
        
    def update_branding(self):
        import os, json
        logo_path = LOGO_PATH
        bg_path = BG_PATH
        
        # Try loading defaults overwritten by user
        if os.path.exists(SYS_INFO_FILE):
             try:
                 with open(SYS_INFO_FILE, 'r') as f:
                     sys_data = json.load(f)
                 logo_path = sys_data.get("logo_path", LOGO_PATH)
                 bg_path = sys_data.get("bg_path", BG_PATH)
             except Exception:
                 pass
                 
        # Update background CSS securely to avoid recursive child inheritance loops
        if os.path.exists(bg_path):
             safe_bg_path = bg_path.replace('\\', '/')
             self.central_widget.setStyleSheet(
                 f"QWidget#CentralBG {{ border-image: url('{safe_bg_path}') 0 0 0 0 stretch stretch; }}\n"
                 f"QFrame#SidePanel {{ background-color: rgba(22, 22, 22, 0.95); }}"
             )
        else:
             self.central_widget.setStyleSheet(
                 "QWidget#CentralBG { background-color: #1a1a1a; }\n"
                 "QFrame#SidePanel { background-color: rgba(22, 22, 22, 0.95); }"
             )
            
        if hasattr(self, 'logo_label') and os.path.exists(logo_path):
             pix_logo = QPixmap(logo_path)
             if not pix_logo.isNull():
                 self.logo_label.setPixmap(pix_logo)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'welcome_overlay') and self.welcome_overlay and self.welcome_overlay.isVisible():
            self.welcome_overlay.setGeometry(self.rect())
