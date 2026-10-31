/*
  =============================================================================
  LAMBTON OPTICAL SORTER - ESP32 8-CHANNEL RELAY CONTROLLER (MODBUS RTU)
  Board: ESP32 Dev Module (30-Pin / 38-Pin)
  Role: Modbus RTU MASTER (Polls PC Modbus Slave Server)
  Baud Rate: 9600 Baud, 8 Data bits, No Parity, 1 Stop bit (8N1)
  PC Slave ID: 1 | Registers: 40001 to 40008 (Holding Registers 0 to 7)
  =============================================================================

  Relay Assignments:
  -------------------------------------------------------------
  Relay 1 (HR 0 / Reg 40001) -> Vibrator / Feeder Motor
  Relay 2 (HR 1 / Reg 40002) -> Ejector Main Air Valve
  Relay 3 (HR 2 / Reg 40003) -> Wiper Motor
  Relay 4 (HR 3 / Reg 40004) -> Front Top LED Light
  Relay 5 (HR 4 / Reg 40005) -> Back Top LED Light
  Relay 6 (HR 5 / Reg 40006) -> Front Bottom LED Light
  Relay 7 (HR 6 / Reg 40007) -> Back Bottom LED Light
  Relay 8 (HR 7 / Reg 40008) -> Background LED Light

  Default ESP32 GPIO to Relay Pin Connections:
  -------------------------------------------------------------
  ESP32 GPIO 13 -> Relay IN 1 (Vibrator)
  ESP32 GPIO 12 -> Relay IN 2 (Ejector)
  ESP32 GPIO 14 -> Relay IN 3 (Wiper)
  ESP32 GPIO 27 -> Relay IN 4 (Front Top LED)
  ESP32 GPIO 26 -> Relay IN 5 (Back Top LED)
  ESP32 GPIO 25 -> Relay IN 6 (Front Bottom LED)
  ESP32 GPIO 33 -> Relay IN 7 (Back Bottom LED)
  ESP32 GPIO 32 -> Relay IN 8 (Background LED)
  ESP32 GPIO 2  -> Onboard Blue LED (Communication Activity Indicator)

  Communication Options:
  - Option A (Direct USB Serial): If connecting ESP32 via Micro-USB to PC (COM4)
  - Option B (RS-485 Module): If using MAX485 (DI->TX, RO->RX, DE/RE to GPIO 4)
  =============================================================================
*/

#include <Arduino.h>

// =============================================================================
// HARDWARE CONFIGURATION
// =============================================================================

// Set to TRUE if using Active LOW relay module (most blue/black relay boards are active LOW)
// Set to FALSE if your relay turns ON with HIGH (3.3V)
#define RELAY_ACTIVE_LOW true

// Communication Port:
// Use Serial (USB port) for direct USB connection to PC (COM4)
#define MODBUS_SERIAL Serial

// Set to TRUE if using external MAX485 module with DE/RE direction control pin
#define USE_RS485_DE_RE false
#define RS485_DE_RE_PIN 4

// Onboard Status LED
#define STATUS_LED_PIN 2

// Number of Relays
#define NUM_RELAYS 8

// Relay GPIO Pins (Standard ESP32 8-Channel Relay Board GPIOs)
const uint8_t RELAY_PINS[NUM_RELAYS] = {
  32,  // Relay 1: Vibrator
  33,  // Relay 2: Ejector
  25,  // Relay 3: Wiper
  26,  // Relay 4: Front Top LED
  27,  // Relay 5: Back Top LED
  14,  // Relay 6: Front Bottom LED
  12,  // Relay 7: Back Bottom LED
  13   // Relay 8: Background LED
};

// =============================================================================
// MODBUS RTU PARAMETERS
// =============================================================================
const uint8_t  SLAVE_ID     = 1;     // PC Modbus Slave ID
const uint8_t  FUNC_READ_HR = 0x03;  // Function 03: Read Holding Registers
const uint16_t START_REG    = 0x0000;// Address 0 (Reg 40001)
const uint16_t REG_COUNT    = 8;     // Read 8 registers (40001 to 40008)

const unsigned long POLL_INTERVAL_MS = 60;   // Poll PC every 60ms
const unsigned long COMM_TIMEOUT_MS  = 250;  // RX packet timeout
const unsigned long SAFETY_TIMEOUT_MS = 4000;// Turn off vibrator/wiper if PC disconnected for 4s

// Global State
uint16_t currentRegisters[NUM_RELAYS] = {0};
unsigned long lastPollTime = 0;
unsigned long lastSuccessTime = 0;
unsigned long rxStartTime = 0;
bool commActive = false;

// =============================================================================
// MODBUS CRC-16 CALCULATION (Standard Modbus polynomial 0xA001)
// =============================================================================
uint16_t calculateCRC16(const uint8_t *buffer, uint16_t length) {
  uint16_t crc = 0xFFFF;
  for (uint16_t i = 0; i < length; i++) {
    crc ^= (uint16_t)buffer[i];
    for (uint8_t bit = 0; bit < 8; bit++) {
      if (crc & 0x0001) {
        crc = (crc >> 1) ^ 0xA001;
      } else {
        crc >>= 1;
      }
    }
  }
  return crc;
}

// =============================================================================
// RELAY CONTROL HELPERS
// =============================================================================
void setRelayHardware(uint8_t index, bool stateOn) {
  if (index >= NUM_RELAYS) return;
  uint8_t pin = RELAY_PINS[index];
  
  if (RELAY_ACTIVE_LOW) {
    digitalWrite(pin, stateOn ? LOW : HIGH);
  } else {
    digitalWrite(pin, stateOn ? HIGH : LOW);
  }
}

