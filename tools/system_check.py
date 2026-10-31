"""
system_check.py - READ-ONLY check of a sorter PC. Prints PASS / WARN / FAIL for:

  1. Python + packages (versions compared with requirements.txt)
  2. MindVision SDK + DLL (installed, version, loads)
  3. Cameras (model, serial number, IP) compared with camera_config.json and the hardware lock
  4. Disk: free space and write speed
  5. ESP32 boards: every USB-serial port with an ESP32 USB chip, firmware version on each board
  6. GPU (only if the AI packages are installed)
  7. Licence: PC UUID, start-up hardware lock, licence key

    python tools\\system_check.py                  (SYSTEM_CHECK.bat in the deployment package)
    python tools\\system_check.py --no-disk-speed  (skip the write-speed test)

It changes nothing:
  - cameras are only LISTED (not opened, no setting touched);
  - every COM port: text "PING" (115200, then 921600 for older firmware) - does not arm the board, no valve is touched;
  - a port that did NOT answer as the ejector: Modbus read of register 11 / write of the non-existent register 200
    (no relay is switched); a port that answered as the ejector never receives Modbus bytes;
  - the feeder's saved port (Nuvoton board never answers) receives nothing;
  - a disabled device (e.g. modbus_enabled = false) is never opened or probed;
  - the result shows what the sorter would do at start-up (port_detect.resolve), with any COM number;
  - ports used by another program (sorter running) are reported as BUSY, not forced;
  - the disk speed test writes ONE temporary file (256 MB) and deletes it again.
Close the sorter program first, otherwise its ports show as BUSY.
"""
import argparse
import ctypes
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(TOOLS)
PKG = os.path.dirname(APP)
sys.path.insert(0, APP)

RESULTS = []


def say(level, text):
    RESULTS.append(level)
    print(f"[{level}] {text}", flush=True)


def head(t):
    print("\n" + "=" * 70 + f"\n {t}\n" + "=" * 70, flush=True)


