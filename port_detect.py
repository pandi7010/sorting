"""
port_detect.py - find the ejector, relay and feeder boards on ANY COM port by their real replies.
No COM number is hard-coded anywhere. No Qt (usable from the sorter, the test panel, tools and SYSTEM_CHECK).

How a port is recognised (safest probe first):
  1. EJECTOR   text "\\nPING\\n" at 115200 (harmless for every device):
                 "PONG:LAMTON_EJECTOR_V2.3 P3 ID:AABBCCDDEEFF"  -> ejector v2.3 (then a binary IDENTIFY confirms it)
                 "ACK:PING" only                                -> OLD text ejector firmware (legacy)
               nothing at 115200 -> the same PING at 921600     -> ejector v2.0 - v2.2 (older binary firmware)
  2. RELAY     only on ports that did NOT answer as an ejector: Modbus RTU 9600, slave 1
                 read register 11 (v2.x relay firmware: version)  or  write register 200 = 0 (a register that does not
                 exist: the old relay firmware only echoes the frame, v2.x answers an exception) -> no relay is switched.
  3. FEEDER    (Nuvoton UART board) never answers anything, so it can NOT be recognised: it is never probed and never
               auto-assigned; its saved port is only checked for "present and can be opened".
A port that answered as the ejector never receives Modbus bytes. One port is never given to two devices.

    python port_detect.py            print what every COM port answers (read-only)
"""
import concurrent.futures
import re
import struct
import time

import serial
import serial.tools.list_ports

import ejector_link as el

EJECTOR_BAUD = el.BINARY_BAUD          # 115200 (v2.3 and the old text firmware)
OLD_EJECTOR_BAUD = el.OLD_BINARY_BAUD  # 921600 (v2.0 - v2.2)
RELAY_BAUD = 9600

# probe times in seconds (tests make them short)
TIMING = {"ejector": 2.0, "ejector_old": 1.0, "relay": 0.8, "open_settle": 0.05}

serial_factory = None      # tests: callable(port, baud) -> serial-like object
comports_func = None       # tests: callable() -> list of objects with .device .description .vid .pid ...

CHIPS = {0x10C4: "Silicon Labs CP210x", 0x1A86: "WCH CH340/CH9102", 0x0403: "FTDI", 0x303A: "Espressif USB",
         0x067B: "Prolific PL2303"}

EJECTOR, LEGACY, RELAY, SILENT, BUSY, ERROR = "EJECTOR", "EJECTOR_LEGACY", "RELAY", "SILENT", "BUSY", "ERROR"


class PortInfo:
    def __init__(self, device, description="", vid=None, pid=None, manufacturer="", hwid=""):
        self.device, self.description, self.vid, self.pid = device, description or "", vid, pid
        self.manufacturer, self.hwid = manufacturer or "", hwid or ""

    @property
    def chip(self):
        return CHIPS.get(self.vid, "")

    @property
    def label(self):
        """Text for a dropdown: port, USB chip and the description Windows shows."""
        chip = f" [{self.chip}]" if self.chip else ""
        return f"{self.device}{chip} - {self.description}" if self.description else f"{self.device}{chip}"


class Result:
    """What one port answered."""

    def __init__(self, port, kind, **kw):
        self.port, self.kind = port, kind
        self.fw = kw.get("fw")              # (major, minor) or None
        self.profile = kw.get("profile")    # ejector pin profile
        self.id = kw.get("id")              # ejector board ID (MAC)
        self.baud = kw.get("baud")
        self.detail = kw.get("detail", "")

    @property
    def ok(self):
        return self.kind in (EJECTOR, LEGACY, RELAY)

    def __repr__(self):
        return f"<{self.port}: {self.kind} {self.detail}>"


def _natural(dev):
    m = re.match(r"([A-Za-z]+)(\d+)$", dev)
    return (m.group(1), int(m.group(2))) if m else (dev, 0)


def list_ports():
    """All COM ports of this PC (natural order COM3, COM13, COM24 ...)."""
    raw = (comports_func or serial.tools.list_ports.comports)()
    out = [PortInfo(p.device, getattr(p, "description", ""), getattr(p, "vid", None), getattr(p, "pid", None),
                    getattr(p, "manufacturer", ""), getattr(p, "hwid", "")) for p in raw]
    return sorted(out, key=lambda p: _natural(p.device))


class _Busy(Exception):
    pass


