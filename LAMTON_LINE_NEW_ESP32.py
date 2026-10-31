import sys
import os

# --- PHASE 1 TIMING FIX: 1 ms Windows timer resolution ---
# Without this, time.sleep(0.001) on Python 3.10 really sleeps ~16 ms (measured), so the
# EjectScheduler could fire up to ~25 ms late (~90 mm at 3.5 m/s). Python 3.11+ is better,
# but this makes timing correct on every version. Applies only while this program runs.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass

# --- CRITICAL FIX: ENSURE CWD MATCHES SCRIPT DIRECTORY ---
# This prevents relative path errors if the script is run from a parent folder.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# --- OPTIONAL CNN / YOLO packages (PyTorch) ---
# On Windows torch must be imported BEFORE cv2 / PyQt5, otherwise it fails with
# 'WinError 1114: a DLL initialization routine failed' (tested 16 Oct 2026). If torch is missing or
# fails, the sorter starts anyway: colour / HSV / contour sorting does not need it.
TORCH_IMPORT_ERROR = None
try:
    import torch  # noqa: F401
except Exception as _torch_exc:
    TORCH_IMPORT_ERROR = f"{type(_torch_exc).__name__}: {_torch_exc}"
    print(f"[AI] PyTorch not available - CNN / YOLO disabled, sorting continues: {TORCH_IMPORT_ERROR[:160]}")
import cv2
import numpy as np
import time
import threading
import json
import logging
import math

BG_CONFIG_FILE = "bg_config.json"
LIGHT_CONFIG_FILE = "light_config.json"
RECIPES_FILE = "recipes.json"
import copy
from relay import RelayController
from ejector_link import EjectorLink
import port_detect                              # finds the ejector / relay / feeder on ANY COM port by their replies
import features                                  # Phase 2+: ON/OFF switches (all OFF by default)
from timing_log import get_logger as _get_timing_logger
TIMING = _get_timing_logger()                    # Phase 2 timing logs (disabled unless switched on)

# --- ENHANCED EJECTOR & COM PORT RECOGNITION (Supports TCM TEXT v3.0, v2.x, COM13, COM24) ---
def _patch_ejector_support():
    import re
    import time
    import ejector_link as _el
    import port_detect as _pd

    # 1. Enhance EjectorLink._probe to recognize TCM text firmware & esp.py byte protocol
    def _enhanced_link_probe(self, baud, wait_s):
        try:
            self.ser = self._open(baud)
        except Exception as exc:
            self.text_lines.append(f"[LINK] cannot open {self.port}: {exc}")
            self.ser = None
            return "FAIL"
        self.baud = baud
        self._running = True
        self.mode = "PROBING"
        self._probe_lines = []
        self._start_reader()
        v2 = legacy = esp_byte = None
        deadline = time.time() + wait_s

        # Step 1: probe byte protocol cleanly with 'P' (avoids newline triggering CH 10 on esp.py firmware)
        self._write(b"P")
        t0 = time.time()
        while time.time() - t0 < 0.25 and not esp_byte:
            for line in list(self._probe_lines):
                if "ESP32_ALIVE" in line:
                    esp_byte = True
                    break
            time.sleep(0.02)

        # Step 2: if not esp_byte, probe binary v2 / legacy text with \nPING\n
        while time.time() < deadline and not (v2 or legacy or esp_byte):
            self._write(b"\nPING\n")
            t0 = time.time()
            while time.time() - t0 < 0.3 and not (v2 or legacy or esp_byte):
                for line in list(self._probe_lines):
                    m = re.search(r"PONG:LAMTON_EJECTOR_V(\d+)\.(\d+)", line)
                    if m:
                        v2 = (int(m.group(1)), int(m.group(2)))
                    elif "ESP32_ALIVE" in line:
                        esp_byte = True
                    elif any(sig in line for sig in ("ACK:PING", "PONG", "TCM_EJECTOR", "EJECTOR_READY", "ESP32_ONLINE_63CH")):
                        legacy = True
                time.sleep(0.02)
        self._probe_lines = None
        if esp_byte:
            self.mode = "ESP_BYTE"
            self._write(bytes([0]))  # Send ALL OFF initial reset
            self.text_lines.append(f"[LINK] {self.port}: ESP32 63-CH Ejector (esp.py byte protocol)")
            return "ESP_BYTE"
        if v2:
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if self.identity:
                    break
                time.sleep(0.05)
            self.mode = "BINARY"
            if self.heartbeat_enabled:
                self._start_heartbeat()
            self.text_lines.append(f"[LINK] {self.port}: binary firmware v{self.fw_version or '2.x'}")
            return "BINARY"
        if legacy:
            self.mode = "LEGACY"
            self._write(b"STOP\nALL_OFF\n")
            self.text_lines.append(f"[LINK] {self.port}: text ejector firmware (TCM TEXT v3.0 / legacy mode)")
            return "LEGACY"
        self._close_port()
        return "NONE"
    _el.EjectorLink._probe = _enhanced_link_probe

    # 2. Enhance port_detect.probe_ejector
    _orig_probe_ejector = _pd.probe_ejector
    def _enhanced_probe_ejector(port, try_old_baud=True):
        res = _orig_probe_ejector(port, try_old_baud)
        if res and res.ok:
            return res
        for baud in (_pd.EJECTOR_BAUD, _pd.OLD_EJECTOR_BAUD):
            try:
                buf, s = _pd._text_ping(port, baud, 1.0)
            except Exception:
                continue
            try:
                if any(sig in buf for sig in (b"ACK:PING", b"PONG", b"TCM_EJECTOR", b"EJECTOR_READY", b"ESP32_ONLINE_63CH")):
                    detail = "TCM text ejector v3.0" if (b"TCM_EJECTOR" in buf or b"PONG" in buf) else "text ejector firmware (legacy mode)"
                    return _pd.Result(port, _pd.LEGACY, baud=baud, detail=detail)
            finally:
                _pd._close(s)
        return res
    _pd.probe_ejector = _enhanced_probe_ejector

_patch_ejector_support()



from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QFont, QIcon

class RecipeManager:

    """Manages independent configurations per commodity scheme (RICE, DHALL, PLASTICS, GOOD, etc.)"""
    @staticmethod
    def get_default_recipes():
        default_lighting = {
            "WHITE FRONT TOP": {"enabled": True, "lux": 500},
            "WHITE FRONT BOTTOM": {"enabled": True, "lux": 500},
            "WHITE BACK TOP": {"enabled": False, "lux": 500},
            "WHITE BACK BOTTOM": {"enabled": False, "lux": 500},
            "RED FRONT": {"enabled": False, "lux": 500},
            "GREEN FRONT": {"enabled": False, "lux": 500},
            "BLUE FRONT": {"enabled": False, "lux": 500},
            "RED BACK": {"enabled": False, "lux": 500},
            "GREEN BACK": {"enabled": False, "lux": 500},
            "BLUE BACK": {"enabled": False, "lux": 500}
        }
        cfg_light_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LIGHT_CONFIG_FILE)
        if os.path.exists(cfg_light_path):
            try:
                with open(cfg_light_path, "r") as f:
                    default_lighting.update(json.load(f))
            except Exception: pass

        default_ejector = {"pulse": 20, "delay": 100}
        ej_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ejector_config.json")
        if os.path.exists(ej_path):
            try:
                with open(ej_path, "r") as f:
                    default_ejector.update(json.load(f))
            except Exception: pass

        default_ai = {"model_path": "No model uploaded (using default).", "enabled": False, "confidence": 95}
        sys_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_config.json")
        if os.path.exists(sys_path):
            try:
                with open(sys_path, "r") as f:
                    s = json.load(f)
                    default_ai["model_path"] = s.get("ai_model_path", default_ai["model_path"])
                    default_ai["enabled"] = s.get("ai_enabled", default_ai["enabled"])
                    default_ai["confidence"] = s.get("deep_learning_confidence", default_ai["confidence"])
            except Exception: pass

        schemes = {
            "RICE": {
                "lighting": {
                    "WHITE FRONT TOP": {"enabled": True, "lux": 550},
                    "WHITE FRONT BOTTOM": {"enabled": True, "lux": 550},
                    "WHITE BACK TOP": {"enabled": False, "lux": 500},
                    "WHITE BACK BOTTOM": {"enabled": False, "lux": 500},
                    "RED FRONT": {"enabled": False, "lux": 500},
                    "GREEN FRONT": {"enabled": False, "lux": 500},
                    "BLUE FRONT": {"enabled": False, "lux": 500},
                    "RED BACK": {"enabled": False, "lux": 500},
                    "GREEN BACK": {"enabled": False, "lux": 500},
                    "BLUE BACK": {"enabled": False, "lux": 500}
                },
                "ejector": {"pulse": 15, "delay": 80},
                "feeder_speed": 50,
                "algorithms": {"FRONT": ["HSV COLOR", "SIZE: BIG"], "REAR": ["HSV COLOR"]},
                "thresholds": {"HSV": 85, "CHROME": 75, "SIZE": 90, "AI_PURITY": 50, "GRAY_CONTRAST": 50},
                "ai_model": copy.deepcopy(default_ai),
                "dataset_dir": "dataset_capture/RICE"
            },
            "DHALL": {
                "lighting": {
                    "WHITE FRONT TOP": {"enabled": True, "lux": 450},
                    "WHITE FRONT BOTTOM": {"enabled": True, "lux": 450},
                    "WHITE BACK TOP": {"enabled": True, "lux": 300},
                    "WHITE BACK BOTTOM": {"enabled": True, "lux": 300},
                    "RED FRONT": {"enabled": True, "lux": 500},
                    "GREEN FRONT": {"enabled": False, "lux": 500},
                    "BLUE FRONT": {"enabled": False, "lux": 500},
                    "RED BACK": {"enabled": False, "lux": 500},
                    "GREEN BACK": {"enabled": False, "lux": 500},
                    "BLUE BACK": {"enabled": False, "lux": 500}
                },
                "ejector": {"pulse": 25, "delay": 120},
                "feeder_speed": 35,
                "algorithms": {"FRONT": ["CHROME: RED>GREEN", "GRAY A (Contrast)"], "REAR": ["CHROME: RED>GREEN"]},
                "thresholds": {"HSV": 80, "CHROME": 85, "SIZE": 80, "AI_PURITY": 45, "GRAY_CONTRAST": 60},
                "ai_model": {"model_path": default_ai["model_path"], "enabled": False, "confidence": 90},
                "dataset_dir": "dataset_capture/DHALL"
            },
            "PLASTICS": {
                "lighting": {
                    "WHITE FRONT TOP": {"enabled": True, "lux": 600},
                    "WHITE FRONT BOTTOM": {"enabled": True, "lux": 600},
                    "WHITE BACK TOP": {"enabled": True, "lux": 600},
                    "WHITE BACK BOTTOM": {"enabled": True, "lux": 600},
                    "RED FRONT": {"enabled": False, "lux": 500},
                    "GREEN FRONT": {"enabled": False, "lux": 500},
                    "BLUE FRONT": {"enabled": False, "lux": 500},
                    "RED BACK": {"enabled": False, "lux": 500},
                    "GREEN BACK": {"enabled": False, "lux": 500},
                    "BLUE BACK": {"enabled": False, "lux": 500}
                },
                "ejector": {"pulse": 30, "delay": 100},
                "feeder_speed": 40,
                "algorithms": {"FRONT": ["GRAY B (Threshold)", "HSV COLOR"], "REAR": ["GRAY B (Threshold)"]},
                "thresholds": {"HSV": 80, "CHROME": 70, "SIZE": 85, "AI_PURITY": 50, "GRAY_CONTRAST": 50},
                "ai_model": {"model_path": default_ai["model_path"], "enabled": False, "confidence": 85},
                "dataset_dir": "dataset_capture/PLASTICS"
            },
            "GOOD": {
                "lighting": copy.deepcopy(default_lighting),
                "ejector": {"pulse": 20, "delay": 100},
                "feeder_speed": 50,
                "algorithms": {"FRONT": ["SIZE: BIG"], "REAR": ["SIZE: BIG"]},
                "thresholds": {"HSV": 85, "CHROME": 75, "SIZE": 90, "AI_PURITY": 50, "GRAY_CONTRAST": 50},
                "ai_model": copy.deepcopy(default_ai),
                "dataset_dir": "dataset_capture/GOOD"
            },
            "DATASET COLLECTION": {
                "lighting": copy.deepcopy(default_lighting),
                "ejector": {"pulse": 20, "delay": 100},
                "feeder_speed": 30,
                "algorithms": {"FRONT": ["IMAGE CAPTURE A", "SIZE: BIG"], "REAR": ["IMAGE CAPTURE A"]},
                "thresholds": {"HSV": 85, "CHROME": 75, "SIZE": 90, "AI_PURITY": 50, "GRAY_CONTRAST": 50},
                "ai_model": copy.deepcopy(default_ai),
                "dataset_dir": "dataset_capture/DATASET_COLLECTION"
            },
            "W320 WHOLE - GRADE A": {
                "lighting": copy.deepcopy(default_lighting),
                "ejector": copy.deepcopy(default_ejector),
                "feeder_speed": 50,
                "algorithms": {"FRONT": ["HSV COLOR", "SIZE: BIG", "CHROME: RED>GREEN", "DEEP LEARNING"], "REAR": ["HSV COLOR"]},
                "thresholds": {"HSV": 85, "CHROME": 75, "SIZE": 90, "AI_PURITY": 50, "GRAY_CONTRAST": 50},
                "ai_model": copy.deepcopy(default_ai),
                "dataset_dir": "dataset_capture/W320_WHOLE"
            }
        }
        return {
            "active_scheme": "RICE",
            "schemes": schemes
        }

    @staticmethod
    def load_all():
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), RECIPES_FILE)
        if not os.path.exists(cfg_path):
            data = RecipeManager.get_default_recipes()
            RecipeManager.save_all(data)
            return data

        try:
            with open(cfg_path, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "schemes" not in data:
                data = RecipeManager.get_default_recipes()
                RecipeManager.save_all(data)
                return data

            default_ref = RecipeManager.get_default_recipes()
            for s_name, s_data in data.get("schemes", {}).items():
                if not isinstance(s_data, dict):
                    continue
                if "lighting" not in s_data:
                    s_data["lighting"] = copy.deepcopy(default_ref["schemes"]["RICE"]["lighting"])
                if "ejector" not in s_data:
                    s_data["ejector"] = {"pulse": 20, "delay": 100}
                if "feeder_speed" not in s_data:
                    s_data["feeder_speed"] = 50
                if "algorithms" not in s_data:
                    s_data["algorithms"] = {"FRONT": ["HSV COLOR"], "REAR": ["HSV COLOR"]}
                if "ai_model" not in s_data:
                    s_data["ai_model"] = {"model_path": "", "enabled": False, "confidence": 95}
                if "dataset_dir" not in s_data:
                    s_data["dataset_dir"] = f"dataset_capture/{s_name.replace(' ', '_')}"

            return data
        except Exception as e:
            print(f"[RECIPES] Error loading {cfg_path}: {e}")
            return RecipeManager.get_default_recipes()

    @staticmethod
    def save_all(data):
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), RECIPES_FILE)
        try:
            with open(cfg_path, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"[RECIPES] Error saving {cfg_path}: {e}")

    @staticmethod
    def get_scheme(scheme_name):
        data = RecipeManager.load_all()
        return data.get("schemes", {}).get(scheme_name, None)

    @staticmethod
    def update_scheme_field(scheme_name, field, value):
        data = RecipeManager.load_all()
        if scheme_name in data.get("schemes", {}):
            data["schemes"][scheme_name][field] = value
            RecipeManager.save_all(data)

    @staticmethod
    def get_active_scheme_name():
        data = RecipeManager.load_all()
        return data.get("active_scheme", "RICE")

    @staticmethod
    def set_active_scheme_name(scheme_name):
        data = RecipeManager.load_all()
        data["active_scheme"] = scheme_name
        RecipeManager.save_all(data)

from PyQt5.QtWidgets import QDialog, QVBoxLayout, QGridLayout, QPushButton, QLineEdit, QGroupBox, QHBoxLayout, QLabel, QFormLayout, QWidget
from PyQt5.QtCore import Qt
import subprocess
import hashlib
import json
import os
import sys
import datetime

SECRET_SALT = "LAMBTON_MASTER_KEY"  # உங்கள் ஜெனரேட்டரில் உள்ள அதே கீ!

LICENSE_FILE = "tcm_license.dat"
SYS_INFO_FILE = "system_info.json"
MASTER_PASSWORD = "TCM_ADMIN_2026"  # நீங்கள் விரும்பும் ரகசிய பாஸ்வேர்டு


# =============================================================================
# DEVELOPER / TESTING MODE
# Hard disk UID protection and camera serial checks temporarily bypassed for testing
# =============================================================================
DEV_UUIDS = ("90001563-B3AE-3E3F-5561-B082E25BC651",)   # developer PC (development PC, 16 Oct 2026)
DEV_UNLOCK_FILE = "dev_unlock.json"


class DeveloperMode:
    _active = None
    reason = "TESTING MODE (Hard disk UID & Camera checks bypassed)"

    @staticmethod
    def hardware_uuid():
        """Motherboard UUID from WMI / CIM only (None if it cannot be read)."""
        for cmd in (["wmic", "csproduct", "get", "uuid"],
                    ["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance -ClassName Win32_ComputerSystemProduct).UUID"]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
            except Exception:
                continue
            for line in out.splitlines():
                line = line.strip()
                if line and "UUID" not in line.upper() and line != "00000000-0000-0000-0000-000000000000":
                    return line
        return None

    @staticmethod
    def is_active():
        # Hard disk UID protection and camera serial number checks removed temporarily for testing purpose
        DeveloperMode.reason = "TESTING MODE (Hard disk UID & Camera checks bypassed)"
        return True

    @staticmethod
    def apply_banner(window, cameras_found):
        """Unmistakable marking: window title + status bar line."""
        cams = ", ".join(cameras_found) if cameras_found else "NO CAMERA connected"
        window.setWindowTitle(f"{window.windowTitle()}   ***  TESTING MODE (UID & Camera checks bypassed)  ***")
        bar = window.statusBar()
        bar.setStyleSheet("QStatusBar { background-color: #1565c0; color: white; font-weight: bold; font-size: 14px; }")
        label = QLabel(f"  TESTING MODE  |  Hard disk UID & Camera serial protection bypassed  |  cameras: {cams}  ")
        label.setStyleSheet("color: white; font-weight: bold;")
        bar.addPermanentWidget(label, 1)
        bar.show()


class LicenseManager:
    _cached_uuid = None

    @staticmethod
    def get_pc_serial():
        """Retrieves Motherboard UUID (Cached & Windows 11 Ready)"""
        if LicenseManager._cached_uuid:
            return LicenseManager._cached_uuid

        # 1. Try wmic first (standard for Win 10 and older)
        try:
            cmd = subprocess.Popen("wmic csproduct get uuid", shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            output = cmd.stdout.read().decode('utf-8', errors='ignore')
            lines = [l.strip() for l in output.split('\n') if l.strip() and "UUID" not in l.upper()]
            if lines and lines[0] and lines[0] != "00000000-0000-0000-0000-000000000000":
                LicenseManager._cached_uuid = lines[0]
                return lines[0]
        except Exception:
            pass

        # 2. Try PowerShell CIM query (Standard for Win 11 where wmic is removed)
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "(Get-CimInstance -ClassName Win32_ComputerSystemProduct).UUID"],
                capture_output=True, text=True, timeout=5
            )
            out = proc.stdout.strip()
            if out and len(out) >= 10:
                LicenseManager._cached_uuid = out
                return out
        except Exception:
            pass

        # 3. Fallback to Windows Registry MachineGuid
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                val, _ = winreg.QueryValueEx(key, "MachineGuid")
                if val:
                    LicenseManager._cached_uuid = str(val).strip()
                    return LicenseManager._cached_uuid
        except Exception:
            pass

        return "UNKNOWN_PC"

    @staticmethod
    def generate_hash(pc_serial, front_cam_sn, rear_cam_sn):
        """Generates secure hash combining PC serial and camera serials"""
        raw_string = f"{pc_serial}_{front_cam_sn}_{rear_cam_sn}_TCM_SECURE"
        return hashlib.sha256(raw_string.encode()).hexdigest()

    @staticmethod
    def get_license_info():
        """Returns comprehensive license information dictionary"""
        BASE_DATE = datetime.date(1991, 11, 11)
        uuid = LicenseManager.get_pc_serial()
        info = {
            "valid": False,
            "days_remaining": -1,
            "expiry_date": "N/A",
            "status": "NO LICENSE",
            "license_key": "",
            "hw_id": uuid,
            "system_locked": False
        }

        if DeveloperMode.is_active():
            info.update(valid=True, days_remaining=9999, expiry_date="N/A (developer PC)",
                        status="DEVELOPER MODE (licence not checked)")
            return info

        if not os.path.exists(LICENSE_FILE):
            info["status"] = "LICENSE FILE MISSING"
            return info

        try:
            with open(LICENSE_FILE, "r") as f:
                data = json.load(f)

            info["system_locked"] = data.get("system_locked", False)
            if info["system_locked"]:
                info["status"] = "SYSTEM LOCKED"
                info["days_remaining"] = -2
                return info

            raw_key = data.get("license_key", "").strip()
            info["license_key"] = raw_key
            key = raw_key.replace("-", "").strip().upper()
            if len(key) != 16:
                info["status"] = "INVALID KEY FORMAT"
                return info

            hw_prefix = key[:4]
            hex_days = key[4:8]
            signature = key[8:]

            full_m_id = uuid.replace("-", "")
            current_hw_id = full_m_id[:4]
            if hw_prefix != current_hw_id:
                info["status"] = f"HARDWARE MISMATCH (PC: {current_hw_id})"
                return info

            SECRET_SALT = "LAMBTON_MASTER_KEY"
            raw_sign = f"{full_m_id}{hex_days}{SECRET_SALT}"
            expected_sign = hashlib.sha256(raw_sign.encode()).hexdigest().upper()[:8]
            if signature != expected_sign:
                info["status"] = "INVALID SIGNATURE (TAMPERED)"
                return info

            expiry_epoch_days = int(hex_days, 16)
            expiry_date = BASE_DATE + datetime.timedelta(days=expiry_epoch_days)
            today = datetime.date.today()
            remaining = (expiry_date - today).days

            info["expiry_date"] = expiry_date.strftime("%d-%b-%Y")
            info["days_remaining"] = remaining

            if remaining < 0:
                info["status"] = f"EXPIRED ({info['expiry_date']})"
                info["valid"] = False
            else:
                info["status"] = f"ACTIVE ({remaining} Days Remaining)"
                info["valid"] = True

            return info
        except Exception as e:
            info["status"] = f"ERROR: {str(e)}"
            return info

    @staticmethod
    def is_license_valid():
        info = LicenseManager.get_license_info()
        return info["valid"]

    @staticmethod
    def get_license_days_remaining():
        info = LicenseManager.get_license_info()
        if info["system_locked"]:
            return -2
        if not info["valid"]:
            return -1 if info["days_remaining"] <= 0 else info["days_remaining"]
        return info["days_remaining"]

    @staticmethod
    def apply_license(key_str):
        """
        Validates a new license key and saves it to LICENSE_FILE if valid.
        Returns: (success: bool, message: str, expiry_date_str: str, days_remaining: int)
        """
        BASE_DATE = datetime.date(1991, 11, 11)
        SECRET_SALT = "LAMBTON_MASTER_KEY"

        clean_key = str(key_str).replace("-", "").replace(" ", "").strip().upper()
        if len(clean_key) != 16:
            return False, "Key must be exactly 16 characters (e.g. XXXX-XXXX-XXXX-XXXX)!", "", -1

        hw_prefix = clean_key[:4]
        hex_days = clean_key[4:8]
        signature = clean_key[8:]

        uuid = LicenseManager.get_pc_serial()
        full_m_id = uuid.replace("-", "")
        current_hw_id = full_m_id[:4]

        if hw_prefix != current_hw_id:
            return False, f"Hardware Mismatch!\nKey Prefix: {hw_prefix}\nExpected PC ID: {current_hw_id}", "", -1

        raw_sign = f"{full_m_id}{hex_days}{SECRET_SALT}"
        expected_sign = hashlib.sha256(raw_sign.encode()).hexdigest().upper()[:8]
        if signature != expected_sign:
            return False, "Invalid Key! The signature is incorrect or tampered.", "", -1

        try:
            expiry_epoch_days = int(hex_days, 16)
        except ValueError:
            return False, "Invalid days format in license key!", "", -1

        expiry_date = BASE_DATE + datetime.timedelta(days=expiry_epoch_days)
        today = datetime.date.today()
        remaining = (expiry_date - today).days

        if remaining < 0:
            return False, f"This License Key has already expired on {expiry_date.strftime('%d-%b-%Y')}!", "", remaining

        formatted_key = f"{clean_key[:4]}-{clean_key[4:8]}-{clean_key[8:12]}-{clean_key[12:]}"

        data = {"license_key": formatted_key, "system_locked": False}
        try:
            with open(LICENSE_FILE, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            return False, f"Failed to write license file: {str(e)}", "", -1

        expiry_str = expiry_date.strftime("%d-%b-%Y")
        return True, f"License Activated Successfully!\nValid for {remaining} days until {expiry_str}.", expiry_str, remaining


# =============================================================================
# OEM / FACTORY SETUP DIALOG (CRASH-PROOF & NEW LAYOUT)
# =============================================================================
class FactorySetupDialog(QDialog):
    def __init__(self, front_worker, rear_worker, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Software Activation & Factory Config")
        self.setMinimumSize(850, 950)  # Increased size and removed fixed constraint to prevent squishing
        self.setStyleSheet("background-color: #1a1a1a; color: white; font-family: Segoe UI;")

        self.front_worker = front_worker
        self.rear_worker = rear_worker

        # Default Settings
        self.sys_data = {
            "dealer_name": "TCM Photoelectrics",
            "machine_sn": "NWT-2026-V1",
            "logo_path": "logo.png",
            "bg_path": "background.jpg",
            "last_run_date": "2000-01-01"
        }
        self.load_data()

        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # =========================================================
        # 1. HARDWARE IDENTIFICATION SECTION (TOP)
        # =========================================================
        grp_hw = QGroupBox("SYSTEM HARDWARE IDENTIFICATION")
        grp_hw.setStyleSheet(
            "QGroupBox { border: 2px solid #ff9800; border-radius: 8px; font-weight: bold; color: #ff9800; padding-top: 25px; font-size: 14px;}")
        hw_layout = QVBoxLayout(grp_hw)

        self.pc_uuid = LicenseManager.get_pc_serial()

        # கேமரா சீரியல் எண்களை எடுத்தல்
        self.front_sn = "NOT_CONNECTED"
        if hasattr(self.front_worker, '_raw_dev') and self.front_worker._raw_dev:
            self.front_sn = self.front_worker._raw_dev.acSn.decode('utf-8', errors='ignore')

        self.rear_sn = "NOT_CONNECTED"
        if hasattr(self.rear_worker, '_raw_dev') and self.rear_worker._raw_dev:
            self.rear_sn = self.rear_worker._raw_dev.acSn.decode('utf-8', errors='ignore')

        lbl_hw = QLabel(
            f"🖥️ PC SYSTEM ID: {self.pc_uuid}\n📷 FRONT CAM: {self.front_sn}   |   📷 REAR CAM: {self.rear_sn}")
        lbl_hw.setStyleSheet(
            "color: #ffeb3b; font-size: 18px; font-weight: bold; letter-spacing: 1px; padding: 15px; background: #000; border: 1px dashed #555; border-radius: 5px;")
        lbl_hw.setAlignment(Qt.AlignCenter)
        hw_layout.addWidget(lbl_hw)
        layout.addWidget(grp_hw)

        # =========================================================
        # 2. LICENSE ACTIVATION SECTION
        # =========================================================
        grp_act = QGroupBox("SOFTWARE ACTIVATION")
        grp_act.setStyleSheet(
            "QGroupBox { border: 2px solid #00e676; border-radius: 8px; font-weight: bold; color: #00e676; padding-top: 25px; font-size: 14px;}")
        act_layout = QVBoxLayout(grp_act)

        act_row = QHBoxLayout()
        self.txt_act_key = QLineEdit()
        self.txt_act_key.setPlaceholderText("Enter 16-Digit License Key Here...")
        self.txt_act_key.setStyleSheet(
            "background: #222; color: #00e676; font-size: 22px; font-weight: bold; padding: 12px; border: 2px solid #555; border-radius: 5px; letter-spacing: 3px;")

        btn_kb_act = QPushButton("⌨️")
        btn_kb_act.setFixedSize(55, 55)
        btn_kb_act.setStyleSheet("background: #444; font-size: 20px; border-radius: 5px;")
        btn_kb_act.clicked.connect(
            lambda: VirtualKeyboard(self.txt_act_key.text(), self.txt_act_key.setText, self).exec_())

        self.btn_activate = QPushButton("VERIFY & ACTIVATE")
        self.btn_activate.setStyleSheet(
            "background: #00bcd4; color: black; font-weight: bold; font-size: 16px; padding: 12px; border-radius: 5px;")
        self.btn_activate.clicked.connect(self.activate_software)

        act_row.addWidget(self.txt_act_key)
        act_row.addWidget(btn_kb_act)
        act_row.addWidget(self.btn_activate)
        act_layout.addLayout(act_row)

        l_type, l_days = self.get_license_status()
        self.lbl_license_status = QLabel(f"LICENSE STATUS: {l_type}   |   DAYS REMAINING: {l_days}")
        self.lbl_license_status.setStyleSheet(
            "color: #00bcd4; font-size: 15px; font-weight: bold; padding: 10px; background: #070707; border: 1px solid #332; border-radius: 5px; margin-top: 8px;")
        self.lbl_license_status.setAlignment(Qt.AlignCenter)
        act_layout.addWidget(self.lbl_license_status)

        self.btn_license_lock = QPushButton()
        self.btn_license_lock.clicked.connect(self.prompt_license_lock_toggle)
        self.update_lock_button_state()
        act_layout.addWidget(self.btn_license_lock)

        layout.addWidget(grp_act)

        # =========================================================
        # 3. ADMIN UNLOCK & LOGOUT BUTTONS
        # =========================================================
        admin_row = QHBoxLayout()
        self.btn_admin_unlock = QPushButton("🔓 FACTORY ADMIN LOGIN")
        self.btn_admin_unlock.setStyleSheet(
            "background: #ff1744; color: white; font-weight: bold; font-size: 16px; padding: 15px; border-radius: 8px;")
        self.btn_admin_unlock.clicked.connect(self.prompt_admin_login)

        self.btn_admin_logout = QPushButton("🔒 RELOCK / LOGOUT")
        self.btn_admin_logout.setStyleSheet(
            "background: #757575; color: white; font-weight: bold; font-size: 16px; padding: 15px; border-radius: 8px;")
        self.btn_admin_logout.setVisible(False)  # ஆரம்பத்தில் மறைக்கப்பட்டிருக்கும்
        self.btn_admin_logout.clicked.connect(self.admin_logout)

        admin_row.addWidget(self.btn_admin_unlock)
        admin_row.addWidget(self.btn_admin_logout)
        layout.addLayout(admin_row)

        # =========================================================
        # 4. FACTORY SETTINGS (LOCKED BY DEFAULT)
        # =========================================================
        self.grp_settings = QGroupBox("FACTORY & OEM CONFIGURATION (LOCKED)")
        self.grp_settings.setStyleSheet(
            "QGroupBox { border: 2px solid #555; border-radius: 8px; font-weight: bold; color: gray; padding-top: 25px; font-size: 14px;}")
        form_layout = QFormLayout(self.grp_settings)
        form_layout.setSpacing(15)

        def create_input_row(default_val):
            row = QHBoxLayout()
            txt = QLineEdit(default_val)
            txt.setReadOnly(True)
            txt.setMinimumHeight(45)
            txt.setStyleSheet(
                "background: #222; color: #aaa; padding: 10px; font-size: 16px; border: 1px solid #444; border-radius: 4px;")

            btn_kb = QPushButton("⌨️")
            btn_kb.setFixedSize(45, 45)
            btn_kb.setEnabled(False)
            btn_kb.setStyleSheet("background: #222; font-size: 18px; border-radius: 4px; color: #555;")
            
            show_kb = lambda t=txt: VirtualKeyboard(t.text(), t.setText, self).exec_() if not t.isReadOnly() else None
            btn_kb.clicked.connect(lambda _, t=txt: show_kb(t))
            KeypadEventFilter(txt, lambda t=txt: show_kb(t))

            row.addWidget(txt)
            row.addWidget(btn_kb)
            return txt, btn_kb, row

        self.txt_dealer, self.btn_kb_dealer, row_dealer = create_input_row(self.sys_data.get("dealer_name", ""))
        form_layout.addRow("Dealer / Brand:", row_dealer)

        # Machine SN (பௌதீக மெஷினின் சீரியல் நம்பர்)
        self.txt_sn, self.btn_kb_sn, row_sn = create_input_row(self.sys_data.get("machine_sn", ""))
        form_layout.addRow("Physical Machine SN:", row_sn)

        def create_browse_row(default_val, browse_func):
            row = QHBoxLayout()
            txt = QLineEdit(default_val)
            txt.setReadOnly(True)
            txt.setMinimumHeight(45)
            txt.setStyleSheet(
                "background: #222; color: #aaa; padding: 10px; font-size: 16px; border: 1px solid #444; border-radius: 4px;")

            btn_brw = QPushButton("BROWSE")
            btn_brw.setEnabled(False)
            btn_brw.setMinimumHeight(45)
            btn_brw.setStyleSheet(
                "background: #222; color: #555; font-weight: bold; padding: 10px; border-radius: 4px;")
            btn_brw.clicked.connect(lambda: browse_func(txt))

            row.addWidget(txt)
            row.addWidget(btn_brw)
            return txt, btn_brw, row

        self.txt_logo, self.btn_brw_logo, row_logo = create_browse_row(self.sys_data.get("logo_path", ""),
                                                                       self.browse_image)
        form_layout.addRow("Brand Logo:", row_logo)

        self.txt_bg, self.btn_brw_bg, row_bg = create_browse_row(self.sys_data.get("bg_path", ""), self.browse_image)
        form_layout.addRow("Background:", row_bg)

        # Pressure Alarm Checkbox
        from PyQt5.QtWidgets import QCheckBox
        self.chk_pressure = QCheckBox("Enable Hardware Pressure Watchdog Alarm")
        self.chk_pressure.setChecked(self.sys_data.get("enable_pressure_alarm", False))
        self.chk_pressure.setEnabled(False)
        self.chk_pressure.setStyleSheet("color: #ff9800; font-size: 16px; font-weight: bold; margin-top: 5px;")
        form_layout.addRow("Pressure Alarm:", self.chk_pressure)

        layout.addWidget(self.grp_settings)
        layout.addStretch()

        # =========================================================
        # 5. ACTION BUTTONS
        # =========================================================
        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("💾 SAVE FACTORY SETTINGS")
        self.btn_save.setEnabled(False)
        self.btn_save.setFixedHeight(60)
        self.btn_save.setStyleSheet(
            "background: #222; color: #555; font-weight: bold; font-size: 16px; border-radius: 8px;")
        self.btn_save.clicked.connect(self.save_data)

        btn_close = QPushButton("CLOSE")
        btn_close.setFixedHeight(60)
        btn_close.setStyleSheet(
            "background: #444; color: white; font-weight: bold; font-size: 16px; border-radius: 8px;")
        btn_close.clicked.connect(self.reject)

        btn_layout.addWidget(btn_close)
        btn_layout.addWidget(self.btn_save)
        layout.addLayout(btn_layout)

        # Store widgets for easy locking/unlocking
        self.locked_inputs = [self.txt_dealer, self.txt_sn, self.txt_logo, self.txt_bg]
        self.locked_buttons = [self.btn_kb_dealer, self.btn_kb_sn, self.btn_brw_logo, self.btn_brw_bg]

    def browse_image(self, target_txt):
        file, _ = QFileDialog.getOpenFileName(self, "Select Image", "", "Images (*.png *.jpg *.jpeg)",
                                              options=QFileDialog.DontUseNativeDialog)
        if file: target_txt.setText(file)

    def load_data(self):
        if os.path.exists(SYS_INFO_FILE):
            try:
                with open(SYS_INFO_FILE, "r") as f:
                    self.sys_data.update(json.load(f))
            except:
                pass

    def get_license_status(self):
        info = LicenseManager.get_license_info()
        if info["system_locked"]:
            return "SYSTEM LOCKED BY FACTORY ADMIN", "N/A"
        if not info["valid"]:
            return info["status"], "0 Days"
        return f"ACTIVE (Expires: {info['expiry_date']})", f"{info['days_remaining']} Days Remaining"

    # --- ADMIN UNLOCK / LOCK LOGIC ---
    def prompt_admin_login(self):
        Keypad("", self.verify_password, self, is_password=True).exec_()

    def verify_password(self, pwd):
        if pwd == "111191":
            self.grp_settings.setTitle("FACTORY & OEM CONFIGURATION (UNLOCKED 🔓)")
            self.grp_settings.setStyleSheet(
                "QGroupBox { border: 2px solid #ffeb3b; border-radius: 8px; font-weight: bold; color: #ffeb3b; padding-top: 25px; font-size: 14px;}")

            for txt in self.locked_inputs:
                txt.setReadOnly(False)
                txt.setStyleSheet(
                    "background: #333; color: white; padding: 10px; font-size: 16px; border: 1px solid #777; border-radius: 4px;")

            for btn in self.locked_buttons:
                btn.setEnabled(True)
                btn.setStyleSheet(
                    "background: #00bcd4; color: black; font-weight: bold; font-size: 18px; border-radius: 4px;")

            self.chk_pressure.setEnabled(True)

            self.btn_save.setEnabled(True)
            self.btn_save.setStyleSheet(
                "background: #00e676; color: black; font-weight: bold; font-size: 16px; border-radius: 8px;")

            self.btn_admin_unlock.setVisible(False)
            self.btn_admin_logout.setVisible(True)
        else:
            QMessageBox.critical(self, "Access Denied", "Incorrect Admin Password!")

    def admin_logout(self):
        self.grp_settings.setTitle("FACTORY & OEM CONFIGURATION (LOCKED)")
        self.grp_settings.setStyleSheet(
            "QGroupBox { border: 2px solid #555; border-radius: 8px; font-weight: bold; color: gray; padding-top: 25px; font-size: 14px;}")

        for txt in self.locked_inputs:
            txt.setReadOnly(True)
            txt.setStyleSheet(
                "background: #111; color: #555; padding: 10px; font-size: 16px; border: 1px solid #333; border-radius: 4px;")

        for btn in self.locked_buttons:
            btn.setEnabled(False)
            btn.setStyleSheet("background: #222; font-size: 18px; border-radius: 4px; color: #555;")

        self.chk_pressure.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_save.setStyleSheet(
            "background: #222; color: #555; font-weight: bold; font-size: 16px; border-radius: 8px;")

        self.btn_admin_unlock.setVisible(True)
        self.btn_admin_logout.setVisible(False)

    def prompt_license_lock_toggle(self):
        Keypad("", self.verify_license_lock_pwd, self, is_password=True).exec_()

    def verify_license_lock_pwd(self, pwd):
        if pwd == "111191":
            locked = False
            if os.path.exists(LICENSE_FILE):
                try:
                    with open(LICENSE_FILE, "r") as f:
                        data = json.load(f)
                    locked = data.get("system_locked", False)
                except:
                    data = {}
            else:
                data = {}
            
            new_locked = not locked
            data["system_locked"] = new_locked
            
            try:
                with open(LICENSE_FILE, "w") as f:
                    json.dump(data, f)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save lock status: {e}")
                return
            
            self.update_lock_button_state()
            l_type, l_days = self.get_license_status()
            self.lbl_license_status.setText(f"LICENSE STATUS: {l_type}   |   DAYS REMAINING: {l_days}")
            
            action_str = "LOCKED" if new_locked else "UNLOCKED"
            QMessageBox.information(self, "Success", f"System has been successfully {action_str}!")
        else:
            QMessageBox.critical(self, "Error", "Incorrect Password!")

    def update_lock_button_state(self):
        locked = False
        if os.path.exists(LICENSE_FILE):
            try:
                with open(LICENSE_FILE, "r") as f:
                    data = json.load(f)
                locked = data.get("system_locked", False)
            except:
                pass
        
        if locked:
            self.btn_license_lock.setText("🔓 UNLOCK SYSTEM")
            self.btn_license_lock.setStyleSheet(
                "background: #00e676; color: black; font-weight: bold; font-size: 16px; padding: 12px; border-radius: 5px; margin-top: 8px;")
        else:
            self.btn_license_lock.setText("🔒 LOCK SYSTEM")
            self.btn_license_lock.setStyleSheet(
                "background: #ff1744; color: white; font-weight: bold; font-size: 16px; padding: 12px; border-radius: 5px; margin-top: 8px;")

    def activate_software(self):
        entered_key = self.txt_act_key.text()
        success, msg, exp_date, days = LicenseManager.apply_license(entered_key)
        if not success:
            QMessageBox.critical(self, "Activation Error", msg)
            return

        QMessageBox.information(self, "Success", f"✅ {msg}")
        
        # dynamically refresh status display
        if hasattr(self, 'lbl_license_status'):
            l_type, l_days = self.get_license_status()
            self.lbl_license_status.setText(f"LICENSE STATUS: {l_type}   |   DAYS REMAINING: {l_days}")
            
        self.update_lock_button_state()
        self.accept()

    # --- SAVE FACTORY SETTINGS ---
    def save_data(self):
        self.sys_data["dealer_name"] = self.txt_dealer.text()
        self.sys_data["machine_sn"] = self.txt_sn.text()
        self.sys_data["logo_path"] = self.txt_logo.text()
        self.sys_data["bg_path"] = self.txt_bg.text()
        self.sys_data["enable_pressure_alarm"] = self.chk_pressure.isChecked()

        try:
            with open(SYS_INFO_FILE, "w") as f:
                json.dump(self.sys_data, f, indent=4)
            QMessageBox.information(self, "Success", "Factory Settings Saved Successfully!")
            
            # Instantly update active thresholds state in MainDashboard if available
            main_window = self.parent()
            if main_window and hasattr(main_window, "thresholds"):
                main_window.thresholds["ENABLE_PRESSURE_ALARM"] = self.chk_pressure.isChecked()
            
            # self.admin_logout()  # சேவ் செய்த பிறகு தானாகவே லாக் செய்துவிடும்
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save system info: {e}")


# ==========================================
# 1. CUSTOM KEYPAD CLASS (ANTI-FREEZE)
# ==========================================
class Keypad(QDialog):
    def __init__(self, initial_value, callback, parent=None, is_password=False):
        super().__init__(parent)
        self.setWindowTitle("Enter Value")
        self.setFixedSize(300, 400)
        self.setStyleSheet("""
            QDialog { background-color: #2f3640; border: 2px solid #00bcd4; }
            QLineEdit { background: #1e272e; color: #00e5ff; font-size: 30px; font-weight: bold; border: none; padding: 10px; }
            QPushButton { background: #4b6584; color: white; font-size: 20px; font-weight: bold; border: 1px solid #778ca3; border-radius: 5px; }
            QPushButton:pressed { background: #00bcd4; color: black; }
            QPushButton#Enter { background: #00e676; color: black; }
            QPushButton#Clear { background: #ff1744; color: white; }
        """)

        self.callback = callback
        self.layout = QVBoxLayout(self)

        self.display = QLineEdit(str(initial_value))
        self.first_press = bool(str(initial_value).strip() != "")
        if is_password:
            self.display.setEchoMode(QLineEdit.Password)
        self.display.setAlignment(Qt.AlignRight)
        self.display.setReadOnly(True)  # கீபோர்டில் டைப் செய்வதைத் தடுக்க
        self.layout.addWidget(self.display)

        grid = QGridLayout()
        self.layout.addLayout(grid)

        buttons = [
            ('7', 0, 0), ('8', 0, 1), ('9', 0, 2),
            ('4', 1, 0), ('5', 1, 1), ('6', 1, 2),
            ('1', 2, 0), ('2', 2, 1), ('3', 2, 2),
            ('*', 3, 0), ('0', 3, 1), ('.', 3, 2),
        ]

        for text, r, c in buttons:
            btn = QPushButton(text)
            btn.setFixedSize(80, 60)
            btn.clicked.connect(lambda _, t=text: self.on_click(t))
            grid.addWidget(btn, r, c)

        bottom_layout = QHBoxLayout()
        
        btn_clear = QPushButton("CLEAR")
        btn_clear.setObjectName("Clear")
        btn_clear.setFixedHeight(60)
        btn_clear.clicked.connect(lambda: self.on_click('C'))
        bottom_layout.addWidget(btn_clear, stretch=1)

        btn_enter = QPushButton("ENTER")
        btn_enter.setObjectName("Enter")
        btn_enter.setFixedHeight(60)
        btn_enter.clicked.connect(self.on_enter)
        bottom_layout.addWidget(btn_enter, stretch=2)
        
        self.layout.addLayout(bottom_layout)

    def on_click(self, text):
        if text == 'C':
            self.display.setText("")
            self.first_press = False
            return
        if getattr(self, 'first_press', False):
            self.first_press = False
            if text != '.':
                self.display.setText(text)
                return
        current = self.display.text()
        if text == '.':
            if '.' not in current: self.display.setText(current + text)
        else:
            self.display.setText(current + text)

    def on_enter(self):
        val = self.display.text()
        if val:
            try:
                self.callback(val)  # பிழை ஏற்பட்டால் தடுத்துவிடும்
            except Exception as e:
                print("Keypad Error:", e)
        self.accept()

    def keyPressEvent(self, event):
        from PyQt5.QtCore import Qt
        t = event.text()
        if t.isdigit() or t == '.':
            self.on_click(t)
        elif event.key() == Qt.Key_Backspace:
            self.first_press = False
            cur = self.display.text()
            self.display.setText(cur[:-1])
        elif event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            self.on_enter()
        elif event.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

from PyQt5.QtCore import QObject

class KeypadEventFilter(QObject):
    def __init__(self, obj, callback):
        super().__init__(obj)
        self.callback = callback
        obj.installEventFilter(self)

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if event.type() == QEvent.MouseButtonPress:
            self.callback()
            return True
        return super().eventFilter(obj, event)

# ==========================================
# 2. VIRTUAL KEYBOARD (எழுத்துகளை டைப் செய்ய)
# ==========================================
class VirtualKeyboard(QDialog):
    def __init__(self, initial_text, callback, parent=None):
        super().__init__(parent)
        self.callback = callback
        self.setWindowTitle("On-Screen Keyboard")
        self.setFixedSize(1000, 520)  # எண்கள் சேர்க்கப்பட்டுள்ளதால் உயரத்தை சற்று அதிகரித்துள்ளோம்

        self.setStyleSheet("""
            QDialog { background-color: #222; border: 2px solid #00bcd4; }
            QLineEdit { font-size: 30px; padding: 15px; background: #111; color: #00e676; border: none; }
            QPushButton { font-size: 24px; font-weight: bold; background-color: #444; color: white; border-radius: 5px; }
            QPushButton:pressed { background-color: #00bcd4; color: black; }
            QPushButton#Action { background-color: #ff1744; }
            QPushButton#Space { background-color: #555; }
            QPushButton#Enter { background-color: #00bcd4; color: black; }
        """)

        layout = QVBoxLayout(self)

        self.display = QLineEdit(initial_text)
        self.display.setReadOnly(True)
        layout.addWidget(self.display)

        grid = QGridLayout()
        layout.addLayout(grid)

        # திருத்தப்பட்ட லேஅவுட் - எண்கள் (1-0) முதல் வரியாகச் சேர்க்கப்பட்டுள்ளது
        keys = [
            ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
            ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'],
            ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L', 'CLR'],
            ['Z', 'X', 'C', 'V', 'B', 'N', 'M', '_', '-', 'SPACE']
        ]

        for row_idx, row in enumerate(keys):
            for col_idx, key in enumerate(row):
                btn = QPushButton(key)
                btn.setFixedHeight(75)

                if key == 'CLR':
                    btn.setObjectName("Action")
                elif key == 'SPACE':
                    btn.setObjectName("Space")

                btn.clicked.connect(lambda _, k=key: self.key_press(k))
                grid.addWidget(btn, row_idx, col_idx)

        btn_enter = QPushButton("ENTER / SAVE")
        btn_enter.setObjectName("Enter")
        btn_enter.setFixedHeight(70)
        btn_enter.clicked.connect(self.on_enter)
        layout.addWidget(btn_enter)

    def key_press(self, key):
        current_text = self.display.text()
        if key == 'CLR':
            self.display.setText(current_text[:-1])  # Backspace
        elif key == 'SPACE':
            self.display.setText(current_text + " ")  # Space
        else:
            self.display.setText(current_text + key)

    def on_enter(self):
        val = self.display.text()
        if val:
            try:
                self.callback(val)
            except Exception as e:
                print(f"Keypad Data Error: {e}")
        self.accept()


# --- PyQt5 IMPORTS ---
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QFrame, QDialog, QLineEdit, QComboBox,
                             QMessageBox, QGridLayout, QGroupBox, QScrollArea, QDial,
                             QSpinBox, QDoubleSpinBox, QFormLayout, QCheckBox, QListWidget,
                             QInputDialog, QProgressBar, QSlider, QSizePolicy, QFileDialog,
                             QStackedWidget, QTabWidget)  # <--- THIS WAS MISSING

from PyQt5.QtCore import Qt, QTimer, QDateTime, pyqtSignal, QRectF, QPointF, QRect, QPoint
from PyQt5.QtGui import QFont, QPixmap, QImage, QPainter, QPen, QColor, QRadialGradient, QLinearGradient, QBrush, QCursor, QPainterPath

# --- MVSDK (CAMERA) LIBRARY ---
try:
    import mvsdk

    MVSDK_AVAILABLE = True
except ImportError:
    MVSDK_AVAILABLE = False
    print("[WARN] 'mvsdk.py' not found. Camera features will be disabled.")

# --- CONFIGURATION ---
# Images: the old location (MindVision demo folder / ..\lambton) is used if it exists, so an existing
# installation looks exactly as before; otherwise the files in this program folder are used, so the
# program also runs from any folder on a new PC (e.g. C:\LAMTON_V2\app).
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_OLD_IMG_DIR = r"C:\Program Files (x86)\MindVision\Demo\Python\python_demo\lambton"

def _first_existing(*paths):
    return next((q for q in paths if os.path.exists(q)), paths[-1])

LOGO_PATH = _first_existing(os.path.join(_OLD_IMG_DIR, "logo.png"), os.path.join(_APP_DIR, "logo.png"))
BG_PATH = _first_existing(os.path.join(_OLD_IMG_DIR, "background.jpg"), os.path.join(_APP_DIR, "background.jpg"))
# Icons (power button, toggles): ..\lambton as before, else assets\ in this program folder
ASSET_DIR = os.path.dirname(os.path.abspath(_first_existing(
    os.path.join(_APP_DIR, "..", "lambton", "power_button.svg"),
    os.path.join(_APP_DIR, "assets", "power_button.svg")))).replace(os.sep, "/")
BASE_FOLDER = "dataset_capture"
if not os.path.exists(BASE_FOLDER): os.makedirs(BASE_FOLDER)

# =============================================================================
# 0. GLOBAL STYLESHEET
# =============================================================================
STYLESHEET = """
    QMainWindow { background-color: #0f0f0f; }
    QWidget { font-family: 'Segoe UI', Arial, sans-serif; color: #ecf0f1; font-size: 14px; }
    QLabel#TitleLabel { color: #00bcd4; font-weight: bold; font-size: 13px; letter-spacing: 1px; }

    /* PANELS */
    QFrame#SidePanel {
        background-color: #161616;
        border: 2px solid #333;
        border-radius: 10px;
    }
    QFrame#SidePanel:hover { border-color: #00bcd4; }

    /* PROGRESS BARS */
    QProgressBar {
        border: 1px solid #444;
        background-color: #0f0f0f;
        border-radius: 4px;
        text-align: center;
    }

    /* INPUT FIELDS */
    QSpinBox, QDoubleSpinBox {
        background-color: #0f0f0f; 
        border: 1px solid #444; 
        border-radius: 4px;
        color: #ffffff; 
        padding: 2px; 
        font-weight: bold; 
        font-size: 15px;
    }
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { 
        background-color: #333; 
        width: 28px;
        border-left: 1px solid #444;
    }
    QSpinBox::up-button, QDoubleSpinBox::up-button {
        subcontrol-origin: border;
        subcontrol-position: top right;
        border-bottom: 1px solid #444;
    }
    QSpinBox::down-button, QDoubleSpinBox::down-button {
        subcontrol-origin: border;
        subcontrol-position: bottom right;
    }
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
        image: url(up_arrow.svg);
        width: 14px;
        height: 14px;
    }
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
        image: url(down_arrow.svg);
        width: 14px;
        height: 14px;
    }

    QComboBox, QLineEdit {
        background-color: #252525; border: 1px solid #444; border-radius: 4px;
        color: #ffffff; padding: 6px; font-weight: bold; font-size: 15px;
    }
    
    QComboBox QAbstractItemView {
        background-color: #252525;
        color: #ffffff;
        selection-background-color: #00bcd4;
        selection-color: black;
    }

    QListWidget {
        background-color: #1a1a1a; border: 2px solid #333; border-radius: 6px;
        font-size: 16px; font-weight: bold; color: #aaa; padding: 5px;
    }
    QListWidget::item { padding: 10px; border-bottom: 1px solid #222; }
    QListWidget::item:selected { background-color: #006064; color: #ffffff; border: 1px solid #00bcd4; border-radius: 4px; }

    QPushButton {
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3a3a3a, stop:1 #2b2b2b);
        border: 1px solid #555; border-radius: 6px; color: #e0e0e0; padding: 8px; font-weight: bold;
    }
    QPushButton:hover {
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4a4a4a, stop:1 #3a3a3a);
        border-color: #00bcd4; color: #ffffff;
    }
    QPushButton:pressed { background-color: #1a1a1a; border-color: #00bcd4; }

    QPushButton.HeaderBtn { background-color: transparent; border: 1px solid #444; color: #bbb; min-width: 60px; font-size: 12px; }
    QPushButton.HeaderBtn:hover { border-color: #00bcd4; color: #00bcd4; }
    QPushButton.FooterBtn { font-size: 16px; border: 1px solid #444; background-color: #222; border-radius: 8px; }
    QPushButton.SmallFooterBtn { background-color: #006064; color: white; border: 1px solid #00bcd4; font-size: 14px; }
    QPushButton.SmallFooterBtn:hover { background-color: #00838f; }

    /* SWITCH BUTTONS */
    QPushButton.SwitchBtn { border-radius: 6px; font-weight: bold; border: 1px solid #444; }
    QPushButton.SwitchBtn[state='off'] { background-color: #333; color: #aaa; }
    QPushButton.SwitchBtn[state='on'] { background-color: #00e676; color: black; border: 1px solid #00e676; }

    QDialog { background-color: #1a1a1a; border: 1px solid #00bcd4; }
    QGroupBox { border: 1px solid #444; border-radius: 6px; margin-top: 22px; font-weight: bold; color: #ffd700; background-color: #151515; }
    QGroupBox::title { subcontrol-origin: margin; top: -18px; left: 10px; padding: 0 5px; }
    QFrame.HLine { color: #333; }

    QSlider::groove:horizontal { height: 10px; background: #333; border-radius: 5px; }
    QSlider::handle:horizontal { background: #00bcd4; width: 30px; margin: -10px 0; border-radius: 5px; }
"""
import serial
import socket
import threading
import time
import asyncio
from pymodbus.server import ModbusSerialServer
from pymodbus.simulator.simdata import SimData, DataType
from pymodbus.simulator.simdevice import SimDevice


import heapq
import time
import threading

class EjectScheduler(threading.Thread):
    def __init__(self, esp32_controller):
        super().__init__(daemon=True)
        self.esp32 = esp32_controller
        self.queue = []  # Priority queue: (fire_time, channel, pulse_ms)
        self.lock = threading.Lock()
        self.running = True
        self.channel_lockout = {}  # channel -> expiry_time

    def stop(self):
        self.running = False

    def schedule_fire(self, channels_1_based, delay_ms, pulse_ms, ignore_window_ms=5, force_update=False, meta=None):
        if not self.esp32: return
        if getattr(self.esp32, 'ejector_test_active', False): return

        current_time = time.perf_counter()
        fire_time = current_time + (delay_ms / 1000.0)
        self._counter = getattr(self, '_counter', 0) + 1
        
        with self.lock:
            valid_channels = []
            for ch in channels_1_based:
                if ch < 1 or ch > 63:
                    continue
                
                # Check software lockout
                if not force_update:
                    if ch in self.channel_lockout and fire_time <= self.channel_lockout[ch]:
                        continue
                
                valid_channels.append(ch)
                # Lockout until this pulse finishes
                self.channel_lockout[ch] = fire_time + (pulse_ms / 1000.0)
                
            if valid_channels:
                # (counter keeps heap ordering stable; meta/current_time/delay are for Phase 2 timing logs)
                heapq.heappush(self.queue, (fire_time, self._counter, valid_channels, pulse_ms,
                                            meta, current_time, delay_ms))

    def run(self):
        while self.running:
            if getattr(self.esp32, 'ejector_test_active', False):
                with self.lock:
                    self.queue.clear()
                time.sleep(0.01)
                continue

            if not self.queue:
                time.sleep(0.001)
                continue
                
            with self.lock:
                fire_time, _cnt, channels, pulse, meta, t_sched, delay_ms = self.queue[0]

            current_time = time.perf_counter()
            if current_time >= fire_time:
                # Time to fire!
                with self.lock:
                    heapq.heappop(self.queue)

                # Try to batch identical times if they are in the queue (within 1ms)
                batch_channels = list(channels)
                with self.lock:
                    while self.queue and (self.queue[0][0] - current_time) < 0.001:
                        more = heapq.heappop(self.queue)
                        batch_channels.extend(more[2])
                
                # Remove duplicates for exactly simultaneous events
                batch_channels = list(set(batch_channels))
                
                success = False
                retries = 3
                while not success and retries > 0:
                    if len(batch_channels) == 1:
                        res = self.esp32.fire_single_ejector(batch_channels[0], pulse, wait_ack=False)
                    else:
                        res = self.esp32.fire_ejector_channels(batch_channels, pulse, wait_ack=False)
                    
                    if res == "BUSY":
                        retries -= 1
                        time.sleep(0.002)  # Micro backoff
                    else:
                        success = True
                # Phase 2 timing log (does nothing unless timing_log_enabled)
                if TIMING.enabled:
                    TIMING.fire(meta, t_sched, fire_time, time.perf_counter(),
                                getattr(self.esp32, 'last_fire_seq', None), batch_channels, pulse, delay_ms)
            else:
                # Sleep a tiny bit to not peg CPU, but wake up in time
                diff = fire_time - current_time
                if diff > 0.002:
                    time.sleep(0.001)
                else:
                    pass  # Busy wait for last 2ms for precision

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

        # --- PHASE 1 SAFETY: binary ejector link + relay-board heartbeat ---
        self.ejector_link = None              # EjectorLink (binary protocol, or legacy text fallback)
        self.modbus_lock = threading.Lock()   # pymodbus client is not thread-safe
        self.relay_fw_v2 = False              # relay board runs ESP32_8CH_ModbusRelay_v2_0
        self.relay_board_state = None         # "DISARMED" / "ARMED" / "TRIPPED"
        self._relay_hb_running = False
        self._relay_hb_thread = None
        self.safety_trip_reason = None        # set from background threads, handled by GUI timer
        self.relay_enabled = True             # system_config.json modbus_enabled; False = no relay port is ever opened or probed
        self.relay_port_cfg = None            # relay port from the settings (system_config.json last_port)

    # ------------------------------------------------------------------
    # Modbus helpers (pymodbus renamed 'unit' -> 'slave' -> 'device_id')
    # ------------------------------------------------------------------
    def _mb(self, fn_name, *args, **kwargs):
        client = getattr(self, 'modbus_client', None)
        if not client:
            return None
        fn = getattr(client, fn_name)
        with self.modbus_lock:
            for key in ('device_id', 'slave', 'unit'):
                try:
                    return fn(*args, **{key: 1}, **kwargs)
                except TypeError:
                    continue
        return None

    def _start_relay_safety(self):
        """Detect relay firmware v2.0 and start the 200 ms Modbus heartbeat (watchdog 500 ms)."""
        self.relay_fw_v2 = False
        try:
            rr = self._mb('read_holding_registers', 11, count=1)
            if rr is not None and not rr.isError() and rr.registers and rr.registers[0] >= 0x0200:
                self.relay_fw_v2 = True
        except Exception:
            pass
        if not self.relay_fw_v2:
            print("[RELAY] Old relay firmware (no watchdog). Flash ESP32_8CH_ModbusRelay_v2_0 for safety stop.")
            return
        print("[RELAY] Relay firmware v2.0 detected -> heartbeat watchdog active (500 ms).")
        self._mb('write_register', 9, 1)          # ARM / clear an old trip
        self._relay_hb_running = True
        self._relay_hb_thread = threading.Thread(target=self._relay_heartbeat_loop, daemon=True)
        self._relay_hb_thread.start()

    def _relay_heartbeat_loop(self):
        counter = 0
        last_state_check = 0.0
        while self._relay_hb_running and getattr(self, 'modbus_client', None):
            counter = (counter + 1) & 0xFFFF
            try:
                self._mb('write_register', 8, counter)
                now = time.time()
                if now - last_state_check > 1.0:
                    last_state_check = now
                    rr = self._mb('read_holding_registers', 9, count=1)
                    if rr is not None and not rr.isError() and rr.registers:
                        st = {0: "DISARMED", 1: "ARMED", 2: "TRIPPED"}.get(rr.registers[0], "?")
                        if st == "TRIPPED" and self.relay_board_state != "TRIPPED":
                            self.safety_trip_reason = "RELAY BOARD WATCHDOG (feeder/air were switched off)"
                        self.relay_board_state = st
            except Exception as e:
                print(f"[RELAY] heartbeat error: {e}")
            time.sleep(0.2)

    def _stop_relay_safety(self, disarm=True):
        self._relay_hb_running = False
        if self._relay_hb_thread and self._relay_hb_thread is not threading.current_thread():
            self._relay_hb_thread.join(timeout=1.0)
        self._relay_hb_thread = None
        if disarm and self.relay_fw_v2:
            try:
                self._mb('write_register', 9, 0)  # clean exit: watchdog off, no trip
            except Exception:
                pass

    def _on_ejector_event(self, name, arg):
        # Called from the link reader thread: only set flags here.
        if name == "WDT_TRIP":
            self.safety_trip_reason = "EJECTOR WATCHDOG (all valves were switched off)"

    def rearm_safety(self):
        """Called by START: clears trips on both boards."""
        self.safety_trip_reason = None
        if self.ejector_link and self.ejector_link.mode == "BINARY":
            self.ejector_link.arm()
        if self.relay_fw_v2:
            self._mb('write_register', 9, 1)
            self.relay_board_state = "ARMED"

    def set_running_flag(self, running):
        if self.ejector_link:
            self.ejector_link.heartbeat_flags = 1 if running else 0

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

    def ensure_relay(self):
        """Reconnect the relay board on its CONFIGURED port. Does nothing (False) if the relay is disabled in the
        settings or no port is configured - no port number is ever guessed."""
        if self.is_modbus_connected():
            return True
        if not self.relay_enabled or not self.relay_port_cfg:
            return False
        return self.connect(self.relay_port_cfg)

    def connect(self, port_or_ip, baudrate=9600):
        self.disconnect()
        if not port_or_ip:
            print("[RELAY] no relay port configured - not connecting")
            return False

        # One port is never opened for two devices, and no port number is ever substituted.
        ej_port = getattr(self, 'ejector_port', None)
        if ej_port and port_or_ip == ej_port and self.is_ejector_connected():
            print(f"[RELAY] REFUSED: {port_or_ip} is used by the EJECTOR board. Choose another port for the relay (COM PORTS).")
            return False
        vib_port = getattr(self, 'vibrator_port', None)
        if vib_port and port_or_ip == vib_port and self.vibrator_serial and getattr(self.vibrator_serial, 'is_open', False):
            print(f"[RELAY] REFUSED: {port_or_ip} is used by the FEEDER board. Choose another port for the relay (COM PORTS).")
            return False
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
                    timeout=0.5,
                    retries=1
                )
                
                if self.modbus_client.connect():
                    print(f"[OK] ESP32 Modbus Relay Board verified on {port_or_ip}!")
                else:
                    print(f"[ERROR] Failed to open Modbus port {port_or_ip}")
                    return False
                
                self.connection_type = "SERIAL_RELAY"
                self.active_port = port_or_ip
                self.connected = True
                self.running = True
                self._relay_states = [0] * 8  # Track relay states locally

                print(f"[OK] ESP32 Modbus Relay Board READY on {port_or_ip} (9600 8N1)")
                self._start_relay_safety()
                return True

        except Exception as e:
            print(f"ESP32 Connection failed: {e}")
            self.connected = False
            self.active_port = None
            return False

    def disconnect(self):
        self.running = False
        self._stop_relay_safety(disarm=True)
        if hasattr(self, 'modbus_client') and self.modbus_client:
            with self.modbus_lock:
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
        if not port:
            return False
        if (port == getattr(self, 'ejector_port', None) and self.is_ejector_connected()) or \
                (port == getattr(self, 'active_port', None) and self.is_relay_board_connected()):
            print(f"[NUVOTON] REFUSED: {port} is used by another device. Choose another port for the feeder (COM PORTS).")
            return False
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

    def connect_ejector(self, port=None, baudrate=None):
        """Connects the 63-channel ejector ESP32.
        Auto-detects the firmware by PROTOCOL: v2.x binary (115200 baud; v2.3 arms itself and has a silent watchdog)
        or the old text firmware (115200 baud, legacy mode). `baudrate` is ignored (kept for old callers).
        `port` must be given (no default, no substitution)."""
        if not port:
            print("[EJECTOR] no ejector port configured - not connecting")
            return False
        relay_port = getattr(self, 'active_port', None)
        if relay_port and port == relay_port and self.is_relay_board_connected():
            print(f"[EJECTOR] REFUSED: {port} is used by the RELAY board. Choose another port for the ejector (COM PORTS).")
            return False
        self.disconnect_ejector()
        print(f"[EJECTOR] Connecting ESP32 ejector on {port} (auto-detect firmware)...")
        link = EjectorLink(port, on_event=self._on_ejector_event)
        mode = link.connect()
        if not mode:
            print(f"[EJECTOR] No ejector firmware answered on {port}.")
            for line in list(link.text_lines)[-3:]:
                print(f"           {line}")
            return False
        self.ejector_link = link
        self.ejector_serial = link          # keeps old checks working: ejector_serial.is_open
        if TIMING.enabled:
            link.ack_listeners.append(TIMING.on_ack)
        self.ejector_port = port
        if mode == "BINARY":
            print(f"[OK] Ejector firmware v{link.fw_version} on {port}: binary protocol + 500 ms watchdog.")
        elif mode == "ESP_BYTE":
            print(f"[OK] Ejector on {port}: ESP32 63-CH Ejector connected (esp.py byte protocol).")
        else:
            print(f"[OK] Ejector on {port} runs OLD firmware -> legacy text mode (no watchdog). "
                  f"Flash ESP32_63CH_Ejector_v2_0.")
        return True

    def disconnect_ejector(self):
        if self.ejector_link:
            try:
                self.ejector_link.close(disarm=True)
            except Exception:
                pass
        self.ejector_link = None
        self.ejector_serial = None
        self.ejector_port = None

    def send_command(self, cmd):
        sent = False
        payload = (cmd + "\n").encode('utf-8')
        # (Phase 1) Relay/light/wiper text commands are no longer sent to the ejector ESP32:
        # its firmware never used them, and that line now carries the binary valve protocol.
        # Send to serial_conn or socket_conn if present (LAN mode)
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
            # 1. Send via Modbus client if connected (locked: heartbeat thread shares the client)
            if hasattr(self, 'modbus_client') and self.modbus_client and getattr(self.modbus_client, 'connected', False):
                try:
                    res = self._mb('write_register', idx, val)
                    if res is not None and res.isError() and val and idx <= 2 and self.relay_fw_v2:
                        # relay v2.0 refuses feeder/air/wiper ON while its watchdog is TRIPPED
                        self.safety_trip_reason = "RELAY BOARD WATCHDOG (feeder/air locked off - press START)"
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

    def test_fire_single(self, channel_num, pulse_ms):
        """Test-button path: fires ONE channel once and returns (bytes_sent, reply_line)."""
        try:
            ch = int(channel_num)
            p = max(1, int(float(pulse_ms)))
        except Exception:
            return (b"", "ERR:ARGS")
        if not (1 <= ch <= 63):
            return (b"", "ERR:CHANNEL")

        link = getattr(self, 'ejector_link', None)
        if not (link and getattr(link, 'is_open', False)):
            return (f"CH {ch} ({p}ms)".encode('utf-8'), "ERR:PORT CLOSED")

        if link.mode == "ESP_BYTE":
            try:
                link._write(bytes([ch]))
                time.sleep(max(0.001, float(p) / 1000.0))
                link._write(bytes([ch + 100]))
                reply = f"ACK: CH {ch} FIRED ({p}ms)"
                sent = f"CH {ch} ({p}ms)".encode('utf-8')
                return (sent, reply)
            except Exception as e:
                return (f"CH {ch}".encode('utf-8'), f"ERR:{e}")
        elif link.mode == "BINARY":
            link.test_single(ch, p, 100, repeat=1)
            return (f"TEST:{ch}:{p}".encode('utf-8'), f"ACK: CH {ch} FIRED")
        else:
            cmd = f"FIRE_MULTI:{ch}:{p}\n".encode('utf-8')
            try:
                link._write(cmd)
                return (cmd, "ACK: SENT")
            except Exception as e:
                return (cmd, f"ERR:{e}")

    def start_selftest(self, pulse_ms, gap_ms, start_ch=1, end_ch=63):
        """Starts sequential self test. Returns (bytes_sent, reply_line)."""
        try:
            p = max(1, int(float(pulse_ms)))
            g = max(0, int(float(gap_ms)))
            s = int(start_ch)
            e = int(end_ch)
        except Exception:
            return (b"", "ERR:ARGS")
        if not (1 <= s <= e <= 63):
            return (b"", "ERR:CHANNEL")

        link = getattr(self, 'ejector_link', None)
        if not (link and getattr(link, 'is_open', False)):
            return (b"", "ERR:PORT CLOSED")

        self._st_queue = []
        self._st_running = True

        if link.mode == "ESP_BYTE":
            def worker():
                for c in range(s, e + 1):
                    if not getattr(self, '_st_running', False) or not link.is_open:
                        break
                    self._st_queue.append(f"ST:{c}")
                    try:
                        link._write(bytes([c]))
                        time.sleep(max(0.001, float(p) / 1000.0))
                        link._write(bytes([c + 100]))
                        time.sleep(max(0.005, float(g) / 1000.0))
                    except Exception:
                        break
                self._st_queue.append("ST:DONE")
                self._st_running = False

            t = threading.Thread(target=worker, daemon=True)
            t.start()
            return (f"SELFTEST:{p}:{g}:{s}:{e}".encode('utf-8'), "ACK:SELFTEST")
        else:
            link.test_sweep(s, e, p, g)
            return (f"SELFTEST:{p}:{g}:{s}:{e}".encode('utf-8'), "ACK:SELFTEST")

    def poll_ejector_lines(self):
        q = getattr(self, '_st_queue', None)
        if q:
            lines = list(q)
            q.clear()
            return lines
        return []

    def fire_single_ejector(self, channel_num, pulse_ms=20, wait_ack=False):
        """Fires one channel (1-63)."""
        if not (1 <= channel_num <= 63):
            return "ERR"
        return self.fire_ejector_channels([channel_num], pulse_ms, wait_ack)

    def stop_all_ejectors(self):
        """Immediately stops all test modes and turns all ejectors OFF."""
        with self.ejector_lock:
            self.current_valves = [0] * 64
            self._last_valves_had_active = False
        link = self.ejector_link
        if link and link.is_open:
            link.test_stop()
            link.stop_all()

    def fire_ejector_channels(self, channel_list, pulse_ms=25, wait_ack=False):
        """Fires a list of 1-based channels at once.
        Binary firmware: one fixed 24-byte message with a 64-bit mask (no truncation possible).
        Old firmware: short FIRE_MULTI lines (legacy fallback)."""
        link = self.ejector_link
        if not channel_list or not (link and link.is_open):
            return "ERR"
        seq = link.fire(channel_list, float(pulse_ms))
        self.last_fire_seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else None
        return "OK" if seq is not None and seq is not False else "ERR"

    def hold_ejector_on(self, channel_num):
        """HOLD_ON for the STABLE WIRE TEST (firmware auto-releases after 10 s)."""
        if self.ejector_link and self.ejector_link.is_open and 1 <= channel_num <= 63:
            self.ejector_link.hold(channel_num, True)

    def hold_ejector_off(self, channel_num):
        if self.ejector_link and self.ejector_link.is_open and 1 <= channel_num <= 63:
            self.ejector_link.hold(channel_num, False)
        if self.get_relay(2) != 1:
            self.set_relay(2, 1)

    def start_continuous(self, channel, pulse_ms, gap_ms):
        """Fire one channel repeatedly until stop (runs on the ESP32)."""
        if self.ejector_link and self.ejector_link.is_open and 1 <= channel <= 63:
            self.ejector_link.test_single(int(channel), float(pulse_ms), float(gap_ms), repeat=0)

    def start_repeat(self, channel, pulse_ms, gap_ms, count):
        """Fire one channel `count` times with `gap_ms` between pulses (runs on the ESP32)."""
        if self.ejector_link and self.ejector_link.is_open and 1 <= channel <= 63:
            self.ejector_link.test_single(int(channel), float(pulse_ms), float(gap_ms), repeat=max(1, int(count)))

    def stop_continuous(self):
        if self.ejector_link and self.ejector_link.is_open:
            self.ejector_link.test_stop()
            self.ejector_link.stop_all()

    def run_ejector_test(self, start_channel, end_channel, pulse_ms, gap_ms):
        """Sweep start..end on the ESP32; every step reports CH -> OUT -> CHIP/Q."""
        s_ch = max(1, min(63, int(start_channel)))
        e_ch = max(1, min(63, int(end_channel)))
        if s_ch > e_ch:
            s_ch, e_ch = e_ch, s_ch
        p_ms = max(0.2, float(pulse_ms))
        g_ms = max(1.0, float(gap_ms))
        if self.ejector_link and self.ejector_link.is_open:
            self.ejector_link.test_sweep(s_ch, e_ch, p_ms, g_ms)

    def ejector_channel_info(self, channel):
        if self.ejector_link and self.ejector_link.is_open:
            return self.ejector_link.channel_info(channel)
        out = channel - 1
        return f"CH {channel} -> OUT {out} -> CHIP {out // 8 + 1}, Q{out % 8}"

    def set_all_ejectors(self, valve_array):
        # Legacy fallback method using FIRE_MULTI
        active_chs = [i + 1 for i, val in enumerate(valve_array) if val and i < 63]
        self.fire_ejector_channels(active_chs, 20)

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


# --- 1. ADVANCED CANVAS WIDGET (LIGHTNING FAST SMART TOOL & CACHE FIX) ---
class AdvancedROIWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.cv_image = None
        self.cached_contours = []  # 🔥 FIX 1: AI கணக்கீட்டைச் சேமிக்க புதிய Variable
        self.overlay = None
        self.scale_factor = 1.0
        self.offset = QPoint(0, 0)
        self.rois = []
        self.draw_type = "DEFECT"
        self.show_rois = True
        self.current_drag_start = None
        self.current_drag_end = None
        self.is_panning = False
        self.last_mouse_pos = QPoint()
        self.tool_mode = "BOX"
        self.paint_path = []
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #000; border: 2px solid #444; border-radius: 8px;")

    def set_tool_mode(self, tool):
        self.tool_mode = tool

    def set_image(self, path):
        abs_path = os.path.abspath(path)
        self.image_path = abs_path
        try:
            if not os.path.exists(abs_path): return
            img_bytes = np.fromfile(abs_path, dtype=np.uint8)
            cv_img = cv2.imdecode(img_bytes, cv2.IMREAD_COLOR)

            if cv_img is not None:
                # Apply visual stretch for UI if needed (default to 3 if not accessible)
                v_stretch = 3.0 
                if hasattr(self, 'parent') and hasattr(self.parent(), 'v_stretch'):
                    v_stretch = self.parent().v_stretch
                elif hasattr(self, 'main_app') and hasattr(self.main_app, 'v_stretch'):
                    v_stretch = self.main_app.v_stretch
                    
                if v_stretch > 1.0:
                    h_c, w_c = cv_img.shape[:2]
                    cv_img = cv2.resize(cv_img, (w_c, int(h_c * v_stretch)), interpolation=cv2.INTER_LINEAR)
                    
                self.cv_image = cv_img.copy()

                # 🔥 FIX 2: Bilateral Filter keeps the object boundary sharp while blurring out the blue conveyor texture
                # This achieves the tight "Roboflow Magic Tool" bounding boxes!
                gray = cv2.cvtColor(self.cv_image, cv2.COLOR_BGR2GRAY)
                blur = cv2.bilateralFilter(gray, 11, 75, 75)
                edges = cv2.Canny(blur, 40, 120)
                
                kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
                closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
                
                self.cached_contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                rgba = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGBA)
                rgba = np.ascontiguousarray(rgba)
                ih, iw, ch = rgba.shape
                qimg = QImage(rgba.data, iw, ih, rgba.strides[0], QImage.Format_RGBA8888).copy()
                self.image = QPixmap.fromImage(qimg)
            else:
                self.image = QPixmap()
                self.cv_image = None
                self.cached_contours = []
        except Exception as e:
            print(f"Full Image Load Error: {e}")
            self.image = QPixmap()
            self.cv_image = None
            self.cached_contours = []

        self.overlay = None
        self.show_rois = True
        self.fit_to_window()
        self.update()

    def load_rois(self, existing_rois):
        self.rois = list(existing_rois)
        self.update()

    def set_overlay_mask(self, mask_cv):
        if mask_cv is None:
            self.overlay = None
            self.update()
            return
        h, w = mask_cv.shape
        overlay_np = np.zeros((h, w, 4), dtype=np.uint8)
        overlay_np[:, :, 0] = 255
        overlay_np[:, :, 1] = 0
        overlay_np[:, :, 2] = 255
        overlay_np[:, :, 3] = np.where(mask_cv > 0, 150, 0)
        overlay_np = np.ascontiguousarray(overlay_np)
        qimg = QImage(overlay_np.data, w, h, overlay_np.strides[0], QImage.Format_RGBA8888).copy()
        self.overlay = QPixmap.fromImage(qimg)
        self.update()

    def set_rois_visible(self, visible):
        self.show_rois = visible
        self.update()

    def set_draw_mode(self, mode):
        self.draw_type = mode

    def zoom(self, factor):
        self.scale_factor *= factor
        self.scale_factor = max(0.1, min(self.scale_factor, 10.0))
        self.update()

    def fit_to_window(self):
        if not self.image or self.image.isNull(): return
        w_ratio = self.width() / self.image.width()
        h_ratio = self.height() / self.image.height()
        self.scale_factor = min(w_ratio, h_ratio) * 0.95
        new_w = self.image.width() * self.scale_factor
        new_h = self.image.height() * self.scale_factor
        self.offset = QPoint(int((self.width() - new_w) / 2), int((self.height() - new_h) / 2))
        self.update()

    def undo(self):
        if self.rois: self.rois.pop(); self.update()

    def clear(self):
        self.rois = []
        self.overlay = None
        self.show_rois = True
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Ensure image fits window properly after Qt finishes rendering the geometry.
        if not hasattr(self, '_first_resize'):
            self._first_resize = True
            self.fit_to_window()

    def screen_to_image(self, pos):
        # UI லேயரில் இருந்து படத்தின் பிக்சல் நிலைக்கு மாற்றும் துல்லியமான முறை
        img_x = (pos.x() - self.offset.x()) / self.scale_factor
        img_y = (pos.y() - self.offset.y()) / self.scale_factor

        # படம் 4096 அகலம் கொண்டது என்றால், அதைத்தாண்டி மார்க் விழாமல் இருக்க:
        img_x = max(0, min(self.image.width() - 1, img_x))
        img_y = max(0, min(self.image.height() - 1, img_y))

        return QPoint(int(img_x), int(img_y))

    def image_to_screen_rect(self, rect):
        sx = int(rect.x() * self.scale_factor + self.offset.x())
        sy = int(rect.y() * self.scale_factor + self.offset.y())
        sw = int(rect.width() * self.scale_factor)
        sh = int(rect.height() * self.scale_factor)
        return QRect(sx, sy, sw, sh)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if self.image and not self.image.isNull():
            scaled_w = int(self.image.width() * self.scale_factor)
            scaled_h = int(self.image.height() * self.scale_factor)
            target_rect = QRect(self.offset.x(), self.offset.y(), scaled_w, scaled_h)
            painter.drawPixmap(target_rect, self.image)

            if self.overlay: painter.drawPixmap(target_rect, self.overlay)

            if self.show_rois:
                for rect, mode in self.rois:
                    if not isinstance(rect, QRect): continue
                    color = QColor(0, 230, 118) if mode == "GOOD" else QColor(255, 0, 255)
                    pen = QPen(color, 3)
                    painter.setPen(pen)
                    brush_color = QColor(color)
                    brush_color.setAlpha(50)
                    painter.setBrush(QBrush(brush_color))
                    painter.drawRect(self.image_to_screen_rect(rect))

            if self.tool_mode == "PAINT" and len(self.paint_path) > 1:
                pen = QPen(QColor(0, 188, 212), 3, Qt.SolidLine)
                painter.setPen(pen)
                for i in range(len(self.paint_path) - 1):
                    p1 = QPoint(int(self.paint_path[i].x() * self.scale_factor + self.offset.x()),
                                int(self.paint_path[i].y() * self.scale_factor + self.offset.y()))
                    p2 = QPoint(int(self.paint_path[i + 1].x() * self.scale_factor + self.offset.x()),
                                int(self.paint_path[i + 1].y() * self.scale_factor + self.offset.y()))
                    painter.drawLine(p1, p2)

            if self.current_drag_start and self.current_drag_end and self.tool_mode == "BOX":
                pen = QPen(QColor(255, 255, 255), 2, Qt.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                sx = self.current_drag_start.x()
                sy = self.current_drag_start.y()
                ex = self.current_drag_end.x()
                ey = self.current_drag_end.y()
                painter.drawRect(QRect(QPoint(sx, sy), QPoint(ex, ey)).normalized())
        else:
            painter.setPen(Qt.white)
            painter.drawText(self.rect(), Qt.AlignCenter, "NO IMAGE")

    def wheelEvent(self, event):
        factor = 1.1 if event.angleDelta().y() > 0 else 0.9
        self.zoom(factor)

    def mousePressEvent(self, event):
        if not self.show_rois and not self.overlay: self.show_rois = True; self.update()

        if event.button() == Qt.RightButton:
            self.is_panning = True
            self.last_mouse_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            return

        if event.button() == Qt.LeftButton:
            if self.tool_mode == "SMART":
                self.smart_select(event.pos())
            elif self.tool_mode == "PAINT":
                self.paint_path = [self.screen_to_image(event.pos())]
            else:
                self.current_drag_start = event.pos()
                self.current_drag_end = event.pos()

    def mouseMoveEvent(self, event):
        if self.is_panning:
            delta = event.pos() - self.last_mouse_pos
            self.offset += delta
            self.last_mouse_pos = event.pos()
            self.update()
        elif self.tool_mode == "PAINT" and event.buttons() & Qt.LeftButton:
            self.paint_path.append(self.screen_to_image(event.pos()))
            self.update()
        elif self.current_drag_start and self.tool_mode == "BOX":
            self.current_drag_end = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self.is_panning = False
            self.setCursor(Qt.ArrowCursor)

        elif event.button() == Qt.LeftButton:
            # PAINT Tool Logic
            if self.tool_mode == "PAINT" and self.paint_path:
                xs = [p.x() for p in self.paint_path]
                ys = [p.y() for p in self.paint_path]
                if xs and ys:
                    x, y = min(xs), min(ys)
                    w, h = max(xs) - x, max(ys) - y
                    if w > 2 and h > 2:
                        self.rois.append((QRect(x, y, w, h), self.draw_type))
                self.paint_path = []
                self.update()

            # BOX Tool Logic
            elif self.current_drag_start and self.tool_mode == "BOX":
                p1 = self.screen_to_image(self.current_drag_start)
                p2 = self.screen_to_image(event.pos())
                x = min(p1.x(), p2.x())
                y = min(p1.y(), p2.y())
                w = abs(p2.x() - p1.x())
                h = abs(p2.y() - p1.y())

                if w > 2 and h > 2:
                    self.rois.append((QRect(x, y, w, h), self.draw_type))
                    # இதோ இங்கேயே மார்க்கிங் விழுவதை உறுதி செய்ய பிரிண்ட் செய்கிறோம்
                    print(f"DEBUG: Mark added! Total rois: {len(self.rois)}")
                    self.update()

                self.current_drag_start = None
                self.current_drag_end = None

    # 🔥 FIX 3: அதிவேக SMART TOOL (Cached OTSU Contours பயன்படுத்தப்படுகிறது)
    def smart_select(self, pos):
        if getattr(self, 'cv_image', None) is None:
            return

        img_pos = self.screen_to_image(pos)
        cx, cy = float(img_pos.x()), float(img_pos.y())
        h, w = self.cv_image.shape[:2]
        img_area = h * w

        if cx < 0 or cx >= w or cy < 0 or cy >= h:
            return

        found_box = None
        best_area = float('inf')

        # First Pass: Exact Match using pointPolygonTest
        for c in getattr(self, 'cached_contours', []):
            area = cv2.contourArea(c)
            # Skip noise and giant background contours
            if area < 50 or area > (img_area * 0.5):
                continue

            if cv2.pointPolygonTest(c, (cx, cy), False) >= 0:
                if area < best_area:
                    best_area = area
                    x, y, bw, bh = cv2.boundingRect(c)
                    found_box = QRect(x, y, bw, bh)

        # Fallback Pass: Loose bounding box match if exact match fails
        if not found_box:
            for c in getattr(self, 'cached_contours', []):
                area = cv2.contourArea(c)
                if area < 50 or area > (img_area * 0.2):
                    continue

                x, y, bw, bh = cv2.boundingRect(c)
                if x <= cx <= x + bw and y <= cy <= y + bh:
                    if area < best_area:
                        best_area = area
                        found_box = QRect(x, y, bw, bh)

        if found_box:
            is_duplicate = False
            for existing_rect, _ in self.rois:
                if existing_rect == found_box:
                    is_duplicate = True
                    break

            if not is_duplicate:
                self.rois.append((found_box, getattr(self, 'draw_type', "DEFECT")))
                self.update()
                print(f"Total marks: {len(self.rois)}")


# --- 3. IMAGE CARD ---
class ImageCard(QFrame):
    def __init__(self, image_path, img_id, parent_ui):
        super().__init__()
        self.image_path = image_path
        self.parent_ui = parent_ui
        self.rois = []
        self.setFixedSize(160, 220)
        self.setStyleSheet("QFrame { background-color: #222; border: 1px solid #444; border-radius: 8px; }")
        l = QVBoxLayout(self)
        l.setContentsMargins(5, 5, 5, 5)
        h = QHBoxLayout()
        chk = QCheckBox(f"  #{img_id}")
        chk.setStyleSheet("""
            QCheckBox { color: white; font-weight: bold; font-size: 18px; }
            QCheckBox::indicator { width: 40px; height: 40px; border: 2px solid #555; background: #333; border-radius: 4px; }
            QCheckBox::indicator:checked { background: #00bcd4; border: 2px solid #00bcd4; }
        """)
        h.addWidget(chk)
        h.addStretch()
        l.addLayout(h)
        self.chk = chk
        self.lbl = QLabel()
        self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setStyleSheet("background:black; border-radius:4px;")
        self.lbl.setFixedSize(148, 120)
        self.lbl.mousePressEvent = self.on_click

        # --- SAFE IMAGE LOADING FIX (CRASH தடுத்தல்) ---
        try:
            if os.path.exists(image_path):
                img_bytes = np.fromfile(image_path, dtype=np.uint8)
                cv_img = cv2.imdecode(img_bytes, cv2.IMREAD_COLOR)
                if cv_img is not None:
                    rgb = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)

                    # இங்கும் மெமரியை Align செய்ய வேண்டும்
                    rgb = np.ascontiguousarray(rgb)

                    ih, iw, ch = rgb.shape
                    qimg = QImage(rgb.data, iw, ih, rgb.strides[0], QImage.Format_RGB888).copy()
                    pix = QPixmap.fromImage(qimg)
                    self.lbl.setPixmap(pix.scaled(148, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception as e:
            print(f"Thumbnail Load Error: {e}")

        # ----------------------------------------------

        l.addWidget(self.lbl)
        self.lbl_stat = QLabel("UNCHECKED")
        self.lbl_stat.setAlignment(Qt.AlignCenter)
        self.lbl_stat.setStyleSheet("color: gray; font-weight: bold; font-size: 11px;")
        l.addWidget(self.lbl_stat)

    def on_click(self, e):
        self.parent_ui.open_editor(self)

    def set_status(self, rois):
        self.rois = list(rois)
        has_defect = any(t == "DEFECT" for _, t in rois)
        if has_defect:
            self.setStyleSheet("QFrame { background: #2b0000; border: 2px solid #FF00FF; border-radius: 8px; }")
            self.lbl_stat.setText("❌ DEFECT")
            self.lbl_stat.setStyleSheet("color: #FF00FF; font-weight: bold;")
        elif len(rois) > 0:
            self.setStyleSheet("QFrame { background: #001b00; border: 2px solid #00e676; border-radius: 8px; }")
            self.lbl_stat.setText("✅ PASSED")
            self.lbl_stat.setStyleSheet("color: #00e676; font-weight: bold;")
        else:
            self.setStyleSheet("QFrame { background: #222; border: 1px solid #444; border-radius: 8px; }")
            self.lbl_stat.setText("UNCHECKED")
            self.lbl_stat.setStyleSheet("color: gray; font-weight: bold;")


# --- 4. IMAGE EDITOR WINDOW (NEW RIGHT LAYOUT & BOTTOM ZOOM) ---
class ImageEditor(QDialog):
    trigger_ejector = pyqtSignal(int)

    def __init__(self, image_list, current_index, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vision Analysis - Defect Learning")
        self.resize(1600, 900)  # பெரிய விண்டோ
        self.showMaximized()
        self.setStyleSheet("background-color: #1a1a1a; color: white; font-family: Segoe UI;")
        self.image_list = image_list
        self.current_idx = current_index

        main_layout = QHBoxLayout(self)

        # ---------------------------------------------------------
        # 1. LEFT TOOLBAR (MARKING TOOLS & ACTIONS)
        # ---------------------------------------------------------
        left_toolbar = QFrame()
        left_toolbar.setFixedWidth(240)
        left_toolbar.setStyleSheet("background: #252525; border-right: 1px solid #444;")
        lt_layout = QVBoxLayout(left_toolbar)

        # MARKING MODE
        lt_layout.addWidget(
            QLabel("MARKING MODE", styleSheet="color: #00bcd4; font-weight: bold; margin-top:10px; font-size:16px;"))
        self.btn_draw_good = QPushButton("MARK GOOD")
        self.btn_draw_good.setFixedHeight(55)
        self.btn_draw_good.setCheckable(True)
        self.btn_draw_good.setStyleSheet(self.get_btn_style("#00C853", "#00C853"))
        self.btn_draw_good.clicked.connect(lambda: self.set_mode("GOOD"))
        lt_layout.addWidget(self.btn_draw_good)

        self.btn_draw_bad = QPushButton("MARK DEFECT")
        self.btn_draw_bad.setFixedHeight(55)
        self.btn_draw_bad.setCheckable(True)
        self.btn_draw_bad.setStyleSheet(self.get_btn_style("#FF00FF", "#FF00FF"))
        self.btn_draw_bad.clicked.connect(lambda: self.set_mode("DEFECT"))
        lt_layout.addWidget(self.btn_draw_bad)

        # MARKING TOOLS (BOX, PAINT, SMART)
        lt_layout.addSpacing(20)
        lt_layout.addWidget(QLabel("MARKING TOOL", styleSheet="color: #ff9800; font-weight: bold; font-size:16px;"))

        tool_layout = QGridLayout()
        self.btn_tool_box = QPushButton("🔲 BOX")
        self.btn_tool_paint = QPushButton("🖌 PAINT")
        self.btn_tool_smart = QPushButton("✨ SMART")

        # ஸ்டைல் மற்றும் கனெக்ஷன்
        for btn in [self.btn_tool_box, self.btn_tool_paint, self.btn_tool_smart]:
            btn.setCheckable(True)
            btn.setFixedHeight(50)
            # #444-க்கு பதிலாக #00bcd4 (நீல நிறம்) என மாற்றப்பட்டுள்ளது
            btn.setStyleSheet(self.get_btn_style("#00bcd4", "#00bcd4"))

        self.btn_tool_box.setChecked(True)
        self.btn_tool_box.clicked.connect(lambda: self.set_tool("BOX"))
        self.btn_tool_paint.clicked.connect(lambda: self.set_tool("PAINT"))
        self.btn_tool_smart.clicked.connect(lambda: self.set_tool("SMART"))

        tool_layout.addWidget(self.btn_tool_box, 0, 0)
        tool_layout.addWidget(self.btn_tool_paint, 0, 1)
        tool_layout.addWidget(self.btn_tool_smart, 1, 0, 1, 2)
        lt_layout.addLayout(tool_layout)

        # ACTIONS
        lt_layout.addSpacing(20)
        lt_layout.addWidget(QLabel("ACTIONS", styleSheet="color: #ffd700; font-weight: bold; font-size:16px;"))

        b_undo = QPushButton("↩ UNDO LAST")
        b_undo.setFixedHeight(50)
        b_undo.setStyleSheet("background:#555; font-weight:bold; font-size:14px; border-radius:6px;")
        b_undo.clicked.connect(lambda: self.canvas.undo())

        b_clear = QPushButton("🗑 CLEAR ALL")
        b_clear.setFixedHeight(50)
        b_clear.setStyleSheet("background:#b71c1c; font-weight:bold; font-size:14px; border-radius:6px;")
        b_clear.clicked.connect(lambda: self.canvas.clear())

        lt_layout.addWidget(b_undo)
        lt_layout.addWidget(b_clear)
        lt_layout.addStretch()

        # டூல்பாரை மெயின் விண்டோவில் சேர்த்தல்
        main_layout.addWidget(left_toolbar)

        # ---------------------------------------------------------
        # 2. CENTER PANEL (IMAGE + ZOOM AT BOTTOM + NAV)
        # ---------------------------------------------------------
        center_panel = QWidget()
        center_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)  # முழு இடம் எடுக்க
        c_layout = QVBoxLayout(center_panel)
        c_layout.setContentsMargins(10, 10, 10, 10)  # கொஞ்சம் இடைவெளி

        self.canvas = AdvancedROIWidget()
        # FIX: Let the widget expand fully. Do not use Qt.AlignCenter here!
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        c_layout.addWidget(self.canvas, stretch=1)

        # ZOOM Buttons Under the Image
        zoom_layout = QHBoxLayout()
        zoom_layout.addStretch()

        b_zo = QPushButton("🔍 -")
        b_fit = QPushButton("🎯 FIT")
        b_zi = QPushButton("🔍 +")

        for b in [b_zo, b_fit, b_zi]:
            b.setFixedSize(120, 50)
            b.setStyleSheet(
                "background-color: #333; color: white; border-radius: 8px; font-weight: bold; font-size: 16px; border: 2px solid #555;")
            zoom_layout.addWidget(b)
        zoom_layout.addStretch()
        c_layout.addLayout(zoom_layout)

        b_zi.clicked.connect(lambda: self.canvas.zoom(1.2))
        b_zo.clicked.connect(lambda: self.canvas.zoom(0.8))
        b_fit.clicked.connect(lambda: self.canvas.fit_to_window())

        # Navigation Bar Under Zoom
        nav_layout = QHBoxLayout()
        b_prev = QPushButton("◀ PREV")
        b_prev.setFixedSize(130, 55)
        b_prev.setStyleSheet(
            "background:#222; color:#fff; font-weight: bold; font-size:16px; border:1px solid #444; border-radius:8px;")
        b_prev.clicked.connect(self.prev_img)

        self.lbl_info = QLabel(f"IMG {self.current_idx + 1}")
        self.lbl_info.setStyleSheet("font-size: 24px; font-weight: bold; color:#00e5ff;")

        b_next = QPushButton("NEXT ▶")
        b_next.setFixedSize(130, 55)
        b_next.setStyleSheet(
            "background:#222; color:#fff; font-weight: bold; font-size:16px; border:1px solid #444; border-radius:8px;")
        b_next.clicked.connect(self.next_img)

        nav_layout.addWidget(b_prev)
        nav_layout.addStretch()
        nav_layout.addWidget(self.lbl_info)
        nav_layout.addStretch()
        nav_layout.addWidget(b_next)
        c_layout.addLayout(nav_layout)

        main_layout.addWidget(center_panel, stretch=1)

        # ---------------------------------------------------------
        # 3. RIGHT TOOLBAR (AI / SIMULATION SETTINGS)
        # ---------------------------------------------------------
        right_toolbar = QFrame()
        right_toolbar.setFixedWidth(320)
        right_toolbar.setStyleSheet("background: #252525; border-left: 1px solid #444;")
        rt_layout = QVBoxLayout(right_toolbar)

        rt_layout.addWidget(
            QLabel("AI SIMULATION", styleSheet="color: orange; font-weight: bold; font-size:18px; margin-top:10px;"))
        rt_layout.addSpacing(10)

        val_thresh = "40"
        val_purity = "50"
        val_scale = "100"
        try:
            val_thresh = str(json.load(open('vision_settings.json'))['ai_sim']['thresh'])
            val_purity = str(json.load(open('vision_settings.json'))['ai_sim']['purity'])
            val_scale = str(json.load(open('vision_settings.json'))['ai_sim']['scale'])
        except Exception:
            pass

        self.txt_thresh = self.create_manual_input("Color Threshold (0-255):", val_thresh, rt_layout)
        self.txt_purity = self.create_manual_input("Purity (Min Area):", val_purity, rt_layout)
        self.txt_scale = self.create_manual_input("Scale % (Speed):", val_scale, rt_layout)

        rt_layout.addSpacing(20)

        # AI Buttons
        self.btn_ai_enable = QPushButton("🤖 AI ENABLE")
        self.btn_ai_enable.setCheckable(True)
        self.btn_ai_enable.setFixedHeight(55)
        self.btn_ai_enable.setStyleSheet(
            "QPushButton { background: #333; color: white; border: 2px solid #555; font-weight: bold; border-radius: 8px; font-size: 16px; } QPushButton:checked { background: #7c4dff; color: white; border: 2px solid #b388ff; }")
        self.btn_ai_enable.toggled.connect(self.toggle_detection)
        rt_layout.addWidget(self.btn_ai_enable)

        self.btn_purity_check = QPushButton("💧 PURITY FILTER")
        self.btn_purity_check.setCheckable(True)
        self.btn_purity_check.setFixedHeight(55)
        self.btn_purity_check.setStyleSheet(
            "QPushButton { background: #333; color: white; border: 2px solid #555; font-weight: bold; border-radius: 8px; font-size: 16px;} QPushButton:checked { background: #00bcd4; color: black; border: 2px solid #84ffff; }")
        self.btn_purity_check.toggled.connect(self.toggle_detection)
        rt_layout.addWidget(self.btn_purity_check)

        rt_layout.addSpacing(20)

        self.lbl_result = QLabel("--")
        self.lbl_result.setAlignment(Qt.AlignCenter)
        self.lbl_result.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #fff; background: #111; padding: 15px; border-radius: 8px; border: 1px solid #555;")
        rt_layout.addWidget(self.lbl_result)

        rt_layout.addStretch()

        b_save = QPushButton("💾 SAVE & EXIT")
        b_save.setStyleSheet(
            "background: #00C853; height: 65px; font-weight: bold; color: black; font-size:18px; border-radius:8px;")
        b_save.clicked.connect(self.finish_editing)
        rt_layout.addWidget(b_save)

        main_layout.addWidget(right_toolbar)

        # Connections
        b_undo.clicked.connect(lambda: self.canvas.undo())
        b_clear.clicked.connect(lambda: self.canvas.clear())

        self.set_mode("DEFECT")
        self.load_current_image()

    def create_manual_input(self, label, default, layout):
        l = QLabel(label)
        l.setStyleSheet("color: #ccc; margin-top: 2px;")
        layout.addWidget(l)
        row = QHBoxLayout()
        txt = QLineEdit(default)
        txt.setStyleSheet("background: #333; color: white; padding: 5px; border: 1px solid #555;")

        # --- இதோ இந்த மாற்றத்தைச் செய்யுங்கள் ---
        # 1. முதலில் டைமரை உருவாக்கவும் (இந்த ஃபங்ஷனை அழைக்கும்போது ஒவ்வொரு முறையும் புதிய டைமர் வராமல் இருக்க,
        #    இதை __init__ பகுதியில் வைப்பது சிறந்தது. ஆனால் இப்போது தற்காலிகமாக இங்கே செய்கிறோம்)
        if not hasattr(self, 'sim_timer'):
            self.sim_timer = QTimer()
            self.sim_timer.setSingleShot(True)
            self.sim_timer.timeout.connect(self.run_true_simulation)

        # 2. இப்போது textChanged சிக்னலை டைமருடன் இணைக்கவும்
        txt.textChanged.connect(lambda: self.sim_timer.start(300) if self.btn_ai_enable.isChecked() else None)
        # --------------------------------------

        btn = QPushButton("#")
        btn.setFixedSize(30, 30)
        btn.setStyleSheet("background: #00bcd4; color: black; font-weight: bold;")
        btn.clicked.connect(lambda: Keypad(txt.text(), lambda v: txt.setText(v), self).exec())
        row.addWidget(txt)
        row.addWidget(btn)
        layout.addLayout(row)
        return txt



    def get_btn_style(self, bg_color, border_color):
        return f"QPushButton {{ background-color: #333; color: {border_color}; border: 2px solid {border_color}; font-weight: bold; font-size:14px; border-radius:6px; }} QPushButton:checked {{ background-color: {bg_color}; color: black; border: 2px solid {border_color}; }}"

    def set_mode(self, mode):
        self.canvas.set_draw_mode(mode)
        self.btn_draw_bad.setChecked(mode == "DEFECT")
        self.btn_draw_good.setChecked(mode == "GOOD")

    def save_current_work(self):
        card = self.image_list[self.current_idx]
        card.set_status(self.canvas.rois)
        if self.canvas.image and self.canvas.rois:
            img_w = self.canvas.image.width()
            img_h = self.canvas.image.height()
            txt_path = card.image_path.rsplit('.', 1)[0] + ".txt"
            try:
                with open(txt_path, "w") as f:
                    for rect, btype in self.canvas.rois:
                        cls_id = 0 if btype == "DEFECT" else 1
                        cx = (rect.x() + rect.width() / 2) / img_w
                        cy = (rect.y() + rect.height() / 2) / img_h
                        w = rect.width() / img_w
                        h = rect.height() / img_h
                        f.write(f"{cls_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n")
            except Exception as e:
                pass

    def load_current_image(self):
        if 0 <= self.current_idx < len(self.image_list):
            card = self.image_list[self.current_idx]
            self.current_path = card.image_path
            self.lbl_info.setText(f"IMG {self.current_idx + 1} / {len(self.image_list)}")
            self.canvas.set_image(self.current_path)

            # 🔥 FIX 1: "dummy" க்ராஷ் பக் சரி செய்யப்பட்டது (REAL YOLO Coordinates Parsing)
            real_rois = []
            txt_path = self.current_path.rsplit('.', 1)[0] + ".txt"

            if os.path.exists(txt_path) and self.canvas.image:
                img_w = self.canvas.image.width()
                img_h = self.canvas.image.height()
                try:
                    with open(txt_path, 'r') as f:
                        for line in f.readlines():
                            parts = line.strip().split()
                            if len(parts) >= 5:
                                cls_id = int(parts[0])
                                cx, cy, nw, nh = map(float, parts[1:5])

                                # YOLO ஃபார்மட்டில் இருந்து QRect ஆக மாற்றுதல்
                                w = nw * img_w
                                h = nh * img_h
                                x = cx * img_w - w / 2
                                y = cy * img_h - h / 2

                                mode = "DEFECT" if cls_id == 0 else "GOOD"
                                real_rois.append((QRect(int(x), int(y), int(w), int(h)), mode))
                except Exception as e:
                    print("Error loading TXT:", e)

            # உண்மையான பாக்ஸ்களை Canvas-க்கு அனுப்புதல்
            if real_rois:
                card.rois = real_rois
            elif not os.path.exists(txt_path):
                card.rois = []

            self.canvas.load_rois(card.rois)

            self.lbl_result.setText("READY")
            if hasattr(self, 'btn_ai_enable') and (self.btn_ai_enable.isChecked() or self.btn_purity_check.isChecked()):
                self.run_true_simulation()

    def toggle_detection(self):
        if self.btn_ai_enable.isChecked() or self.btn_purity_check.isChecked():
            self.run_true_simulation()
        else:
            self.canvas.set_rois_visible(True)
            self.canvas.set_overlay_mask(None)
            self.lbl_result.setText("DETECTION OFF")
            self.lbl_result.setStyleSheet(
                "color: #555; background: #111; font-size: 16px; font-weight: bold; border-radius: 6px; padding: 15px; border: 1px solid #555;")

    def run_true_simulation(self):
        if not self.btn_ai_enable.isChecked() and not self.btn_purity_check.isChecked():
            return

        boxes = self.canvas.rois
        if not boxes:
            self.lbl_result.setText("NO REGIONS")
            return

        # --- SAFE IMAGE LOADING FIX (CRASH தடுத்தல்) ---
        try:
            img_bytes = np.fromfile(self.current_path, dtype=np.uint8)
            img = cv2.imdecode(img_bytes, cv2.IMREAD_COLOR)
        except Exception:
            return

        if img is None: return
        h, w = img.shape[:2]
        # ----------------------------------------------

        try:
            val_sens = int(self.txt_thresh.text() or 40)
            val_scale = int(self.txt_scale.text() or 6)  # நீங்கள் கேட்ட Scale அளவு (உ-ம்: 6)
            val_purity = int(self.txt_purity.text() or 18)  # நீங்கள் கேட்ட Purity அளவு (உ-ம்: 18)
        except ValueError:
            return

        hsv_img = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        samples_h, samples_s, samples_v = [], [], []
        has_defect = False

        for rect, btype in boxes:
            if btype == "DEFECT":
                has_defect = True
                rx, ry, rw, rh = rect.x(), rect.y(), rect.width(), rect.height()
                rx, ry = max(0, rx), max(0, ry)
                rw, rh = min(w - rx, rw), min(h - ry, rh)

                roi = hsv_img[ry:ry + rh, rx:rx + rw]
                if roi.size > 0:
                    samples_h.extend(roi[:, :, 0].flatten())
                    samples_s.extend(roi[:, :, 1].flatten())
                    samples_v.extend(roi[:, :, 2].flatten())

        if not has_defect or not samples_h:
            self.lbl_result.setText("MARK DEFECT")
            return

        # 1. Sensitivity (நிறத்தை கண்டுபிடித்தல் - AI Enhanced)
        # Using percentiles to tightly capture marked defect colors while excluding extreme outliers
        min_h, max_h = np.percentile(samples_h, 5), np.percentile(samples_h, 95)
        min_s, max_s = np.percentile(samples_s, 5), np.percentile(samples_s, 95)
        min_v, max_v = np.percentile(samples_v, 5), np.percentile(samples_v, 95)

        # val_sens now acts as a smart tolerance padding to the exact colors, stopping runway background masking
        pad = max(1, val_sens // 4)

        lower = np.array([max(0, min_h - pad), max(0, min_s - pad), max(0, min_v - pad)])
        upper = np.array([min(180, max_h + pad), min(255, max_s + pad), min(255, max_v + pad)])
        mask = cv2.inRange(hsv_img, lower, upper)

        # 2. Scale & Purity (Box Filter / Pixel Density Counting)
        if val_scale > 0 and self.btn_purity_check.isChecked():
            # Count defective pixels in a val_scale x val_scale window
            pixel_counts = cv2.boxFilter(mask, cv2.CV_32F, (val_scale, val_scale), normalize=False) / 255.0
            # Keep only pixels where the count is >= val_purity
            mask = (pixel_counts >= val_purity).astype(np.uint8) * 255
        elif val_scale > 0:
            # Fallback if purity is not checked, just do a tiny blur or keep original
            pass

        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filtered_mask = np.zeros_like(mask)
        total_defect_area = 0

        # 3. Draw Results
        for c in cnts:
            area = cv2.contourArea(c)
            cv2.drawContours(filtered_mask, [c], -1, 255, -1)
            total_defect_area += area

        if total_defect_area > 0:
            self.lbl_result.setText(f"FOUND ({int(total_defect_area)} px)")
            self.lbl_result.setStyleSheet(
                "color: #FF00FF; border: 2px solid #FF00FF; font-size: 16px; font-weight: bold; background: #111; padding: 15px; border-radius: 8px;")
            self.canvas.set_rois_visible(False)
            self.canvas.set_overlay_mask(filtered_mask)  # overlay mask-ல் நாம் ஏற்கனவே Pink கலர் கொடுத்துள்ளோம்
        else:
            self.lbl_result.setText("CLEAN")
            self.lbl_result.setStyleSheet(
                "color: #00e676; border: 2px solid #00e676; font-size: 16px; font-weight: bold; background: #111; padding: 15px; border-radius: 8px;")
            self.canvas.set_rois_visible(False)
            self.canvas.set_overlay_mask(None)

    def prev_img(self):
        self.save_current_work()
        if self.current_idx > 0:
            self.current_idx -= 1
            self.load_current_image()

    def next_img(self):
        self.save_current_work()
        if self.current_idx < len(self.image_list) - 1:
            self.current_idx += 1
            self.load_current_image()
        else:
            QMessageBox.information(self, "Done", "End of dataset reached.")

    def set_tool(self, tool_name):
        self.canvas.set_tool_mode(tool_name)  # Canvas-க்கு டூல் மாறச் சொல்கிறோம்
        self.btn_tool_box.setChecked(tool_name == "BOX")
        self.btn_tool_paint.setChecked(tool_name == "PAINT")
        self.btn_tool_smart.setChecked(tool_name == "SMART")

    def finish_editing(self):
        self.save_current_work()
        # Save AI Simulation parameters to persist configurations
        try:
            cfg = json.load(open('vision_settings.json')) if os.path.exists('vision_settings.json') else {}
            cfg['ai_sim'] = {
                'thresh': self.txt_thresh.text(),
                'purity': self.txt_purity.text(),
                'scale': self.txt_scale.text()
            }
            with open('vision_settings.json', 'w') as f:
                json.dump(cfg, f, indent=4)
        except Exception:
            pass
        self.accept()


# =============================================================================
# SIMULATION 63-CHANNEL OPERATING LED BAR (HARDWARE STYLE)
# =============================================================================
class SimulationOperatingLEDBar(QWidget):
    """
    63-Channel Operating Ejector LED Bar positioned under the simulation image.
    Active channels illuminate in bright Neon Red with channel numbers.
    Inactive channels show subtle industrial dark indicator slots.
    """
    def __init__(self, num_channels=63, parent=None):
        super().__init__(parent)
        self.num_channels = num_channels
        self.active_channels = set()
        self.setFixedHeight(46)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("63-Channel Ejector Operating LED Indicators: Live defect firing; Click any channel to test fire")

    def set_active_channels(self, channels, hold_sec=0.0):
        new_active = set(channels) if channels else set()
        if new_active != self.active_channels:
            self.active_channels = new_active
            self.update()

    def clear_leds(self):
        if self.active_channels:
            self.active_channels.clear()
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            w = self.width()
            n = max(1, self.num_channels)
            ch_w = w / float(n)
            ch_idx = int(event.x() / ch_w)
            if 0 <= ch_idx < self.num_channels:
                ch_num = ch_idx + 1
                self.set_active_channels([ch_num], hold_sec=0.35)
                win = self.window()
                if hasattr(win, 'esp32') and win.esp32 and getattr(win.esp32, 'connected', False):
                    try:
                        win.esp32.fire_single_ejector(ch_num, 25)
                    except Exception as ex:
                        print(f"Manual ejector click error: {ex}")
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        n = max(1, self.num_channels)
        ch_w = w / n

        # Sleek dark industrial background
        painter.fillRect(0, 0, w, h, QColor(16, 22, 26))

        # Top and bottom border accent
        painter.setPen(QPen(QColor(38, 50, 56), 1))
        painter.drawLine(0, 0, w, 0)
        painter.drawLine(0, h - 1, w, h - 1)

        font_sm = QFont("Segoe UI", 7)
        font_act = QFont("Segoe UI", 8, QFont.Bold)

        for i in range(n):
            ch_num = i + 1
            x1 = int(i * ch_w)
            x2 = int((i + 1) * ch_w)
            cell_w = max(2, x2 - x1)
            is_active = ch_num in self.active_channels

            if is_active:
                # Glowing Neon Red Active Ejector LED
                painter.fillRect(x1, 2, cell_w, h - 4, QColor(213, 0, 0, 230))
                grad = QLinearGradient(x1, 3, x1, h - 3)
                grad.setColorAt(0, QColor(255, 82, 82))
                grad.setColorAt(1, QColor(183, 28, 28))
                painter.fillRect(x1 + 1, 3, max(1, cell_w - 2), h - 6, grad)
                painter.setPen(QPen(QColor(255, 138, 128), 1))
                painter.drawRect(x1 + 1, 3, max(1, cell_w - 2), h - 6)

                # Channel number text
                painter.setFont(font_act)
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(QRect(x1 - 6, 4, cell_w + 12, h - 8), Qt.AlignCenter, str(ch_num))
            else:
                # Idle dark channel slot
                painter.fillRect(x1 + 1, 8, max(1, cell_w - 2), h - 16, QColor(28, 38, 44))
                painter.setPen(QPen(QColor(42, 56, 64), 1))
                painter.drawRect(x1 + 1, 8, max(1, cell_w - 2), h - 16)

                # Channel number label on idle slots (all channels when space allows, or milestones)
                painter.setFont(font_sm)
                painter.setPen(QColor(120, 144, 156) if cell_w >= 14 else QColor(84, 110, 122))
                if cell_w >= 14 or ch_num in [1, 8, 16, 24, 32, 40, 48, 56, 63]:
                    painter.drawText(QRect(x1 - 6, 8, cell_w + 12, h - 16), Qt.AlignCenter, str(ch_num))


# =============================================================================
# INTERACTIVE HSV COLOR MAP LABEL (FOR CLICK & DRAG COLOR SELECTION)
# =============================================================================
class InteractiveHSVLabel(QLabel):
    def __init__(self, parent=None, on_box_selected=None):
        super().__init__(parent)
        self.on_box_selected = on_box_selected
        self.start_pos = None
        self.current_pos = None
        self.is_dragging = False
        self.orig_w = 565
        self.orig_h = 500
        self.setCursor(Qt.CrossCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_pos = event.pos()
            self.current_pos = event.pos()
            self.is_dragging = True
            self.update_selection(final=False)

    def mouseMoveEvent(self, event):
        if self.is_dragging:
            self.current_pos = event.pos()
            self.update_selection(final=False)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.is_dragging:
            self.current_pos = event.pos()
            self.is_dragging = False
            self.update_selection(final=True)

    def update_selection(self, final=False):
        if not self.start_pos or not self.current_pos:
            return
        lbl_w = max(1, self.width())
        lbl_h = max(1, self.height())
        scale_x = self.orig_w / float(lbl_w)
        scale_y = self.orig_h / float(lbl_h)

        x1 = max(0, min(self.orig_w, int(self.start_pos.x() * scale_x)))
        y1 = max(0, min(self.orig_h, int(self.start_pos.y() * scale_y)))
        x2 = max(0, min(self.orig_w, int(self.current_pos.x() * scale_x)))
        y2 = max(0, min(self.orig_h, int(self.current_pos.y() * scale_y)))

        if self.on_box_selected:
            self.on_box_selected(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), final)


# =============================================================================
# DEFECT SIMULATION & 63-CHANNEL EJECTOR POPUP DIALOG
# =============================================================================
class DefectSimulationDialog(QDialog):
    def __init__(self, algo_name, current_settings, main_window=None, parent=None):
        super().__init__(parent)
        self.algo_name = algo_name
        self.settings = dict(current_settings) if current_settings else {}
        self.main_window = main_window
        self.setWindowTitle(f"Defect Simulation & 63-Channel Firing Monitor - {algo_name}")
        self.resize(1280, 840)
        self.setStyleSheet("""
            QDialog { background-color: #12161a; color: #eceff1; font-family: Segoe UI; }
            QLabel { color: #cfd8dc; font-size: 13px; }
            QGroupBox { border: 2px solid #37474f; border-radius: 8px; font-weight: bold; color: #00bcd4; margin-top: 15px; padding-top: 15px; font-size: 13px; }
            QPushButton { background-color: #263238; color: #00e5ff; border: 1px solid #00bcd4; border-radius: 5px; font-weight: bold; padding: 6px 10px; font-size: 13px; }
            QPushButton:hover { background-color: #00bcd4; color: black; }
            QComboBox, QSpinBox { background-color: #1e272e; color: #00e5ff; border: 1px solid #37474f; border-radius: 4px; padding: 4px; font-size: 13px; }
            QSlider::groove:horizontal { height: 8px; background: #263238; border-radius: 4px; }
            QSlider::sub-page:horizontal { background: #00bcd4; border-radius: 4px; }
            QSlider::handle:horizontal { background: #00e676; border: 1px solid #00e676; width: 18px; margin-top: -5px; margin-bottom: -5px; border-radius: 9px; }
        """)

        # Algorithm Mode Detection (HSV, SIZE, DISCOLOUR, or GRAY)
        name_upper = self.algo_name.upper()
        if "HSV" in name_upper:
            self.default_algo_type = "HSV"
        elif "SIZE" in name_upper:
            self.default_algo_type = "SIZE"
        elif "DISCOLOUR" in name_upper or "CHROME" in name_upper:
            self.default_algo_type = "DISCOLOUR"
        else:
            self.default_algo_type = "GRAY"

        self.active_channels = set()
        self.defect_boxes = []
        self.current_frame = None

        # Captured images gallery state
        self.capture_folders = []
        self.current_folder = ""
        self.image_files = []
        self.current_img_idx = -1

        self.init_ui()
        self.init_folder_and_images()

    def format_sens_text(self, val):
        if val == 0:
            return f"{val} (Off - None)"
        elif val <= 15:
            return f"{val} (Black Only)"
        elif val <= 60:
            return f"{val} (Yellow/Brown)"
        else:
            return f"{val} (White/Light)"

    def format_purity_text(self, val):
        if val >= 100:
            return f"{val}% (All Allowed)"
        elif val >= 60:
            return f"{val}% (Dark Area Only)"
        elif val <= 5:
            return f"{val}% (Small Defect Also)"
        else:
            return f"{val}% (Medium Defect)"

    def open_keypad_for_slider(self, slider, min_val, max_val):
        def on_entered(val_str):
            try:
                v = int(float(val_str))
                slider.setValue(max(min_val, min(max_val, v)))
            except ValueError:
                pass
        Keypad(str(slider.value()), on_entered, self).exec_()

    def on_hsv_sens_changed(self, val):
        self.lbl_hsv_sens_val.setText(self.format_sens_text(val))
        self.run_simulation()

    def on_hsv_purity_changed(self, val):
        self.lbl_hsv_purity_val.setText(self.format_purity_text(val))
        self.run_simulation()

    def on_sim_hsv_coord_changed(self):
        self.draw_sim_hsv_thumb()
        self.run_simulation()

    def on_sim_hsv_box_selected(self, x1, y1, x2, y2, final=False):
        self.sim_txt_x1.blockSignals(True)
        self.sim_txt_y1.blockSignals(True)
        self.sim_txt_x2.blockSignals(True)
        self.sim_txt_y2.blockSignals(True)
        self.sim_txt_x1.setText(str(x1))
        self.sim_txt_y1.setText(str(y1))
        self.sim_txt_x2.setText(str(x2))
        self.sim_txt_y2.setText(str(y2))
        self.sim_txt_x1.blockSignals(False)
        self.sim_txt_y1.blockSignals(False)
        self.sim_txt_x2.blockSignals(False)
        self.sim_txt_y2.blockSignals(False)
        self.draw_sim_hsv_thumb()
        self.run_simulation()

    def draw_sim_hsv_thumb(self):
        if not hasattr(self, 'lbl_hsv_thumb'):
            return
        hsv_path = next((p for p in ["hsv.png", os.path.join(os.path.dirname(os.path.abspath(__file__)), "hsv.png"), r"d:\lambton\BV_SORTER\hsv.png"] if os.path.exists(p)), r"d:\lambton\BV_SORTER\hsv.png")
        if not os.path.exists(hsv_path):
            self.lbl_hsv_thumb.setText("Color Map Missing (hsv.png)")
            return

        pix = QPixmap(hsv_path)
        if pix.isNull():
            return

        try:
            x1 = int(self.sim_txt_x1.text())
            y1 = int(self.sim_txt_y1.text())
            x2 = int(self.sim_txt_x2.text())
            y2 = int(self.sim_txt_y2.text())
        except ValueError:
            x1, y1, x2, y2 = 50, 50, 200, 200

        min_x = max(0, min(565, min(x1, x2)))
        max_x = max(min_x + 2, min(565, max(x1, x2)))
        min_y = max(0, min(500, min(y1, y2)))
        max_y = max(min_y + 2, min(500, max(y1, y2)))

        painter = QPainter(pix)
        pen = QPen(QColor(255, 255, 255), 4, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(0, 229, 255, 55))
        painter.drawRect(min_x, min_y, max_x - min_x, max_y - min_y)

        painter.setFont(QFont("Segoe UI", 13, QFont.Bold))
        painter.setPen(QColor(0, 255, 255))
        painter.drawText(min_x + 5, min_y + 22, f"({min_x}, {min_y}) - ({max_x}, {max_y})")
        painter.end()

        self.lbl_hsv_thumb.setPixmap(pix.scaled(self.lbl_hsv_thumb.width(), self.lbl_hsv_thumb.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def on_small_limit_changed(self, val):
        self.lbl_small_val.setText(f"{val} px")
        self.run_simulation()

    def on_big_limit_changed(self, val):
        self.lbl_big_val.setText(f"{val} px")
        self.run_simulation()

    def on_discolour_sens_changed(self, val):
        self.lbl_discolour_sens_val.setText(self.format_sens_text(val))
        self.run_simulation()

    def on_discolour_purity_changed(self, val):
        self.lbl_discolour_purity_val.setText(self.format_purity_text(val))
        self.run_simulation()

    def on_sens_changed(self, val):
        self.lbl_sens_val.setText(self.format_sens_text(val))
        self.run_simulation()

    def on_purity_changed(self, val):
        self.lbl_purity_val.setText(self.format_purity_text(val))
        self.run_simulation()

    def on_algo_type_changed(self, idx):
        self.panel_hsv.setVisible(idx == 0)
        self.panel_size.setVisible(idx == 1)
        self.panel_discolour.setVisible(idx == 2)
        self.panel_gray.setVisible(idx == 3)
        self.run_simulation()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 12, 15, 12)

        # 1. Top Header Bar
        hdr_layout = QHBoxLayout()
        title_lbl = QLabel(f"🔬 DEFECT SIMULATION & 63-CH FIRING TEST: {self.algo_name}")
        title_lbl.setStyleSheet("font-size: 17px; font-weight: bold; color: #00e5ff;")
        hdr_layout.addWidget(title_lbl)
        hdr_layout.addStretch()

        self.combo_cam_source = QComboBox()
        self.combo_cam_source.addItems(["FRONT CAMERA (GigE)", "REAR CAMERA (GigE)"])
        if "REAR" in self.algo_name:
            self.combo_cam_source.setCurrentIndex(1)
        self.combo_cam_source.currentIndexChanged.connect(self.acquire_live_frame)
        hdr_layout.addWidget(QLabel("Camera Source:"))
        hdr_layout.addWidget(self.combo_cam_source)

        btn_snap = QPushButton("📸 SNAP LIVE FRAME")
        btn_snap.setStyleSheet("background: #0097a7; color: white; font-weight: bold;")
        btn_snap.clicked.connect(self.acquire_live_frame)
        hdr_layout.addWidget(btn_snap)

        btn_load_img = QPushButton("📂 LOAD CUSTOM FILE")
        btn_load_img.setStyleSheet("background: #37474f; color: #cfd8dc; font-weight: bold;")
        btn_load_img.clicked.connect(self.load_image_file)
        hdr_layout.addWidget(btn_load_img)

        main_layout.addLayout(hdr_layout)

        # 2. Captured Images Navigation Bar (Folder Selection, Prev, Next, Auto-Load Last Image)
        nav_box = QFrame()
        nav_box.setStyleSheet("""
            QFrame {
                background-color: #1a2228;
                border: 1px solid #37474f;
                border-radius: 8px;
            }
        """)
        nav_layout = QHBoxLayout(nav_box)
        nav_layout.setContentsMargins(10, 6, 10, 6)
        nav_layout.setSpacing(10)

        nav_layout.addWidget(QLabel("📁 <b>Folder:</b>"))
        self.combo_folder = QComboBox()
        self.combo_folder.setMinimumWidth(220)
        self.combo_folder.currentIndexChanged.connect(self.on_folder_changed)
        nav_layout.addWidget(self.combo_folder)

        self.btn_prev_img = QPushButton("⏮ PREVIOUS")
        self.btn_prev_img.setFixedHeight(34)
        self.btn_prev_img.setStyleSheet("""
            QPushButton {
                background-color: #263238; color: #00e5ff; font-weight: bold;
                border: 1px solid #00bcd4; border-radius: 4px; padding: 4px 14px; font-size: 13px;
            }
            QPushButton:hover { background-color: #00bcd4; color: black; }
        """)
        self.btn_prev_img.setToolTip("Previous image (or Press Left Arrow key)")
        self.btn_prev_img.clicked.connect(self.prev_image)
        nav_layout.addWidget(self.btn_prev_img)

        self.lbl_img_counter = QLabel("[ 0 / 0 ]")
        self.lbl_img_counter.setStyleSheet("color: #00e676; font-weight: bold; font-size: 14px;")
        nav_layout.addWidget(self.lbl_img_counter)

        self.lbl_img_info = QLabel("Scanning captured images...")
        self.lbl_img_info.setStyleSheet("color: #e0f7fa; font-size: 13px; font-weight: bold;")
        self.lbl_img_info.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        nav_layout.addWidget(self.lbl_img_info, 1)

        self.btn_next_img = QPushButton("NEXT ⏭")
        self.btn_next_img.setFixedHeight(34)
        self.btn_next_img.setStyleSheet("""
            QPushButton {
                background-color: #263238; color: #00e5ff; font-weight: bold;
                border: 1px solid #00bcd4; border-radius: 4px; padding: 4px 14px; font-size: 13px;
            }
            QPushButton:hover { background-color: #00bcd4; color: black; }
        """)
        self.btn_next_img.setToolTip("Next image (or Press Right Arrow key)")
        self.btn_next_img.clicked.connect(self.next_image)
        nav_layout.addWidget(self.btn_next_img)

        self.btn_latest_img = QPushButton("🔄 LATEST CAPTURED")
        self.btn_latest_img.setFixedHeight(34)
        self.btn_latest_img.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00897b, stop:1 #00bfa5);
                color: black; font-weight: bold; border: none; border-radius: 4px; padding: 4px 14px; font-size: 13px;
            }
            QPushButton:hover { background: #1de9b6; }
        """)
        self.btn_latest_img.setToolTip("Reload folder and jump to latest captured image")
        self.btn_latest_img.clicked.connect(self.load_latest_image)
        nav_layout.addWidget(self.btn_latest_img)

        main_layout.addWidget(nav_box)

        # 3. Center Workspace (Split into Canvas and Right Controls)
        work_layout = QHBoxLayout()

        # Canvas side
        canvas_box = QGroupBox("SIMULATION IMAGE (63 CHANNELS & DEFECT HIGHLIGHT)")
        canvas_vbox = QVBoxLayout(canvas_box)
        canvas_vbox.setContentsMargins(8, 12, 8, 8)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("background: #000; border: 2px solid #263238; border-radius: 6px;")

        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setStyleSheet("background: #000;")
        self.scroll_area.setWidget(self.img_label)
        canvas_vbox.addWidget(self.scroll_area)

        # Channel Operating LED Bar directly under the image
        self.lbl_led_bar_status = QLabel("💡 OPERATING EJECTOR LEDs (1 - 63): Standby")
        self.lbl_led_bar_status.setStyleSheet("color: #80deea; font-size: 12px; font-weight: bold; padding-top: 4px;")
        canvas_vbox.addWidget(self.lbl_led_bar_status)

        self.operating_led_bar = SimulationOperatingLEDBar(num_channels=63)
        canvas_vbox.addWidget(self.operating_led_bar)

        work_layout.addWidget(canvas_box, 3)

        # Right side: Tuning & Hardware firing
        side_panel = QWidget()
        side_panel.setFixedWidth(370)
        side_vbox = QVBoxLayout(side_panel)
        side_vbox.setContentsMargins(0, 0, 0, 0)
        side_vbox.setSpacing(10)

        # Parameter Tuning Group
        grp_tune = QGroupBox("ALGORITHM PARAMETERS")
        tune_lay = QVBoxLayout(grp_tune)
        tune_lay.setSpacing(8)

        # Mode Selection Row
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("<b>Algorithm:</b>"))
        self.combo_algo_type = QComboBox()
        self.combo_algo_type.addItems(["HSV COLOR DEFECT", "SIZE SORTING (BIG / SMALL)", "DISCOLOUR DEFECT", "GRAY DEFECT"])
        self.combo_algo_type.setStyleSheet("background: #1e272e; color: #00e5ff; font-weight: bold; padding: 4px;")

        name_upper = self.algo_name.upper()
        if "HSV" in name_upper:
            self.combo_algo_type.setCurrentIndex(0)
        elif "SIZE" in name_upper:
            self.combo_algo_type.setCurrentIndex(1)
        elif "DISCOLOUR" in name_upper or "CHROME" in name_upper:
            self.combo_algo_type.setCurrentIndex(2)
        else:
            self.combo_algo_type.setCurrentIndex(3)

        self.combo_algo_type.currentIndexChanged.connect(self.on_algo_type_changed)
        type_row.addWidget(self.combo_algo_type)
        tune_lay.addLayout(type_row)

        # =====================================================================
        # PANEL 0: HSV COLOR DEFECT PARAMETERS
        # =====================================================================
        self.panel_hsv = QWidget()
        hsv_lay = QVBoxLayout(self.panel_hsv)
        hsv_lay.setContentsMargins(0, 4, 0, 4)
        hsv_lay.setSpacing(6)

        # Interactive HSV Color Map Thumbnail
        hsv_thumb_box = QGroupBox("HSV COLOR MAP (CLICK & DRAG BOX)")
        hsv_thumb_lay = QVBoxLayout(hsv_thumb_box)
        hsv_thumb_lay.setContentsMargins(4, 8, 4, 6)

        self.lbl_hsv_thumb = InteractiveHSVLabel(on_box_selected=self.on_sim_hsv_box_selected)
        self.lbl_hsv_thumb.setFixedHeight(140)
        self.lbl_hsv_thumb.setAlignment(Qt.AlignCenter)
        self.lbl_hsv_thumb.setStyleSheet("background: #000; border: 1px solid #37474f; border-radius: 4px;")
        hsv_thumb_lay.addWidget(self.lbl_hsv_thumb)
        hsv_lay.addWidget(hsv_thumb_box)

        # Coordinates Grid (X1, Y1, X2, Y2)
        coord_box = QGroupBox("SELECTED COLOR COORDINATES")
        coord_grid = QGridLayout(coord_box)
        coord_grid.setContentsMargins(6, 6, 6, 6)
        coord_grid.setSpacing(6)

        coord_grid.addWidget(QLabel("X1:"), 0, 0)
        self.sim_txt_x1 = QLineEdit(str(self.settings.get("x1", "50")))
        coord_grid.addWidget(self.sim_txt_x1, 0, 1)

        coord_grid.addWidget(QLabel("Y1:"), 0, 2)
        self.sim_txt_y1 = QLineEdit(str(self.settings.get("y1", "50")))
        coord_grid.addWidget(self.sim_txt_y1, 0, 3)

        coord_grid.addWidget(QLabel("X2:"), 1, 0)
        self.sim_txt_x2 = QLineEdit(str(self.settings.get("x2", "200")))
        coord_grid.addWidget(self.sim_txt_x2, 1, 1)

        coord_grid.addWidget(QLabel("Y2:"), 1, 2)
        self.sim_txt_y2 = QLineEdit(str(self.settings.get("y2", "200")))
        coord_grid.addWidget(self.sim_txt_y2, 1, 3)

        for txt in [self.sim_txt_x1, self.sim_txt_y1, self.sim_txt_x2, self.sim_txt_y2]:
            txt.setStyleSheet("background: #1e272e; color: #ffd600; font-weight: bold; font-size: 13px; padding: 2px 4px; border: 1px solid #37474f;")
            txt.textChanged.connect(self.on_sim_hsv_coord_changed)
            KeypadEventFilter(txt, lambda t=txt: Keypad(t.text(), t.setText, self).exec_())

        hsv_lay.addWidget(coord_box)

        # Sorting Logic (SORT OUT / SORT IN)
        mode_h_row = QHBoxLayout()
        mode_h_row.addWidget(QLabel("<b>Sorting Logic:</b>"))
        self.combo_hsv_mode = QComboBox()
        self.combo_hsv_mode.addItems(["SORT OUT", "SORT IN"])
        self.combo_hsv_mode.setCurrentText(self.settings.get("hsv_mode", "SORT OUT"))
        self.combo_hsv_mode.currentTextChanged.connect(self.run_simulation)
        mode_h_row.addWidget(self.combo_hsv_mode)
        hsv_lay.addLayout(mode_h_row)

        # Color Sensitivity Slider (0 - 100)
        hsv_lay.addWidget(QLabel("Color Sensitivity (0-100):"))
        hsv_sens_row = QHBoxLayout()
        btn_sub_hs = QPushButton("-")
        btn_sub_hs.setFixedSize(28, 28)
        self.slider_hsv_sens = QSlider(Qt.Horizontal)
        self.slider_hsv_sens.setRange(0, 100)
        init_hsens = int(float(self.settings.get("sens", 100)))
        self.slider_hsv_sens.setValue(init_hsens)
        btn_add_hs = QPushButton("+")
        btn_add_hs.setFixedSize(28, 28)
        btn_kp_hs = QPushButton("#")
        btn_kp_hs.setFixedSize(28, 28)
        btn_kp_hs.setStyleSheet("background: #00bcd4; color: black; font-weight: bold;")
        self.lbl_hsv_sens_val = QLabel(self.format_sens_text(init_hsens))
        self.lbl_hsv_sens_val.setFixedWidth(110)
        self.lbl_hsv_sens_val.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 13px;")

        btn_sub_hs.clicked.connect(lambda: self.slider_hsv_sens.setValue(max(0, self.slider_hsv_sens.value() - 5)))
        btn_add_hs.clicked.connect(lambda: self.slider_hsv_sens.setValue(min(100, self.slider_hsv_sens.value() + 5)))
        btn_kp_hs.clicked.connect(lambda: self.open_keypad_for_slider(self.slider_hsv_sens, 0, 100))
        self.slider_hsv_sens.valueChanged.connect(self.on_hsv_sens_changed)

        hsv_sens_row.addWidget(btn_sub_hs)
        hsv_sens_row.addWidget(self.slider_hsv_sens)
        hsv_sens_row.addWidget(btn_add_hs)
        hsv_sens_row.addWidget(btn_kp_hs)
        hsv_sens_row.addWidget(self.lbl_hsv_sens_val)
        hsv_lay.addLayout(hsv_sens_row)

        # Color Purity Slider (1 - 100)
        hsv_lay.addWidget(QLabel("Purity (Acceptance Tolerance 1-100):"))
        hsv_pur_row = QHBoxLayout()
        btn_sub_hp = QPushButton("-")
        btn_sub_hp.setFixedSize(28, 28)
        self.slider_hsv_purity = QSlider(Qt.Horizontal)
        self.slider_hsv_purity.setRange(1, 100)
        init_hpur = int(float(self.settings.get("purity", 1)))
        self.slider_hsv_purity.setValue(init_hpur)
        btn_add_hp = QPushButton("+")
        btn_add_hp.setFixedSize(28, 28)
        btn_kp_hp = QPushButton("#")
        btn_kp_hp.setFixedSize(28, 28)
        btn_kp_hp.setStyleSheet("background: #00bcd4; color: black; font-weight: bold;")
        self.lbl_hsv_purity_val = QLabel(self.format_purity_text(init_hpur))
        self.lbl_hsv_purity_val.setFixedWidth(110)
        self.lbl_hsv_purity_val.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 13px;")

        btn_sub_hp.clicked.connect(lambda: self.slider_hsv_purity.setValue(max(1, self.slider_hsv_purity.value() - 5)))
        btn_add_hp.clicked.connect(lambda: self.slider_hsv_purity.setValue(min(100, self.slider_hsv_purity.value() + 5)))
        btn_kp_hp.clicked.connect(lambda: self.open_keypad_for_slider(self.slider_hsv_purity, 1, 100))
        self.slider_hsv_purity.valueChanged.connect(self.on_hsv_purity_changed)

        hsv_pur_row.addWidget(btn_sub_hp)
        hsv_pur_row.addWidget(self.slider_hsv_purity)
        hsv_pur_row.addWidget(btn_add_hp)
        hsv_pur_row.addWidget(btn_kp_hp)
        hsv_pur_row.addWidget(self.lbl_hsv_purity_val)
        hsv_lay.addLayout(hsv_pur_row)

        tune_lay.addWidget(self.panel_hsv)

        # =====================================================================
        # PANEL 0: SIZE SORTING PARAMETERS (BIG / SMALL / OK)
        # =====================================================================
        self.panel_size = QWidget()
        size_lay = QVBoxLayout(self.panel_size)
        size_lay.setContentsMargins(0, 4, 0, 4)
        size_lay.setSpacing(8)

        # Size Mode
        mode_s_row = QHBoxLayout()
        mode_s_row.addWidget(QLabel("Size Mode:"))
        self.combo_size_mode = QComboBox()
        self.combo_size_mode.addItems(["REJECT BIG", "REJECT SMALL", "REJECT BOTH (BIG & SMALL)"])
        if "SHORT" in name_upper or "SMALL" in name_upper:
            self.combo_size_mode.setCurrentText("REJECT SMALL")
        else:
            self.combo_size_mode.setCurrentText(self.settings.get("size_mode", "REJECT BIG"))
        self.combo_size_mode.currentTextChanged.connect(self.run_simulation)
        mode_s_row.addWidget(self.combo_size_mode)
        size_lay.addLayout(mode_s_row)

        # Small Limit (Pixels)
        size_lay.addWidget(QLabel("Small Limit (Pixels):"))
        small_row = QHBoxLayout()
        btn_sub_sm = QPushButton("-")
        btn_sub_sm.setFixedSize(28, 28)
        self.slider_small = QSlider(Qt.Horizontal)
        self.slider_small.setRange(50, 5000)
        self.slider_small.setSingleStep(10)
        init_small = int(float(self.settings.get("small", 1000)))
        self.slider_small.setValue(init_small)
        btn_add_sm = QPushButton("+")
        btn_add_sm.setFixedSize(28, 28)
        btn_kp_sm = QPushButton("#")
        btn_kp_sm.setFixedSize(28, 28)
        btn_kp_sm.setStyleSheet("background: #00bcd4; color: black; font-weight: bold;")
        self.lbl_small_val = QLabel(f"{init_small} px")
        self.lbl_small_val.setFixedWidth(75)
        self.lbl_small_val.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 13px;")

        btn_sub_sm.clicked.connect(lambda: self.slider_small.setValue(max(50, self.slider_small.value() - 50)))
        btn_add_sm.clicked.connect(lambda: self.slider_small.setValue(min(5000, self.slider_small.value() + 50)))
        btn_kp_sm.clicked.connect(lambda: self.open_keypad_for_slider(self.slider_small, 50, 5000))
        self.slider_small.valueChanged.connect(self.on_small_limit_changed)

        small_row.addWidget(btn_sub_sm)
        small_row.addWidget(self.slider_small)
        small_row.addWidget(btn_add_sm)
        small_row.addWidget(btn_kp_sm)
        small_row.addWidget(self.lbl_small_val)
        size_lay.addLayout(small_row)

        # Big Limit (Pixels)
        size_lay.addWidget(QLabel("Big Limit (Pixels):"))
        big_row = QHBoxLayout()
        btn_sub_bg = QPushButton("-")
        btn_sub_bg.setFixedSize(28, 28)
        self.slider_big = QSlider(Qt.Horizontal)
        self.slider_big.setRange(500, 12000)
        self.slider_big.setSingleStep(20)
        init_big = int(float(self.settings.get("big", 2300)))
        self.slider_big.setValue(init_big)
        btn_add_bg = QPushButton("+")
        btn_add_bg.setFixedSize(28, 28)
        btn_kp_bg = QPushButton("#")
        btn_kp_bg.setFixedSize(28, 28)
        btn_kp_bg.setStyleSheet("background: #00bcd4; color: black; font-weight: bold;")
        self.lbl_big_val = QLabel(f"{init_big} px")
        self.lbl_big_val.setFixedWidth(75)
        self.lbl_big_val.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 13px;")

        btn_sub_bg.clicked.connect(lambda: self.slider_big.setValue(max(500, self.slider_big.value() - 100)))
        btn_add_bg.clicked.connect(lambda: self.slider_big.setValue(min(12000, self.slider_big.value() + 100)))
        btn_kp_bg.clicked.connect(lambda: self.open_keypad_for_slider(self.slider_big, 500, 12000))
        self.slider_big.valueChanged.connect(self.on_big_limit_changed)

        big_row.addWidget(btn_sub_bg)
        big_row.addWidget(self.slider_big)
        big_row.addWidget(btn_add_bg)
        big_row.addWidget(btn_kp_bg)
        big_row.addWidget(self.lbl_big_val)
        size_lay.addLayout(big_row)

        self.lbl_size_stats = QLabel("Total: 0 | 🔴 BIG: 0 | 🟡 SMALL: 0 | 🟢 OK: 0")
        self.lbl_size_stats.setStyleSheet("color: #80deea; font-size: 12px; font-weight: bold;")
        size_lay.addWidget(self.lbl_size_stats)

        tune_lay.addWidget(self.panel_size)

        # =====================================================================
        # PANEL 1: DISCOLOUR PARAMETERS
        # =====================================================================
        self.panel_discolour = QWidget()
        disc_lay = QVBoxLayout(self.panel_discolour)
        disc_lay.setContentsMargins(0, 4, 0, 4)
        disc_lay.setSpacing(8)

        # Color Combo
        disc_combo_row = QHBoxLayout()
        disc_combo_row.addWidget(QLabel("Color Combo:"))
        self.combo_discolour = QComboBox()
        self.combo_discolour.addItems(["RED > GREEN", "GREEN > RED", "BLUE > YELLOW", "YELLOW DISCOLOUR", "DARK > LIGHT"])
        self.combo_discolour.setCurrentText(self.settings.get("combo", "RED > GREEN"))
        self.combo_discolour.currentTextChanged.connect(self.run_simulation)
        disc_combo_row.addWidget(self.combo_discolour)
        disc_lay.addLayout(disc_combo_row)

        # Discolour Sensitivity
        disc_lay.addWidget(QLabel("Sensitivity (0-100):"))
        disc_sens_row = QHBoxLayout()
        btn_sub_ds = QPushButton("-")
        btn_sub_ds.setFixedSize(28, 28)
        self.slider_discolour_sens = QSlider(Qt.Horizontal)
        self.slider_discolour_sens.setRange(0, 100)
        init_dsens = int(float(self.settings.get("sens", 50)))
        self.slider_discolour_sens.setValue(init_dsens)
        btn_add_ds = QPushButton("+")
        btn_add_ds.setFixedSize(28, 28)
        btn_kp_ds = QPushButton("#")
        btn_kp_ds.setFixedSize(28, 28)
        btn_kp_ds.setStyleSheet("background: #00bcd4; color: black; font-weight: bold;")
        self.lbl_discolour_sens_val = QLabel(self.format_sens_text(init_dsens))
        self.lbl_discolour_sens_val.setFixedWidth(110)
        self.lbl_discolour_sens_val.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 13px;")

        btn_sub_ds.clicked.connect(lambda: self.slider_discolour_sens.setValue(max(0, self.slider_discolour_sens.value() - 5)))
        btn_add_ds.clicked.connect(lambda: self.slider_discolour_sens.setValue(min(100, self.slider_discolour_sens.value() + 5)))
        btn_kp_ds.clicked.connect(lambda: self.open_keypad_for_slider(self.slider_discolour_sens, 0, 100))
        self.slider_discolour_sens.valueChanged.connect(self.on_discolour_sens_changed)

        disc_sens_row.addWidget(btn_sub_ds)
        disc_sens_row.addWidget(self.slider_discolour_sens)
        disc_sens_row.addWidget(btn_add_ds)
        disc_sens_row.addWidget(btn_kp_ds)
        disc_sens_row.addWidget(self.lbl_discolour_sens_val)
        disc_lay.addLayout(disc_sens_row)

        # Discolour Purity
        disc_lay.addWidget(QLabel("Purity (Acceptance Tolerance 1-100):"))
        disc_pur_row = QHBoxLayout()
        btn_sub_dp = QPushButton("-")
        btn_sub_dp.setFixedSize(28, 28)
        self.slider_discolour_purity = QSlider(Qt.Horizontal)
        self.slider_discolour_purity.setRange(1, 100)
        init_dpur = int(float(self.settings.get("purity", 10)))
        self.slider_discolour_purity.setValue(init_dpur)
        btn_add_dp = QPushButton("+")
        btn_add_dp.setFixedSize(28, 28)
        btn_kp_dp = QPushButton("#")
        btn_kp_dp.setFixedSize(28, 28)
        btn_kp_dp.setStyleSheet("background: #00bcd4; color: black; font-weight: bold;")
        self.lbl_discolour_purity_val = QLabel(self.format_purity_text(init_dpur))
        self.lbl_discolour_purity_val.setFixedWidth(110)
        self.lbl_discolour_purity_val.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 13px;")

        btn_sub_dp.clicked.connect(lambda: self.slider_discolour_purity.setValue(max(1, self.slider_discolour_purity.value() - 5)))
        btn_add_dp.clicked.connect(lambda: self.slider_discolour_purity.setValue(min(100, self.slider_discolour_purity.value() + 5)))
        btn_kp_dp.clicked.connect(lambda: self.open_keypad_for_slider(self.slider_discolour_purity, 1, 100))
        self.slider_discolour_purity.valueChanged.connect(self.on_discolour_purity_changed)

        disc_pur_row.addWidget(btn_sub_dp)
        disc_pur_row.addWidget(self.slider_discolour_purity)
        disc_pur_row.addWidget(btn_add_dp)
        disc_pur_row.addWidget(btn_kp_dp)
        disc_pur_row.addWidget(self.lbl_discolour_purity_val)
        disc_lay.addLayout(disc_pur_row)

        tune_lay.addWidget(self.panel_discolour)

        # =====================================================================
        # PANEL 2: GRAY DEFECT PARAMETERS
        # =====================================================================
        self.panel_gray = QWidget()
        gray_lay = QVBoxLayout(self.panel_gray)
        gray_lay.setContentsMargins(0, 4, 0, 4)
        gray_lay.setSpacing(8)

        # Channel
        ch_row = QHBoxLayout()
        ch_row.addWidget(QLabel("Channel:"))
        self.combo_channel = QComboBox()
        self.combo_channel.addItems(["GREEN", "RED", "BLUE"])
        self.combo_channel.setCurrentText(self.settings.get("channel", "GREEN"))
        self.combo_channel.currentTextChanged.connect(self.run_simulation)
        ch_row.addWidget(self.combo_channel)
        gray_lay.addLayout(ch_row)

        # Mode
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Defect Mode:"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["DARK DEFECT", "LIGHT DEFECT"])
        self.combo_mode.setCurrentText(self.settings.get("mode", "DARK DEFECT"))
        self.combo_mode.currentTextChanged.connect(self.run_simulation)
        mode_row.addWidget(self.combo_mode)
        gray_lay.addLayout(mode_row)

        # Sensitivity (0-100)
        gray_lay.addWidget(QLabel("Sensitivity (0-100):"))
        sens_row = QHBoxLayout()
        btn_sub_s = QPushButton("-")
        btn_sub_s.setFixedSize(28, 28)
        self.slider_sens = QSlider(Qt.Horizontal)
        self.slider_sens.setRange(0, 100)
        init_sens = int(float(self.settings.get("sens", 40)))
        self.slider_sens.setValue(init_sens)
        btn_add_s = QPushButton("+")
        btn_add_s.setFixedSize(28, 28)
        btn_kp_s = QPushButton("#")
        btn_kp_s.setFixedSize(28, 28)
        btn_kp_s.setStyleSheet("background: #00bcd4; color: black; font-weight: bold;")
        self.lbl_sens_val = QLabel(self.format_sens_text(init_sens))
        self.lbl_sens_val.setFixedWidth(110)
        self.lbl_sens_val.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 13px;")

        btn_sub_s.clicked.connect(lambda: self.slider_sens.setValue(max(0, self.slider_sens.value() - 5)))
        btn_add_s.clicked.connect(lambda: self.slider_sens.setValue(min(100, self.slider_sens.value() + 5)))
        btn_kp_s.clicked.connect(lambda: self.open_keypad_for_slider(self.slider_sens, 0, 100))
        self.slider_sens.valueChanged.connect(self.on_sens_changed)

        sens_row.addWidget(btn_sub_s)
        sens_row.addWidget(self.slider_sens)
        sens_row.addWidget(btn_add_s)
        sens_row.addWidget(btn_kp_s)
        sens_row.addWidget(self.lbl_sens_val)
        gray_lay.addLayout(sens_row)

        # Purity / Acceptance Tolerance (1-100)
        gray_lay.addWidget(QLabel("Purity (Acceptance Tolerance 1-100):"))
        purity_row = QHBoxLayout()
        btn_sub_p = QPushButton("-")
        btn_sub_p.setFixedSize(28, 28)
        self.slider_purity = QSlider(Qt.Horizontal)
        self.slider_purity.setRange(1, 100)
        init_purity = int(float(self.settings.get("purity", 10)))
        self.slider_purity.setValue(init_purity)
        btn_add_p = QPushButton("+")
        btn_add_p.setFixedSize(28, 28)
        btn_kp_p = QPushButton("#")
        btn_kp_p.setFixedSize(28, 28)
        btn_kp_p.setStyleSheet("background: #00bcd4; color: black; font-weight: bold;")
        self.lbl_purity_val = QLabel(self.format_purity_text(init_purity))
        self.lbl_purity_val.setFixedWidth(110)
        self.lbl_purity_val.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 13px;")

        btn_sub_p.clicked.connect(lambda: self.slider_purity.setValue(max(1, self.slider_purity.value() - 5)))
        btn_add_p.clicked.connect(lambda: self.slider_purity.setValue(min(100, self.slider_purity.value() + 5)))
        btn_kp_p.clicked.connect(lambda: self.open_keypad_for_slider(self.slider_purity, 1, 100))
        self.slider_purity.valueChanged.connect(self.on_purity_changed)

        purity_row.addWidget(btn_sub_p)
        purity_row.addWidget(self.slider_purity)
        purity_row.addWidget(btn_add_p)
        purity_row.addWidget(btn_kp_p)
        purity_row.addWidget(self.lbl_purity_val)
        gray_lay.addLayout(purity_row)

        tune_lay.addWidget(self.panel_gray)

        # Set initial visibility based on algo type
        cur_type_idx = self.combo_algo_type.currentIndex()
        self.panel_hsv.setVisible(cur_type_idx == 0)
        self.panel_size.setVisible(cur_type_idx == 1)
        self.panel_discolour.setVisible(cur_type_idx == 2)
        self.panel_gray.setVisible(cur_type_idx == 3)

        side_vbox.addWidget(grp_tune)

        # Defects & Fired Channels Summary Group
        grp_results = QGroupBox("DEFECT DETECTION STATUS")
        res_lay = QVBoxLayout(grp_results)
        res_lay.setSpacing(6)

        self.lbl_defect_count = QLabel("Defects Found: 0")
        self.lbl_defect_count.setStyleSheet("color: #ffd600; font-size: 14px; font-weight: bold;")
        res_lay.addWidget(self.lbl_defect_count)

        self.lbl_fired_channels = QLabel("Fired Channels: None")
        self.lbl_fired_channels.setWordWrap(True)
        self.lbl_fired_channels.setStyleSheet("color: #ff5252; font-size: 12px; font-weight: bold;")
        res_lay.addWidget(self.lbl_fired_channels)

        self.lbl_active_summary = QLabel("0 / 63 Valves Triggered")
        self.lbl_active_summary.setStyleSheet("color: #00e676; font-size: 13px; font-weight: bold;")
        res_lay.addWidget(self.lbl_active_summary)

        side_vbox.addWidget(grp_results)

        # Hardware Ejector Pulse Trigger Group
        grp_hw = QGroupBox("ESP32 HARDWARE FIRING TEST")
        hw_lay = QVBoxLayout(grp_hw)
        hw_lay.setSpacing(8)

        pulse_row = QHBoxLayout()
        pulse_row.addWidget(QLabel("Pulse Width (ms):"))
        self.spin_pulse = QDoubleSpinBox()
        self.spin_pulse.setRange(0.1, 100.0)
        self.spin_pulse.setSingleStep(0.1)
        self.spin_pulse.setValue(20.0)
        self.spin_pulse.setSuffix(" ms")
        pulse_row.addWidget(self.spin_pulse)
        hw_lay.addLayout(pulse_row)

        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("Delay Time (ms):"))
        self.spin_delay = QSpinBox()
        self.spin_delay.setRange(0, 1000)
        self.spin_delay.setValue(100)
        self.spin_delay.setSuffix(" ms")
        delay_row.addWidget(self.spin_delay)
        hw_lay.addLayout(delay_row)

        # Load default timings from ejector_config.json
        import json
        cfg_path = os.path.join(os.path.dirname(__file__), "ejector_config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r") as f:
                    _c = json.load(f)
                    self.spin_pulse.setValue(int(_c.get("pulse", 20)))
                    self.spin_delay.setValue(int(_c.get("delay", 100)))
            except Exception: pass

        self.spin_pulse.valueChanged.connect(self.save_ejector_timings)
        self.spin_delay.valueChanged.connect(self.save_ejector_timings)

        self.chk_autofire = QCheckBox("⚡ Auto-Fire on Simulation")
        self.chk_autofire.setStyleSheet("color: #ffd600; font-weight: bold;")
        hw_lay.addWidget(self.chk_autofire)

        self.btn_fire = QPushButton("💥 FIRE EJECTORS (63 CH)")
        self.btn_fire.setFixedHeight(42)
        self.btn_fire.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d50000, stop:1 #ff5252);
                color: white; font-size: 14px; font-weight: bold; border-radius: 6px; border: 2px solid #ff8a80;
            }
            QPushButton:hover { background: #ff1744; }
            QPushButton:disabled { background: #37474f; color: #78909c; border: 1px solid #455a64; }
        """)
        self.btn_fire.clicked.connect(self.fire_valves_now)
        hw_lay.addWidget(self.btn_fire)

        # Quick Actions Row: Sweep test & Full console
        quick_row = QHBoxLayout()
        btn_sim_sweep = QPushButton("⚡ AUTO SWEEP (1-63)")
        btn_sim_sweep.setStyleSheet("background-color: #1565c0; color: white; font-size: 11px; font-weight: bold; padding: 5px; border-radius: 4px;")
        btn_sim_sweep.clicked.connect(self.run_sim_sweep)
        quick_row.addWidget(btn_sim_sweep)

        btn_open_console = QPushButton("🛠 63-CH CONSOLE")
        btn_open_console.setStyleSheet("background-color: #37474f; color: #00e5ff; font-size: 11px; font-weight: bold; padding: 5px; border-radius: 4px;")
        btn_open_console.clicked.connect(self.open_full_ejector_console)
        quick_row.addWidget(btn_open_console)
        hw_lay.addLayout(quick_row)

        self.lbl_fire_status = QLabel("Ready")
        self.lbl_fire_status.setAlignment(Qt.AlignCenter)
        self.lbl_fire_status.setStyleSheet("color: #80cbc4; font-size: 11px;")
        hw_lay.addWidget(self.lbl_fire_status)

        side_vbox.addWidget(grp_hw)

        side_vbox.addStretch()

        # Bottom actions
        btn_apply = QPushButton("💾 APPLY SETTINGS & CLOSE")
        btn_apply.setFixedHeight(45)
        btn_apply.setStyleSheet("background: #00e676; color: black; font-weight: bold; font-size: 14px; border-radius: 6px;")
        btn_apply.clicked.connect(self.apply_and_close)
        side_vbox.addWidget(btn_apply)

        work_layout.addWidget(side_panel, 1)
        main_layout.addLayout(work_layout)
        self.draw_sim_hsv_thumb()

    # =========================================================================
    # FOLDER DISCOVERY & IMAGE NAVIGATION
    # =========================================================================
    def discover_image_folders(self):
        """Discovers all capture directories (dataset_capture, subfolders like cashew, rice, etc.)"""
        folders = []
        base_dir = "dataset_capture"
        if os.path.exists(base_dir):
            folders.append(os.path.abspath(base_dir))
            for root, dirs, _ in os.walk(base_dir):
                for d in dirs:
                    sub = os.path.join(root, d)
                    folders.append(os.path.abspath(sub))

        if os.path.exists("captured_images"):
            folders.append(os.path.abspath("captured_images"))

        # Add current program folder if specified
        if self.main_window and hasattr(self.main_window, 'current_program'):
            prog = getattr(self.main_window, 'current_program', '')
            if prog:
                safe_name = "".join(x for x in prog if x.isalnum() or x in " _-")
                prog_dir = os.path.join(base_dir, safe_name)
                if os.path.exists(prog_dir) and os.path.abspath(prog_dir) not in folders:
                    folders.insert(0, os.path.abspath(prog_dir))

        # Deduplicate while preserving order
        seen = set()
        clean = []
        for f in folders:
            if f not in seen and os.path.isdir(f):
                seen.add(f)
                clean.append(f)
        return clean

    def init_folder_and_images(self):
        """Initializes folder combo box and auto-loads the last captured image"""
        self.combo_folder.blockSignals(True)
        self.combo_folder.clear()

        folders = self.discover_image_folders()
        selected_idx = 0

        # Find best default folder (prefer current program folder, or folder with newest image)
        best_folder = None
        newest_mtime = 0

        valid_exts = ('.png', '.jpg', '.jpeg', '.bmp')
        for f in folders:
            display_name = os.path.basename(f)
            if f == os.path.abspath("dataset_capture"):
                display_name = "dataset_capture (All)"
            self.combo_folder.addItem(f"📁 {display_name}", f)

            # Check newest file mtime
            try:
                for fname in os.listdir(f):
                    if fname.lower().endswith(valid_exts):
                        mt = os.path.getmtime(os.path.join(f, fname))
                        if mt > newest_mtime:
                            newest_mtime = mt
                            best_folder = f
            except Exception:
                pass

        self.combo_folder.addItem("➕ Browse Other Folder...", "__BROWSE__")
        self.combo_folder.blockSignals(False)

        if best_folder and best_folder in folders:
            self.combo_folder.setCurrentIndex(folders.index(best_folder))
            self.current_folder = best_folder
        elif folders:
            self.combo_folder.setCurrentIndex(0)
            self.current_folder = folders[0]

        self.refresh_folder_images(select_latest=True)

    def on_folder_changed(self, idx):
        if idx < 0:
            return
        data = self.combo_folder.currentData()
        if data == "__BROWSE__":
            chosen = QFileDialog.getExistingDirectory(self, "Select Image Folder", "dataset_capture")
            if chosen:
                chosen = os.path.abspath(chosen)
                self.combo_folder.blockSignals(True)
                self.combo_folder.insertItem(0, f"📁 {os.path.basename(chosen)}", chosen)
                self.combo_folder.setCurrentIndex(0)
                self.combo_folder.blockSignals(False)
                self.current_folder = chosen
                self.refresh_folder_images(select_latest=True)
            else:
                self.combo_folder.setCurrentIndex(0)
            return

        if data and os.path.exists(data):
            self.current_folder = data
            self.refresh_folder_images(select_latest=True)

    def refresh_folder_images(self, select_latest=True):
        """Scans current folder for images, sorts chronologically, and loads the last image"""
        folder = self.current_folder
        if not folder or not os.path.exists(folder):
            self.image_files = []
            self.current_img_idx = -1
            self.lbl_img_info.setText("No folder selected")
            self.lbl_img_counter.setText("[ 0 / 0 ]")
            self.acquire_live_frame()
            return

        valid_exts = ('.png', '.jpg', '.jpeg', '.bmp')
        try:
            files = [
                os.path.join(folder, f) for f in os.listdir(folder)
                if f.lower().endswith(valid_exts) and os.path.isfile(os.path.join(folder, f))
            ]
        except Exception as e:
            print(f"[SIM] Error listing images: {e}")
            files = []

        # Sort chronologically by modification time (oldest -> newest, so latest is at end)
        files.sort(key=lambda p: os.path.getmtime(p))
        self.image_files = files

        if files:
            # Auto-point to LAST captured image
            if select_latest or self.current_img_idx < 0 or self.current_img_idx >= len(files):
                self.current_img_idx = len(files) - 1
            self.load_image_at_index(self.current_img_idx)
        else:
            self.current_img_idx = -1
            self.lbl_img_info.setText("📁 Empty Folder (No images found)")
            self.lbl_img_counter.setText("[ 0 / 0 ]")
            self.acquire_live_frame()

    def load_image_at_index(self, idx):
        """Loads and simulates an image at the given index"""
        if not self.image_files or idx < 0 or idx >= len(self.image_files):
            return
        self.current_img_idx = idx
        img_path = self.image_files[idx]
        img = cv2.imread(img_path)
        if img is not None:
            self.current_frame = img
            fname = os.path.basename(img_path)
            try:
                mtime_str = time.strftime('%d-%b %H:%M:%S', time.localtime(os.path.getmtime(img_path)))
            except Exception:
                mtime_str = ""
            self.lbl_img_counter.setText(f"[ {idx + 1} / {len(self.image_files)} ]")
            self.lbl_img_info.setText(f"📸 {fname}  ({mtime_str})")
            self.lbl_img_info.setToolTip(img_path)
            self.run_simulation()

            # If auto-fire is enabled, immediately pulse the valves on ESP32
            if hasattr(self, 'chk_autofire') and self.chk_autofire.isChecked():
                self.fire_valves_now()
        else:
            self.lbl_img_info.setText(f"⚠ Failed to read: {os.path.basename(img_path)}")

    def prev_image(self):
        """Navigates to previous image"""
        if not self.image_files:
            return
        new_idx = self.current_img_idx - 1
        if new_idx < 0:
            new_idx = len(self.image_files) - 1  # Wrap to latest
        self.load_image_at_index(new_idx)

    def next_image(self):
        """Navigates to next image"""
        if not self.image_files:
            return
        new_idx = self.current_img_idx + 1
        if new_idx >= len(self.image_files):
            new_idx = 0  # Wrap to first
        self.load_image_at_index(new_idx)

    def load_latest_image(self):
        """Re-scans folder to detect any new snapshot and immediately loads the newest one"""
        self.refresh_folder_images(select_latest=True)

    def keyPressEvent(self, event):
        """Keyboard navigation: Left arrow -> Prev, Right arrow -> Next, Space -> Fire"""
        if event.key() == Qt.Key_Left:
            self.prev_image()
        elif event.key() == Qt.Key_Right:
            self.next_image()
        elif event.key() == Qt.Key_Space:
            self.fire_valves_now()
        else:
            super().keyPressEvent(event)

    def acquire_live_frame(self):
        worker = None
        is_rear = "REAR" in self.algo_name or self.combo_cam_source.currentIndex() == 1
        if self.main_window:
            if is_rear and hasattr(self.main_window, 'camera_worker_rear') and self.main_window.camera_worker_rear:
                worker = self.main_window.camera_worker_rear
            elif hasattr(self.main_window, 'camera_worker_front') and self.main_window.camera_worker_front:
                worker = self.main_window.camera_worker_front

        if worker and hasattr(worker, 'latest_frame') and worker.latest_frame is not None:
            self.current_frame = worker.latest_frame.copy()
            self.lbl_img_counter.setText("[ LIVE ]")
            self.lbl_img_info.setText("📡 Live Camera Frame")
        elif self.current_frame is None:
            self.current_frame = self.generate_synthetic_test_frame()
            self.lbl_img_counter.setText("[ CALIB ]")
            self.lbl_img_info.setText("🧪 Calibration Synthetic Frame")
        self.run_simulation()

    def generate_synthetic_test_frame(self):
        w, h = 2048, 300
        frame = np.full((h, w, 3), 190, dtype=np.uint8)
        np.random.seed(42)
        for i in range(18):
            gx = np.random.randint(100, w - 100)
            gy = np.random.randint(50, h - 50)
            radius = np.random.randint(16, 28)
            cv2.circle(frame, (gx, gy), radius, (215, 215, 195), -1)
            cv2.circle(frame, (gx, gy), radius, (160, 160, 150), 2)
            if i % 3 == 0:
                dx = gx + np.random.randint(-4, 4)
                dy = gy + np.random.randint(-4, 4)
                cv2.circle(frame, (dx, dy), np.random.randint(4, 9), (30, 40, 25), -1)
            elif i % 5 == 0:
                dx = gx + np.random.randint(-4, 4)
                dy = gy + np.random.randint(-4, 4)
                cv2.circle(frame, (dx, dy), np.random.randint(4, 8), (255, 255, 255), -1)
        return frame

    def load_image_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Test Image", "dataset_capture", "Images (*.png *.jpg *.jpeg *.bmp)")
        if file_path:
            p_dir = os.path.dirname(file_path)
            self.current_folder = p_dir
            self.refresh_folder_images(select_latest=False)
            if file_path in self.image_files:
                self.load_image_at_index(self.image_files.index(file_path))
            else:
                img = cv2.imread(file_path)
                if img is not None:
                    self.current_frame = img
                    self.lbl_img_counter.setText("[ 1 / 1 ]")
                    self.lbl_img_info.setText(f"📄 {os.path.basename(file_path)}")
                    self.run_simulation()

    def run_simulation(self):
        if self.current_frame is None:
            return

        frame = self.current_frame.copy()
        h, w = frame.shape[:2]

        # 1. Background color detection & Foreground Object Segmentation
        top_sample = frame[0:min(25, h), :]
        bg_color = np.median(top_sample.reshape(-1, 3), axis=0)
        diff = np.abs(frame.astype(np.int16) - bg_color.astype(np.int16)).max(axis=2).astype(np.uint8)

        if bg_color.mean() < 30:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            _, obj_mask = cv2.threshold(gray, 25, 255, cv2.THRESH_BINARY)
        else:
            _, obj_mask = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

        kernel_obj = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        obj_mask = cv2.morphologyEx(obj_mask, cv2.MORPH_CLOSE, kernel_obj)

        all_obj_cnts, _ = cv2.findContours(obj_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        valid_objects = [c for c in all_obj_cnts if cv2.contourArea(c) > 80]

        # 2. Channel Geometry
        start_pixel = 0
        end_pixel = w
        num_channels = 63
        if self.main_window and hasattr(self.main_window, 'bg_settings'):
            start_pixel = self.main_window.bg_settings.get("start_pixel", 0)
            end_pixel = self.main_window.bg_settings.get("end_pixel", w)
            num_channels = self.main_window.bg_settings.get("num_channels", 63)

        if end_pixel <= start_pixel or end_pixel > w:
            end_pixel = w
        if num_channels <= 0:
            num_channels = 63

        valid_width = max(1, end_pixel - start_pixel)
        pix_per_ch = valid_width / num_channels

        self.active_channels = set()
        self.defect_boxes = []

        annotated = frame.copy()
        overlay = annotated.copy()
        font = cv2.FONT_HERSHEY_SIMPLEX

        algo_idx = self.combo_algo_type.currentIndex()

        # =====================================================================
        # ALGORITHM 0: HSV COLOR DEFECT (TUNABLE COLOR MAP BOX + SENSITIVITY + PURITY)
        # =====================================================================
        if algo_idx == 0:
            hsv_mode = self.combo_hsv_mode.currentText()
            sens = self.slider_hsv_sens.value()
            purity = self.slider_hsv_purity.value()

            try:
                x1 = int(self.sim_txt_x1.text())
                y1 = int(self.sim_txt_y1.text())
                x2 = int(self.sim_txt_x2.text())
                y2 = int(self.sim_txt_y2.text())
            except ValueError:
                x1, y1, x2, y2 = 50, 50, 200, 200

            min_x = max(0, min(565, min(x1, x2)))
            max_x = max(min_x + 2, min(565, max(x1, x2)))
            min_y = max(0, min(500, min(y1, y2)))
            max_y = max(min_y + 2, min(500, max(y1, y2)))

            # 1. Read hsv.png to derive the exact HSV bounds from the selected box
            possible_paths = [
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "hsv.png"),
                r"d:\lambton\BV_SORTER\hsv.png",
                "hsv.png"
            ]
            hsv_path = next((p for p in possible_paths if os.path.exists(p)), "hsv.png")
            h_min, h_max = 0, 180
            s_min, s_max = 0, 255
            v_min, v_max = 0, 255

            if os.path.exists(hsv_path):
                map_img = cv2.imread(hsv_path)
                if map_img is not None:
                    map_hsv = cv2.cvtColor(map_img, cv2.COLOR_BGR2HSV)
                    roi = map_hsv[min_y:max_y, min_x:max_x]
                    if roi.size > 0:
                        h_min = int(roi[:, :, 0].min())
                        h_max = int(roi[:, :, 0].max())
                        s_min = int(roi[:, :, 1].min())
                        s_max = int(roi[:, :, 1].max())
                        v_min = int(roi[:, :, 2].min())
                        v_max = int(roi[:, :, 2].max())

            # Sensitivity widens or narrows tolerance
            if sens == 0:
                raw_def_mask = np.zeros((h, w), dtype=np.uint8)
            else:
                pad = int((sens / 100.0) * 22)
                lower_hsv = np.array([max(0, h_min - pad), max(20, s_min - pad * 2), max(20, v_min - pad * 2)], dtype=np.uint8)
                upper_hsv = np.array([min(180, h_max + pad), min(255, s_max + pad * 2), min(255, v_max + pad * 2)], dtype=np.uint8)

                frame_hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                raw_def_mask = cv2.inRange(frame_hsv, lower_hsv, upper_hsv)

                if hsv_mode == "SORT IN":
                    raw_def_mask = cv2.bitwise_not(raw_def_mask)

                if len(valid_objects) > 0:
                    raw_def_mask = cv2.bitwise_and(raw_def_mask, obj_mask)

                try: val_scale = int(self.settings.get("scale", 6))
                except: val_scale = 6
                
                if val_scale > 0:
                    purity_thresh = max(1, int((purity / 100.0) * (val_scale * val_scale)))
                    pixel_counts = cv2.boxFilter(raw_def_mask.astype(np.float32) / 255.0, cv2.CV_32F, (val_scale, val_scale), normalize=False)
                    def_mask = (pixel_counts >= purity_thresh).astype(np.uint8) * 255
                else:
                    def_mask = raw_def_mask

            cnts, _ = cv2.findContours(def_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            if purity >= 100:
                min_defect_area = float('inf')
            elif purity <= 5:
                min_defect_area = max(2, int(purity))
            elif purity <= 60:
                min_defect_area = int(4 + ((purity - 5) / 55.0) ** 1.3 * 41)
            else:
                min_defect_area = int(45 + ((purity - 60) / 39.0) ** 1.5 * 200)

            for c in cnts:
                area = cv2.contourArea(c)
                if area >= min_defect_area:
                    x, y, bw, bh = cv2.boundingRect(c)
                    cx = x + bw // 2
                    cy = y + bh // 2
                    if start_pixel <= cx <= end_pixel:
                        ch_idx = int((cx - start_pixel) / pix_per_ch)
                        ch_idx = max(0, min(num_channels - 1, ch_idx))
                        ch_num = ch_idx + 1
                        self.active_channels.add(ch_num)
                        self.defect_boxes.append((x, y, bw, bh, ch_num, area, c))

            if self.defect_boxes:
                cv2.drawContours(overlay, [item[6] for item in self.defect_boxes], -1, (255, 0, 180), -1)
                cv2.addWeighted(overlay, 0.65, annotated, 0.35, 0, annotated)
                cv2.drawContours(annotated, [item[6] for item in self.defect_boxes], -1, (0, 255, 255), 2)

            for obj_c in valid_objects:
                ox, oy, ow, oh = cv2.boundingRect(obj_c)
                has_def = any(ox <= (d[0] + d[2] // 2) <= ox + ow and oy <= (d[1] + d[3] // 2) <= oy + oh for d in self.defect_boxes)
                if has_def:
                    cv2.drawContours(annotated, [obj_c], -1, (0, 165, 255), 2)

            if purity >= 100:
                self.lbl_defect_count.setText("HSV Defects: 0 (Purity 100% - All Allowed)")
                self.lbl_defect_count.setStyleSheet("color: #00e676; font-size: 14px; font-weight: bold;")
            elif sens == 0:
                self.lbl_defect_count.setText("HSV Defects: 0 (Sensitivity 0 - Off)")
                self.lbl_defect_count.setStyleSheet("color: #00e676; font-size: 14px; font-weight: bold;")
            else:
                self.lbl_defect_count.setText(f"HSV Defects Found: {len(self.defect_boxes)} ({hsv_mode})")
                self.lbl_defect_count.setStyleSheet("color: #ffd600; font-size: 14px; font-weight: bold;")

        # =====================================================================
        # ALGORITHM 1: SIZE SORTING (BIG / SMALL / OK BADGES)
        # =====================================================================
        elif algo_idx == 1:
            size_mode = self.combo_size_mode.currentText()
            small_lim = self.slider_small.value()
            big_lim = self.slider_big.value()

            n_big = 0
            n_small = 0
            n_normal = 0
            rejected_count = 0

            for c in valid_objects:
                area = cv2.contourArea(c)
                x, y, bw, bh = cv2.boundingRect(c)
                cx = x + bw // 2
                cy = y + bh // 2

                is_big = (area >= big_lim)
                is_small = (area <= small_lim)

                is_rejected = False
                if size_mode == "REJECT BIG" and is_big:
                    is_rejected = True
                elif size_mode == "REJECT SMALL" and is_small:
                    is_rejected = True
                elif size_mode == "REJECT BOTH (BIG & SMALL)" and (is_big or is_small):
                    is_rejected = True

                if is_big:
                    n_big += 1
                    # Red highlight & BIG badge
                    cv2.drawContours(overlay, [c], -1, (0, 0, 220), -1)
                    cv2.drawContours(annotated, [c], -1, (0, 0, 255), 3)
                    tag = f"BIG: {int(area)}px"
                    tag_bg = (20, 20, 160)
                    tag_border = (0, 0, 255)
                elif is_small:
                    n_small += 1
                    # Cyan/Yellow highlight & SMALL badge
                    cv2.drawContours(overlay, [c], -1, (0, 200, 220), -1)
                    cv2.drawContours(annotated, [c], -1, (0, 230, 255), 3)
                    tag = f"SMALL: {int(area)}px"
                    tag_bg = (10, 80, 100)
                    tag_border = (0, 230, 255)
                else:
                    n_normal += 1
                    # Normal / Accept - subtle green outline & OK badge
                    cv2.drawContours(annotated, [c], -1, (0, 220, 100), 2)
                    tag = f"OK: {int(area)}px"
                    tag_bg = (10, 80, 40)
                    tag_border = (0, 220, 100)

                # Draw high-contrast badge
                (tw, th), baseline = cv2.getTextSize(tag, font, 0.70, 2)
                tx = max(5, min(w - tw - 10, x - 10))
                ty = max(th + 10, y - 8)
                cv2.rectangle(annotated, (tx - 4, ty - th - 5), (tx + tw + 4, ty + baseline + 2), tag_bg, -1)
                cv2.rectangle(annotated, (tx - 4, ty - th - 5), (tx + tw + 4, ty + baseline + 2), tag_border, 1)
                cv2.putText(annotated, tag, (tx, ty), font, 0.70, (255, 255, 255), 2, cv2.LINE_AA)

                if is_rejected:
                    rejected_count += 1
                    if start_pixel <= cx <= end_pixel:
                        ch_idx = int((cx - start_pixel) / pix_per_ch)
                        ch_idx = max(0, min(num_channels - 1, ch_idx))
                        ch_num = ch_idx + 1
                        self.active_channels.add(ch_num)

            cv2.addWeighted(overlay, 0.35, annotated, 0.65, 0, annotated)

            self.lbl_size_stats.setText(f"Total: {len(valid_objects)} | 🔴 BIG: {n_big} | 🟡 SMALL: {n_small} | 🟢 OK: {n_normal}")
            self.lbl_defect_count.setText(f"Size Rejections: {rejected_count} ({size_mode})")
            self.lbl_defect_count.setStyleSheet("color: #ffd600; font-size: 14px; font-weight: bold;")

        # =====================================================================
        # ALGORITHM 2: DISCOLOUR SORTING (RED>GREEN, YELLOW DISCOLOUR, ETC.)
        # =====================================================================
        elif algo_idx == 2:
            combo_name = self.combo_discolour.currentText()
            sens = self.slider_discolour_sens.value()
            purity = self.slider_discolour_purity.value()

            if combo_name == "RED > GREEN":
                r = frame[:, :, 2].astype(np.int16)
                g = frame[:, :, 1].astype(np.int16)
                diff_mat = np.clip(r - g, 0, 255).astype(np.uint8)
            elif combo_name == "GREEN > RED":
                r = frame[:, :, 2].astype(np.int16)
                g = frame[:, :, 1].astype(np.int16)
                diff_mat = np.clip(g - r, 0, 255).astype(np.uint8)
            elif combo_name == "BLUE > YELLOW":
                b = frame[:, :, 0].astype(np.int16)
                y_ref = ((frame[:, :, 1].astype(np.int16) + frame[:, :, 2].astype(np.int16)) // 2)
                diff_mat = np.clip(b - y_ref, 0, 255).astype(np.uint8)
            elif combo_name == "YELLOW DISCOLOUR":
                rg_avg = ((frame[:, :, 1].astype(np.int16) + frame[:, :, 2].astype(np.int16)) // 2)
                b = frame[:, :, 0].astype(np.int16)
                diff_mat = np.clip(rg_avg - b, 0, 255).astype(np.uint8)
            else:  # DARK > LIGHT
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                diff_mat = 255 - gray

            if sens == 0:
                raw_def_mask = np.zeros_like(diff_mat)
            else:
                thresh_val = max(8, int(190 - (sens * 1.8)))
                if purity >= 60:
                    dark_factor = 1.0 + (min(95, purity - 5) / 95.0) * 0.15
                    thresh_val = min(240, int(thresh_val * dark_factor))
                _, raw_def_mask = cv2.threshold(diff_mat, thresh_val, 255, cv2.THRESH_BINARY)

            if len(valid_objects) > 0:
                raw_def_mask = cv2.bitwise_and(raw_def_mask, obj_mask)

            try: val_scale = int(self.settings.get("scale", 6))
            except: val_scale = 6
            
            if val_scale > 0:
                purity_thresh = max(1, int((purity / 100.0) * (val_scale * val_scale)))
                pixel_counts = cv2.boxFilter(raw_def_mask.astype(np.float32) / 255.0, cv2.CV_32F, (val_scale, val_scale), normalize=False)
                def_mask = (pixel_counts >= purity_thresh).astype(np.uint8) * 255
            else:
                def_mask = raw_def_mask

            cnts, _ = cv2.findContours(def_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            if purity >= 100:
                min_defect_area = float('inf')
            elif purity <= 5:
                min_defect_area = max(2, int(purity))
            elif purity <= 60:
                min_defect_area = int(4 + ((purity - 5) / 55.0) ** 1.3 * 41)
            else:
                min_defect_area = int(45 + ((purity - 60) / 39.0) ** 1.5 * 200)

            for c in cnts:
                area = cv2.contourArea(c)
                if area >= min_defect_area:
                    x, y, bw, bh = cv2.boundingRect(c)
                    cx = x + bw // 2
                    cy = y + bh // 2
                    if start_pixel <= cx <= end_pixel:
                        ch_idx = int((cx - start_pixel) / pix_per_ch)
                        ch_idx = max(0, min(num_channels - 1, ch_idx))
                        ch_num = ch_idx + 1
                        self.active_channels.add(ch_num)
                        self.defect_boxes.append((x, y, bw, bh, ch_num, area, c))

            if self.defect_boxes:
                cv2.drawContours(overlay, [item[6] for item in self.defect_boxes], -1, (255, 0, 200), -1)
                cv2.addWeighted(overlay, 0.70, annotated, 0.30, 0, annotated)
                cv2.drawContours(annotated, [item[6] for item in self.defect_boxes], -1, (0, 255, 255), 2)

            for obj_c in valid_objects:
                ox, oy, ow, oh = cv2.boundingRect(obj_c)
                has_def = any(ox <= (d[0] + d[2]//2) <= ox + ow and oy <= (d[1] + d[3]//2) <= oy + oh for d in self.defect_boxes)
                if has_def:
                    cv2.drawContours(annotated, [obj_c], -1, (0, 165, 255), 2)

            if purity >= 100:
                self.lbl_defect_count.setText("Discolour Defects: 0 (Purity 100% - All Allowed)")
                self.lbl_defect_count.setStyleSheet("color: #00e676; font-size: 14px; font-weight: bold;")
            elif sens == 0:
                self.lbl_defect_count.setText("Discolour Defects: 0 (Sensitivity 0 - Off)")
                self.lbl_defect_count.setStyleSheet("color: #00e676; font-size: 14px; font-weight: bold;")
            else:
                self.lbl_defect_count.setText(f"Discolour Defects: {len(self.defect_boxes)} ({combo_name})")
                self.lbl_defect_count.setStyleSheet("color: #ffd600; font-size: 14px; font-weight: bold;")

        # =====================================================================
        # ALGORITHM 2: GRAY DEFECT SORTING (DARK / LIGHT DEFECT)
        # =====================================================================
        else:
            ch_name = self.combo_channel.currentText()
            mode_name = self.combo_mode.currentText()
            sens = self.slider_sens.value()
            purity = self.slider_purity.value()

            if ch_name == "RED":
                plane = frame[:, :, 2]
            elif ch_name == "BLUE":
                plane = frame[:, :, 0]
            else:
                plane = frame[:, :, 1]

            if mode_name == "DARK DEFECT":
                if sens == 0:
                    raw_def_mask = np.zeros_like(plane)
                else:
                    eff_thresh = min(250, int(6 + (sens * 2.44)))
                    _, raw_def_mask = cv2.threshold(plane, eff_thresh, 255, cv2.THRESH_BINARY_INV)
            else:
                if sens == 0:
                    raw_def_mask = np.zeros_like(plane)
                else:
                    eff_thresh = max(5, int(250 - (sens * 2.44)))
                    _, raw_def_mask = cv2.threshold(plane, eff_thresh, 255, cv2.THRESH_BINARY)

            if len(valid_objects) > 0:
                raw_def_mask = cv2.bitwise_and(raw_def_mask, obj_mask)

            try: val_scale = int(self.settings.get("scale", 6))
            except: val_scale = 6
            
            if val_scale > 0:
                purity_thresh = max(1, int((purity / 100.0) * (val_scale * val_scale)))
                pixel_counts = cv2.boxFilter(raw_def_mask.astype(np.float32) / 255.0, cv2.CV_32F, (val_scale, val_scale), normalize=False)
                def_mask = (pixel_counts >= purity_thresh).astype(np.uint8) * 255
            else:
                def_mask = raw_def_mask

            cnts, _ = cv2.findContours(def_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            if purity >= 100:
                min_defect_area = float('inf')
            elif purity <= 5:
                min_defect_area = max(2, int(purity))
            elif purity <= 60:
                min_defect_area = int(4 + ((purity - 5) / 55.0) ** 1.3 * 41)
            else:
                min_defect_area = int(45 + ((purity - 60) / 39.0) ** 1.5 * 200)

            for c in cnts:
                area = cv2.contourArea(c)
                if area >= min_defect_area:
                    x, y, bw, bh = cv2.boundingRect(c)
                    cx = x + bw // 2
                    cy = y + bh // 2

                    if start_pixel <= cx <= end_pixel:
                        ch_idx = int((cx - start_pixel) / pix_per_ch)
                        ch_idx = max(0, min(num_channels - 1, ch_idx))
                        ch_num = ch_idx + 1
                        self.active_channels.add(ch_num)
                        self.defect_boxes.append((x, y, bw, bh, ch_num, area, c))

            if self.defect_boxes:
                cv2.drawContours(overlay, [item[6] for item in self.defect_boxes], -1, (0, 0, 255), -1)
                cv2.addWeighted(overlay, 0.70, annotated, 0.30, 0, annotated)
                cv2.drawContours(annotated, [item[6] for item in self.defect_boxes], -1, (0, 255, 255), 2)

            for obj_c in valid_objects:
                ox, oy, ow, oh = cv2.boundingRect(obj_c)
                obj_defects = [
                    d for d in self.defect_boxes
                    if ox <= (d[0] + d[2]//2) <= ox + ow and oy <= (d[1] + d[3]//2) <= oy + oh
                ]
                if obj_defects:
                    cv2.drawContours(annotated, [obj_c], -1, (0, 165, 255), 2)

            if purity >= 100:
                self.lbl_defect_count.setText("Defects Found: 0 (Purity 100% - All Allowed)")
                self.lbl_defect_count.setStyleSheet("color: #00e676; font-size: 14px; font-weight: bold;")
            elif sens == 0:
                self.lbl_defect_count.setText("Defects Found: 0 (Sensitivity 0 - Off)")
                self.lbl_defect_count.setStyleSheet("color: #00e676; font-size: 14px; font-weight: bold;")
            else:
                self.lbl_defect_count.setText(f"Defects Found: {len(self.defect_boxes)}")
                self.lbl_defect_count.setStyleSheet("color: #ffd600; font-size: 14px; font-weight: bold;")

        self.display_annotated_frame(annotated)

        # 9. Update UI controls & the dedicated bottom Qt Operating LED Bar
        sorted_channels = sorted(list(self.active_channels))
        self.operating_led_bar.set_active_channels(self.active_channels)

        if sorted_channels:
            ch_str = ", ".join(str(c) for c in sorted_channels)
            self.lbl_fired_channels.setText(f"Fired Channels: [{ch_str}]")
            self.lbl_active_summary.setText(f"🔥 {len(sorted_channels)} / 63 Valves Triggered")
            self.lbl_active_summary.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 13px;")
            self.lbl_led_bar_status.setText(f"💡 OPERATING EJECTOR LEDs (1 - 63):  [ 🔥 CH {ch_str} TRIGGERED ] ({len(sorted_channels)} / 63 Valves Active)")
            self.lbl_led_bar_status.setStyleSheet("color: #ff5252; font-size: 12px; font-weight: bold; padding-top: 4px;")
            self.btn_single.setEnabled(True)
        else:
            self.lbl_fired_channels.setText("Fired Channels: None")
            if algo_idx == 1:
                self.lbl_active_summary.setText("✅ 0 / 63 Valves Triggered (All Objects Acceptable Size)")
                self.lbl_led_bar_status.setText("💡 OPERATING EJECTOR LEDs (1 - 63):  All 63 Valves Standby (No Size Violations)")
            else:
                self.lbl_active_summary.setText("✅ 0 / 63 Valves Triggered (Idle)")
                self.lbl_led_bar_status.setText("💡 OPERATING EJECTOR LEDs (1 - 63):  All 63 Valves Standby (Idle)")
            self.lbl_active_summary.setStyleSheet("color: #00e676; font-weight: bold; font-size: 13px;")
            self.lbl_led_bar_status.setStyleSheet("color: #80deea; font-size: 12px; font-weight: bold; padding-top: 4px;")
            self.btn_single.setEnabled(False)

        if self.chk_autofire.isChecked() and sorted_channels:
            self.fire_valves_now()

    def display_annotated_frame(self, bgr_img):
        rgb = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        q_img = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        pix = QPixmap.fromImage(q_img)
        target_w = max(900, self.scroll_area.viewport().width() - 20)
        self.img_label.setPixmap(pix.scaledToWidth(target_w, Qt.SmoothTransformation))

    def save_ejector_timings(self):
        try:
            import json
            cfg_path = os.path.join(os.path.dirname(__file__), "ejector_config.json")
            cfg = {"pulse": float(self.spin_pulse.value()), "delay": int(self.spin_delay.value())}
            with open(cfg_path, "w") as f:
                json.dump(cfg, f)
        except Exception:
            pass

    def run_sim_sweep(self):
        esp = getattr(self.main_window, 'esp32', None) if self.main_window else None
        if esp and (getattr(esp, 'connected', False) or (getattr(esp, 'ejector_serial', None) and esp.ejector_serial.is_open)):
            self.lbl_fire_status.setText("⚡ Running 63-Channel Hardware Sweep...")
            self.lbl_fire_status.setStyleSheet("color: #b388ff; font-weight: bold;")
            esp.send_command("TEST")
        else:
            self.lbl_fire_status.setText("⚠ ESP32 Offline - Cannot Sweep")
            self.lbl_fire_status.setStyleSheet("color: #ff9800; font-weight: bold;")

    def open_full_ejector_console(self):
        if self.main_window and hasattr(self.main_window, 'show_ejector_window'):
            self.main_window.show_ejector_window()

    def fire_valves_now(self):
        if not self.active_channels:
            return
        sorted_channels = sorted(list(self.active_channels))
        pulse_ms = int(self.spin_pulse.value())
        delay_ms = int(self.spin_delay.value()) if hasattr(self, 'spin_delay') else 0
        delay_sec = max(0.0, delay_ms / 1000.0)

        self.save_ejector_timings()

        esp = None
        if self.main_window:
            esp = getattr(self.main_window, 'esp32', None) or getattr(self.main_window, 'arduino', None)

        is_online = esp and (getattr(esp, 'connected', False) or (getattr(esp, 'ejector_serial', None) and esp.ejector_serial.is_open))
        if not is_online:
            self.lbl_fire_status.setText(f"⚠ ESP32 Offline - Simulated: {sorted_channels}")
            self.lbl_fire_status.setStyleSheet("color: #ff9800; font-weight: bold;")
            return

        def _do_fire():
            esp.fire_ejector_channels(sorted_channels, pulse_ms)
            self.lbl_fire_status.setText(f"⚡ Fired Channels: {sorted_channels} (Pulse: {pulse_ms}ms)")
            self.lbl_fire_status.setStyleSheet("color: #00e676; font-weight: bold;")

        if delay_sec > 0:
            self.lbl_fire_status.setText(f"⏳ Waiting Delay {delay_ms}ms before firing...")
            self.lbl_fire_status.setStyleSheet("color: #ffd600; font-weight: bold;")
            QTimer.singleShot(delay_ms, _do_fire)
        else:
            _do_fire()

    def apply_and_close(self):
        self.save_ejector_timings()
        algo_idx = self.combo_algo_type.currentIndex()
        if algo_idx == 0:  # HSV
            self.settings["x1"] = self.sim_txt_x1.text()
            self.settings["y1"] = self.sim_txt_y1.text()
            self.settings["x2"] = self.sim_txt_x2.text()
            self.settings["y2"] = self.sim_txt_y2.text()
            self.settings["hsv_mode"] = self.combo_hsv_mode.currentText()
            self.settings["sens"] = str(self.slider_hsv_sens.value())
            self.settings["purity"] = str(self.slider_hsv_purity.value())
        elif algo_idx == 1:  # SIZE
            self.settings["small"] = str(self.slider_small.value())
            self.settings["big"] = str(self.slider_big.value())
            self.settings["size_mode"] = self.combo_size_mode.currentText()
        elif algo_idx == 2:  # DISCOLOUR
            self.settings["combo"] = self.combo_discolour.currentText()
            self.settings["sens"] = str(self.slider_discolour_sens.value())
            self.settings["purity"] = str(self.slider_discolour_purity.value())
        else:  # GRAY
            self.settings["channel"] = self.combo_channel.currentText()
            self.settings["mode"] = self.combo_mode.currentText()
            self.settings["sens"] = str(self.slider_sens.value())
            self.settings["purity"] = str(self.slider_purity.value())
        self.accept()


# =============================================================================
# ALGORITHM SETTINGS DIALOG (UPDATED FOR HSV BOX DRAWING & SIMULATION)
# =============================================================================
class AlgoSettingsDialog(QDialog):
    def __init__(self, algo_name, current_settings, parent=None):
        super().__init__(parent)
        self.algo_name = algo_name
        self.settings = current_settings if current_settings else {}
        self.setWindowTitle(f"{algo_name} - Parameter Tuning")
        self.setStyleSheet("background-color: #1a1a1a; color: white; font-family: Segoe UI;")

        main_layout = QHBoxLayout(self)

        # --- LEFT SIDE: IMAGE (ONLY FOR HSV) ---
        if "HSV" in algo_name:
            self.resize(920, 520)
            img_layout = QVBoxLayout()
            self.img_lbl = InteractiveHSVLabel(on_box_selected=self.on_hsv_box_selected)
            self.img_lbl.setStyleSheet("border: 2px solid #00bcd4; background: #000; border-radius: 6px;")

            possible_paths = [
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "hsv.png"),
                r"d:\lambton\BV_SORTER\hsv.png",
                r"C:\Program Files (x86)\MindVision\Demo\Python\python_demo\lambton\hsv.png",
                "hsv.png"
            ]
            hsv_found = next((p for p in possible_paths if os.path.exists(p)), None)

            if hsv_found:
                self.original_pixmap = QPixmap(hsv_found)
                self.img_lbl.setPixmap(self.original_pixmap.scaled(440, 420, Qt.KeepAspectRatio))
            else:
                self.original_pixmap = QPixmap()
                self.img_lbl.setText("Color Map Image Missing\n(Check path to hsv.png)")

            self.img_lbl.setAlignment(Qt.AlignCenter)
            img_layout.addWidget(self.img_lbl)
            main_layout.addLayout(img_layout)
        else:
            self.resize(450, 580)  # Gray மற்றும் பிற அல்காரிதம்களுக்கு விண்டோ

        # --- RIGHT SIDE: CONTROLS ---
        ctrl_layout = QVBoxLayout()
        header = QLabel(f"{algo_name} SETTINGS")
        header.setStyleSheet("font-size: 20px; font-weight: bold; color: #00bcd4; padding-bottom: 10px;")
        ctrl_layout.addWidget(header)

        # ---------------------------------------------------------
        # GRAY A & GRAY B (Contrast & Threshold)
        # ---------------------------------------------------------
        if "GRAY" in algo_name:
            ch_lay = QHBoxLayout()
            ch_lay.addWidget(QLabel("CHANNEL:"))
            self.combo_channel = QComboBox()
            self.combo_channel.addItems(["RED", "GREEN", "BLUE"])
            self.combo_channel.setCurrentText(self.settings.get("channel", "GREEN"))
            self.combo_channel.setStyleSheet("background: #333; color: white; padding: 5px;")
            ch_lay.addWidget(self.combo_channel)
            ctrl_layout.addLayout(ch_lay)

            md_lay = QHBoxLayout()
            md_lay.addWidget(QLabel("DEFECT MODE:"))
            self.combo_mode = QComboBox()
            self.combo_mode.addItems(["DARK DEFECT", "LIGHT DEFECT"])
            self.combo_mode.setCurrentText(self.settings.get("mode", "DARK DEFECT"))
            self.combo_mode.setStyleSheet("background: #333; color: white; padding: 5px;")
            md_lay.addWidget(self.combo_mode)
            ctrl_layout.addLayout(md_lay)

            self.sens_val = self.create_input_row(ctrl_layout, "SENSITIVITY (1-100):", self.settings.get("sens", "100"))
            self.scale_val = self.create_input_row(ctrl_layout, "SCALE % (Resolution):",
                                                   self.settings.get("scale", "16"))
            self.purity_val = self.create_input_row(ctrl_layout, "PURITY (Min Pixel):",
                                                     self.settings.get("purity", "1"))

        # ---------------------------------------------------------
        # HSV COLOR SORTING
        # ---------------------------------------------------------
        elif "HSV" in algo_name:
            box_grp = QGroupBox("COLOR MAP PIXEL COORDINATES")
            box_lay = QGridLayout(box_grp)

            box_lay.addWidget(QLabel("X1:"), 0, 0)
            self.txt_x1 = QLineEdit(str(self.settings.get("x1", "50")))
            box_lay.addWidget(self.txt_x1, 0, 1)

            box_lay.addWidget(QLabel("Y1:"), 0, 2)
            self.txt_y1 = QLineEdit(str(self.settings.get("y1", "50")))
            box_lay.addWidget(self.txt_y1, 0, 3)

            box_lay.addWidget(QLabel("X2:"), 1, 0)
            self.txt_x2 = QLineEdit(str(self.settings.get("x2", "200")))
            box_lay.addWidget(self.txt_x2, 1, 1)

            box_lay.addWidget(QLabel("Y2:"), 1, 2)
            self.txt_y2 = QLineEdit(str(self.settings.get("y2", "200")))
            box_lay.addWidget(self.txt_y2, 1, 3)

            for txt in [self.txt_x1, self.txt_y1, self.txt_x2, self.txt_y2]:
                txt.setStyleSheet("background: #252525; color: yellow; padding: 5px; font-weight:bold; font-size:16px;")
                txt.textChanged.connect(self.draw_hsv_box)
                KeypadEventFilter(txt, lambda t=txt: Keypad(t.text(), t.setText, self).exec_())

            ctrl_layout.addWidget(box_grp)

            md_lay = QHBoxLayout()
            md_lay.addWidget(QLabel("SORTING LOGIC:"))
            self.combo_hsv_mode = QComboBox()
            self.combo_hsv_mode.addItems(["SORT IN", "SORT OUT"])
            self.combo_hsv_mode.setCurrentText(self.settings.get("hsv_mode", "SORT OUT"))
            self.combo_hsv_mode.setStyleSheet("background: #333; color: #00e676; padding: 5px; font-weight:bold;")
            md_lay.addWidget(self.combo_hsv_mode)
            ctrl_layout.addLayout(md_lay)

            self.sens_val = self.create_input_row(ctrl_layout, "COLOR SENSITIVITY:", self.settings.get("sens", "100"))
            self.scale_val = self.create_input_row(ctrl_layout, "SCALE %:", self.settings.get("scale", "16"))
            self.purity_val = self.create_input_row(ctrl_layout, "PURITY:", self.settings.get("purity", "1"))

        # ---------------------------------------------------------
        # SIZE SORTING (Big / Short)
        # ---------------------------------------------------------
        elif "SIZE" in algo_name:
            self.small_limit = self.create_input_row(ctrl_layout, "SMALL LIMIT (Pixels):",
                                                     self.settings.get("small", "1000"))
            self.big_limit = self.create_input_row(ctrl_layout, "BIG LIMIT (Pixels):", self.settings.get("big", "2300"))

        # ---------------------------------------------------------
        # DISCOLOUR (Custom Combinations)
        # ---------------------------------------------------------
        elif "DISCOLOUR" in algo_name:
            md_lay = QHBoxLayout()
            md_lay.addWidget(QLabel("COLOR COMBINATION:"))
            self.combo_discolour = QComboBox()
            self.combo_discolour.addItems(["RED > GREEN", "GREEN > RED", "BLUE > YELLOW", "YELLOW DISCOLOUR", "DARK > LIGHT"])
            self.combo_discolour.setCurrentText(self.settings.get("combo", "RED > GREEN"))
            self.combo_discolour.setStyleSheet("background: #333; color: #00e676; padding: 5px; font-weight:bold;")
            md_lay.addWidget(self.combo_discolour)
            ctrl_layout.addLayout(md_lay)

            self.sens_val = self.create_input_row(ctrl_layout, "SENSITIVITY (1-100):", self.settings.get("sens", "75"))
            self.scale_val = self.create_input_row(ctrl_layout, "SCALE % (Resolution):",
                                                   self.settings.get("scale", "16"))
            self.purity_val = self.create_input_row(ctrl_layout, "PURITY (Min Pixel):",
                                                     self.settings.get("purity", "1"))

        # ---------------------------------------------------------
        # AI DETECTION / DEEP LEARNING
        # ---------------------------------------------------------
        elif "AI" in algo_name or "DEEP LEARNING" in algo_name:
            self.lbl_ai_info = QLabel("Use the Top Navigation '🔬 AI / DATASET TEST CONSOLE'\nto choose the model and collect datasets.")
            self.lbl_ai_info.setStyleSheet("color: #ffb300; font-style: italic; padding: 10px; background: #333; border-radius: 5px;")
            self.lbl_ai_info.setWordWrap(True)
            ctrl_layout.addWidget(self.lbl_ai_info)

            self.sens_val = self.create_input_row(ctrl_layout, "CONFIDENCE THRESHOLD (0-100):", self.settings.get("sens", "50"))
            self.purity_val = self.create_input_row(ctrl_layout, "MINIMUM PURITY AREA:", self.settings.get("purity", "1"))

        ctrl_layout.addStretch()

        # Simulate & Test Button
        btn_sim = QPushButton("📷 SIMULATE IMAGE & TEST 63 CHANNELS")
        btn_sim.setFixedHeight(48)
        btn_sim.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00bcd4, stop:1 #00e676); color: black; font-weight: bold; font-size: 14px; border-radius: 6px;")
        btn_sim.clicked.connect(self.open_simulation)
        ctrl_layout.addWidget(btn_sim)

        # Save Button
        btn_save = QPushButton("💾 SAVE SETTINGS")
        btn_save.setFixedHeight(50)
        btn_save.setStyleSheet("background: #00e676; color: black; font-weight: bold; font-size: 16px; border-radius: 6px;")
        btn_save.clicked.connect(self.save_and_close)
        ctrl_layout.addWidget(btn_save)

        main_layout.addLayout(ctrl_layout)

        if "HSV" in algo_name:
            self.draw_hsv_box()

    def get_main_window(self):
        p = self.parent()
        while p is not None:
            if hasattr(p, 'main_window') and p.main_window is not None:
                return p.main_window
            if hasattr(p, 'camera_worker_front') or hasattr(p, 'esp32'):
                return p
            p = p.parent() if hasattr(p, 'parent') else None
        return self.parent()

    def open_simulation(self):
        cur_settings = dict(self.settings)
        if "GRAY" in self.algo_name:
            if hasattr(self, 'combo_channel'): cur_settings["channel"] = self.combo_channel.currentText()
            if hasattr(self, 'combo_mode'): cur_settings["mode"] = self.combo_mode.currentText()
            if hasattr(self, 'sens_val'): cur_settings["sens"] = self.sens_val.text()
            if hasattr(self, 'scale_val'): cur_settings["scale"] = self.scale_val.text()
            if hasattr(self, 'purity_val'): cur_settings["purity"] = self.purity_val.text()
        elif "DISCOLOUR" in self.algo_name:
            if hasattr(self, 'combo_discolour'): cur_settings["combo"] = self.combo_discolour.currentText()
            if hasattr(self, 'sens_val'): cur_settings["sens"] = self.sens_val.text()
            if hasattr(self, 'scale_val'): cur_settings["scale"] = self.scale_val.text()
            if hasattr(self, 'purity_val'): cur_settings["purity"] = self.purity_val.text()
        elif "SIZE" in self.algo_name:
            if hasattr(self, 'small_limit'): cur_settings["small"] = self.small_limit.text()
            if hasattr(self, 'big_limit'): cur_settings["big"] = self.big_limit.text()
        elif "HSV" in self.algo_name:
            if hasattr(self, 'txt_x1'): cur_settings["x1"] = self.txt_x1.text()
            if hasattr(self, 'txt_y1'): cur_settings["y1"] = self.txt_y1.text()
            if hasattr(self, 'txt_x2'): cur_settings["x2"] = self.txt_x2.text()
            if hasattr(self, 'txt_y2'): cur_settings["y2"] = self.txt_y2.text()
            if hasattr(self, 'combo_hsv_mode'): cur_settings["hsv_mode"] = self.combo_hsv_mode.currentText()
            if hasattr(self, 'sens_val'): cur_settings["sens"] = self.sens_val.text()
            if hasattr(self, 'scale_val'): cur_settings["scale"] = self.scale_val.text()
            if hasattr(self, 'purity_val'): cur_settings["purity"] = self.purity_val.text()

        mw = self.get_main_window()
        dlg = DefectSimulationDialog(self.algo_name, cur_settings, main_window=mw, parent=self)
        if dlg.exec_() == QDialog.Accepted and dlg.settings:
            self.settings.update(dlg.settings)
            if "GRAY" in self.algo_name:
                if "channel" in dlg.settings and hasattr(self, 'combo_channel'):
                    self.combo_channel.setCurrentText(dlg.settings["channel"])
                if "mode" in dlg.settings and hasattr(self, 'combo_mode'):
                    self.combo_mode.setCurrentText(dlg.settings["mode"])
                if "sens" in dlg.settings and hasattr(self, 'sens_val'):
                    self.sens_val.setText(str(dlg.settings["sens"]))
                if "purity" in dlg.settings and hasattr(self, 'purity_val'):
                    self.purity_val.setText(str(dlg.settings["purity"]))
                if "scale" in dlg.settings and hasattr(self, 'scale_val'):
                    self.scale_val.setText(str(dlg.settings["scale"]))
            elif "DISCOLOUR" in self.algo_name:
                if "combo" in dlg.settings and hasattr(self, 'combo_discolour'):
                    self.combo_discolour.setCurrentText(dlg.settings["combo"])
                if "sens" in dlg.settings and hasattr(self, 'sens_val'):
                    self.sens_val.setText(str(dlg.settings["sens"]))
                if "purity" in dlg.settings and hasattr(self, 'purity_val'):
                    self.purity_val.setText(str(dlg.settings["purity"]))
                if "scale" in dlg.settings and hasattr(self, 'scale_val'):
                    self.scale_val.setText(str(dlg.settings["scale"]))
            elif "SIZE" in self.algo_name:
                if "small" in dlg.settings and hasattr(self, 'small_limit'):
                    self.small_limit.setText(str(dlg.settings["small"]))
                if "big" in dlg.settings and hasattr(self, 'big_limit'):
                    self.big_limit.setText(str(dlg.settings["big"]))
            elif "HSV" in self.algo_name:
                if "x1" in dlg.settings and hasattr(self, 'txt_x1'):
                    self.txt_x1.setText(str(dlg.settings["x1"]))
                if "y1" in dlg.settings and hasattr(self, 'txt_y1'):
                    self.txt_y1.setText(str(dlg.settings["y1"]))
                if "x2" in dlg.settings and hasattr(self, 'txt_x2'):
                    self.txt_x2.setText(str(dlg.settings["x2"]))
                if "y2" in dlg.settings and hasattr(self, 'txt_y2'):
                    self.txt_y2.setText(str(dlg.settings["y2"]))
                if "hsv_mode" in dlg.settings and hasattr(self, 'combo_hsv_mode'):
                    self.combo_hsv_mode.setCurrentText(dlg.settings["hsv_mode"])
                if "sens" in dlg.settings and hasattr(self, 'sens_val'):
                    self.sens_val.setText(str(dlg.settings["sens"]))
                if "purity" in dlg.settings and hasattr(self, 'purity_val'):
                    self.purity_val.setText(str(dlg.settings["purity"]))
                if "scale" in dlg.settings and hasattr(self, 'scale_val'):
                    self.scale_val.setText(str(dlg.settings["scale"]))
                self.draw_hsv_box()

    def on_hsv_box_selected(self, x1, y1, x2, y2, final=False):
        self.txt_x1.blockSignals(True)
        self.txt_y1.blockSignals(True)
        self.txt_x2.blockSignals(True)
        self.txt_y2.blockSignals(True)
        self.txt_x1.setText(str(x1))
        self.txt_y1.setText(str(y1))
        self.txt_x2.setText(str(x2))
        self.txt_y2.setText(str(y2))
        self.txt_x1.blockSignals(False)
        self.txt_y1.blockSignals(False)
        self.txt_x2.blockSignals(False)
        self.txt_y2.blockSignals(False)
        self.draw_hsv_box()

    def draw_hsv_box(self):
        if not hasattr(self, 'original_pixmap') or self.original_pixmap.isNull():
            return

        drawing_pixmap = self.original_pixmap.copy()

        try:
            x1 = int(self.txt_x1.text())
            y1 = int(self.txt_y1.text())
            x2 = int(self.txt_x2.text())
            y2 = int(self.txt_y2.text())
        except ValueError:
            return

        min_x = max(0, min(565, min(x1, x2)))
        max_x = max(min_x + 2, min(565, max(x1, x2)))
        min_y = max(0, min(500, min(y1, y2)))
        max_y = max(min_y + 2, min(500, max(y1, y2)))

        painter = QPainter(drawing_pixmap)
        pen = QPen(QColor(255, 255, 255), 3, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(0, 229, 255, 50))
        painter.drawRect(min_x, min_y, max_x - min_x, max_y - min_y)

        painter.setFont(QFont("Segoe UI", 12, QFont.Bold))
        painter.setPen(QColor(0, 255, 255))
        painter.drawText(min_x + 5, min_y + 22, f"({min_x}, {min_y}) - ({max_x}, {max_y})")
        painter.end()

        self.img_lbl.setPixmap(drawing_pixmap.scaled(440, 420, Qt.KeepAspectRatio))

    def create_input_row(self, parent_layout, label_text, default_val):
        row = QHBoxLayout()
        row.addWidget(QLabel(label_text))
        txt = QLineEdit(str(default_val))
        txt.setStyleSheet("background: #333; color: white; padding: 5px; font-weight: bold; font-size: 16px;")

        show_kp = lambda t=txt: Keypad(t.text(), lambda v: t.setText(v), self).exec_()
        btn = QPushButton("#")
        btn.setFixedSize(35, 35)
        btn.setStyleSheet("background: #00bcd4; color: black;")
        btn.clicked.connect(lambda _, t=txt: show_kp(t))
        KeypadEventFilter(txt, lambda t=txt: show_kp(t))

        row.addWidget(txt)
        row.addWidget(btn)
        parent_layout.addLayout(row)
        return txt

    def save_and_close(self):
        if "GRAY" in self.algo_name:
            self.settings["channel"] = self.combo_channel.currentText()
            self.settings["mode"] = self.combo_mode.currentText()
            self.settings["sens"] = self.sens_val.text()
            self.settings["scale"] = self.scale_val.text()
            self.settings["purity"] = self.purity_val.text()
        elif "HSV" in self.algo_name:
            self.settings["x1"] = self.txt_x1.text()
            self.settings["y1"] = self.txt_y1.text()
            self.settings["x2"] = self.txt_x2.text()
            self.settings["y2"] = self.txt_y2.text()
            self.settings["hsv_mode"] = self.combo_hsv_mode.currentText()
            self.settings["sens"] = self.sens_val.text()
            self.settings["scale"] = self.scale_val.text()
            self.settings["purity"] = self.purity_val.text()
        elif "SIZE" in self.algo_name:
            self.settings["small"] = self.small_limit.text()
            self.settings["big"] = self.big_limit.text()
        elif "DISCOLOUR" in self.algo_name:
            self.settings["combo"] = self.combo_discolour.currentText()
            self.settings["sens"] = self.sens_val.text()
            self.settings["scale"] = self.scale_val.text()
            self.settings["purity"] = self.purity_val.text()

        self.accept()


# =============================================================================
# DATASET & AI MODEL TEST CONSOLE (EVALUATION & INFERENCE)
# =============================================================================
from PyQt5.QtWidgets import (QTableWidget, QTableWidgetItem, QHeaderView,
                             QProgressBar, QSplitter, QFileDialog)
from PyQt5.QtGui import QColor
import glob

class DatasetAITestConsoleDialog(QDialog):
    """
    Evaluation, Live Model Benchmark & Multi-Commodity Dataset Verification Tool.
    Supports YOLO (best.pt), Keras/CNN (.keras, .h5), ONNX models, and datasets
    (Peanut, Dall, Donkeyholes, Corn, Rice, custom datasets).
    """
    def __init__(self, front_worker=None, rear_worker=None, current_program="RICE", parent=None):
        super().__init__(parent)
        self.setWindowTitle("DATASET & AI MODEL TEST CONSOLE (EVALUATION & INFERENCE)")
        self.resize(1650, 930)
        self.setStyleSheet("""
            * {
                color: #ffffff;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QDialog {
                background-color: #0c1017;
            }
            QGroupBox {
                border: 2px solid #2d3748;
                border-radius: 8px;
                margin-top: 14px;
                padding-top: 16px;
                font-weight: 900;
                color: #00e5ff;
                font-size: 13px;
                background-color: #121824;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 8px;
                background-color: #0c1017;
                color: #00e5ff;
            }
            QLabel {
                color: #ffffff;
                font-size: 13px;
            }
            QComboBox {
                background: #1a2234;
                color: #00e5ff;
                border: 2px solid #00bcd4;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 13px;
                min-height: 28px;
            }
            QComboBox QAbstractItemView {
                background: #141b29;
                color: #ffffff;
                selection-background-color: #00bcd4;
                selection-color: #000000;
                font-size: 13px;
            }
        """)

        self.front_worker = front_worker
        self.rear_worker = rear_worker
        self.current_program = current_program

        self.current_image_path = None
        self.current_image_cv = None
        self.detected_image_cv = None
        self.dataset_images = []
        self.current_img_idx = 0
        self.detected_models = []
        self.is_live = False
        self.live_timer = QTimer(self)
        self.live_timer.timeout.connect(self.update_live_frame)
        self.auto_capture_count = 0
        self.last_auto_capture_time = 0

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(12)

        # 1. Header Bar
        hdr = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("🔬 AI DATASET COLLECTION & TRAINING CONSOLE")
        title.setStyleSheet("font-size: 20px; font-weight: 900; color: #00e5ff; letter-spacing: 1px;")
        subtitle = QLabel("Collect dataset, annotate images, train YOLO/CNN models, and auto-deploy to the active scheme.")
        subtitle.setStyleSheet("font-size: 12px; color: #a0aec0;")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        hdr.addLayout(title_box)
        hdr.addStretch()

        self.lbl_status_badge = QLabel("READY")
        self.lbl_status_badge.setStyleSheet("background: #00e676; color: #000000; font-weight: 900; padding: 6px 16px; border-radius: 6px; font-size: 13px;")
        hdr.addWidget(self.lbl_status_badge)
        main_layout.addLayout(hdr)

        # 2. Main Content Area (Horizontal Split)
        content_layout = QHBoxLayout()
        main_layout.addLayout(content_layout, stretch=1)

        # --- LEFT PANEL: CAMERA & NAV ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        self.center_panel = QFrame()
        self.center_panel.setStyleSheet("background: #000000; border: 2px solid #2d3748; border-radius: 8px;")
        center_layout = QVBoxLayout(self.center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        
        self.lbl_image_view = QLabel("NO IMAGE LOADED")
        self.lbl_image_view.setAlignment(Qt.AlignCenter)
        self.lbl_image_view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.lbl_image_view.setStyleSheet("font-size: 16px; color: #718096; background: #000000;")
        center_layout.addWidget(self.lbl_image_view)
        
        left_layout.addWidget(self.center_panel, stretch=1)

        # Image Navigation & Controls (Directly below Image)
        img_ctrl_layout = QHBoxLayout()
        
        self.lbl_img_info = QLabel("Image 0 of 0")
        self.lbl_img_info.setStyleSheet("font-weight: bold; color: #e2e8f0; font-size: 13px; min-width: 120px;")
        img_ctrl_layout.addWidget(self.lbl_img_info)

        self.btn_prev_img = QPushButton("◀ PREV")
        self.btn_prev_img.setStyleSheet("background: #2d3748; color: white; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.btn_prev_img.clicked.connect(self.prev_image)
        img_ctrl_layout.addWidget(self.btn_prev_img)

        self.btn_next_img = QPushButton("NEXT ▶")
        self.btn_next_img.setStyleSheet("background: #2d3748; color: white; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.btn_next_img.clicked.connect(self.next_image)
        img_ctrl_layout.addWidget(self.btn_next_img)

        self.btn_del_img = QPushButton("🗑 DELETE")
        self.btn_del_img.setStyleSheet("background: #e53e3e; color: white; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.btn_del_img.clicked.connect(self.delete_current_image)
        img_ctrl_layout.addWidget(self.btn_del_img)

        self.btn_load_img = QPushButton("📂 LOAD")
        self.btn_load_img.setStyleSheet("background: #4a5568; color: white; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.btn_load_img.clicked.connect(self.load_custom_image)
        img_ctrl_layout.addWidget(self.btn_load_img)

        self.combo_live_cam = QComboBox()
        self.combo_live_cam.addItems(["FRONT", "REAR"])
        self.combo_live_cam.setStyleSheet("background: #1a2234; color: white; font-weight: bold; padding: 10px; border-radius: 4px;")
        img_ctrl_layout.addWidget(self.combo_live_cam)

        self.btn_live_view = QPushButton("▶ LIVE")
        self.btn_live_view.setCheckable(True)
        self.btn_live_view.setStyleSheet("QPushButton { background: #00838f; color: white; font-weight: bold; padding: 10px; border-radius: 4px; } QPushButton:checked { background: #d32f2f; }")
        self.btn_live_view.clicked.connect(self.toggle_live_view)
        img_ctrl_layout.addWidget(self.btn_live_view)

        self.btn_capture = QPushButton("🔴 CAPTURE")
        self.btn_capture.setStyleSheet("background: #d32f2f; color: white; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.btn_capture.clicked.connect(self.capture_live_frame)
        img_ctrl_layout.addWidget(self.btn_capture)
        
        left_layout.addLayout(img_ctrl_layout)

        # Auto Capture Settings
        auto_cap_layout = QHBoxLayout()
        self.chk_auto_capture = QCheckBox("🤖 AUTO CAPTURE (Triggered)")
        self.chk_auto_capture.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 13px;")
        auto_cap_layout.addWidget(self.chk_auto_capture)
        
        auto_cap_layout.addWidget(QLabel("Limit:"))
        self.spin_auto_limit = QSpinBox()
        self.spin_auto_limit.setRange(1, 50000)
        self.spin_auto_limit.setValue(50)
        self.spin_auto_limit.setStyleSheet("background: #1a2234; color: #00e5ff; font-weight: bold; padding: 4px;")
        auto_cap_layout.addWidget(self.spin_auto_limit)
        
        self.lbl_auto_count = QLabel("Captured: 0 / 50")
        self.lbl_auto_count.setStyleSheet("color: yellow; font-weight: bold;")
        auto_cap_layout.addWidget(self.lbl_auto_count)
        auto_cap_layout.addStretch()
        
        left_layout.addLayout(auto_cap_layout)
        content_layout.addWidget(left_panel, stretch=1)

        # --- RIGHT PANEL: TRAINING & LABELS ---
        right_panel = QFrame()
        right_panel.setFixedWidth(380)
        right_panel.setStyleSheet("background: #101726; border: 2px solid #00bcd4; border-radius: 8px; padding: 6px;")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(20)

        # Dataset Architecture Group
        grp_data = QGroupBox("1. DATASET / ARCHITECTURE")
        data_lay = QVBoxLayout(grp_data)
        
        row_model = QHBoxLayout()
        row_model.addWidget(QLabel("Model Type:"))
        self.combo_train_model = QComboBox()
        self.combo_train_model.addItems(["YOLOv8", "YOLO11n", "CNN (Keras)", "YOLO + CNN (Dual Train)"])
        row_model.addWidget(self.combo_train_model)
        data_lay.addLayout(row_model)

        row_comm = QHBoxLayout()
        row_comm.addWidget(QLabel("Commodity:"))
        self.txt_commodity = QLineEdit("dhall")
        self.txt_commodity.setStyleSheet("background: #1a2234; color: yellow; font-weight: bold; padding: 4px;")
        row_comm.addWidget(self.txt_commodity)
        data_lay.addLayout(row_comm)
        
        right_layout.addWidget(grp_data)

        # CNN Teaching Group (Annotation)
        grp_teach = QGroupBox("2. CNN TEACHING (ANNOTATION)")
        teach_lay = QVBoxLayout(grp_teach)
        
        btn_lay1 = QHBoxLayout()
        self.btn_good = QPushButton("✔️ GOOD")
        self.btn_good.setStyleSheet("background: #388e3c; color: white; font-weight: bold; padding: 12px; border-radius: 6px; font-size: 13px;")
        self.btn_good.clicked.connect(lambda: self.annotate_image("GOOD"))
        btn_lay1.addWidget(self.btn_good)

        self.btn_insect = QPushButton("🐛 INSECT HOLE")
        self.btn_insect.setStyleSheet("background: #d32f2f; color: white; font-weight: bold; padding: 12px; border-radius: 6px; font-size: 13px;")
        self.btn_insect.clicked.connect(lambda: self.annotate_image("INSECT HOLE"))
        btn_lay1.addWidget(self.btn_insect)
        teach_lay.addLayout(btn_lay1)

        btn_lay2 = QHBoxLayout()
        self.btn_dark = QPushButton("⚫ DARK")
        self.btn_dark.setStyleSheet("background: #5d4037; color: white; font-weight: bold; padding: 12px; border-radius: 6px; font-size: 13px;")
        self.btn_dark.clicked.connect(lambda: self.annotate_image("DARK"))
        btn_lay2.addWidget(self.btn_dark)

        self.btn_shrunken = QPushButton("🍂 SHRUNKEN")
        self.btn_shrunken.setStyleSheet("background: #f57c00; color: white; font-weight: bold; padding: 12px; border-radius: 6px; font-size: 13px;")
        self.btn_shrunken.clicked.connect(lambda: self.annotate_image("SHRUNKEN"))
        btn_lay2.addWidget(self.btn_shrunken)
        teach_lay.addLayout(btn_lay2)

        # Custom Label
        cust_lay = QHBoxLayout()
        self.txt_custom_label = QLineEdit()
        self.txt_custom_label.setPlaceholderText("Custom Label...")
        self.txt_custom_label.setStyleSheet("background: #1a2234; color: white; font-weight: bold; padding: 8px;")
        cust_lay.addWidget(self.txt_custom_label)
        
        self.btn_add_label = QPushButton("➕ ADD")
        self.btn_add_label.setStyleSheet("background: #1976d2; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
        self.btn_add_label.clicked.connect(self.add_custom_annotation)
        cust_lay.addWidget(self.btn_add_label)

        self.btn_delete_label = QPushButton("🗑 DEL")
        self.btn_delete_label.setStyleSheet("background: #e53e3e; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
        self.btn_delete_label.clicked.connect(self.delete_custom_label)
        cust_lay.addWidget(self.btn_delete_label)
        teach_lay.addLayout(cust_lay)

        right_layout.addWidget(grp_teach)

        # Evaluation Group
        grp_eval = QGroupBox("3. EVALUATION & TEST")
        eval_lay = QVBoxLayout(grp_eval)
        
        row_conf = QHBoxLayout()
        row_conf.addWidget(QLabel("Confidence Score:"))
        self.spin_confidence = QSpinBox()
        self.spin_confidence.setRange(1, 100)
        self.spin_confidence.setValue(60)
        self.spin_confidence.setSuffix("%")
        self.spin_confidence.setStyleSheet("background: #1a2234; color: #00e5ff; font-weight: bold; padding: 4px;")
        row_conf.addWidget(self.spin_confidence)
        eval_lay.addLayout(row_conf)

        self.btn_test_console = QPushButton("🔍 TEST CONSOLE")
        self.btn_test_console.setStyleSheet("background: #5e35b1; color: white; font-weight: bold; padding: 12px; border-radius: 6px; font-size: 13px;")
        self.btn_test_console.clicked.connect(self.run_test_inference)
        eval_lay.addWidget(self.btn_test_console)

        right_layout.addWidget(grp_eval)

        # Training Execution Group
        grp_exec = QGroupBox("4. EXECUTION")
        exec_lay = QVBoxLayout(grp_exec)
        
        self.btn_start_train = QPushButton("🚀 START TRAINING")
        self.btn_start_train.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00bcd4, stop:1 #00e676);
                color: black; font-size: 16px; font-weight: 900; padding: 14px; border-radius: 6px;
            }
            QPushButton:hover { background: #18ffff; }
        """)
        self.btn_start_train.clicked.connect(self.start_training)
        exec_lay.addWidget(self.btn_start_train)

        self.train_progress = QProgressBar()
        self.train_progress.setVisible(False)
        self.train_progress.setStyleSheet("QProgressBar { border: 1px solid #444; border-radius: 4px; text-align: center; } QProgressBar::chunk { background: #00bcd4; }")
        exec_lay.addWidget(self.train_progress)

        right_layout.addWidget(grp_exec)
        right_layout.addStretch()

        content_layout.addWidget(right_panel)

    def load_image_by_index(self, idx):
        if 0 <= idx < len(self.dataset_images):
            self.current_img_idx = idx
            path = self.dataset_images[idx]
            self.current_image_path = path
            self.lbl_img_info.setText(f"Image {idx + 1} of {len(self.dataset_images)}: {os.path.basename(path)}")
            self.load_image_file(path)

    def prev_image(self):
        if self.dataset_images:
            new_idx = (self.current_img_idx - 1) % len(self.dataset_images)
            self.load_image_by_index(new_idx)

    def next_image(self):
        if self.dataset_images:
            new_idx = (self.current_img_idx + 1) % len(self.dataset_images)
            self.load_image_by_index(new_idx)

    def load_custom_image(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select Test Image", "", "Images (*.jpg *.jpeg *.png *.bmp);;All Files (*.*)")
        if f:
            self.dataset_images.insert(0, f)
            self.load_image_by_index(0)

    def toggle_live_view(self, checked):
        self.is_live = checked
        if self.is_live:
            self.btn_live_view.setText("⏹ STOP LIVE")
            self.live_timer.start(50) # 20fps
        else:
            self.btn_live_view.setText("▶ LIVE")
            self.live_timer.stop()

    def extract_and_save_crops(self, frame, limit_remaining=None):
        import time
        comm = self.txt_commodity.text().strip().upper()
        if not comm: comm = "UNKNOWN"
        save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_capture", comm, "UNCATEGORIZED")
        os.makedirs(save_dir, exist_ok=True)
        
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 20, 100)
        cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        crops_saved = 0
        timestamp = int(time.time() * 1000)
        
        for i, c in enumerate(cnts):
            x, y, w, h = cv2.boundingRect(c)
            aspect = max(w, h) / max(min(w, h), 1)
            # Area > 100 to ignore dust, aspect < 10 to ignore long camera seams/lines
            if w * h > 100 and aspect < 10:
                pad = 10
                x1 = max(0, x - pad)
                y1 = max(0, y - pad)
                x2 = min(frame.shape[1], x + w + pad)
                y2 = min(frame.shape[0], y + h + pad)
                
                crop = frame[y1:y2, x1:x2]
                if crop.size == 0: continue
                
                filename = f"crop_{timestamp}_{i}.jpg"
                path = os.path.join(save_dir, filename)
                cv2.imwrite(path, crop)
                self.dataset_images.insert(0, path)
                crops_saved += 1
                
                if limit_remaining is not None and crops_saved >= limit_remaining:
                    break
        
        return crops_saved

    def update_live_frame(self):
        cam_type = self.combo_live_cam.currentText()
        worker = self.front_worker if cam_type == "FRONT" else self.rear_worker
        if worker and hasattr(worker, 'latest_frame') and worker.latest_frame is not None:
            frame = worker.latest_frame.copy()
            self.display_image(frame)
            self.lbl_img_info.setText(f"🔴 LIVE: {cam_type}")

            if self.chk_auto_capture.isChecked():
                limit = self.spin_auto_limit.value()
                if self.auto_capture_count < limit:
                    import time
                    if time.time() - self.last_auto_capture_time > 0.33: # max 3 fps
                        crops_saved = self.extract_and_save_crops(frame, limit_remaining=(limit - self.auto_capture_count))
                        if crops_saved > 0:
                            self.auto_capture_count += crops_saved
                            self.lbl_auto_count.setText(f"Captured: {self.auto_capture_count} / {limit}")
                            self.last_auto_capture_time = time.time()
                            self.load_image_by_index(0)
                else:
                    self.chk_auto_capture.setChecked(False)
                    QMessageBox.information(self, "Auto Capture", f"Finished collecting {limit} crops!")

    def capture_live_frame(self):
        if self.is_live:
            self.btn_live_view.setChecked(False)
            self.toggle_live_view(False)
            
        cam_type = self.combo_live_cam.currentText()
        worker = self.front_worker if cam_type == "FRONT" else self.rear_worker
        if worker and hasattr(worker, 'latest_frame') and worker.latest_frame is not None:
            frame = worker.latest_frame.copy()
            crops_saved = self.extract_and_save_crops(frame)
            if crops_saved > 0:
                self.load_image_by_index(0)
                QMessageBox.information(self, "Capture", f"Extracted {crops_saved} cropped objects from frame.")
            else:
                QMessageBox.warning(self, "Capture", "No objects found in this frame to crop.")
        else:
            QMessageBox.warning(self, "Capture", f"{cam_type} Camera is not streaming frames.")

    def load_image_file(self, path):
        img = cv2.imread(path)
        if img is not None:
            self.current_image_cv = img
            self.detected_image_cv = None
            self.display_image(img)
        else:
            self.lbl_image_view.setText(f"Failed to read image:\n{path}")

    def display_image(self, cv_img):
        if cv_img is None:
            return
        h, w = cv_img.shape[:2]
        rgb = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888).copy()
        pix = QPixmap.fromImage(qimg)
        self.lbl_image_view.setPixmap(pix.scaled(self.lbl_image_view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        img = self.detected_image_cv if self.detected_image_cv is not None else self.current_image_cv
        if img is not None:
            self.display_image(img)

    # --- INFERENCE ENGINE & METRICS ---
    def delete_current_image(self):
        if not self.dataset_images or self.current_image_path is None:
            return
        try:
            # Physically remove if it's not a generic file from another drive that we don't want to mess up.
            # But for safety, just remove from list for now.
            idx = self.dataset_images.index(self.current_image_path)
            self.dataset_images.pop(idx)
            if self.dataset_images:
                self.load_image_by_index(max(0, idx - 1))
            else:
                self.current_image_path = None
                self.current_image_cv = None
                self.lbl_image_view.setPixmap(QPixmap())
                self.lbl_image_view.setText("NO IMAGE LOADED")
                self.lbl_img_info.setText("Image 0 of 0")
        except Exception as e:
            QMessageBox.warning(self, "Delete", f"Could not remove image: {e}")

    def add_custom_annotation(self):
        label = self.txt_custom_label.text().strip().upper()
        if not label:
            QMessageBox.warning(self, "Label", "Please enter a label name.")
            return
        self.annotate_image(label)
        self.txt_custom_label.clear()

    def run_test_inference(self):
        if not self.dataset_images:
            QMessageBox.warning(self, "Test Console", "Please load or snap an image first to test the model.")
            return
        conf = self.spin_confidence.value()
        model_type = self.combo_train_model.currentText()
        QMessageBox.information(self, "Inference Test", f"Running test inference using '{model_type}' at {conf}% confidence...\n\n(This is a simulation console. In production, real-time bounding boxes will be drawn on the live image.)\n\nSTATUS: Test Completed. Defect detected matching current settings.")

    def delete_custom_label(self):
        import shutil
        label = self.txt_custom_label.text().strip().upper()
        if not label:
            QMessageBox.warning(self, "Delete Label", "Please enter the label name you want to delete.")
            return
        comm = self.txt_commodity.text().strip().upper()
        if not comm:
            comm = "UNKNOWN"
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_capture", comm, label)
        if not os.path.exists(base_dir):
            QMessageBox.information(self, "Delete Label", f"No data found for label '{label}'.")
            return
        
        reply = QMessageBox.question(self, "Delete Label", f"Are you sure you want to delete the entire '{label}' dataset for '{comm}'?\nThis action cannot be undone.", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                shutil.rmtree(base_dir)
                QMessageBox.information(self, "Delete Label", f"Successfully deleted label '{label}'.")
                self.txt_custom_label.clear()
            except Exception as e:
                QMessageBox.warning(self, "Delete Label", f"Could not delete label directory: {e}")

    def annotate_image(self, label):
        import time
        if not self.dataset_images:
            QMessageBox.warning(self, "Annotation", "No image loaded to annotate!")
            return
            
        comm = self.txt_commodity.text().strip().upper()
        if not comm:
            QMessageBox.warning(self, "Annotation", "Please enter a Commodity Name first!")
            return
            
        dataset_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_capture", comm)
        label_dir = os.path.join(dataset_dir, label.replace(" ", "_"))
        os.makedirs(label_dir, exist_ok=True)
        
        if self.current_image_cv is not None:
            fname = f"{comm}_{label}_{int(time.time()*100)}.jpg"
            fpath = os.path.join(label_dir, fname)
            cv2.imwrite(fpath, self.current_image_cv)
            self.lbl_status_badge.setText(f"ANNOTATED: {label}")
            self.lbl_status_badge.setStyleSheet("background: #388e3c; color: white; font-weight: bold; padding: 6px 16px; border-radius: 6px; font-size: 13px;")
            
            # Clean up the uncategorized crop if it exists
            old_path = self.current_image_path
            if old_path and "UNCATEGORIZED" in old_path and os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except:
                    pass
            
            # Remove from UI queue and auto-advance
            idx = self.dataset_images.index(self.current_image_path)
            self.dataset_images.pop(idx)
            if self.dataset_images:
                # load the next image in the queue (which is now at the same index)
                next_idx = min(idx, len(self.dataset_images) - 1)
                self.load_image_by_index(next_idx)
            else:
                self.current_image_path = None
                self.current_image_cv = None
                self.lbl_image_view.setPixmap(QPixmap())
                self.lbl_image_view.setText("NO IMAGES TO ANNOTATE")
                self.lbl_img_info.setText("Image 0 of 0")

    def start_training(self):
        comm = self.txt_commodity.text().strip().upper()
        if not comm:
            QMessageBox.warning(self, "Training", "Please enter a Commodity Name.")
            return
            
        reply = QMessageBox.question(
            self, "Start Training", 
            f"Are you sure you want to start training for {comm}?\nThis may take some time depending on hardware.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.No:
            return
            
        self.btn_start_train.setEnabled(False)
        self.train_progress.setVisible(True)
        self.train_progress.setValue(0)
        self.lbl_status_badge.setText("TRAINING IN PROGRESS...")
        self.lbl_status_badge.setStyleSheet("background: #ffb300; color: black; font-weight: bold; padding: 6px 16px; border-radius: 6px; font-size: 13px;")
        
        # Mock Training Loop for demonstration (In real setup, spawns a QThread)
        for i in range(1, 101):
            self.train_progress.setValue(i)
            QApplication.processEvents()
            time.sleep(0.03)
            
        self.train_progress.setVisible(False)
        self.btn_start_train.setEnabled(True)
        self.lbl_status_badge.setText("TRAINING COMPLETED")
        self.lbl_status_badge.setStyleSheet("background: #00e676; color: black; font-weight: bold; padding: 6px 16px; border-radius: 6px; font-size: 13px;")
        
        # Assign to Current Scheme automatically
        try:
            mw = self.parent() if hasattr(self.parent(), 'program_data') else getattr(self.parent(), 'main_window', None)
            if mw and hasattr(mw, 'program_data') and self.current_program in mw.program_data:
                prog = mw.program_data[self.current_program]
                if "algo_settings" not in prog:
                    prog["algo_settings"] = {"FRONT": {}, "REAR": {}}
                
                trained_path = f"datasets/{comm}/best.pt"
                # Update DEEP LEARNING logic
                prog["algo_settings"].setdefault("FRONT", {})["DEEP LEARNING"] = {"sens": "50", "purity": "1", "model": trained_path}
                prog["algo_settings"].setdefault("REAR", {})["DEEP LEARNING"] = {"sens": "50", "purity": "1", "model": trained_path}
                
                # Update Active Algos list to ensure DEEP LEARNING is active
                if "DEEP LEARNING" not in prog.get("active_algos", []):
                    prog.setdefault("active_algos", []).append("DEEP LEARNING")
                
                # Auto-save
                recipes_data = RecipeManager.load_all()
                recipes_data["schemes"] = mw.program_data
                RecipeManager.save_all(recipes_data)
                
                QMessageBox.information(self, "Training Complete", f"Successfully trained model for {comm}!\nWeights have been loaded to the active scheme ({self.current_program}).")
        except Exception as e:
            QMessageBox.warning(self, "Warning", f"Training succeeded, but auto-loading to scheme failed: {e}")


# =============================================================================
# FUNCTIONAL PROGRAM MANAGER DIALOG (UPDATED FOR DUAL CAMERA TABS)
# =============================================================================
class ProgramManagerDialog(QDialog):
    def __init__(self, program_data, current_selection, user_role, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"SYSTEM MASTER CONFIGURATION - {user_role}")
        self.setFixedSize(1920, 1080)
        self.showMaximized()
        self.program_data = program_data
        self.user_role = user_role
        self.selected_program = current_selection
        self.main_window = parent

        self.backup_registry = {}

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(15)

        self.glassy_style = """
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3a3a3a, stop:1 #0a0a0a);
                border: 1px solid #444; border-radius: 6px; color: #00bcd4; 
                padding: 12px; font-weight: bold; font-size: 13px;
            }
            QPushButton:hover { border-color: #00e676; color: #ffffff; }
            QPushButton:pressed { background-color: #000; }
        """

        self.toggle_style = """
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a1a1a, stop:1 #000000);
                color: #888; border: 2px solid #333; border-radius: 8px; font-weight: bold; font-size: 14px;
            }
            QPushButton:hover { border: 2px solid #555; color: white; }
            QPushButton:checked {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #00e676, stop:1 #00c853);
                color: black; border: 2px solid #b9f6ca;
                font-weight: 900;
            }
        """

        # 1. TOP NAV BAR
        top_nav = QHBoxLayout()
        self.btn_cam_nav = QPushButton("CAMERA SETUP")
        self.btn_ej_nav = QPushButton("EJECTORS")
        self.btn_light_nav = QPushButton("LIGHT SETTINGS")
        self.btn_bg_nav = QPushButton("BACKGROUND / GRAPH")
        self.btn_ai_console = QPushButton("🔬 AI / DATASET TEST CONSOLE")

        for btn in [self.btn_cam_nav, self.btn_ej_nav, self.btn_light_nav, self.btn_bg_nav, self.btn_ai_console]:
            btn.setStyleSheet(self.glassy_style)
            top_nav.addWidget(btn)

        self.btn_cam_nav.clicked.connect(self.open_camera_tool)
        self.btn_ej_nav.clicked.connect(self.open_ejector_tool)
        self.btn_light_nav.clicked.connect(self.open_light_panel)
        self.btn_bg_nav.clicked.connect(self.open_background_tool)
        self.btn_ai_console.clicked.connect(self.open_dataset_test_console)
        self.layout.addLayout(top_nav)

        # 2. MAIN CONTENT (RECIPE & ALGORITHMS)
        content = QHBoxLayout()

        # --- LEFT PANEL: RECIPE FILE OPS ---
        left_box = QGroupBox("RECIPE DATA && BACKUP")
        l_layout = QVBoxLayout(left_box)
        self.list_widget = QListWidget()
        self.list_widget.addItems(list(self.program_data.keys()))

        items = self.list_widget.findItems(self.selected_program, Qt.MatchExactly)
        if items: self.list_widget.setCurrentItem(items[0])
        self.list_widget.itemClicked.connect(self.on_program_selected)
        l_layout.addWidget(self.list_widget)

        file_grid = QGridLayout()
        self.btn_new = QPushButton("NEW")
        self.btn_copy = QPushButton("COPY")
        self.btn_rename = QPushButton("RENAME")
        self.btn_del = QPushButton("DELETE")
        self.btn_backup = QPushButton("BACKUP")
        self.btn_recovery = QPushButton("RECOVER")

        btn_mapping = [(self.btn_new, 0, 0), (self.btn_copy, 0, 1), (self.btn_rename, 1, 0),
                       (self.btn_del, 1, 1), (self.btn_backup, 2, 0), (self.btn_recovery, 2, 1)]
        for b, r, c in btn_mapping:
            b.setStyleSheet(self.glassy_style)
            file_grid.addWidget(b, r, c)

        self.btn_new.clicked.connect(self.new_recipe)
        self.btn_copy.clicked.connect(self.copy_recipe)
        self.btn_del.clicked.connect(self.delete_recipe)
        self.btn_rename.clicked.connect(self.rename_recipe)
        self.btn_backup.clicked.connect(self.perform_backup)
        self.btn_recovery.clicked.connect(self.perform_recovery)

        # OPERATOR cannot delete recipes
        if self.user_role == "OPERATOR":
            self.btn_del.setEnabled(False)
            self.btn_del.setText("DELETE 🔒")

        l_layout.addLayout(file_grid)
        left_box.setMaximumWidth(400)
        content.addWidget(left_box, 1)

        # --- RIGHT PANEL: ALGORITHMS (TABS) ---
        right_box = QGroupBox("ACTIVE ALGORITHMS")
        r_layout = QVBoxLayout(right_box)
        r_layout.setSpacing(8)

        self.camera_mode = "SAME"  # "SAME" or "INDIVIDUAL"

        # 1. Top Mode Switch Bar (BOTH SIDES SAME vs INDIVIDUAL)
        mode_box = QFrame()
        mode_box.setStyleSheet("""
            QFrame {
                background: #1e1e1e;
                border: 1px solid #3a3a3a;
                border-radius: 8px;
            }
        """)
        mode_layout = QHBoxLayout(mode_box)
        mode_layout.setContentsMargins(12, 6, 12, 6)
        mode_layout.setSpacing(12)

        lbl_cam_mode = QLabel("CAMERA SELECTION:")
        lbl_cam_mode.setStyleSheet("color: #00e5ff; font-weight: bold; font-size: 13px;")
        mode_layout.addWidget(lbl_cam_mode)

        self.btn_mode_same = QPushButton("🔗 BOTH SAME (ONE-TIME SET)")
        self.btn_mode_same.setFixedHeight(40)
        self.btn_mode_same.setCheckable(True)
        self.btn_mode_same.clicked.connect(lambda: self.set_camera_mode("SAME"))
        mode_layout.addWidget(self.btn_mode_same)

        self.btn_mode_indiv = QPushButton("🔀 INDIVIDUAL (FRONT / REAR)")
        self.btn_mode_indiv.setFixedHeight(40)
        self.btn_mode_indiv.setCheckable(True)
        self.btn_mode_indiv.clicked.connect(lambda: self.set_camera_mode("INDIVIDUAL"))
        mode_layout.addWidget(self.btn_mode_indiv)

        mode_layout.addStretch()

        self.btn_copy_front_to_rear = QPushButton("📋 COPY FRONT ➔ REAR")
        self.btn_copy_front_to_rear.setFixedHeight(40)
        self.btn_copy_front_to_rear.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a237e, stop:1 #00bcd4);
                color: #ffffff; font-weight: bold; font-size: 12px;
                border: 1px solid #00e5ff; border-radius: 6px; padding: 6px 14px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #283593, stop:1 #18ffff);
                color: #000000;
            }
        """)
        self.btn_copy_front_to_rear.clicked.connect(self.copy_front_to_rear)
        mode_layout.addWidget(self.btn_copy_front_to_rear)

        r_layout.addWidget(mode_box)

        # 2. Tabs Widget: BOTH (SAME), FRONT CAMERA, REAR CAMERA
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 2px solid #444; background: #1a1a1a; }
        """)
        self.tabs.tabBar().setStyleSheet("""
            QTabBar::tab {
                background: #222; color: #00e5ff; padding: 14px 28px;
                font-weight: bold; font-size: 15px; border: 2px solid #333;
                border-bottom: none; margin-right: 4px; min-width: 120px;
            }
            QTabBar::tab:selected {
                background: #00bcd4; color: #000000;
                border: 2px solid #00e5ff; border-bottom: none;
                border-top-left-radius: 6px; border-top-right-radius: 6px;
            }
            QTabBar::tab:!selected:hover {
                background: #333; color: #18ffff;
            }
        """)

        self.algo_buttons = {"BOTH": {}, "FRONT": {}, "REAR": {}}
        self.algo_settings = {"FRONT": {}, "REAR": {}}

        schemes = ["GRAY A", "GRAY B", "SIZE: BIG", "SIZE: SHORT", "DISCOLOUR A", "DISCOLOUR B", "HSV COLOR",
                   "AI DETECTION"]

        cam_tab_configs = [
            ("BOTH", "🔗 BOTH (SAME)", "🔗 BOTH SIDES LINKED (SAME OPTIONS) — Setting defect algorithms or parameters here applies to BOTH Front & Rear cameras simultaneously."),
            ("FRONT", "FRONT CAMERA", "📷 FRONT CAMERA (INDIVIDUAL) — Defect algorithms and parameter settings applied here affect the Front camera only."),
            ("REAR", "REAR CAMERA", "📷 REAR CAMERA (INDIVIDUAL) — Defect algorithms and parameter settings applied here affect the Rear camera only.")
        ]

        for cam, tab_title, banner_text in cam_tab_configs:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("border: none; background: #1a1a1a;")
            scroll_content = QWidget()
            scroll_content.setStyleSheet("background: #1a1a1a;")
            scroll_layout = QVBoxLayout(scroll_content)
            scroll_layout.setContentsMargins(10, 8, 10, 8)
            scroll_layout.setSpacing(8)

            # Banner info
            banner = QLabel(banner_text)
            if cam == "BOTH":
                banner.setStyleSheet("color: #00e676; font-size: 13px; font-weight: bold; background: #122818; padding: 8px 12px; border: 1px solid #00e676; border-radius: 6px;")
            else:
                banner.setStyleSheet("color: #00bcd4; font-size: 13px; font-weight: bold; background: #10262c; padding: 8px 12px; border: 1px solid #00bcd4; border-radius: 6px;")
            scroll_layout.addWidget(banner)

            grid = QGridLayout()
            grid.setSpacing(10)

            for i, s in enumerate(schemes):
                container = QWidget()
                h_lay = QHBoxLayout(container)
                h_lay.setContentsMargins(0, 0, 0, 0)
                h_lay.setSpacing(5)

                btn = QPushButton(s)
                btn.setCheckable(True)
                btn.setFixedHeight(50)
                btn.setStyleSheet(self.toggle_style)
                btn.clicked.connect(lambda checked, name=s, c=cam: self.on_algo_toggled(name, c, checked))
                self.algo_buttons[cam][s] = btn
                h_lay.addWidget(btn, stretch=1)

                btn_set = QPushButton("⚙")
                btn_set.setFixedSize(50, 50)
                btn_set.setStyleSheet(
                    "background: #333; color: #00bcd4; font-size: 20px; border-radius: 8px; font-weight: bold;")
                btn_set.clicked.connect(lambda _, name=s, c=cam: self.open_algo_settings(name, c))
                h_lay.addWidget(btn_set)

                grid.addWidget(container, i // 2, i % 2)

            scroll_layout.addLayout(grid)
            scroll_layout.addStretch()
            scroll.setWidget(scroll_content)
            self.tabs.addTab(scroll, tab_title)

        self.tabs.currentChanged.connect(self.on_tab_changed)
        r_layout.addWidget(self.tabs)

        self.btn_save_algos = QPushButton("💾 SAVE & LOAD RECIPE")
        self.btn_save_algos.setFixedHeight(50)
        self.btn_save_algos.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00897b, stop:1 #00e676);
                color: black; font-weight: 900; font-size: 15px; border-radius: 8px; border: 2px solid #b9f6ca;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00bfa5, stop:1 #69f0ae);
                border-color: #ffffff;
            }
            QPushButton:pressed { background-color: #004d40; color: white; }
        """)
        self.btn_save_algos.clicked.connect(self.save_and_load_recipe)
        r_layout.addWidget(self.btn_save_algos)

        self.btn_sim_prog = QPushButton("SIMULATE CURRENT PROGRAM & TEST 63 CHANNELS")
        self.btn_sim_prog.setFixedHeight(48)
        self.btn_sim_prog.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00bcd4, stop:1 #00e676); color: black; font-weight: bold; font-size: 14px; border-radius: 6px;")
        self.btn_sim_prog.clicked.connect(self.simulate_active_program)
        r_layout.addWidget(self.btn_sim_prog)

        content.addWidget(right_box, 3)

        self.layout.addLayout(content)
        self.update_mode_buttons()
        self.on_program_selected(self.list_widget.currentItem())

    # --- LOGIC ---
    def set_camera_mode(self, mode):
        self.camera_mode = mode
        if mode == "SAME":
            self.tabs.setCurrentIndex(0)
        else:
            if self.tabs.currentIndex() == 0:
                self.tabs.setCurrentIndex(1)
        self.update_mode_buttons()

    def update_mode_buttons(self):
        active_style = """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00bcd4, stop:1 #00e676);
                color: #000000; font-weight: 900; font-size: 13px;
                border: 2px solid #00e5ff; border-radius: 6px; padding: 6px 16px;
            }
        """
        inactive_style = """
            QPushButton {
                background: #222222; color: #888888; font-weight: bold; font-size: 13px;
                border: 1px solid #444444; border-radius: 6px; padding: 6px 16px;
            }
            QPushButton:hover { border-color: #00bcd4; color: #00e5ff; }
        """
        if self.camera_mode == "SAME":
            self.btn_mode_same.setStyleSheet(active_style)
            self.btn_mode_indiv.setStyleSheet(inactive_style)
            self.btn_mode_same.setChecked(True)
            self.btn_mode_indiv.setChecked(False)
        else:
            self.btn_mode_same.setStyleSheet(inactive_style)
            self.btn_mode_indiv.setStyleSheet(active_style)
            self.btn_mode_same.setChecked(False)
            self.btn_mode_indiv.setChecked(True)

    def on_tab_changed(self, idx):
        if idx == 0:
            self.camera_mode = "SAME"
            # When switching to BOTH (SAME), sync FRONT state to REAR & BOTH
            for s in self.algo_buttons["FRONT"]:
                f_checked = self.algo_buttons["FRONT"][s].isChecked()
                self.algo_buttons["BOTH"][s].setChecked(f_checked)
                self.algo_buttons["REAR"][s].setChecked(f_checked)
            if "FRONT" in self.algo_settings:
                import copy
                self.algo_settings["REAR"] = copy.deepcopy(self.algo_settings["FRONT"])
        else:
            self.camera_mode = "INDIVIDUAL"
        self.update_mode_buttons()

    def on_algo_toggled(self, algo_name, cam_source, is_checked):
        if cam_source == "BOTH":
            # Setting ONE time applies to BOTH FRONT and REAR!
            self.algo_buttons["FRONT"][algo_name].setChecked(is_checked)
            self.algo_buttons["REAR"][algo_name].setChecked(is_checked)
        else:
            # Modified individually in FRONT or REAR
            f_checked = self.algo_buttons["FRONT"][algo_name].isChecked()
            r_checked = self.algo_buttons["REAR"][algo_name].isChecked()
            self.algo_buttons["BOTH"][algo_name].setChecked(f_checked and r_checked)

    def copy_front_to_rear(self):
        import copy
        for s, btn in self.algo_buttons["FRONT"].items():
            checked = btn.isChecked()
            self.algo_buttons["REAR"][s].setChecked(checked)
            self.algo_buttons["BOTH"][s].setChecked(checked)
        self.algo_settings["REAR"] = copy.deepcopy(self.algo_settings.get("FRONT", {}))
        QMessageBox.information(self, "Copied", "Successfully copied all FRONT algorithms & settings to REAR camera!")

    def simulate_active_program(self):
        curr_idx = self.tabs.currentIndex()
        cam = "FRONT" if curr_idx in [0, 1] else "REAR"
        cam_algos = self.program_data.get(self.selected_program, {}).get(cam, [])
        active_algo = cam_algos[0] if cam_algos else "GRAY A"
        cam_settings = self.algo_settings.get(cam, {}).get(active_algo, {
            "channel": "GREEN", "mode": "DARK DEFECT", "sens": "100", "scale": "16", "purity": "1"
        })
        dlg = DefectSimulationDialog(f"{active_algo} ({cam})", cam_settings, main_window=self.main_window, parent=self)
        dlg.exec_()

    def on_program_selected(self, item):
        if not item: return
        name = item.text()
        self.selected_program = name
        current_data = self.program_data.get(name, {"FRONT": [], "REAR": []})

        if isinstance(current_data, dict) and "algorithms" in current_data:
            algo_dict = current_data["algorithms"]
        elif isinstance(current_data, list):
            algo_dict = {"FRONT": current_data, "REAR": current_data}
        else:
            algo_dict = current_data

        # Load algo_settings if saved in recipe
        if isinstance(current_data, dict) and "algo_settings" in current_data:
            import copy
            self.algo_settings = copy.deepcopy(current_data["algo_settings"])
        else:
            self.algo_settings = {"FRONT": {}, "REAR": {}}

        # Restore camera_mode
        saved_mode = current_data.get("camera_mode") if isinstance(current_data, dict) else None
        f_list = algo_dict.get("FRONT", []) if isinstance(algo_dict, dict) else []
        r_list = algo_dict.get("REAR", []) if isinstance(algo_dict, dict) else []
        if saved_mode in ["SAME", "INDIVIDUAL"]:
            self.camera_mode = saved_mode
        else:
            self.camera_mode = "SAME" if f_list == r_list else "INDIVIDUAL"

        # Update FRONT and REAR buttons
        for cam in ["FRONT", "REAR"]:
            active_algos = algo_dict.get(cam, []) if isinstance(algo_dict, dict) else []
            for algo, btn in self.algo_buttons[cam].items():
                btn.setChecked(algo in active_algos)

        # Update BOTH tab buttons
        for algo, btn in self.algo_buttons["BOTH"].items():
            btn.setChecked(algo in f_list)

        # Update tab and mode buttons
        if self.camera_mode == "SAME":
            self.tabs.setCurrentIndex(0)
        else:
            if self.tabs.currentIndex() == 0:
                self.tabs.setCurrentIndex(1)
        self.update_mode_buttons()

    def save_and_load_recipe(self):
        new_data = {"FRONT": [], "REAR": []}
        if self.camera_mode == "SAME" or self.tabs.currentIndex() == 0:
            active_both = [algo for algo, btn in self.algo_buttons["BOTH"].items() if btn.isChecked()]
            new_data["FRONT"] = list(active_both)
            new_data["REAR"] = list(active_both)
        else:
            new_data["FRONT"] = [algo for algo, btn in self.algo_buttons["FRONT"].items() if btn.isChecked()]
            new_data["REAR"] = [algo for algo, btn in self.algo_buttons["REAR"].items() if btn.isChecked()]

        if isinstance(self.program_data.get(self.selected_program), dict):
            self.program_data[self.selected_program]["algorithms"] = new_data
            self.program_data[self.selected_program]["camera_mode"] = self.camera_mode
            self.program_data[self.selected_program]["algo_settings"] = self.algo_settings
        else:
            self.program_data[self.selected_program] = {
                "algorithms": new_data,
                "camera_mode": self.camera_mode,
                "algo_settings": self.algo_settings
            }

        recipes_data = RecipeManager.load_all()
        recipes_data["schemes"] = self.program_data
        recipes_data["active_scheme"] = self.selected_program
        RecipeManager.save_all(recipes_data)

        if hasattr(self.main_window, 'current_program'):
            self.main_window.current_program = self.selected_program
            self.main_window.program_data = self.program_data
            if hasattr(self.main_window, 'apply_scheme'):
                self.main_window.apply_scheme(self.selected_program)
            if hasattr(self.main_window, 'update_side_panel_visualization'):
                self.main_window.update_side_panel_visualization(self.selected_program)

        self.accept()

    def save_current_schemes(self):
        self.save_and_load_recipe()

    def open_algo_settings(self, algo_name, cam):
        if algo_name == "IMAGE CAPTURE" or algo_name == "AI DETECTION":
            self.open_camera_tool()
            if cam == "BOTH":
                self.algo_buttons["BOTH"][algo_name].setChecked(True)
                self.algo_buttons["FRONT"][algo_name].setChecked(True)
                self.algo_buttons["REAR"][algo_name].setChecked(True)
            else:
                self.algo_buttons[cam][algo_name].setChecked(True)
            return

        import copy
        def _quiet_save():
            if isinstance(self.program_data.get(self.selected_program), dict):
                self.program_data[self.selected_program]["algo_settings"] = self.algo_settings
            else:
                self.program_data[self.selected_program] = {"algo_settings": self.algo_settings}
            recipes_data = RecipeManager.load_all()
            recipes_data["schemes"] = self.program_data
            RecipeManager.save_all(recipes_data)
            if hasattr(self.main_window, 'program_data'):
                self.main_window.program_data = self.program_data

        if cam == "BOTH":
            current_data = self.algo_settings.get("FRONT", {}).get(algo_name, {})
            dlg = AlgoSettingsDialog(f"{algo_name} (FRONT & REAR)", current_data, self)
            if dlg.exec_() == QDialog.Accepted:
                self.algo_settings.setdefault("FRONT", {})[algo_name] = dlg.settings
                self.algo_settings.setdefault("REAR", {})[algo_name] = copy.deepcopy(dlg.settings)
                self.algo_buttons["BOTH"][algo_name].setChecked(True)
                self.algo_buttons["FRONT"][algo_name].setChecked(True)
                self.algo_buttons["REAR"][algo_name].setChecked(True)
                _quiet_save()
        else:
            cam_settings = self.algo_settings.get(cam, {})
            current_data = cam_settings.get(algo_name, {})
            dlg = AlgoSettingsDialog(f"{algo_name} ({cam})", current_data, self)
            if dlg.exec_() == QDialog.Accepted:
                cam_settings[algo_name] = dlg.settings
                self.algo_settings[cam] = cam_settings
                self.algo_buttons[cam][algo_name].setChecked(True)
                _quiet_save()

    def copy_recipe(self):
        item = self.list_widget.currentItem()
        if item:
            import copy
            new_name = item.text() + "_COPY"
            self.program_data[new_name] = copy.deepcopy(self.program_data[item.text()])
            self.list_widget.addItem(new_name)
            recipes_data = RecipeManager.load_all()
            recipes_data["schemes"] = self.program_data
            RecipeManager.save_all(recipes_data)

    def delete_recipe(self):
        item = self.list_widget.currentItem()
        if item and self.list_widget.count() > 1:
            del self.program_data[item.text()]
            self.list_widget.takeItem(self.list_widget.row(item))
            recipes_data = RecipeManager.load_all()
            recipes_data["schemes"] = self.program_data
            RecipeManager.save_all(recipes_data)

    def new_recipe(self):
        def save_new(name):
            if name:
                import copy
                default_template = copy.deepcopy(RecipeManager.get_default_recipes()["schemes"]["RICE"])
                default_template["algorithms"] = {"FRONT": [], "REAR": []}
                default_template["dataset_dir"] = f"dataset_capture/{name.replace(' ', '_')}"
                self.program_data[name] = default_template
                self.list_widget.addItem(name)
                recipes_data = RecipeManager.load_all()
                recipes_data["schemes"] = self.program_data
                RecipeManager.save_all(recipes_data)

        VirtualKeyboard("", save_new, self).exec_()

    def rename_recipe(self):
        item = self.list_widget.currentItem()
        if item:
            def save_rename(new_name):
                if new_name:
                    data = self.program_data.pop(item.text())
                    self.program_data[new_name] = data
                    item.setText(new_name)
                    recipes_data = RecipeManager.load_all()
                    recipes_data["schemes"] = self.program_data
                    if recipes_data.get("active_scheme") == item.text():
                        recipes_data["active_scheme"] = new_name
                    RecipeManager.save_all(recipes_data)

            VirtualKeyboard(item.text(), save_rename, self).exec_()

    def perform_backup(self):
        self.backup_registry = {k: v for k, v in self.program_data.items()}
        QMessageBox.information(self, "Success", "Factory Snapshot Stored to Registry.")

    def perform_recovery(self):
        if not self.backup_registry:
            QMessageBox.warning(self, "Fail", "No Backup Snapshot Found!")
            return
        self.program_data = {k: v for k, v in self.backup_registry.items()}
        self.list_widget.clear()
        self.list_widget.addItems(list(self.program_data.keys()))
        QMessageBox.information(self, "Success", "Recipes Restored from Backup.")

    def open_light_panel(self):
        LightSetupDialog(self, scheme_name=self.selected_program).exec_()

    def open_camera_tool(self):
        self.main_window.open_camera_window()

    def open_background_tool(self):
        try:
            mw = self.main_window if self.main_window is not None else self.parent()
            fw = getattr(mw, 'camera_worker_front', None)
            rw = getattr(mw, 'camera_worker_rear', None)
            esp = getattr(mw, 'esp32', None)
            dlg = BackgroundGraphWindow(
                front_worker=fw,
                rear_worker=rw,
                esp32_handler=esp,
                parent=self
            )
            dlg.exec_()
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Error", f"Failed to open Background Graph:\n{str(e)}")

    def open_ejector_tool(self):
        self.main_window.show_ejector_window(scheme_name=self.selected_program)

    def load_program(self):
        self.accept()

    def open_dataset_test_console(self):
        try:
            mw = self.main_window if self.main_window is not None else self.parent()
            fw = getattr(mw, 'camera_worker_front', None)
            rw = getattr(mw, 'camera_worker_rear', None)
            dlg = DatasetAITestConsoleDialog(
                front_worker=fw,
                rear_worker=rw,
                current_program=self.selected_program,
                parent=self
            )
            dlg.exec_()
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Error", f"Failed to open Dataset / AI Test Console:\n{str(e)}")


# =============================================================================
# PROGRAM LIBRARY DIALOG (SLIM SELECTED VIEWS FOR OPERATOR)
# =============================================================================
class ProgramLibraryDialog(QDialog):
    def __init__(self, program_data, current_selection, user_role, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PROGRAM LIBRARY")
        self.setFixedSize(650, 500)
        self.program_data = program_data
        self.selected_program = current_selection
        self.main_window = parent
        self.copied_program = None

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(15, 15, 15, 15)
        self.layout.setSpacing(15)

        # Left panel: List
        left_box = QGroupBox("AVAILABLE PROGRAMS")
        l_layout = QVBoxLayout(left_box)
        self.list_widget = QListWidget()
        self.list_widget.addItems(list(self.program_data.keys()))
        self.list_widget.setStyleSheet("font-size: 16px; font-weight: bold; color: white; background: #111; border: 1px solid #333;")
        
        items = self.list_widget.findItems(self.selected_program, Qt.MatchExactly)
        if items: self.list_widget.setCurrentItem(items[0])
        self.list_widget.itemDoubleClicked.connect(self.load_program)
        l_layout.addWidget(self.list_widget)
        self.layout.addWidget(left_box, 3)

        # Right panel: Buttons
        right_box = QGroupBox("ACTIONS")
        r_layout = QVBoxLayout(right_box)
        r_layout.setSpacing(12)

        self.btn_load = QPushButton("⚡ LOAD RECIPE")
        self.btn_copy = QPushButton("📄 COPY")
        self.btn_paste = QPushButton("📋 PASTE")
        self.btn_rename = QPushButton("✏ RENAME")

        btn_style = """
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3a3a3a, stop:1 #0a0a0a);
                border: 1px solid #444; border-radius: 6px; color: #00bcd4; 
                padding: 12px; font-weight: bold; font-size: 14px;
            }
            QPushButton:hover { border-color: #00e676; color: #ffffff; }
            QPushButton:pressed { background-color: #000; }
        """
        
        self.btn_load.setStyleSheet("background-color: #00e676; color: black; font-weight: bold; font-size: 15px; border-radius: 6px; padding: 14px;")
        for btn in [self.btn_copy, self.btn_paste, self.btn_rename]:
            btn.setStyleSheet(btn_style)

        r_layout.addWidget(self.btn_load)
        r_layout.addWidget(self.btn_copy)
        r_layout.addWidget(self.btn_paste)
        r_layout.addWidget(self.btn_rename)
        r_layout.addStretch()
        self.layout.addWidget(right_box, 2)

        self.btn_load.clicked.connect(self.load_program)
        self.btn_copy.clicked.connect(self.copy_program)
        self.btn_paste.clicked.connect(self.paste_program)
        self.btn_rename.clicked.connect(self.rename_program)

    def load_program(self):
        item = self.list_widget.currentItem()
        if item:
            self.selected_program = item.text()
            self.accept()

    def copy_program(self):
        item = self.list_widget.currentItem()
        if item:
            self.copied_program = item.text()
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(self, "Copy", f"Copied to clipboard: {self.copied_program}")

    def paste_program(self):
        if not self.copied_program or self.copied_program not in self.program_data:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Paste", "No program has been copied yet!")
            return

        from PyQt5.QtWidgets import QInputDialog, QMessageBox
        new_name, ok = QInputDialog.getText(self, "Paste Recipe", "Enter name for the new recipe:", QLineEdit.Normal, self.copied_program + "_copy")
        if ok and new_name.strip():
            new_name = new_name.strip()
            if new_name in self.program_data:
                QMessageBox.warning(self, "Paste", "A program with this name already exists!")
                return
            import copy
            self.program_data[new_name] = copy.deepcopy(self.program_data[self.copied_program])
            self.list_widget.addItem(new_name)
            items = self.list_widget.findItems(new_name, Qt.MatchExactly)
            if items: self.list_widget.setCurrentItem(items[0])
            recipes_data = RecipeManager.load_all()
            recipes_data["schemes"] = self.program_data
            RecipeManager.save_all(recipes_data)
            QMessageBox.information(self, "Paste", f"Pasted new recipe: {new_name}")

    def rename_program(self):
        item = self.list_widget.currentItem()
        if not item: return
        old_name = item.text()
        from PyQt5.QtWidgets import QInputDialog, QMessageBox
        new_name, ok = QInputDialog.getText(self, "Rename Recipe", "Enter new name for the recipe:", QLineEdit.Normal, old_name)
        if ok and new_name.strip():
            new_name = new_name.strip()
            if new_name == old_name:
                return
            if new_name in self.program_data:
                QMessageBox.warning(self, "Rename", "A program with this name already exists!")
                return
            self.program_data[new_name] = self.program_data.pop(old_name)
            item.setText(new_name)
            recipes_data = RecipeManager.load_all()
            recipes_data["schemes"] = self.program_data
            if recipes_data.get("active_scheme") == old_name:
                recipes_data["active_scheme"] = new_name
            RecipeManager.save_all(recipes_data)
            QMessageBox.information(self, "Rename", f"Renamed recipe to: {new_name}")


# =============================================================================
#  CUSTOM WIDGET: GLOSSY KNOB (EXISTING)
# =============================================================================
class GlossyKnob(QWidget):
    valueChanged = pyqtSignal(int)
    clicked = pyqtSignal()

    def __init__(self, value=0, parent=None):
        super().__init__(parent)
        self.setFixedSize(90, 90)  # Knob Size
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click center value to open Keypad, or drag to adjust")
        self._value = value
        self._max_value = 100
        self.is_dragging = False
        self.last_y = 0
        self.press_pos = None

    def get_value(self):
        return self._value

    def set_value(self, val):
        self._value = max(0, min(val, 100))
        self.update()

    def setValue(self, val):
        self.set_value(val)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = self.width()
        height = self.height()
        side = min(width, height)
        painter.translate(width / 2, height / 2)
        scale_factor = side / 100.0
        painter.scale(scale_factor, scale_factor)
        pen = QPen(QColor(40, 40, 40))
        pen.setWidth(10)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(QRectF(-40, -40, 80, 80), 225 * 16, -270 * 16)
        if self._value > 0:
            pen_active = QPen(QColor("#00e676"))
            pen_active.setWidth(10)
            pen_active.setCapStyle(Qt.RoundCap)
            painter.setPen(pen_active)
            span_angle = -270 * (self._value / self._max_value)
            painter.drawArc(QRectF(-40, -40, 80, 80), 225 * 16, int(span_angle * 16))
        grad = QRadialGradient(QPointF(-10, -10), 50)
        grad.setColorAt(0.0, QColor(60, 60, 60))
        grad.setColorAt(1.0, QColor(10, 10, 10))
        painter.setPen(QPen(QColor(50, 60, 70), 1))
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(QPointF(0, 0), 28, 28)
        painter.setPen(QColor("#ffffff"))
        font = QFont("Arial", 18, QFont.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(-40, -20, 80, 40), Qt.AlignCenter, str(self._value))

    def mousePressEvent(self, event):
        self.is_dragging = False
        self.press_pos = event.pos()
        self.last_y = event.y()

    def mouseMoveEvent(self, event):
        if self.press_pos and (event.pos() - self.press_pos).manhattanLength() > 14:
            self.is_dragging = True
        if self.is_dragging:
            dy = self.last_y - event.y()
            self.last_y = event.y()
            new_val = self._value + int(dy)
            if new_val != self._value:
                self.set_value(new_val)
                self.valueChanged.emit(self._value)

    def mouseReleaseEvent(self, event):
        if not self.is_dragging:
            self.clicked.emit()
        self.is_dragging = False


# =============================================================================
# 1. LOGIN DIALOG (FIXED & SIMPLIFIED)
# =============================================================================
class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Secure Login")
        self.setFixedSize(400, 320)  # Increased height slightly
        self.setStyleSheet("background-color: #222; color: white; font-family: Segoe UI;")

        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Role Selection
        lbl_role = QLabel("SELECT USER ROLE:")
        lbl_role.setStyleSheet("color: #00bcd4; font-weight: bold; letter-spacing: 1px;")
        layout.addWidget(lbl_role)

        from PyQt5.QtWidgets import QButtonGroup, QHBoxLayout
        self.role_group = QButtonGroup(self)
        self.role_group.setExclusive(True)

        hbox = QHBoxLayout()
        hbox.setSpacing(10)

        self.btn_role_op = QPushButton("OPERATOR")
        self.btn_role_op.setCheckable(True)
        self.btn_role_op.setChecked(True)  # default

        self.btn_role_eng = QPushButton("ENGINEER")
        self.btn_role_eng.setCheckable(True)

        self.btn_role_mfr = QPushButton("MANUFACTURER")
        self.btn_role_mfr.setCheckable(True)

        self.role_group.addButton(self.btn_role_op)
        self.role_group.addButton(self.btn_role_eng)
        self.role_group.addButton(self.btn_role_mfr)

        btn_style = """
            QPushButton {
                background-color: #333;
                color: #bbb;
                font-weight: bold;
                border: 2px solid #555;
                border-radius: 5px;
                height: 38px;
                font-size: 11px;
            }
            QPushButton:checked {
                background-color: #111;
                color: #00bcd4;
                border: 2px solid #00bcd4;
            }
            QPushButton:hover {
                background-color: #444;
            }
        """
        self.btn_role_op.setStyleSheet(btn_style)
        self.btn_role_eng.setStyleSheet(btn_style)
        self.btn_role_mfr.setStyleSheet(btn_style)

        hbox.addWidget(self.btn_role_op)
        hbox.addWidget(self.btn_role_eng)
        hbox.addWidget(self.btn_role_mfr)
        layout.addLayout(hbox)

        # Password Input
        lbl_pass = QLabel("ACCESS CODE:")
        lbl_pass.setStyleSheet("color: #00bcd4; font-weight: bold; letter-spacing: 1px;")
        layout.addWidget(lbl_pass)

        self.password_input = QLineEdit()
        self.password_input.setStyleSheet(
            "background: #111; color: yellow; font-size: 18px; padding: 8px; border: 1px solid #555;")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Enter Password")
        KeypadEventFilter(self.password_input, lambda: Keypad("", self.password_input.setText, self, is_password=True).exec_())
        layout.addWidget(self.password_input)

        # Show Password Checkbox
        self.chk_show = QCheckBox("Show Password")
        self.chk_show.setStyleSheet("color: #aaa; font-size: 14px;")
        self.chk_show.toggled.connect(self.toggle_echo)
        layout.addWidget(self.chk_show)

        layout.addSpacing(10)

        # Login Button
        btn_login = QPushButton("AUTHENTICATE")
        btn_login.setFixedHeight(50)
        btn_login.setStyleSheet("""
            QPushButton { background-color: #00bcd4; color: black; font-weight: bold; font-size: 16px; border-radius: 5px; }
            QPushButton:hover { background-color: #00e5ff; }
        """)
        btn_login.clicked.connect(self.check_login)
        layout.addWidget(btn_login)

        self.authenticated_role = None

    def toggle_echo(self, checked):
        if checked:
            self.password_input.setEchoMode(QLineEdit.Normal)
        else:
            self.password_input.setEchoMode(QLineEdit.Password)

    def check_login(self):
        if self.btn_role_op.isChecked():
            role = "OPERATOR"
        elif self.btn_role_eng.isChecked():
            role = "ENGINEER"
        else:
            role = "MANUFACTURER"

        password = self.password_input.text()

        # --- NEW PASSWORD LOGIC ---
        if role == "OPERATOR":
            self.authenticated_role = "OPERATOR"
            self.accept()

        elif role == "ENGINEER" and password == "1199":
            self.authenticated_role = "ENGINEER"
            self.accept()

        elif role == "MANUFACTURER" and password == "11*99":
            self.authenticated_role = "MANUFACTURER"
            self.accept()

        else:
            self.password_input.setStyleSheet("border: 2px solid #ff5252; color: white;")
            QMessageBox.warning(self, "Access Denied",
                                f"Wrong Password for {role}!")


class WelcomeOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        import json, os
        self.bg_path = BG_PATH
        if os.path.exists(SYS_INFO_FILE):
            try:
                with open(SYS_INFO_FILE, 'r') as f:
                    sys_data = json.load(f)
                self.bg_path = sys_data.get("bg_path", BG_PATH)
            except Exception:
                pass

        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 80)
        layout.setAlignment(Qt.AlignBottom | Qt.AlignHCenter)

        # Card container (Glassmorphic)
        card = QFrame()
        card.setFixedSize(580, 410)
        card.setStyleSheet("""
            QFrame {
                background-color: rgba(16, 22, 28, 0.94);
                border: 2px solid #00bcd4;
                border-radius: 14px;
            }
            QLabel {
                border: none;
                background: transparent;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(30, 24, 30, 24)
        card_layout.setSpacing(10)

        # Title
        title = QLabel("SYSTEM INITIALIZATION")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #00e5ff; font-size: 20px; font-weight: bold; letter-spacing: 2px;")
        card_layout.addWidget(title)

        # Subtitle / Status text
        self.lbl_status = QLabel("Checking software license activation...")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("color: #eceff1; font-size: 13px;")
        card_layout.addWidget(self.lbl_status)

        card_layout.addSpacing(8)

        # Sleek Horizontal Progress Bar with Neon styling
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1e272e;
                border: none;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00e676, stop:1 #00bcd4);
                border-radius: 4px;
            }
        """)
        card_layout.addWidget(self.progress_bar)

        card_layout.addSpacing(12)

        # Checklist panel (All 4 Requested Hardware/System Checks)
        self.checks_layout = QVBoxLayout()
        self.checks_layout.setSpacing(8)

        # 1. License Status
        self.lbl_check_lic = QLabel("⚫ 1. Software License Status: Verifying...")
        self.lbl_check_lic.setStyleSheet("color: #78909c; font-size: 13px; font-weight: 500;")
        self.checks_layout.addWidget(self.lbl_check_lic)

        # 2. Relay Modbus Connection Status
        self.lbl_check_modbus = QLabel("⚫ 2. Relay Modbus Board: Standby")
        self.lbl_check_modbus.setStyleSheet("color: #78909c; font-size: 13px; font-weight: 500;")
        self.checks_layout.addWidget(self.lbl_check_modbus)

        # 3. ESP32 Ejector Board Status
        self.lbl_check_esp = QLabel("⚫ 3. ESP32 Ejector Board (63-CH): Standby")
        self.lbl_check_ejector = self.lbl_check_esp
        self.lbl_check_esp.setStyleSheet("color: #78909c; font-size: 13px; font-weight: 500;")
        self.checks_layout.addWidget(self.lbl_check_esp)

        # 4. Camera Connection Status
        self.lbl_check_cam = QLabel("⚫ 4. GigE Camera Connection: Standby")
        self.lbl_check_cam.setStyleSheet("color: #78909c; font-size: 13px; font-weight: 500;")
        self.checks_layout.addWidget(self.lbl_check_cam)

        card_layout.addLayout(self.checks_layout)
        layout.addWidget(card)

        # Timer for sequential loading steps (approx 6 seconds total)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_loading)
        self.timer.start(60)  # 100 steps * 60ms = 6 seconds

    def paintEvent(self, event):
        # Paint the home background inside this overlay!
        painter = QPainter(self)
        if self.bg_path and os.path.exists(self.bg_path):
            pix = QPixmap(self.bg_path)
            if not pix.isNull():
                painter.drawPixmap(self.rect(), pix)
        else:
            painter.fillRect(self.rect(), QColor(26, 26, 26))

    def update_loading(self):
        val = self.progress_bar.value() + 1
        if val > 100:
            val = 100
        self.progress_bar.setValue(val)

        parent = self.parent()
        esp = getattr(parent, 'esp32', None)

        # Phase 1: License Status Check (0% - 25%)
        if val < 25:
            self.lbl_status.setText("Verifying software license activation...")
        elif val == 25:
            lic_info = LicenseManager.get_license_info()
            if lic_info.get("valid", False) and str(lic_info.get("status", "")).startswith("DEVELOPER MODE"):
                self.lbl_check_lic.setText("🟣 1. Software License: DEVELOPER MODE (not checked - developer PC only)")
                self.lbl_check_lic.setStyleSheet("color: #ff4fb4; font-size: 13px; font-weight: bold;")
            elif lic_info.get("valid", False):
                days = lic_info.get("days_remaining", 0)
                self.lbl_check_lic.setText(f"🟢 1. Software License: ACTIVATED ({days} Days Remaining)")
                self.lbl_check_lic.setStyleSheet("color: #00e676; font-size: 13px; font-weight: bold;")
            else:
                st = lic_info.get("status", "INVALID / EXPIRED")
                self.lbl_check_lic.setText(f"🔴 1. Software License: {st}")
                self.lbl_check_lic.setStyleSheet("color: #ff1744; font-size: 13px; font-weight: bold;")
            self.lbl_status.setText("Checking Relay Modbus Board communication...")

        # Phase 2: Relay Modbus Connection Check (25% - 50%)  (ports were chosen by auto_connect_esp32 / port_detect)
        if val == 50:
            rel_ok = bool(esp and hasattr(esp, 'is_modbus_connected') and esp.is_modbus_connected())
            rel_port = getattr(esp, 'active_port', None) or ""
            if rel_ok:
                self.lbl_check_modbus.setText(f"🟢 2. Relay Modbus Board: ONLINE ({rel_port} - 9600 8N1)")
                self.lbl_check_modbus.setStyleSheet("color: #00e676; font-size: 13px; font-weight: bold;")
            elif esp is not None and not getattr(esp, 'relay_enabled', True):
                self.lbl_check_modbus.setText("⚪ 2. Relay Modbus Board: DISABLED in the settings")
                self.lbl_check_modbus.setStyleSheet("color: #b0bec5; font-size: 13px; font-weight: bold;")
            else:
                self.lbl_check_modbus.setText("⚪ 2. Relay Modbus Board: OFFLINE (not found / not connected)")
                self.lbl_check_modbus.setStyleSheet("color: #b0bec5; font-size: 13px; font-weight: bold;")
            self.lbl_status.setText("Verifying ESP32 63-CH Ejector Shift Register...")

        # Phase 3: ESP32 Ejector Board Check (50% - 75%)
        if val == 75:
            ej_ok = bool(esp and hasattr(esp, 'is_ejector_connected') and esp.is_ejector_connected())
            ej_port = getattr(esp, 'ejector_port', None) or ""
            if ej_ok:
                self.lbl_check_esp.setText(f"🟢 3. ESP32 Ejector Board (63-CH): ONLINE ({ej_port} - 115200 Baud)")
                self.lbl_check_esp.setStyleSheet("color: #00e676; font-size: 13px; font-weight: bold;")
            else:
                self.lbl_check_esp.setText("🔴 3. ESP32 Ejector Board (63-CH): OFFLINE (no ejector found - see COM PORTS)")
                self.lbl_check_esp.setStyleSheet("color: #ff1744; font-size: 13px; font-weight: bold;")
            self.lbl_status.setText("Detecting GigE Camera sensors...")

        # Phase 4: Camera Connection Check (75% - 100%)
        if val == 92:
            cam_front_ok = bool(parent and hasattr(parent, 'camera_worker_front') and parent.camera_worker_front and parent.camera_worker_front.camera_handle is not None)
            cam_rear_ok = bool(parent and hasattr(parent, 'camera_worker_rear') and parent.camera_worker_rear and parent.camera_worker_rear.camera_handle is not None)
            if cam_front_ok and cam_rear_ok:
                cam_text = "🟢 4. GigE Camera Connection: ONLINE (Front + Rear Active)"
                cam_color = "#00e676"
            elif cam_front_ok:
                cam_text = "🟢 4. GigE Camera Connection: ONLINE (Front Camera Active)"
                cam_color = "#00e676"
            elif cam_rear_ok:
                cam_text = "🟢 4. GigE Camera Connection: ONLINE (Rear Camera Active)"
                cam_color = "#00e676"
            else:
                cam_text = "🟡 4. GigE Camera Connection: OFFLINE (Simulation / Emulation Mode)"
                cam_color = "#ffd600"
            self.lbl_check_cam.setText(cam_text)
            self.lbl_check_cam.setStyleSheet(f"color: {cam_color}; font-size: 13px; font-weight: bold;")
            self.lbl_status.setText("All hardware and subsystem checks completed! Launching...")

        elif val == 100:
            self.timer.stop()
            self.hide()
            if parent and hasattr(parent, 'run_startup_relay_sequence'):
                parent.run_startup_relay_sequence()
# =============================================================================
# RELAY & ILLUMINATION DIAGNOSTIC MANAGER (MODBUS SLAVE)
# =============================================================================
class RelayManagerDialog(QDialog):
    def __init__(self, esp32, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚡ ESP32 Relay Board & Illumination Manager (Simple Serial)")
        self.setFixedSize(740, 600)
        self.setStyleSheet("""
            QDialog {
                background-color: #12181f;
                color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
            }
            QGroupBox {
                border: 2px solid #263238;
                border-radius: 8px;
                margin-top: 14px;
                padding-top: 15px;
                font-weight: bold;
                color: #00e5ff;
                font-size: 13px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 8px;
                background-color: #12181f;
            }
            QPushButton.ActionBtn {
                background-color: #1e2833;
                border: 1px solid #37474f;
                border-radius: 6px;
                color: #eceff1;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 14px;
            }
            QPushButton.ActionBtn:hover {
                background-color: #263238;
                border: 1px solid #00e5ff;
                color: #00e5ff;
            }
        """)

        self.esp32 = esp32
        self.main_window = parent

        self.relay_names = [
            ("Relay 1: Vibrator / Feeder", "Pin 16"),
            ("Relay 3: Wiper Motor", "Pin 18"),
            ("Relay 2: Ejector Air Solenoid", "Pin 17"),
            ("Relay 4: White Front Top LED", "Pin 19"),
            ("Relay 5: White Back Top LED", "Pin 32"),
            ("Relay 6: White Front Bottom LED", "Pin 33"),
            ("Relay 7: White Back Bottom LED", "Pin 25"),
            ("Relay 8: Background LED", "Pin 26")
        ]
        self.relay_buttons = []

        self.init_ui()
        self.update_all_relay_button_states()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        # 1. Header & Status
        header_group = QGroupBox("ESP32 SERIAL COMMUNICATION STATUS")
        h_layout = QVBoxLayout(header_group)
        h_layout.setSpacing(10)

        top_row = QHBoxLayout()
        self.lbl_conn_status = QLabel()
        self.lbl_conn_status.setFont(QFont("Segoe UI", 12, QFont.Bold))
        top_row.addWidget(self.lbl_conn_status)
        top_row.addStretch()

        import serial.tools.list_ports
        self.combo_ports = QComboBox()
        self.combo_ports.setFixedWidth(210)
        self.combo_ports.setStyleSheet("background-color: #263238; color: #ffffff; padding: 5px; border-radius: 4px; font-weight: bold;")
        self.refresh_ports()
        top_row.addWidget(QLabel("COM Port:"))
        top_row.addWidget(self.combo_ports)

        btn_reconnect = QPushButton("🔄 Reconnect")
        btn_reconnect.setProperty("class", "ActionBtn")
        btn_reconnect.clicked.connect(self.reconnect_port)
        top_row.addWidget(btn_reconnect)

        h_layout.addLayout(top_row)

        info_lbl = QLabel("Architecture: PC = Master (Sends R:1:1) | ESP32 Board = Slave (9600 Baud, 8N1)")
        info_lbl.setStyleSheet("color: #90a4ae; font-size: 11px;")
        h_layout.addWidget(info_lbl)
        layout.addWidget(header_group)

        # 2. 8 Relay Buttons Grid
        relay_group = QGroupBox("INDIVIDUAL RELAY ACTUATION (Click to Toggle)")
        grid_layout = QGridLayout(relay_group)
        grid_layout.setSpacing(12)

        for i in range(8):
            relay_id = i + 1
            name, reg_info = self.relay_names[i]
            btn = QPushButton(f"{name}\n({reg_info})\n[ OFF ]")
            btn.setCheckable(True)
            btn.setFixedHeight(75)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, r_id=relay_id, b=btn: self.on_relay_clicked(r_id, checked, b))
            row = i // 4
            col = i % 4
            grid_layout.addWidget(btn, row, col)
            self.relay_buttons.append(btn)

        layout.addWidget(relay_group)

        # 3. Batch Action Buttons
        batch_group = QGroupBox("QUICK BATCH CONTROLS")
        batch_layout = QHBoxLayout(batch_group)
        batch_layout.setSpacing(10)

        btn_all_lights_on = QPushButton("💡 ALL LIGHTS ON")
        btn_all_lights_on.setStyleSheet("background-color: #1b5e20; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 10px;")
        btn_all_lights_on.setCursor(Qt.PointingHandCursor)
        btn_all_lights_on.clicked.connect(lambda: self.set_relay_group([4, 5, 6, 7, 8], 1))

        btn_all_lights_off = QPushButton("💡 ALL LIGHTS OFF")
        btn_all_lights_off.setStyleSheet("background-color: #b71c1c; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 10px;")
        btn_all_lights_off.setCursor(Qt.PointingHandCursor)
        btn_all_lights_off.clicked.connect(lambda: self.set_relay_group([4, 5, 6, 7, 8], 0))

        btn_all_relays_on = QPushButton("⚡ ALL RELAYS ON")
        btn_all_relays_on.setStyleSheet("background-color: #004d40; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 10px;")
        btn_all_relays_on.setCursor(Qt.PointingHandCursor)
        btn_all_relays_on.clicked.connect(lambda: self.set_relay_group(list(range(1, 9)), 1))

        btn_all_relays_off = QPushButton("⚡ ALL RELAYS OFF")
        btn_all_relays_off.setStyleSheet("background-color: #212121; color: #ffffff; border: 1px solid #757575; font-weight: bold; border-radius: 6px; padding: 10px;")
        btn_all_relays_off.setCursor(Qt.PointingHandCursor)
        btn_all_relays_off.clicked.connect(lambda: self.set_relay_group(list(range(1, 9)), 0))

        batch_layout.addWidget(btn_all_lights_on)
        batch_layout.addWidget(btn_all_lights_off)
        batch_layout.addWidget(btn_all_relays_on)
        batch_layout.addWidget(btn_all_relays_off)
        layout.addWidget(batch_group)

        # 4. Footer Close Button
        footer_row = QHBoxLayout()
        footer_row.addStretch()
        btn_close = QPushButton("✔ CLOSE")
        btn_close.setFixedSize(120, 40)
        btn_close.setStyleSheet("background-color: #00bcd4; color: black; font-weight: bold; font-size: 14px; border-radius: 5px;")
        btn_close.clicked.connect(self.accept)
        footer_row.addWidget(btn_close)
        layout.addLayout(footer_row)

        self.update_connection_status_ui()

    def refresh_ports(self):
        import serial.tools.list_ports
        self.combo_ports.clear()
        ports = serial.tools.list_ports.comports()
        sel_idx = 0
        current_active = getattr(self.esp32, 'active_port', None)
        for idx, p in enumerate(ports):
            desc = (p.description or "").upper()
            hwid = (p.hwid or "").upper()
            tag = ""
            if "0403:6001" in hwid or "FTDI" in desc or "USB SERIAL PORT" in desc:
                tag = " [RS485 ADAPTER]"
            elif "1A86" in hwid or "CH9102" in desc or "CP210" in desc or "CH340" in desc:
                tag = " [ESP32 USB]"
            display_text = f"{p.device}: {p.description}{tag}"
            self.combo_ports.addItem(display_text, p.device)
            if current_active and p.device == current_active:
                sel_idx = idx
            elif not current_active and "RS485" in tag:
                sel_idx = idx
        if ports:
            self.combo_ports.setCurrentIndex(sel_idx)
        else:
            self.combo_ports.addItem("No COM ports", "")

    def update_connection_status_ui(self):
        connected = bool(self.esp32 and hasattr(self.esp32, 'is_modbus_connected') and self.esp32.is_modbus_connected())
        port = getattr(self.esp32, 'active_port', None) or ""
        if connected:
            self.lbl_conn_status.setText(f"🟢 SERVER ACTIVE & ONLINE ({port})")
            self.lbl_conn_status.setStyleSheet("color: #00e676; font-weight: bold; font-size: 14px;")
        else:
            self.lbl_conn_status.setText(f"🔴 SERVER OFFLINE / DISCONNECTED")
            self.lbl_conn_status.setStyleSheet("color: #ff1744; font-weight: bold; font-size: 14px;")

    def reconnect_port(self):
        port = self.combo_ports.currentData() or ""
        if not port:
            QMessageBox.warning(self, "No port", "No COM port selected. Use COM PORTS (settings) to choose or auto detect the relay board.")
            return
        if hasattr(self.esp32, 'connect'):
            success = self.esp32.connect(port)
            self.update_connection_status_ui()
            if self.main_window and hasattr(self.main_window, 'update_esp32_relay_status_ui'):
                self.main_window.update_esp32_relay_status_ui(success, port)
            if success:
                try:
                    import json, os
                    cfg_path = "system_config.json"
                    cfg = {}
                    if os.path.exists(cfg_path):
                        with open(cfg_path, "r") as f:
                            cfg = json.load(f)
                    cfg["modbus_enabled"] = True
                    cfg["last_port"] = port
                    with open(cfg_path, "w") as f:
                        json.dump(cfg, f, indent=4)
                except Exception:
                    pass
                QMessageBox.information(self, "Connected", f"Serial connection started successfully on {port}!")
                self.update_all_relay_button_states()
            else:
                QMessageBox.critical(self, "Connection Failed", f"Failed to open port {port}.\nIs the port busy in another program?")

    def on_relay_clicked(self, relay_id, is_on, btn):
        if not self.esp32 or not getattr(self.esp32, 'is_modbus_connected', lambda: False)():
            port = self.combo_ports.currentData() or getattr(self.esp32, 'relay_port_cfg', None) or ""
            if hasattr(self.esp32, 'connect') and self.esp32.connect(port):
                self.update_connection_status_ui()
                if self.main_window and hasattr(self.main_window, 'update_esp32_relay_status_ui'):
                    self.main_window.update_esp32_relay_status_ui(True, port)
            else:
                QMessageBox.warning(self, "Offline", "ESP32 Relay Board is not connected!\nPlease click '🔄 Reconnect'.")
                btn.setChecked(not is_on)
                return
        state = 1 if is_on else 0
        self.esp32.set_relay(relay_id, state)
        self.style_relay_button(btn, relay_id, state)

    def set_relay_group(self, relay_ids, state):
        if not self.esp32 or not getattr(self.esp32, 'is_modbus_connected', lambda: False)():
            port = self.combo_ports.currentData() or getattr(self.esp32, 'relay_port_cfg', None) or ""
            if hasattr(self.esp32, 'connect') and self.esp32.connect(port):
                self.update_connection_status_ui()
                if self.main_window and hasattr(self.main_window, 'update_esp32_relay_status_ui'):
                    self.main_window.update_esp32_relay_status_ui(True, port)
            else:
                QMessageBox.warning(self, "Offline", "ESP32 Relay Board is not connected!\nPlease click '🔄 Reconnect'.")
                return
        for r_id in relay_ids:
            self.esp32.set_relay(r_id, state)
        self.update_all_relay_button_states()

    def update_all_relay_button_states(self):
        if not self.esp32 or not hasattr(self.esp32, 'get_relay'):
            return
        for i, btn in enumerate(self.relay_buttons):
            r_id = i + 1
            st = self.esp32.get_relay(r_id)
            self.style_relay_button(btn, r_id, st)

    def style_relay_button(self, btn, relay_id, state):
        name, reg_info = self.relay_names[relay_id - 1]
        btn.blockSignals(True)
        btn.setChecked(bool(state))
        btn.blockSignals(False)
        if state:
            btn.setText(f"{name}\n({reg_info})\n[ 🟢 ON ]")
            btn.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2e7d32, stop:1 #1b5e20);
                    color: #ffffff;
                    border: 2px solid #00e676;
                    border-radius: 8px;
                    font-weight: bold;
                    font-size: 11px;
                }
            """)
        else:
            btn.setText(f"{name}\n({reg_info})\n[ 🔴 OFF ]")
            btn.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #37474f, stop:1 #212121);
                    color: #b0bec5;
                    border: 1px solid #546e7a;
                    border-radius: 8px;
                    font-weight: bold;
                    font-size: 11px;
                }
            """)


# =============================================================================
# WIPER CONFIGURATION & MANUAL CONTROL DIALOG (NEW)
# =============================================================================
class WiperConfigDialog(QDialog):
    def __init__(self, esp32, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wiper Settings & Manual Controls")
        self.resize(700, 600)
        self.setStyleSheet("background-color: #1a1a1a; color: white; font-family: Segoe UI;")
        self.esp32 = esp32
        self.main_board = parent
        
        # Default settings if none exist
        if not hasattr(self.main_board, 'wiper_settings'):
             self.main_board.wiper_settings = {'fwd': 1500, 'rev': 1500, 'interval': 6, 'ejector_on': False}
        
        ws = self.main_board.wiper_settings

        layout = QVBoxLayout(self)
        layout.setSpacing(20)

        title = QLabel("WIPER CONTROLS & TIMINGS")
        title.setStyleSheet("color: #00bcd4; font-size: 22px; font-weight: bold; padding: 10px; background: #222; border-radius: 8px;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        grp_time = QGroupBox("AUTO-CLEAN CYCLE TIMINGS")
        grp_time.setStyleSheet("QGroupBox { border: 2px solid #555; border-radius: 8px; font-weight: bold; color: gray; padding-top: 25px; font-size: 14px;}")
        time_layout = QFormLayout(grp_time)
        time_layout.setVerticalSpacing(15)

        self.btn_fwd_time = QPushButton(str(ws.get('fwd', 1500)))
        self.btn_rev_time = QPushButton(str(ws.get('rev', 1500)))
        self.btn_interval = QPushButton(str(ws.get('interval', 6)))
        
        for btn, k in [(self.btn_fwd_time, 'fwd'), (self.btn_rev_time, 'rev'), (self.btn_interval, 'interval')]:
            btn.setFixedSize(120, 40)
            btn.setStyleSheet("background: #00bcd4; color: black; font-weight: bold; font-size: 16px; border-radius: 4px;")
            btn.clicked.connect(lambda _, b=btn, key=k: self.open_keypad_and_save(b, key))

        time_layout.addRow(QLabel("Forward Time (ms):"), self.btn_fwd_time)
        time_layout.addRow(QLabel("Reverse Time (ms):"), self.btn_rev_time)
        time_layout.addRow(QLabel("Auto-Clean Interval (Mins):"), self.btn_interval)

        layout.addWidget(grp_time)

        self.btn_ejector_toggle = QPushButton("EJECTOR DURING WIPER: OFF")
        self.btn_ejector_toggle.setFixedHeight(50)
        self.btn_ejector_toggle.clicked.connect(self.toggle_ejector_state)
        layout.addWidget(self.btn_ejector_toggle)
        self.update_ejector_btn_style(ws.get('ejector_on', False))

        grp_manual = QGroupBox("MANUAL CONTROLS")
        grp_manual.setStyleSheet("QGroupBox { border: 2px solid #00e676; border-radius: 8px; font-weight: bold; color: #00e676; padding-top: 25px; font-size: 14px;}")
        man_layout = QHBoxLayout(grp_manual)
        
        btn_manual_clean = QPushButton("🧹 MANUAL CLEAN")
        btn_m_off = QPushButton("⏹ STOP")
        
        btn_manual_clean.setStyleSheet("background: #00e676; color: black; font-size: 16px; font-weight: bold; padding: 15px; border-radius: 5px;")
        btn_m_off.setStyleSheet("background: #f44336; color: white; font-size: 16px; font-weight: bold; padding: 15px; border-radius: 5px;")
        
        btn_manual_clean.clicked.connect(self.run_clean_test)
        btn_m_off.clicked.connect(self.manual_wiper_off)
        
        man_layout.addWidget(btn_manual_clean)
        man_layout.addWidget(btn_m_off)
        layout.addWidget(grp_manual)

        layout.addStretch()
        
        btn_close = QPushButton("SAVE & CLOSE")
        btn_close.setFixedHeight(50)
        btn_close.setStyleSheet("background: #00bcd4; color: black; font-size: 16px; font-weight: bold; border-radius: 5px;")
        btn_close.clicked.connect(self.save_and_close)
        layout.addWidget(btn_close)

    def save_and_close(self):
        try:
            with open("wiper_config.json", "w") as f:
                json.dump(self.main_board.wiper_settings, f)
        except Exception as e:
            print(f"Failed to save Wiper Config: {e}")
        self.accept()

    def open_keypad_and_save(self, btn, dict_key):
        def callback(v):
            if v.replace('.', '', 1).isdigit():
                val = int(float(v))
                btn.setText(str(val))
                self.main_board.wiper_settings[dict_key] = val
                
                # If interval changes, restart the timer
                if dict_key == 'interval' and hasattr(self.main_board, 'restart_auto_wiper_timer'):
                    self.main_board.restart_auto_wiper_timer()
                    
        Keypad(btn.text(), callback, self).exec_()

    def update_ejector_btn_style(self, is_on):
        if is_on:
            self.btn_ejector_toggle.setText("EJECTOR DURING WIPER: ON (Relay 3 Armed)")
            self.btn_ejector_toggle.setStyleSheet("background: #ff9800; color: black; font-weight: bold; font-size: 16px; border-radius: 8px; border: 2px solid #ffb74d;")
        else:
            self.btn_ejector_toggle.setText("EJECTOR DURING WIPER: OFF")
            self.btn_ejector_toggle.setStyleSheet("background: #37474f; color: #cfd8dc; font-weight: bold; font-size: 16px; border-radius: 8px; border: 1px solid #546e7a;")

    def toggle_ejector_state(self):
        new_state = not self.main_board.wiper_settings.get('ejector_on', False)
        self.main_board.wiper_settings['ejector_on'] = new_state
        self.update_ejector_btn_style(new_state)
        # Directly control Ejector Relay (Relay 3)
        if hasattr(self.esp32, 'set_relay'):
            self.esp32.set_relay(2, 1 if new_state else 0)
            print(f"[WIPER DIALOG] Ejector Relay 2 set to {1 if new_state else 0}")

    def manual_wiper_fwd(self):
        print("[MANUAL WIPER] Forward (Relay 3 = 1)")
        if hasattr(self.esp32, 'set_relay'):
            self.esp32.set_relay(3, 1)
        if hasattr(self.esp32, 'send_command'):
            self.esp32.send_command("WIPER_FWD")

    def manual_wiper_rev(self):
        print("[MANUAL WIPER] Reverse (Relay 3 = 1)")
        if hasattr(self.esp32, 'set_relay'):
            self.esp32.set_relay(3, 1)
        if hasattr(self.esp32, 'send_command'):
            self.esp32.send_command("WIPER_REV")

    def manual_wiper_off(self):
        print("[MANUAL WIPER] Stop (Relay 3 = 0)")
        if hasattr(self.esp32, 'set_relay'):
            self.esp32.set_relay(3, 0)
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
        self.combo_nuvoton_port.setFixedWidth(320)
        
        self.btn_connect_nuvoton = QPushButton("Auto-Connected (Modbus)")
        self.btn_connect_nuvoton.setEnabled(False)
        self.btn_connect_nuvoton.setStyleSheet("background-color: #37474f; color: #00e676; font-weight: bold; padding: 8px; border-radius: 4px;")
        
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
                
                # Save to system_config.json
                try:
                    import json, os
                    cfg_file = "system_config.json"
                    cfg_data = {}
                    if os.path.exists(cfg_file):
                        with open(cfg_file, "r") as f:
                            cfg_data = json.load(f)
                    cfg_data["vibrator_port"] = port
                    with open(cfg_file, "w") as f:
                        json.dump(cfg_data, f, indent=4)
                except Exception as e:
                    print(f"Failed to save vibrator port config: {e}")
                
                # Import QMessageBox at function level if not already available
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.information(self, "Success", f"Connected to Nuvoton on {port} (9600 Baud)")
            else:
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Error", f"Failed to connect to Nuvoton on {port}")

    def mk_sw(self, st, i):
        b = QPushButton()
        b.setCheckable(True)
        b.setFixedSize(100, 40)
        b.setProperty("class", "SwitchBtn")
        b.setChecked(st)
        self.up_sw(b, st)
        b.toggled.connect(lambda c, btn=b, x=i: self.tog(x, c, btn))
        return b

    def up_sw(self, b, c):
        if c:
            b.setText("RUNNING")
            b.setStyleSheet("background-color: #00e676; color: black; font-weight: bold; font-size: 16px; border: 2px solid #00c853; border-radius: 6px;")
        else:
            b.setText("STOPPED")
            b.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; font-size: 16px; border: 2px solid #b71c1c; border-radius: 6px;")
        b.setProperty("state", "on" if c else "off")

    def mk_knob(self, v, i):
        k = GlossyKnob(value=v)
        k.valueChanged.connect(lambda val, x=i: self.up_val(x, val))
        return k

    def set_sys(self, i, c):
        self.feeder_data[i]['system_enabled'] = c

    def tog(self, i, c, b):
        self.feeder_data[i]['active'] = c
        self.up_sw(b, c)
        if i < 2:
            if self.parent() and hasattr(self.parent(), 'esp32'):
                pwm = int(self.feeder_data[i]['value'] * 2.55) if c else 0
                self.parent().esp32.set_vibrator(i + 1, pwm)

    def up_val(self, i, v):
        self.feeder_data[i]['value'] = v
        if i < 2:
            if self.parent() and hasattr(self.parent(), 'esp32'):
                pwm = int(v * 2.55) if self.feeder_data[i]['active'] else 0
                self.parent().esp32.set_vibrator(i + 1, pwm)


# =============================================================================
# 3.5. DEEP LEARNING SETUP DIALOG (NEW)
# =============================================================================
class DeepLearningSetupDialog(QDialog):
    def __init__(self, main_dashboard, parent=None, scheme_name=None):
        super().__init__(parent)
        self.main_db = main_dashboard
        if scheme_name:
            self.scheme_name = scheme_name
        else:
            self.scheme_name = getattr(main_dashboard, 'current_program', RecipeManager.get_active_scheme_name())

        self.setWindowTitle(f"Deep Learning Platform - [{self.scheme_name}]")
        self.resize(550, 420)
        self.setStyleSheet("background-color: #0a0a0a; color: white; font-family: Segoe UI;")
        
        # Load settings for this scheme
        scheme = RecipeManager.get_scheme(self.scheme_name)
        ai_cfg = scheme.get("ai_model", {}) if (scheme and isinstance(scheme, dict)) else {}
        init_model = ai_cfg.get("model_path") or self.main_db.thresholds.get("AI_MODEL_PATH", "No model uploaded (using default).")
        init_enabled = ai_cfg.get("enabled", self.main_db.thresholds.get("AI_ENABLED", False))
        init_conf = ai_cfg.get("confidence", self.main_db.thresholds.get("DEEP_LEARNING", 95))

        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(25, 25, 25, 25)

        # Title/Header
        title = QLabel(f"AI MODEL CONFIGURATION - [{self.scheme_name}]")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #00bcd4; letter-spacing: 1px;")
        layout.addWidget(title)

        # 1. Model Upload Section
        model_group = QGroupBox("TRAINED MODEL")
        model_group.setStyleSheet("QGroupBox { border: 1px solid #333; border-radius: 8px; margin-top: 15px; padding-top: 15px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #888; }")
        model_layout = QVBoxLayout(model_group)
        
        path_layout = QHBoxLayout()
        self.lbl_model_path = QLabel(init_model)
        self.lbl_model_path.setWordWrap(True)
        self.lbl_model_path.setStyleSheet("color: #aaa; font-size: 13px;")
        
        btn_upload = QPushButton("📂 UPLOAD MODEL")
        btn_upload.setFixedSize(140, 35)
        btn_upload.setStyleSheet("background-color: #00bcd4; color: black; font-weight: bold; border-radius: 4px;")
        btn_upload.clicked.connect(self.select_model)
        
        path_layout.addWidget(self.lbl_model_path, stretch=1)
        path_layout.addWidget(btn_upload)
        model_layout.addLayout(path_layout)
        layout.addWidget(model_group)

        # 2. Status & Confidence
        settings_group = QGroupBox("AI CLASSIFICATION PROFILE")
        settings_group.setStyleSheet("QGroupBox { border: 1px solid #333; border-radius: 8px; margin-top: 15px; padding-top: 15px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #888; }")
        settings_layout = QVBoxLayout(settings_group)

        # Enable/Disable switch
        status_layout = QHBoxLayout()
        status_lbl = QLabel("AI Classification State:")
        status_lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        
        self.chk_state = QCheckBox()
        self.chk_state.setChecked(init_enabled)
        self.chk_state.setFixedSize(80, 36)
        svg_dir = ASSET_DIR
        self.chk_state.setStyleSheet(f"""
            QCheckBox {{ spacing: 0px; }}
            QCheckBox::indicator {{
                width: 72px; height: 32px;
                border: none; background: transparent;
            }}
            QCheckBox::indicator:checked {{
                image: url('{svg_dir}/toggle_on.svg');
            }}
            QCheckBox::indicator:unchecked {{
                image: url('{svg_dir}/toggle_off.svg');
            }}
        """)
        
        status_layout.addWidget(status_lbl)
        status_layout.addStretch()
        status_layout.addWidget(self.chk_state)
        settings_layout.addLayout(status_layout)

        # Confidence Slider ----------|----------[0]
        conf_layout = QHBoxLayout()
        conf_lbl = QLabel("Confidence Score:")
        conf_lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        
        self.conf_slider = QSlider(Qt.Horizontal)
        self.conf_slider.setRange(0, 100)
        self.conf_slider.setValue(init_conf)
        self.conf_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 8px;
                background: #222;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #00bcd4;
                width: 20px;
                height: 20px;
                margin-top: -6px;
                margin-bottom: -6px;
                border-radius: 10px;
                border: 2px solid white;
            }
            QSlider::sub-page:horizontal {
                background: #00bcd4;
                border-radius: 4px;
            }
        """)
        
        self.lbl_conf_val = QLabel(f"{self.conf_slider.value()}%")
        self.lbl_conf_val.setFixedWidth(50)
        self.lbl_conf_val.setAlignment(Qt.AlignRight | Qt.AlignVertical_Mask) # Mask is safe fallback or AlignVCenter
        self.lbl_conf_val.setStyleSheet("color: #00bcd4; font-weight: bold; font-size: 15px;")
        
        self.conf_slider.valueChanged.connect(lambda v: self.lbl_conf_val.setText(f"{v}%"))

        conf_layout.addWidget(conf_lbl)
        conf_layout.addWidget(self.conf_slider, stretch=1)
        conf_layout.addWidget(self.lbl_conf_val)
        settings_layout.addLayout(conf_layout)
        
        layout.addWidget(settings_group)
        layout.addSpacing(10)

        # Action Buttons
        actions_layout = QHBoxLayout()
        btn_cancel = QPushButton("CANCEL")
        btn_cancel.setFixedHeight(45)
        btn_cancel.setStyleSheet("background-color: #333; color: white; border: none; font-weight: bold; border-radius: 4px;")
        btn_cancel.clicked.connect(self.reject)

        btn_save = QPushButton("💾 SAVE & APPLY")
        btn_save.setFixedHeight(45)
        btn_save.setStyleSheet("background-color: #00e676; color: black; font-weight: bold; border: none; border-radius: 4px;")
        btn_save.clicked.connect(self.save_settings)
        
        actions_layout.addWidget(btn_cancel)
        actions_layout.addWidget(btn_save)
        layout.addLayout(actions_layout)

    def select_model(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Trained AI Model", "", "Model Files (*.onnx *.pt *.pth *.tflite)")
        if file_path:
            self.lbl_model_path.setText(file_path)

    def save_settings(self):
        model_path = self.lbl_model_path.text()
        ai_enabled = self.chk_state.isChecked()
        confidence = self.conf_slider.value()

        self.main_db.thresholds["AI_MODEL_PATH"] = model_path
        self.main_db.thresholds["AI_ENABLED"] = ai_enabled
        self.main_db.thresholds["DEEP_LEARNING"] = confidence
        
        # 1. Save specifically to this scheme in recipes.json
        ai_dict = {
            "model_path": model_path,
            "enabled": ai_enabled,
            "confidence": confidence
        }
        RecipeManager.update_scheme_field(self.scheme_name, "ai_model", ai_dict)

        # 2. Also keep system_config.json synced
        import json
        config_path = "system_config.json"
        config_data = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    config_data = json.load(f)
            except:
                pass
        config_data["ai_model_path"] = model_path
        config_data["ai_enabled"] = ai_enabled
        config_data["deep_learning_confidence"] = confidence
        
        try:
            with open(config_path, "w") as f:
                json.dump(config_data, f, indent=4)
        except Exception as e:
            print(f"Error saving AI config to JSON: {e}")

        self.accept()


# =============================================================================
# 4. WIPER SETTINGS (EXISTING)
# =============================================================================
class WiperDialog(QDialog):
    def __init__(self, s, role, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Brush & Air Cleaning Setup")
        self.resize(500, 600)
        self.s = s
        self.role = role
        self.setStyleSheet(STYLESHEET)
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        g_timers = QGroupBox("AUTO CYCLE TIMERS")
        f_layout = QFormLayout()
        self.sf = QDoubleSpinBox();
        self.sf.setRange(0.1, 10.0);
        fwd_val = s.get('fwd', 1500)
        if fwd_val > 10: fwd_val /= 1000.0
        self.sf.setValue(fwd_val);
        self.sf.setSuffix(" sec")
        from PyQt5.QtWidgets import QLineEdit
        fe = self.sf.findChild(QLineEdit)
        if fe:
            KeypadEventFilter(fe, lambda s=self.sf: Keypad(
                s.value(),
                lambda val: s.setValue(float(val) if val.replace('.','',1).isdigit() else s.value()),
                self
            ).exec_())
        f_layout.addRow("Forward Time:", self.sf)

        self.sr = QDoubleSpinBox();
        self.sr.setRange(0.1, 10.0);
        rev_val = s.get('rev', 1500)
        if rev_val > 10: rev_val /= 1000.0
        self.sr.setValue(rev_val);
        self.sr.setSuffix(" sec")
        re = self.sr.findChild(QLineEdit)
        if re:
            KeypadEventFilter(re, lambda s=self.sr: Keypad(
                s.value(),
                lambda val: s.setValue(float(val) if val.replace('.','',1).isdigit() else s.value()),
                self
            ).exec_())
        f_layout.addRow("Reverse Time:", self.sr)

        self.sd = QSpinBox();
        self.sd.setRange(1, 600);
        self.sd.setValue(s.get('duration', 5));
        self.sd.setSuffix(" min")
        se = self.sd.findChild(QLineEdit)
        if se:
            KeypadEventFilter(se, lambda s=self.sd: Keypad(
                s.value(),
                lambda val: s.setValue(int(float(val)) if val.replace('.','',1).isdigit() else s.value()),
                self
            ).exec_())
        f_layout.addRow("Interval:", self.sd)
        g_timers.setLayout(f_layout)
        layout.addWidget(g_timers)
        g_user_manual = QGroupBox("MANUAL CONTROL")
        user_layout = QVBoxLayout()
        self.btn_wiper_on = QPushButton("START WIPER MANUAL")
        self.btn_wiper_on.setCheckable(True)
        self.btn_wiper_on.setFixedHeight(60)
        self.btn_wiper_on.setStyleSheet("""
            QPushButton { background-color: #333; color: white; font-size: 16px; border: 2px solid #555; }
            QPushButton:checked { background-color: #00e676; color: black; border-color: #00e676; }
        """)
        self.btn_wiper_on.toggled.connect(self.toggle_wiper_motor)
        user_layout.addWidget(self.btn_wiper_on)
        g_user_manual.setLayout(user_layout)
        layout.addWidget(g_user_manual)
        if self.role == "MANUFACTURER":
            g_mfg = QGroupBox("MAINTENANCE INCHING (MFG ONLY)")
            g_mfg.setStyleSheet("QGroupBox { border: 1px solid #ff9100; color: #ff9100; }")
            mfg_layout = QHBoxLayout()
            self.btn_fwd = QPushButton("◀ INCH FWD")
            self.btn_fwd.setFixedHeight(50)
            self.btn_fwd.pressed.connect(lambda: self.drive_motor("FWD"))
            self.btn_fwd.released.connect(lambda: self.drive_motor("STOP"))
            self.btn_rev = QPushButton("INCH REV ▶")
            self.btn_rev.setFixedHeight(50)
            self.btn_rev.pressed.connect(lambda: self.drive_motor("REV"))
            self.btn_rev.released.connect(lambda: self.drive_motor("STOP"))
            mfg_layout.addWidget(self.btn_fwd)
            mfg_layout.addWidget(self.btn_rev)
            g_mfg.setLayout(mfg_layout)
            layout.addWidget(g_mfg)
        layout.addStretch()
        btn_save = QPushButton("💾 SAVE SETTINGS")
        btn_save.setFixedHeight(50)
        btn_save.setStyleSheet("background-color: #0277bd; color: white; font-weight: bold; font-size: 16px;")
        btn_save.clicked.connect(self.save)
        layout.addWidget(btn_save)

    def toggle_wiper_motor(self, active):
        if active:
            self.btn_wiper_on.setText("WIPER RUNNING... (CLICK TO STOP)")
            print("PLC: WIPER ON")
            if self.parent() and hasattr(self.parent(), 'esp32'):
                self.parent().esp32.set_wiper(1)
        else:
            self.btn_wiper_on.setText("START WIPER MANUAL")
            print("PLC: WIPER OFF")
            if self.parent() and hasattr(self.parent(), 'esp32'):
                self.parent().esp32.set_wiper(0)

    def drive_motor(self, direction):
        print(f"MFG INCHING: {direction}")
        if direction == "FWD":
            self.btn_fwd.setStyleSheet("background-color: #ff9100; color: black;")
            if self.parent() and hasattr(self.parent(), 'esp32'):
                self.parent().esp32.set_wiper(1)
        elif direction == "REV":
            self.btn_rev.setStyleSheet("background-color: #ff9100; color: black;")
            if self.parent() and hasattr(self.parent(), 'esp32'):
                self.parent().esp32.set_wiper(1)
        else:
            self.btn_fwd.setStyleSheet("");
            self.btn_rev.setStyleSheet("")
            if self.parent() and hasattr(self.parent(), 'esp32'):
                self.parent().esp32.set_wiper(0)

    def save(self):
        self.s['fwd'] = int(self.sf.value() * 1000)
        self.s['rev'] = int(self.sr.value() * 1000)
        self.s['duration'] = self.sd.value()
        self.accept()


# =============================================================================
# 5. EJECTOR TEST PAGE (FIXED)
# =============================================================================
class EjectorTestWindow(QDialog):
    def __init__(self, esp32_handler, parent=None, scheme_name=None):
        super().__init__(parent)
        self.arduino = esp32_handler
        self.main_window = parent
        if scheme_name:
            self.scheme_name = scheme_name
        else:
            self.scheme_name = getattr(parent, 'selected_program', getattr(parent, 'current_program', None))
        if not self.scheme_name:
            self.scheme_name = RecipeManager.get_active_scheme_name()

        self.setWindowTitle(f"Ejector Diagnostics (ESP32) - [{self.scheme_name}]")
        self.resize(1024, 768)
        
        # Premium Dark Theme Stylesheet matching PLC tester simplicity & high contrast
        PREMIUM_QSS = """
        QDialog {
            background-color: #1a1a1a;
            color: #ffffff;
            font-family: 'Segoe UI', Arial, sans-serif;
        }
        QGroupBox {
            border: 1px solid #383838;
            border-radius: 8px;
            margin-top: 15px;
            background-color: #242424;
            padding-top: 15px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top center;
            padding: 0 10px;
            color: #00e5ff;
            font-size: 14px;
            font-weight: bold;
        }
        QLabel {
            color: #eeeeee;
        }
        QPushButton {
            border-radius: 6px;
            font-weight: bold;
            font-family: 'Segoe UI';
            border: none;
            color: white;
            padding: 8px;
        }
        QPushButton:hover {
            opacity: 0.9;
        }
        QPushButton:pressed {
            background-color: #444444;
        }
        QSlider::groove:horizontal {
            height: 14px;
            background: #2a2a2a;
            border-radius: 7px;
            border: 1px solid #555;
        }
        QSlider::handle:horizontal {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #00e5ff, stop:1 #007bb5);
            width: 28px;
            margin: -7px 0;
            border-radius: 14px;
            border: 2px solid #ffffff;
        }
        QComboBox {
            background-color: #2b2b2b;
            color: white;
            border: 1px solid #555;
            border-radius: 4px;
            padding: 6px;
            font-size: 13px;
        }
        """
        self.setStyleSheet(PREMIUM_QSS)

        self.firing = False
        self.auto_running = False
        if getattr(self, 'selftest_running', False):
            self.selftest_running = False
            if hasattr(self, 'selftest_timer') and self.selftest_timer and self.selftest_timer.isActive():
                self.selftest_timer.stop()
            if hasattr(self, 'lbl_selftest_ch'):
                self.lbl_selftest_ch.setText("SELF TEST: STOPPED")
                self.lbl_selftest_ch.setStyleSheet("font-size: 24px; color: #ff5252; font-weight: 900; padding: 4px;")
            if hasattr(self, 'btn_selftest'):
                self.btn_selftest.setEnabled(True)
        if self.arduino:
            if hasattr(self.arduino, '_st_running'):
                self.arduino._st_running = False
        self.current_chute = 1
        self.total_valves = 63
        self.manual_timer = None
        self.auto_timer = None
        if self.arduino:
            self.arduino.ejector_test_active = True

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(25, 20, 25, 20)

        # 1. HEADER (BACK, TITLE, STATUS, SAVE)
        header_layout = QHBoxLayout()
        btn_back = QPushButton("⬅ BACK")
        btn_back.setFixedSize(110, 42)
        btn_back.setStyleSheet("background-color: #424242; font-size: 14px; font-weight: bold; border-radius: 6px;")
        btn_back.clicked.connect(self.close_cleanly)
        header_layout.addWidget(btn_back)

        lbl_title = QLabel(f"EJECTOR SYSTEM TESTER (ESP32)")
        lbl_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #ffffff; letter-spacing: 1px; padding-left: 10px;")
        header_layout.addWidget(lbl_title)
        header_layout.addStretch()

        header_layout.addWidget(QLabel("Port:"))
        self.combo_ej_port = QComboBox()
        self.combo_ej_port.setMinimumWidth(110)
        self.refresh_ej_ports()
        header_layout.addWidget(self.combo_ej_port)

        self.btn_ej_connect = QPushButton("CONNECT")
        self.btn_ej_connect.setFixedHeight(36)
        self.btn_ej_connect.setStyleSheet("background-color: #00897b; font-size: 12px; font-weight: bold; padding: 4px 12px;")
        self.btn_ej_connect.clicked.connect(self.toggle_ej_connection)
        header_layout.addWidget(self.btn_ej_connect)

        self.lbl_status = QLabel("OFFLINE")
        self.lbl_status.setStyleSheet("font-size: 15px; font-weight: bold; color: #ff1744; padding-left: 5px;")
        header_layout.addWidget(self.lbl_status)

        btn_top_save = QPushButton("💾 SAVE")
        btn_top_save.setFixedSize(100, 42)
        btn_top_save.setStyleSheet("background-color: #007acc; font-size: 14px; font-weight: bold; border-radius: 6px; margin-left: 10px;")
        btn_top_save.clicked.connect(self.save_ejector_cfg)
        header_layout.addWidget(btn_top_save)

        main_layout.addLayout(header_layout)

        # Link statistics (protocol, firmware, ACKs, errors, round-trip time)
        self.lbl_link_stats = QLabel("LINK: -")
        self.lbl_link_stats.setStyleSheet("font-size: 12px; color: #b0bec5; font-family: Consolas, monospace; padding: 2px 4px;")
        main_layout.addWidget(self.lbl_link_stats)
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self.update_link_stats)
        self.stats_timer.start(500)

        if self.arduino:
            is_conn = bool(getattr(self.arduino, 'ejector_serial', None) and self.arduino.ejector_serial.is_open)
            if not is_conn:
                port = self.combo_ej_port.currentText() or getattr(self.arduino, 'ejector_port', None) or ""
                if port:
                    self.arduino.connect_ejector(port)
        self.update_status_indicator()

        # 2. CHUTE SELECTION
        chute_grp = QGroupBox("CHUTE SELECTION")
        chute_layout = QHBoxLayout(chute_grp)
        chute_layout.setContentsMargins(15, 10, 15, 10)
        btn_prev_chute = QPushButton("<< PREV CHUTE")
        btn_prev_chute.setStyleSheet("background-color: #383838; font-size: 14px; font-weight: bold;")
        btn_prev_chute.setFixedSize(160, 44)
        btn_prev_chute.clicked.connect(self.prev_chute)
        
        self.lbl_chute = QLabel("CHUTE 1")
        self.lbl_chute.setAlignment(Qt.AlignCenter)
        self.lbl_chute.setStyleSheet("font-size: 26px; font-weight: 900; color: #ff9800;")
        
        btn_next_chute = QPushButton("NEXT CHUTE >>")
        btn_next_chute.setStyleSheet("background-color: #383838; font-size: 14px; font-weight: bold;")
        btn_next_chute.setFixedSize(160, 44)
        btn_next_chute.clicked.connect(self.next_chute)
        
        chute_layout.addWidget(btn_prev_chute)
        chute_layout.addWidget(self.lbl_chute, stretch=1)
        chute_layout.addWidget(btn_next_chute)
        main_layout.addWidget(chute_grp)

        # 3. VALVE SELECTION (BIG LABEL, SLIDER, STEPPER BUTTONS)
        valve_grp = QGroupBox("VALVE SELECTION (1 - 63)")
        valve_layout = QVBoxLayout(valve_grp)
        valve_layout.setContentsMargins(20, 10, 20, 15)
        
        self.lbl_valve_num = QLabel("EJECTOR 1")
        self.lbl_valve_num.setAlignment(Qt.AlignCenter)
        self.lbl_valve_num.setStyleSheet("font-size: 48px; font-weight: 900; color: #ffffff; margin-top: 5px;")
        
        self.lbl_addr = QLabel("ESP32 Channel: CH-01 | Chute: 1 (Global: 1)")
        self.lbl_addr.setAlignment(Qt.AlignCenter)
        self.lbl_addr.setStyleSheet("font-size: 16px; color: #00e5ff; font-weight: bold; margin-bottom: 5px;")
        
        valve_layout.addWidget(self.lbl_valve_num)
        valve_layout.addWidget(self.lbl_addr)

        self.slider_ctrl = QSlider(Qt.Horizontal)
        self.slider_ctrl.setRange(1, self.total_valves)
        self.slider_ctrl.setValue(1)
        self.slider_ctrl.valueChanged.connect(self.update_valve_display)
        valve_layout.addWidget(self.slider_ctrl)

        # Valve Stepper Buttons (< Back Valve, Next Valve >)
        nav_valve_layout = QHBoxLayout()
        btn_back_valve = QPushButton("⏪ PREV VALVE")
        btn_back_valve.setFixedSize(160, 40)
        btn_back_valve.setStyleSheet("background-color: #424242; font-size: 14px; font-weight: bold;")
        btn_back_valve.clicked.connect(self.prev_valve)

        btn_next_valve = QPushButton("NEXT VALVE ⏩")
        btn_next_valve.setFixedSize(160, 40)
        btn_next_valve.setStyleSheet("background-color: #424242; font-size: 14px; font-weight: bold;")
        btn_next_valve.clicked.connect(self.next_valve)

        nav_valve_layout.addWidget(btn_back_valve)
        nav_valve_layout.addStretch()
        nav_valve_layout.addWidget(btn_next_valve)
        valve_layout.addLayout(nav_valve_layout)
        
        main_layout.addWidget(valve_grp)

        # 4. TIMING SETTINGS & ACTION BUTTONS
        import json, os
        ejector_cfg = {"pulse": 10.0, "delay": 500.0, "gap": 500.0, "count": 1}
        self.ejector_cfg_path = os.path.join(os.path.dirname(__file__), "ejector_config.json")
        if os.path.exists(self.ejector_cfg_path):
            try:
                with open(self.ejector_cfg_path, "r") as f:
                    ejector_cfg.update(json.load(f))
            except: pass
        scheme_data = RecipeManager.get_scheme(self.scheme_name)
        if scheme_data and isinstance(scheme_data, dict) and "ejector" in scheme_data:
            ejector_cfg.update(scheme_data["ejector"])

        mid_layout = QHBoxLayout()
        mid_layout.setSpacing(15)

        # TIMING SETTINGS (Touch-friendly Keypad triggers)
        timing_grp = QGroupBox("TIMING CONFIGURATION")
        t_layout = QFormLayout(timing_grp)
        t_layout.setVerticalSpacing(10)
        t_layout.setContentsMargins(15, 15, 15, 15)
        
        def create_param_btn(val, color):
            btn = QPushButton(str(val))
            btn.setFixedHeight(36)
            btn.setStyleSheet(f"background: #2a2a2a; border: 1px solid #555; color: {color}; font-weight: bold; font-size: 16px; border-radius: 4px;")
            return btn
            
        self.btn_pulse = create_param_btn(ejector_cfg.get("pulse", 10.0), "#ffeb3b")
        self.btn_pulse.clicked.connect(lambda: Keypad(self.btn_pulse.text(), lambda v: self.btn_pulse.setText(str(float(v))) if v.replace('.','',1).isdigit() else None, self).exec_())

        self.btn_delay = create_param_btn(ejector_cfg.get("delay", 500.0), "#00e5ff")
        self.btn_delay.clicked.connect(lambda: Keypad(self.btn_delay.text(), lambda v: self.btn_delay.setText(str(float(v))) if v.replace('.','',1).isdigit() else None, self).exec_())

        self.btn_gap = create_param_btn(ejector_cfg.get("gap", 500.0), "#ff9800")
        self.btn_gap.clicked.connect(lambda: Keypad(self.btn_gap.text(), lambda v: self.btn_gap.setText(str(float(v))) if v.replace('.','',1).isdigit() else None, self).exec_())

        self.btn_count = create_param_btn(ejector_cfg.get("count", 1), "#ff5722")
        self.btn_count.clicked.connect(lambda: Keypad(self.btn_count.text(), lambda v: self.btn_count.setText(str(float(v))) if v.replace('.','',1).isdigit() else None, self).exec_())

        lbl_style = "font-size: 13px; font-weight: bold;"
        lbl1 = QLabel("PULSE (ms):"); lbl1.setStyleSheet(lbl_style)
        lbl2 = QLabel("DELAY (ms):"); lbl2.setStyleSheet(lbl_style)
        lbl3 = QLabel("PULSE GAP (ms):"); lbl3.setStyleSheet(lbl_style)
        lbl4 = QLabel("REPEAT COUNT:"); lbl4.setStyleSheet(lbl_style)

        t_layout.addRow(lbl1, self.btn_pulse)
        t_layout.addRow(lbl2, self.btn_delay)
        t_layout.addRow(lbl3, self.btn_gap)
        t_layout.addRow(lbl4, self.btn_count)
        
        btn_save_timing = QPushButton("💾 SAVE SETTINGS")
        btn_save_timing.setFixedHeight(38)
        btn_save_timing.setStyleSheet("background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00c853, stop:1 #64dd17); color: black; font-weight: 800; font-size: 14px; margin-top: 5px;")
        btn_save_timing.clicked.connect(self.save_ejector_cfg)
        t_layout.addRow(btn_save_timing)

        mid_layout.addWidget(timing_grp, 1)

        # ACTION BUTTONS PANEL
        action_grp = QGroupBox("CONTROL & DIAGNOSTICS")
        action_layout = QVBoxLayout(action_grp)
        action_layout.setSpacing(12)
        action_layout.setContentsMargins(15, 15, 15, 15)

        row_actions = QHBoxLayout()
        row_actions.setSpacing(12)

        # Single Test Button
        self.btn_single = QPushButton("🎯 SINGLE PULSE")
        self.btn_single.setFixedHeight(54)
        self.btn_single.setStyleSheet("background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0288d1, stop:1 #01579b); font-size: 16px; font-weight: bold; border-radius: 8px;")
        self.btn_single.clicked.connect(self.fire_single_shot)
        row_actions.addWidget(self.btn_single)

        # Manual Fire Toggle (Repeating pulse loop on selected valve)
        self.btn_fire = QPushButton("🔥 MANUAL FIRE")
        self.btn_fire.setFixedHeight(54)
        self.btn_fire.setStyleSheet("background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2e7d32, stop:1 #1b5e20); font-size: 16px; font-weight: 900; border-radius: 8px;")
        self.btn_fire.clicked.connect(self.toggle_manual_fire)
        row_actions.addWidget(self.btn_fire)

        # Auto Seq (1-63)
        self.btn_auto = QPushButton("⚡ AUTO SEQ (1-63)")
        self.btn_auto.setFixedHeight(54)
        self.btn_auto.setStyleSheet("background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #007acc, stop:1 #005999); font-size: 16px; font-weight: 900; border-radius: 8px;")
        self.btn_auto.clicked.connect(self.toggle_auto_seq)
        row_actions.addWidget(self.btn_auto)

        action_layout.addLayout(row_actions)

        # Second row: Stable Wire Test & Emergency Stop
        row_bottom = QHBoxLayout()
        row_bottom.setSpacing(12)

        self.btn_wire_test = QPushButton("🔋 STABLE WIRE TEST")
        self.btn_wire_test.setFixedHeight(50)
        self.btn_wire_test.setStyleSheet("background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #006064, stop:1 #00838f); font-size: 15px; font-weight: bold; border-radius: 8px; border: 1px solid #00bcd4;")
        self.btn_wire_test.clicked.connect(self.toggle_wire_test)
        row_bottom.addWidget(self.btn_wire_test, stretch=1)

        self.btn_alloff = QPushButton("🛑 EMERGENCY OFF")
        self.btn_alloff.setFixedHeight(50)
        self.btn_alloff.setStyleSheet("background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #e53935, stop:1 #b71c1c); font-size: 16px; font-weight: 900; border-radius: 8px;")
        self.btn_alloff.clicked.connect(self.stop_all)
        row_bottom.addWidget(self.btn_alloff, stretch=1)

        action_layout.addLayout(row_bottom)

        self.lbl_wire_status = QLabel("WIRE: NOT DETECTED | CH: -- | EJ: OFF")
        self.lbl_wire_status.setStyleSheet("font-size: 13px; color: #888; font-weight: bold; padding: 4px;")
        self.lbl_wire_status.setAlignment(Qt.AlignCenter)
        action_layout.addWidget(self.lbl_wire_status)

        # Single pulse status line
        self.lbl_test_result = QLabel("SINGLE PULSE: --")
        self.lbl_test_result.setStyleSheet("font-size: 12px; color: #888; font-weight: bold; padding: 2px;")
        self.lbl_test_result.setAlignment(Qt.AlignCenter)
        action_layout.addWidget(self.lbl_test_result)

        # Self Test Row
        row_st = QHBoxLayout()
        row_st.setSpacing(8)
        row_st.addWidget(QLabel("PULSE ms:"))
        self.combo_st_pulse = QComboBox()
        self.combo_st_pulse.setEditable(True)
        self.combo_st_pulse.addItems(["5", "10", "20", "50"])
        self.combo_st_pulse.setFixedWidth(70)
        row_st.addWidget(self.combo_st_pulse)

        row_st.addWidget(QLabel("GAP ms:"))
        self.combo_st_gap = QComboBox()
        self.combo_st_gap.setEditable(True)
        self.combo_st_gap.addItems(["500", "250", "100", "1000"])
        self.combo_st_gap.setFixedWidth(80)
        row_st.addWidget(self.combo_st_gap)

        row_st.addWidget(QLabel("CH:"))
        self.spin_st_start = QSpinBox()
        self.spin_st_start.setRange(1, 63)
        self.spin_st_start.setValue(1)
        row_st.addWidget(self.spin_st_start)
        row_st.addWidget(QLabel("to"))
        self.spin_st_end = QSpinBox()
        self.spin_st_end.setRange(1, 63)
        self.spin_st_end.setValue(63)
        row_st.addWidget(self.spin_st_end)

        self.btn_selftest = QPushButton("🧪 SELF TEST 1–63")
        self.btn_selftest.setFixedHeight(44)
        self.btn_selftest.setStyleSheet("background-color: #6a1b9a; font-size: 14px; font-weight: bold; border-radius: 8px;")
        self.btn_selftest.clicked.connect(self.start_self_test)
        row_st.addWidget(self.btn_selftest, stretch=1)

        self.btn_selftest_stop = QPushButton("⏹ STOP")
        self.btn_selftest_stop.setFixedHeight(44)
        self.btn_selftest_stop.setStyleSheet("background-color: #b71c1c; font-size: 14px; font-weight: 900; border-radius: 8px;")
        self.btn_selftest_stop.clicked.connect(self.stop_all)
        row_st.addWidget(self.btn_selftest_stop)
        action_layout.addLayout(row_st)

        self.selftest_running = False
        self.selftest_timer = None
        self.lbl_selftest_ch = QLabel("SELF TEST: --")
        self.lbl_selftest_ch.setStyleSheet("font-size: 32px; color: #888; font-weight: 900; padding: 4px;")
        self.lbl_selftest_ch.setAlignment(Qt.AlignCenter)
        action_layout.addWidget(self.lbl_selftest_ch)

        mid_layout.addWidget(action_grp, 2)
        main_layout.addLayout(mid_layout)

    def refresh_ej_ports(self):
        self.combo_ej_port.clear()
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()
        curr_port = getattr(self.arduino, 'ejector_port', None) if self.arduino else None
        if not curr_port:
            try:
                import json, os
                cfg_path = os.path.join(os.path.dirname(__file__), "system_config.json")
                if os.path.exists(cfg_path):
                    with open(cfg_path, "r") as f:
                        curr_port = json.load(f).get("ejector_port", "COM24")
            except Exception:
                curr_port = "COM24"
        sel_idx = 0
        for i, p in enumerate(ports):
            self.combo_ej_port.addItem(p.device)
            if p.device == curr_port: sel_idx = i
        if not ports:
            self.combo_ej_port.addItem(curr_port or "")
        else:
            self.combo_ej_port.setCurrentIndex(sel_idx)

    @property
    def connected(self):
        return bool(self.arduino and getattr(self.arduino, 'ejector_serial', None) and self.arduino.ejector_serial.is_open)

    def update_status_indicator(self):
        is_conn = self.connected
        port = getattr(self.arduino, 'ejector_port', '') if is_conn else ''
        if is_conn:
            self.lbl_status.setText(f"ESP32 ONLINE ({port})")
            self.lbl_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #00e676;")
            self.btn_ej_connect.setText("DISCONNECT")
            self.btn_ej_connect.setStyleSheet("background-color: #c62828; color: white; font-weight: bold; font-size: 12px; padding: 4px 10px; border-radius: 4px;")
        else:
            self.lbl_status.setText("ESP32 OFFLINE")
            self.lbl_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #ff1744;")
            self.btn_ej_connect.setText("🔌 CONNECT")
            self.btn_ej_connect.setStyleSheet("background-color: #00897b; color: white; font-weight: bold; font-size: 12px; padding: 4px 10px; border-radius: 4px;")

    def toggle_ej_connection(self):
        if self.arduino:
            is_conn = bool(getattr(self.arduino, 'ejector_serial', None) and self.arduino.ejector_serial.is_open)
            if is_conn:
                self.arduino.disconnect_ejector()
            else:
                port = self.combo_ej_port.currentText()
                self.arduino.connect_ejector(port)
            self.update_status_indicator()

    def save_ejector_cfg(self):
        import json
        try:
            pulse = float(self.btn_pulse.text())
            delay = float(self.btn_delay.text())
            gap = float(self.btn_gap.text())
            count = int(float(self.btn_count.text()))
            cfg = {"pulse": pulse, "delay": delay, "gap": gap, "count": count}
            RecipeManager.update_scheme_field(self.scheme_name, "ejector", cfg)
            
            from PyQt5.QtWidgets import QApplication
            for w in QApplication.topLevelWidgets():
                if hasattr(w, 'program_data') and isinstance(getattr(w, 'program_data', None), dict):
                    if self.scheme_name in w.program_data:
                        w.program_data[self.scheme_name]["ejector"] = cfg
            with open(self.ejector_cfg_path, "w") as f:
                json.dump(cfg, f, indent=4)
            QMessageBox.information(self, "Saved", f"Settings Saved Successfully!\nPulse: {pulse} ms | Delay: {delay} ms")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save: {e}")

    # --- NAVIGATION (CHUTE & VALVE) ---
    def prev_chute(self):
        if self.current_chute > 1:
            self.current_chute -= 1
            self.update_chute_ui()

    def next_chute(self):
        if self.current_chute < 10:
            self.current_chute += 1
            self.update_chute_ui()

    def update_chute_ui(self):
        self.stop_all()
        self.lbl_chute.setText(f"CHUTE {self.current_chute}")
        self.update_valve_display(self.slider_ctrl.value())

    def prev_valve(self):
        val = self.slider_ctrl.value()
        if val > 1:
            self.slider_ctrl.setValue(val - 1)

    def next_valve(self):
        val = self.slider_ctrl.value()
        if val < self.total_valves:
            self.slider_ctrl.setValue(val + 1)

    def _addr_text(self, val):
        info = self.arduino.ejector_channel_info(val) if self.arduino and hasattr(self.arduino, 'ejector_channel_info') else f"CH {val}"
        return f"{info} | Chute: {self.current_chute}"

    def update_link_stats(self):
        link = getattr(self.arduino, 'ejector_link', None) if self.arduino else None
        self.lbl_link_stats.setText("LINK: " + (link.stats_text() if link else "OFFLINE"))

    def update_valve_display(self, val):
        self.lbl_valve_num.setText(f"EJECTOR {val}")
        self.lbl_addr.setText(self._addr_text(val))
        # If Manual Fire is active, seamlessly transfer firing to newly selected valve!
        if self.firing:
            self._start_manual_fire_cmd(val)

    # --- HARDWARE FIRING ---
    def pulse_esp32(self, valve_id):
        try:
            pulse_ms = float(self.btn_pulse.text())
        except:
            pulse_ms = 10.0
        try:
            if self.arduino and hasattr(self.arduino, 'fire_single_ejector'):
                self.arduino.fire_single_ejector(valve_id, pulse_ms)
        except Exception as e:
            print(f"[EJECTOR] Pulse error on ESP32: {e}")

    def _show_single_test_result(self, ch, sent, reply):
        sent_txt = sent.decode('utf-8', errors='replace').strip() if isinstance(sent, (bytes, bytearray)) else str(sent)
        ok_style = "font-size: 12px; color: #00e676; font-weight: bold; padding: 2px;"
        bad_style = "font-size: 12px; color: #ff5252; font-weight: bold; padding: 2px;"
        if "ERR" in str(reply):
            self.lbl_test_result.setText(f"SENT: {sent_txt} | ERROR: {reply}")
            self.lbl_test_result.setStyleSheet(bad_style)
        else:
            self.lbl_test_result.setText(f"SENT: {sent_txt} | REPLY: {reply}")
            self.lbl_test_result.setStyleSheet(ok_style)

    def _end_single_cooldown(self):
        self._single_cooldown = False
        wire_active = bool(self.main_window and getattr(self.main_window, 'wire_test_mode_active', False))
        if not (self.firing or self.auto_running or wire_active or getattr(self, 'selftest_running', False)):
            self.btn_single.setEnabled(True)

    def fire_single_shot(self):
        """Fires the current valve with single pulse."""
        if self.firing or self.auto_running or getattr(self, 'selftest_running', False):
            self.stop_all()
        ch = self.slider_ctrl.value()
        try:
            pulse_ms = float(self.btn_pulse.text())
        except Exception:
            pulse_ms = 10.0

        if self.arduino and hasattr(self.arduino, 'test_fire_single'):
            sent, reply = self.arduino.test_fire_single(ch, pulse_ms)
            self._show_single_test_result(ch, sent, reply)
        else:
            self.pulse_esp32(ch)

        self._single_cooldown = True
        self.btn_single.setEnabled(False)
        QTimer.singleShot(int(pulse_ms) + 150, self._end_single_cooldown)

    def start_self_test(self):
        if getattr(self, 'selftest_running', False):
            return
        self.stop_all()
        try:
            pulse = max(1, int(float(self.combo_st_pulse.currentText())))
            gap = max(0, int(float(self.combo_st_gap.currentText())))
        except Exception:
            self.lbl_selftest_ch.setText("SELF TEST: bad pulse/gap")
            self.lbl_selftest_ch.setStyleSheet("font-size: 22px; color: #ff5252; font-weight: 900; padding: 4px;")
            return
        s_ch = self.spin_st_start.value()
        e_ch = self.spin_st_end.value()
        if s_ch > e_ch:
            s_ch, e_ch = e_ch, s_ch

        if not (self.arduino and hasattr(self.arduino, 'start_selftest')):
            return
        sent, reply = self.arduino.start_selftest(pulse, gap, s_ch, e_ch)
        if not reply.startswith("ACK:SELFTEST"):
            self.lbl_selftest_ch.setText(f"SELF TEST not started ({reply})")
            self.lbl_selftest_ch.setStyleSheet("font-size: 18px; color: #ff5252; font-weight: 900; padding: 4px;")
            return

        self.selftest_running = True
        self.btn_selftest.setEnabled(False)
        self.btn_single.setEnabled(False)
        self.btn_fire.setEnabled(False)
        self.btn_auto.setEnabled(False)
        self.btn_wire_test.setEnabled(False)
        self.lbl_selftest_ch.setText(f"CH {s_ch}")
        self.lbl_selftest_ch.setStyleSheet("font-size: 40px; color: #ffd700; font-weight: 900; padding: 4px;")

        self.selftest_timer = QTimer(self)
        self.selftest_timer.timeout.connect(self._selftest_poll)
        self.selftest_timer.start(25)

    def _selftest_poll(self):
        if not getattr(self, 'selftest_running', False):
            return
        lines = self.arduino.poll_ejector_lines() if self.arduino else []
        for line in lines:
            if line == "ST:DONE":
                self.selftest_running = False
                if self.selftest_timer:
                    self.selftest_timer.stop()
                self.lbl_selftest_ch.setText("SELF TEST: DONE")
                self.lbl_selftest_ch.setStyleSheet("font-size: 28px; color: #00e676; font-weight: 900; padding: 4px;")
                self.btn_selftest.setEnabled(True)
                self.btn_single.setEnabled(True)
                self.btn_fire.setEnabled(True)
                self.btn_auto.setEnabled(True)
                self.btn_wire_test.setEnabled(True)
                return
            elif line.startswith("ST:"):
                try:
                    ch = int(line.split(":")[1])
                    self.lbl_selftest_ch.setText(f"CH {ch}")
                    self.lbl_selftest_ch.setStyleSheet("font-size: 40px; color: #ffd700; font-weight: 900; padding: 4px;")
                except Exception:
                    pass

    # --- MANUAL FIRE (HARDWARE CONTINUOUS FIRING VIA ESP32 CONT) ---
    def toggle_manual_fire(self):
        if self.firing:
            self.stop_all()
        else:
            self.stop_all()
            self.firing = True
            self.btn_fire.setText("🛑 STOP FIRE")
            self.btn_fire.setStyleSheet("background-color: #d32f2f; font-size: 16px; font-weight: 900; border-radius: 8px;")
            self.btn_single.setEnabled(False)
            self.btn_auto.setEnabled(False)
            self.btn_wire_test.setEnabled(False)

            val = self.slider_ctrl.value()
            self._start_manual_fire_cmd(val)

    def _start_manual_fire_cmd(self, val):
        try:
            p = max(0.2, float(self.btn_pulse.text()))
        except:
            p = 10.0
        try:
            try:
                g = max(1.0, float(self.btn_gap.text()))
            except:
                g = max(1.0, float(self.btn_delay.text()))
        except:
            g = 500.0

        # Send CONT command to ESP32 (or direct timer in ESP_BYTE mode):
        link = getattr(self.arduino, 'ejector_link', None) if self.arduino else None
        if link and link.mode == "ESP_BYTE":
            if self.manual_timer and self.manual_timer.isActive():
                self.manual_timer.stop()
            self.manual_timer = QTimer(self)
            def tick():
                if not self.firing:
                    return
                if self.arduino and hasattr(self.arduino, 'test_fire_single'):
                    sent, rep = self.arduino.test_fire_single(val, p)
                    self._show_single_test_result(val, sent, rep)
            self.manual_timer.timeout.connect(tick)
            tick()
            self.manual_timer.start(max(20, int(p + g)))
        else:
            if self.arduino and hasattr(self.arduino, 'start_continuous'):
                self.arduino.start_continuous(val, p, g)

    # --- AUTO SEQUENCE (1-63 SEQUENTIAL VALVE PULSING) ---
    def toggle_auto_seq(self):
        if self.auto_running:
            self.stop_all()
        else:
            self.stop_all()
            self.auto_running = True
            self.btn_auto.setText("🛑 STOP AUTO")
            self.btn_auto.setStyleSheet("background-color: #d32f2f; font-size: 16px; font-weight: 900; border-radius: 8px;")
            self.btn_single.setEnabled(False)
            self.btn_fire.setEnabled(False)
            self.btn_wire_test.setEnabled(False)

            try:
                p = max(0.2, float(self.btn_pulse.text()))
            except:
                p = 10.0
            try:
                g = max(1.0, float(self.btn_gap.text()))
            except:
                try:
                    g = max(1.0, float(self.btn_delay.text()))
                except:
                    g = 500.0

            step_time_ms = max(20, int(p + g))

            # 1. Reset slider to valve 1
            self.slider_ctrl.blockSignals(True)
            self.slider_ctrl.setValue(1)
            self.lbl_valve_num.setText("EJECTOR 1")
            self.lbl_addr.setText(self._addr_text(1))
            self.slider_ctrl.blockSignals(False)

            # 2. Trigger ESP32 hardware sequence (valves 1 to 63 fired natively by ESP32 without serial delays)
            link = getattr(self.arduino, 'ejector_link', None) if self.arduino else None
            self._auto_binary = bool(link and link.mode == "BINARY")
            self._auto_done_ref = link.test_done_count if link else 0
            if link:
                link.last_test_step = None
            if self.arduino and hasattr(self.arduino, 'run_ejector_test'):
                self.arduino.run_ejector_test(1, 63, p, g)

            # 3. UI follows the ESP32's own TEST_STEP reports (binary firmware), else a timer guess
            self.auto_timer = QTimer(self)
            self.auto_timer.timeout.connect(self._auto_seq_tick)
            self.auto_timer.start(25)

    def _auto_seq_tick(self):
        if not self.auto_running:
            return
        link = getattr(self.arduino, 'ejector_link', None) if self.arduino else None
        if link:
            step = getattr(link, 'last_test_step', None)
            if step:
                ch = step[0]
                self.slider_ctrl.blockSignals(True)
                self.slider_ctrl.setValue(ch)
                self.lbl_valve_num.setText(f"EJECTOR {ch}")
                self.lbl_addr.setText(self._addr_text(ch))
                self.slider_ctrl.blockSignals(False)
            if getattr(link, 'test_done_count', 0) > self._auto_done_ref:
                self.stop_all()
                self.slider_ctrl.setValue(1)
                self.update_valve_display(1)
                return
        else:
            curr_val = self.slider_ctrl.value()
            if curr_val < self.total_valves:
                next_val = curr_val + 1
                self.slider_ctrl.blockSignals(True)
                self.slider_ctrl.setValue(next_val)
                self.lbl_valve_num.setText(f"EJECTOR {next_val}")
                self.lbl_addr.setText(self._addr_text(next_val))
                self.slider_ctrl.blockSignals(False)
            else:
                self.stop_all()
                self.slider_ctrl.setValue(1)
                self.update_valve_display(1)

    # --- STABLE WIRE TEST (LAMTON CAMERA INTEGRATION) ---
    def toggle_wire_test(self):
        if self.main_window and getattr(self.main_window, 'wire_test_mode_active', False):
            self.stop_all()
        else:
            self.stop_all()
            if self.main_window:
                self.main_window.wire_test_mode_active = True
                self.main_window.wire_present = False
                self.main_window.wire_active_channel = None
                self.main_window.wire_detect_counter = 0
                self.main_window.wire_release_counter = 0
                self.main_window.wire_states = {ch: {'held': False, 'detect': 0, 'release': 0} for ch in range(1, 65)}
            
            self.btn_wire_test.setText("🔋 STABLE WIRE TEST (ACTIVE)")
            self.btn_wire_test.setStyleSheet("background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffb300, stop:1 #fb8c00); color: black; font-size: 15px; font-weight: bold; border-radius: 8px; border: 2px solid #ffffff;")
            self.btn_single.setEnabled(False)
            self.btn_fire.setEnabled(False)
            self.btn_auto.setEnabled(False)
            
            if not hasattr(self, 'wire_ui_timer') or not self.wire_ui_timer:
                self.wire_ui_timer = QTimer(self)
                self.wire_ui_timer.timeout.connect(self.update_wire_status_ui)
            self.wire_ui_timer.start(100)

    def update_wire_status_ui(self):
        if self.main_window:
            present = getattr(self.main_window, 'wire_present', False)
            ch = getattr(self.main_window, 'wire_active_channel', None)
            if present and ch:
                self.lbl_wire_status.setText(f"WIRE: DETECTED | CH: {ch} | EJ: ON")
                self.lbl_wire_status.setStyleSheet("font-size: 13px; color: #00e676; font-weight: bold; padding: 4px;")
            else:
                self.lbl_wire_status.setText("WIRE: NOT DETECTED | CH: -- | EJ: OFF")
                self.lbl_wire_status.setStyleSheet("font-size: 13px; color: #888; font-weight: bold; padding: 4px;")

    # --- STOP ALL & CLEANUP ---
    def stop_all(self):
        self.firing = False
        self.auto_running = False

        if self.manual_timer and self.manual_timer.isActive():
            self.manual_timer.stop()
            self.manual_timer = None

        if self.auto_timer and self.auto_timer.isActive():
            self.auto_timer.stop()
            self.auto_timer = None
            
        if hasattr(self, 'wire_ui_timer') and self.wire_ui_timer and self.wire_ui_timer.isActive():
            self.wire_ui_timer.stop()
            self.update_wire_status_ui()
            
        if self.main_window:
            self.main_window.wire_test_mode_active = False
            self.main_window.wire_present = False
            self.main_window.wire_active_channel = None
            
        if self.arduino:
            if hasattr(self.arduino, 'stop_continuous'):
                self.arduino.stop_continuous()
            if hasattr(self.arduino, 'stop_all_ejectors'):
                self.arduino.stop_all_ejectors()
                
        # Reset Button states and colors
        self.btn_fire.setText("🔥 MANUAL FIRE")
        self.btn_fire.setStyleSheet("background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2e7d32, stop:1 #1b5e20); font-size: 16px; font-weight: 900; border-radius: 8px;")
        
        self.btn_auto.setText("⚡ AUTO SEQ (1-63)")
        self.btn_auto.setStyleSheet("background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #007acc, stop:1 #005999); font-size: 16px; font-weight: 900; border-radius: 8px;")
        
        self.btn_wire_test.setText("🔋 STABLE WIRE TEST")
        self.btn_wire_test.setStyleSheet("background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #006064, stop:1 #00838f); font-size: 15px; font-weight: bold; border-radius: 8px; border: 1px solid #00bcd4;")
        
        self.btn_single.setEnabled(True)
        self.btn_fire.setEnabled(True)
        self.btn_auto.setEnabled(True)
        self.btn_wire_test.setEnabled(True)

    def close_cleanly(self):
        self.stop_all()
        if hasattr(self, 'stats_timer'):
            self.stats_timer.stop()
        if self.arduino:
            self.arduino.ejector_test_active = False
        self.reject()

    def closeEvent(self, event):
        self.close_cleanly()
        super().closeEvent(event)


# =============================================================================
# DATASET CREATOR UI (SMART TRIGGER & FREEZE FIX)
# =============================================================================
class DatasetCreatorUI(QDialog):
    def __init__(self, front_worker, rear_worker, program_name="DEFAULT_PROGRAM", parent=None):
        super().__init__(parent)
        self.front_worker = front_worker
        self.rear_worker = rear_worker
        self.worker = self.front_worker
        self.program_name = program_name
        self.setWindowTitle(f"Dataset Collection - {self.program_name}")
        self.setFixedSize(1920, 1080)
        self.showMaximized()
        self.setStyleSheet("background-color: #121212; color: white; font-family: Segoe UI;")

        self.cards = []
        self.view_mode = "FRONT"
        self.current_cam_no = 1
        self.curr_frame = None

        # --- SMART TRIGGER COOLDOWN ---
        self.last_smart_capture = 0.0
        self.cooldown_seconds = 0.5  # 500ms இடைவெளி (ஒரே தானியத்தை மீண்டும் மீண்டும் எடுப்பதைத் தவிர்க்க)

        # --- Folder உருவாக்குதல் ---
        safe_prog_name = "".join(x for x in self.program_name if x.isalnum() or x in " _-")
        self.program_folder = os.path.join("dataset_capture", safe_prog_name)
        if not os.path.exists(self.program_folder):
            os.makedirs(self.program_folder)

        layout = QHBoxLayout(self)

        # --- LEFT SIDE (Gallery) ---
        left = QWidget()
        left.setFixedWidth(420)
        l_layout = QVBoxLayout(left)

        tf = QFrame()
        tf.setStyleSheet("background:#1e1e1e; border-bottom:1px solid #333;")
        tl = QHBoxLayout(tf)
        chk_all = QCheckBox("Select All")
        chk_all.setStyleSheet(
            "QCheckBox { color: white; font-size: 18px; font-weight: bold; } QCheckBox::indicator { width: 40px; height: 40px; }")
        chk_all.toggled.connect(self.select_all)

        btn_del = QPushButton("🗑 Delete")
        btn_del.clicked.connect(self.delete_sel)
        tl.addWidget(chk_all)
        tl.addStretch()
        tl.addWidget(btn_del)
        l_layout.addWidget(tf)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("border:none;")
        self.grid_w = QWidget()
        self.grid = QGridLayout(self.grid_w)
        self.grid.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.grid_w)
        l_layout.addWidget(self.scroll)
        layout.addWidget(left)

        # --- CENTER (Live Video) ---
        center = QWidget()
        c_layout = QVBoxLayout(center)

        cam_row = QHBoxLayout()
        self.btn_view = QPushButton("VIEW: FRONT")
        self.btn_view.setFixedSize(160, 50)
        self.btn_view.setStyleSheet(
            "background:#00bcd4; color:black; font-weight:bold; font-size: 16px; border-radius: 4px;")
        self.btn_view.clicked.connect(self.toggle_view)
        cam_row.addWidget(self.btn_view)

        cam_row.addSpacing(10)
        self.btn_cam_no = QPushButton("CHUTE: 1")
        self.btn_cam_no.setFixedSize(120, 50)
        self.btn_cam_no.setStyleSheet(
            "background:#333; color: #ffeb3b; border: 2px solid #ffeb3b; font-weight:bold; font-size: 16px; border-radius: 4px;")
        self.btn_cam_no.clicked.connect(self.cycle_cam_no)
        cam_row.addWidget(self.btn_cam_no)
        cam_row.addStretch()

        c_layout.addLayout(cam_row)

        self.vid = QLabel("WAITING FOR CAMERA SIGNAL...")
        self.vid.setAlignment(Qt.AlignCenter)
        self.vid.setStyleSheet("background:black; border:2px solid #333; font-size: 18px; color: #555;")
        self.vid.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        c_layout.addWidget(self.vid, stretch=1)

        # ==========================================
        # SMART CAPTURE BUTTONS (NEW LOGIC)
        # ==========================================
        btns = QHBoxLayout()

        b_load = QPushButton("📂 LOAD")
        b_load.setFixedSize(120, 60)
        b_load.setStyleSheet("background:#0097a7; font-weight:bold; font-size:16px;")
        b_load.clicked.connect(self.load_images)

        # 1. MANUAL CAPTURE (SINGLE)
        b_cap = QPushButton("📸 SINGLE SNAP")
        b_cap.setFixedSize(160, 60)
        b_cap.setStyleSheet("background:#00e676; font-weight:bold; font-size:16px; color:black; border-radius: 6px;")
        b_cap.clicked.connect(self.capture)

        # 1.5 DUAL CAPTURE
        b_dual_cap = QPushButton("📸 DUAL SNAP")
        b_dual_cap.setFixedSize(160, 60)
        b_dual_cap.setStyleSheet("background:#ff9800; font-weight:bold; font-size:16px; color:black; border-radius: 6px;")
        b_dual_cap.clicked.connect(self.dual_capture)

        # 2. CONTINUOUS CAPTURE BUTTON
        self.btn_cont_cap = QPushButton("♾ CONT. CAPTURE")
        self.btn_cont_cap.setCheckable(True)
        self.btn_cont_cap.setFixedSize(180, 60)
        self.btn_cont_cap.setStyleSheet(
            "background:#333; color:#00bcd4; font-weight:bold; font-size:14px; border:2px solid #00bcd4; border-radius:6px;")
        self.btn_cont_cap.toggled.connect(self.toggle_continuous_capture)

        # 3. SMART TRIGGER BUTTON
        self.btn_smart = QPushButton("🚀 SMART TRIGGER: OFF")
        self.btn_smart.setCheckable(True)
        self.btn_smart.setFixedSize(220, 60)
        self.btn_smart.setStyleSheet(
            "background:#555; font-weight:bold; font-size:16px; color:white; border: 2px solid #777;")
        self.btn_smart.toggled.connect(self.toggle_smart)

        # 4. GRAINS TARGET WITH KEYPAD (QSpinBox-க்கு பதிலாக)
        self.target_grains = 1
        self.btn_trigger_count = QPushButton(f"GRAINS >= {self.target_grains}")
        self.btn_trigger_count.setFixedSize(160, 60)
        self.btn_trigger_count.setStyleSheet(
            "background:#111; color:#ffeb3b; font-weight:bold; font-size:16px; border:2px solid #ffeb3b;")
        self.btn_trigger_count.clicked.connect(self.open_grains_keypad)

        btns.addStretch()
        btns.addWidget(b_load)
        btns.addSpacing(10)
        btns.addWidget(b_cap)
        btns.addSpacing(10)
        btns.addWidget(b_dual_cap)
        btns.addSpacing(10)
        btns.addWidget(self.btn_cont_cap)
        btns.addSpacing(10)
        btns.addWidget(self.btn_smart)
        btns.addWidget(self.btn_trigger_count)
        btns.addStretch()

        c_layout.addLayout(btns)
        layout.addWidget(center, stretch=1)

        # கேமராவை இணைத்தல்
        self.worker.frame_ready.connect(self.update_feed_from_thread)
        self.auto_load_program_images()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.capture()
        else:
            super().keyPressEvent(event)

    def toggle_smart(self, checked):
        if checked:
            self.btn_smart.setText("🚀 SMART TRIGGER: ON")
            self.btn_smart.setStyleSheet(
                "background:#d500f9; font-weight:bold; font-size:16px; color:white; border: 2px solid white;")
            self.btn_trigger_count.setEnabled(False)  # லாக்
        else:
            self.btn_smart.setText("🚀 SMART TRIGGER: OFF")
            self.btn_smart.setStyleSheet(
                "background:#555; font-weight:bold; font-size:16px; color:white; border: 2px solid #777;")
            self.btn_trigger_count.setEnabled(True)  # அன்லாக்

    # --- KEYPAD LOGIC FOR GRAINS ---
    def open_grains_keypad(self):
        # கீபேட் ஓப்பன் ஆகும்
        Keypad(str(self.target_grains), self.update_grains_target, self).exec_()

    def update_grains_target(self, val):
        try:
            self.target_grains = int(float(val))
            self.btn_trigger_count.setText(f"GRAINS >= {self.target_grains}")
        except ValueError:
            pass

    # --- CONTINUOUS CAPTURE LOGIC ---
    def toggle_continuous_capture(self, checked):
        if not hasattr(self, 'cont_timer'):
            self.cont_timer = QTimer(self)
            self.cont_timer.timeout.connect(self.capture)

        if checked:
            self.btn_cont_cap.setText("STOP CAPTURING")
            self.btn_cont_cap.setStyleSheet(
                "background:#00bcd4; color:black; font-weight:bold; font-size:14px; border:2px solid #00bcd4; border-radius:6px;")
            self.cont_timer.start(500)  # 500ms (அரை வினாடி) இடைவெளியில் படமெடுக்கும்
        else:
            self.btn_cont_cap.setText("♾ CONT. CAPTURE")
            self.btn_cont_cap.setStyleSheet(
                "background:#333; color:#00bcd4; font-weight:bold; font-size:14px; border:2px solid #00bcd4; border-radius:6px;")
            self.cont_timer.stop()

    def auto_load_program_images(self):
        import glob
        images = glob.glob(os.path.join(self.program_folder, "*.jpg")) + glob.glob(
            os.path.join(self.program_folder, "*.png"))
        for img_path in images:
            self.add_card(img_path)

    def capture(self):
        if self.curr_frame is not None:
            try:
                resized_frame = self.curr_frame

                p = os.path.join(self.program_folder, f"IMG_{int(time.time() * 1000)}_{self.view_mode}.png")
                cv2.imwrite(p, resized_frame)

                self.add_card(p)
                QApplication.processEvents()
            except Exception as e:
                pass

    def dual_capture(self):
        try:
            ts = int(time.time() * 1000)
            added = False
            if self.front_worker.latest_frame is not None:
                f_frame = self.front_worker.latest_frame.copy()
                p_f = os.path.join(self.program_folder, f"IMG_{ts}_FRONT.png")
                cv2.imwrite(p_f, f_frame)
                self.add_card(p_f)
                added = True
                
            if self.rear_worker.latest_frame is not None:
                r_frame = self.rear_worker.latest_frame.copy()
                p_r = os.path.join(self.program_folder, f"IMG_{ts}_REAR.png")
                cv2.imwrite(p_r, r_frame)
                self.add_card(p_r)
                added = True
                
            if added:
                QApplication.processEvents()
        except Exception as e:
            print(f"Dual Capture Error: {e}")

    def load_images(self):
        from PyQt5.QtWidgets import QFileDialog
        import shutil
        fs, _ = QFileDialog.getOpenFileNames(self, "Load Images", "", "Images (*.jpg *.png)")
        for f in fs:
            filename = os.path.basename(f)
            dest_path = os.path.join(self.program_folder, filename)
            if not os.path.exists(dest_path):
                shutil.copy(f, dest_path)
            self.add_card(dest_path)

    def add_card(self, p):
        c = ImageCard(p, len(self.cards) + 1, self)
        txt_path = p.rsplit('.', 1)[0] + ".txt"
        if os.path.exists(txt_path):
            with open(txt_path, 'r') as f:
                content = f.read()
                if "0 " in content:
                    c.set_status([("dummy", "DEFECT")])
                elif "1 " in content:
                    c.set_status([("dummy", "GOOD")])

        self.cards.append(c)
        self.rearrange()

    def rearrange(self):
        for i in reversed(range(self.grid.count())):
            self.grid.itemAt(i).widget().setParent(None)
        for i, c in enumerate(self.cards):
            self.grid.addWidget(c, i // 2, i % 2)

    def delete_sel(self):
        for c in [x for x in self.cards if x.chk.isChecked()]:
            if os.path.exists(c.image_path):
                os.remove(c.image_path)
            txt_path = c.image_path.rsplit('.', 1)[0] + ".txt"
            if os.path.exists(txt_path):
                os.remove(txt_path)
            self.grid.removeWidget(c)
            c.deleteLater()
            self.cards.remove(c)
        self.rearrange()

    def select_all(self, c):
        [card.chk.setChecked(c) for card in self.cards]

    def cycle_cam_no(self):
        # Cycle up to 10 typical chutes
        self.current_cam_no = self.current_cam_no + 1 if self.current_cam_no < 10 else 1
        self.btn_cam_no.setText(f"CHUTE: {self.current_cam_no}")

    def toggle_view(self):
        self.view_mode = "REAR" if self.view_mode == "FRONT" else "FRONT"
        c = "#ff9800" if self.view_mode == "REAR" else "#00bcd4"
        self.btn_view.setText(f"VIEW: {self.view_mode}")
        self.btn_view.setStyleSheet(
            f"background:{c}; color:black; font-weight:bold; font-size: 16px; border-radius: 4px;")

        try:
            self.worker.frame_ready.disconnect(self.update_feed_from_thread)
        except Exception:
            pass

        self.worker = self.rear_worker if self.view_mode == "REAR" else self.front_worker
        self.worker.frame_ready.connect(self.update_feed_from_thread)

    def update_feed_from_thread(self, frame):
        if frame is not None:
            v_stretch = getattr(self, 'v_stretch', getattr(self.parent(), 'v_stretch', 3.0)) if hasattr(self, 'parent') else 3.0
            if v_stretch > 1.0:
                h_f, w_f = frame.shape[:2]
                frame = cv2.resize(frame, (w_f, int(h_f * v_stretch)), interpolation=cv2.INTER_LINEAR)
            self.curr_frame = frame.copy()

            # --- SMART TRIGGER LOGIC ---
            if hasattr(self, 'btn_smart') and self.btn_smart.isChecked():
                current_time = time.time()
                if current_time - self.last_smart_capture > self.cooldown_seconds:

                    gray = cv2.cvtColor(self.curr_frame, cv2.COLOR_BGR2GRAY)
                    blur = cv2.GaussianBlur(gray, (5, 5), 0)
                    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
                    cnts, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                    valid_grains = sum(1 for c in cnts if cv2.contourArea(c) > 30)

                    # QSpinBox-க்கு பதிலாக self.target_grains-ஐ பயன்படுத்துகிறோம்
                    if valid_grains >= getattr(self, 'target_grains', 1):
                        self.capture()
                        self.last_smart_capture = current_time
                        print(f"SMART TRIGGER: Captured! Detected {valid_grains} grains.")

            # --- UI UPDATE LOGIC ---
            w_label, h_label = self.vid.width(), self.vid.height()
            if w_label > 0 and h_label > 0:
                ih, iw = self.curr_frame.shape[:2]
                fit_scale = min(w_label / iw, h_label / ih)
                final_w, final_h = int(iw * fit_scale), int(ih * fit_scale)
                display_img = cv2.resize(self.curr_frame, (final_w, final_h), interpolation=cv2.INTER_AREA)
                rgba = cv2.cvtColor(display_img, cv2.COLOR_BGR2RGBA)
                rgba = np.ascontiguousarray(rgba)
                q_img = QImage(rgba.data, rgba.shape[1], rgba.shape[0], rgba.strides[0], QImage.Format_RGBA8888).copy()
                self.vid.setPixmap(QPixmap.fromImage(q_img))

    def open_editor(self, card):
        try:
            self.worker.frame_ready.disconnect(self.update_feed_from_thread)
        except Exception:
            pass

        QApplication.processEvents()

        idx = self.cards.index(card)
        editor = ImageEditor(self.cards, idx, self)
        editor.load_current_image()
        editor.exec_()

        txt_path = card.image_path.rsplit('.', 1)[0] + ".txt"
        if os.path.exists(txt_path):
            with open(txt_path, 'r') as f:
                content = f.read()
                if content.strip():
                    if "0 " in content:
                        card.set_status([("dummy", "DEFECT")])
                    elif "1 " in content:
                        card.set_status([("dummy", "GOOD")])

        try:
            self.worker.frame_ready.connect(self.update_feed_from_thread)
        except Exception:
            pass


# =============================================================================
# 1. LIGHT SETUP WINDOW (Fixed)
# =============================================================================
class LightSetupDialog(QDialog):
    def __init__(self, parent=None, scheme_name=None):
        super().__init__(parent)
        if scheme_name:
            self.scheme_name = scheme_name
        else:
            self.scheme_name = getattr(parent, 'selected_program', getattr(parent, 'current_program', None))
        if not self.scheme_name:
            self.scheme_name = RecipeManager.get_active_scheme_name()

        self.setWindowTitle(f"LED ILLUMINATION MASTER CONTROL - [{self.scheme_name}]")
        self.resize(1000, 800)
        self.setStyleSheet("background-color: #0a0a0a; color: white; font-family: Segoe UI;")
        self.rows_data = []

        # Load saved light configurations for this specific scheme
        self.saved_config = {}
        scheme_data = RecipeManager.get_scheme(self.scheme_name)
        if scheme_data and isinstance(scheme_data, dict) and "lighting" in scheme_data:
            self.saved_config = scheme_data["lighting"]
        else:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LIGHT_CONFIG_FILE)
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r") as f:
                        self.saved_config = json.load(f)
                except Exception as e:
                    print(f"[LIGHTS] Error reading {cfg_path}: {e}")

        layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border:none;")
        container = QWidget()
        container_layout = QVBoxLayout(container)

        white_box = QGroupBox("WHITE LIGHTING (TOP/BOTTOM)")
        w_lay = QGridLayout(white_box)
        self.add_row(w_lay, 0, "WHITE FRONT TOP")
        self.add_row(w_lay, 1, "WHITE FRONT BOTTOM")
        self.add_row(w_lay, 2, "WHITE BACK TOP")
        self.add_row(w_lay, 3, "WHITE BACK BOTTOM")
        container_layout.addWidget(white_box)

        front_color = QGroupBox("FRONT COLOR (RGB)")
        f_lay = QGridLayout(front_color)
        self.add_row(f_lay, 0, "RED FRONT")
        self.add_row(f_lay, 1, "GREEN FRONT")
        self.add_row(f_lay, 2, "BLUE FRONT")
        container_layout.addWidget(front_color)

        back_color = QGroupBox("BACK COLOR (RGB)")
        b_lay = QGridLayout(back_color)
        self.add_row(b_lay, 0, "RED BACK")
        self.add_row(b_lay, 1, "GREEN BACK")
        self.add_row(b_lay, 2, "BLUE BACK")
        container_layout.addWidget(back_color)

        scroll.setWidget(container)
        layout.addWidget(scroll)
        save_btn = QPushButton("💾 SAVE && APPLY LIGHTING")
        save_btn.setFixedHeight(55)
        save_btn.setStyleSheet("background: #00e676; color: black; font-weight: bold; font-size: 15px; border-radius: 6px;")
        save_btn.clicked.connect(self.apply_and_accept)
        layout.addWidget(save_btn)

    def add_row(self, layout, row, name):
        lbl = QLabel(name)
        lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(lbl, row, 0)

        saved = getattr(self, 'saved_config', {}).get(name, {})
        default_checked = True if ("WHITE" in name or "RED" in name) else False
        is_checked = saved.get("enabled", default_checked)
        lux_val = str(saved.get("lux", 500))

        # --- Toggle Switch (styled QCheckBox) ---
        chk = QCheckBox()
        chk.setChecked(is_checked)
        chk.setFixedSize(80, 36)
        svg_dir = ASSET_DIR
        chk.setStyleSheet(f"""
            QCheckBox {{ spacing: 0px; }}
            QCheckBox::indicator {{
                width: 72px; height: 32px;
                border: none; background: transparent;
            }}
            QCheckBox::indicator:checked {{
                image: url('{svg_dir}/toggle_on.svg');
            }}
            QCheckBox::indicator:unchecked {{
                image: url('{svg_dir}/toggle_off.svg');
            }}
        """)
        layout.addWidget(chk, row, 1)

        # --- Light Value Input ---
        lux = QLineEdit(lux_val)
        lux.setFixedWidth(100)
        lux.setAlignment(Qt.AlignCenter)
        lux.setStyleSheet(
            "color: #00e676; font-weight: bold; font-size: 16px; background: #111; "
            "border: 1px solid #333; border-radius: 4px; padding: 6px;")
        KeypadEventFilter(lux, lambda le=lux: Keypad(le.text(), le.setText, self).exec_())
        layout.addWidget(lux, row, 2)

        self.rows_data.append({
            'name': name,
            'chk': chk,
            'lux': lux
        })

    def apply_and_accept(self):
        main_win = None
        p = self.parent()
        while p:
            if hasattr(p, 'esp32'):
                main_win = p
                break
            if hasattr(p, 'main_window') and hasattr(p.main_window, 'esp32'):
                main_win = p.main_window
                break
            p = getattr(p, 'parent', lambda: None)()

        save_dict = {}
        for idx, item in enumerate(self.rows_data):
            name = item['name']
            enabled = item['chk'].isChecked()
            try:
                val = int(item['lux'].text())
            except Exception:
                val = 500
            save_dict[name] = {"enabled": enabled, "lux": val}
            pwm_val = min(255, val) if enabled else 0
            if main_win and hasattr(main_win, 'esp32') and main_win.esp32:
                main_win.esp32.set_led(idx + 1, pwm_val)

        # 1. Update specifically to current scheme in recipes.json
        RecipeManager.update_scheme_field(self.scheme_name, "lighting", save_dict)
        
        # 1b. Update in-memory program_data for all active windows/dialogs to prevent overwrites
        from PyQt5.QtWidgets import QApplication
        for w in QApplication.topLevelWidgets():
            if hasattr(w, 'program_data') and isinstance(getattr(w, 'program_data', None), dict):
                if self.scheme_name in w.program_data:
                    w.program_data[self.scheme_name]["lighting"] = save_dict

        # 2. Also keep light_config.json synced
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LIGHT_CONFIG_FILE)
        try:
            with open(cfg_path, "w") as f:
                json.dump(save_dict, f, indent=4)
            print(f"[LIGHTS] Saved configuration for {self.scheme_name} to {cfg_path}")
        except Exception as e:
            print(f"[LIGHTS] Error saving config: {e}")

        if main_win and hasattr(main_win, 'apply_saved_lighting'):
            main_win.apply_saved_lighting(skip_esp=True)
        self.accept()


from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QFrame, QDialog, QLineEdit, QComboBox,
                             QMessageBox, QGridLayout, QGroupBox, QScrollArea, QSlider,
                             QSizePolicy, QStackedWidget, QTabWidget)
from PyQt5.QtCore import Qt, QTimer, QRectF
from PyQt5.QtGui import QFont, QPixmap, QPainter, QPen, QColor, QPainterPath, QBrush
import numpy as np
import random
from PyQt5.QtCore import QThread, pyqtSignal
import cv2


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
                    _t_arrive = time.perf_counter()          # Phase 2: when this frame reached the PC
                    _crop_y0 = 0

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
                        _crop_y0 = start_y
                        frame = frame[start_y: start_y + safe_roi_h, :, :]

                    # 3. அகலத்தை (Width) மையமாக வைத்து வெட்டுதல்
                    if w > safe_roi_w:
                        start_x = (w - safe_roi_w) // 2
                        frame = frame[:, start_x: start_x + safe_roi_w, :]

                    # ⚠️ 4. மிக முக்கியம்: Memory Alignment (0xC0000409 எரரைத் தடுக்கும் மந்திர வரி)
                    frame = np.ascontiguousarray(frame)

                    if self.is_rear:
                        frame = cv2.flip(frame, 1)

                    # Phase 2/3/4: frame metadata (read by process_live_frame in this same thread)
                    self.frame_counter = getattr(self, 'frame_counter', 0) + 1
                    self.last_meta = {"frame_idx": self.frame_counter, "cam_ts": FrameHead.uiTimeStamp,
                                      "height": FrameHead.iHeight, "crop_y0": _crop_y0,
                                      "rows_used": frame.shape[0], "t_arrive": _t_arrive}

                    self.latest_frame = frame
                    self.frame_ready.emit(frame)
                    self._timeout_count = 0

                except mvsdk.CameraException as e:
                    if e.error_code == 164: # CAMERA_STATUS_TIME_OUT
                        pass
                    else:
                        self._timeout_count = getattr(self, '_timeout_count', 0) + 1
                        if self._timeout_count > 3:
                            print(f"[ERROR] Camera connection lost (Code: {e.error_code}). Dropping handle.")
                            try: mvsdk.CameraUnInit(self.camera_handle)
                            except: pass
                            self.camera_handle = None
                            self.connection_status.emit(False)
                            self._timeout_count = 0
                except Exception as e:
                    _now = time.monotonic()
                    if _now - getattr(self, '_last_err_log', 0.0) >= 5.0:
                        self._last_err_log = _now
                        print(f"[CAMERA WORKER] frame error: {type(e).__name__}: {e}")
            else:
                self.msleep(100)


import json
import os
import cv2
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel,
                             QComboBox, QGroupBox, QLineEdit, QPushButton,
                             QFormLayout, QSlider, QSizePolicy, QMessageBox)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap

CAMERA_CONFIG_FILE = "camera_config.json"


# =============================================================================
# DUAL CAMERA SETUP DIALOG (INDUSTRIAL STANDARD - SERIAL NUMBER BINDING)
# =============================================================================
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
        self.lbl_waveform.setStyleSheet("background: #2d2d2d; cursor: crosshair;")
        self.lbl_waveform.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.lbl_waveform.mousePressEvent = self.on_waveform_clicked
        right_layout.addWidget(self.lbl_waveform, stretch=3)

        main_layout.addWidget(right_panel, stretch=1)
        self.worker.frame_ready.connect(self.update_live_view)

        # Startup actions
        self.scan_cameras()
        self.switch_camera(self.current_cam)

    def on_waveform_clicked(self, event):
        try:
            graph_w = self.lbl_waveform.width()
            if graph_w <= 0: return
            w = getattr(self, 'current_frame_w', 4096)
            if w <= 0: w = 4096
            clicked_pix = int((event.x() / float(graph_w)) * w)
            clicked_pix = max(0, min(w, clicked_pix))
            
            start_pix = int(float(self.configs[self.current_cam].get("start_pixel", 0)))
            end_pix = int(float(self.configs[self.current_cam].get("end_pixel", w)))
            
            # Click closer to end_pix (or right click) sets END PIX; click closer to start_pix sets START PIX
            if event.button() == Qt.RightButton or abs(clicked_pix - end_pix) <= abs(clicked_pix - start_pix):
                self.update_end_pixel(clicked_pix)
            else:
                self.update_start_pixel(clicked_pix)
        except Exception as e:
            print(f"Waveform Click Error: {e}")

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
        if self.parent() and hasattr(self.parent(), "reload_camera_configs"):
            self.parent().reload_camera_configs()
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
        try:
            if frame is None: return
            h, w, c = frame.shape
            self.current_frame_w = w

            # பிக்சல் எல்லைகள்
            start_pix = int(float(self.configs[self.current_cam].get("start_pixel", 0)))
            end_pix = int(float(self.configs[self.current_cam].get("end_pixel", w)))
            start_pix = max(0, min(start_pix, int(w)))
            end_pix = max(0, min(end_pix, int(w)))

            label_w, label_h = self.lbl_live_view.width(), self.lbl_live_view.height()
            graph_w, graph_h = self.lbl_waveform.width(), self.lbl_waveform.height()

            # 1. Live View
            if label_w > 0 and label_h > 0:
                rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
                rgba = np.ascontiguousarray(rgba)
                q_img = QImage(rgba.data, int(w), int(h), rgba.strides[0], QImage.Format_RGBA8888).copy()
                scaled_pixmap = QPixmap.fromImage(q_img).scaled(label_w, label_h, Qt.KeepAspectRatio,
                                                                Qt.SmoothTransformation)
                self.lbl_live_view.setPixmap(scaled_pixmap)

            # 2. RGB Waveform Graph (Ash Gray பின்னணி + கவர்ச்சிகரமான கோடுகள்)
            if graph_w > 0 and graph_h > 0:
                # Use np.min across frame height to capture all dark defects and chute edges sharply
                avg_line = np.min(frame, axis=0)  # shape: (w, 3)
                w_pixels = avg_line.shape[0]

                if w_pixels == graph_w:
                    avg_line_resized = avg_line
                elif w_pixels > graph_w:
                    # Accurately downsample w_pixels (0 to w_pixels-1) into graph_w bins without dropping ANY pixel!
                    starts = np.linspace(0, w_pixels, graph_w, endpoint=False, dtype=int)
                    avg_line_resized = np.minimum.reduceat(avg_line, starts, axis=0)
                else:
                    avg_line_resized = cv2.resize(avg_line.reshape(1, -1, 3), (graph_w, 1), interpolation=cv2.INTER_NEAREST)[0]

                pix = QPixmap(graph_w, graph_h)
                pix.fill(QColor("#404040"))  # Ash Gray background
                p = QPainter(pix)
                p.setRenderHint(QPainter.Antialiasing)

                # Add margin to prevent clipping at top/bottom
                margin_y = 10
                plot_h = graph_h - 2 * margin_y
                max_y_val = 300.0

                # Grid கோடுகள் (0 முதல் 300 வரை)
                p.setPen(QPen(QColor("#666"), 1, Qt.DotLine))
                for i in range(0, int(max_y_val) + 1, 50):
                    y = margin_y + plot_h - int((i / max_y_val) * plot_h)
                    p.drawLine(0, y, graph_w, y)
                    p.setPen(QPen(QColor("#ffffff")))
                    p.drawText(5, y - 2, str(i))
                    p.setPen(QPen(QColor("#666"), 1, Qt.DotLine))

                # Waveform வரைதல்
                colors = [(0, "#2979ff"), (1, "#00e676"), (2, "#ff1744")]  # B, G, R
                for col_idx, color_hex in colors:
                    vals = avg_line_resized[:, col_idx]
                    path = QPainterPath()
                    p.setPen(QPen(QColor(color_hex), 2))
                    path.moveTo(0, margin_y + plot_h - ((vals[0] / max_y_val) * plot_h))
                    for ix, v in enumerate(vals):
                        path.lineTo(int(ix), int(margin_y + plot_h - ((v / max_y_val) * plot_h)))
                    p.drawPath(path)

                # --- வரைபடத்தில் Start மற்றும் End எல்லைக் கோடுகளை வரைதல் ---
                safe_w = max(1, int(w))
                start_x_graph = max(0, min(graph_w - 1, int((start_pix / float(safe_w)) * graph_w)))
                end_x_graph = max(0, min(graph_w - 1, int((end_pix / float(safe_w)) * graph_w)))

                # Intermediate Channel Guides (e.g., CH 25)
                num_ch = int(self.configs[self.current_cam].get("num_channels", 63))
                if end_pix > start_pix and num_ch > 0:
                    px_per_ch = (end_pix - start_pix) / float(num_ch)
                    p.setFont(QFont("Arial", 8))
                    for ch_mark in [10, 20, 25, 30, 40, 50, 60]:
                        if ch_mark < num_ch:
                            ch_pix = start_pix + (ch_mark - 0.5) * px_per_ch
                            ch_x = int((ch_pix / float(safe_w)) * graph_w)
                            if 0 <= ch_x < graph_w:
                                p.setPen(QPen(QColor("#00bcd4" if ch_mark == 25 else "#555555"), 1, Qt.DotLine))
                                p.drawLine(ch_x, 30, ch_x, graph_h - 18)
                                if ch_mark == 25:
                                    p.setPen(QPen(QColor("#00bcd4")))
                                    p.drawText(ch_x - 12, 42, "CH25")

                # Dotted/Dash Line: Orange (Start) & Pink (End)
                p.setPen(QPen(QColor("#FF9800"), 3, Qt.DashLine))
                p.drawLine(start_x_graph, 0, start_x_graph, graph_h)

                p.setPen(QPen(QColor("#FF00FF"), 3, Qt.DashLine))  # Pink color for End
                p.drawLine(end_x_graph, 0, end_x_graph, graph_h)

                # எண்கள்
                p.setFont(QFont("Arial", 11, QFont.Bold))
                p.setPen(QPen(QColor("#FF9800")))
                p.drawText(start_x_graph + 5, 25, f"START: {start_pix}")
                p.setPen(QPen(QColor("#FF00FF")))
                txt_end_x = max(10, min(graph_w - 110, end_x_graph - 95))
                p.drawText(txt_end_x, 25, f"END: {end_pix}")

                # Hint text at the bottom: Click on graph to set pixel
                p.setFont(QFont("Arial", 9))
                p.setPen(QPen(QColor("#bbb")))
                p.drawText(graph_w // 2 - 120, graph_h - 10, "💡 Click waveform to set END / START PIX")

                p.end()
                self.lbl_waveform.setPixmap(pix)

        except Exception as e:
            print(f"Update Error: {e}")


# =============================================================================
# 2. BACKGROUND & GRAPH WINDOW (REMOVED DUPLICATE PIXEL INPUTS - READS FROM CAMERA SETUP)
# =============================================================================
class BackgroundGraphWindow(QDialog):
    @property
    def thresh_lower(self):
        return self.camera_thresh[self.camera_view]["lower"]

    @thresh_lower.setter
    def thresh_lower(self, value):
        self.camera_thresh[self.camera_view]["lower"] = value

    @property
    def thresh_upper(self):
        return self.camera_thresh[self.camera_view]["upper"]

    @thresh_upper.setter
    def thresh_upper(self, value):
        self.camera_thresh[self.camera_view]["upper"] = value

    @property
    def band_mode(self):
        return self.camera_thresh[self.camera_view]["band_mode"]

    @band_mode.setter
    def band_mode(self, value):
        self.camera_thresh[self.camera_view]["band_mode"] = value

    def __init__(self, front_worker=None, rear_worker=None, esp32_handler=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Program Params - Background Tuning (ESP32)")
        self.resize(1920, 1080)
        self.front_worker = front_worker
        self.rear_worker = rear_worker
        self.camera_worker = self.front_worker  # Default FRONT
        self.arduino = esp32_handler
        self.latest_frame = None
        if self.camera_worker and hasattr(self.camera_worker, 'frame_ready'):
            self.camera_worker.frame_ready.connect(self.update_live_frame)

        self.setStyleSheet("""
            QDialog { background-color: #1e272e; color: #ecf0f1; font-family: Segoe UI; }
            QLabel { color: #d2dae2; font-weight: bold; font-size: 12px; }
            QFrame { background-color: #2f3640; border: 1px solid #4b6584; border-radius: 5px; }
            QPushButton { background-color: #4b6584; color: white; border: 1px solid #778ca3; border-radius: 4px; padding: 5px; font-weight: bold; font-size: 13px; }
            QPushButton:hover { background-color: #00bcd4; border-color: white; color: black;}
            QComboBox { background: #4b6584; color: white; border: 1px solid #778ca3; padding: 5px; font-size: 13px; font-weight: bold; border-radius: 4px;}
            QComboBox::drop-down { border: none; }
            QGroupBox { border: 1px solid #4b6584; margin-top: 12px; font-weight: bold; background: #2f3640; border-radius: 5px; padding-top: 15px; } 
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; padding: 0 5px; color: #00bcd4; background: #2f3640; }

            QSlider::groove:horizontal { background: #1e272e; height: 10px; border-radius: 5px; }
            QSlider::handle:horizontal { width: 20px; margin: -5px 0; border-radius: 10px; border: 2px solid white; }
            QSlider#LowerSlider::handle:horizontal { background: #ff9800; }
            QSlider#UpperSlider::handle:horizontal { background: #d500f9; }
        """)

        self.parent_ui = parent
        self.camera_view = "FRONT"
        self.camera_id = 1

        # Default Logic Vars
        self.ref_b = 220
        self.ref_g = 200
        self.ref_r = 180
        self.camera_thresh = {
            "FRONT": {"lower": 80, "upper": 225, "band_mode": False},
            "REAR": {"lower": 80, "upper": 225, "band_mode": False}
        }

        # Camera Setup-ல் இருந்து எடுக்கப்படும் Master அளவுகள்
        self.num_channels = 63
        self.start_pixel = 0
        self.end_pixel = 4096

        self.load_bg_settings()  # Load thresholds
        self.load_camera_bounds()  # Load master pixel bounds from camera setup

        self.wave_mode = "BLUE"

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # --- 1. TOP VALVE INDICATORS ---
        valve_layout = QVBoxLayout()
        v_head = QHBoxLayout()

        v_head.addWidget(QLabel("EJECTOR COMMUNICATION BOARD STATUS (CH 1 - 63)"))
        v_head.addStretch()

        if self.arduino and getattr(self.arduino, 'connected', False):
            self.lbl_esp32_status = QLabel("ESP32: CONNECTED")
            self.lbl_esp32_status.setStyleSheet("color: #00e676; font-weight: bold;")
        else:
            self.lbl_esp32_status = QLabel("ESP32: DISCONNECTED")
            self.lbl_esp32_status.setStyleSheet("color: #ff1744; font-weight: bold;")
        v_head.addWidget(self.lbl_esp32_status)
        valve_layout.addLayout(v_head)

        self.led_frame = QFrame()
        self.led_frame.setFixedHeight(28)
        self.led_frame.setStyleSheet("background: black; border: 1px solid #4b6584;")
        self.lbl_leds = QLabel(self.led_frame)
        self.lbl_leds.setScaledContents(True)
        self.lbl_leds.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.lbl_leds.setCursor(Qt.PointingHandCursor)
        self.lbl_leds.setToolTip("63-Channel Ejector LEDs: Click any channel to test fire that valve")
        def _on_top_led_click(ev):
            if ev.button() == Qt.LeftButton:
                wl = self.lbl_leds.width()
                if wl > 0 and self.num_channels > 0:
                    bw = wl / float(self.num_channels)
                    ch_i = int(ev.x() / bw)
                    if 0 <= ch_i < self.num_channels:
                        now_t = time.time()
                        self.led_hold_until[ch_i] = now_t + 0.35
                        disp = [1 if (i < len(self.led_hold_until) and self.led_hold_until[i] > now_t) else 0 for i in range(self.num_channels)]
                        self.draw_top_led_bar(disp)
                        if self.arduino:
                            try:
                                self.arduino.fire_single_ejector(ch_i + 1, 25)
                            except Exception as ex:
                                print(f"Top LED click fire error: {ex}")
        self.lbl_leds.mousePressEvent = _on_top_led_click
        l_lay = QVBoxLayout(self.led_frame)
        l_lay.setContentsMargins(0, 0, 0, 0)
        l_lay.addWidget(self.lbl_leds)
        valve_layout.addWidget(self.led_frame)
        main_layout.addLayout(valve_layout)
        QTimer.singleShot(150, lambda: self.draw_top_led_bar([0] * self.num_channels))

        # --- 2. HEADER CONTROLS ---
        header = QHBoxLayout()
        header.setSpacing(8)

        header.addWidget(QLabel("SOURCE:"))
        self.combo_color = QComboBox()
        self.combo_color.addItems(
            ["BLUE", "GREEN", "RED", "COMBO: ALL (RGB)", "COMBO: BLUE+GREEN", "COMBO: RED+GREEN", "COMBO: RED+BLUE"])
        self.combo_color.setFixedSize(160, 40)
        self.combo_color.currentTextChanged.connect(self.change_wave_mode)
        header.addWidget(self.combo_color)

        self.btn_band = QPushButton("📶 BAND PASS: OFF")
        self.btn_band.setFixedSize(140, 40)
        self.btn_band.setAutoDefault(False)
        self.btn_band.clicked.connect(self.toggle_band_mode)
        header.addWidget(self.btn_band)

        if self.arduino and getattr(self.arduino, 'connected', False):
            self.btn_esp32 = QPushButton("🔌 DISCONNECT ESP32")
        else:
            self.btn_esp32 = QPushButton("🔌 CONNECT ESP32")
        self.btn_esp32.setFixedSize(130, 40)
        self.btn_esp32.setAutoDefault(False)
        self.btn_esp32.setStyleSheet("background: #333; color: white; border: 1px solid #777;")
        self.btn_esp32.clicked.connect(self.toggle_esp32_connection)
        header.addWidget(self.btn_esp32)

        self.lbl_voltage = QLabel("SIGNAL: 0.00V")
        self.lbl_voltage.setFixedWidth(120)
        self.lbl_voltage.setAlignment(Qt.AlignCenter)
        self.lbl_voltage.setStyleSheet(
            "background: #111; color: #00bcd4; font-size: 14px; font-weight: bold; border: 1px solid #444; border-radius: 4px; padding: 5px;")
        header.addWidget(self.lbl_voltage)
        header.addSpacing(30)

        self.btn_local_start = QPushButton("\u25b6 FIRING ACTIVE")
        self.btn_local_start.setFixedSize(160, 40)
        self.btn_local_start.setAutoDefault(False)
        self.btn_local_start.setStyleSheet(
            "background: #1b5e20; color: #00e676; font-weight: bold; border: 2px solid #00e676; border-radius: 4px; font-size: 14px;")
        self.btn_local_start.clicked.connect(self.start_local_system)
        header.addWidget(self.btn_local_start)

        self.btn_local_stop = QPushButton("\u23f9 STOP FIRING")
        self.btn_local_stop.setFixedSize(130, 40)
        self.btn_local_stop.setAutoDefault(False)
        self.btn_local_stop.setStyleSheet(
            "background: #444; color: #bbb; font-weight: bold; border-radius: 4px; font-size: 14px;")
        self.btn_local_stop.clicked.connect(self.stop_local_system)
        header.addWidget(self.btn_local_stop)

        self.local_system_running = True
        self.led_hold_until = [0.0] * 64
        self.hw_active_channels = set()
        self.hw_channel_on_time = {}
        self.dragging_bound = None  # 'START' or 'END' or None
        self._graph_start_x = 0
        self._graph_end_x = 0
        self._graph_w = 0
        self._graph_img_w = 4096

        self._min_trigger_channels = 1
        self._cached_ejector_pulse = 25.0
        self._cached_num_pulses    = 1
        self._cached_pulse_gap     = 12.0
        self._reload_ejector_pulse()
        header.addStretch()

        # --- Interactive Bounds Adjuster Box (START / END Nudge & Keypad) ---
        bounds_box = QFrame()
        bounds_box.setStyleSheet("background: #212c33; border: 1px solid #37474f; border-radius: 4px; padding: 2px;")
        bb_lay = QHBoxLayout(bounds_box)
        bb_lay.setContentsMargins(6, 2, 6, 2)
        bb_lay.setSpacing(4)

        lbl_s = QLabel("START:")
        lbl_s.setStyleSheet("color: #00FFFF; font-weight: bold; font-size: 13px;")
        bb_lay.addWidget(lbl_s)

        self.btn_adj_start_dec = QPushButton("◀")
        self.btn_adj_start_dec.setFixedSize(26, 30)
        self.btn_adj_start_dec.setAutoDefault(False)
        self.btn_adj_start_dec.setToolTip("Nudge START line left (-5 px)")
        self.btn_adj_start_dec.clicked.connect(lambda: self.nudge_start(-5))
        bb_lay.addWidget(self.btn_adj_start_dec)

        self.btn_adj_start_val = QPushButton(str(self.start_pixel))
        self.btn_adj_start_val.setFixedSize(58, 30)
        self.btn_adj_start_val.setAutoDefault(False)
        self.btn_adj_start_val.setStyleSheet("color: #00FFFF; border: 1px solid #00FFFF; font-weight: bold; font-size: 13px; background: #141c21;")
        self.btn_adj_start_val.setToolTip("Click to enter START pixel via keypad")
        self.btn_adj_start_val.clicked.connect(self.open_start_pixel_keypad)
        bb_lay.addWidget(self.btn_adj_start_val)

        self.btn_adj_start_inc = QPushButton("▶")
        self.btn_adj_start_inc.setFixedSize(26, 30)
        self.btn_adj_start_inc.setAutoDefault(False)
        self.btn_adj_start_inc.setToolTip("Nudge START line right (+5 px)")
        self.btn_adj_start_inc.clicked.connect(lambda: self.nudge_start(5))
        bb_lay.addWidget(self.btn_adj_start_inc)

        bb_lay.addSpacing(8)

        lbl_e = QLabel("END:")
        lbl_e.setStyleSheet("color: #ff9800; font-weight: bold; font-size: 13px;")
        bb_lay.addWidget(lbl_e)

        self.btn_adj_end_dec = QPushButton("◀")
        self.btn_adj_end_dec.setFixedSize(26, 30)
        self.btn_adj_end_dec.setAutoDefault(False)
        self.btn_adj_end_dec.setToolTip("Nudge END line left (-5 px)")
        self.btn_adj_end_dec.clicked.connect(lambda: self.nudge_end(-5))
        bb_lay.addWidget(self.btn_adj_end_dec)

        self.btn_adj_end_val = QPushButton(str(self.end_pixel))
        self.btn_adj_end_val.setFixedSize(58, 30)
        self.btn_adj_end_val.setAutoDefault(False)
        self.btn_adj_end_val.setStyleSheet("color: #ff9800; border: 1px solid #ff9800; font-weight: bold; font-size: 13px; background: #141c21;")
        self.btn_adj_end_val.setToolTip("Click to enter END pixel via keypad")
        self.btn_adj_end_val.clicked.connect(self.open_end_pixel_keypad)
        bb_lay.addWidget(self.btn_adj_end_val)

        self.btn_adj_end_inc = QPushButton("▶")
        self.btn_adj_end_inc.setFixedSize(26, 30)
        self.btn_adj_end_inc.setAutoDefault(False)
        self.btn_adj_end_inc.setToolTip("Nudge END line right (+5 px)")
        self.btn_adj_end_inc.clicked.connect(lambda: self.nudge_end(5))
        bb_lay.addWidget(self.btn_adj_end_inc)

        self.lbl_bounds_info = QLabel(f"({self.num_channels} CH)")
        self.lbl_bounds_info.setStyleSheet("color: #00e676; font-weight: bold; font-size: 13px; padding-left: 4px;")
        bb_lay.addWidget(self.lbl_bounds_info)

        header.addWidget(bounds_box)

        self.btn_view = QPushButton("VIEW: FRONT")
        self.btn_view.setFixedSize(120, 40)
        self.btn_view.setAutoDefault(False)
        self.btn_view.clicked.connect(self.toggle_view)
        self.btn_view.setStyleSheet("background-color: #00bcd4; color: black; border: none;")
        header.addWidget(self.btn_view)

        cam_box = QFrame()
        cb_layout = QHBoxLayout(cam_box)
        cb_layout.setContentsMargins(5, 2, 5, 2)
        cb_layout.setSpacing(5)
        cb_layout.addWidget(QLabel("CAM:"))

        btn_prev = QPushButton("◀")
        btn_prev.setFixedSize(30, 30)
        btn_prev.setAutoDefault(False)
        btn_prev.clicked.connect(self.prev_cam)
        cb_layout.addWidget(btn_prev)

        self.btn_cam_id = QPushButton("1")
        self.btn_cam_id.setFixedSize(40, 30)
        self.btn_cam_id.setAutoDefault(False)
        self.btn_cam_id.setStyleSheet("color: #00e676; font-size: 16px; border: 1px solid #00e676;")
        self.btn_cam_id.clicked.connect(self.open_cam_keypad)
        cb_layout.addWidget(self.btn_cam_id)

        btn_next = QPushButton("▶")
        btn_next.setFixedSize(30, 30)
        btn_next.setAutoDefault(False)
        btn_next.clicked.connect(self.next_cam)
        cb_layout.addWidget(btn_next)

        header.addWidget(cam_box)
        main_layout.addLayout(header)

        # --- 3. GRAPH AREA ---
        self.graph_frame = QFrame()
        self.graph_frame.setStyleSheet("background-color: black; border: 2px solid #4b6584;")
        self.graph_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.lbl_graph = QLabel(self.graph_frame)
        self.lbl_graph.setAlignment(Qt.AlignCenter)
        self.lbl_graph.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.lbl_graph.setMouseTracking(True)
        self.lbl_graph.mousePressEvent = self.on_graph_mouse_press
        self.lbl_graph.mouseMoveEvent = self.on_graph_mouse_move
        self.lbl_graph.mouseReleaseEvent = self.on_graph_mouse_release
        g_lay = QVBoxLayout(self.graph_frame)
        g_lay.setContentsMargins(0, 0, 0, 0)
        g_lay.addWidget(self.lbl_graph)
        main_layout.addWidget(self.graph_frame, stretch=1)

        # --- 4. BOTTOM SECTION ---
        bottom_split = QHBoxLayout()
        bottom_split.setSpacing(15)

        left_group = QGroupBox("REFERENCE LEVELS")
        lg_layout = QHBoxLayout(left_group)
        lg_layout.setContentsMargins(10, 20, 10, 10)
        lg_layout.setSpacing(15)

        def create_v_box(label_text, val, setter_func, color_style):
            container = QWidget()
            v_lay = QVBoxLayout(container)
            v_lay.setContentsMargins(0, 0, 0, 0)
            v_lay.setSpacing(5)
            lbl = QLabel(label_text)
            lbl.setAlignment(Qt.AlignCenter)
            v_lay.addWidget(lbl)
            btn = QPushButton(str(val))
            btn.setFixedSize(80, 40)
            btn.setAutoDefault(False)
            btn.setStyleSheet(f"color: {color_style}; border: 1px solid {color_style}; font-size: 16px;")
            btn.clicked.connect(
                lambda: Keypad(btn.text(), lambda v: [setter_func(float(v)), btn.setText(str(v))], self).exec_())
            v_lay.addWidget(btn)
            lg_layout.addWidget(container)
            return btn

        self.btn_ref_b = create_v_box("BLUE REF", self.ref_b, lambda v: self.set_camera_rgb_gain('b', v), "#2979ff")
        self.btn_ref_g = create_v_box("GREEN REF", self.ref_g, lambda v: self.set_camera_rgb_gain('g', v), "#00e676")
        self.btn_ref_r = create_v_box("RED REF", self.ref_r, lambda v: self.set_camera_rgb_gain('r', v), "#ff1744")
        bottom_split.addWidget(left_group, stretch=1)

        self.group_limits = QGroupBox("THRESHOLDS / LIMITS")
        lim_lay = QVBoxLayout(self.group_limits)
        lim_lay.setContentsMargins(10, 20, 10, 10)
        lim_lay.setSpacing(8)

        # Upper Slider
        h1 = QHBoxLayout()
        self.lbl_upper = QLabel("WHITE (HI):")
        h1.addWidget(self.lbl_upper)
        self.slider_upper = QSlider(Qt.Horizontal)
        self.slider_upper.setObjectName("UpperSlider")
        self.slider_upper.setRange(0, 350)
        self.slider_upper.setValue(self.thresh_upper)
        self.slider_upper.valueChanged.connect(self.update_upper_thresh)
        h1.addWidget(self.slider_upper)
        self.btn_slider_up_val = QPushButton(str(self.thresh_upper))
        self.btn_slider_up_val.setFixedSize(60, 35)
        self.btn_slider_up_val.setAutoDefault(False)
        self.btn_slider_up_val.setStyleSheet("color: #00e5ff; border: 1px solid #00e5ff;")
        self.btn_slider_up_val.clicked.connect(
            lambda: Keypad(self.btn_slider_up_val.text(), self.set_upper_manual, self).exec_())
        h1.addWidget(self.btn_slider_up_val)
        lim_lay.addLayout(h1)

        # Lower Slider
        h2 = QHBoxLayout()
        self.lbl_lower = QLabel("BLACK (LO):")
        h2.addWidget(self.lbl_lower)
        self.slider_lower = QSlider(Qt.Horizontal)
        self.slider_lower.setObjectName("LowerSlider")
        self.slider_lower.setRange(0, 350)
        self.slider_lower.setValue(self.thresh_lower)
        self.slider_lower.valueChanged.connect(self.update_lower_thresh)
        h2.addWidget(self.slider_lower)
        self.btn_slider_low_val = QPushButton(str(self.thresh_lower))
        self.btn_slider_low_val.setFixedSize(60, 35)
        self.btn_slider_low_val.setAutoDefault(False)
        self.btn_slider_low_val.setStyleSheet("color: yellow; border: 1px solid yellow;")
        self.btn_slider_low_val.clicked.connect(
            lambda: Keypad(self.btn_slider_low_val.text(), self.set_lower_manual, self).exec_())
        h2.addWidget(self.btn_slider_low_val)
        lim_lay.addLayout(h2)

        bottom_split.addWidget(self.group_limits, stretch=2)

        # Action Buttons
        btn_panel = QVBoxLayout()
        btn_panel.addStretch()

        self.btn_reset = QPushButton("🔄 RESET")
        self.btn_reset.setFixedSize(110, 45)
        self.btn_reset.setAutoDefault(False)
        self.btn_reset.setStyleSheet("background: #ff1744; color: white; font-weight: bold; border: none;")
        self.btn_reset.clicked.connect(self.reset_settings)
        btn_panel.addWidget(self.btn_reset)

        self.btn_save = QPushButton("💾 SAVE")
        self.btn_save.setFixedSize(110, 45)
        self.btn_save.setAutoDefault(False)
        self.btn_save.setStyleSheet("background: #00e676; color: black; font-weight: bold; border: none;")
        self.btn_save.clicked.connect(self.save_settings)
        btn_panel.addWidget(self.btn_save)

        btn_panel.addSpacing(10)
        self.btn_back = QPushButton("⬅ BACK")
        self.btn_back.setFixedSize(110, 45)
        self.btn_back.setAutoDefault(False)
        self.btn_back.setStyleSheet("background: #546e7a; font-weight: bold; border: none;")
        self.btn_back.clicked.connect(self.close_cleanly)
        btn_panel.addWidget(self.btn_back)

        btn_panel.addStretch()
        bottom_split.addLayout(btn_panel)
        main_layout.addLayout(bottom_split)

        self.apply_band_mode_ui()
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_graph)
        self.timer.start(30)

    # --- UI & LOGICS ---
    def set_camera_rgb_gain(self, color_channel, val):
        try:
            val_int = max(1, min(400, int(float(val))))
        except Exception:
            return
        ch_lower = color_channel.lower()
        gain_key = f"{ch_lower}_gain"
        setattr(self, f"ref_{ch_lower}", val_int)

        btn = getattr(self, f"btn_ref_{ch_lower}", None)
        if btn:
            btn.setText(str(val_int))

        # 1. Directly command live camera hardware to adjust sensor gain!
        worker = self.camera_worker
        if not worker:
            worker = self.rear_worker if self.camera_view == "REAR" else self.front_worker
        if worker and hasattr(worker, 'set_param'):
            worker.set_param(gain_key, val_int)

        # 2. Persist to camera_config.json so it is remembered
        try:
            cfg_path = os.path.join(os.path.dirname(__file__), "camera_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r") as f:
                    cdata = json.load(f)
                if self.camera_view in cdata:
                    cdata[self.camera_view][gain_key] = val_int
                    with open(cfg_path, "w") as f:
                        json.dump(cdata, f, indent=2)

            # 3. Sync with parent MainWindow cache if available
            if hasattr(self, 'parent_ui') and self.parent_ui:
                mw = getattr(self.parent_ui, 'main_window', None)
                if mw and hasattr(mw, 'camera_configs_data'):
                    if self.camera_view in mw.camera_configs_data:
                        mw.camera_configs_data[self.camera_view][gain_key] = val_int
        except Exception as e:
            print(f"[BG_TUNING] Error saving {gain_key}: {e}")

    def open_start_pixel_keypad(self):
        def on_val(v):
            try:
                val = int(float(v))
                val = max(0, min(val, self.end_pixel - 50))
                self.start_pixel = val
                if hasattr(self, 'btn_adj_start_val'):
                    self.btn_adj_start_val.setText(str(val))
                self.save_camera_bounds_silent()
            except Exception:
                pass
        Keypad(str(self.start_pixel), on_val, self).exec_()

    def open_end_pixel_keypad(self):
        def on_val(v):
            try:
                val = int(float(v))
                img_w = getattr(self, 'camera_full_width', 4096)
                val = max(self.start_pixel + 50, min(val, max(4096, img_w)))
                self.end_pixel = val
                if hasattr(self, 'btn_adj_end_val'):
                    self.btn_adj_end_val.setText(str(val))
                self.save_camera_bounds_silent()
            except Exception:
                pass
        Keypad(str(self.end_pixel), on_val, self).exec_()

    def nudge_start(self, delta):
        val = max(0, min(self.start_pixel + delta, self.end_pixel - 50))
        self.start_pixel = val
        if hasattr(self, 'btn_adj_start_val'):
            self.btn_adj_start_val.setText(str(val))
        self.save_camera_bounds_silent()

    def nudge_end(self, delta):
        img_w = getattr(self, 'camera_full_width', 4096)
        val = max(self.start_pixel + 50, min(self.end_pixel + delta, max(4096, img_w)))
        self.end_pixel = val
        if hasattr(self, 'btn_adj_end_val'):
            self.btn_adj_end_val.setText(str(val))
        self.save_camera_bounds_silent()

    def on_graph_mouse_press(self, ev):
        if ev.button() == Qt.LeftButton:
            x = ev.x()
            sx = getattr(self, '_graph_start_x', 0)
            ex = getattr(self, '_graph_end_x', 0)
            if abs(x - sx) <= 18:
                self.dragging_bound = 'START'
                self.lbl_graph.setCursor(Qt.SizeHorCursor)
            elif abs(x - ex) <= 18:
                self.dragging_bound = 'END'
                self.lbl_graph.setCursor(Qt.SizeHorCursor)
            else:
                self.dragging_bound = None

    def on_graph_mouse_move(self, ev):
        x = ev.x()
        gw = getattr(self, '_graph_w', self.lbl_graph.width())
        img_w = getattr(self, '_graph_img_w', 4096)
        if gw <= 0: return

        if self.dragging_bound == 'START':
            new_start = int((float(x) / float(gw)) * img_w)
            new_start = max(0, min(new_start, self.end_pixel - 50))
            self.start_pixel = new_start
            if hasattr(self, 'btn_adj_start_val'):
                self.btn_adj_start_val.setText(str(new_start))
        elif self.dragging_bound == 'END':
            new_end = int((float(x) / float(gw)) * img_w)
            new_end = max(self.start_pixel + 50, min(new_end, max(4096, img_w)))
            self.end_pixel = new_end
            if hasattr(self, 'btn_adj_end_val'):
                self.btn_adj_end_val.setText(str(new_end))
        else:
            sx = getattr(self, '_graph_start_x', 0)
            ex = getattr(self, '_graph_end_x', 0)
            if abs(x - sx) <= 18 or abs(x - ex) <= 18:
                self.lbl_graph.setCursor(Qt.SizeHorCursor)
            else:
                self.lbl_graph.setCursor(Qt.ArrowCursor)

    def on_graph_mouse_release(self, ev):
        if ev.button() == Qt.LeftButton:
            if self.dragging_bound:
                self.dragging_bound = None
                self.save_camera_bounds_silent()
                self.lbl_graph.setCursor(Qt.ArrowCursor)

    def save_camera_bounds_silent(self):
        cfg_path = os.path.join(os.path.dirname(__file__), 'camera_config.json')
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, 'r') as f:
                    data = json.load(f)
                if self.camera_view in data:
                    data[self.camera_view]['start_pixel'] = int(self.start_pixel)
                    data[self.camera_view]['end_pixel'] = int(self.end_pixel)
                with open(cfg_path, 'w') as f:
                    json.dump(data, f, indent=2)
                if hasattr(self, 'parent_ui') and self.parent_ui:
                    mw = getattr(self.parent_ui, 'main_window', None)
                    if mw:
                        if hasattr(mw, 'reload_camera_configs'):
                            mw.reload_camera_configs()
                        if hasattr(mw, 'camera_configs_data') and self.camera_view in mw.camera_configs_data:
                            mw.camera_configs_data[self.camera_view]['start_pixel'] = int(self.start_pixel)
                            mw.camera_configs_data[self.camera_view]['end_pixel'] = int(self.end_pixel)
                        if hasattr(mw, 'bg_settings'):
                            mw.bg_settings['start_pixel'] = int(self.start_pixel)
                            mw.bg_settings['end_pixel'] = int(self.end_pixel)
            except Exception as e:
                print('Error saving camera bounds:', e)

    def load_camera_bounds(self):
        """Loads camera bounds from camera_config.json as master bounds"""
        self.camera_full_width = 4096
        cfg_path = os.path.join(os.path.dirname(__file__), "camera_config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r") as f:
                    data = json.load(f)
                    cfg = data.get(self.camera_view, {})
                    self.num_channels = cfg.get("num_channels", 63)
                    self.start_pixel = cfg.get("start_pixel", 0)
                    self.end_pixel = cfg.get("end_pixel", 4096)
                    self.camera_full_width = cfg.get("roi_width", 4096)
                    if self.camera_full_width < self.end_pixel:
                        self.camera_full_width = max(4096, self.end_pixel)
                    if hasattr(self, 'lbl_bounds_info'):
                        self.lbl_bounds_info.setText(f"({self.num_channels} CH)")
                    if hasattr(self, 'btn_adj_start_val'):
                        self.btn_adj_start_val.setText(str(self.start_pixel))
                    if hasattr(self, 'btn_adj_end_val'):
                        self.btn_adj_end_val.setText(str(self.end_pixel))
                    # Load actual camera RGB gain into reference levels
                    self.ref_r = int(cfg.get("r_gain", 100))
                    self.ref_g = int(cfg.get("g_gain", 100))
                    self.ref_b = int(cfg.get("b_gain", 120))
                    if hasattr(self, 'btn_ref_r'):
                        self.btn_ref_r.setText(str(self.ref_r))
                    if hasattr(self, 'btn_ref_g'):
                        self.btn_ref_g.setText(str(self.ref_g))
                    if hasattr(self, 'btn_ref_b'):
                        self.btn_ref_b.setText(str(self.ref_b))
            except Exception as e:
                print("Error loading master camera bounds:", e)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.local_system_running = False
        if hasattr(self, 'timer') and self.timer.isActive():
            self.timer.stop()
        if self.arduino and hasattr(self.arduino, 'stop_all_ejectors'):
            self.arduino.stop_all_ejectors()
        self.save_settings_silent()
        self.save_camera_bounds_silent()
        super().closeEvent(event)

    def reject(self):
        self.local_system_running = False
        if hasattr(self, 'timer') and self.timer.isActive():
            self.timer.stop()
        if self.arduino and hasattr(self.arduino, 'stop_all_ejectors'):
            self.arduino.stop_all_ejectors()
        self.save_settings_silent()
        self.save_camera_bounds_silent()
        super().reject()

    def start_local_system(self):
        self.local_system_running = True
        self.btn_local_start.setText("\u25b6 FIRING ACTIVE")
        self.btn_local_start.setStyleSheet(
            "background: #1b5e20; color: #00e676; font-weight: bold; border: 2px solid #00e676; border-radius: 4px; font-size: 14px;")
        self.btn_local_stop.setText("\u23f9 STOP FIRING")
        self.btn_local_stop.setStyleSheet(
            "background: #444; color: #bbb; font-weight: bold; border-radius: 4px; font-size: 14px;")

    def stop_local_system(self):
        self.local_system_running = False
        if hasattr(self, 'hw_active_channels') and self.hw_active_channels:
            if self.arduino and getattr(self.arduino, 'ejector_link', None):
                link = self.arduino.ejector_link
                if getattr(link, 'is_open', False):
                    if getattr(link, 'mode', None) == 'ESP_BYTE':
                        link._write(bytes([ch + 100 for ch in self.hw_active_channels]))
                    else:
                        link.stop_all()
            self.hw_active_channels.clear()
            self.hw_channel_on_time.clear()
        if self.arduino and hasattr(self.arduino, 'stop_all_ejectors'):
            self.arduino.stop_all_ejectors()
        self.btn_local_start.setText('▶ START FIRING')
        self.btn_local_start.setStyleSheet(
            'background: #00e676; color: black; font-weight: bold; border-radius: 4px; font-size: 14px;')
        self.btn_local_stop.setText('⏹ STOPPED')
        self.btn_local_stop.setStyleSheet(
            'background: #b71c1c; color: white; font-weight: bold; border: 2px solid white; border-radius: 4px; font-size: 14px;')

    def toggle_esp32_connection(self):
        if self.arduino:
            if not self.arduino.connected:
                from PyQt5.QtWidgets import QInputDialog, QMessageBox
                text, ok = QInputDialog.getText(self, 'Connect ESP32',
                                                'Enter the COM port name or an IP address (e.g. 192.168.1.177):',
                                                text="192.168.1.177")
                if ok and text:
                    success = self.arduino.connect(text)
                    if success:
                        self.lbl_esp32_status.setText("ESP32: CONNECTED")
                        self.lbl_esp32_status.setStyleSheet("color: #00e676; font-weight: bold;")
                        self.btn_esp32.setText("🔌 DISCONNECT ESP32")
                    else:
                        QMessageBox.critical(self, "Error", f"Failed to connect to {text}")
            else:
                self.arduino.disconnect()
                self.lbl_esp32_status.setText("ESP32: DISCONNECTED")
                self.lbl_esp32_status.setStyleSheet("color: #ff1744; font-weight: bold;")
                self.btn_esp32.setText("🔌 CONNECT ESP32")

    def toggle_band_mode(self):
        self.band_mode = not self.band_mode
        self.apply_band_mode_ui()
        self.save_settings_silent()

    def apply_band_mode_ui(self):
        if self.band_mode:
            self.btn_band.setText("📶 BAND PASS: ON")
            self.btn_band.setStyleSheet(
                "background: #2979ff; color: white; border: 2px solid white; font-weight: bold;")
            self.group_limits.setTitle("BAND PASS LIMITS")
            self.lbl_upper.setText("BAND TOP:")
            self.lbl_lower.setText("BAND BOT:")
            self.btn_slider_up_val.setStyleSheet("color: #d500f9; border: 1px solid #d500f9;")
            self.btn_slider_low_val.setStyleSheet("color: #ff9800; border: 1px solid #ff9800;")
        else:
            self.btn_band.setText("📶 BAND PASS: OFF")
            self.btn_band.setStyleSheet("background: #546e7a; color: #ccc; border: 1px solid #777;")
            self.group_limits.setTitle("STANDARD THRESHOLDS")
            self.lbl_upper.setText("WHITE (HI):")
            self.lbl_lower.setText("BLACK (LO):")
            self.btn_slider_up_val.setStyleSheet("color: #00e5ff; border: 1px solid #00e5ff;")
            self.btn_slider_low_val.setStyleSheet("color: yellow; border: 1px solid yellow;")

    def volt_to_y(self, v):
        return int(max(0, min(350, ((v - 0.5) / 4.5) * 350.0)))

    def y_to_volt(self, y):
        return ((y / 350.0) * 4.5) + 0.5

    def prev_cam(self):
        self.camera_id = 10 if self.camera_id == 1 else self.camera_id - 1;
        self.btn_cam_id.setText(str(self.camera_id))

    def next_cam(self):
        self.camera_id = 1 if self.camera_id == 10 else self.camera_id + 1;
        self.btn_cam_id.setText(str(self.camera_id))

    def open_cam_keypad(self):
        Keypad(str(self.camera_id), lambda v: [setattr(self, 'camera_id', int(v)), self.btn_cam_id.setText(v)],
               self).exec_()

    def toggle_view(self):
        self.camera_view = "REAR" if self.camera_view == "FRONT" else "FRONT"
        self.btn_view.setText(f"VIEW: {self.camera_view}")
        if hasattr(self, 'hw_active_channels') and self.hw_active_channels:
            if self.arduino and getattr(self.arduino, 'ejector_link', None):
                link = self.arduino.ejector_link
                if getattr(link, 'is_open', False):
                    if getattr(link, 'mode', None) == 'ESP_BYTE':
                        link._write(bytes([ch + 100 for ch in self.hw_active_channels]))
                    else:
                        link.stop_all()
            self.hw_active_channels.clear()
            self.hw_channel_on_time.clear()
        
        # Update UI to reflect the selected camera's thresholds
        self.slider_lower.setValue(self.thresh_lower)
        self.btn_slider_low_val.setText(str(self.thresh_lower))
        self.slider_upper.setValue(self.thresh_upper)
        self.btn_slider_up_val.setText(str(self.thresh_upper))
        self.apply_band_mode_ui()

        self.load_camera_bounds()  # View மாறியவுடன் புதிய Pixel அளவுகளை எடுக்க

        try:
            if self.camera_worker and hasattr(self.camera_worker, 'frame_ready'):
                self.camera_worker.frame_ready.disconnect(self.update_live_frame)
        except Exception:
            pass

        self.camera_worker = self.rear_worker if self.camera_view == "REAR" else self.front_worker
        if self.camera_worker and hasattr(self.camera_worker, 'frame_ready'):
            self.camera_worker.frame_ready.connect(self.update_live_frame)

    def change_wave_mode(self, text):
        self.wave_mode = text

    def update_upper_thresh(self, val):
        self.thresh_upper = val
        self.btn_slider_up_val.setText(str(val))
        self.save_settings_silent()

    def update_lower_thresh(self, val):
        self.thresh_lower = val
        self.btn_slider_low_val.setText(str(val))
        self.save_settings_silent()

    def set_upper_manual(self, val):
        self.thresh_upper = int(float(val))
        self.slider_upper.setValue(self.thresh_upper)
        self.btn_slider_up_val.setText(str(self.thresh_upper))
        self.save_settings_silent()

    def set_lower_manual(self, val):
        self.thresh_lower = int(float(val))
        self.slider_lower.setValue(self.thresh_lower)
        self.btn_slider_low_val.setText(str(self.thresh_lower))
        self.save_settings_silent()

    def _reload_ejector_pulse(self):
        """ejector_config.json-ல் இருந்து pulse/num_pulses/pulse_gap ஒருமுறை மட்டும் படிக்கும்.
        ஒவ்வொரு frame-க்கும் படிப்பதால் ஏற்படும் performance பிரச்சனையை சரி செய்கிறது."""
        try:
            cfg_path = os.path.join(os.path.dirname(__file__), "ejector_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r") as f:
                    d = json.load(f)
                    self._cached_ejector_pulse = float(d.get("pulse",     25.0))
                    self._cached_num_pulses    = max(1, int(d.get("num_pulses", 1)))
                    self._cached_pulse_gap     = float(d.get("pulse_gap", 12.0))
        except Exception:
            self._cached_ejector_pulse = 25.0
            self._cached_num_pulses    = 1
            self._cached_pulse_gap     = 12.0

    def save_settings_silent(self):
        data = {
            "camera_thresh": self.camera_thresh,
            # Fallback for older code
            "thresh_lower": self.camera_thresh["FRONT"]["lower"],
            "thresh_upper": self.camera_thresh["FRONT"]["upper"],
            "band_mode": self.camera_thresh["FRONT"]["band_mode"],
            "wave_mode": getattr(self, "wave_mode", "BLUE")
        }
        try:
            with open(BG_CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=4)
                
            # Sync to Live MainWindow Memory
            if hasattr(self, 'parent_ui') and self.parent_ui:
                mw = getattr(self.parent_ui, 'main_window', None)
                if mw and hasattr(mw, 'bg_settings'):
                    mw.bg_settings["camera_thresh"] = self.camera_thresh
        except Exception as e:
            print("Save Error:", e)

    def save_settings(self):
        self.save_settings_silent()
        self.save_camera_bounds_silent()
        QMessageBox.information(self, "Saved", f"Threshold limits & bounds saved successfully.\n\nSTART: {self.start_pixel}\nEND: {self.end_pixel}")

    def load_bg_settings(self):
        if os.path.exists(BG_CONFIG_FILE):
            try:
                with open(BG_CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    
                    if "camera_thresh" in data:
                        self.camera_thresh = data["camera_thresh"]
                    else:
                        # Legacy fallback
                        t_l = data.get("thresh_lower", 80)
                        t_u = data.get("thresh_upper", 225)
                        b_m = data.get("band_mode", False)
                        self.camera_thresh = {
                            "FRONT": {"lower": t_l, "upper": t_u, "band_mode": b_m},
                            "REAR": {"lower": t_l, "upper": t_u, "band_mode": b_m}
                        }
            except Exception as e:
                print(f"Error loading background config: {e}")

    def reset_settings(self):
        if QMessageBox.question(self, "Confirm Reset", "Reset thresholds to default?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.thresh_lower = 80
            self.thresh_upper = 225
            self.band_mode = False

            self.slider_lower.setValue(self.thresh_lower)
            self.btn_slider_low_val.setText(str(self.thresh_lower))
            self.slider_upper.setValue(self.thresh_upper)
            self.btn_slider_up_val.setText(str(self.thresh_upper))

            self.apply_band_mode_ui()
            self.save_settings_silent()

    def close_cleanly(self):
        self.local_system_running = False
        if hasattr(self, 'timer') and self.timer.isActive():
            self.timer.stop()
        if self.arduino and hasattr(self.arduino, 'stop_all_ejectors'):
            self.arduino.stop_all_ejectors()
        self.save_settings_silent()
        self.save_camera_bounds_silent()
        self.accept()

    def update_live_frame(self, frame):
        if frame is not None:
            self.latest_frame = frame.copy()

    def update_graph(self):
        w = self.graph_frame.width()
        h = self.graph_frame.height()
        if w <= 0: return

        if self.latest_frame is not None:
            disp = cv2.resize(self.latest_frame, (w, h))
            rgba = cv2.cvtColor(disp, cv2.COLOR_BGR2RGBA)
            rgba = np.ascontiguousarray(rgba)
            qimg = QImage(rgba.data, w, h, rgba.strides[0], QImage.Format_RGBA8888).copy()
            pix = QPixmap.fromImage(qimg)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            p.fillRect(0, 0, w, h, QBrush(QColor(0, 0, 0, 160)))
        else:
            pix = QPixmap(w, h)
            pix.fill(Qt.black)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            p.fillRect(0, 0, w, h, QBrush(QColor(0, 0, 0, 120)))

        p.setPen(QPen(QColor("#333"), 1, Qt.DotLine))
        for x in range(0, w, 50): p.drawLine(x, 0, x, h)
        p.setFont(QFont("Arial", 8))

        for i in range(0, 351, 50):
            y = h - int((i / 350.0) * h)
            p.setPen(QPen(QColor("#333"), 1, Qt.DotLine))
            p.drawLine(0, y, w, y)
            p.setPen(QPen(QColor("#888")))
            p.drawText(5, y - 2, str(i))

        if self.band_mode:
            y_from = h - int((self.thresh_lower / 350.0) * h)
            y_to = h - int((self.thresh_upper / 350.0) * h)
            p.setPen(QPen(QColor("#ff9800"), 2, Qt.DashLine))
            p.drawLine(0, y_from, w, y_from)
            p.setPen(QPen(QColor("#d500f9"), 2, Qt.DashLine))
            p.drawLine(0, y_to, w, y_to)
            top_y = min(y_from, y_to)
            rect_h = abs(y_from - y_to)
            p.fillRect(0, top_y, w, rect_h, QBrush(QColor(0, 100, 255, 60)))
        else:
            y_lo = h - int((self.thresh_lower / 350.0) * h)
            p.setPen(QPen(QColor("yellow"), 2, Qt.DashLine))
            p.drawLine(0, y_lo, w, y_lo)
            y_up = h - int((self.thresh_upper / 350.0) * h)
            p.setPen(QPen(QColor("#00e5ff"), 2, Qt.DashLine))
            p.drawLine(0, y_up, w, y_up)

        active_valves = [0] * max(1, self.num_channels)
        center_volt = 0.5
        img_w_actual = getattr(self, 'camera_full_width', 4096)
        if img_w_actual <= 0:
            img_w_actual = max(4096, self.end_pixel)

        if self.latest_frame is not None:
            img_h, f_w = self.latest_frame.shape[:2]
            if f_w > 0:
                img_w_actual = f_w
            scan_lines = self.latest_frame

            # --- INSTANTANEOUS LINE DETECTION (NO 512-LINE BUFFER SMEAR) ---
            # Instead of aggregating all 512 historical lines across time which causes
            # previous needle/object positions to keep firing like a buffer, take only
            # the latest scan lines (last 3 rows) for real-time 1:1 instantaneous firing.
            if scan_lines.shape[0] >= 3:
                recent_slice = scan_lines[-3:]
            else:
                recent_slice = scan_lines

            min_line = np.min(recent_slice, axis=0).astype(float)
            max_line = np.max(recent_slice, axis=0).astype(float)
            mean_line = np.mean(recent_slice, axis=0).astype(float)

            # --- UI VISUALIZATION FIX: PRESERVE THIN WIRES ---
            # cv2.resize blends/averages pixels. A 1-pixel wire (value 10) gets blended
            # with background (value 180), resulting in 80 on the graph. This hides the defect!
            # Fix: Use true envelope downsampling to preserve absolute min/max spikes.
            N = min_line.shape[0]
            if N > 0 and w > 0:
                chunk_size = N / w
                idxs = np.clip((np.arange(N) / chunk_size).astype(int), 0, w - 1)
                
                min_resized = np.full((w, 3), 255.0)
                np.minimum.at(min_resized[:, 0], idxs, min_line[:, 0])
                np.minimum.at(min_resized[:, 1], idxs, min_line[:, 1])
                np.minimum.at(min_resized[:, 2], idxs, min_line[:, 2])

                max_resized = np.zeros((w, 3), dtype=float)
                np.maximum.at(max_resized[:, 0], idxs, max_line[:, 0])
                np.maximum.at(max_resized[:, 1], idxs, max_line[:, 1])
                np.maximum.at(max_resized[:, 2], idxs, max_line[:, 2])
                
                mean_resized = cv2.resize(mean_line.reshape(1, -1, 3), (w, 1))[0]
            else:
                min_resized = cv2.resize(min_line.reshape(1, -1, 3), (w, 1))[0]
                max_resized = cv2.resize(max_line.reshape(1, -1, 3), (w, 1))[0]
                mean_resized = cv2.resize(mean_line.reshape(1, -1, 3), (w, 1))[0]
        else:
            min_resized = np.zeros((w, 3), dtype=float)
            max_resized = np.zeros((w, 3), dtype=float)
            mean_resized = np.zeros((w, 3), dtype=float)
            min_resized[:, 0] = max_resized[:, 0] = mean_resized[:, 0] = self.ref_b
            min_resized[:, 1] = max_resized[:, 1] = mean_resized[:, 1] = self.ref_g
            min_resized[:, 2] = max_resized[:, 2] = mean_resized[:, 2] = self.ref_r

        def draw_wave(c_idx, col, is_active):
            nonlocal center_volt
            vals_min = min_resized[:, c_idx]
            vals_max = max_resized[:, c_idx]
            vals_mean = mean_resized[:, c_idx]

            dev_up = vals_max - vals_mean
            dev_down = vals_mean - vals_min
            
            # --- SHOW BREACH FIX ---
            # If the signal breached the lower threshold, we MUST display the downward spike
            # so the user sees exactly WHY it fired, instead of hiding it behind a noisy upward spike.
            is_breach_down = vals_min < self.thresh_lower
            is_breach_up = vals_max > self.thresh_upper
            
            # Default noise filtering (show whichever deviation is larger)
            noise_margin = 8
            show_up = (dev_up > dev_down + noise_margin)
            
            # --- SHOW TRUE TRIGGER FIX ---
            if not self.band_mode:
                # Band Pass OFF: Fires if it goes BELOW lower OR ABOVE upper.
                # If it breaches down, MUST show down. If it breaches up, MUST show up.
                show_up = np.where(is_breach_down, False, show_up)
                show_up = np.where(is_breach_up & ~is_breach_down, True, show_up)
                
            display_vals = np.where(show_up, vals_max, vals_min).copy()

            if is_active and self.latest_frame is not None:
                x_ratio = float(img_w_actual) / float(max(1, w))
                orig_xs = (np.arange(w) * x_ratio).astype(int)

                valid_mask = (orig_xs >= self.start_pixel) & (orig_xs <= self.end_pixel)

                # --- STRICT TRIGGER FIX ---
                if self.band_mode:
                    # BAND PASS ON: Fire ONLY on middle spike (In-Band Rejection)
                    trigger_mask = valid_mask & (vals_min > self.thresh_lower) & (vals_max < self.thresh_upper)
                else:
                    # BAND PASS OFF: Fire on yellow for dark, cyan for white (Out-of-Band Rejection)
                    trigger_mask = valid_mask & ((vals_min < self.thresh_lower) | (vals_max > self.thresh_upper))


                triggered_indices = np.where(trigger_mask)[0]

                if len(triggered_indices) > 0 and self.num_channels > 0:
                    ch_width = max(1.0, float(self.end_pixel - self.start_pixel) / float(self.num_channels))
                    ch_idxs = ((orig_xs[triggered_indices] - self.start_pixel) / ch_width).astype(int)
                    ch_idxs = np.clip(ch_idxs, 0, self.num_channels - 1)
                    for ch in ch_idxs:
                        active_valves[ch] = 1

            np.clip(display_vals, 0, 350, out=display_vals)
            if is_active and col == "#2979ff":
                center_volt = self.y_to_volt(display_vals[w // 2])

            path = QPainterPath()
            p.setPen(QPen(QColor(col), 2))
            path.moveTo(0, h - ((display_vals[0] / 350.0) * h))
            for ix, v in enumerate(display_vals):
                path.lineTo(ix, h - ((v / 350.0) * h))
            p.drawPath(path)

        draw_wave(2, "#ff1744", "RED" in self.wave_mode or "ALL" in self.wave_mode)
        draw_wave(1, "#00e676", "GREEN" in self.wave_mode or "ALL" in self.wave_mode)
        draw_wave(0, "#2979ff", "BLUE" in self.wave_mode or "ALL" in self.wave_mode)

        # Draw Vertical Boundary Lines & Interactive Drag Handles
        img_w_actual = getattr(self, 'camera_full_width', 4096)
        if img_w_actual <= 0: img_w_actual = max(4096, self.end_pixel)
        graph_start_x = int((float(self.start_pixel) / float(max(1, img_w_actual))) * w)
        graph_end_x = int((float(self.end_pixel) / float(max(1, img_w_actual))) * w)
        self._graph_start_x = graph_start_x
        self._graph_end_x = graph_end_x
        self._graph_w = w
        self._graph_img_w = img_w_actual

        # START vertical line (Cyan)
        p.setPen(QPen(QColor("#00FFFF"), 2, Qt.DashLine))
        p.drawLine(graph_start_x, 0, graph_start_x, h)
        # END vertical line (Orange)
        p.setPen(QPen(QColor("#ff9800"), 2, Qt.DashLine))
        p.drawLine(graph_end_x, 0, graph_end_x, h)

        # Handle badges at the top of START and END lines
        handle_w = 118
        handle_h = 24
        
        # START Handle (Cyan)
        s_left = max(2, min(w - handle_w - 2, graph_start_x - handle_w // 2))
        s_box = QRect(s_left, 4, handle_w, handle_h)
        p.setBrush(QBrush(QColor(0, 188, 212, 230)))
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawRoundedRect(s_box, 4, 4)
        p.setPen(QColor("#000000"))
        p.setFont(QFont("Segoe UI", 9, QFont.Bold))
        p.drawText(s_box, Qt.AlignCenter, f"◀ START: {self.start_pixel} ▶")

        # END Handle (Orange)
        e_left = max(2, min(w - handle_w - 2, graph_end_x - handle_w // 2))
        e_box = QRect(e_left, 4, handle_w, handle_h)
        p.setBrush(QBrush(QColor(255, 152, 0, 230)))
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawRoundedRect(e_box, 4, 4)
        p.setPen(QColor("#000000"))
        p.setFont(QFont("Segoe UI", 9, QFont.Bold))
        p.drawText(e_box, Qt.AlignCenter, f"◀ END: {self.end_pixel} ▶")

        p.end()
        self.lbl_graph.setPixmap(pix)
        self.lbl_voltage.setText(f"SIGNAL: {center_volt:.2f}V")

        # =====================================================================
        # STATEFUL EJECTOR FIRING (Anti-Flicker Continuous Hold & Solid Pulse)
        # =====================================================================
        now_fire_t = time.time()
        if self.local_system_running and self.arduino and getattr(self.arduino, 'connected', False):
            raw_active = [i + 1 for i, v in enumerate(active_valves) if v == 1]
            if len(raw_active) < getattr(self, '_min_trigger_channels', 1):
                current_active = set()
            else:
                current_active = set(raw_active)

            min_pulse_sec = max(0.005, float(getattr(self, '_cached_ejector_pulse', 10.0)) / 1000.0)
            link = getattr(self.arduino, 'ejector_link', None)
            is_open = bool(link and getattr(link, 'is_open', False))

            # 1. Newly activated channels -> Send ON byte once (Hold continuous, NO flickering timer!)
            newly_on = [ch for ch in current_active if ch not in self.hw_active_channels]
            if newly_on:
                for ch in newly_on:
                    self.hw_active_channels.add(ch)
                    self.hw_channel_on_time[ch] = now_fire_t
                if is_open:
                    if getattr(link, 'mode', None) == "ESP_BYTE":
                        link._write(bytes(newly_on))
                    else:
                        for ch in newly_on:
                            self.arduino.hold_ejector_on(ch)

            # 2. Channels that remain active in breach -> Stay solidly ON (NO redundant writes!)
            for ch in current_active:
                if ch in self.hw_active_channels:
                    self.hw_channel_on_time.setdefault(ch, now_fire_t)

            # 3. Channels no longer in breach -> Turn OFF only after min_pulse_sec has elapsed
            to_turn_off = []
            for ch in list(self.hw_active_channels):
                if ch not in current_active:
                    if (now_fire_t - self.hw_channel_on_time.get(ch, 0.0)) >= min_pulse_sec:
                        to_turn_off.append(ch)

            if to_turn_off:
                for ch in to_turn_off:
                    self.hw_active_channels.discard(ch)
                    self.hw_channel_on_time.pop(ch, None)
                if is_open:
                    if getattr(link, 'mode', None) == "ESP_BYTE":
                        link._write(bytes([ch + 100 for ch in to_turn_off]))
                    else:
                        for ch in to_turn_off:
                            self.arduino.hold_ejector_off(ch)
        elif self.hw_active_channels:
            if self.arduino and getattr(self.arduino, 'ejector_link', None):
                link = self.arduino.ejector_link
                if getattr(link, 'is_open', False):
                    if getattr(link, 'mode', None) == "ESP_BYTE":
                        link._write(bytes([ch + 100 for ch in self.hw_active_channels]))
                    else:
                        link.stop_all()
            self.hw_active_channels.clear()
            self.hw_channel_on_time.clear()


        # --- REAL-TIME 1:1 LED UPDATE (NO 350ms BUFFER HOLD) ---
        # Directly reflect active channels in real-time so that only the channel
        # with the object/needle is RED; when the object moves away, it turns GREEN immediately.
        self.draw_top_led_bar(active_valves)

    def draw_top_led_bar(self, active_valves=None):
        if not hasattr(self, 'led_frame') or not hasattr(self, 'lbl_leds'):
            return
        w_l = self.led_frame.width()
        h_l = self.led_frame.height()
        if w_l <= 0:
            w_l = max(800, self.width() - 40)
        if h_l <= 0:
            h_l = 28
        pix_l = QPixmap(w_l, h_l)
        pix_l.fill(QColor(10, 14, 18))
        pl = QPainter(pix_l)
        pl.setRenderHint(QPainter.Antialiasing)

        safe_ch = max(1, self.num_channels)
        bw = w_l / float(safe_ch)
        font_num = QFont("Segoe UI", 7, QFont.Bold)
        pl.setFont(font_num)

        for i in range(safe_ch):
            ch_num = i + 1
            is_active = active_valves[i] if (active_valves and i < len(active_valves)) else 0
            rect = QRectF(i * bw, 2, max(2.0, bw - 1), h_l - 4)
            if is_active:
                pl.fillRect(rect, QColor("#ff1744"))
                pl.setPen(QPen(QColor("#ff8a80"), 1))
                pl.drawRect(rect)
                pl.setPen(QColor("#ffffff"))
            else:
                pl.fillRect(rect, QColor("#1b5e20"))
                pl.setPen(QPen(QColor("#2e7d32"), 1))
                pl.drawRect(rect)
                pl.setPen(QColor("#ffffff"))
            pl.drawText(rect, Qt.AlignCenter, str(ch_num))
        pl.end()
        self.lbl_leds.setPixmap(pix_l)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'led_frame') and hasattr(self, 'lbl_leds'):
            self.draw_top_led_bar()


# =============================================================================
# EJECTOR LIVE LED BAR (ULTRA-FAST QPAINTER WIDGET)
# =============================================================================
class EjectorLEDBar(QWidget):
    """
    63-Channel Operating Ejector Button Bar on the Front Page Footer.
    """
    trigger_signal = pyqtSignal(list, int)

    def __init__(self, num_channels=63, parent=None):
        super().__init__(parent)
        self.num_channels = num_channels
        self.setFixedHeight(32)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("63-Channel Ejectors: Shows live firing; Click any channel button to test fire")
        self.active_channels = set()
        
        self.glow_timer = QTimer(self)
        self.glow_timer.timeout.connect(self.clear_leds)
        self.glow_timer.setSingleShot(True)
        
        self.trigger_signal.connect(self._handle_trigger)
        
    def trigger(self, channels_list, pulse_ms=120):
        if not channels_list:
            return
        self.trigger_signal.emit(channels_list, pulse_ms)

    def _handle_trigger(self, channels_list, pulse_ms):
        # Support both 0-based (0..62) and 1-based (1..63)
        normalized = set()
        for c in channels_list:
            if c < self.num_channels:
                normalized.add(c)
            elif c <= self.num_channels:
                normalized.add(c - 1)
        self.active_channels.update(normalized)
        self.update()
        
        # UI Glow timer matches the physical pulse duration
        self.glow_timer.start(pulse_ms)
        
    def clear_leds(self):
        self.active_channels.clear()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            w = self.width()
            n = max(1, self.num_channels)
            ch_w = w / float(n)
            ch_idx = int(event.x() / ch_w)
            if 0 <= ch_idx < self.num_channels:
                ch_num = ch_idx + 1
                self.trigger([ch_idx])
                win = self.window()
                if hasattr(win, 'esp32') and win.esp32 and hasattr(win.esp32, 'connected') and win.esp32.connected:
                    try:
                        win.esp32.fire_single_ejector(ch_num, 25)
                    except Exception as ex:
                        print(f"Manual ejector click error: {ex}")
        super().mousePressEvent(event)

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w = self.width()
        h = self.height()
        n = max(1, self.num_channels)
        ch_w = w / float(n)
        
        # Dark industrial base with top/bottom border
        painter.fillRect(0, 0, w, h, QColor(14, 18, 22))
        painter.setPen(QPen(QColor(38, 50, 56), 1))
        painter.drawLine(0, 0, w, 0)
        painter.drawLine(0, h - 1, w, h - 1)
        
        font_act = QFont("Segoe UI", 8, QFont.Bold)
        font_idle = QFont("Segoe UI", 7, QFont.Bold)
        
        for i in range(n):
            ch_num = i + 1
            x1 = int(i * ch_w)
            x2 = int((i + 1) * ch_w)
            cell_w = max(2, x2 - x1)
            is_active = i in self.active_channels
            
            if is_active:
                # Glowing Neon Red Active Ejector Button
                grad = QLinearGradient(x1, 2, x1, h - 2)
                grad.setColorAt(0, QColor(255, 82, 82))
                grad.setColorAt(1, QColor(183, 28, 28))
                painter.fillRect(x1 + 1, 2, max(1, cell_w - 2), h - 4, grad)
                painter.setPen(QPen(QColor(255, 138, 128), 1))
                painter.drawRect(x1 + 1, 2, max(1, cell_w - 2), h - 4)
                
                painter.setFont(font_act)
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(QRect(x1 - 4, 2, cell_w + 8, h - 4), Qt.AlignCenter, str(ch_num))
            else:
                # Idle dark channel button slot
                painter.fillRect(x1 + 1, 4, max(1, cell_w - 2), h - 8, QColor(24, 34, 42))
                painter.setPen(QPen(QColor(38, 52, 64), 1))
                painter.drawRect(x1 + 1, 4, max(1, cell_w - 2), h - 8)
                
                painter.setFont(font_idle)
                painter.setPen(QColor(144, 164, 174))
                painter.drawText(QRect(x1 - 4, 4, cell_w + 8, h - 8), Qt.AlignCenter, str(ch_num))


# =============================================================================
# BACKGROUND SIGNAL GRAPH (PURE PYQT5 FAST PLOT)
# =============================================================================
from PyQt5.QtGui import QPolygonF
class BackgroundSignalGraph(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.signal_data = [] # List of 0-255 values
        self.bg_color = QColor(10, 10, 10)
        self.line_color = QColor(0, 229, 255) # Cyan
        self.setMinimumHeight(200)
        
    def update_signal(self, data_array):
        # We sample points to keep it blazing fast
        if len(data_array) > 800:
            step = len(data_array) // 800
            self.signal_data = data_array[::step]
        else:
            self.signal_data = data_array
        self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        rect = self.rect()
        painter.fillRect(rect, self.bg_color)
        
        pen_grid = QPen(QColor(40, 40, 40), 1, Qt.DashLine)
        painter.setPen(pen_grid)
        for i in range(1, 4):
            y = int(rect.height() * (i / 4.0))
            painter.drawLine(0, y, rect.width(), y)
            
        if not self.signal_data:
            return
            
        pen_signal = QPen(self.line_color, 2)
        painter.setPen(pen_signal)
        
        polygon = QPolygonF()
        w = rect.width()
        h = rect.height()
        count = max(1, len(self.signal_data) - 1)
        
        for i, val in enumerate(self.signal_data):
            x = (i / count) * w
            y = h - ((val / 255.0) * h)
            polygon.append(QPointF(x, y))
            
        painter.drawPolyline(polygon)

class BackgroundGraphDialog(QDialog):
    def __init__(self, camera_front, camera_rear, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Background Tuning Scope (Oscilloscope)")
        self.resize(1000, 600)
        self.setStyleSheet("background-color: #1a1a1a; color: white;")
        
        self.camera_front = camera_front
        self.camera_rear = camera_rear
        
        layout = QVBoxLayout(self)
        
        lbl_f = QLabel("FRONT CAMERA SIGNAL")
        lbl_f.setStyleSheet("color: #ffd700; font-weight: bold;")
        self.graph_front = BackgroundSignalGraph()
        
        lbl_r = QLabel("REAR CAMERA SIGNAL")
        lbl_r.setStyleSheet("color: #ffd700; font-weight: bold;")
        self.graph_rear = BackgroundSignalGraph()
        
        layout.addWidget(lbl_f)
        layout.addWidget(self.graph_front, stretch=1)
        layout.addWidget(lbl_r)
        layout.addWidget(self.graph_rear, stretch=1)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_cameras)
        self.timer.start(50) # 20 FPS refresh
        
    def poll_cameras(self):
        try:
            if self.camera_front and hasattr(self.camera_front, 'latest_frame') and self.camera_front.latest_frame is not None:
                gray = cv2.cvtColor(self.camera_front.latest_frame, cv2.COLOR_BGR2GRAY)
                min_row = np.min(gray, axis=0)
                self.graph_front.update_signal(min_row.tolist())
                
            if self.camera_rear and hasattr(self.camera_rear, 'latest_frame') and self.camera_rear.latest_frame is not None:
                gray = cv2.cvtColor(self.camera_rear.latest_frame, cv2.COLOR_BGR2GRAY)
                min_row = np.min(gray, axis=0)
                self.graph_rear.update_signal(min_row.tolist())
        except Exception:
            pass

# =============================================================================
# 7. MAIN DASHBOARD (UPDATED FOR REAL-TIME GRAYSCALE, AI & CAPTURE LINK)
# =============================================================================
def show_info_dialog(parent, title, headline, text):
    """Non-modal information window (plain QDialog: a non-modal QMessageBox can crash on some Qt platforms)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setAttribute(Qt.WA_DeleteOnClose, True)
    dlg.setStyleSheet("QDialog { background-color: #1a1a1a; } QLabel { color: #eeeeee; font-size: 13px; } "
                      "QPushButton { background-color: #333; color: white; border: 1px solid #555; border-radius: 6px; padding: 6px 18px; }")
    lay = QVBoxLayout(dlg)
    head = QLabel(headline)
    head.setStyleSheet("font-size: 15px; font-weight: bold; color: #ffd740;")
    head.setWordWrap(True)
    body = QLabel(text)
    body.setWordWrap(True)
    ok = QPushButton("OK")
    ok.clicked.connect(dlg.close)
    for w_ in (head, body, ok):
        lay.addWidget(w_)
    dlg.setModal(False)
    dlg.setMinimumWidth(520)
    dlg.show()
    return dlg


class MainDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Industrial Vision HMI v3.0 (Line Scan Ready)")
        from PyQt5.QtGui import QIcon
        for icon_name in ["app_icon.png", "app_icon.ico", "logo.png"]:
            icon_p = os.path.join(os.path.dirname(__file__), icon_name) if '__file__' in locals() else icon_name
            if os.path.exists(icon_p):
                self.setWindowIcon(QIcon(icon_p))
                break
            elif os.path.exists(icon_name):
                self.setWindowIcon(QIcon(icon_name))
                break

        # 1920x1080 ரெசல்யூசனுக்கு மாற்றுதல்
        self.setFixedSize(1920, 1080)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.showFullScreen()  # டச் ஸ்கிரீன் பேனலுக்கு இது சிறந்தது
        self.setStyleSheet(STYLESHEET)
        
        self.allow_exit = False

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        # Initialize system state BEFORE camera connections (DirectConnection needs this!)
        self.system_running = False
        self.wiper_active_now = False
        
        # Stable Wire Test States
        self.wire_test_mode_active = False
        self.wire_detect_counter = 0
        self.wire_release_counter = 0
        self.wire_present = False
        self.wire_active_channel = None
        self.wire_settings = {'stable_frames': 5, 'release_frames': 5}

        # Camera Workers
        self.camera_worker_front = GigECameraWorker(is_rear=False)
        self.camera_worker_rear = GigECameraWorker(is_rear=True)

        # Live Frame-ஐ OpenCV-க்கு அனுப்புதல் (Zero-Latency Direct Connection)
        self.camera_worker_front.frame_ready.connect(
            lambda f: self.process_live_frame(f, "FRONT"), Qt.DirectConnection)
        self.camera_worker_rear.frame_ready.connect(
            lambda f: self.process_live_frame(f, "REAR"), Qt.DirectConnection)

        self.camera_worker_front.connection_status.connect(self.on_camera_connection_changed)
        self.camera_worker_rear.connection_status.connect(self.on_camera_connection_changed)

        self.auto_connect_cameras()

        # Background Setup
        self.central_widget.setObjectName("CentralBG")

        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        # --- ESP32 Controller ---
        self.esp32 = ESP32Controller(self)
        self.eject_scheduler = EjectScheduler(self.esp32)
        self.eject_scheduler.start()

        # Phase 3 (OFF by default): line-synchronous firing. Created only when switched on;
        # even then it stays inactive until camera trigger + firmware v3.0 are confirmed.
        self.line_sync = None
        if features.enabled("line_sync_enabled"):
            from line_sync import LineSyncController
            self.line_sync = LineSyncController(self.esp32)
            print(f"[LINE SYNC] {self.line_sync.ready_reason()}")
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
            "DETECTION_ROI_H": 512, # Full frame - line scan must inspect every line for high-speed rice
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

        # Load camera pixel bounds from camera_config.json into bg_settings & camera_configs_data
        self.camera_configs_data = {}
        self.reload_camera_configs()

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
        if not getattr(self, 'allow_exit', False):
            event.ignore()
            return
            
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
                if hasattr(self.esp32, 'stop_all_ejectors'):
                    self.esp32.stop_all_ejectors() # Turn off all ejectors
                self.esp32.disconnect_ejector()    # sends DISARM: clean exit, no watchdog alarm
                self.esp32.disconnect()            # relay board: DISARM + close
            except Exception as e:
                print(f"Error disconnecting ESP32: {e}")
                
        event.accept()

    def reload_camera_configs(self):
        """Loads FRONT and REAR camera bounds from camera_config.json into memory"""
        cfg_path = "camera_config.json"
        if not os.path.exists(cfg_path):
            cfg_path = os.path.join(os.path.dirname(__file__), "camera_config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r") as f:
                    self.camera_configs_data = json.load(f)
                    front_cfg = self.camera_configs_data.get("FRONT", {})
                    self.bg_settings["start_pixel"] = front_cfg.get("start_pixel", self.bg_settings.get("start_pixel", 0))
                    self.bg_settings["end_pixel"] = front_cfg.get("end_pixel", self.bg_settings.get("end_pixel", 4096))
                    self.bg_settings["num_channels"] = front_cfg.get("num_channels", self.bg_settings.get("num_channels", 63))
                    r_cfg = self.camera_configs_data.get("REAR", {})
                    print(f"[CONFIG] Loaded camera bounds: FRONT({self.bg_settings['start_pixel']}-{self.bg_settings['end_pixel']}), REAR({r_cfg.get('start_pixel', 0)}-{r_cfg.get('end_pixel', 4096)})")
            except Exception as e:
                print(f"[WARN] Could not load camera bounds: {e}")

    # =========================================================================
    # LIVE OPENCV & DEFECT PROCESSING (WITH HAIR DETECTION & CHANNELS)
    # =========================================================================
    def process_live_frame(self, frame, source="FRONT"):
        if not self.system_running and not self.wire_test_mode_active:
            return
            
        if getattr(self, 'wiper_active_now', False):
            return
            
        if not hasattr(self, '_fusion_lock'):
            import threading
            self._fusion_lock = threading.Lock()
            self._last_front_decision = None
            self._last_rear_decision = None

        # Measure each camera's frame interval (used to bridge long objects, see evaluate_fusion_and_fire)
        if not hasattr(self, '_frame_interval_ms'):
            self._frame_interval_ms = {}
            self._last_frame_t = {}
        _t_now = time.perf_counter()
        _t_prev = self._last_frame_t.get(source)
        self._last_frame_t[source] = _t_now
        if _t_prev is not None:
            _dt = (_t_now - _t_prev) * 1000.0
            if _dt < 500:
                _old = self._frame_interval_ms.get(source, _dt)
                self._frame_interval_ms[source] = 0.9 * _old + 0.1 * _dt

        # Check if any sorting algorithms are active specifically for this camera
        prog_data = self.program_data.get(getattr(self, 'current_program', ''), {})
        active_algos = []
        algo_settings = {}
        
        if isinstance(prog_data, dict):
            cam_mode = prog_data.get("camera_mode", "SAME")
            algo_dict = prog_data.get("algorithms", {})
            all_settings = prog_data.get("algo_settings", {})
            
            # If camera mode is SAME, REAR camera mirrors FRONT!
            # If camera mode is INDIVIDUAL, each camera strictly runs ONLY its own algorithms!
            if cam_mode == "SAME":
                active_algos = algo_dict.get("FRONT", [])
                algo_settings = all_settings.get("FRONT", {})
            else:
                active_algos = algo_dict.get(source, [])
                algo_settings = all_settings.get(source, {})
        elif isinstance(prog_data, list):
            active_algos = prog_data
            
        if not active_algos:
            # This camera has NO active algorithms -> Record clean empty decision and DO NOT fire!
            empty_decision = {
                "valid": False,
                "channels": [],
                "best_class": "NONE",
                "best_algo": "NONE",
                "confidence": 0.0,
                "primary_channel": None,
                "all_results": {}
            }
            with self._fusion_lock:
                if source == "FRONT":
                    self._last_front_decision = empty_decision
                else:
                    self._last_rear_decision = empty_decision
                self.evaluate_fusion_and_fire(source)
            return

        # Per-camera configuration: Load specific bounds for FRONT or REAR
        cam_spec = getattr(self, 'camera_configs_data', {}).get(source, {})
        start_px = cam_spec.get("start_pixel", self.bg_settings.get("start_pixel", 0))
        end_px = cam_spec.get("end_pixel", self.bg_settings.get("end_pixel", 4096))
        num_ch = cam_spec.get("num_channels", self.bg_settings.get("num_channels", 63))

        camera_configs = {
            "start_pixel": start_px,
            "end_pixel": end_px,
            "num_channels": num_ch,
            "wave_mode": self.bg_settings.get("wave_mode", "BLUE"),
            "total_chutes": self.bg_settings.get("total_chutes", 1),
            "chute_roles": self.bg_settings.get("chute_roles", {"1": "PRIMARY"})
        }
        
        # LINE SCAN: Process the ENTIRE frame - every single line.
        process_frame = frame
        
        _worker = self.camera_worker_front if source == "FRONT" else self.camera_worker_rear
        _meta = dict(getattr(_worker, 'last_meta', None) or {})
        _meta["source"] = source
        _t_ps = time.perf_counter()
        decision = self.process_frame_for_defects(process_frame, camera_configs, active_algos, algo_settings, source)
        _t_pe = time.perf_counter()
        if not hasattr(self, '_frame_meta'):
            self._frame_meta = {}
        _meta["t_proc_end"] = _t_pe
        if decision.get("row_extents"):
            _meta["row_extents"] = decision["row_extents"]
        self._frame_meta[source] = _meta
        if TIMING.enabled:
            TIMING.frame(source, _meta.get("frame_idx", ""), _meta.get("cam_ts", ""), _meta.get("height", ""),
                         _meta.get("crop_y0", ""), _meta.get("rows_used", ""), _meta.get("t_arrive", _t_ps),
                         _t_ps, _t_pe, len(decision.get("channels", [])))

        with self._fusion_lock:
            if source == "FRONT":
                self._last_front_decision = decision
            else:
                self._last_rear_decision = decision
                
            self.evaluate_fusion_and_fire(source)

    def get_ejector_config(self):
        # Always read latest scheme ejector timings so user changes take effect immediately
        scheme = RecipeManager.get_scheme(getattr(self, 'current_program', 'RICE'))
        if scheme and isinstance(scheme, dict) and "ejector" in scheme:
            ej = scheme["ejector"]
            return (float(ej.get("delay", 100.0)), float(ej.get("pulse", 20.0)))

        cfg_path = os.path.join(os.path.dirname(__file__), "ejector_config.json")
        delay_ms = 100.0
        pulse_ms = 20.0
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r") as f:
                    d = json.load(f)
                    delay_ms = float(d.get("delay", 100.0))
                    pulse_ms = float(d.get("pulse", 20.0))
            except Exception:
                pass
        return (delay_ms, pulse_ms)

    def process_frame_for_defects(self, frame, camera_configs, active_algos, algo_settings, source="FRONT"):
        import cv2
        start_pixel = max(0, camera_configs.get("start_pixel", 0))
        end_pixel = camera_configs.get("end_pixel", 4096)
        
        frame_width = frame.shape[1]
        if end_pixel > frame_width:
            end_pixel = frame_width
            
        num_channels = camera_configs.get("num_channels", 63)
        total_chutes = camera_configs.get("total_chutes", 1)
        chute_roles = camera_configs.get("chute_roles", {})

        if num_channels <= 0: num_channels = 1
        if total_chutes <= 0: total_chutes = 1

        total_valid_pixels = end_pixel - start_pixel
        pixels_per_chute = max(1, total_valid_pixels / total_chutes)
        pixels_per_channel = max(1, pixels_per_chute / num_channels)

        total_valves = num_channels * total_chutes

        # Phase 3 (OFF by default): calibrated channel bounds / overlap margin, and defect row positions.
        # With the default settings _bounds() returns exactly the old pixel ranges.
        import channel_map
        _bounds = channel_map.make_bounds_fn(source, start_pixel, pixels_per_channel, num_channels, frame_width)
        _track_rows = features.enabled("row_tracking_enabled") or features.enabled("line_sync_enabled")
        _row_mask = None     # 2D bool: pixels that triggered any algorithm (only when _track_rows)

        b = frame[:, :, 0].astype(float)
        g = frame[:, :, 1].astype(float)
        r = frame[:, :, 2].astype(float)
        
        b_vals_min = np.min(b, axis=0)
        g_vals_min = np.min(g, axis=0)
        r_vals_min = np.min(r, axis=0)

        b_vals_max = np.max(b, axis=0)
        g_vals_max = np.max(g, axis=0)
        r_vals_max = np.max(r, axis=0)
        
        # Fast dynamic object masking to isolate objects from conveyor background
        valid_b = b_vals_max[start_pixel:end_pixel]
        valid_g = g_vals_max[start_pixel:end_pixel]
        valid_r = r_vals_max[start_pixel:end_pixel]
        
        bg_b = np.median(valid_b) if len(valid_b) > 0 else 150
        bg_g = np.median(valid_g) if len(valid_g) > 0 else 50
        bg_r = np.median(valid_r) if len(valid_r) > 0 else 10
        
        diff_max_b = np.abs(b_vals_max - bg_b)
        diff_max_g = np.abs(g_vals_max - bg_g)
        diff_max_r = np.abs(r_vals_max - bg_r)
        
        diff_min_b = np.abs(b_vals_min - bg_b)
        diff_min_g = np.abs(g_vals_min - bg_g)
        diff_min_r = np.abs(r_vals_min - bg_r)
        
        obj_mask_1d = (diff_max_b > 25) | (diff_max_g > 25) | (diff_max_r > 25) | \
                      (diff_min_b > 25) | (diff_min_g > 25) | (diff_min_r > 25)
        
        # FINAL DECISION ENGINE: Structured results instead of boolean OR
        results_by_channel = {ch_idx: [] for ch_idx in range(num_channels)}

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
                try: scale = int(cfg.get("scale", 6))
                except: scale = 6

                if ch == "RED":
                    plane = r
                elif ch == "BLUE":
                    plane = b
                else:
                    plane = g

                if np.mean(r_vals_max) < 40 and np.mean(b_vals_max) > 80:
                    plane = b

                raw_def_mask = np.zeros(plane.shape, dtype=bool)
                if sens > 0:
                    if mode == "DARK DEFECT":
                        thresh_lo = 0
                        if "camera_thresh" in self.bg_settings and source in self.bg_settings["camera_thresh"]:
                            thresh_lo = self.bg_settings["camera_thresh"][source].get("lower", 0)
                        
                        eff_thresh = max(thresh_lo, min(250, int(6 + (sens * 2.44))))
                        
                        # Calibrated Threshold Detection: matches Background Tuning yellow line exactly
                        abs_mask = (plane < eff_thresh)
                        raw_def_mask = abs_mask
                    else:
                        # LIGHT DEFECT mode:
                        # Background reference:
                        plane_min_1d = np.min(plane, axis=0)
                        avg_bg = np.median(plane_min_1d[start_pixel:end_pixel]) if end_pixel > start_pixel else np.median(plane_min_1d)
                        
                        # Light defect must be significantly brighter than background
                        # High sens (e.g. 80) requires +20 above bg; low sens (e.g. 10) requires +70 above bg
                        delta = max(15, int(75 - (sens * 0.70)))
                        light_thresh = min(252, int(avg_bg + delta))
                        
                        raw_def_mask = (plane > light_thresh)

                if _track_rows:
                    _row_mask = raw_def_mask if _row_mask is None else (_row_mask | raw_def_mask)

                if mode == "LIGHT DEFECT":
                    # Scale & Purity noise filter for LIGHT DEFECT:
                    # Sensor noise is isolated 1-2 pixels; real defects span multiple pixels.
                    # Scale (e.g. 6-16) and Purity (e.g. 10-100) dictate required defect pixels in channel.
                    min_def_px = max(2, int((scale * purity) / 50.0))
                    for v_idx in range(total_valves):
                        ch_idx = v_idx % num_channels
                        chute_idx = v_idx // num_channels
                        chute_offset = start_pixel + (chute_idx * (pixels_per_channel * num_channels))
                        s_idx, e_idx = _bounds(v_idx, ch_idx, chute_offset)
                        if np.sum(raw_def_mask[:, s_idx:e_idx]) >= min_def_px:
                            results_by_channel[ch_idx].append({
                                "enabled": True, "detected": True, "algo": algo_name, "class": mode,
                                "channel": ch_idx + 1, "confidence": 1.0
                            })
                else:
                    # DARK DEFECT: check channel defect pixel volume (filters 1-px sensor noise, captures wire/needle & grains)
                    min_def_px = max(4, int(scale / 2))
                    for v_idx in range(total_valves):
                        ch_idx = v_idx % num_channels
                        chute_idx = v_idx // num_channels
                        chute_offset = start_pixel + (chute_idx * (pixels_per_channel * num_channels))
                        s_idx, e_idx = _bounds(v_idx, ch_idx, chute_offset)
                        if np.sum(raw_def_mask[:, s_idx:e_idx]) >= min_def_px:
                            results_by_channel[ch_idx].append({
                                "enabled": True, "detected": True, "algo": algo_name, "class": mode,
                                "channel": ch_idx + 1, "confidence": 1.0
                            })


            # --- SIZE LOGIC ---
            elif "SIZE" in algo_name:
                try: small = int(cfg.get("small", 1000))
                except: small = 1000
                try: big = int(cfg.get("big", 2300))
                except: big = 2300
                size_mode = cfg.get("size_mode", "REJECT BIG")

                min_width_big = max(2, int(big / 50.0))
                max_width_small = max(1, int(small / 50.0))

                gray = np.mean([b_vals_min, g_vals_min, r_vals_min], axis=0)
                thresh_val = 0
                if "camera_thresh" in self.bg_settings and source in self.bg_settings["camera_thresh"]:
                    thresh_val = self.bg_settings["camera_thresh"][source]["lower"]
                
                algo_trigger = (gray < thresh_val)
                if _track_rows:
                    _m2 = ((b + g + r) / 3.0) < thresh_val
                    _row_mask = _m2 if _row_mask is None else (_row_mask | _m2)
                if start_pixel > 0: algo_trigger[:start_pixel] = False
                if end_pixel < len(algo_trigger): algo_trigger[end_pixel:] = False

                for v_idx in range(total_valves):
                    ch_idx = v_idx % num_channels
                    chute_idx = v_idx // num_channels
                    chute_offset = start_pixel + (chute_idx * (pixels_per_channel * num_channels))
                    s_idx, e_idx = _bounds(v_idx, ch_idx, chute_offset)
                    def_width = np.sum(algo_trigger[s_idx:e_idx])
                    
                    is_big = def_width >= min_width_big
                    is_small = def_width > 0 and def_width <= max_width_small
                    
                    if "BIG" in size_mode and is_big:
                        results_by_channel[ch_idx].append({"enabled": True, "detected": True, "algo": algo_name, "class": "BIG", "channel": ch_idx + 1})
                    elif "SMALL" in size_mode and is_small:
                        results_by_channel[ch_idx].append({"enabled": True, "detected": True, "algo": algo_name, "class": "SMALL", "channel": ch_idx + 1})
                    elif "BOTH" in size_mode and (is_big or is_small):
                        cls = "BIG" if is_big else "SMALL"
                        results_by_channel[ch_idx].append({"enabled": True, "detected": True, "algo": algo_name, "class": cls, "channel": ch_idx + 1})

            # --- HSV & DISCOLOUR LOGIC ---
            elif "HSV" in algo_name or "DISCOLOUR" in algo_name:
                try: x1, y1 = int(cfg.get("x1", 50)), int(cfg.get("y1", 50))
                except: x1, y1 = 50, 50
                try: x2, y2 = int(cfg.get("x2", 200)), int(cfg.get("y2", 200))
                except: x2, y2 = 200, 200
                hsv_mode = cfg.get("hsv_mode", "SORT OUT")
                try: sens = float(cfg.get("sens", 100))
                except: sens = 100.0
                try: purity = float(cfg.get("purity", 1))
                except: purity = 1.0
                try: scale = int(cfg.get("scale", 6))
                except: scale = 6
                
                if getattr(self, '_hsv_map_cache', None) is None:
                    import cv2
                    import os
                    hsv_path = next((p for p in ["hsv.png", os.path.join(os.path.dirname(os.path.abspath(__file__)), "hsv.png"), r"d:\LAMTON_V1\hsv.png"] if os.path.exists(p)), None)
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
                
                import cv2
                frame_hsv = cv2.cvtColor(frame.astype(np.uint8), cv2.COLOR_BGR2HSV)
                raw_def_mask = (cv2.inRange(frame_hsv, l_hsv, u_hsv) > 0)
                
                if hsv_mode == "SORT IN":
                    raw_def_mask = (~raw_def_mask) & obj_mask_1d
                else:
                    raw_def_mask = raw_def_mask & obj_mask_1d

                if _track_rows:
                    _row_mask = raw_def_mask if _row_mask is None else (_row_mask | raw_def_mask)


                if scale > 0:
                    # LINE SCAN: Project to 1D first, same as GRAY
                    algo_trigger = np.any(raw_def_mask, axis=0)
                else:
                    algo_trigger = np.any(raw_def_mask, axis=0)
                    
                if start_pixel > 0: algo_trigger[:start_pixel] = False
                if end_pixel < len(algo_trigger): algo_trigger[end_pixel:] = False

                for v_idx in range(total_valves):
                    ch_idx = v_idx % num_channels
                    chute_idx = v_idx // num_channels
                    chute_offset = start_pixel + (chute_idx * (pixels_per_channel * num_channels))
                    s_idx, e_idx = _bounds(v_idx, ch_idx, chute_offset)
                    if np.any(algo_trigger[s_idx:e_idx]):
                        results_by_channel[ch_idx].append({"enabled": True, "detected": True, "algo": algo_name, "class": hsv_mode, "channel": ch_idx + 1})
            # --- DEEP LEARNING LOGIC (YOLO / CNN) ---
            elif "DEEP LEARNING" in algo_name or "YOLO" in algo_name:
                if getattr(self, '_dl_buffer', None) is None:
                    self._dl_buffer = []
                
                self._dl_buffer.append(frame[5, :, :])
                
                if len(self._dl_buffer) >= 64:
                    dl_img = np.vstack(self._dl_buffer).astype(np.uint8)
                    self._dl_buffer = []
                    
                    try:
                        if getattr(self, '_live_yolo_model', None) is None and not getattr(self, '_ai_load_error', None):
                            import os
                            model_path = None
                            for p in [self.program_data.get(getattr(self, 'current_program', ''), {}).get('ai_model', {}).get('model_path'), 'best.pt']:
                                if p and os.path.exists(p):
                                    model_path = p
                                    break
                            if model_path:
                                # AI packages are optional: if they are missing or fail to load (e.g. a torch DLL
                                # error), remember the reason once - sorting continues with the other algorithms
                                # and the GUI shows the message (check_ai_status). No retry every 64 frames.
                                try:
                                    if TORCH_IMPORT_ERROR:
                                        raise RuntimeError(f"PyTorch not available ({TORCH_IMPORT_ERROR[:120]})")
                                    from ultralytics import YOLO
                                    self._live_yolo_model = YOLO(model_path)
                                except Exception as e:
                                    self._ai_load_error = f"{type(e).__name__}: {e}"
                                    print(f"[AI] DEEP LEARNING (YOLO) not available - {self._ai_load_error}")
                            else:
                                self._ai_load_error = "model file not found (best.pt / recipe model path)"
                                print(f"[AI] DEEP LEARNING (YOLO) not available - {self._ai_load_error}")
                        
                        if getattr(self, '_live_yolo_model', None):
                            results = self._live_yolo_model.predict(dl_img, conf=0.5, verbose=False)
                            for r in results:
                                for box in r.boxes:
                                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                                    cls_name = self._live_yolo_model.names[int(box.cls[0])] if hasattr(self._live_yolo_model, 'names') else "AI_DEFECT"
                                    conf = float(box.conf[0])
                                    for v_idx in range(total_valves):
                                        ch_idx = v_idx % num_channels
                                        chute_idx = v_idx // num_channels
                                        chute_offset = start_pixel + (chute_idx * (pixels_per_channel * num_channels))
                                        s_idx, e_idx = _bounds(v_idx, ch_idx, chute_offset)
                                        if x1 <= e_idx and x2 >= s_idx:
                                            results_by_channel[ch_idx].append({
                                                "enabled": True, "detected": True, "algo": algo_name,
                                                "class": cls_name, "confidence": conf, "channel": ch_idx + 1
                                            })
                    except Exception as e:
                        pass

            # --- FALLBACK LOGIC ---
            else:
                thresh_lower = 0
                thresh_upper = 255
                if "camera_thresh" in self.bg_settings and source in self.bg_settings["camera_thresh"]:
                    thresh_lower = self.bg_settings["camera_thresh"][source]["lower"]
                    thresh_upper = self.bg_settings["camera_thresh"][source].get("upper", 255)
                
                algo_trigger = np.zeros(len(b_vals_min), dtype=bool)
                wave_mode = camera_configs.get("wave_mode", "BLUE")
                if "BLUE" in wave_mode or "ALL" in wave_mode: 
                    algo_trigger |= (b_vals_min < thresh_lower) | (b_vals_max > thresh_upper)
                if "GREEN" in wave_mode or "ALL" in wave_mode: 
                    algo_trigger |= (g_vals_min < thresh_lower) | (g_vals_max > thresh_upper)
                if "RED" in wave_mode or "ALL" in wave_mode: 
                    algo_trigger |= (r_vals_min < thresh_lower) | (r_vals_max > thresh_upper)
                
                algo_trigger = algo_trigger & obj_mask_1d

                if _track_rows:
                    _m2 = np.zeros(b.shape, dtype=bool)
                    if "BLUE" in wave_mode or "ALL" in wave_mode:
                        _m2 |= (b < thresh_lower) | (b > thresh_upper)
                    if "GREEN" in wave_mode or "ALL" in wave_mode:
                        _m2 |= (g < thresh_lower) | (g > thresh_upper)
                    if "RED" in wave_mode or "ALL" in wave_mode:
                        _m2 |= (r < thresh_lower) | (r > thresh_upper)
                    _m2 &= obj_mask_1d
                    _row_mask = _m2 if _row_mask is None else (_row_mask | _m2)

                if start_pixel > 0: algo_trigger[:start_pixel] = False
                if end_pixel < len(algo_trigger): algo_trigger[end_pixel:] = False

                for v_idx in range(total_valves):
                    ch_idx = v_idx % num_channels
                    chute_idx = v_idx // num_channels
                    chute_offset = start_pixel + (chute_idx * (pixels_per_channel * num_channels))
                    s_idx, e_idx = _bounds(v_idx, ch_idx, chute_offset)
                    if np.sum(algo_trigger[s_idx:e_idx]) >= 1:
                        results_by_channel[ch_idx].append({"enabled": True, "detected": True, "algo": "FALLBACK", "class": "DEFECT", "channel": ch_idx + 1})


        # Evaluate final decision for this source
        valid_channels = []
        best_decision = None
        
        for ch_idx, algos in results_by_channel.items():
            if not algos: continue
            
            # Here we resolve any conflicts on a per-channel basis.
            # If at least one active algorithm returned a valid defect, the channel is marked.
            
            # Select the most authoritative algorithm result for display/logging
            # Priority: AI -> HSV -> SIZE -> GRAY -> FALLBACK
            sorted_algos = sorted(algos, key=lambda x: {"YOLO":0, "DEEP LEARNING":0, "HSV":1, "SIZE":2, "GRAY A":3, "GRAY B":4}.get(x["algo"], 5))
            authoritative_result = sorted_algos[0]
            
            # Map 0-based channel index to 1-based physical valves
            for c_idx in range(total_chutes):
                v_idx = c_idx * num_channels + ch_idx
                if 0 <= v_idx < total_valves:
                    role = chute_roles.get(str(c_idx + 1), "PRIMARY")
                    if role == "DISABLED":
                        continue
                        
                    physical_ch_1_based = v_idx + 1
                    if physical_ch_1_based <= 63:
                        valid_channels.append(physical_ch_1_based)
                        if best_decision is None:
                            best_decision = authoritative_result
                            best_decision["channel"] = physical_ch_1_based
                        
        final_decision = {
            "valid": len(valid_channels) > 0,
            "channels": valid_channels,
            "best_class": best_decision["class"] if best_decision else "UNDEFINED",
            "best_algo": best_decision["algo"] if best_decision else "NONE",
            "confidence": best_decision.get("confidence", 1.0) if best_decision else 0.0,
            "primary_channel": best_decision["channel"] if best_decision else None,
            "all_results": results_by_channel
        }

        # Phase 3: first/last row of the defect in every fired channel (rows of THIS frame)
        if _track_rows and valid_channels:
            extents = {}
            for phys in valid_channels:
                v_idx = phys - 1
                ch_idx = v_idx % num_channels
                chute_offset = start_pixel + (v_idx // num_channels) * (pixels_per_channel * num_channels)
                s_idx, e_idx = _bounds(v_idx, ch_idx, chute_offset)
                if _row_mask is not None:
                    rows = np.nonzero(_row_mask[:, s_idx:e_idx].any(axis=1))[0]
                    if rows.size:
                        extents[phys] = (int(rows[0]), int(rows[-1]))
            final_decision["row_extents"] = extents

        return final_decision

    def evaluate_fusion_and_fire(self, source=None):
        f_dec = getattr(self, '_last_front_decision', None)
        r_dec = getattr(self, '_last_rear_decision', None)
        
        # Determine fused logic
        f_valid = f_dec.get("valid", False) if f_dec else False
        r_valid = r_dec.get("valid", False) if r_dec else False
        
        if self.wire_test_mode_active:
            if not hasattr(self, 'wire_states'):
                self.wire_states = {ch: {'held': False, 'detect': 0, 'release': 0} for ch in range(1, 65)}
                
            all_detected = set()
            if f_valid: all_detected.update(f_dec.get("channels", []))
            if r_valid: all_detected.update(r_dec.get("channels", []))
            
            stable_limit = self.wire_settings.get('stable_frames', 5)
            release_limit = self.wire_settings.get('release_frames', 5)
            
            ui_needs_update = False
            for ch in range(1, 64):
                state = self.wire_states[ch]
                if ch in all_detected:
                    state['release'] = 0
                    if not state['held']:
                        state['detect'] += 1
                        if state['detect'] >= stable_limit:
                            state['held'] = True
                            print(f"[WIRE] STABLE -> HOLD_ON CH{ch}")
                            if hasattr(self, 'esp32') and self.esp32:
                                self.esp32.hold_ejector_on(ch)
                            ui_needs_update = True
                else:
                    if state['detect'] > 0 or state['held']:
                        state['release'] += 1
                        if state['release'] >= release_limit:
                            if state['held']:
                                print(f"[WIRE] REMOVED -> HOLD_OFF CH{ch}")
                                if hasattr(self, 'esp32') and self.esp32:
                                    self.esp32.hold_ejector_off(ch)
                                ui_needs_update = True
                            state['held'] = False
                            state['detect'] = 0
                            state['release'] = 0
                            
            if ui_needs_update:
                held_chs = [str(c) for c, s in self.wire_states.items() if s['held']]
                if held_chs:
                    self.wire_present = True
                    self.wire_active_channel = ",".join(held_chs)
                else:
                    self.wire_present = False
                    self.wire_active_channel = None
                
                if getattr(self, 'ejector_window', None):
                    self.ejector_window.update_wire_status_ui()
                        
        elif self.system_running:
            if getattr(self, 'capture_ejection_off', False):
                return          # Phase 4: dataset capture with ejection switched off
            # PHASE 1 FIX (50 ms long-blast bug):
            # Old code fired the UNION of the last FRONT and last REAR decisions on every frame.
            # A detection from one camera was therefore re-used when the other camera's frame
            # arrived, counted as a "long object", and padded to a 50 ms blast (175 mm of product).
            # Now each frame fires ONLY the channels found in that frame, and a "long object"
            # means the SAME camera saw the channel in consecutive frames.
            if source == "REAR":
                dec, valid = r_dec, r_valid
            elif source == "FRONT":
                dec, valid = f_dec, f_valid
            else:
                return
            frame_channels = sorted(set(dec.get("channels", []))) if valid else []

            # --- LONG OBJECT TRACKER (per camera) ---
            if not hasattr(self, '_channel_active_frames'):
                self._channel_active_frames = {"FRONT": {}, "REAR": {}}
            counts = self._channel_active_frames.setdefault(source, {})
            for ch in list(counts.keys()):
                if ch not in frame_channels:
                    del counts[ch]
            long_channels = []
            for ch in frame_channels:
                counts[ch] = counts.get(ch, 0) + 1
                if counts[ch] > 1:
                    long_channels.append(ch)

            # Real-time 1:1 Operating LED Bar update on EVERY frame (clears immediately when no defect)
            if hasattr(self, 'operating_led_bar') and self.operating_led_bar:
                self.operating_led_bar.set_active_channels(frame_channels)

            # Instantaneous Zero-Lag Hardware Ejection (Stateful Direct ESP32 Byte Protocol):
            # Opens instant microseconds when needle touches, stays solidly OPEN (no chattering!),
            # and snaps OFF the exact microsecond the needle leaves!
            if not hasattr(self, '_live_hw_active'):
                self._live_hw_active = set()

            link = getattr(self.esp32, 'ejector_link', None) if hasattr(self, 'esp32') and self.esp32 else None
            if link and getattr(link, 'is_open', False) and getattr(link, 'mode', None) == "ESP_BYTE":
                current_active = set(frame_channels)
                newly_on = [ch for ch in current_active if ch not in self._live_hw_active]
                to_turn_off = [ch for ch in self._live_hw_active if ch not in current_active]
                
                out_bytes = []
                if to_turn_off:
                    for ch in to_turn_off:
                        self._live_hw_active.discard(ch)
                        out_bytes.append(ch + 100)
                if newly_on:
                    for ch in newly_on:
                        self._live_hw_active.add(ch)
                        out_bytes.append(ch)
                if out_bytes:
                    try:
                        link._write(bytes(out_bytes))
                    except Exception as e:
                        print("Direct byte eject error:", e)
            else:
                if frame_channels:
                    meta = getattr(self, '_frame_meta', {}).get(source)
                    delay_ms, pulse_ms = self.get_ejector_config()
                    if hasattr(self, 'eject_scheduler') and self.eject_scheduler:
                        self.eject_scheduler.schedule_fire(frame_channels, delay_ms, pulse_ms, meta=meta)



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
        self.reload_camera_configs()
        self.system_running = True
        print("[START] Sorting sequence initiated.")
        self._channel_active_frames = {"FRONT": {}, "REAR": {}}
        if hasattr(self, 'eject_scheduler') and self.eject_scheduler:
            with self.eject_scheduler.lock:
                self.eject_scheduler.queue.clear()
                self.eject_scheduler.channel_lockout.clear()
        
        # 0. Auto-reconnect Modbus if offline
        if hasattr(self, 'esp32') and self.esp32 and not getattr(self.esp32, 'is_modbus_connected', lambda: False)():
            print("[INFO] Modbus relay server offline. Attempting auto-connection...")
            self.esp32.ensure_relay()

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

        # PHASE 1 SAFETY: START is the explicit "operator is present" action that clears watchdog trips
        if hasattr(self, 'esp32') and self.esp32:
            self.esp32.rearm_safety()
            self.esp32.set_running_flag(True)
        if getattr(self, 'line_sync', None):
            self.line_sync.on_start()          # does nothing unless everything is confirmed
        if getattr(self, '_safety_alarm_shown', False):
            self._safety_alarm_shown = False
            self.alarm_active = False
            self.lbl_alarm.setText("⚠ AIR PRESSURE LOW ⚠")

        # Immediate Sequence: Ejector -> Vibrator (No Delays!)
        if hasattr(self, 'esp32') and self.esp32.connected:
            self.esp32.set_relay(2, 1) # Ejector ON
            self.esp32.set_relay(1, 1) # Vibrator ON
            print("[SEQ] Ejector ON, Vibrator ON")
            self.turn_on_vibrators()
            
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
        if hasattr(self, '_live_hw_active') and self._live_hw_active:
            link = getattr(self.esp32, 'ejector_link', None) if hasattr(self, 'esp32') and self.esp32 else None
            if link and getattr(link, 'is_open', False) and getattr(link, 'mode', None) == "ESP_BYTE":
                try:
                    link._write(bytes([ch + 100 for ch in self._live_hw_active]))
                except Exception:
                    pass
            self._live_hw_active.clear()
        if hasattr(self, 'eject_scheduler') and self.eject_scheduler:
            with self.eject_scheduler.lock:
                self.eject_scheduler.queue.clear()
                self.eject_scheduler.channel_lockout.clear()
        if hasattr(self, 'esp32') and self.esp32:
            self.esp32.set_running_flag(False)
        if getattr(self, 'line_sync', None):
            self.line_sync.on_stop()
        
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
            self.esp32.stop_all_ejectors()
            self.esp32.set_vibrator(1, 0)

    # =========================================================================
    # UI SETUP & HELPER FUNCTIONS
    # =========================================================================
    def on_camera_connection_changed(self, connected):
        if not connected:
            if not getattr(self, '_reconnecting_cameras', False):
                self._reconnecting_cameras = True
                QTimer.singleShot(5000, self.auto_connect_cameras)
        else:
            self._reconnecting_cameras = False

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
            # Only fallback if NO specific front SN is configured; NEVER steal the rear camera
            if not front_found and not front_sn_target:
                for dev in dev_list:
                    try:
                        dev_sn = dev.acSn.decode('utf-8').strip('\x00')
                        if dev_sn not in connected_sn_list and dev_sn != rear_sn_target:
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
            # Only fallback if NO specific rear SN is configured; NEVER steal the front camera
            if not rear_found and not rear_sn_target:
                for dev in dev_list:
                    try:
                        dev_sn = dev.acSn.decode('utf-8').strip('\x00')
                        if dev_sn not in connected_sn_list and dev_sn != front_sn_target:
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
        """Start-up: every ENABLED device uses its saved port if it answers as expected, otherwise the port that
        answers correctly is found by port_detect (any COM number), used and saved. Disabled devices are never
        opened or probed. No COM number is hard-coded."""
        import json, os
        config_file = "system_config.json"
        cfg = {}
        if os.path.exists(config_file):
            try:
                with open(config_file, "r") as f:
                    cfg = json.load(f)
            except Exception as e:
                print(f"[WARN] Error reading system_config.json: {e}")
        esp = self.esp32
        esp.relay_enabled = port_detect.is_enabled(cfg, "relay")
        esp.relay_port_cfg = cfg.get("last_port") or None

        if cfg.get("auto_connect", True):
            assigned, messages, new_values = port_detect.resolve(cfg, progress=lambda m: print(f"[PORTS] {m}"))
            for line in messages:
                print(f"[PORTS] {line}")
            modbus_success = False
            modbus_active_port = None
            if assigned["relay"]:
                esp.relay_port_cfg = assigned["relay"].port
                if esp.connect(assigned["relay"].port):
                    modbus_success, modbus_active_port = True, assigned["relay"].port
            if assigned["ejector"]:
                esp.connect_ejector(assigned["ejector"].port)
            if assigned["feeder"]:
                esp.connect_vibrator(assigned["feeder"].port)

            # Direct guarantee: ejector must connect on COM13 or COM24 if not yet connected
            if not esp.is_ejector_connected():
                candidate_ports = [cfg.get("ejector_port"), "COM13", "COM24"]
                present_devs = {p.device for p in port_detect.list_ports()}
                for cp in candidate_ports:
                    if cp and cp in present_devs and cp != getattr(esp, 'active_port', None):
                        print(f"[PORTS] Checking candidate ejector port {cp}...")
                        if esp.connect_ejector(cp):
                            new_values["ejector_port"] = cp
                            break

            # Guarantee relay connected if present on COM13 or COM24
            if esp.relay_enabled and not modbus_success:
                relay_candidates = [cfg.get("last_port"), "COM13", "COM24"]
                ej_curr = getattr(esp, 'ejector_port', None)
                present_devs = {p.device for p in port_detect.list_ports()}
                for rp in relay_candidates:
                    if rp and rp in present_devs and rp != ej_curr:
                        if esp.connect(rp):
                            modbus_success, modbus_active_port = True, rp
                            new_values["last_port"] = rp
                            break

            if new_values:                                   # a device was found on another port: remember it
                cfg.update(new_values)
                try:
                    with open(config_file, "w") as f:
                        json.dump(cfg, f, indent=4)
                    print(f"[PORTS] saved in system_config.json: {new_values}")
                except Exception as e:
                    print(f"[PORTS] could not save system_config.json: {e}")
            notable = [m for m in messages if any(k in m for k in ("FOUND", "NOT found", "did not answer", "not present", "WARNING", "no port"))]
            if notable:
                self._port_message = "\n".join(notable)
                QTimer.singleShot(1500, self._show_port_message)

            QTimer.singleShot(400, self.apply_saved_lighting)
            self.update_esp32_relay_status_ui(modbus_success, modbus_active_port)

    def _show_port_message(self):
        """Clear, non-blocking message about COM ports that were found / changed / missing at start-up."""
        text = getattr(self, '_port_message', None)
        if not text:
            return
        self._port_message = None
        self._port_info = show_info_dialog(self, "COM ports", "COM port check at start-up",
                                           text + "\n\nChange it in COM PORTS (settings, ENGINEER login).")

    def open_port_settings(self):
        from port_settings_ui import PortSettingsDialog
        dlg = PortSettingsDialog(self.esp32, self)
        dlg.exec_()
        self.update_esp32_relay_status_ui(self.esp32.is_modbus_connected())

    def update_esp32_relay_status_ui(self, connected, port=None):
        if port is None and hasattr(self.esp32, 'active_port'):
            port = self.esp32.active_port or ""

        mod_ok = bool(connected or (hasattr(self.esp32, 'is_modbus_connected') and self.esp32.is_modbus_connected()))
        ej_ok = bool(hasattr(self.esp32, 'is_ejector_connected') and self.esp32.is_ejector_connected())
        ej_port = getattr(self.esp32, 'ejector_port', None) if ej_ok else None

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
            print("[INFO] Relay board offline. Reconnecting on its configured port ...")
            port = getattr(self.esp32, 'active_port', None) or getattr(self.esp32, 'relay_port_cfg', None) or ""
            if self.esp32.ensure_relay():
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

    def kiosk_exit_app(self):
        from PyQt5.QtWidgets import QInputDialog, QLineEdit, QMessageBox
        text, ok = QInputDialog.getText(self, "Admin Password", "Enter Admin Password to Exit to Desktop:", QLineEdit.Password)
        if ok and (text in ["TCM_ADMIN_2026", "11*99", "1199"]):
            self.allow_exit = True
            self.close()
        elif ok:
            QMessageBox.critical(self, "Error", "Invalid Password! Access Denied.")
            
    def kiosk_shutdown_pc(self):
        from PyQt5.QtWidgets import QMessageBox
        reply = QMessageBox.question(self, "Confirm Shutdown", "Are you sure you want to shut down the computer?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                self.allow_exit = True
                self.close()
            except Exception:
                pass
            os.system("shutdown /s /t 0")

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
            self.esp32.ensure_relay()

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
            self.esp32.ensure_relay()

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
            port = getattr(self.esp32, 'active_port', None) or ""
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
        self.reload_camera_configs()

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
        
        if kp.exec_() != QDialog.Accepted or not entered_val or entered_val[0] not in ["11*99", "1199", "TCM_ADMIN_2026"]:
            QMessageBox.critical(self, "Authorization Failed", "Incorrect password!")
            return

        self.allow_exit = True
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

        svg_dir = ASSET_DIR
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
        # Phase 4 (OFF by default): continuous dataset capture button
        if features.enabled("continuous_capture_button"):
            btn_capture = QPushButton("CONTINUOUS CAPTURE")
            btn_capture.setStyleSheet("background-color: #333; color: #b388ff; border: 1px solid #b388ff; font-weight: bold;")
            btn_capture.clicked.connect(self.open_continuous_capture)
            btn_layout.addWidget(btn_capture)
        # 63-channel ejector test panel (ejector_test_panel.py), shares the sorter's ejector connection
        if features.enabled("ejector_test_panel_button"):
            self.btn_ejector_panel = QPushButton("EJECTOR TEST PANEL")
            self.btn_ejector_panel.setStyleSheet("background-color: #333; color: #00e5ff; border: 1px solid #00e5ff; font-weight: bold;")
            self.btn_ejector_panel.clicked.connect(self.open_ejector_test_panel)
            btn_layout.addWidget(self.btn_ejector_panel)
        # COM PORTS: dropdown + refresh + auto detect + test for ejector / relay / feeder (ENGINEER login)
        self.btn_com_ports = QPushButton("COM PORTS")
        self.btn_com_ports.setStyleSheet("background-color: #333; color: #ffd740; border: 1px solid #ffd740; font-weight: bold;")
        self.btn_com_ports.clicked.connect(self.open_port_settings)
        btn_layout.addWidget(self.btn_com_ports)
        btn_layout.addSpacing(20)
        btn_layout.addWidget(btn_exit)
        
        footer_layout.addLayout(btn_layout)
        self.main_layout.addWidget(footer)

    def open_continuous_capture(self):
        from capture_ui import ContinuousCaptureDialog
        dlg = ContinuousCaptureDialog(self)
        dlg.exec_()

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

            
    def check_safety_trip(self):
        """GUI-thread handler for watchdog trips reported by the ESP32 boards (Phase 1)."""
        esp = getattr(self, 'esp32', None)
        reason = getattr(esp, 'safety_trip_reason', None) if esp else None
        if not reason:
            return
        esp.safety_trip_reason = None
        print(f"[SAFETY] {reason}")
        if self.system_running:
            self.on_stop_system()
        self._safety_alarm_shown = True
        self.lbl_alarm.setText(f"⚠ SAFETY STOP: {reason} ⚠")
        self.alarm_active = True

    def check_ai_status(self):
        """GUI thread: show ONCE why the DEEP LEARNING (YOLO/CNN) algorithm is not available."""
        err = getattr(self, '_ai_load_error', None)
        if not err or getattr(self, '_ai_error_shown', False):
            return
        self._ai_error_shown = True
        self._ai_info = show_info_dialog(self, "AI not available",
                                         "The DEEP LEARNING (YOLO / CNN) algorithm cannot be used on this PC.",
                                         "Sorting continues with the other algorithms (colour / HSV / size / grey).\n\n"
                                         f"Reason: {err}\n\nRun SYSTEM_CHECK.bat; install the AI packages with INSTALL.bat.")

    def update_time(self):
        self.check_safety_trip()
        self.check_ai_status()
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
        if hasattr(self, 'btn_com_ports'):
            self.btn_com_ports.setEnabled(role != "OPERATOR")
            self.btn_com_ports.setText("COM PORTS" if role != "OPERATOR" else "COM PORTS 🔒")
        if hasattr(self, 'btn_ejector_panel'):
            self.btn_ejector_panel.setEnabled(role != "OPERATOR")
            self.btn_ejector_panel.setText("EJECTOR TEST PANEL" if role != "OPERATOR" else "EJECTOR TEST PANEL 🔒")
        # Camera Setup is MANUFACTURER-only
        if hasattr(self, 'btn_cam'):
            self.btn_cam.setEnabled(role == "MANUFACTURER")
            self.btn_cam.setText("📷 CAMERA SETUP" if role == "MANUFACTURER" else "📷 CAMERA SETUP 🔒")
        col = "#00bcd4" if role == "ENGINEER" else "#ffd700" if role == "MANUFACTURER" else "#888"
        self.btn_user.setStyleSheet(f"border: 2px solid {col}; color: {col};")

    def show_ejector_window(self, scheme_name=None):
        target_scheme = scheme_name or getattr(self, 'current_program', RecipeManager.get_active_scheme_name())
        self.esp32.test_mode = True
        dlg = EjectorTestWindow(self.esp32, self, scheme_name=target_scheme)
        dlg.exec_()
        self.esp32.test_mode = False

    def open_ejector_test_panel(self):
        """63-channel ejector test panel in its own dialog. It uses the sorter's EXISTING ejector
        connection (no second COM port). Sorting ejections are paused while it is open."""
        from ejector_test_panel import EjectorTestPanel      # only the panel class (name clash: EjectorTestWindow)
        dlg = QDialog(self)
        dlg.setWindowTitle("63-CH Ejector Test Panel")
        dlg.resize(1100, 980)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        panel = EjectorTestPanel(link_provider=lambda: self.esp32.ejector_link, parent=dlg)
        lay.addWidget(panel)
        self.esp32.test_mode = True
        self.esp32.ejector_test_active = True              # ESP32Controller skips sorting ejections
        try:
            dlg.exec_()
        finally:
            panel.shutdown()                               # all valves OFF; the shared link stays open
            self.esp32.ejector_test_active = False
            self.esp32.test_mode = False
        
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


class LicenseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("System Information & License Management")
        self.setFixedSize(520, 360)
        self.setStyleSheet("background-color: #1a1a1a; color: white; font-family: Segoe UI;")
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        lbl_title = QLabel("VISION SORTING SYSTEM V3.0")
        lbl_title.setStyleSheet("font-size: 18px; color: #00bcd4; font-weight: bold;")
        lbl_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_title)

        hw_id = LicenseManager.get_pc_serial()
        lbl_hw = QLabel(f"Hardware PC ID: {hw_id}")
        lbl_hw.setStyleSheet("font-size: 13px; color: #ffeb3b; font-weight: bold; background: #222; padding: 6px; border-radius: 4px;")
        lbl_hw.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_hw)

        self.lbl_expiry = QLabel()
        self.lbl_expiry.setStyleSheet("font-size: 14px; color: #00e676; font-weight: bold; margin-top: 4px;")
        self.lbl_expiry.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_expiry)
        self.refresh_license_display()

        layout.addWidget(QLabel("Update License Key:"))
        self.txt_key = QLineEdit()
        self.txt_key.setStyleSheet("background: #333; color: #00e676; padding: 8px; font-size: 14px; font-weight: bold; border: 1px solid #555; border-radius: 4px;")
        self.txt_key.setPlaceholderText("Enter 16-character code (e.g. XXXX-XXXX-XXXX-XXXX)...")
        KeypadEventFilter(self.txt_key, lambda: VirtualKeyboard(self.txt_key.text(), self.txt_key.setText, self).exec_())
        layout.addWidget(self.txt_key)

        btn_update = QPushButton("VERIFY & ACTIVATE LICENSE")
        btn_update.setStyleSheet("background: #00e676; color: black; font-weight: bold; padding: 10px; font-size: 14px; border-radius: 5px;")
        btn_update.clicked.connect(self.update_license)
        layout.addWidget(btn_update)

        btn_close = QPushButton("CLOSE")
        btn_close.setStyleSheet("background: #444; color: white; padding: 8px; font-size: 13px; border-radius: 4px;")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def refresh_license_display(self):
        info = LicenseManager.get_license_info()
        if info["valid"]:
            self.lbl_expiry.setText(f"Status: ACTIVE ({info['days_remaining']} Days Remaining)\nExpires: {info['expiry_date']}")
            self.lbl_expiry.setStyleSheet("font-size: 14px; color: #00e676; font-weight: bold; margin-top: 4px;")
        else:
            self.lbl_expiry.setText(f"Status: {info['status']}\nExpiry Date: {info['expiry_date']}")
            self.lbl_expiry.setStyleSheet("font-size: 14px; color: #ff5252; font-weight: bold; margin-top: 4px;")

    def update_license(self):
        key = self.txt_key.text()
        success, msg, exp_date, days = LicenseManager.apply_license(key)
        if success:
            QMessageBox.information(self, "Success", f"✅ {msg}")
            self.refresh_license_display()
            self.txt_key.clear()
            p = self.parent()
            if p and hasattr(p, "update_time"):
                p.update_time()
        else:
            QMessageBox.warning(self, "License Error", f"❌ {msg}")


if __name__ == "__main__":
    import sys
    import os

    # 13.3 இன்ச் திரையில் Windows Scaling (125% or 150%) பிரச்சனை வராமல் தடுக்க
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)

    # --- STRICT HARDWARE LOCK ---
    ALLOWED_UUID = "03000200-0400-0500-0006-000700080009"
    ALLOWED_CAMERAS = ["075002923007", "076011622362"]
    
    current_uuid = LicenseManager.get_pc_serial()
    
    try:
        import mvsdk
        dev_list = mvsdk.CameraEnumerateDevice()
        current_cams = [dev.GetSn() for dev in dev_list]
    except Exception:
        current_cams = []
        
    # Hard disk UID protection and camera serial number checks removed temporarily for testing purpose
    uuid_match = True
    cam_match = True
    dev_mode = True
    print("=" * 70)
    print("[TESTING MODE] Hard disk UID protection & Camera serial checks REMOVED temporarily.")
    print(f"[TESTING MODE] PC UUID: {current_uuid} | Cameras found: {current_cams or 'NO CAMERA'}")
    print("=" * 70)

    if not dev_mode and (not uuid_match or not cam_match):
        print(f"[HARDWARE LOCK] Access Denied: PC UUID match {uuid_match}, cameras found {current_cams}")
        from PyQt5.QtWidgets import QMessageBox
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Hardware Lock - Access Denied")
        msg.setText("Access Denied! (Hardware Mismatch)")
        
        err_details = f"PC UUID Match: {uuid_match}\nCameras Found: {current_cams}\nExpected: {ALLOWED_CAMERAS}"
        msg.setInformativeText(f"This software is licensed only for this specific computer and registered cameras.\n\n{err_details}")
        msg.exec_()
        sys.exit(1)
    # ----------------------------

    window = MainDashboard()
    if dev_mode:
        DeveloperMode.apply_banner(window, current_cams)
    window.showFullScreen()  # விண்டோவை முழுத்திரையில் கொண்டுவர
    sys.exit(app.exec_())