def load_json(name):
    try:
        with open(os.path.join(APP, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ----------------------------------------------------------------------------- 1. Python
def check_python():
    head("1. PYTHON AND PACKAGES")
    v = sys.version_info
    bits = platform.architecture()[0]
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    say("PASS" if v >= (3, 8) and bits == "64bit" else "FAIL",
        f"Python {platform.python_version()} {bits}  ({sys.executable}){'  [package .venv]' if in_venv else ''}")
    req = {}
    for path in (os.path.join(PKG, "requirements.txt"), os.path.join(PKG, "requirements_cnn.txt"),
                 os.path.join(APP, "requirements.txt")):
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([^\s#;]+)", line)
                if m:
                    req.setdefault(m.group(1).lower(), m.group(2))
    try:
        from importlib import metadata
    except ImportError:
        metadata = None

    def ver(dist):
        try:
            return metadata.version(dist) if metadata else "?"
        except Exception:
            return None

    core = [("PyQt5", "PyQt5.QtWidgets"), ("numpy", "numpy"), ("opencv-python", "cv2"),
            ("pyserial", "serial"), ("pymodbus", "pymodbus")]
    for dist, mod in core:
        have = ver(dist)
        try:
            __import__(mod)
            ok = True
        except Exception as exc:
            ok, have = False, f"{have or 'not installed'} - import failed: {str(exc)[:80]}"
        want = req.get(dist.lower())
        if not ok:
            say("FAIL", f"{dist:14} {have}   (needed {want or '-'})")
        elif want and have != want:
            say("WARN", f"{dist:14} {have}   (tested version {want})")
        else:
            say("PASS", f"{dist:14} {have}")
    # optional AI packages: checked in a separate process (torch must not be imported after PyQt5 here)
    ai = {d: ver(d) for d in ("torch", "torchvision", "ultralytics")}
    if not any(ai.values()):
        say("WARN", "CNN packages (torch / torchvision / ultralytics) not installed - the sorter still starts and "
                    "sorts (colour / HSV / contour); CNN inference + training need them: run INSTALL.bat")
        return False
    for d, have in ai.items():
        want = req.get(d)
        say("PASS" if have and (not want or have.split("+")[0] == want) else "WARN",
            f"{d:14} {have or 'not installed'}" + (f"   (tested {want})" if want and have and have.split('+')[0] != want else ""))
    for d in (os.path.join(PKG, "cnn"), os.path.join(APP, "cnn")):
        script = os.path.join(d, "check_cnn.py")
        if os.path.exists(script):
            r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=300)
            line = (r.stdout.strip().splitlines() or [r.stderr.strip()[-200:]])[-1]
            say("PASS" if line.startswith("CNN_OK") else "FAIL",
                "CNN self-test (CPU): " + line.replace("CNN_OK ", "").replace("CNN_FAIL ", ""))
            break
    # The sorter imports torch FIRST (before cv2 / PyQt5): the other order fails on Windows (WinError 1114).
    code = "import torch, cv2, PyQt5.QtWidgets; from ultralytics import YOLO; print('ok')"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    if "ok" in r.stdout:
        say("PASS", "torch + ultralytics load in the sorter's import order (torch first, then cv2 / PyQt5)")
    else:
        msg = (r.stderr.strip().splitlines() or ["?"])[-1][:150]
        say("WARN", f"CNN / YOLO packages fail to load -> the sorter still starts, CNN/YOLO disabled: {msg}")
    return True


# ----------------------------------------------------------------------------- 2. MindVision
def file_version(path):
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
        buf = ctypes.create_string_buffer(size)
        ver.GetFileVersionInfoW(path, 0, size, buf)
        p, n = ctypes.c_void_p(), ctypes.c_uint()
        ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n))
        ms, ls = struct.unpack_from("<II", ctypes.string_at(p.value + 8, 8))
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return "?"


def check_mindvision():
    head("2. MINDVISION CAMERA SDK")
    sdk_path = None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\MindVision\Settings") as k:
            sdk_path = winreg.QueryValueEx(k, "DeviceInterfacePath")[0]
    except Exception:
        pass
    if sdk_path and os.path.exists(os.path.join(sdk_path, "X64", "GiGeCamera_X64.Interface")):
        say("PASS", f"MindVision SDK installed: {sdk_path} (GigE interface present)")
    else:
        say("FAIL", "MindVision SDK NOT installed (registry/GigE interface missing) - run the MindVision "
                    "Camera Platform Setup in drivers\\")
    app_dll = os.path.join(APP, "MVCAMSDK_X64.dll")
    sys_dll = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "MVCAMSDK_X64.dll")
    va = file_version(app_dll) if os.path.exists(app_dll) else None
    vs = file_version(sys_dll) if os.path.exists(sys_dll) else None
    say("INFO", f"DLL in program folder: {va or 'missing'} | DLL in System32 (from the SDK installer): {vs or 'missing'}")
    if va and vs and va != vs:
        say("WARN", "DLL versions differ: mvsdk.py loads the program-folder DLL first. Install SDK "
                    f"version {va}, or test the cameras carefully.")
    try:
        import mvsdk
        mvsdk.CameraEnumerateDevice
        say("PASS", f"mvsdk.py loaded the DLL ({mvsdk.CameraSdkGetVersionString() if hasattr(mvsdk, 'CameraSdkGetVersionString') else ''})")
        return mvsdk
    except Exception as exc:
        say("FAIL", f"mvsdk.py could not load the MindVision DLL: {exc}")
        return None


# ----------------------------------------------------------------------------- 3. cameras
def allowed_from_source():
    try:
        src = open(os.path.join(APP, "LAMTON_LINE_NEW_ESP32.py"), encoding="utf-8", errors="replace").read()
        uuid = re.search(r'ALLOWED_UUID\s*=\s*"([^"]+)"', src).group(1)
        cams = re.findall(r'"(\d{6,})"', re.search(r"ALLOWED_CAMERAS\s*=\s*\[([^\]]*)\]", src).group(1))
        return uuid, cams
    except Exception:
        return None, []


