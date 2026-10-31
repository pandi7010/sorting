"""
Tests port_detect.py with SIMULATED boards on different COM numbers (no hardware, nothing can fire):
ejector v2.3 / v2.2 (921600) / OLD text firmware / relay (old + v2.x firmware) / silent feeder / busy port.

    python tools\\test_port_detect_sim.py
"""
import os
import struct
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import ejector_link as el  # noqa: E402
import port_detect as pd  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures += 1


pd.TIMING.update({"ejector": 0.7, "ejector_old": 0.4, "relay": 0.3, "open_settle": 0.0})


from port_sim import FakeSerial, World, crc  # noqa: E402


def run(devices, cfg):
    w = World(devices)
    w.install()
    msgs = []
    assigned, messages, new = pd.resolve(cfg, progress=lambda m: None)
    return w, assigned, messages, new


# ---------------------------------------------------------------- 1. ejector COM13 + relay COM24, saved ports SWAPPED
w, a, m, new = run({"COM13": "ejector23", "COM24": "relay_old"}, {"ejector_port": "COM24", "last_port": "COM13"})
check("swapped: ejector found on COM13", a["ejector"] and a["ejector"].port == "COM13" and a["ejector"].kind == pd.EJECTOR, str(a["ejector"]))
check("swapped: relay found on COM24", a["relay"] and a["relay"].port == "COM24" and a["relay"].kind == pd.RELAY, str(a["relay"]))
check("swapped: new ports to save", new == {"ejector_port": "COM13", "last_port": "COM24"}, str(new))
check("swapped: messages tell what happened", any("FOUND on COM13" in x for x in m) and any("FOUND on COM24" in x for x in m))
check("NO Modbus byte ever sent to the ejector port", w.modbus_bytes_to("COM13") == [] and "COM13" not in w.opened_at_9600())
check("ejector identity read (profile 3, board ID)", a["ejector"].profile == 3 and a["ejector"].id == "AABBCCDDEEFF", a["ejector"].detail)

# ---------------------------------------------------------------- 2. the same with other numbers, saved ports correct -> no scan changes
w, a, m, new = run({"COM5": "relay_v2", "COM31": "ejector23"}, {"ejector_port": "COM31", "last_port": "COM5"})
check("correct saved ports: both kept", a["ejector"].port == "COM31" and a["relay"].port == "COM5" and new == {}, f"{new}")
check("relay v2.x firmware version read", a["relay"].fw == (2, 0), a["relay"].detail)

# ---------------------------------------------------------------- 3. ejector alone, relay enabled but not connected
w, a, m, new = run({"COM13": "ejector23"}, {"ejector_port": "COM24", "last_port": "COM13", "modbus_enabled": True})
check("ejector alone: ejector found, relay not assigned", a["ejector"].port == "COM13" and a["relay"] is None, str(a))
check("ejector alone: relay saved on the ejector port is refused", any("relay: saved port COM13" in x and "EJECTOR" in x for x in m), str(m))
check("ejector alone: no Modbus bytes to the ejector", w.modbus_bytes_to("COM13") == [])

# ---------------------------------------------------------------- 4. relay disabled: nothing is opened/probed for it
w, a, m, new = run({"COM13": "ejector23", "COM24": "relay_old"}, {"ejector_port": "COM24", "last_port": "COM24", "modbus_enabled": False})
check("modbus_enabled=false: relay never probed (nothing opened at 9600)", w.opened_at_9600() == set(), str(w.opened_at_9600()))
check("modbus_enabled=false: relay is None, message says disabled", a["relay"] is None and any("relay: disabled" in x for x in m))
check("modbus_enabled=false: ejector still found on COM13", a["ejector"] and a["ejector"].port == "COM13")

# ---------------------------------------------------------------- 5. feeder: never probed, saved port kept
w, a, m, new = run({"COM13": "ejector23", "COM7": "feeder"}, {"ejector_port": "COM13", "vibrator_port": "COM7"})
check("feeder: saved port accepted without sending anything", a["feeder"] and a["feeder"].port == "COM7" and w.written.get("COM7") is None, str(w.written.get("COM7")))
w, a, m, new = run({"COM9": "ejector23", "COM7": "feeder"}, {"ejector_port": "COM1", "vibrator_port": "COM7"})
check("feeder: not touched while the ejector is searched", a["ejector"].port == "COM9" and w.written.get("COM7") is None)
w, a, m, new = run({"COM9": "feeder"}, {"vibrator_port": "COM4"})
check("feeder: a missing saved port is NOT replaced by a guess", a["feeder"] is None and any("feeder" in x for x in m))

# ---------------------------------------------------------------- 6. other firmware kinds
w, a, m, new = run({"COM5": "legacy"}, {})
check("legacy text ejector recognised at 115200", a["ejector"] and a["ejector"].kind == pd.LEGACY, str(a["ejector"]))
w, a, m, new = run({"COM6": "ejector22"}, {})
check("older v2.2 ejector (921600) recognised and flagged", a["ejector"] and a["ejector"].kind == pd.EJECTOR and a["ejector"].baud == 921600
      and "OLDER" in a["ejector"].detail, a["ejector"].detail if a["ejector"] else "")
w, a, m, new = run({"COM8": "busy", "COM13": "ejector23"}, {})
check("busy port reported, other port still found", a["ejector"].port == "COM13" and w.written.get("COM8") is None)
check("busy port -> BUSY result", pd.probe_ejector("COM8").kind == pd.BUSY)

# ---------------------------------------------------------------- 7. ejector_link tells v2.3 and legacy apart by protocol (same 115200)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ejector_sim import SimEsp32  # noqa: E402
link = el.EjectorLink("COM77")
link.serial_factory = SimEsp32.factory
mode = link.connect()
check("EjectorLink.connect: v2.3 board -> BINARY at 115200", mode == "BINARY" and link.baud == 115200 and link.fw_version == "2.3", f"{mode} {link.baud} {link.fw_version}")
check("EjectorLink: IDENTIFY answered (device, profile, id)", link.identity.get("device") == "EJECTOR" and link.identity.get("profile") == 3
      and link.identity.get("id") == "AABBCCDDEEFF", str(link.identity))
link.close()


class LegacyOnly:
    """OLD text firmware behind EjectorLink: answers only 'ACK:PING' at 115200."""

    def __init__(self, port, baud):
        self.baud, self.is_open, self.out, self.got = baud, True, bytearray(), bytearray()
        self.in_waiting = 0

    def read(self, n=1):
        if self.out:
            d = bytes(self.out[:n]); del self.out[:n]; return d
        time.sleep(0.003); return b""

    def write(self, d):
        self.got += d
        if self.baud == 115200 and b"PING\n" in self.got:
            self.got.clear(); self.out += b"ACK:PING\n"
        return len(d)

    def close(self):
        self.is_open = False


holder = {}
link = el.EjectorLink("COM78")
link.serial_factory = lambda port, baud: holder.setdefault(baud, LegacyOnly(port, baud))
mode = link.connect()
check("EjectorLink.connect: OLD text firmware -> LEGACY (not taken for v2.x)", mode == "LEGACY" and not any(b"\xA5\x5A" in bytes(h.got) for h in holder.values()),
      f"{mode}")
link.close()

print()
print("ALL TESTS PASSED" if failures == 0 else f"{failures} TEST(S) FAILED")
sys.exit(1 if failures else 0)
