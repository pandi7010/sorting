"""
latency_report.py - turn Phase 2 timing logs into a latency report (no hardware needed to run it).

    python tools\\latency_report.py                     (newest log session in logs\\)
    python tools\\latency_report.py --session 20261016_101500 --speed 3.5

Inputs : logs/timing_<stamp>_frames.csv, _fires.csv, _acks.csv  (written when timing_log_enabled = true)
         logs/ball_speed_*.json (newest, if present) for product speed; else --speed (default 3.5 m/s)
Output : printed summary + logs/latency_report_<stamp>.md

STAGES (one defect, from the moment its line was captured):
  A  frame filling  : the line waits until the frame is complete (0 .. frame height x line period)
  B  transfer       : camera -> PC network delay (only the VARIATION is measurable; a minimum is assumed)
  C  PC processing  : frame arrived -> defect decision ready (incl. waiting for the CPU)
  D  scheduler      : extra delay beyond the configured eject delay (Windows/Python lateness)
  E  USB + ESP32    : command sent -> ESP32 has switched the valve (from the ESP32's ACK time)
  F  valve opening  : 1 ms (your valves)
"Decision latency" = A + B + C + D + E + F, WITHOUT the configured delay. In the Phase 3 design the
ESP32 fires at exactly the right line as long as decision latency < travel time to the nozzle.
"""

import argparse
import csv
import glob
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
DISTANCES_MM = [35, 40, 55, 70]


