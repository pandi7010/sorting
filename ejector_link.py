"""
ejector_link.py - PC side of the LAMBTON ejector protocol (firmware ESP32_63CH_Ejector_v2_3, 115200 baud;
older v2.0 - v2.2 at 921600 baud are still found).

Frame (both directions):
    A5 5A | LEN | TYPE | SEQ lo hi | PAYLOAD ... | CRC lo hi
    LEN   = 3 + len(PAYLOAD)          (TYPE + SEQ + PAYLOAD)
    CRC   = CRC-16/CCITT-FALSE over LEN..PAYLOAD (poly 0x1021, init 0xFFFF)

Simple example - fire channels 3 and 17 for 4 ms:
    link = EjectorLink("COM24"); link.connect()
    link.fire([3, 17], pulse_ms=4)

The firmware also prints normal text lines (boot banner, test steps). Those
arrive between frames and are collected in `link.text_lines`.

If the board still runs the OLD firmware (ESP32_63CH_ShiftRegister, text only),
connect() detects it and falls back to LEGACY text mode, so sorting keeps
working. In legacy mode long FIRE_MULTI commands are split into short ones,
which also avoids the old 64-character truncation bug.
"""

import collections
import re
import struct
import threading
import time

import serial

# ---- protocol constants (must match the firmware) ----
SOF = b"\xA5\x5A"
MAX_LEN = 60

T_HEARTBEAT, T_FIRE_NOW, T_CONFIG, T_REMAP_SET = 0x01, 0x11, 0x20, 0x21
T_CMD, T_TEST, T_STATUS_REQ, T_REMAP_REQ = 0x30, 0x31, 0x40, 0x41
T_ACK, T_STATUS, T_EVENT, T_REMAP = 0x80, 0x81, 0x82, 0x83
T_IDENTIFY_REQ, T_IDENTIFY = 0x43, 0x85        # v2.3: who are you? -> "EJECTOR", fw, pin profile, board ID

C_STOP_ALL, C_ARM, C_HOLD_ON, C_HOLD_OFF = 1, 4, 5, 6
C_DISARM, C_RESET_COUNTS, C_REMAP_SAVE, C_REMAP_DEFAULT = 8, 9, 10, 11
# v3.0 (Phase 3, line-synchronous firing)
T_FIRE_LINE, T_CONFIG_LINE, T_LINE_STATUS_REQ, T_LINE_STATUS = 0x12, 0x22, 0x42, 0x84
C_CLOCK_START, C_CLOCK_STOP = 12, 13
TRIG_MODES = {"OFF": 0, "LINE": 1, "FRAME": 2, "QUAD": 3}

RESULT_NAMES = {0: "OK", 1: "LATE", 2: "PARTIAL_BUSY", 3: "BUSY", 4: "BAD_PARAM",
                6: "TRIPPED", 7: "UNKNOWN_TYPE", 8: "NOT_READY"}
STATE_NAMES = {0: "DISARMED", 1: "ARMED", 2: "TRIPPED"}
EVENT_NAMES = {1: "BOOT", 2: "WDT_TRIP", 3: "TEST_STEP", 4: "TEST_DONE", 5: "HOLD_TIMEOUT", 6: "ARMED"}
STATUS_FIELDS = ["rx_ok", "crc_err", "len_err", "unknown_type", "seq_gaps", "fired",
                 "busy", "wdt_trips", "text_cmds", "line_too_long", "disabled_hits", "late"]

NUM_CHANNELS = 63
BINARY_BAUD = 115200             # v2.3 (and the old text firmware: told apart by protocol, see connect())
OLD_BINARY_BAUD = 921600         # v2.0 - v2.2 only
LEGACY_BAUD = 115200
HEARTBEAT_S = 0.100
ACK_TIMEOUT_S = 0.100


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc


def build_frame(msg_type: int, seq: int, payload: bytes = b"") -> bytes:
    if len(payload) + 3 > MAX_LEN:
        raise ValueError("payload too long")
    body = bytes([3 + len(payload), msg_type]) + struct.pack("<H", seq & 0xFFFF) + payload
    return SOF + body + struct.pack("<H", crc16_ccitt(body))