def check_cameras(mvsdk):
    head("3. CAMERAS (listed only - not opened, no setting changed)")
    cfg = load_json("camera_config.json")
    want = {role: str(cfg.get(role, {}).get("sn", "")) for role in ("FRONT", "REAR")}
    _, allowed = allowed_from_source()
    found = []
    if mvsdk is None:
        say("FAIL", "cannot list cameras (SDK not loaded)")
        return found
    try:
        devs = mvsdk.CameraEnumerateDevice()
    except Exception as exc:
        say("FAIL", f"camera enumeration failed: {exc}")
        return found
    for d in devs:
        sn = d.GetSn()
        found.append(sn)
        ip = ""
        try:
            cam_ip, _m, _g, eth_ip, _em, _eg = mvsdk.CameraGigeGetIp(d)
            ip = f"  camera IP {cam_ip}  (PC port {eth_ip})"
        except Exception:
            pass
        role = next((r for r, s in want.items() if s == sn), "not in camera_config.json")
        say("INFO", f"camera {d.GetProductName() or d.GetFriendlyName()}  SN {sn}  [{d.GetPortType()}]  -> {role}{ip}")
    say("INFO", f"{len(devs)} camera(s) found")
    for role, sn in want.items():
        say("PASS" if sn in found else "FAIL", f"{role:5} camera SN {sn} (camera_config.json) "
                                              f"{'found' if sn in found else 'NOT found'}")
    if allowed and set(allowed) != set(want.values()):
        say("WARN", f"hardware lock expects cameras {allowed}, camera_config.json has {list(want.values())}")
    return found


