/*
  =============================================================================
  LAMBTON OPTICAL SORTER - 8-CHANNEL RELAY CONTROLLER  v2.0  (Modbus RTU SLAVE)
  Board: ESP32 Dev Module          Serial: USB, 9600 baud 8N1, Slave ID 1
  Replaces (does NOT overwrite): ESP32_8CH_ModbusRelay_NEW.ino  (same pins)
  =============================================================================

  Relays (same as _NEW):
    Reg 0 -> GPIO 16 -> Relay 1  Vibrator / Feeder        (SAFETY: off on trip)
    Reg 1 -> GPIO 17 -> Relay 2  Ejector main air valve   (SAFETY: off on trip)
    Reg 2 -> GPIO 18 -> Relay 3  Wiper motor              (SAFETY: off on trip)
    Reg 3 -> GPIO 19 -> Relay 4  White LED front top
    Reg 4 -> GPIO 32 -> Relay 5  White LED back top
    Reg 5 -> GPIO 33 -> Relay 6  White LED front bottom
    Reg 6 -> GPIO 25 -> Relay 7  White LED back bottom
    Reg 7 -> GPIO 26 -> Relay 8  Blue background LED

  NEW IN v2.0 - SAFETY WATCHDOG
    Reg 8  (write)  HEARTBEAT. The sorter PC writes a counter here every 200 ms.
                    The first heartbeat ARMS the watchdog. If no valid Modbus
                    frame arrives for 500 ms while ARMED:
                    feeder, main air and wiper relays switch OFF -> state TRIPPED.
    Reg 9  (write)  1 = ARM / clear trip,  0 = DISARM (watchdog off, e.g. clean exit)
    While TRIPPED, turning relays 1-3 ON is REFUSED (Modbus exception 04)
    until the PC writes Reg 9 = 1. Lights can still be switched.

    Read registers (function 03):
      0-7 relay states | 8 last heartbeat | 9 state (0 disarmed, 1 armed, 2 tripped)
      10 trip count    | 11 firmware version (0x0200) | 12 ejector "PC OK" input

  Old tools (relay.py) never write Reg 8, so the watchdog stays off for them.

  OPTIONAL HARDWARE INTERLOCK (USE_EJECTOR_OK_INPUT 1):
    Ejector ESP32 GPIO 32 ("PC OK", HIGH when armed) -> 1 kOhm -> this board GPIO 34,
    plus 10 kOhm GPIO 34 -> GND, and common GND. If ARMED and this input stays LOW
    for 500 ms, the same safety trip happens.
  =============================================================================
*/

#include <Arduino.h>

#define SLAVE_ID           1
#define RELAY_ACTIVE_LOW   false     // same as _NEW
#define NUM_RELAYS         8
#define STATUS_LED_PIN     2
#define FW_VERSION         0x0200

#define WDT_MS             500
#define USE_EJECTOR_OK_INPUT 0       // 1 = also trip when ejector board "PC OK" line goes LOW
#define EJECTOR_OK_PIN     34

#define REG_HEARTBEAT      8
#define REG_CONTROL        9
#define NUM_READ_REGS      13

const uint8_t RELAY_PINS[NUM_RELAYS] = {16, 17, 18, 19, 32, 33, 25, 26};
const uint8_t SAFETY_RELAYS[] = {0, 1, 2};   // feeder, main air, wiper

enum { ST_DISARMED = 0, ST_ARMED = 1, ST_TRIPPED = 2 };

uint8_t  rxBuffer[32];
uint8_t  rxIndex = 0;
unsigned long lastRxTime = 0;

uint16_t relayState[NUM_RELAYS] = {0};
uint16_t lastHeartbeat = 0;
uint8_t  state = ST_DISARMED;
uint16_t tripCount = 0;
unsigned long lastValidFrameMs = 0;
unsigned long ejectorOkLowSince = 0;

uint16_t calculateCRC16(const uint8_t *buffer, uint16_t length) {
  uint16_t crc = 0xFFFF;
  for (uint16_t i = 0; i < length; i++) {
    crc ^= (uint16_t)buffer[i];
    for (uint8_t bit = 0; bit < 8; bit++) crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : (crc >> 1);
  }
  return crc;
}

void setRelay(uint8_t index, bool turnOn) {
  if (index >= NUM_RELAYS) return;
  relayState[index] = turnOn ? 1 : 0;
  digitalWrite(RELAY_PINS[index], (turnOn ^ RELAY_ACTIVE_LOW) ? HIGH : LOW);
}

bool isSafetyRelay(uint16_t addr) {
  for (uint8_t i = 0; i < sizeof(SAFETY_RELAYS); i++) if (SAFETY_RELAYS[i] == addr) return true;
  return false;
}

