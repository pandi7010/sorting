"""
tools/test_baud_230400_sim.py - 18 Nov 2026: the PC finds the ejector ESP32 at 230400 baud (esp.ino byte firmware,
user's decision) and still at 115200 (boards not yet re-flashed). Simulated serial port, no hardware.

    python tools/test_baud_230400_sim.py      -> must print  ALL TESTS PASSED
STILL NEEDS THE REAL MACHINE: esp.ino with Serial.begin(230400) uploaded to the ejector ESP32.
"""
import os, sys, time, types, threading
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import serial.tools.list_ports  # noqa: F401
except Exception:
    for n in ("serial", "serial.tools", "serial.tools.list_ports"):
        m = types.ModuleType(n); m.__path__ = []; sys.modules[n] = m
    sys.modules["serial"].SerialException = type("SerialException", (Exception,), {})
    sys.modules["serial"].Serial = object
    sys.modules["serial.tools.list_ports"].comports = lambda: []

import ejector_link as el
import port_detect as pd

fails = []
def check(n, c, i=""):
    print(f"  [{'OK  ' if c else 'FAIL'}] {n} {i}")
    if not c:
        fails.append(n)


class FakeEspByte:
    """esp.ino byte firmware: answers 'P' with ESP32_ALIVE - only when the PC uses the board's baud rate."""
    opens = []

    def __init__(self, board_baud, baud):
        self.board_baud, self.baud = board_baud, baud
        self.rx = bytearray(); self.lock = threading.Lock(); self.is_open = True; self.written = bytearray()
        FakeEspByte.opens.append(baud)

    @property
    def in_waiting(self):
        with self.lock:
            return len(self.rx)

    def write(self, b):
        self.written += b
        if self.baud == self.board_baud and b"P" in bytes(b):
            with self.lock:
                self.rx += b"ESP32_ALIVE\r\n"
        return len(b)

    def read(self, n=1):
        time.sleep(0.005)
        with self.lock:
            out = bytes(self.rx[:n]); del self.rx[:n]
        return out

    def flush(self): pass
    def reset_input_buffer(self):
        with self.lock:
            self.rx.clear()
    def close(self): self.is_open = False


def link_for(board_baud):
    FakeEspByte.opens = []
    lk = el.EjectorLink("COM24", heartbeat=False)
    lk.serial_factory = lambda port, baud: FakeEspByte(board_baud, baud)
    t0 = time.time()
    mode = lk.connect(boot_wait_s=0.6)
    return lk, mode, time.time() - t0


print("EjectorLink.connect")
check("ESP_BYTE_BAUD is 230400", el.ESP_BYTE_BAUD == 230400)
lk, mode, dt = link_for(230400)
check("board at 230400 -> ESP_BYTE mode at 230400", mode == "ESP_BYTE" and lk.baud == 230400, f"{mode} {lk.baud}")
check("230400 tried first (no other baud sent to the board)", FakeEspByte.opens == [230400], str(FakeEspByte.opens))
lk.close()
lk, mode, dt = link_for(115200)
check("board still at 115200 -> found at 115200", mode == "ESP_BYTE" and lk.baud == 115200, f"{mode} {lk.baud}")
check("   order 230400 then 115200", FakeEspByte.opens[:2] == [230400, 115200], str(FakeEspByte.opens))
check("   extra time for the old board is small", dt < 4.0, f"{dt:.1f} s")
lk.close()
lk, mode, dt = link_for(57600)
check("board at an unknown baud -> not connected", mode is None, str(mode))
check("   message names 230400", any("230400" in l for l in lk.text_lines), str(list(lk.text_lines)[-1:]))

print("port_detect.probe_ejector (COM PORTS / start-up)")
pd.TIMING.update({"ejector": 0.3, "ejector_old": 0.2, "open_settle": 0.0})
for board in (230400, 115200):
    FakeEspByte.opens = []
    pd.serial_factory = lambda port, baud, b=board: FakeEspByte(b, baud)
    r = pd.probe_ejector("COM24")
    check(f"board at {board} -> EJECTOR at {board}", r is not None and r.kind == pd.EJECTOR and r.baud == board,
          str(r))
pd.serial_factory = None

print("\nALL TESTS PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
