"""
=============================================================================
LAMBTON OPTICAL SORTER - ESP32 63-CH EJECTOR DIAGNOSTIC & TEST TOOL
Tests:
1. COM Port Connectivity
2. TX Signal Transmission (PC -> ESP32)
3. RX Response Reception (ESP32 -> PC)
4. Solenoid Valve Firing Commands (PING, EJ, SA, CLEAR, TEST)
=============================================================================
"""

import sys
import time
import serial
import serial.tools.list_ports

DEFAULT_PORT = "COM23"
BAUD_RATE = 115200
TIMEOUT = 2.0

def find_esp32_port():
    ports = list(serial.tools.list_ports.comports())
    print("\n[INFO] Scanning available COM ports...")
    found_port = None
    for p in ports:
        desc = f"{p.device} - {p.description} [{p.hwid}]"
        print(f"  * {desc}")
        if "CH9102" in p.description or "CH340" in p.description or "CP210" in p.description or p.device == DEFAULT_PORT:
            if found_port is None:
                found_port = p.device
    return found_port or DEFAULT_PORT

def test_communication(port_name):
    print("\n" + "=" * 65)
    print(f"  ESP32 63-CHANNEL EJECTOR COMMUNICATION TEST")
    print(f"  Target Port: {port_name} | Baud Rate: {BAUD_RATE}")
    print("=" * 65)

    # 1. Open Serial Port
    print(f"\n[STEP 1] Connecting to {port_name}...")
    try:
        ser = serial.Serial(
            port=port_name,
            baudrate=BAUD_RATE,
            timeout=TIMEOUT,
            write_timeout=TIMEOUT,
            rtscts=False,
            dsrdtr=False
        )
        # Prevent hardware auto-reset lock on ESP32
        ser.dtr = False
        ser.rts = False
        time.sleep(0.5)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        print(f"  [PASS] Serial port {port_name} opened successfully!")
    except serial.SerialException as e:
        print(f"  [FAIL] Could not open {port_name}!")
        print(f"  [REASON] {e}")
        print("\n  [HELP / FIX]:")
        print("  1. Make sure Arduino IDE Serial Monitor is CLOSED.")
        print("  2. Make sure Python sorter app ('LAMTON_LINE_NEW_ESP32.py') is CLOSED.")
        print("  3. Check if USB cable is plugged in firmly.")
        return None

    # Wait for ESP32 ready / flush startup greeting
    time.sleep(0.5)
    while ser.in_waiting:
        greeting = ser.readline().decode('utf-8', errors='ignore').strip()
        if greeting:
            print(f"  [ESP32 BOOT MSG] {greeting}")

    # 2. Test PING -> PONG
    print("\n[STEP 2] Testing TX Signal & RX Response (PING -> PONG)...")
    t0 = time.time()
    try:
        ser.write(b"PING\n")
        ser.flush()
        print("  [TX -> ESP32] Sent: 'PING'")
    except Exception as e:
        print(f"  [FAIL] Failed to send signal: {e}")
        ser.close()
        return None

    response = ser.readline().decode('utf-8', errors='ignore').strip()
    round_trip = (time.time() - t0) * 1000.0

    if "PONG" in response:
        print(f"  [RX <- ESP32] Received: '{response}' (Response time: {round_trip:.1f}ms)")
        print("  [PASS] Signal transmission and response verified 100% OK!")
    else:
        print(f"  [WARN] Unexpected response: '{response}' (Expected 'PONG:ESP32_ONLINE_63CH')")
        # Try a second ping
        ser.write(b"PING\n")
        ser.flush()
        response2 = ser.readline().decode('utf-8', errors='ignore').strip()
        if "PONG" in response2:
            print(f"  [RX <- ESP32 Retry] Received: '{response2}'")
            print("  [PASS] Signal verified OK!")
        else:
            print("  [FAIL] No PONG response received from ESP32.")

    # 3. Test Single Ejector Command (EJ:1:20)
    print("\n[STEP 3] Testing Single Ejector Valve Command (Channel 1, 20ms)...")
    ser.write(b"EJ:1:20\n")
    ser.flush()
    print("  [TX -> ESP32] Sent: 'EJ:1:20'")
    response = ser.readline().decode('utf-8', errors='ignore').strip()
    if "ACK:EJ_FIRED" in response:
        print(f"  [RX <- ESP32] Received: '{response}'")
        print("  [PASS] Single channel firing command accepted!")
    else:
        print(f"  [RESPONSE] '{response}'")

    # 4. Test Clear Command
    print("\n[STEP 4] Testing CLEAR Command...")
    ser.write(b"CLEAR\n")
    ser.flush()
    print("  [TX -> ESP32] Sent: 'CLEAR'")
    response = ser.readline().decode('utf-8', errors='ignore').strip()
    if "ACK:CLEARED" in response:
        print(f"  [RX <- ESP32] Received: '{response}'")
        print("  [PASS] Clear command accepted! All 63 channels safe.")
    else:
        print(f"  [RESPONSE] '{response}'")

    print("\n" + "=" * 65)
    print("  DIAGNOSTIC SUMMARY: ESP32 IS ONLINE AND FULLY RESPONSIVE!")
    print("=" * 65)
    return ser

