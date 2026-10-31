import serial
import time
import sys

# Change this to the COM port of your USB to UART adapter (e.g., 'COM3', 'COM4')
# நீங்கள் Device Manager-ல் பார்த்த COM Port எண்ணை இங்கே மாற்றவும்
COM_PORT = 'COM3' 
BAUD_RATE = 115200 # Nuvoton-க்கு ஏற்ற Baud rate-ஐ கொடுக்கவும் (பொதுவாக 9600 அல்லது 115200)

try:
    print(f"[{COM_PORT}] Port-ஐ திறக்க முயற்சிக்கிறது...")
    ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
    time.sleep(1) # Connection 안정화를 위해 잠시 대기
    print(f"✅ {COM_PORT} வெற்றிகரமாக இணைக்கப்பட்டது!")
    
    # User கேட்டபடி ASCII 86 ('V') மற்றும் 100 ஆகியவற்றை அனுப்புகிறோம்
    # Sending raw bytes: 86 (0x56) and 100 (0x64)
    command_bytes = bytes([86, 100])
    
    print(f"🚀 Nuvoton-க்கு சிக்னல் அனுப்புகிறது: {list(command_bytes)}")
    ser.write(command_bytes)
    
    time.sleep(0.5)
    
    # Nuvoton போர்டில் இருந்து ஏதேனும் பதில் வந்தால் அதைப் படிக்கவும்
    if ser.in_waiting > 0:
        response = ser.read(ser.in_waiting)
        print(f"📩 Nuvoton-லிருந்து வந்த பதில்: {response}")
    else:
        print("நிறைவடைந்தது! Nuvoton-லிருந்து எந்த பதிலும் வரவில்லை (இது சாதாரணம்).")
        
    ser.close()
    
except serial.SerialException as e:
    print(f"❌ பிழை (Error): {COM_PORT} போர்ட்டை திறக்க முடியவில்லை.")
    print("தயவுசெய்து சரியான COM Port எண்ணை உள்ளிடவும் அல்லது USB வயரை சரிபார்க்கவும்.")
except Exception as e:
    print(f"❌ எதிர்பாராத பிழை: {e}")
