import serial
import time

try:
    s = serial.Serial('COM24', 9600)
    print("Sending test data to COM24...")
    for i in range(10):
        s.write(b"TEST MESSAGE FROM PC\n")
        s.flush()
        print("Sent message", i+1)
        time.sleep(0.5)
    s.close()
    print("Done. Did any LED blink on the ESP32 side?")
except Exception as e:
    print("Error:", e)