# ----------------------------------------------------------------------------- 4. disk
def check_disk(do_speed):
    head("4. DISK")
    free = shutil.disk_usage(APP).free / 1e9
    drive = os.path.splitdrive(APP)[0] or APP
    say("PASS" if free >= 20 else "WARN" if free >= 5 else "FAIL",
        f"free space on {drive}  {free:.1f} GB  (capture stops below 1 GB, strips below 3 GB; keep 20 GB+)")
    if not do_speed:
        say("INFO", "disk speed test skipped (--no-disk-speed)")
        return
    path = os.path.join(APP, "_system_check_speed.tmp")
    size_mb = 256
    if free * 1000 < size_mb * 3:
        say("WARN", "not enough free space for the speed test - skipped")
        return
    block = os.urandom(4 * 1024 * 1024)
    try:
        t0 = time.perf_counter()
        with open(path, "wb", buffering=0) as f:
            for _ in range(size_mb // 4):
                f.write(block)
            os.fsync(f.fileno())
        mbs = size_mb / (time.perf_counter() - t0)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    say("PASS" if mbs >= 100 else "WARN" if mbs >= 50 else "FAIL",
        f"write speed {mbs:.0f} MB/s on {drive} (256 MB test file, deleted)  - capture strips need 25-50 MB/s "
        "continuous; an internal SSD is recommended")


# ----------------------------------------------------------------------------- 5. ESP32 boards
EXPECTED_EJECTOR_FW = (2, 3)          # current ejector firmware: ESP32_63CH_Ejector_v2_3 (115200 baud, PIN_PROFILE 3 default)
ESP_USB_VIDS = {0x10C4: "CP210x", 0x1A86: "CH340/CH9102", 0x0403: "FTDI", 0x303A: "ESP32 USB"}


def modbus_crc(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def open_port(serial, port, baud):
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, baud, 0.05, 0.5
    s.dtr = False                      # do not reset the ESP32 when opening
    s.rts = False
    s.open()
    return s


def check_esp32():
    head("5. COM PORTS AND BOARDS (what every port really answers - same logic as the sorter)")
    try:
        import port_detect
    except Exception as exc:
        say("FAIL", f"port_detect.py / pyserial not available: {exc}")
        return
    cfg = load_json("system_config.json")
    ports = port_detect.list_ports()
    enabled = {d: port_detect.is_enabled(cfg, d) for d in port_detect.DEVICES}
    say("INFO", "system_config.json: " + ", ".join(
        f"{d} = {cfg.get(port_detect.CFG_PORT[d]) or '-'}{'' if enabled[d] else ' (DISABLED)'}" for d in port_detect.DEVICES))
    if not ports:
        say("FAIL", "no COM ports found on this PC")
    # 1) what does every port answer? (the feeder's saved port and disabled relay are never probed)
    feeder_port = cfg.get(port_detect.CFG_PORT["feeder"]) if enabled["feeder"] else None
    scan = port_detect.scan([p for p in ports if p.device != feeder_port], want_relay=enabled["relay"])
    for p in ports:
        r = scan.get(p.device)
        if r is None:
            say("INFO", f"{p.label}  -> not probed (saved feeder port: the feeder board never answers, nothing is sent)")
            continue
        level, text = "INFO", f"{r.kind}: {r.detail}"
        if r.kind in (port_detect.EJECTOR, port_detect.LEGACY):
            level = "PASS"
            if r.fw and r.fw < EXPECTED_EJECTOR_FW:
                level = "WARN"
                text += (f"  <- OLDER than the current v{EXPECTED_EJECTOR_FW[0]}.{EXPECTED_EJECTOR_FW[1]} "
                         "(see firmware\\README.txt; upload the current ejector firmware)")
            elif r.kind == port_detect.LEGACY:
                level = "WARN"
                text += f"  (rollback firmware; current = v{EXPECTED_EJECTOR_FW[0]}.{EXPECTED_EJECTOR_FW[1]})"
        elif r.kind == port_detect.RELAY:
            level = "PASS"
        elif r.kind in (port_detect.BUSY, port_detect.ERROR):
            level = "WARN"
            text += "  (close the sorter / Serial Monitor)"
        say(level, f"{p.label}  -> {text}")
    # 2) what would the sorter do at start-up with these settings?
    assigned, messages, new_values = port_detect.resolve(cfg, progress=lambda m: None)
    for d in port_detect.DEVICES:
        r = assigned[d]
        if not enabled[d]:
            say("INFO", f"{d}: disabled in the settings - the sorter never opens or probes a port for it")
        elif r is not None:
            ch = f" (saved port {cfg.get(port_detect.CFG_PORT[d])} is replaced and saved)" if port_detect.CFG_PORT[d] in new_values else ""
            say("PASS", f"{d}: the sorter will use {r.port}{ch}")
        elif d == "feeder":
            say("WARN", "feeder: no port chosen / port not present - choose it in COM PORTS (the feeder board cannot be detected)")
        else:
            say("FAIL", f"{d}: NOT found on any COM port - check the USB cable, power and the settings")
    for m in messages:
        if any(k in m for k in ("did not answer", "not present", "WARNING", "already used")):
            say("INFO", m)


# ----------------------------------------------------------------------------- 6. GPU
def check_gpu(ai_installed):
    head("6. GPU")
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=20)
        gpu = r.stdout.strip()
    except Exception:
        gpu = ""
    say("INFO", f"NVIDIA GPU: {gpu}" if gpu else "no NVIDIA GPU/driver found (not needed for sorting)")
    if not ai_installed:
        say("INFO", "AI packages not installed - GPU not used")
        return
    code = "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '-')"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=180)
    out = r.stdout.strip()
    if not out:
        say("WARN", "torch could not be loaded: " + (r.stderr.strip().splitlines() or ["?"])[-1][:150])
    elif " True " in f" {out} ":
        say("PASS", f"torch uses the GPU: {out}")
    else:
        say("INFO" if not gpu else "WARN", f"torch runs on CPU only: {out}"
            + (" (GPU present: install the CUDA build of torch, see INSTALL_NEW_PC.md)" if gpu else ""))


