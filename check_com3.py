import serial
import time

try:
    s = serial.Serial('COM3', 9600, timeout=1.0)
    print("Opened COM3 at 9600 successfully.")
    start = time.time()
    while time.time() - start < 3.0:
        if s.in_waiting:
            data = s.read(s.in_waiting)
            print("Received from ESP32:", data.hex(' '))
        time.sleep(0.1)
    s.close()
except Exception as e:
    print("COM3 Error:", e)