def channels_to_mask(channels) -> int:
    """[1, 3, 63] -> 64-bit mask with bit 0 = channel 1."""
    mask = 0
    for ch in channels:
        ch = int(ch)
        if 1 <= ch <= NUM_CHANNELS:
            mask |= 1 << (ch - 1)
    return mask


def mask_to_channels(mask: int):
    return [i + 1 for i in range(NUM_CHANNELS) if mask >> i & 1]


def parse_identify(p: bytes) -> dict:
    """T_IDENTIFY payload (18 bytes) -> {"device": "EJECTOR", "fw": "2.3", "profile": 3, "id": "AABBCCDDEEFF"}"""
    fw = struct.unpack_from("<H", p, 8)[0]
    return {"device": p[:8].split(b"\0")[0].decode("ascii", "replace"), "fw": f"{fw >> 8}.{fw & 0xFF}",
            "profile": p[10], "id": p[12:18].hex().upper()}


def out_to_chip(out: int):
    """Physical output 0..63 -> (chip 1..8, Q 0..7). Chip 1 is nearest the ESP32."""
    return out // 8 + 1, out % 8


class FrameParser:
    """Splits a byte stream into binary frames and text lines."""

    def __init__(self):
        self.buf = bytearray()
        self.text = bytearray()
        self.crc_errors = 0

    def feed(self, data: bytes):
        """Returns (frames, text_lines). frames = [(type, seq, payload)]."""
        frames, lines = [], []
        self.buf += data
        while self.buf:
            if self.buf[0] != 0xA5:
                b = self.buf.pop(0)
                if b in (10, 13):
                    if self.text:
                        lines.append(self.text.decode("ascii", "replace"))
                        self.text.clear()
                elif 32 <= b < 127:
                    self.text.append(b)
                continue
            if len(self.buf) < 3:
                break
            if self.buf[1] != 0x5A or not (3 <= self.buf[2] <= MAX_LEN):
                self.buf.pop(0)
                continue
            total = 2 + 1 + self.buf[2] + 2
            if len(self.buf) < total:
                break
            body = bytes(self.buf[2:total - 2])
            crc_rx = struct.unpack_from("<H", self.buf, total - 2)[0]
            if crc16_ccitt(body) != crc_rx:
                self.crc_errors += 1
                # resync: jump to the next frame start inside the bad frame, else skip the whole bad frame
                nxt = self.buf.find(SOF, 1, total)
                del self.buf[:nxt if nxt > 0 else total]
                continue
            del self.buf[:total]
            frames.append((body[1], struct.unpack_from("<H", body, 2)[0], body[4:]))
        return frames, lines


