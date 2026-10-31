"""
End-to-end test of the PC side against a SIMULATED v2.0 ejector (no hardware).

    python tools\\test_ejector_link_sim.py

The simulator answers like ESP32_63CH_Ejector_v2_0: ACK, STATUS, REMAP, TEST_STEP
events and the 500 ms watchdog. It tests ejector_link.py and the ESP32Controller
methods used by the sorter and the in-app ejector test window.
"""
import os
import struct
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ejector_link as el  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures += 1


class SimEsp32:
    """Minimal behavioural model of the v2.0 firmware over a fake serial port."""

    def __init__(self, port, baud, legacy=False):
        self.port, self.baud, self.legacy = port, baud, legacy
        self.is_open = True
        self.rx = el.FrameParser()
        self.out = bytearray()
        self.lock = threading.Lock()
        self.seq = 0
        self.state = 0
        self.last_rx = time.time()
        self.fired_masks = []
        self.holds = set()
        self.remap = list(range(63))
        self.counters = dict.fromkeys(el.STATUS_FIELDS, 0)
        self.trips = 0
        threading.Thread(target=self._watchdog, daemon=True).start()

    # --- serial API used by EjectorLink ---
    @property
    def in_waiting(self):
        with self.lock:
            return len(self.out)

    def read(self, n=1):
        deadline = time.time() + 0.02
        while time.time() < deadline:
            with self.lock:
                if self.out:
                    data = bytes(self.out[:max(n, 1)])
                    del self.out[:len(data)]
                    return data
            time.sleep(0.002)
        return b""

    def write(self, data):
        if self.legacy:
            for line in data.decode().split("\n"):
                if line == "PING":
                    self._emit(b"ACK:PING\n")
                elif line:
                    self.fired_masks.append(("TEXT", line))
            return len(data)
        if self.baud != el.BINARY_BAUD:
            return len(data)                       # wrong baud: garbage, no reply
        frames, lines = self.rx.feed(data)
        if "PING" in lines:
            self._emit(b"ACK:PING PONG:LAMTON_EJECTOR_V2.3 P3 ID:AABBCCDDEEFF\n")   # v2.3 text reply (how connect() recognises it)
        for t, seq, p in frames:
            self._handle(t, seq, p)
        return len(data)

    def close(self):
        self.is_open = False

    # --- firmware model ---
    def _emit(self, b):
        with self.lock:
            self.out += b

    def _send(self, t, payload):
        self._emit(el.build_frame(t, self.seq, payload))
        self.seq += 1

    def _ack(self, seq, result=0):
        self._send(el.T_ACK, struct.pack("<HBBI", seq, result, self.state, int(time.time() * 1e6) & 0xFFFFFFFF))

    def _event(self, code, arg=0):
        self._send(el.T_EVENT, struct.pack("<BHI", code, arg, 0))

    def _watchdog(self):
        while self.is_open:
            if self.state == 1 and time.time() - self.last_rx > 0.5:
                self.state = 0                    # v2.3: silent watchdog - outputs off, idle, no event
                self.trips += 1
                self.holds.clear()
            time.sleep(0.01)

    def _handle(self, t, seq, p):
        self.counters["rx_ok"] += 1
        self.last_rx = time.time()
        is_cmd = t == el.T_CMD
        code = p[0] if is_cmd else None
        if self.state == 0 and not (is_cmd and code == el.C_DISARM):
            self.state = 1
        if t == el.T_HEARTBEAT:
            self._ack(seq)
        elif t == el.T_FIRE_NOW:
            mask, pulse_us, delay_us = struct.unpack("<QII", p)
            self.fired_masks.append((mask, pulse_us))
            self._ack(seq)
        elif t == el.T_STATUS_REQ:
            vals = [self.counters[k] for k in el.STATUS_FIELDS]
            vals[el.STATUS_FIELDS.index("wdt_trips")] = self.trips
            self._send(el.T_STATUS, struct.pack("<HB", 0x0203, self.state) + struct.pack("<12I", *vals))
        elif t == el.T_REMAP_REQ:
            self._ack(seq)
            for start in (0, 32):
                part = self.remap[start:start + 32]
                self._send(el.T_REMAP, bytes([start, len(part)]) + bytes(part))
        elif t == el.T_REMAP_SET:
            start, count = p[0], p[1]
            self.remap[start:start + count] = list(p[2:2 + count])
            self._ack(seq)
        elif is_cmd:
            arg = struct.unpack_from("<H", p, 1)[0]
            if code == el.C_ARM:
                self.state = 1
            elif code == el.C_DISARM:
                self.state = 0
            elif code == el.C_HOLD_ON:
                self.holds.add(arg)
            elif code == el.C_HOLD_OFF:
                self.holds.discard(arg)
            elif code == el.C_STOP_ALL:
                self.holds.clear()
            self._ack(seq)
        elif t == el.T_TEST:
            mode, a, b, pulse_us, gap_us, rep = struct.unpack("<BBBIIH", p)
            self._ack(seq)
            if mode == 2:
                def run():
                    for ch in range(a, b + 1):
                        self._event(3, ch | (self.remap[ch - 1] << 8))
                        time.sleep(0.002)
                    self._event(4)
                threading.Thread(target=run, daemon=True).start()
        else:
            self._ack(seq, 7)


# ---------------------------------------------------------------- 1. link basics
sims = {}


def factory(port, baud):
    sims[port] = SimEsp32(port, baud)
    return sims[port]


el.EjectorLink.serial_factory = staticmethod(factory)
events = []
link = el.EjectorLink("SIM1", on_event=lambda n, a: events.append((n, a)))
mode = link.connect(boot_wait_s=1.0)
sim = sims["SIM1"]
check("detect binary firmware", mode == "BINARY", mode)
check("firmware version read", link.fw_version == "2.3", link.fw_version)
check("remap table read", link.remap[:3] == [0, 1, 2] and link.remap[62] == 62)
check("board armed after connect", sim.state == 1, sim.state)

