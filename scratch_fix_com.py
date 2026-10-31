import os
files = [r'd:\LAMTON_V1\LAMTON_LINE_NEW_ESP32.py', r'd:\LAMTON_V1\test_esp32_ejector.py', r'd:\LAMTON_V1\relay.py']
for f in files:
    with open(f, 'r', encoding='utf-8') as file:
        data = file.read()
    data = data.replace('"COM3"', '"COM23"').replace("'COM3'", "'COM23'")
    data = data.replace('"COM4"', '"COM24"').replace("'COM4'", "'COM24'")
    with open(f, 'w', encoding='utf-8') as file:
        file.write(data)
print('Done!')