def _open(port, baud):
    if serial_factory:
        return serial_factory(port, baud)
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, baud, 0.05, 0.5
    s.dtr = False                       # do not reset the ESP32 when opening
    s.rts = False
    try:
        s.open()
    except Exception as exc:
        text = f"{exc!r}"
        if "PermissionError" in text or "Access is denied" in text or "denied" in text.lower():
            raise _Busy(str(exc))
        raise
    return s


def _close(s):
    try:
        s.close()
    except Exception:
        pass


def _read_for(s, seconds, stop=None):
    """Read everything for up to `seconds` (or until stop(buf) is true)."""
    t_end, buf = time.time() + seconds, b""
    while time.time() < t_end:
        chunk = s.read(256)
        if chunk:
            buf += chunk
            if stop and stop(buf):
                break
        else:
            time.sleep(0.005)
    return buf


# ------------------------------------------------------------------------------------------ ejector
_PONG = re.compile(rb"PONG:LAMTON_EJECTOR_V(\d+)\.(\d+)(?: P(\d+))?(?: ID:([0-9A-Fa-f]{12}))?")


def _text_ping(port, baud, wait_s):
    """Open, send PING repeatedly until a reply or timeout. Returns (bytes, s) with the port still OPEN."""
    s = _open(port, baud)
    time.sleep(TIMING["open_settle"])
    t_end, buf, t_ping = time.time() + wait_s, b"", 0.0
    # Send P first for clean esp.py firmware detection
    s.write(b"P")
    t_ping = time.time()
    while time.time() < t_end:
        if time.time() - t_ping > 0.35:
            s.write(b"\nPING\n")
            t_ping = time.time()
        chunk = s.read(256)
        if chunk:
            buf += chunk
            if _PONG.search(buf) or b"ACK:PING" in buf or b"ESP32_ALIVE" in buf:
                buf += _read_for(s, 0.05)
                break
        else:
            time.sleep(0.005)
    return buf, s


def _binary_identify(s):
    """v2.3: binary IDENTIFY request on an open port. Returns the parsed identity dict or {}."""
    try:
        s.write(el.build_frame(el.T_IDENTIFY_REQ, 1, b""))
        buf = _read_for(s, 0.6, stop=lambda b: b"\xA5\x5A" in b and len(b) >= 26)
        frames, _ = el.FrameParser().feed(buf)
        for ftype, _seq, payload in frames:
            if ftype == el.T_IDENTIFY and len(payload) == 18:
                return el.parse_identify(payload)
    except Exception:
        pass
    return {}


def probe_ejector(port, try_old_baud=True):
    """Result(EJECTOR / LEGACY) if this port is an ejector board, Result(BUSY/ERROR) if it cannot be opened,
    None if it did not answer as an ejector (nothing is sent that could switch anything)."""
    steps = [(EJECTOR_BAUD, TIMING["ejector"])]
    if try_old_baud:
        steps.append((OLD_EJECTOR_BAUD, TIMING["ejector_old"]))
    for baud, wait_s in steps:
        try:
            buf, s = _text_ping(port, baud, wait_s)
        except _Busy as exc:
            return Result(port, BUSY, detail=f"in use by another program ({exc})")
        except Exception as exc:
            return Result(port, ERROR, detail=str(exc)[:120])
        try:
            m = _PONG.search(buf)
            if m:
                fw = (int(m.group(1)), int(m.group(2)))
                prof = int(m.group(3)) if m.group(3) else None
                bid = m.group(4).decode().upper() if m.group(4) else None
                ident = _binary_identify(s) if baud == EJECTOR_BAUD and fw >= (2, 3) else {}
                if ident:
                    prof, bid = ident.get("profile", prof), ident.get("id", bid)
                older = fw < (2, 3)
                detail = (f"ejector firmware v{fw[0]}.{fw[1]}" + (f", pin profile {prof}" if prof else "")
                          + (f", board {bid}" if bid else "") + (" (OLDER: runs at 921600, upload v2.3)" if older else ""))
                return Result(port, EJECTOR, fw=fw, profile=prof, id=bid, baud=baud, detail=detail)
            if (b"ACK:PING" in buf or b"ESP32_ALIVE" in buf) and baud == EJECTOR_BAUD:
                detail = "ESP32 63-CH Ejector (esp.py protocol)" if b"ESP32_ALIVE" in buf else "OLD text ejector firmware (ESP32_63CH_ShiftRegister)"
                return Result(port, EJECTOR, baud=baud, detail=detail)
        finally:
            _close(s)
    return None