# ----------------------------------------------------------------------------- 7. licence
def check_licence(cams_found):
    head("7. LICENCE (read only)")
    allowed_uuid, allowed_cams = allowed_from_source()
    try:
        cwd = os.getcwd()
        import importlib.util
        spec = importlib.util.spec_from_file_location("sorter_main", os.path.join(APP, "LAMTON_LINE_NEW_ESP32.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)                      # module import only (window is not created)
        os.chdir(cwd)
        uuid = m.LicenseManager.get_pc_serial()
        os.chdir(APP)
        info = m.LicenseManager.get_license_info()
        os.chdir(cwd)
    except Exception as exc:
        say("FAIL", f"could not read the licence code: {exc}")
        return
    say("INFO", f"this PC's UUID: {uuid}")
    # developer-mode unlock files must never be on a customer PC
    unlock = sorted(f for f in os.listdir(APP) if f.lower().startswith("dev_unlock") and f.lower().endswith(".json"))
    dev_active = bool(getattr(m, "DeveloperMode", None) and m.DeveloperMode.is_active())
    if unlock and dev_active:
        say("WARN", f"DEVELOPER MODE active ({m.DeveloperMode.reason}): licence + hardware lock are NOT checked "
                    f"on this registered developer PC. Never copy {unlock} to a customer PC.")
    elif unlock:
        say("FAIL", f"developer unlock file {unlock} found on a NON-developer PC "
                    f"({m.DeveloperMode.reason if hasattr(m, 'DeveloperMode') else '?'}) - delete it from {APP}")
    else:
        say("PASS", "no developer unlock file (normal customer install)")
    uuid_ok = uuid.strip() == (allowed_uuid or "").strip()
    cams_ok = bool(allowed_cams) and all(c in cams_found for c in allowed_cams)
    say("PASS" if uuid_ok else "FAIL", f"start-up hardware lock, PC UUID: {'match' if uuid_ok else 'MISMATCH'} "
                                      f"(program expects {allowed_uuid})")
    say("PASS" if cams_ok else "FAIL", f"start-up hardware lock, cameras {allowed_cams}: "
                                      f"{'both connected' if cams_ok else 'NOT both connected'}")
    say("PASS" if info.get("valid") else "FAIL", f"licence key ({info.get('license_key', '')[:4]}-...): {info.get('status')}"
                                                 + (f", expires {info.get('expiry_date')}" if info.get('valid') else ""))
    if dev_active:
        say("INFO", "the sorter will start in DEVELOPER MODE on this PC (hardware lock skipped)")
    elif uuid_ok and cams_ok:
        say("PASS", "the sorter WILL start on this PC")
    else:
        say("FAIL", "the sorter will show 'Access Denied' and close on this PC (see INSTALL_NEW_PC.md, Licence)")


def main():
    ap = argparse.ArgumentParser(description="Read-only system check for the LAMTON sorter")
    ap.add_argument("--no-disk-speed", action="store_true", help="skip the 256 MB write-speed test")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(f"LAMTON SYSTEM CHECK   {time.strftime('%Y-%m-%d %H:%M')}   program folder: {APP}")
    ver = os.path.join(PKG, "VERSION.txt")
    if os.path.exists(ver):
        print("Package: " + open(ver, encoding="utf-8").readline().strip())
    ai = check_python()
    mvsdk = check_mindvision()
    cams = check_cameras(mvsdk)
    check_disk(not a.no_disk_speed)
    check_esp32()
    check_gpu(ai)
    check_licence(cams)
    head("SUMMARY")
    n = {k: RESULTS.count(k) for k in ("PASS", "WARN", "FAIL")}
    print(f" PASS {n['PASS']}   WARN {n['WARN']}   FAIL {n['FAIL']}")
    print(" FAIL = must be fixed before production.  WARN = check it.  Nothing was changed on this PC.")
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
