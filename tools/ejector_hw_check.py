"""
Ejector board connection check (PHASE1_GUIDE T2) + one single-channel test, from the command line.

    python tools\ejector_hw_check.py                       # finds the ejector on ANY COM port by itself (T2 only)
    python tools\ejector_hw_check.py --channel 1           # + fire channel 1 three times (30 ms, 500 ms gap)
    python tools\ejector_hw_check.py PORTNAME ...          # use this port (the name Windows shows) instead of auto detect

Close the sorter program and any Serial Monitor first (only one program can use the COM port).
Safe without 24 V on the valves: you then only see the 595/drive-board LEDs.
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ejector_link as el  # noqa: E402
import port_detect  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", help="COM port name; omitted = auto detect")
    ap.add_argument("--channel", type=int, default=0, help="1-63: fire this channel (0 = no firing)")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--pulse", type=float, default=30.0, help="ms")
    ap.add_argument("--gap", type=float, default=500.0, help="ms")
    a = ap.parse_args()
    if not a.port:
        print("Auto detect: asking every COM port who it is ...")
        found = [r for r in port_detect.scan(want_relay=False).values() if r.kind in (port_detect.EJECTOR, port_detect.LEGACY)]
        if not found:
            print("RESULT: NO EJECTOR FOUND on any COM port (cable, power, driver, or the port is used by another program)")
            return 2
        found.sort(key=lambda r: port_detect._natural(r.port))
        a.port = found[0].port
        print(f"Ejector found on {a.port}: {found[0].detail}")

    events = []
    link = el.EjectorLink(a.port, on_event=lambda n, arg: events.append((n, arg)),
                          on_text=lambda line: None)
    mode = link.connect()
    for line in list(link.text_lines):
        print(line)
    if not mode:
        print("RESULT: NO FIRMWARE ANSWERED (wrong port, port busy, or board not running)")
        return 2
    print(f"Mode: {mode}   firmware: v{link.fw_version or '? (old text firmware)'}")
    ok = True
    if mode == "BINARY":
        st = link.request_status()
        print("Status:", {k: st.get(k) for k in ["fw", "state", "rx_ok", "crc_err", "len_err", "wdt_trips", "fired"]})
        ok = st.get("crc_err") == 0 and st.get("len_err") == 0
        print(f"T2: badge would show 'v{link.fw_version} BINARY ({a.port})'   crc_err={st.get('crc_err')} "
              f"len_err={st.get('len_err')}  -> {'PASS' if ok else 'FAIL'}")

    if 1 <= a.channel <= el.NUM_CHANNELS:
        print(f"Single test: {link.channel_info(a.channel)}, {a.repeat} x {a.pulse} ms, gap {a.gap} ms")
        link.test_single(a.channel, a.pulse, a.gap, a.repeat)
        time.sleep(a.repeat * (a.pulse + a.gap) / 1000.0 + 0.5)
        if mode == "BINARY":
            st = link.request_status()
            print(f"After test: fired={st.get('fired')} crc_err={st.get('crc_err')} state={st.get('state')}")
            print("Events:", events[-6:])
    print("Link:", link.stats_text())
    link.close()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