# ------------------------------------------------------------------------------------------ relay
def _crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def probe_relay(port):
    """Modbus RTU 9600, slave 1. ONLY call this for a port that did not answer as an ejector.
    Never switches a relay. Returns Result(RELAY) / Result(BUSY/ERROR) / None."""
    try:
        s = _open(port, RELAY_BAUD)
    except _Busy as exc:
        return Result(port, BUSY, detail=f"in use by another program ({exc})")
    except Exception as exc:
        return Result(port, ERROR, detail=str(exc)[:120])
    try:
        time.sleep(TIMING["open_settle"])
        # a) v2.x relay firmware: read holding register 11 = firmware version
        req = bytes([1, 3, 0, 11, 0, 1])
        s.write(req + _crc16(req))
        buf = _read_for(s, TIMING["relay"], stop=lambda b: len(b) >= 7)
        if len(buf) >= 7 and buf[:3] == b"\x01\x03\x02" and _crc16(buf[:5]) == buf[5:7]:
            v = (buf[3] << 8) | buf[4]
            return Result(port, RELAY, fw=(v >> 8, v & 0xFF), baud=RELAY_BAUD,
                          detail=f"relay board firmware v{v >> 8}.{v & 0xFF} (Modbus 9600)")
        # b) old relay firmware: write register 200 (does not exist) = 0 -> echo (old) or exception (v2.x); no relay is switched
        req = bytes([1, 6, 0, 200, 0, 0])
        frame = req + _crc16(req)
        s.reset_input_buffer() if hasattr(s, "reset_input_buffer") else None
        s.write(frame)
        buf = _read_for(s, TIMING["relay"], stop=lambda b: len(b) >= 8)
        if buf[:8] == frame:
            return Result(port, RELAY, baud=RELAY_BAUD, detail="relay board, OLD firmware (echoes Modbus frames, 9600)")
        if len(buf) >= 5 and buf[0] == 1 and buf[1] == 0x86 and _crc16(buf[:3]) == buf[3:5]:
            return Result(port, RELAY, baud=RELAY_BAUD, detail="relay board (Modbus exception reply, 9600)")
        return None
    finally:
        _close(s)


# ------------------------------------------------------------------------------------------ one port / many ports
def identify_port(port, want_relay=True):
    """Everything this port answers, ejector first. Always a Result (SILENT if nobody answers)."""
    r = probe_ejector(port)
    if r is not None:
        return r
    if want_relay:
        r = probe_relay(port)
        if r is not None:
            return r
    return Result(port, SILENT, detail="no answer (powered off, feeder board, or unknown device)")


def scan(ports=None, want_relay=True, progress=None, skip=()):
    """Probe many ports in parallel (they are independent). Returns {port: Result}."""
    names = [p.device for p in (ports if ports is not None else list_ports()) if p.device not in skip]
    out = {}

    def work(dev):
        try:
            return dev, identify_port(dev, want_relay)
        except Exception as exc:
            return dev, Result(dev, ERROR, detail=str(exc)[:120])

    if not names:
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(names))) as ex:
        for dev, res in ex.map(work, names):
            out[dev] = res
            if progress:
                progress(f"{dev}: {res.kind} {res.detail}")
    return out


def verify(device, port):
    """Does `port` really hold `device` ('ejector' / 'relay' / 'feeder')? -> Result (check .ok)."""
    if device == "ejector":
        return probe_ejector(port) or Result(port, SILENT, detail="did not answer as an ejector board")
    if device == "relay":
        g = probe_ejector(port, try_old_baud=True)          # guard: never send Modbus to the ejector
        if g is not None and g.kind in (EJECTOR, LEGACY):
            return Result(port, g.kind, detail="this port is the EJECTOR board, not the relay board")
        if g is not None:
            return g
        return probe_relay(port) or Result(port, SILENT, detail="did not answer as a relay board")
    # feeder: the Nuvoton board never answers - only check that the port exists and can be opened, send NOTHING
    try:
        s = _open(port, 9600)
        _close(s)
        return Result(port, "FEEDER_UNVERIFIED", detail="port opens (the feeder board does not answer, cannot be verified)")
    except _Busy as exc:
        return Result(port, BUSY, detail=f"in use by another program ({exc})")
    except Exception as exc:
        return Result(port, ERROR, detail=str(exc)[:120])


def feeder_ok(result):
    return result.kind == "FEEDER_UNVERIFIED"


# ------------------------------------------------------------------------------------------ start-up resolution
DEVICES = ("ejector", "relay", "feeder")
CFG_PORT = {"ejector": "ejector_port", "relay": "last_port", "feeder": "vibrator_port"}      # keys in system_config.json
CFG_ENABLED = {"ejector": "ejector_enabled", "relay": "modbus_enabled", "feeder": "vibrator_enabled"}


