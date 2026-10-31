"""
make_deploy_package.py - builds the LAMTON V2 deployment package from the COMMITTED version.

    python deploy\\make_deploy_package.py D:\\LAMTON_V2_DEPLOY

- refuses to run with uncommitted changes (the package must match a git commit)
- refuses to write into an existing folder (never overwrites)
- app\\              git archive of HEAD, without development/licence-generator files
- firmware\\         ejector v2.3 (default PIN_PROFILE 3, checked against the Arduino copy), relay v2.0, rollback firmware
- ejector_test_kit\\ standalone test panel kit
- tools\\            .bat launchers for the tools in app\\tools
- drivers\\          installers found on this PC (MindVision SDK, CP210x, Arduino IDE, Python 3.12)
- INSTALL.bat, START_SORTER.bat, SYSTEM_CHECK.bat, MAKE_OFFLINE_WHEELS.bat, requirements*, docs
- VERSION.txt (commit, date) and MANIFEST.sha256 (checksum of every file)
"""
import argparse
import datetime
import fnmatch
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile

DEPLOY = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(DEPLOY)
GIT = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"

# files/folders of the repository that do NOT go into app\
EXCLUDE = [
    "LICENCE_BOSS.py", "manufacturer_data_v3.db",      # licence-key generator + customer database
    "fix.py", "patch.py", "scratch_*.py", "scratch/*",  # old one-off development scripts (d:\ paths)
    "ESP32_*/*", "OLD_ESP32_CODE/*",                    # firmware -> firmware\
    "deploy/*",                                         # these templates
    "CLAUDE.md", ".gitignore", "requirements.txt", "INSTALL_NEW_PC.md",   # old root copies (package root has new ones)
    "firmware/*",                                       # ready-to-open Arduino copies live in the DEV folder only
    "create_shortcut.vbs", "SETUP_AUTOSTART.bat",       # autostart to d:\LAMTON_V1 / to a non-existing .exe
    "BUILD_STANDALONE_EXE.bat", "BUILD_STANDALONE_EXE_v2.bat", "LAMTON_COLOUR_SORTER.spec",  # build PC only
]

PIN1_OLD = "#define PIN_PROFILE        2         // 2 = new safe pins (recommended), 1 = legacy wiring"
PIN1_NEW = "#define PIN_PROFILE        1         // ARDUINO-IDE COPY: legacy wiring"
UPLOADED_COPY = os.path.expanduser(r"~\Documents\Arduino\ESP32_63CH_Ejector_v2_3\ESP32_63CH_Ejector_v2_3.ino")

DOWNLOADS = os.path.expanduser(r"~\Downloads")
DRIVERS = [  # (source, name in drivers\)
    (os.path.join(DOWNLOADS, "MindVision Camera Platform Setup(2.1.10.195_202604021438).exe"), None),
    (os.path.join(DOWNLOADS, "CP210x_Universal_Windows_Driver.zip"), None),
    (os.path.join(DOWNLOADS, "SLAB_License_Agreement_VCP_Windows.txt"), "CP210x_SLAB_License_Agreement.txt"),
    (os.path.join(DOWNLOADS, "arduino-ide_2.3.10_Windows_64bit.exe"), None),
]

TOOL_BATS = {  # name: (script in app\tools, note)
    "CAMERA_PROBE.bat": ("camera_probe.py", "READ-ONLY camera report + settings BACKUP (app\\camera_backups\\). "
                                            "Run it FIRST, before any camera change. Close the sorter first."),
    "CAMERA_RESTORE.bat": ("camera_restore.py", "Restores camera settings from a backup made by CAMERA_PROBE. "
                                                "Usage: CAMERA_RESTORE.bat <backup .config file> [--save-in-camera]"),
    "BALL_SPEED.bat": ("ball_speed.py", "Measures product speed with 10 mm balls (needs cameras). "
                                        "Try without hardware: BALL_SPEED.bat --simulate"),
    "LATENCY_REPORT.bat": ("latency_report.py", "Latency report from timing logs (switch timing_log_enabled "
                                                "ON in app\\features_config.json, run the sorter, then this)."),
    "DISK_SPEED_TEST.bat": ("disk_speed_test.py", "Disk write speed for dataset capture. "
                                                  "Example: DISK_SPEED_TEST.bat --dirs D:\\capture"),
    "SYSTEM_CHECK_ONLY.bat": ("system_check.py", "Same as ..\\SYSTEM_CHECK.bat (read-only PASS/FAIL check)."),
}

DEV_UNLOCK = "dev_unlock*.json"   # never in a package
KIT_FILES = ["ejector_test_panel.py", "ejector_link.py", "port_detect.py", "tools/ejector_sim.py"]
TEXT_EXT = (".bat", ".txt", ".md")


