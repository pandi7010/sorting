"""
make_dev_unlock.py - creates dev_unlock.json (DEVELOPER MODE) on the developer's own PC.

    python tools\\make_dev_unlock.py            create / refresh it
    python tools\\make_dev_unlock.py --remove   delete it (back to the normal licence + hardware lock)

Developer mode needs BOTH this file AND this PC's UUID in DEV_UUIDS in LAMTON_LINE_NEW_ESP32.py,
so the file is useless on any other PC. It is in .gitignore; make_deploy_package.py refuses to build a
package that would contain it, and SYSTEM_CHECK reports FAIL if one is found on a customer PC.
The sorter's normal licence / hardware lock is not changed by this tool.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(APP, "dev_unlock.json")


def hardware_uuid():
    """Same source as DeveloperMode.hardware_uuid() in the sorter (WMI / CIM, not the registry)."""
    for cmd in (["wmic", "csproduct", "get", "uuid"],
                ["powershell", "-NoProfile", "-Command", "(Get-CimInstance -ClassName Win32_ComputerSystemProduct).UUID"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
        except Exception:
            continue
        for line in out.splitlines():
            line = line.strip()
            if line and "UUID" not in line.upper() and line != "00000000-0000-0000-0000-000000000000":
                return line
    return None


def dev_uuids():
    src = open(os.path.join(APP, "LAMTON_LINE_NEW_ESP32.py"), encoding="utf-8", errors="replace").read()
    m = re.search(r"^DEV_UUIDS\s*=\s*\(([^)]*)\)", src, re.M)
    return [u.upper() for u in re.findall(r'"([0-9A-Fa-f-]{20,})"', m.group(1))] if m else []


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args()
    if a.remove:
        if os.path.exists(FILE):
            os.remove(FILE)
            print(f"removed {FILE} - the sorter uses the normal licence + hardware lock again")
        else:
            print("no dev_unlock.json - nothing to do")
        return 0
    uuid = hardware_uuid()
    if not uuid:
        print("ERROR: this PC's hardware UUID could not be read")
        return 1
    if uuid.upper() not in dev_uuids():
        print(f"REFUSED: this PC ({uuid}) is not in DEV_UUIDS in LAMTON_LINE_NEW_ESP32.py.\n"
              "Developer mode works only on a registered developer PC. Nothing was written.")
        return 1
    data = {"uuid": uuid, "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "pc": os.environ.get("COMPUTERNAME", "?"),
            "note": "DEVELOPER MODE unlock for this PC only. Never copy to a customer PC."}
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    print(f"written {FILE}\nUUID {uuid} - the sorter starts in DEVELOPER MODE on this PC.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
