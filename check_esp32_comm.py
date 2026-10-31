import serial
import time
import sys

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

print("="*65)
print("  ESP32 63-CHANNEL EJECTOR (COM3) LIVE COMMUNICATION TEST")
print("="*65)

try:
    print("\n[படி 1] COM3 போர்ட்டை திறக்கிறது (115200 Baud)...")
    s = serial.Serial('COM3', 115200, timeout=1.5)
    s.dtr = False
    s.rts = False
    
    # ESP32 தொடக்கத்திற்கு 1.5 வினாடி காத்திருப்பு
    print("        ESP32 பூட் ஆகிறது (Waiting for boot)...")
    time.sleep(1.5)
    
    # பூட் மெசேஜ்களைப் படித்தல்
    boot_banner = s.read_all().decode('utf-8', errors='ignore').strip()
    print("        ESP32 தொடக்க செய்தி (Boot Output):")
    for line in boot_banner.split('\n'):
        if line.strip():
            print(f"        | {line.strip()}")
    
    s.reset_input_buffer()

    # Test 1: PING
    print("\n[படி 2] PING சிக்னல் அனுப்புகிறது...")
    t0 = time.time()
    s.write(b"PING\n")
    s.flush()
    resp_ping = s.readline().decode('utf-8', errors='ignore').strip()
    dt_ping = (time.time() - t0) * 1000.0
    print(f"        TX -> PING")
    print(f"        RX <- {resp_ping} ({dt_ping:.1f} ms-ல்)")

    # Test 2: Channel 1
    print("\n[படி 3] சேனல் 1-க்கு வால்வு சிக்னல் (EJ:1:20)...")
    t0 = time.time()
    s.write(b"EJ:1:20\n")
    s.flush()
    resp_ej1 = s.readline().decode('utf-8', errors='ignore').strip()
    dt_ej1 = (time.time() - t0) * 1000.0
    print(f"        TX -> EJ:1:20")
    print(f"        RX <- {resp_ej1} ({dt_ej1:.1f} ms-ல்)")

    # Test 3: Channel 63
    print("\n[படி 4] சேனல் 63-க்கு வால்வு சிக்னல் (EJ:63:20)...")
    t0 = time.time()
    s.write(b"EJ:63:20\n")
    s.flush()
    resp_ej63 = s.readline().decode('utf-8', errors='ignore').strip()
    dt_ej63 = (time.time() - t0) * 1000.0
    print(f"        TX -> EJ:63:20")
    print(f"        RX <- {resp_ej63} ({dt_ej63:.1f} ms-ல்)")

    # Test 4: CLEAR
    print("\n[படி 5] அனைத்து வால்வுகளையும் அணைக்க (CLEAR)...")
    t0 = time.time()
    s.write(b"CLEAR\n")
    s.flush()
    resp_clr = s.readline().decode('utf-8', errors='ignore').strip()
    dt_clr = (time.time() - t0) * 1000.0
    print(f"        TX -> CLEAR")
    print(f"        RX <- {resp_clr} ({dt_clr:.1f} ms-ல்)")

    s.close()

    print("\n" + "="*65)
    if "PONG" in resp_ping and "ACK:EJ_FIRED:1" in resp_ej1 and "ACK:EJ_FIRED:63" in resp_ej63:
        print("  ✅ வெற்றி! ESP32 COM3 போர்ட்டில் 100% முழுமையாக இயங்குகிறது!")
        print("     அனைத்து கமாண்டுகளுக்கும் உடனுக்குடன் மில்லிசெகண்டில் பதில் வருகிறது.")
    else:
        print("  ⚠️ பதில் சரிபார்க்கவும்.")
    print("="*65)

except Exception as e:
    print(f"\n[பிழை / ERROR]: {e}")
    print("குறிப்பு: வேறு ஏதேனும் மென்பொருள் COM3-ஐப் பயன்படுத்திக் கொண்டிருக்கிறதா என்று பார்க்கவும்.")
