"""
Simulated ESP32_63CH_Ejector_v2_3 behind a fake serial port (no hardware needed).

Used by tools/test_ejector_test_panel_sim.py and by `python ejector_test_panel.py --sim`.
Behaves like the firmware for everything the test panel uses: ACK, STATUS (counters),
REMAP read/set/save/default, CMD (stop/arm/hold/disarm), TEST single/sweep/stop with
real pulse+gap timing, TEST_STEP/TEST_DONE/ARMED events, IDENTIFY, and the v2.3 SILENT 500 ms watchdog
(PC quiet -> outputs OFF, state IDLE, no event, nothing refused; the next command arms again).

    import ejector_link as el
    from tools.ejector_sim import SimEsp32
    link = el.EjectorLink("SIM")
    link.serial_factory = SimEsp32.factory
"""
import os
import struct
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import ejector_link as el  # noqa: E402

S_DISARMED, S_ARMED, S_TRIPPED = 0, 1, 2


class SimEsp32:
    last = None                          # most recently opened simulator (tests inspect it)

    @staticmethod
    def factory(port, baud):
        SimEsp32.last = SimEsp32(port, baud)
        return SimEsp32.last

    def __init__(self, port, baud):
        self.port, self.baud = port, baud
        self.is_open = True
        self.rx = el.FrameParser()
        self.out = bytearray()
        self.lock = threading.Lock()
        self.seq = 0
        self.state = S_DISARMED
        self.last_rx = time.time()
        self.fire_log = []               # (time, [channels], pulse_ms) for every FIRE_NOW / test step
        self.holds = set()
        self.remap = list(range(63))
        self.saved_remap = list(range(63))
        self.counters = dict.fromkeys(el.STATUS_FIELDS, 0)
        self.commands = []               # names of everything received, in order
        self.silence_log = []            # times the watchdog switched the outputs off
        self._test_gen = 0               # bumping it cancels a running test
        threading.Thread(target=self._watchdog, daemon=True).start()

    # ---------------------------------------------------------------- serial API used by EjectorLink
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
        if self.baud != el.BINARY_BAUD:
            return len(data)                         # wrong baud: no answer (like the real board)
        frames, lines = self.rx.feed(data)
        for line in lines:
            self.commands.append("TEXT:" + line)
            if line == "PING":
                self._emit(b"ACK:PING PONG:LAMTON_EJECTOR_V2.3 P3 ID:AABBCCDDEEFF\n")
        for t, seq, p in frames:
            self._handle(t, seq, p)
        return len(data)

    def close(self):
        self.is_open = False
        self._test_gen += 1

    # ---------------------------------------------------------------- helpers
    def _emit(self, b):
        with self.lock:
            self.out += b

    def _send(self, t, payload):
        self._emit(el.build_frame(t, self.seq, payload))
        self.seq = (self.seq + 1) & 0xFFFF

    def _ack(self, seq, result=0):
        self._send(el.T_ACK, struct.pack("<HBBI", seq, result, self.state, int(time.time() * 1e6) & 0xFFFFFFFF))

    def _event(self, code, arg=0):
        self._send(el.T_EVENT, struct.pack("<BHI", code, arg, int(time.time() * 1e6) & 0xFFFFFFFF))

    def _fire(self, chans, pulse_ms):
        chans = [c for c in chans if self.remap[c - 1] != 0xFF]
        self.fire_log.append((time.time(), chans, pulse_ms))
        self.counters["fired"] += len(chans)

    def _stop(self):
        self._test_gen += 1
        self.holds.clear()

    def _watchdog(self):
        while self.is_open:
            if self.state == S_ARMED and time.time() - self.last_rx > 0.5:
                self._stop()                      # v2.3: only the outputs go OFF - silent, nothing is refused afterwards
                self.state = S_DISARMED
                self.counters["wdt_trips"] += 1
                self.silence_log.append(time.time())
            time.sleep(0.01)

    def _run_test(self, gen, chans, pulse_ms, gap_ms):
        for ch in chans:
            if gen != self._test_gen or not self.is_open:
                return
            self._fire([ch], pulse_ms)
            self._event(3, ch | (self.remap[ch - 1] << 8))
            time.sleep((pulse_ms + gap_ms) / 1000.0)
        if gen == self._test_gen:
            self._event(4)

    # ---------------------------------------------------------------- firmware model
    def _handle(self, t, seq, p):
        self.counters["rx_ok"] += 1
        self.last_rx = time.time()
        is_cmd = t == el.T_CMD
        code = p[0] if is_cmd else None
        if self.state == S_DISARMED and not (is_cmd and code == el.C_DISARM) and t != el.T_IDENTIFY_REQ:
            self.state = S_ARMED              # v2.3: ANY valid command arms the board
            self._event(6)
        if t == el.T_IDENTIFY_REQ:
            self.commands.append("IDENTIFY")
            self._send(el.T_IDENTIFY, b"EJECTOR\0" + struct.pack("<H", 0x0203) + bytes([3, 0]) + bytes.fromhex("AABBCCDDEEFF"))
            return
        if t == el.T_HEARTBEAT:
            self._ack(seq)
        elif t == el.T_FIRE_NOW:
            mask, pulse_us, _delay = struct.unpack("<QII", p)
            self.commands.append("FIRE")
            self._fire(el.mask_to_channels(mask), pulse_us / 1000.0)
            self._ack(seq)
        elif t == el.T_STATUS_REQ:
            self._send(el.T_STATUS, struct.pack("<HB", 0x0203, self.state) +
                       struct.pack("<12I", *[self.counters[k] for k in el.STATUS_FIELDS]))
        elif t == el.T_REMAP_REQ:
            self._ack(seq)
            for start in (0, 32):
                part = self.remap[start:start + 32]
                self._send(el.T_REMAP, bytes([start, len(part)]) + bytes(part))
        elif t == el.T_REMAP_SET:
            start, count = p[0], p[1]
            self.remap[start:start + count] = list(p[2:2 + count])
            self.commands.append("REMAP_SET")
            self._ack(seq)
        elif is_cmd:
            arg = struct.unpack_from("<H", p, 1)[0]
            names = {el.C_STOP_ALL: "STOP_ALL", el.C_ARM: "ARM", el.C_HOLD_ON: "HOLD_ON", el.C_HOLD_OFF: "HOLD_OFF",
                     el.C_DISARM: "DISARM", el.C_RESET_COUNTS: "RESET_COUNTS", el.C_REMAP_SAVE: "REMAP_SAVE",
                     el.C_REMAP_DEFAULT: "REMAP_DEFAULT"}
            self.commands.append(names.get(code, f"CMD{code}"))
            if code == el.C_ARM:
                self._stop()
                if self.state != S_ARMED:
                    self.state = S_ARMED
                    self._event(6)
            elif code == el.C_DISARM:
                self._stop()
                self.state = S_DISARMED
            elif code == el.C_HOLD_ON:
                self.holds.add(arg)
            elif code == el.C_HOLD_OFF:
                self.holds.discard(arg)
            elif code == el.C_STOP_ALL:
                self._stop()
            elif code == el.C_REMAP_SAVE:
                self.saved_remap = list(self.remap)
            elif code == el.C_REMAP_DEFAULT:
                self.remap = list(range(63))
            self._ack(seq)
        elif t == el.T_TEST:
            mode, a, b, pulse_us, gap_us, rep = struct.unpack("<BBBIIH", p)
            self._stop()
            self.commands.append(f"TEST{mode}")
            self._ack(seq)
            if mode == 0:
                return
            if mode == 1:
                chans = [a] * (10 ** 6 if rep == 0xFFFF else rep)
            else:
                chans = list(range(a, b + 1))
            gen = self._test_gen
            threading.Thread(target=self._run_test, args=(gen, chans, pulse_us / 1000.0, gap_us / 1000.0),
                             daemon=True).start()
        else:
            self._ack(seq, 7)