def git(*args, binary=False):
    r = subprocess.run([GIT, "-C", REPO] + list(args), capture_output=True)
    if r.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace')}")
    return r.stdout if binary else r.stdout.decode("utf-8", errors="replace").strip()


def excluded(path):
    return any(fnmatch.fnmatch(path, pat) for pat in EXCLUDE)


def write_text(dst, text):
    """Text files for Windows users get CRLF line endings."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    text = text.replace("\r\n", "\n")
    if dst.lower().endswith(TEXT_EXT):
        text = text.replace("\n", "\r\n")
    with open(dst, "w", encoding="utf-8-sig" if dst.endswith("_TAMIL.md") or dst.endswith("_TAMIL.txt") else "utf-8",
              newline="") as f:
        f.write(text)


def copy_text(src, dst):
    write_text(dst, open(src, encoding="utf-8-sig").read())


def blob(path):
    return git("show", f"HEAD:{path}", binary=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="new folder, e.g. D:\\LAMTON_V2_DEPLOY")
    ap.add_argument("--python-installer", help="python-3.12.10-amd64.exe to put into drivers\\")
    a = ap.parse_args()
    out = os.path.abspath(a.target)

    if git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("Uncommitted changes in tracked files - commit first (the package must match a commit).")
    if os.path.exists(out):
        raise SystemExit(f"{out} already exists - choose a new folder (this script never overwrites).")
    # Developer-mode unlock files must never reach a customer: refuse if one is tracked in git.
    tracked_unlock = [f for f in git("ls-files").splitlines() if fnmatch.fnmatch(os.path.basename(f).lower(), DEV_UNLOCK)]
    if tracked_unlock:
        raise SystemExit(f"REFUSED: developer unlock file(s) tracked in git: {tracked_unlock} - remove them first.")
    commit = git("rev-parse", "HEAD")
    print(f"Building {out} from commit {commit[:7]} ...")

    # ---- app\ (git archive of HEAD) ----
    tar = tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", "HEAD", binary=True)))
    skipped = []
    n_cnn = 0
    for m in tar.getmembers():
        if not m.isfile():
            continue
        if excluded(m.name):
            skipped.append(m.name)
            continue
        if m.name.startswith("cnn/"):
            dst = os.path.join(out, *m.name.split("/"))                 # cnn\ at the package root
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            data = tar.extractfile(m).read()
            if dst.lower().endswith(TEXT_EXT):                          # .bat / .txt / .md: Windows line endings
                write_text(dst, data.decode("utf-8"))
            else:
                open(dst, "wb").write(data)
            n_cnn += 1
            continue
        dst = os.path.join(out, "app", *m.name.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(tar.extractfile(m).read())
    print(f"  app\\: {sum(1 for m in tar.getmembers() if m.isfile()) - len(skipped) - n_cnn} files "
          f"({len(skipped)} left out); cnn\\: {n_cnn} files")

    # ---- firmware\ ----
    fw = os.path.join(out, "firmware")
    ej = blob("ESP32_63CH_Ejector_v2_3/ESP32_63CH_Ejector_v2_3.ino")           # default PIN_PROFILE 3, used as it is
    p = os.path.join(fw, "ESP32_63CH_Ejector_v2_3", "ESP32_63CH_Ejector_v2_3.ino")
    os.makedirs(os.path.dirname(p))
    open(p, "wb").write(ej)
    if os.path.exists(UPLOADED_COPY):
        same = sha256(p) == sha256(UPLOADED_COPY)
        print(f"  ejector v2.3 == Arduino copy ({UPLOADED_COPY}): {same}")
        if not same:
            raise SystemExit("ejector firmware differs from the uploaded file - stop")
    for src, dst in [("ESP32_8CH_ModbusRelay_v2_0/ESP32_8CH_ModbusRelay_v2_0.ino", "ESP32_8CH_ModbusRelay_v2_0"),
                     ("ESP32_63CH_ShiftRegister/ESP32_63CH_ShiftRegister.ino", "ROLLBACK_OLD/ESP32_63CH_ShiftRegister"),
                     ("ESP32_63CH_Ejector_v2_0/ESP32_63CH_Ejector_v2_0.ino", "ROLLBACK_OLD/ESP32_63CH_Ejector_v2_0"),
                     ("ESP32_63CH_Ejector_v2_1/ESP32_63CH_Ejector_v2_1.ino", "ROLLBACK_OLD/ESP32_63CH_Ejector_v2_1"),
                     ("ESP32_63CH_Ejector_v2_2/ESP32_63CH_Ejector_v2_2.ino", "ROLLBACK_OLD/ESP32_63CH_Ejector_v2_2"),
                     ("ESP32_8CH_ModbusRelay_NEW/ESP32_8CH_ModbusRelay_NEW.ino", "ROLLBACK_OLD/ESP32_8CH_ModbusRelay_NEW")]:
        d = os.path.join(fw, *dst.split("/"), os.path.basename(src))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        open(d, "wb").write(blob(src))
    copy_text(os.path.join(DEPLOY, "firmware_README.txt"), os.path.join(fw, "README.txt"))

    # ---- ejector_test_kit\ ----
    kit = os.path.join(out, "ejector_test_kit")
    for f in KIT_FILES:
        d = os.path.join(kit, *f.split("/"))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        open(d, "wb").write(blob(f))
    for f in os.listdir(os.path.join(DEPLOY, "ejector_test_kit")):
        copy_text(os.path.join(DEPLOY, "ejector_test_kit", f), os.path.join(kit, f))
    shutil.copytree(os.path.join(fw, "ESP32_63CH_Ejector_v2_3"), os.path.join(kit, "firmware", "ESP32_63CH_Ejector_v2_3"))
    shutil.copytree(os.path.join(fw, "ROLLBACK_OLD", "ESP32_63CH_ShiftRegister"),
                    os.path.join(kit, "firmware", "ESP32_63CH_ShiftRegister"))

    # ---- tools\ (launchers for app\tools) ----
    lines = ["LAMTON V2 - TOOLS", "", "The tool programs are in app\\tools (one copy, always matching the app).",
             "These .bat files start them with the package Python (.venv). Arguments are passed on.", ""]
    for name, (script, note) in TOOL_BATS.items():
        write_text(os.path.join(out, "tools", name), "\n".join([
            "@echo off", "setlocal", f"title LAMTON V2 - {name[:-4]}", f"REM {note}",
            'cd /d "%~dp0..\\app"', 'set "PY=%~dp0..\\.venv\\Scripts\\python.exe"',
            'if not exist "%PY%" (', "    echo Run INSTALL.bat first.", "    pause", "    exit /b 1", ")",
            f'"%PY%" tools\\{script} %*', "echo.", "pause", ""]))
        lines.append(f"{name:22} {note}")
    write_text(os.path.join(out, "tools", "README.txt"), "\n".join(lines) + "\n")

    # ---- root files ----
    for f in ["INSTALL.bat", "START_SORTER.bat", "SYSTEM_CHECK.bat", "MAKE_OFFLINE_WHEELS.bat",
              "requirements.txt", "requirements_cnn.txt", "constraints.txt", "README_FIRST.txt",
              "INSTALL_NEW_PC.md", "INSTALL_NEW_PC_TAMIL.md", "UPGRADE_TO_GPU.bat", "UPGRADE_TO_GPU.py",
              "UPGRADE_TO_GPU.md", "UPGRADE_TO_GPU_TAMIL.md"]:
        copy_text(os.path.join(DEPLOY, f), os.path.join(out, f))

    # ---- drivers\ ----
    drv = os.path.join(out, "drivers")
    os.makedirs(drv)
    srcs = DRIVERS + ([(a.python_installer, None)] if a.python_installer else [])
    missing = []
    for src, name in srcs:
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(drv, name or os.path.basename(src)))
        else:
            missing.append(src)
    for m in missing:
        print(f"  WARNING: driver/installer not found, not included: {m}")

    # ---- VERSION.txt + MANIFEST.sha256 ----
    date = git("show", "-s", "--format=%ci", "HEAD")
    subject = git("show", "-s", "--format=%s", "HEAD")
    write_text(os.path.join(out, "VERSION.txt"), "\n".join([
        f"LAMTON V2 deployment package - commit {commit}",
        f"commit date : {date}",
        f"commit      : {subject}",
        f"built       : {datetime.datetime.now():%Y-%m-%d %H:%M} on {os.environ.get('COMPUTERNAME', '?')}",
        f"source repo : {REPO}",
        "",
        "Left out of app\\ (development / licence generator / firmware in firmware\\):",
        *[f"  {s}" for s in skipped],
        "",
        "Drivers not found when building:" if missing else "All driver installers included.",
        *[f"  {m}" for m in missing], ""]))
    files = []
    for root, _dirs, names in os.walk(out):
        for n in names:
            files.append(os.path.relpath(os.path.join(root, n), out))
    leaked = [f for f in files if fnmatch.fnmatch(os.path.basename(f).lower(), DEV_UNLOCK)]
    if leaked:   # second safety net: the finished package itself
        raise SystemExit(f"REFUSED: the package would contain developer unlock file(s) {leaked} - "
                         f"delete {out} and check the sources.")
    with open(os.path.join(out, "MANIFEST.sha256"), "w", encoding="utf-8", newline="\r\n") as f:
        for rel in sorted(files):
            f.write(f"{sha256(os.path.join(out, rel))}  {rel}\n")
    size = sum(os.path.getsize(os.path.join(out, r)) for r in files)
    print(f"DONE: {len(files) + 1} files, {size / 1e6:.1f} MB  ->  {out}")


if __name__ == "__main__":
    main()