link.fire(list(range(1, 64)), pulse_ms=4)
time.sleep(0.1)
check("63-channel fire arrives as one mask", sim.fired_masks and sim.fired_masks[-1] == ((1 << 63) - 1, 4000),
      sim.fired_masks[-1:] if sim.fired_masks else None)
check("ACK received", link.counters["ack_ok"] >= 1, dict(link.counters))
check("round-trip time measured", len(link.rtt_ms) > 0)

time.sleep(0.6)
check("heartbeat keeps board armed (no trip after 0.6 s)", sim.state == 1 and sim.trips == 0, (sim.state, sim.trips))

link.test_sweep(1, 63, 2, 1)
t_end = time.time() + 5
while link.test_done_count == 0 and time.time() < t_end:   # simulator sleeps are slow on Python 3.10
    time.sleep(0.05)
check("sweep TEST_STEP events", link.last_test_step == (63, 62), link.last_test_step)
check("sweep TEST_DONE", link.test_done_count == 1, link.test_done_count)
check("channel info text", link.channel_info(12) == "CH 12 -> OUT 11 -> CHIP 2, Q3", link.channel_info(12))

table = list(range(63))
table[0], table[1] = 1, 0
link.set_remap(table)
time.sleep(0.05)
check("remap set reaches board", sim.remap[:2] == [1, 0], sim.remap[:2])
link.set_remap(list(range(63)))

# ---------------------------------------------------------------- 2. watchdog
link.heartbeat_enabled = False
link._running = False                      # stop heartbeat + reader to simulate a frozen PC
time.sleep(0.8)
check("silent watchdog: after 500 ms silence the board is idle (outputs off), counted once", sim.state == 0 and sim.trips == 1, (sim.state, sim.trips))
link.close(disarm=False)

# ---------------------------------------------------------------- 3. ESP32Controller in the main program
import importlib.util  # noqa: E402

cwd = os.getcwd()
spec = importlib.util.spec_from_file_location("lamton_main", os.path.join(ROOT, "LAMTON_LINE_NEW_ESP32.py"))
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)                # defines classes only (__main__ guard)
os.chdir(cwd)

esp = main.ESP32Controller(None)
ok = esp.connect_ejector("SIM2")
sim2 = sims["SIM2"]
check("ESP32Controller.connect_ejector", ok and esp.is_ejector_connected(), esp.ejector_link.mode if esp.ejector_link else None)
check("old-style check ejector_serial.is_open still works", bool(esp.ejector_serial and esp.ejector_serial.is_open))
esp.fire_ejector_channels([5, 6], 4)
esp.fire_single_ejector(63, 3)
time.sleep(0.1)
check("fire_ejector_channels", (0b110000, 4000) in sim2.fired_masks, sim2.fired_masks)
check("fire_single_ejector", (1 << 62, 3000) in sim2.fired_masks)
esp.hold_ejector_on(7)
time.sleep(0.05)
check("hold on", 7 in sim2.holds)
esp.stop_all_ejectors()
time.sleep(0.05)
check("stop_all clears hold", not sim2.holds)
check("channel info via controller", esp.ejector_channel_info(9) == "CH 9 -> OUT 8 -> CHIP 2, Q0")

# EjectScheduler path used during production sorting
sched = main.EjectScheduler(esp)
sched.start()
n_before = len(sim2.fired_masks)
sched.schedule_fire([10, 11], delay_ms=5, pulse_ms=4)
time.sleep(0.1)
check("EjectScheduler fires through binary link", len(sim2.fired_masks) == n_before + 1 and
      sim2.fired_masks[-1] == ((1 << 9) | (1 << 10), 4000), sim2.fired_masks[-1:])
sched.stop()

# Watchdog trip reported to the controller (GUI then stops sorting)
esp.ejector_link._running = False
time.sleep(0.8)
esp.ejector_link._running = True
esp.ejector_link._start_reader()
time.sleep(0.2)
check("silent watchdog: no event, no safety stop in the sorter", esp.safety_trip_reason is None and sim2.state == 0, (esp.safety_trip_reason, sim2.state))
n_fired = len(sim2.fired_masks)
esp.fire_single_ejector(63, 3)                          # NO re-arm: the next command arms the board by itself
time.sleep(0.1)
check("next click after the silence fires at once (auto-arm)", sim2.state == 1 and len(sim2.fired_masks) == n_fired + 1, (sim2.state, len(sim2.fired_masks) - n_fired))
esp.disconnect_ejector()
check("clean disconnect sends DISARM", sim2.state == 0, sim2.state)


# ---------------------------------------------------------------- 4. legacy fallback
def legacy_factory(port, baud):
    sims[port] = SimEsp32(port, baud, legacy=True)
    return sims[port]


el.EjectorLink.serial_factory = staticmethod(legacy_factory)
link3 = el.EjectorLink("SIM3")
mode3 = link3.connect(boot_wait_s=0.6)
check("old firmware detected -> LEGACY mode", mode3 == "LEGACY", mode3)
link3.fire(range(1, 64), 4)
time.sleep(0.05)
lines = [l for kind, l in sims["SIM3"].fired_masks if kind == "TEXT" and l.startswith("FIRE_MULTI")]
check("legacy fire split into short lines", lines and all(len(l) < 60 for l in lines), [len(l) for l in lines])
link3.close()

print("\nALL TESTS PASSED" if failures == 0 else f"\n{failures} TEST(S) FAILED")
sys.stdout.flush()
os._exit(1 if failures else 0)       # skip waiting for daemon threads