void applyRegistersToRelays() {
  for (uint8_t i = 0; i < NUM_RELAYS; i++) {
    bool isOn = (currentRegisters[i] > 0);
    setRelayHardware(i, isOn);
  }
}

void allRelaysOff() {
  for (uint8_t i = 0; i < NUM_RELAYS; i++) {
    currentRegisters[i] = 0;
    setRelayHardware(i, false);
  }
}

// =============================================================================
// MODBUS RTU MASTER POLL
// =============================================================================
void sendModbusPoll() {
  uint8_t query[8];
  query[0] = SLAVE_ID;
  query[1] = FUNC_READ_HR;
  query[2] = (uint8_t)(START_REG >> 8);
  query[3] = (uint8_t)(START_REG & 0xFF);
  query[4] = (uint8_t)(REG_COUNT >> 8);
  query[5] = (uint8_t)(REG_COUNT & 0xFF);

  uint16_t crc = calculateCRC16(query, 6);
  query[6] = (uint8_t)(crc & 0xFF);         // CRC Low first
  query[7] = (uint8_t)((crc >> 8) & 0xFF);  // CRC High second

#if USE_RS485_DE_RE
  digitalWrite(RS485_DE_RE_PIN, HIGH); // Transmit Mode
  delayMicroseconds(50);
#endif

  // Clear any residual incoming junk before sending
  while (MODBUS_SERIAL.available()) {
    MODBUS_SERIAL.read();
  }

  MODBUS_SERIAL.write(query, sizeof(query));
  MODBUS_SERIAL.flush();

#if USE_RS485_DE_RE
  delayMicroseconds(50);
  digitalWrite(RS485_DE_RE_PIN, LOW); // Receive Mode
#endif
}

// Expected response length for 8 Holding Registers:
// 1(ID) + 1(Func) + 1(ByteCount=16) + 16(Data) + 2(CRC) = 21 bytes
const uint8_t EXPECTED_RESPONSE_LEN = 3 + (REG_COUNT * 2) + 2;

bool readModbusResponse() {
  uint8_t buffer[64];
  uint8_t bytesRead = 0;
  unsigned long start = millis();

  while ((millis() - start) < COMM_TIMEOUT_MS) {
    if (MODBUS_SERIAL.available()) {
      buffer[bytesRead++] = MODBUS_SERIAL.read();
      if (bytesRead >= EXPECTED_RESPONSE_LEN) {
        break;
      }
    }
  }

  if (bytesRead < EXPECTED_RESPONSE_LEN) {
    return false; // Incomplete response or timeout
  }

  // Validate Slave ID and Function
  if (buffer[0] != SLAVE_ID || buffer[1] != FUNC_READ_HR) {
    return false;
  }

  uint8_t dataBytes = buffer[2];
  if (dataBytes != (REG_COUNT * 2)) {
    return false;
  }

  // Validate CRC16
  uint16_t receivedCrc = (uint16_t)buffer[bytesRead - 2] | ((uint16_t)buffer[bytesRead - 1] << 8);
  uint16_t calculatedCrc = calculateCRC16(buffer, bytesRead - 2);

  if (receivedCrc != calculatedCrc) {
    return false; // CRC mismatch
  }

  // Extract Holding Registers (Big-Endian in Modbus)
  for (uint8_t i = 0; i < REG_COUNT; i++) {
    uint8_t hi = buffer[3 + (i * 2)];
    uint8_t lo = buffer[3 + (i * 2) + 1];
    currentRegisters[i] = ((uint16_t)hi << 8) | (uint16_t)lo;
  }

  return true;
}

// =============================================================================
// ARDUINO SETUP
// =============================================================================
void setup() {
  // Initialize Modbus communication at 9600 Baud (Standard 8N1)
  MODBUS_SERIAL.begin(9600);
  delay(100);

#if USE_RS485_DE_RE
  pinMode(RS485_DE_RE_PIN, OUTPUT);
  digitalWrite(RS485_DE_RE_PIN, LOW); // Default to RX mode
#endif

  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);

  // Initialize all Relay GPIO Pins as OUTPUT
  for (uint8_t i = 0; i < NUM_RELAYS; i++) {
    pinMode(RELAY_PINS[i], OUTPUT);
  }

  // Initial State: Turn OFF all relays
  allRelaysOff();

  lastPollTime = millis();
  lastSuccessTime = millis();
}

// =============================================================================
// MAIN LOOP
// =============================================================================
void loop() {
  unsigned long now = millis();

  // 1. Periodic Modbus RTU Poll
  if (now - lastPollTime >= POLL_INTERVAL_MS) {
    lastPollTime = now;

    sendModbusPoll();
    bool success = readModbusResponse();

    if (success) {
      lastSuccessTime = now;
      commActive = true;

      // Toggle status LED on valid packet
      digitalWrite(STATUS_LED_PIN, !digitalRead(STATUS_LED_PIN));

      // Apply new register states directly to physical relays
      applyRegistersToRelays();
    }
  }

  // 2. Safety Watchdog: If Modbus PC connection lost > 4 seconds,
  // turn OFF Vibrator (Relay 1) and Wiper (Relay 2) to prevent damage.
  if (commActive && (now - lastSuccessTime >= SAFETY_TIMEOUT_MS)) {
    commActive = false;
    digitalWrite(STATUS_LED_PIN, LOW);

    // Turn OFF moving machinery for safety
    setRelayHardware(0, false); // Vibrator OFF
    setRelayHardware(1, false); // Ejector OFF
    setRelayHardware(2, false); // Wiper OFF
  }
}
