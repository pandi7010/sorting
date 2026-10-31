"""
Offline self-test of the ejector protocol (no hardware needed).

    python tools\\test_ejector_protocol.py

Checks: CRC algorithm, frame sizes, 63-channel mask, parser with mixed text,
corrupted bytes and split reads, and that legacy FIRE_MULTI lines stay short.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ejector_link as el  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures += 1


# 1. CRC-16/CCITT-FALSE standard check value
check("CRC check value 0x29B1", el.crc16_ccitt(b"123456789") == 0x29B1, hex(el.crc16_ccitt(b"123456789")))

# 2. FIRE message is the same size for 1 and 63 channels (the truncation fix)
f1 = el.build_frame(el.T_FIRE_NOW, 7, struct.pack("<QII", el.channels_to_mask([20]), 4000, 0))
f63 = el.build_frame(el.T_FIRE_NOW, 8, struct.pack("<QII", el.channels_to_mask(range(1, 64)), 4000, 0))
check("FIRE frame 1 ch = 24 bytes", len(f1) == 24, len(f1))
check("FIRE frame 63 ch = 24 bytes", len(f63) == 24, len(f63))
check("63-channel mask", el.channels_to_mask(range(1, 64)) == (1 << 63) - 1)
check("mask round trip", el.mask_to_channels(el.channels_to_mask([1, 17, 63])) == [1, 17, 63])
print("     example frame (ch 3+17, 4 ms):",
      el.build_frame(el.T_FIRE_NOW, 7, struct.pack("<QII", el.channels_to_mask([3, 17]), 4000, 0)).hex(" ").upper())

# 3. Parser: text + frames + garbage + corrupted frame, delivered in 1-byte pieces
p = el.FrameParser()
ack = el.build_frame(el.T_ACK, 1, struct.pack("<HBBI", 7, 0, 1, 123456))
bad = bytearray(el.build_frame(el.T_ACK, 2, struct.pack("<HBBI", 8, 0, 1, 1)))
bad[8] ^= 0xFF                                            # corrupt one payload byte
stream = b"LAMBTON EJECTOR v2.0 READY\r\n" + ack + b"\x00\x13" + bytes(bad) + b"[TEST] CH 1 -> OUT 0\n" + ack
frames, lines = [], []
for i in range(len(stream)):
    fr, ln = p.feed(stream[i:i + 1])
    frames += fr
    lines += ln
check("parser frames", len(frames) == 2, len(frames))
check("parser text lines", lines == ["LAMBTON EJECTOR v2.0 READY", "[TEST] CH 1 -> OUT 0"], lines)
check("parser counted CRC error", p.crc_errors == 1, p.crc_errors)
t, seq, pl = frames[0]
check("ACK decode", (t, struct.unpack("<HBBI", pl)) == (el.T_ACK, (7, 0, 1, 123456)))

# 4. Payload sizes match firmware expectations
sizes = {
    "HEARTBEAT": (struct.calcsize("<BI"), 5), "FIRE_NOW": (struct.calcsize("<QII"), 16),
    "CONFIG": (struct.calcsize("<IIHH"), 12), "CMD": (struct.calcsize("<BH"), 3),
    "TEST": (struct.calcsize("<BBBIIH"), 13), "STATUS": (3 + 4 * len(el.STATUS_FIELDS), 51),
}
for name, (got, want) in sizes.items():
    check(f"payload size {name}", got == want, f"{got} (firmware expects {want})")


# 5. Legacy fallback splits long channel lists
class FakeSer:
    def __init__(self):
        self.out = b""
        self.is_open = True

    def write(self, d):
        self.out += d


link = el.EjectorLink("COMX", heartbeat=False)
link.ser = FakeSer()
link.mode = "LEGACY"
link.fire(range(1, 64), 4)
legacy_lines = link.ser.out.decode().strip().split("\n")
check("legacy lines < 60 chars", all(len(l) < 60 for l in legacy_lines), [len(l) for l in legacy_lines])
sent = sorted(int(c) for l in legacy_lines for c in l.split(":")[1].split(","))
check("legacy all 63 channels sent", sent == list(range(1, 64)))

# 6. Chip / bit mapping
check("OUT 0 -> chip 1 Q0", el.out_to_chip(0) == (1, 0))
check("OUT 62 -> chip 8 Q6", el.out_to_chip(62) == (8, 6))

print("\nALL TESTS PASSED" if failures == 0 else f"\n{failures} TEST(S) FAILED")
sys.exit(1 if failures else 0)
