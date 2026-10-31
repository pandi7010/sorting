"""
camera_probe.py - READ-ONLY MindVision line-scan camera inspection + settings backup.

What it does (for every connected camera):
  1. Prints model, serial, firmware, interface and GigE network info.
  2. Prints capabilities: max resolution, trigger modes, exposure limits,
     camera IO pins and what they can do (trigger input, encoder input, strobe...).
  3. Prints the CURRENT settings: frame height, line rate, exposure,
     trigger mode, gains, IO modes, strobe...
  4. Saves a BACKUP of all current settings:
        camera_backups/<date_time>/<ROLE>_<SN>.config   (MindVision format, restorable)
        camera_backups/<date_time>/<ROLE>_<SN>.json     (human readable)
        camera_backups/<date_time>/probe_report.txt     (everything printed on screen)
  5. Watches the live stream for a few seconds (no settings changed) and measures
     the REAL line rate from the camera's own timestamps.

It does NOT change any camera setting, unless you add  --probe-max-rate
(that option sets line rate to "maximum" for a few seconds to measure it,
then puts the original value back and checks it).

IMPORTANT: close the sorter program first - a camera can only be opened by one program.

Usage (from the LAMTON_V1 folder):
    python tools\\camera_probe.py
    python tools\\camera_probe.py --seconds 5
    python tools\\camera_probe.py --probe-max-rate
"""

import argparse
import datetime
import json
import os
import sys
import time
from ctypes import byref, c_int, c_uint, create_string_buffer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mvsdk  # noqa: E402

TRIGGER_MODE_NAMES = {0: "CONTINUOUS (free-run)", 1: "SOFTWARE trigger", 2: "HARDWARE (external) trigger",
                      3: "ENCODER trigger (line scan)", 4: "ENCODER CONDITION trigger (line scan)"}
IO_MODE_NAMES = {0: "TRIG_INPUT", 1: "STROBE_OUTPUT", 2: "GP_INPUT", 3: "GP_OUTPUT",
                 4: "PWM_OUTPUT", 5: "ROTARYENC_INPUT (encoder)", 6: "LINE_STROBE_OUTPUT"}
IO_FORMAT_NAMES = {0: "SINGLE-ENDED", 1: "RS422 differential", 2: "RS422 + termination", 3: "OPTO-COUPLER"}
EXT_TRIG_SIGNAL_NAMES = {0: "rising edge", 1: "falling edge", 2: "high level", 3: "low level", 4: "both edges"}


class Report:
    """Prints to screen and keeps a copy for probe_report.txt."""

    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        print(text)
        self.lines.append(text)


def sdk_get(func_name, *in_args, out_types=(c_int,)):
    """Call an SDK getter directly (for functions mvsdk.py does not wrap).
    Returns (error_code, [values]). Never raises."""
    func = getattr(mvsdk._sdk, func_name, None)
    if func is None:
        return ("NOT_IN_DLL", [])
    outs = [t() for t in out_types]
    try:
        err = func(*in_args, *[byref(o) for o in outs])
    except Exception as exc:  # wrong signature / access violation guard
        return (f"EXC {exc}", [])
    return (err, [o.value for o in outs])


def safe(label, fn, *args):
    """Run a mvsdk getter, return its value or an error string."""
    try:
        value = fn(*args)
        err = mvsdk.GetLastError()
        if err not in (0, None):
            return f"<error {err}: {mvsdk.CameraGetErrorString(err)}>"
        return value
    except Exception as exc:
        return f"<error {exc}>"


def bits_to_names(mask, names):
    if not isinstance(mask, int):
        return str(mask)
    found = [name for bit, name in names.items() if mask & (1 << bit)]
    return ", ".join(found) if found else "none"


def load_roles():
    """Map serial number -> FRONT/REAR from camera_config.json (read only)."""
    roles = {}
    try:
        with open(os.path.join(ROOT, "camera_config.json"), "r") as f:
            cfg = json.load(f)
        for role, data in cfg.items():
            sn = str(data.get("sn", "")).strip()
            if sn:
                roles[sn] = role
    except Exception:
        pass
    return roles


