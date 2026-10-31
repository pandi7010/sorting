import serial
import time

PORT = 'COM24'

def test_baud(baud):
    print(f"Testing {baud} baud...")
    try:
        s = serial.Serial(PORT, baud, timeout=3.0)
        # RS485 adapters usually need default DTR/RTS (True) to receive.
        # We will not touch them.
        start_time = time.time()
        data = b""
        while time.time() - start_time < 3.0:
            if s.in_waiting:
                data += s.read(s.in_waiting)
                if len(data) >= 8:
                    break
            time.sleep(0.1)
        s.close()
        
        if data:
            print(f"  -> RECEIVED DATA at {baud} baud: {data.hex().upper()}")
            return True
        else:
            print(f"  -> No data received at {baud} baud.")
            return False
    except Exception as e:
        print(f"  -> Error opening port: {e}")
        return False

print("--- MODBUS RS485 DIAGNOSTIC ---")
print("Make sure LAMTON_LINE_NEW_ESP32.py and relay.py are CLOSED before running this.\n")

if not test_baud(9600):
    test_baud(115200)

print("\nDiagnostics complete.")
