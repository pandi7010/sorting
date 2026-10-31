"""
capture_ui.py - CONTINUOUS CAPTURE window (Phase 4).

Shown only when features_config.json "continuous_capture_button": true (button in the footer).
Uses the frames the sorter's cameras already deliver (no camera settings are changed).

Typical use:
  1. Choose product, class label, camera(s), output folder.
  2. Feeder OFF -> press LEARN BACKGROUND (1 s of empty chute).
  3. Tick "Ejection OFF" (recommended), START CAPTURE, start the feeder with your product.
  4. Watch: objects counter goes up, DROPPED FRAMES must stay 0, no "DISK TOO SLOW" warning.
  5. STOP CAPTURE -> files are in the shown folder (crops/<CLASS>/ is ready for CNN training).
"""

import os
import time

import numpy as np
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QVBoxLayout)

from capture_engine import CNN_CASHEW_CLASSES, CaptureConfig, CaptureSession, load_presets


class ContinuousCaptureDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.mw = main_window
        self.setWindowTitle("Continuous Dataset Capture (Phase 4)")
        self.resize(760, 560)
        self.session = None
        self._connections = []
        self._bg_frames = {}
        self._learning_until = 0.0
        self.setStyleSheet("QWidget { font-size: 14px; } QPushButton { padding: 8px 14px; font-weight: bold; }")

        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.cmb_product = QComboBox()
        self.cmb_product.addItems(list(load_presets().get("products", {}).keys()) or ["CASHEWS"])
        self.cmb_product.setCurrentText("CASHEWS")
        form.addRow("Product preset:", self.cmb_product)
        self.cmb_class = QComboBox()
        self.cmb_class.setEditable(True)
        self.cmb_class.addItems(["UNLABELLED"] + CNN_CASHEW_CLASSES + ["GOOD", "BAD", "INSECT_HOLE", "BROKEN", "FOREIGN"])
        form.addRow("Class label (folder name):", self.cmb_class)
        self.cmb_cam = QComboBox()
        self.cmb_cam.addItems(["FRONT", "REAR", "BOTH"])
        form.addRow("Camera:", self.cmb_cam)
        out_row = QHBoxLayout()
        self.txt_out = QLineEdit(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_capture", "continuous"))
        btn_browse = QPushButton("...")
        btn_browse.clicked.connect(self.browse)
        out_row.addWidget(self.txt_out)
        out_row.addWidget(btn_browse)
        form.addRow("Output folder:", out_row)
        self.chk_strips = QCheckBox("Save full strip images (JPG) - needs a fast disk")
        self.chk_strips.setChecked(True)
        form.addRow(self.chk_strips)
        self.chk_raw = QCheckBox("Also save unscaled crops")
        form.addRow(self.chk_raw)
        self.chk_eject_off = QCheckBox("Ejection OFF while capturing (recommended)")
        self.chk_eject_off.setChecked(True)
        form.addRow(self.chk_eject_off)
        self.txt_speed = QLineEdit("3.5")
        form.addRow("Product speed m/s (for true shape):", self.txt_speed)
        lay.addLayout(form)

        row = QHBoxLayout()
        self.btn_bg = QPushButton("LEARN BACKGROUND (feeder OFF)")
        self.btn_bg.clicked.connect(self.learn_background)
        self.btn_start = QPushButton("START CAPTURE")
        self.btn_start.setStyleSheet("background-color: #00c853; color: black;")
        self.btn_start.clicked.connect(self.toggle_capture)
        row.addWidget(self.btn_bg)
        row.addWidget(self.btn_start)
        lay.addLayout(row)

        self.lbl_status = QLabel("Not capturing.")
        self.lbl_status.setStyleSheet("font-family: Consolas, monospace; font-size: 15px; padding: 8px;")
        self.lbl_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.lbl_status, stretch=1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(500)

    # ------------------------------------------------------------------ helpers
    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.txt_out.text())
        if d:
            self.txt_out.setText(d)

    def cams(self):
        c = self.cmb_cam.currentText()
        return ("FRONT", "REAR") if c == "BOTH" else (c,)

    def workers(self):
        return {"FRONT": self.mw.camera_worker_front, "REAR": self.mw.camera_worker_rear}

    def _connect(self, handler):
        """Receive frames in the CAMERA thread (DirectConnection): only a copy into a queue happens there."""
        from PyQt5.QtCore import Qt as _Qt
        for cam in self.cams():
            w = self.workers()[cam]
            fn = (lambda f, cam=cam, w=w: handler(cam, f, getattr(w, "last_meta", None)))
            w.frame_ready.connect(fn, _Qt.DirectConnection)
            self._connections.append((w, fn))

    def _disconnect(self):
        for w, fn in self._connections:
            try:
                w.frame_ready.disconnect(fn)
            except Exception:
                pass
        self._connections = []

    # ------------------------------------------------------------------ background
    def learn_background(self):
        self._bg_frames = {c: [] for c in self.cams()}
        self._learning_until = time.time() + 1.0
        self._disconnect()
        self._connect(self._collect_bg)
        self.lbl_status.setText("Learning background for 1 s - keep the feeder OFF ...")
        QTimer.singleShot(1200, self._bg_done)

    def _collect_bg(self, cam, frame, meta):
        if time.time() < self._learning_until and len(self._bg_frames.get(cam, [])) < 100:
            self._bg_frames[cam].append(frame.copy())

    def _bg_done(self):
        self._disconnect()
        got = {c: len(v) for c, v in self._bg_frames.items()}
        self.lbl_status.setText(f"Background frames: {got}. Ready to capture." if all(got.values())
                                else f"Background NOT learned (no frames: {got}). Are the cameras running?")

    # ------------------------------------------------------------------ capture
    def toggle_capture(self):
        if self.session:
            self.stop_capture()
        else:
            self.start_capture()

    def start_capture(self):
        product = self.cmb_product.currentText()
        cam_cfg = getattr(self.mw, "camera_configs_data", {}) or {}
        cams = self.cams()
        c0 = cam_cfg.get(cams[0], {})
        x_range = (int(c0.get("start_pixel", 0)), int(c0.get("end_pixel", 4096))) if c0 else None
        mm_per_line = 0.0
        try:
            speed = float(self.txt_speed.text())
            lr = self._line_rate()
            if lr:
                mm_per_line = speed * 1000.0 / lr
        except ValueError:
            pass
        cfg = CaptureConfig.from_preset(product, class_label=self.cmb_class.currentText().strip() or "UNLABELLED",
                                        cameras=cams, out_root=self.txt_out.text(), save_strips=self.chk_strips.isChecked(),
                                        save_raw=self.chk_raw.isChecked(), x_range=x_range, mm_per_line=mm_per_line)
        self.session = CaptureSession(cfg)
        self.session.start()
        for cam, frames in self._bg_frames.items():
            if cam in cams and frames:
                self.session.learn_background(cam, frames)
        if self.chk_eject_off.isChecked():
            self.mw.capture_ejection_off = True
        def push(cam, f, meta):
            if not self.session:
                return
            m = dict(meta or {})
            # The sorter keeps only the middle roi_height rows of each camera frame. If rows are cut away,
            # tell the engine the true line numbers so it never joins rows that are not neighbours.
            if m.get("height") and m.get("rows_used") and m["rows_used"] < m["height"]:
                m["first_line"] = int(m.get("frame_idx", 0)) * int(m["height"]) + int(m.get("crop_y0", 0))
            self.session.push_frame(cam, f, m)
        self._connect(push)
        self.btn_start.setText("STOP CAPTURE")
        self.btn_start.setStyleSheet("background-color: #d50000; color: white;")

    def _line_rate(self):
        """Line rate from the frame timestamps the camera workers report (no camera access)."""
        w = self.workers()[self.cams()[0]]
        m = getattr(w, "last_meta", None) or {}
        iv = getattr(self.mw, "_frame_interval_ms", {}).get(self.cams()[0])
        if m.get("height") and iv:
            return m["height"] / (iv / 1000.0)
        return None

    def stop_capture(self):
        self._disconnect()
        sess = self.session
        self.session = None
        self.mw.capture_ejection_off = False
        if sess:
            self.lbl_status.setText("Finishing: writing remaining files ...")
            c = sess.stop()
            self.lbl_status.setText(f"DONE. {sum(c.objects.values())} objects, dropped frames {c.dropped_frames}, "
                                    f"files {c.files_written}.\nFolder: {sess.dir}")
        self.btn_start.setText("START CAPTURE")
        self.btn_start.setStyleSheet("background-color: #00c853; color: black;")

    def update_status(self):
        if not self.session:
            return
        st = self.session.status()
        dropped = sum(st["dropped_frames"].values()) + sum(st["queue_overflow"].values())
        lines = [f"OBJECTS: {st['total_objects']}   {st['objects']}",
                 f"DROPPED FRAMES: {dropped}  (camera gaps {st['dropped_frames']}, PC queue full {st['queue_overflow']})",
                 f"frame queue {st['frame_queue']}   write backlog {st['write_backlog']}",
                 f"written {st['files']} files, {st['MB_written']} MB ({st['MB_per_s']} MB/s)   size-rejected {st['rejected_size']}"]
        lines += [f"!! {w}" for w in st["warnings"]]
        for cam in self.cams():
            m = getattr(self.workers()[cam], "last_meta", None) or {}
            if m.get("height") and m.get("rows_used") and m["rows_used"] < m["height"]:
                lines.append(f"!! {cam}: camera frame is {m['height']} lines but only {m['rows_used']} are used "
                             f"(camera_config roi_height). Lines in between are NOT captured - set roi_height "
                             f">= {m['height']} for complete strips.")
        self.lbl_status.setText("\n".join(lines))
        self.lbl_status.setStyleSheet("font-family: Consolas, monospace; font-size: 15px; padding: 8px; color: "
                                      + ("#ff5252" if dropped or st["warnings"] else "#00e676") + ";")

    def closeEvent(self, e):
        if self.session:
            self.stop_capture()
        self._disconnect()
        self.timer.stop()
        super().closeEvent(e)