def measure_stream(h, seconds, out):
    """Grab frames without changing settings; measure line rate from camera timestamps.
    Frame header timestamp unit = 0.1 ms."""
    mvsdk.CameraPlay(h)
    heights, cam_ts, pc_ts = [], [], []
    timeouts = 0
    t_end = time.perf_counter() + seconds
    while time.perf_counter() < t_end:
        try:
            p_raw, head = mvsdk.CameraGetImageBuffer(h, 1000)
            pc_ts.append(time.perf_counter())
            heights.append(head.iHeight)
            cam_ts.append(head.uiTimeStamp)
            mvsdk.CameraReleaseImageBuffer(h, p_raw)
        except mvsdk.CameraException as exc:
            timeouts += 1
            if timeouts >= 3:
                out(f"    No frames arriving ({exc}). Camera may be waiting for an external trigger.")
                break
    mvsdk.CameraPause(h)

    result = {"frames": len(heights), "timeouts": timeouts}
    if len(heights) < 3:
        out("    Not enough frames to measure.")
        return result

    height = max(set(heights), key=heights.count)
    # 32-bit timestamp in 0.1 ms; handle wrap
    dts = [((b - a) & 0xFFFFFFFF) / 10.0 for a, b in zip(cam_ts, cam_ts[1:])]
    pc_dts = [(b - a) * 1000.0 for a, b in zip(pc_ts, pc_ts[1:])]
    dts_sorted = sorted(dts)
    median_dt = dts_sorted[len(dts_sorted) // 2]
    total_ms = sum(dts)
    frames_measured = len(dts)
    line_rate = (frames_measured * height) / (total_ms / 1000.0) if total_ms > 0 else 0
    gaps = sum(1 for d in dts if median_dt > 0 and d > 1.5 * median_dt)

    result.update({
        "frame_height_lines": height,
        "median_frame_interval_ms": round(median_dt, 3),
        "measured_line_rate_hz": round(line_rate),
        "line_period_us": round(1e6 / line_rate, 2) if line_rate else None,
        "frame_rate_fps": round(1000.0 / median_dt, 2) if median_dt else None,
        "possible_dropped_frames": gaps,
        "pc_arrival_interval_ms_min_max": [round(min(pc_dts), 2), round(max(pc_dts), 2)],
    })
    out(f"    Frames grabbed            : {len(heights)}  (timeouts {timeouts})")
    out(f"    Frame height (from image) : {height} lines")
    out(f"    Frame interval (camera)   : {median_dt:.2f} ms  -> {result['frame_rate_fps']} frames/s")
    out(f"    MEASURED LINE RATE        : {line_rate:,.0f} lines/s  (line period {result['line_period_us']} us)")
    out(f"    Possible dropped frames   : {gaps}")
    out(f"    PC arrival interval       : min {min(pc_dts):.2f} ms, max {max(pc_dts):.2f} ms "
        f"(big max = PC/network delay spikes)")
    return result


def probe_camera(dev, index, roles, backup_dir, args, out):
    sn = dev.GetSn() if hasattr(dev, "GetSn") else dev.acSn.decode(errors="ignore").strip("\x00")
    role = roles.get(sn, f"CAM{index + 1}")
    info = {"role": role, "serial": sn}

    out("=" * 78)
    out(f" CAMERA {index + 1}: {role}   SN {sn}")
    out("=" * 78)

    # ---- 1. Identity (available before opening) ----
    identity = {
        "product_series": dev.GetProductSeries(),
        "product_name (MODEL)": dev.GetProductName(),
        "friendly_name": dev.GetFriendlyName(),
        "sensor_type": dev.GetSensorType(),
        "port_type": dev.GetPortType() if hasattr(dev, "GetPortType") else "",
        "driver_version": dev.GetDriverVersion(),
    }
    try:
        ip = mvsdk.CameraGigeGetIp(dev)
        mac = mvsdk.CameraGigeGetMac(dev)
        identity.update({"camera_ip": ip[0], "camera_mask": ip[1], "camera_gateway": ip[2],
                         "pc_nic_ip": ip[3], "pc_nic_mask": ip[4], "camera_mac": mac[0], "pc_nic_mac": mac[1]})
    except Exception as exc:
        identity["gige_info"] = f"<unavailable: {exc}>"
    info["identity"] = identity
    out(" [Identity]")
    for k, v in identity.items():
        out(f"    {k:28s}: {v}")

    # ---- open camera (loads its last-used settings, same as the sorter does) ----
    try:
        h = mvsdk.CameraInit(dev, -1, -1)
    except mvsdk.CameraException as exc:
        out(f"    !! Could not open camera: {exc}. Is the sorter program still running?")
        info["error"] = str(exc)
        return info

    try:
        info["firmware_version"] = safe("fw", mvsdk.CameraGetFirmwareVersion, h)
        info["interface_version"] = safe("if", mvsdk.CameraGetInerfaceVersion, h)
        out(f"    {'firmware_version':28s}: {info['firmware_version']}")

        # ---- 2. Capabilities ----
        cap = mvsdk.CameraGetCapability(h)
        rr = cap.sResolutionRange
        trig_modes = [f"{cap.pTriggerDesc[i].iIndex}: {cap.pTriggerDesc[i].GetDescription()}"
                      for i in range(cap.iTriggerDesc)]
        speeds = [f"{cap.pFrameSpeedDesc[i].iIndex}: {cap.pFrameSpeedDesc[i].GetDescription()}"
                  for i in range(cap.iFrameSpeedDesc)]
        line_time_us = safe("lt", mvsdk.CameraGetExposureLineTime, h)
        exp_range = safe("er", mvsdk.CameraGetExposureTimeRange, h)
        trig_cap = safe("tc", mvsdk.CameraGetExtTrigCapability, h)
        capability = {
            "max_width": rr.iWidthMax, "min_width": rr.iWidthMin,
            "max_height_lines": rr.iHeightMax, "min_height_lines": rr.iHeightMin,
            "colour_camera": not cap.sIspCapacity.bMonoSensor,
            "trigger_modes_supported": trig_modes,
            "frame_speed_modes": speeds,
            "exposure_range_us (min,max,step)": exp_range,
            "exposure_range_lines (min,max)": (cap.sExposeDesc.uiExposeTimeMin, cap.sExposeDesc.uiExposeTimeMax),
            "line_time_us (CameraGetExposureLineTime)": line_time_us,
            "analog_gain_range": (cap.sExposeDesc.uiAnalogGainMin, cap.sExposeDesc.uiAnalogGainMax,
                                  cap.sExposeDesc.fAnalogGainStep),
            "ext_trigger_capability_mask": trig_cap,
            "ext_trigger_capability": bits_to_names(trig_cap, {0: "GRR shutter", 1: "level trigger",
                                                              2: "double edge", 3: "buffered delay"}),
            "input_io_count": cap.iInputIoCounts,
            "output_io_count": cap.iOutputIoCounts,
            "params_stored_in_camera": bool(cap.bParamInDevice),
        }
        info["capability"] = capability
        out(" [Capabilities]")
        for k, v in capability.items():
            out(f"    {k:42s}: {v}")

        # ---- 3. Current settings ----
        res = mvsdk.CameraGetImageResolution(h)
        trig_mode = safe("tm", mvsdk.CameraGetTriggerMode, h)
        e_rate, v_rate = sdk_get("CameraGetFrameRate", h)
        e_splice, v_splice = sdk_get("CameraGetSpliceLines", h)
        e_enc, v_enc = sdk_get("CameraGetRotaryEncTrigger", h)
        e_encf, v_encf = sdk_get("CameraGetRotaryEncFreq", h, out_types=(c_int, c_int))
        exposure_us = safe("ex", mvsdk.CameraGetExposureTime, h)
        media = safe("mt", mvsdk.CameraGetMediaType, h)
        current = {
            "frame_height_lines (iHeight)": res.iHeight,
            "frame_width (iWidth)": res.iWidth,
            "roi_offset_x,y / fov_w,h": (res.iHOffsetFOV, res.iVOffsetFOV, res.iWidthFOV, res.iHeightFOV),
            "resolution_index (255=custom)": res.iIndex,
            "line_rate_setting_hz (CameraGetFrameRate, <=0 = max)":
                v_rate[0] if e_rate == 0 else f"<not supported: {e_rate}>",
            "splice_lines (CameraGetSpliceLines)": v_splice[0] if e_splice == 0 else f"<n/a: {e_splice}>",
            "trigger_mode": f"{trig_mode} = {TRIGGER_MODE_NAMES.get(trig_mode, '?')}",
            "trigger_count (frames per trigger)": safe("trc", mvsdk.CameraGetTriggerCount, h),
            "trigger_delay_us": safe("trd", mvsdk.CameraGetTriggerDelayTime, h),
            "ext_trig_signal": EXT_TRIG_SIGNAL_NAMES.get(safe("ets", mvsdk.CameraGetExtTrigSignalType, h), "?"),
            "ext_trig_shutter_type": safe("esh", mvsdk.CameraGetExtTrigShutterType, h),
            "ext_trig_delay_us": safe("etd", mvsdk.CameraGetExtTrigDelayTime, h),
            "ext_trig_jitter(debounce)_us": safe("etj", mvsdk.CameraGetExtTrigJitterTime, h),
            "ext_trig_interval_us": safe("eti", mvsdk.CameraGetExtTrigIntervalTime, h),
            "encoder_trigger_active": v_enc[0] if e_enc == 0 else f"<n/a: {e_enc}>",
            "encoder_freq (mul,div)": tuple(v_encf) if e_encf == 0 else f"<n/a: {e_encf}>",
            "exposure_us": exposure_us,
            "max_line_rate_for_this_exposure_hz":
                round(1e6 / exposure_us) if isinstance(exposure_us, (int, float)) and exposure_us > 0 else None,
            "auto_exposure": safe("ae", mvsdk.CameraGetAeState, h),
            "analog_gain": safe("ag", mvsdk.CameraGetAnalogGain, h),
            "rgb_gain": safe("rgb", mvsdk.CameraGetGain, h),
            "gamma": safe("g", mvsdk.CameraGetGamma, h),
            "contrast": safe("c", mvsdk.CameraGetContrast, h),
            "saturation": safe("s", mvsdk.CameraGetSaturation, h),
            "sharpness": safe("sh", mvsdk.CameraGetSharpness, h),
            "frame_speed_index": safe("fs", mvsdk.CameraGetFrameSpeed, h),
            "media_type (raw format from camera)": hex(media) if isinstance(media, int) else media,
            "parameter_group_in_use": safe("pg", mvsdk.CameraGetCurrentParameterGroup, h),
            "parameter_mode": safe("pm", mvsdk.CameraGetParameterMode, h),
        }
        info["current"] = current
        out(" [Current settings]")
        for k, v in current.items():
            out(f"    {k:52s}: {v}")

        # ---- camera IO pins ----
        ios = {"inputs": [], "outputs": []}
        for i in range(cap.iInputIoCounts):
            e1, m = sdk_get("CameraGetInPutIOMode", h, i)
            e2, mc = sdk_get("CameraGetInPutIOModeCapbility", h, i, out_types=(c_uint,))
            e3, fm = sdk_get("CameraGetInPutIOFormat", h, i)
            e4, fc = sdk_get("CameraGetInPutIOFormatCapbility", h, i, out_types=(c_uint,))
            ios["inputs"].append({
                "index": i,
                "mode": IO_MODE_NAMES.get(m[0], m[0]) if e1 == 0 else f"<{e1}>",
                "modes_supported": bits_to_names(mc[0], IO_MODE_NAMES) if e2 == 0 else f"<{e2}>",
                "format": IO_FORMAT_NAMES.get(fm[0], fm[0]) if e3 == 0 else f"<{e3}>",
                "formats_supported": bits_to_names(fc[0], IO_FORMAT_NAMES) if e4 == 0 else f"<{e4}>",
            })
        for i in range(cap.iOutputIoCounts):
            e1, m = sdk_get("CameraGetOutPutIOMode", h, i)
            e2, mc = sdk_get("CameraGetOutPutIOModeCapbility", h, i, out_types=(c_uint,))
            e3, fm = sdk_get("CameraGetOutPutIOFormat", h, i)
            ios["outputs"].append({
                "index": i,
                "mode": IO_MODE_NAMES.get(m[0], m[0]) if e1 == 0 else f"<{e1}>",
                "modes_supported": bits_to_names(mc[0], IO_MODE_NAMES) if e2 == 0 else f"<{e2}>",
                "format": IO_FORMAT_NAMES.get(fm[0], fm[0]) if e3 == 0 else f"<{e3}>",
            })
        ios["strobe"] = {
            "mode": safe("sm", mvsdk.CameraGetStrobeMode, h),
            "delay_us": safe("sd", mvsdk.CameraGetStrobeDelayTime, h),
            "pulse_width_us": safe("sw", mvsdk.CameraGetStrobePulseWidth, h),
            "polarity": safe("sp", mvsdk.CameraGetStrobePolarity, h),
        }
        info["io"] = ios
        out(" [Camera IO pins]")
        for p in ios["inputs"]:
            out(f"    INPUT  {p['index']}: mode={p['mode']}; can be: {p['modes_supported']}; "
                f"format={p['format']}; formats: {p['formats_supported']}")
        for p in ios["outputs"]:
            out(f"    OUTPUT {p['index']}: mode={p['mode']}; can be: {p['modes_supported']}; format={p['format']}")
        out(f"    strobe: {ios['strobe']}")

        # ---- SDK free-text info (sometimes lists max line rate) ----
        text = safe("info", mvsdk.CameraGetInformation, h)
        info["sdk_information_text"] = text
        if text and isinstance(text, str):
            out(" [SDK information text]")
            for line in text.splitlines():
                if line.strip():
                    out(f"    {line.strip()}")

        # ---- 4. Backup ----
        base = os.path.join(backup_dir, f"{role}_{sn}")
        err = mvsdk.CameraSaveParameterToFile(h, base + ".config")
        info["backup_config_file"] = base + ".config" if err == 0 else f"<save failed {err}>"
        out(" [Backup]")
        out(f"    MindVision settings file : {info['backup_config_file']}")

        # ---- 5. Live measurement (read-only) ----
        out(f" [Live stream measurement, {args.seconds} s, no settings changed]")
        info["measured"] = measure_stream(h, args.seconds, out)

        # ---- optional: maximum line rate ----
        if args.probe_max_rate:
            info["max_rate_probe"] = probe_max_rate(h, base + ".config", args, out)

        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2, default=str)
        out(f"    Readable JSON            : {base}.json")
    finally:
        mvsdk.CameraUnInit(h)
    return info