def pct(values, p):
    if not values:
        return float("nan")
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))
    return s[k]


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fnum(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def esp_clock_model(acks):
    """Fit esp_seconds = a + b * pc_mid using the fastest round trips (least affected by delays).
    esp_us is a 32-bit microsecond counter that wraps every 71.6 minutes -> unwrap first."""
    pts = []
    wrap = 0
    last = None
    for r in acks:
        e = fnum(r["esp_us"])
        ts, tr = fnum(r["t_sent"]), fnum(r["t_rx"])
        if e is None or ts is None or tr is None:
            continue
        if last is not None and e < last - 2 ** 31:
            wrap += 2 ** 32
        last = e
        pts.append(((ts + tr) / 2.0, (e + wrap) / 1e6, tr - ts, ts, tr))
    if len(pts) < 5:
        return None
    pts.sort(key=lambda p: p[0])
    # fastest 20 % in each of 10 time windows
    best = []
    n = len(pts)
    for w in range(10):
        chunk = pts[w * n // 10:(w + 1) * n // 10]
        if chunk:
            chunk = sorted(chunk, key=lambda p: p[2])[:max(1, len(chunk) // 5)]
            best += chunk
    xs = [p[0] for p in best]
    ys = [p[1] for p in best]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    b = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx) if sxx > 0 else 1.0
    a = my - b * mx
    rtts = [p[2] * 1000 for p in pts]
    return {"a": a, "b": b, "rtt_ms_p50": pct(rtts, 50), "rtt_ms_p99": pct(rtts, 99),
            "unwrap": True, "n": len(pts)}


def load_speed(args):
    if args.speed:
        return args.speed, args.speed_sd or 0.0, "command line"
    files = sorted(glob.glob(os.path.join(LOG_DIR, "ball_speed_*.json")))
    files = [f for f in files if not f.endswith("_SIM.json")] or files
    if files:
        with open(files[-1]) as f:
            data = json.load(f)
        res = [r for r in data.get("results", []) if r.get("mean_mps")]
        if res:
            means = [r["mean_mps"] for r in res]
            sds = [r.get("std_mps", 0.0) for r in res]
            return statistics.fmean(means), max(sds), os.path.basename(files[-1])
    return 3.5, 0.0, "default (not measured!)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", help="timestamp of the log session (default: newest)")
    ap.add_argument("--log-dir", default=LOG_DIR)
    ap.add_argument("--speed", type=float, help="product speed m/s (default: newest ball_speed json)")
    ap.add_argument("--speed-sd", type=float)
    ap.add_argument("--valve-ms", type=float, default=1.0)
    ap.add_argument("--min-transfer-ms", type=float, default=1.0,
                    help="assumed minimum camera->PC transfer (only its variation is measured)")
    args = ap.parse_args()

    log_dir = args.log_dir
    if args.session:
        stamp = args.session
    else:
        sessions = sorted(glob.glob(os.path.join(log_dir, "timing_*_frames.csv")))
        if not sessions:
            sys.exit("No timing logs found. Switch on timing_log_enabled in features_config.json and run the sorter.")
        stamp = os.path.basename(sessions[-1])[len("timing_"):-len("_frames.csv")]
    base = os.path.join(log_dir, f"timing_{stamp}")
    frames = read_csv(base + "_frames.csv")
    fires = read_csv(base + "_fires.csv")
    acks = read_csv(base + "_acks.csv")
    speed, speed_sd, speed_src = load_speed(args)

    out_lines = []

    def out(s=""):
        print(s)
        out_lines.append(s)

    out(f"# Latency report - session {stamp}")
    out(f"\nFrames: {len(frames)}   ejector commands: {len(fires)}   ESP32 ACKs: {len(acks)}")
    out(f"Product speed: {speed:.3f} m/s (+/- {speed_sd:.3f}) from {speed_src}")

    # ---------------- per camera: line period, frame fill, transfer jitter, processing
    stages = {}
    by_src = {}
    for r in frames:
        by_src.setdefault(r["source"], []).append(r)
    line_period = {}
    out("\n## Camera frames")
    out("| camera | frames | height (lines) | line period (us) | line rate (Hz) | frame time (ms) | dropped frames |")
    out("|---|---|---|---|---|---|---|")
    for src, rows in sorted(by_src.items()):
        ts = [fnum(r["cam_ts_01ms"]) for r in rows if fnum(r["cam_ts_01ms"]) is not None]
        h = statistics.median([fnum(r["height"], 0) for r in rows]) or 1
        dts = [((b - a) % 2 ** 32) / 10.0 for a, b in zip(ts, ts[1:])]
        dts = [d for d in dts if d > 0]
        med = statistics.median(dts) if dts else float("nan")
        dropped = sum(1 for d in dts if d > 1.5 * med)
        lp_us = med * 1000.0 / h if dts else float("nan")
        line_period[src] = lp_us
        out(f"| {src} | {len(rows)} | {h:.0f} | {lp_us:.2f} | {1e6 / lp_us:,.0f} | {med:.2f} | {dropped} |")
        # B: transfer variation
        d = [fnum(r["t_arrive"]) - fnum(r["cam_ts_01ms"]) * 1e-4 for r in rows
             if fnum(r["t_arrive"]) is not None and fnum(r["cam_ts_01ms"]) is not None]
        if d:
            # camera clock vs PC clock may drift slowly: remove a straight-line trend first
            n = len(d)
            xs = list(range(n))
            mx, my = statistics.fmean(xs), statistics.fmean(d)
            sxx = sum((x - mx) ** 2 for x in xs) or 1
            b = sum((x - mx) * (y - my) for x, y in zip(xs, d)) / sxx
            resid = [y - b * x for x, y in zip(xs, d)]
            mn = min(resid)
            stages.setdefault("B transfer (variation) ms", []).extend((v - mn) * 1000 for v in resid)
        stages.setdefault("A frame fill, oldest row ms", []).append(h * lp_us / 1000.0)
        stages.setdefault("C processing ms", []).extend(
            (fnum(r["t_proc_end"]) - fnum(r["t_arrive"])) * 1000 for r in rows
            if fnum(r["t_proc_end"]) is not None and fnum(r["t_arrive"]) is not None)

    # ---------------- fires: scheduler lateness and USB/ESP32
    model = esp_clock_model(acks)
    ack_by_seq = {}
    for r in acks:
        ack_by_seq.setdefault(r["seq"], r)
    sched_late, usb = [], []
    for r in fires:
        t_target, t_send = fnum(r["t_target"]), fnum(r["t_send"])
        if t_target is not None and t_send is not None:
            sched_late.append(max(0.0, (t_send - t_target) * 1000))
        a = ack_by_seq.get(r.get("seq"))
        if model and a and t_send is not None:
            # unwrap this ACK relative to the fitted line, then convert to PC time
            e = fnum(a["esp_us"]) / 1e6
            pc_guess = (fnum(a["t_rx"]))
            esp_pred = model["a"] + model["b"] * pc_guess
            k = round((esp_pred - e) / (2 ** 32 / 1e6))
            e += k * (2 ** 32 / 1e6)
            t_esp_pc = (e - model["a"]) / model["b"]
            usb.append((t_esp_pc - t_send) * 1000)
    stages["D scheduler lateness ms"] = sched_late
    stages["E USB + ESP32 ms"] = usb

    out("\n## Stages (milliseconds)")
    out("| stage | samples | p50 | p95 | p99 | max |")
    out("|---|---|---|---|---|---|")
    for name in ["A frame fill, oldest row ms", "B transfer (variation) ms", "C processing ms",
                 "D scheduler lateness ms", "E USB + ESP32 ms"]:
        v = stages.get(name, [])
        out(f"| {name} | {len(v)} | {pct(v, 50):.2f} | {pct(v, 95):.2f} | {pct(v, 99):.2f} | {max(v) if v else float('nan'):.2f} |")
    if model:
        out(f"\nESP32 clock fitted from {model['n']} ACKs; USB round trip p50 {model['rtt_ms_p50']:.2f} ms, "
            f"p99 {model['rtt_ms_p99']:.2f} ms.")
    else:
        out("\n(No ESP32 ACK timing: stage E could not be measured. Needs binary firmware v2.0 + timing log.)")

    # ---------------- decision latency
    A = statistics.fmean(stages.get("A frame fill, oldest row ms", [0])) if stages.get("A frame fill, oldest row ms") else 0
    def p(name, q):
        v = stages.get(name, [])
        return pct(v, q) if v else 0.0
    newest = {q: args.min_transfer_ms + p("B transfer (variation) ms", q) + p("C processing ms", q)
              + p("D scheduler lateness ms", q) + p("E USB + ESP32 ms", q) + args.valve_ms for q in (50, 99)}
    oldest = {q: newest[q] + A for q in (50, 99)}
    out("\n## Decision latency (line captured -> air on the product, WITHOUT the configured delay)")
    out(f"- newest line of a frame: p50 {newest[50]:.1f} ms, p99 {newest[99]:.1f} ms")
    out(f"- oldest line of a frame: p50 {oldest[50]:.1f} ms, p99 {oldest[99]:.1f} ms  (frame fill adds {A:.1f} ms)")
    out(f"  (assumed minimum transfer {args.min_transfer_ms} ms and valve {args.valve_ms} ms)")

    # ---------------- distance decision
    out("\n## Nozzle distance check")
    out(f"Speed {speed:.2f} m/s. 'Late' = the decision (p99, oldest line) arrives after the product passed the nozzle.")
    out("| distance | travel time | margin vs p99 latency | result | position spread with Phase 1 timing (p1..p99) | expected error with Phase 3 line sync |")
    out("|---|---|---|---|---|---|")
    total_spread_ms = (oldest[99] - newest[50]) if stages else float("nan")
    lp = statistics.fmean([v for v in line_period.values() if v == v]) if line_period else 100.0
    recommend = None
    for d in DISTANCES_MM:
        travel = d / speed
        margin = travel - oldest[99]
        ok = margin > 0
        spread_mm = speed * total_spread_ms
        sync_err = d * (speed_sd / speed if speed else 0) + speed * lp / 1000.0
        if ok and recommend is None:
            recommend = d
        out(f"| {d} mm | {travel:.1f} ms | {margin:+.1f} ms | {'OK' if ok else 'TOO SHORT'} | "
            f"+/-{spread_mm / 2:.1f} mm | +/-{sync_err:.1f} mm |")
    if recommend:
        out(f"\n**Smallest distance that fits the measured p99 latency: {recommend} mm.**")
    else:
        out("\n**None of 35-70 mm fits the measured p99 latency: reduce frame height / processing time first (Phase 3).**")
    out("Phase 1 timing: the position spread comes from the random row position inside the frame and the "
        "varying PC delays. Phase 3 line sync removes these; only speed variation and 1-line steps remain.")

    # ---------------- whole objects
    try:
        with open(os.path.join(ROOT, "product_presets.json")) as f:
            presets = json.load(f)["products"]
    except Exception:
        presets = {}
    if presets:
        out("\n## Whole objects: time from the object's LAST line to its CENTRE reaching the nozzle")
        out("(A whole-object decision, e.g. a CNN on a cashew crop, can only start after the last line.)")
        out("| product | length | " + " | ".join(f"{d} mm" for d in DISTANCES_MM) + " |")
        out("|---|---|" + "---|" * len(DISTANCES_MM))
        for name, pr in presets.items():
            L = pr.get("length_mm", 0)
            cells = []
            for d in DISTANCES_MM:
                t = (d - L / 2.0) / speed - args.valve_ms
                cells.append(f"{t:.1f} ms" if t > 0 else "none")
            out(f"| {name} | {L} mm | " + " | ".join(cells) + " |")
        out("Compare these times with 'decision latency (newest line)' plus the classifier time per object.")

    path = os.path.join(log_dir, f"latency_report_{stamp}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"\nSaved: {path}")
    return {"stages": stages, "newest": newest, "oldest": oldest, "recommend": recommend, "model": model}


if __name__ == "__main__":
    main()