void trip() {
  for (uint8_t i = 0; i < sizeof(SAFETY_RELAYS); i++) setRelay(SAFETY_RELAYS[i], false);
  state = ST_TRIPPED;
  tripCount++;
}

void sendFrame(uint8_t* f, uint8_t lenWithoutCrc) {
  uint16_t crc = calculateCRC16(f, lenWithoutCrc);
  f[lenWithoutCrc] = crc & 0xFF;
  f[lenWithoutCrc + 1] = crc >> 8;
  Serial.write(f, lenWithoutCrc + 2);
  Serial.flush();
}

void sendException(uint8_t func, uint8_t code) {
  uint8_t f[5] = {SLAVE_ID, (uint8_t)(func | 0x80), code};
  sendFrame(f, 3);
}

uint16_t readRegister(uint16_t addr) {
  if (addr < NUM_RELAYS) return relayState[addr];
  switch (addr) {
    case 8:  return lastHeartbeat;
    case 9:  return state;
    case 10: return tripCount;
    case 11: return FW_VERSION;
    case 12: return digitalRead(EJECTOR_OK_PIN);
  }
  return 0;
}

void handleFrame() {
  if (rxBuffer[0] != SLAVE_ID) return;
  uint16_t rxCrc = (uint16_t)rxBuffer[6] | ((uint16_t)rxBuffer[7] << 8);
  if (rxCrc != calculateCRC16(rxBuffer, 6)) return;

  lastValidFrameMs = millis();
  digitalWrite(STATUS_LED_PIN, !digitalRead(STATUS_LED_PIN));

  uint8_t  func = rxBuffer[1];
  uint16_t addr = ((uint16_t)rxBuffer[2] << 8) | rxBuffer[3];
  uint16_t val  = ((uint16_t)rxBuffer[4] << 8) | rxBuffer[5];

  if (func == 0x06) {                                  // write single register
    if (addr < NUM_RELAYS) {
      if (state == ST_TRIPPED && isSafetyRelay(addr) && val > 0) { sendException(func, 0x04); return; }
      setRelay(addr, val > 0);
    } else if (addr == REG_HEARTBEAT) {
      lastHeartbeat = val;
      if (state == ST_DISARMED) state = ST_ARMED;       // first heartbeat arms the watchdog
    } else if (addr == REG_CONTROL) {
      state = (val == 1) ? ST_ARMED : ST_DISARMED;
    } else {
      sendException(func, 0x02);                        // illegal address
      return;
    }
    Serial.write(rxBuffer, 8);                          // normal reply = echo
    Serial.flush();
  } else if (func == 0x03) {                            // read holding registers
    if (val == 0 || addr + val > NUM_READ_REGS) { sendException(func, 0x02); return; }
    uint8_t f[3 + 2 * NUM_READ_REGS + 2];
    f[0] = SLAVE_ID; f[1] = 0x03; f[2] = val * 2;
    for (uint16_t i = 0; i < val; i++) {
      uint16_t r = readRegister(addr + i);
      f[3 + 2 * i] = r >> 8;
      f[4 + 2 * i] = r & 0xFF;
    }
    sendFrame(f, 3 + 2 * val);
  } else {
    sendException(func, 0x01);                          // illegal function
  }
}

void setup() {
  for (uint8_t i = 0; i < NUM_RELAYS; i++) { pinMode(RELAY_PINS[i], OUTPUT); setRelay(i, false); }
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);
  pinMode(EJECTOR_OK_PIN, INPUT);                       // GPIO 34 is input-only; needs external pull-down
  Serial.begin(9600);
}

void loop() {
  while (Serial.available()) {
    uint8_t b = Serial.read();
    if (millis() - lastRxTime > 10) rxIndex = 0;        // inter-frame gap -> new frame
    lastRxTime = millis();
    if (rxIndex < sizeof(rxBuffer)) rxBuffer[rxIndex++] = b;
    if (rxIndex == 8) { handleFrame(); rxIndex = 0; }   // all supported requests are 8 bytes
  }

  unsigned long now = millis();
  if (state == ST_ARMED && now - lastValidFrameMs > WDT_MS) trip();

#if USE_EJECTOR_OK_INPUT
  if (state == ST_ARMED) {
    if (digitalRead(EJECTOR_OK_PIN) == LOW) {
      if (ejectorOkLowSince == 0) ejectorOkLowSince = now;
      else if (now - ejectorOkLowSince > WDT_MS) trip();
    } else {
      ejectorOkLowSince = 0;
    }
  }
#endif

  if (state == ST_TRIPPED) digitalWrite(STATUS_LED_PIN, (now / 100) % 2);   // fast blink
}