def probe_max_rate(h, backup_file, args, out):
    """Temporarily set line rate to maximum, measure, then restore and verify."""
    out(" [MAX LINE RATE PROBE - temporarily changes the line rate, then restores it]")
    e, v = sdk_get("CameraGetFrameRate", h)
    if e != 0:
        out(f"    CameraGetFrameRate not supported ({e}); cannot probe.")
        return {"error": e}
    original = v[0]
    result = {"original_setting_hz": original}
    try:
        err = mvsdk._sdk.CameraSetFrameRate(h, 0)  # 0 = maximum
        e2, v2 = sdk_get("CameraGetFrameRate", h)
        result["set_max_error"] = err
        result["setting_reads_back_as"] = v2[0] if e2 == 0 else None
        out(f"    Set to maximum (err {err}); reads back as {result['setting_reads_back_as']}")
        result["measured_at_max"] = measure_stream(h, args.seconds, out)
    finally:
        mvsdk._sdk.CameraSetFrameRate(h, original)
        e3, v3 = sdk_get("CameraGetFrameRate", h)
        restored = (e3 == 0 and v3[0] == original)
        if not restored:
            mvsdk.CameraReadParameterFromFile(h, backup_file)
            e3, v3 = sdk_get("CameraGetFrameRate", h)
            restored = (e3 == 0 and v3[0] == original)
        result["restored_ok"] = restored
        out(f"    Restored original line-rate setting {original}: {'OK' if restored else '!! CHECK MANUALLY !!'}")
    return result