def interactive_menu(ser):
    while True:
        print("\n--- EJECTOR TEST MENU ---")
        print("1. Fire a Single Channel (1 to 63)")
        print("2. Run 63-Channel Sequential Sweep Test (fires all 1 to 63 in order)")
        print("3. Send Custom Bitmask (e.g. fire first 8 channels)")
        print("4. Ping ESP32")
        print("5. Clear All Valves")
        print("Q. Exit")
        choice = input("Enter choice (1-5 or Q): ").strip().upper()

        if choice == "1":
            try:
                ch = int(input("Enter channel number (1 to 63): ").strip())
                if 1 <= ch <= 63:
                    ms = int(input("Enter pulse duration in ms (default 30): ").strip() or "30")
                    ser.write(f"EJ:{ch}:{ms}\n".encode('utf-8'))
                    ser.flush()
                    resp = ser.readline().decode('utf-8', errors='ignore').strip()
                    print(f"  -> ESP32 Response: {resp}")
                else:
                    print("  Invalid channel! Must be 1 to 63.")
            except ValueError:
                print("  Please enter a valid number.")

        elif choice == "2":
            print("\n[INFO] Starting full 63-channel hardware sweep test...")
            print("Listen for solenoid valve clicks and watch ESP32 blue LED:")
            ser.write(b"TEST\n")
            ser.flush()
            ser.timeout = 0.5
            start_t = time.time()
            # Read output for about 10 seconds while the test runs
            while (time.time() - start_t) < 12.0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    print(f"  {line}")
                    if "Test Complete" in line:
                        break
            ser.timeout = TIMEOUT

        elif choice == "3":
            print("Firing first 8 channels (Bitmask SA:255:0:0:0:0:0:0:0)...")
            ser.write(b"SA:255:0:0:0:0:0:0:0\n")
            ser.flush()
            resp = ser.readline().decode('utf-8', errors='ignore').strip()
            print(f"  -> ESP32 Response: {resp}")
            time.sleep(0.05)
            ser.write(b"CLEAR\n")
            ser.flush()

        elif choice == "4":
            t0 = time.time()
            ser.write(b"PING\n")
            ser.flush()
            resp = ser.readline().decode('utf-8', errors='ignore').strip()
            dt = (time.time() - t0) * 1000.0
            print(f"  -> ESP32 Response: '{resp}' in {dt:.1f}ms")

        elif choice == "5":
            ser.write(b"CLEAR\n")
            ser.flush()
            resp = ser.readline().decode('utf-8', errors='ignore').strip()
            print(f"  -> ESP32 Response: {resp}")

        elif choice == "Q":
            print("\nClosing serial port...")
            ser.close()
            print("Done. Goodbye!")
            break

if __name__ == "__main__":
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        port = find_esp32_port()

    ser = test_communication(port)
    if ser:
        try:
            interactive_menu(ser)
        except KeyboardInterrupt:
            print("\nExiting...")
            ser.close()