def is_enabled(cfg, device):
    port = cfg.get(CFG_PORT[device])
    flag = cfg.get(CFG_ENABLED[device])
    if flag is not None:
        return bool(flag)
    return True if device != "feeder" else bool(port)     # feeder: only if a port was chosen


def resolve(cfg, progress=print, auto_detect=True):
    """Decide which port each ENABLED device uses.
    Order: the saved port if it answers as expected; otherwise auto detect over the free ports.
    Disabled devices are never opened or probed.
    Returns (assigned, messages, new_cfg_values)
       assigned    {device: Result or None}
       messages    list of text lines for the operator (what was found, what was changed)
       new_values  {config key: port} to save in system_config.json"""
    messages, new_values, assigned, claimed = [], {}, {d: None for d in DEVICES}, set()
    ports = list_ports()
    present = {p.device for p in ports}
    enabled = {d: is_enabled(cfg, d) for d in DEVICES}
    for d in DEVICES:
        if not enabled[d]:
            messages.append(f"{d}: disabled in the settings - no port is opened or probed")

    # 1. the saved port, if it answers as expected (ejector first, then relay, then feeder)
    for d in DEVICES:
        if not enabled[d]:
            continue
        sp = cfg.get(CFG_PORT[d])
        if not sp:
            continue
        if sp not in present:
            messages.append(f"{d}: saved port {sp} is not present on this PC")
            continue
        if sp in claimed:
            messages.append(f"{d}: saved port {sp} is already used by another device")
            continue
        r = verify(d, sp)
        if d == "feeder":
            if feeder_ok(r):
                assigned[d] = r
                claimed.add(sp)
            else:
                messages.append(f"feeder: saved port {sp}: {r.detail}")
        elif r.ok and ((d == "ejector" and r.kind in (EJECTOR, LEGACY)) or (d == "relay" and r.kind == RELAY)):
            assigned[d] = r
            claimed.add(sp)
            messages.append(f"{d}: {sp} - {r.detail}")
        else:
            messages.append(f"{d}: saved port {sp} did not answer as the {d} ({r.detail})")

    # 2. auto detect for enabled devices that still have no port (the feeder cannot be detected)
    need = [d for d in ("ejector", "relay") if enabled[d] and assigned[d] is None]
    if need and auto_detect:
        feeder_port = assigned["feeder"].port if assigned["feeder"] else None
        skip = set(claimed) | ({feeder_port} if feeder_port else set())
        # a saved feeder port that is still free must not receive probe bytes either
        if enabled["feeder"] and cfg.get(CFG_PORT["feeder"]) in present:
            skip.add(cfg.get(CFG_PORT["feeder"]))
        messages.append("auto detect: scanning " + ", ".join(sorted(present - skip, key=_natural)) + " ...")
        found = scan([p for p in ports if p.device not in skip], want_relay=("relay" in need), progress=progress)
        for d in need:
            kinds = (EJECTOR, LEGACY) if d == "ejector" else (RELAY,)
            cands = [r for dev, r in sorted(found.items(), key=lambda kv: _natural(kv[0]))
                     if r.kind in kinds and dev not in claimed]
            if cands:
                r = cands[0]
                assigned[d] = r
                claimed.add(r.port)
                old = cfg.get(CFG_PORT[d])
                messages.append(f"{d}: FOUND on {r.port} - {r.detail}"
                                + (f" (saved port {old} did not answer; {r.port} is saved now)" if old and old != r.port else ""))
                if len(cands) > 1:
                    messages.append(f"{d}: WARNING several boards answered: {', '.join(c.port for c in cands)} - using {r.port}")
            else:
                messages.append(f"{d}: NOT found on any COM port")
    elif need:
        for d in need:
            messages.append(f"{d}: no port (auto detect off)")
    if enabled["feeder"] and assigned["feeder"] is None:
        messages.append("feeder: no port - the feeder board never answers, so choose its port in COM PORTS (settings)")

    for d in DEVICES:
        if assigned[d] and cfg.get(CFG_PORT[d]) != assigned[d].port:
            new_values[CFG_PORT[d]] = assigned[d].port
    return assigned, messages, new_values


def report(ports=None):
    """Printable table: what every COM port answers."""
    ports = ports or list_ports()
    lines = []
    res = scan(ports)
    for p in ports:
        r = res.get(p.device)
        lines.append(f"{p.label}\n      -> {r.kind}: {r.detail}" if r else f"{p.label}\n      -> (skipped)")
    return "\n".join(lines) or "no COM ports found"


if __name__ == "__main__":
    print(report())