def main():
    ap = argparse.ArgumentParser(description="Read-only MindVision camera probe and settings backup")
    ap.add_argument("--seconds", type=float, default=3.0, help="live measurement time per camera")
    ap.add_argument("--probe-max-rate", action="store_true",
                    help="temporarily set line rate to max to measure it (restores afterwards)")
    args = ap.parse_args()

    out = Report()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(ROOT, "camera_backups", stamp)
    os.makedirs(backup_dir, exist_ok=True)

    out(f"LAMTON camera probe  {stamp}   (read-only{'; MAX-RATE PROBE ON' if args.probe_max_rate else ''})")
    out(f"SDK version: {safe('v', mvsdk.CameraSdkGetVersionString)}")
    devs = mvsdk.CameraEnumerateDevice()
    out(f"Cameras found: {len(devs)}")
    roles = load_roles()

    summary = []
    for i, dev in enumerate(devs):
        info = probe_camera(dev, i, roles, backup_dir, args, out)
        summary.append(info)

    out("")
    out("=" * 78)
    out(" SUMMARY")
    out("=" * 78)
    for info in summary:
        cur = info.get("current", {})
        meas = info.get("measured", {})
        cap = info.get("capability", {})
        out(f" {info['role']:6s} {info.get('identity', {}).get('product_name (MODEL)', '?'):20s} "
            f"height={cur.get('frame_height_lines (iHeight)', '?')} lines, "
            f"line rate measured={meas.get('measured_line_rate_hz', '?')} Hz, "
            f"exposure={cur.get('exposure_us', '?')} us, "
            f"trigger modes={len(cap.get('trigger_modes_supported', []))}")
    out("")
    out(f"Backups and report saved in: {backup_dir}")

    with open(os.path.join(backup_dir, "probe_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out.lines))


if __name__ == "__main__":
    main()