class EjectorLink:
    def __init__(self, port, on_event=None, on_text=None, heartbeat=True):
        self.port = port
        self.on_event = on_event          # callback(name, arg) - called from reader thread!
        self.on_text = on_text            # callback(line)      - called from reader thread!
        self.heartbeat_enabled = heartbeat
        self.ser = None
        self.mode = None                  # "BINARY" | "LEGACY" | None
        self.fw_version = None
        self.esp_state = None
        self.status = {}
        self.remap = [None] * NUM_CHANNELS
        self.text_lines = collections.deque(maxlen=500)
        self.last_test_step = None        # (channel, out) of the latest TEST_STEP event
        self.test_done_count = 0
        self.tripped_event = threading.Event()
        self.baud = None
        self.identity = {}                # v2.3 IDENTIFY reply: device, fw, profile, id
        self._identify_evt = threading.Event()
        self._probe_lines = None          # connect(): text lines seen while probing

        self._seq = 0
        self._write_lock = threading.Lock()
        self._pending = {}                # seq -> (send_time, type)
        self._pending_lock = threading.Lock()
        self._status_evt = threading.Event()
        self._remap_evt = threading.Event()
        self._line_status_evt = threading.Event()
        self.line_status = {}
        self._remap_received = 0
        self._running = False
        self._threads = []
        self._parser = FrameParser()
        self.heartbeat_flags = 0
        self.heartbeat_paused = False     # testers: pause to prove the 500 ms watchdog trips

        # PC-side counters
        self.counters = collections.Counter()
        self.rtt_ms = collections.deque(maxlen=1000)
        self.ack_listeners = []           # callbacks(seq, result_name, esp_us, t_ack, t_sent) - Phase 2 timing

        # ESP_BYTE mode anti-flicker channel tracking & timer extension
        self._byte_active_channels = set()
        self._byte_channel_timers = {}
        self._byte_channel_expire = {}
        self._byte_lock = threading.Lock()

    # ------------------------------------------------------------------ connect
    @property
    def is_open(self):
        return bool(self.ser and self.ser.is_open and self.mode)

    serial_factory = None                 # tests can replace the serial port with a simulator

    def _open(self, baud):
        if self.serial_factory:
            return self.serial_factory(self.port, baud)
        s = serial.Serial()
        s.port = self.port
        s.baudrate = baud
        s.timeout = 0.02
        s.write_timeout = 0.5
        s.dtr = False                     # do not reset the ESP32 when opening
        s.rts = False
        s.open()
        return s

    def connect(self, boot_wait_s=2.5):
        """Open the port and detect the firmware by PROTOCOL (not by baud rate).
        Returns 'BINARY', 'LEGACY' or None.

        1. text PING at BINARY_BAUD (115200) - harmless for every firmware:
             reply contains "PONG:LAMTON_EJECTOR_V"  -> v2.x firmware -> binary handshake (STATUS reply)
             reply is only "ACK:PING"                 -> OLD text firmware -> LEGACY mode
           (the old firmware never receives binary bytes, and a v2.x board is never taken for legacy)
        2. nothing answered: text PING at 921600 -> v2.0 - v2.2 firmware (older, binary at 921600)."""
        self.close()
        for baud, wait_s in ((BINARY_BAUD, boot_wait_s), (OLD_BINARY_BAUD, 1.5)):
            kind = self._probe(baud, wait_s)
            if kind in ("BINARY", "LEGACY", "ESP_BYTE"):
                return self.mode
            if kind == "FAIL":                      # port cannot be opened / v2.x found but binary silent
                return None
        self.text_lines.append(f"[LINK] {self.port}: no ejector firmware answered (115200 or 921600 baud)")
        return None

    def _probe(self, baud, wait_s):
        """One probe at `baud`. 'BINARY' / 'LEGACY' = connected (port stays open), 'NONE' = nobody answered
        (port closed), 'FAIL' = error (port closed)."""
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

        # Step 2: if not esp_byte, probe text / v2 binary with \nPING\n
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
                    elif "ACK:PING" in line:
                        legacy = True
                time.sleep(0.02)
        self._probe_lines = None
        if v2:                                        # v2.x: binary handshake on the same open port
            deadline = time.time() + 2.0
            while time.time() < deadline:
                self._status_evt.clear()
                self._send(T_STATUS_REQ, track=False)   # answered by STATUS, not ACK -> not a "no-ack"
                if self._status_evt.wait(0.25):
                    self.mode = "BINARY"
                    self._send_cmd(C_STOP_ALL)
                    self._send_cmd(C_ARM)
                    self.request_remap(timeout=1.0)
                    if baud == BINARY_BAUD:
                        self.identify(0.4)
                    if self.heartbeat_enabled:
                        self._start_heartbeat()
                    extra = "" if baud == BINARY_BAUD else f" at {baud} baud (older firmware: upload v2.3 for {BINARY_BAUD})"
                    self.text_lines.append(f"[LINK] {self.port}: binary firmware v{self.fw_version}{extra}")
                    return "BINARY"
            self.text_lines.append(f"[LINK] {self.port}: v{v2[0]}.{v2[1]} firmware answers text but not the binary protocol")
            self._close_port()
            return "FAIL"
        if esp_byte:
            self.mode = "ESP_BYTE"
            self._write(bytes([0]))  # Send ALL OFF initial reset
            self.text_lines.append(f"[LINK] {self.port}: ESP32 63-CH Ejector (esp.py byte protocol)")
            return "ESP_BYTE"
        if legacy:
            self.mode = "LEGACY"
            self._write(b"STOP\nALL_OFF\n")
            self.text_lines.append(f"[LINK] {self.port}: OLD text firmware (legacy mode)")
            return "LEGACY"
        self._close_port()
        return "NONE"

    def _close_port(self):
        self._stop_threads()
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.mode = None

    def close(self, disarm=True):
        if self.mode == "BINARY" and disarm:
            try:
                self._send_cmd(C_STOP_ALL)
                self._send_cmd(C_DISARM)      # clean exit: no watchdog trip
                time.sleep(0.05)
            except Exception:
                pass
        if hasattr(self, '_byte_lock'):
            with self._byte_lock:
                for t in self._byte_channel_timers.values():
                    if t and t.is_alive():
                        try:
                            t.cancel()
                        except Exception:
                            pass
                self._byte_channel_timers.clear()
                self._byte_channel_expire.clear()
                self._byte_active_channels.clear()
        self._stop_threads()
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.mode = None

    def _stop_threads(self):
        self._running = False
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=0.5)
        self._threads = []

    # ------------------------------------------------------------------ low level
    def _write(self, data: bytes):
        if not self.ser:
            return False
        try:
            with self._write_lock:
                self.ser.write(data)
            return True
        except Exception as exc:
            self.counters["write_errors"] += 1
            self.text_lines.append(f"[LINK] write error: {exc}")
            return False

    def _send(self, msg_type, payload=b"", track=True):
        with self._write_lock:
            seq = self._seq
            self._seq = (self._seq + 1) & 0xFFFF
            frame = build_frame(msg_type, seq, payload)
            t = time.perf_counter()
            if track:
                with self._pending_lock:
                    self._pending[seq] = (t, msg_type)
            try:
                self.ser.write(frame)
            except Exception as exc:
                self.counters["write_errors"] += 1
                self.text_lines.append(f"[LINK] write error: {exc}")
                return None
        self.counters["sent"] += 1
        return seq

    def _send_cmd(self, code, arg=0):
        return self._send(T_CMD, struct.pack("<BH", code, arg))

    # ------------------------------------------------------------------ threads
    def _start_reader(self):
        t = threading.Thread(target=self._reader, name="EjectorLinkReader", daemon=True)
        self._threads.append(t)
        t.start()

    def _start_heartbeat(self):
        t = threading.Thread(target=self._heartbeat, name="EjectorLinkHeartbeat", daemon=True)
        self._threads.append(t)
        t.start()

    def _heartbeat(self):
        while self._running and self.mode == "BINARY":
            if not self.heartbeat_paused:
                pc_us = int(time.perf_counter() * 1e6) & 0xFFFFFFFF
                self._send(T_HEARTBEAT, struct.pack("<BI", self.heartbeat_flags & 0xFF, pc_us))
            time.sleep(HEARTBEAT_S)

    def _reader(self):
        last_sweep = time.perf_counter()
        while self._running:
            try:
                data = self.ser.read(self.ser.in_waiting or 1)
            except Exception as exc:
                self.text_lines.append(f"[LINK] read error: {exc}")
                self.counters["read_errors"] += 1
                self.mode = None
                break
            t_rx = time.perf_counter()
            if data:
                frames, lines = self._parser.feed(data)
                for line in lines:
                    self.text_lines.append(line)
                    if self._probe_lines is not None:
                        self._probe_lines.append(line)
                    self.counters["text_lines"] += 1
                    if self.on_text:
                        self._safe_cb(self.on_text, line)
                for ftype, seq, payload in frames:
                    self._handle_frame(ftype, seq, payload, t_rx)
                self.counters["rx_crc_err"] = self._parser.crc_errors
            if t_rx - last_sweep > 0.05:            # expire unanswered messages
                last_sweep = t_rx
                with self._pending_lock:
                    old = [s for s, (t, _) in self._pending.items() if t_rx - t > ACK_TIMEOUT_S]
                    for s in old:
                        del self._pending[s]
                self.counters["no_ack"] += len(old)

    def _safe_cb(self, fn, *args):
        try:
            fn(*args)
        except Exception as exc:
            self.text_lines.append(f"[LINK] callback error: {exc}")

    def _handle_frame(self, ftype, seq, p, t_rx):
        if ftype == T_ACK and len(p) == 8:
            acked, result, state, esp_us = struct.unpack("<HBBI", p)
            self.esp_state = STATE_NAMES.get(state, state)
            name = RESULT_NAMES.get(result, f"R{result}")
            self.counters[f"ack_{name.lower()}"] += 1
            with self._pending_lock:
                sent = self._pending.pop(acked, None)
            if sent:
                self.rtt_ms.append((t_rx - sent[0]) * 1000.0)
            for cb in self.ack_listeners:
                self._safe_cb(cb, acked, name, esp_us, t_rx, sent[0] if sent else None)
            if result == 6:
                self.tripped_event.set()
        elif ftype == T_STATUS and len(p) == 3 + 4 * len(STATUS_FIELDS):
            fw, state = struct.unpack_from("<HB", p)
            vals = struct.unpack_from("<" + "I" * len(STATUS_FIELDS), p, 3)
            self.fw_version = f"{fw >> 8}.{fw & 0xFF}"
            self.esp_state = STATE_NAMES.get(state, state)
            self.status = dict(zip(STATUS_FIELDS, vals), state=self.esp_state, fw=self.fw_version)
            self._status_evt.set()
        elif ftype == T_EVENT and len(p) == 7:
            code, arg, esp_us = struct.unpack("<BHI", p)
            name = EVENT_NAMES.get(code, f"E{code}")
            if code == 3:
                self.last_test_step = (arg & 0xFF, arg >> 8)
            elif code == 4:
                self.test_done_count += 1
            elif code == 2:
                self.esp_state = "TRIPPED"
                self.tripped_event.set()
            self.counters[f"event_{name.lower()}"] += 1
            if self.on_event:
                self._safe_cb(self.on_event, name, arg)
        elif ftype == T_LINE_STATUS and len(p) == 18:
            now_line, rate, mode, running, late, fired = struct.unpack("<IIBBII", p)
            self.line_status = {"now_line": now_line, "line_rate_hz": rate, "trig_mode": mode,
                                "running": bool(running), "late": late, "fired_line": fired, "t_rx": t_rx}
            self._line_status_evt.set()
        elif ftype == T_IDENTIFY and len(p) == 18:
            self.identity = parse_identify(p)
            self._identify_evt.set()
        elif ftype == T_REMAP and len(p) >= 2:
            start, count = p[0], p[1]
            for i in range(count):
                v = p[2 + i]
                self.remap[start + i] = None if v == 0xFF else v
            self._remap_received += count
            if self._remap_received >= NUM_CHANNELS:
                self._remap_evt.set()

    # ------------------------------------------------------------------ public API
    def fire(self, channels, pulse_ms, delay_ms=0.0):
        """Fire 1-based channels. Returns sequence number (binary) or True (legacy)."""
        chs = [int(c) for c in channels if 1 <= int(c) <= NUM_CHANNELS]
        if not chs or not self.is_open:
            return None
        if self.mode == "ESP_BYTE":
            # Anti-flickering bridging pulse duration:
            # Physical camera line scan frames arrive every 25-65ms.
            # An anti-flickering hold window of max(0.095, (pulse_ms + 75ms)) guarantees
            # that consecutive frames seamlessly bridge without turning OFF, eliminating
            # all LED flickering and pneumatic valve chattering!
            eff_pulse_s = max(0.095, (float(pulse_ms) + 75.0) / 1000.0)
            now = time.perf_counter()
            target_expire = now + eff_pulse_s

            to_turn_on = []
            with self._byte_lock:
                for c in chs:
                    # Cancel existing turn-off timer for this channel
                    old_t = self._byte_channel_timers.get(c)
                    if old_t and old_t.is_alive():
                        try:
                            old_t.cancel()
                        except Exception:
                            pass

                    # Extend channel expiration
                    self._byte_channel_expire[c] = max(self._byte_channel_expire.get(c, 0.0), target_expire)
                    rem_s = max(0.001, self._byte_channel_expire[c] - now)

                    # Only send ON byte if channel is not currently ON in hardware
                    if c not in self._byte_active_channels:
                        to_turn_on.append(c)
                        self._byte_active_channels.add(c)

                    def make_turn_off(ch):
                        def turn_off():
                            with self._byte_lock:
                                now_t = time.perf_counter()
                                exp_t = self._byte_channel_expire.get(ch, 0.0)
                                # If expiration was extended by a newer frame, reschedule for remaining time
                                if now_t < exp_t - 0.003:
                                    rem = exp_t - now_t
                                    t_resched = threading.Timer(rem, make_turn_off(ch))
                                    t_resched.daemon = True
                                    self._byte_channel_timers[ch] = t_resched
                                    t_resched.start()
                                    return
                                self._byte_active_channels.discard(ch)
                                self._byte_channel_timers.pop(ch, None)
                                self._byte_channel_expire.pop(ch, None)
                                if self.is_open:
                                    try:
                                        self._write(bytes([ch + 100]))
                                    except Exception:
                                        pass
                        return turn_off

                    t = threading.Timer(rem_s, make_turn_off(c))
                    t.daemon = True
                    self._byte_channel_timers[c] = t
                    t.start()

                if to_turn_on and self.is_open:
                    self._write(bytes(to_turn_on))
            return 1
        if self.mode == "BINARY":
            payload = struct.pack("<QII", channels_to_mask(chs), int(pulse_ms * 1000), int(delay_ms * 1000))
            return self._send(T_FIRE_NOW, payload)
        # legacy: keep each line short (old firmware truncates beyond ~60 characters)
        ok = True
        chunk = []
        for ch in chs:
            chunk.append(str(ch))
            if len(",".join(chunk)) > 40:
                ok &= self._write(f"FIRE_MULTI:{','.join(chunk)}:{int(round(pulse_ms))}\n".encode())
                chunk = []
        if chunk:
            ok &= self._write(f"FIRE_MULTI:{','.join(chunk)}:{int(round(pulse_ms))}\n".encode())
        return ok

    def stop_all(self):
        if self.mode == "ESP_BYTE":
            with self._byte_lock:
                for t in self._byte_channel_timers.values():
                    if t and t.is_alive():
                        try:
                            t.cancel()
                        except Exception:
                            pass
                self._byte_channel_timers.clear()
                self._byte_channel_expire.clear()
                self._byte_active_channels.clear()
            return self._write(bytes([0]))
        if self.mode == "BINARY":
            return self._send_cmd(C_STOP_ALL)
        return self._write(b"STOP\nALL_OFF\n")

    def arm(self):
        self.tripped_event.clear()
        return self._send_cmd(C_ARM) if self.mode == "BINARY" else True

    def hold(self, channel, on):
        if self.mode == "ESP_BYTE":
            ch = int(channel)
            if 1 <= ch <= NUM_CHANNELS:
                with self._byte_lock:
                    old_t = self._byte_channel_timers.pop(ch, None)
                    if old_t and old_t.is_alive():
                        try:
                            old_t.cancel()
                        except Exception:
                            pass
                    self._byte_channel_expire.pop(ch, None)
                    if on:
                        self._byte_active_channels.add(ch)
                        return self._write(bytes([ch]))
                    else:
                        self._byte_active_channels.discard(ch)
                        return self._write(bytes([ch + 100]))
            return False
        if self.mode == "BINARY":
            return self._send_cmd(C_HOLD_ON if on else C_HOLD_OFF, int(channel))
        return self._write(f"{'HOLD_ON' if on else 'HOLD_OFF'}:{int(channel)}\n".encode())

    def test_single(self, channel, pulse_ms, gap_ms, repeat=1):
        """Fire one channel `repeat` times (repeat=0 -> forever until stop)."""
        if self.mode == "ESP_BYTE":
            ch = int(channel)
            if not (1 <= ch <= NUM_CHANNELS): return False
            self._write(bytes([ch]))
            time.sleep(max(0.001, float(pulse_ms) / 1000.0))
            self._write(bytes([ch + 100]))
            return True
        if self.mode == "BINARY":
            rep = 0xFFFF if repeat == 0 else min(int(repeat), 0xFFFE)
            payload = struct.pack("<BBBIIH", 1, channel, channel, int(pulse_ms * 1000), int(gap_ms * 1000), rep)
            return self._send(T_TEST, payload)
        if repeat == 0:
            return self._write(f"CONT:{channel}:{int(pulse_ms)}:{int(gap_ms)}\n".encode())
        return self._write(f"FIRE:{channel}:{int(pulse_ms)}:{int(gap_ms)}:{int(repeat)}\n".encode())

    def test_sweep(self, ch_from, ch_to, pulse_ms, gap_ms):
        if self.mode == "ESP_BYTE":
            self._test_stop_requested = False
            def worker():
                for ch in range(ch_from, ch_to + 1):
                    if not self.is_open or getattr(self, '_test_stop_requested', False):
                        break
                    self.last_test_step = (ch, 0)
                    self._write(bytes([ch]))
                    p_wait = max(0.001, float(pulse_ms) / 1000.0)
                    t_end = time.time() + p_wait
                    while time.time() < t_end:
                        if getattr(self, '_test_stop_requested', False) or not self.is_open:
                            break
                        time.sleep(0.002)
                    self._write(bytes([ch + 100]))
                    g_wait = max(0.005, float(gap_ms) / 1000.0)
                    t_end = time.time() + g_wait
                    while time.time() < t_end:
                        if getattr(self, '_test_stop_requested', False) or not self.is_open:
                            break
                        time.sleep(0.005)
                self.test_done_count += 1
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            return True
        if self.mode == "BINARY":
            payload = struct.pack("<BBBIIH", 2, ch_from, ch_to, int(pulse_ms * 1000), int(gap_ms * 1000), 0)
            return self._send(T_TEST, payload)
        return self._write(f"TEST:{ch_from}:{ch_to}:{int(pulse_ms)}:{int(gap_ms)}\n".encode())

    def test_stop(self):
        self._test_stop_requested = True
        if self.mode == "ESP_BYTE":
            return self.stop_all()
        if self.mode == "BINARY":
            return self._send(T_TEST, struct.pack("<BBBIIH", 0, 0, 0, 0, 0, 0))
        return self._write(b"STOP\n")

    def configure(self, max_pulse_ms=200.0, min_pulse_ms=0.2, wdt_ms=500, hold_max_ms=10000):
        if self.mode != "BINARY":
            return None
        return self._send(T_CONFIG, struct.pack("<IIHH", int(max_pulse_ms * 1000), int(min_pulse_ms * 1000),
                                                int(wdt_ms), int(hold_max_ms)))

    def identify(self, timeout=0.5):
        """v2.3 firmware: ask who is on this port. Returns {} for older firmware (no answer)."""
        if self.mode != "BINARY":
            return {}
        self._identify_evt.clear()
        self._send(T_IDENTIFY_REQ, track=False)
        self._identify_evt.wait(timeout)
        return dict(self.identity)

    def request_status(self, timeout=0.5):
        if self.mode != "BINARY":
            return {}
        self._status_evt.clear()
        self._send(T_STATUS_REQ, track=False)
        self._status_evt.wait(timeout)
        return dict(self.status)

    def request_remap(self, timeout=1.0):
        if self.mode != "BINARY":
            return list(self.remap)
        self._remap_evt.clear()
        self._remap_received = 0
        self._send(T_REMAP_REQ)
        self._remap_evt.wait(timeout)
        return list(self.remap)

    def set_remap(self, table):
        """table: list of 63 entries, physical output 0..63 or None (disabled)."""
        if self.mode != "BINARY":
            return False
        vals = [0xFF if v is None else int(v) for v in table]
        for start in range(0, NUM_CHANNELS, 32):
            part = vals[start:start + 32]
            self._send(T_REMAP_SET, bytes([start, len(part)]) + bytes(part))
        self.remap = [None if v == 0xFF else v for v in vals]
        return True

    def save_remap(self):
        return self._send_cmd(C_REMAP_SAVE) if self.mode == "BINARY" else self._write(b"MAP_SAVE\n")

    def default_remap(self):
        if self.mode == "BINARY":
            self._send_cmd(C_REMAP_DEFAULT)
            return self.request_remap()
        return self._write(b"MAP_DEFAULT\n")

    def reset_counters(self):
        self.counters.clear()
        self.rtt_ms.clear()
        return self._send_cmd(C_RESET_COUNTS) if self.mode == "BINARY" else None

    # ------------------------------------------------------------------ v3.0 line-synchronous firing
    @property
    def supports_line_sync(self):
        try:
            return self.mode == "BINARY" and float(self.fw_version or 0) >= 3.0
        except ValueError:
            return False

    def configure_line(self, line_rate_hz, trig_mode, frame_lines, d_front, d_rear,
                       lead=2, tail=2, min_lines=3, max_lines=2000, late_tol=2):
        if not self.supports_line_sync:
            return None
        mode = TRIG_MODES.get(trig_mode, trig_mode) if isinstance(trig_mode, str) else int(trig_mode)
        payload = struct.pack("<IBHHHBBHHB", int(line_rate_hz), mode, int(frame_lines), int(d_front), int(d_rear),
                              int(lead), int(tail), int(min_lines), int(max_lines), int(late_tol))
        return self._send(T_CONFIG_LINE, payload)

    def clock_start(self):
        return self._send_cmd(C_CLOCK_START) if self.supports_line_sync else None

    def clock_stop(self):
        return self._send_cmd(C_CLOCK_STOP) if self.supports_line_sync else None

    def fire_line(self, cam, line, channels, length_lines):
        """cam 0 = FRONT, 1 = REAR. The ESP32 fires at line + D[cam]."""
        if not self.supports_line_sync:
            return None
        mask = channels_to_mask(channels)
        if not mask:
            return None
        return self._send(T_FIRE_LINE, struct.pack("<BIQH", int(cam), int(line) & 0xFFFFFFFF, mask,
                                                   max(1, min(int(length_lines), 0xFFFF))))

    def request_line_status(self, timeout=0.3):
        if not self.supports_line_sync:
            return {}
        self._line_status_evt.clear()
        self._send(T_LINE_STATUS_REQ, track=False)
        self._line_status_evt.wait(timeout)
        return dict(self.line_status)

    def send_text(self, line):
        """Raw text command (diagnostics console)."""
        return self._write((line.strip() + "\n").encode("ascii", "replace"))

    def channel_info(self, channel):
        """'CH 12 -> OUT 11 -> CHIP 2, Q3' using the remap table read from the board."""
        out = self.remap[channel - 1] if 1 <= channel <= NUM_CHANNELS else None
        if out is None:
            if self.mode == "BINARY":
                return f"CH {channel} -> DISABLED"
            out = channel - 1          # old firmware: fixed mapping
        chip, q = out_to_chip(out)
        return f"CH {channel} -> OUT {out} -> CHIP {chip}, Q{q}"

    def stats_text(self):
        c = self.counters
        rtt = sorted(self.rtt_ms)
        if rtt:
            p99 = rtt[min(len(rtt) - 1, int(len(rtt) * 0.99))]
            rtt_s = f"RTT {rtt[len(rtt) // 2]:.1f}/{p99:.1f}/{rtt[-1]:.1f} ms"
        else:
            rtt_s = "RTT -"
        return (f"{self.mode or 'OFFLINE'} fw {self.fw_version or '-'} state {self.esp_state or '-'} | "
                f"sent {c['sent']} ok {c['ack_ok']} busy {c['ack_busy'] + c['ack_partial_busy']} "
                f"tripped {c['ack_tripped']} no-ack {c['no_ack']} crc {c['rx_crc_err']} | {rtt_s} (p50/p99/max)")
