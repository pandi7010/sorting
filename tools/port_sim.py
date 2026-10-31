"""Simulated serial boards for the port-detection tests (no hardware): ejector v2.3 / v2.2 (921600) / OLD text firmware /
relay (old + v2.x) / silent feeder / busy port. World(devices).install() plugs them into port_detect."""
import os
import struct
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ejector_link as el  # noqa: E402
import port_detect as pd  # noqa: E402


def crc(b):
    return pd._crc16(b)


class FakeSerial:
    """One simulated board behind a port name; records every byte written to it."""

    def __init__(self, world, port, baud):
        self.world, self.port, self.baud = world, port, baud
        self.kind = world.devices.get(port)
        self.rx = bytearray()
        self.buf = bytearray()
        world.opens.append((port, baud))
        if self.kind == "busy":
            raise pd._Busy("Access is denied")

    def reset_input_buffer(self):
        self.rx.clear()

    def read(self, n=1):
        if self.rx:
            d = bytes(self.rx[:n])
            del self.rx[:n]
            return d
        time.sleep(0.003)
        return b""

    def close(self):
        pass

    def write(self, data):
        self.world.written.setdefault(self.port, []).append((self.baud, bytes(data)))
        k = self.kind
        if k in ("ejector23", "ejector22", "legacy"):
            self.buf += data
            if b"PING\n" in self.buf:
                self.buf.clear()
                if k == "ejector23" and self.baud == 115200:
                    self.rx += b"ACK:PING PONG:LAMTON_EJECTOR_V2.3 P3 ID:AABBCCDDEEFF\n"
                elif k == "ejector22" and self.baud == 921600:
                    self.rx += b"ACK:PING PONG:LAMTON_EJECTOR_V2.2\n"
                elif k == "legacy" and self.baud == 115200:
                    self.rx += b"ACK:PING\n"
            if k == "ejector23" and self.baud == 115200 and data[:2] == b"\xA5\x5A" and data[3] == el.T_IDENTIFY_REQ:
                self.rx += el.build_frame(el.T_IDENTIFY, 5, b"EJECTOR\0" + struct.pack("<H", 0x0203) + bytes([3, 0])
                                          + bytes.fromhex("AABBCCDDEEFF"))
        elif k in ("relay_old", "relay_v2") and self.baud == 9600 and len(data) >= 8 and data[0] == 1:
            if crc(data[:6]) != data[6:8]:
                return len(data)
            if data[1] == 3 and k == "relay_v2" and data[2:4] == b"\x00\x0b":
                p = bytes([1, 3, 2, 0x02, 0x00])
                self.rx += p + crc(p)
            elif data[1] == 6:
                if k == "relay_old":
                    self.rx += data[:8]
                else:
                    p = bytes([1, 0x86, 2])
                    self.rx += p + crc(p)
        return len(data)


class World:
    def __init__(self, devices):
        self.devices, self.opens, self.written = devices, [], {}

    def install(self):
        pd.serial_factory = lambda port, baud: FakeSerial(self, port, baud)
        pd.comports_func = lambda: [type("P", (), dict(device=d, description=f"USB Serial ({d})", vid=0x10C4, pid=0xEA60,
                                                       manufacturer="Silicon Labs", hwid=""))() for d in self.devices]

    def modbus_bytes_to(self, port):
        return [w for baud, w in self.written.get(port, []) if baud == 9600]

    def opened_at_9600(self):
        return {p for p, b in self.opens if b == 9600}
