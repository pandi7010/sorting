/*
  =============================================================================
  LAMBTON OPTICAL SORTER - ESP32 8-CHANNEL RELAY CONTROLLER
  FINAL VERSION - MODBUS RTU SLAVE
  
  Board: ESP32 Dev Module
  Role: Modbus RTU SLAVE (Listens for commands from PC)
  Baud Rate: 9600 Baud, 8N1
  Slave ID: 1 | Registers: 0 to 7
  =============================================================================

  Relay Assignments (NEW PIN MAPPING):
  -------------------------------------------------------------
  Relay 1 (Address 0) -> GPIO 16 -> Vibrator / Feeder Motor
  Relay 2 (Address 1) -> GPIO 17 -> Ejector Main Air Valve
  Relay 3 (Address 2) -> GPIO 18 -> Wiper Motor
  Relay 4 (Address 3) -> GPIO 19 -> White LED 1 (Front Top)
  Relay 5 (Address 4) -> GPIO 32 -> White LED 2 (Back Top)
  Relay 6 (Address 5) -> GPIO 33 -> White LED 3 (Front Bottom)
  Relay 7 (Address 6) -> GPIO 25 -> White LED 4 (Back Bottom)
  Relay 8 (Address 7) -> GPIO 26 -> Blue LED (Background)
  =============================================================================
*/

#include <Arduino.h>

#define SLAVE_ID 1
#define RELAY_ACTIVE_LOW false // Set to true if your specific relay board turns on with a LOW signal
#define NUM_RELAYS 8
#define STATUS_LED_PIN 2

const uint8_t RELAY_PINS[NUM_RELAYS] = {16, 17, 18, 19, 32, 33, 25, 26};

// Buffer for Modbus RTU frames
uint8_t rxBuffer[32];
uint8_t rxIndex = 0;
unsigned long lastRxTime = 0;

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

void setRelay(uint8_t index, bool turnOn) {
  if (index >= NUM_RELAYS) return;
  uint8_t pin = RELAY_PINS[index];
  
  if (RELAY_ACTIVE_LOW) {
    digitalWrite(pin, turnOn ? LOW : HIGH);
  } else {
    digitalWrite(pin, turnOn ? HIGH : LOW);
  }
}

void setup() {
  Serial.begin(9600);
  
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);

  // Initialize all Relay GPIO Pins as OUTPUT and turn them OFF
  for (uint8_t i = 0; i < NUM_RELAYS; i++) {
    pinMode(RELAY_PINS[i], OUTPUT);
    setRelay(i, false);
  }
}

void loop() {
  // Read incoming bytes
  while (Serial.available()) {
    uint8_t b = Serial.read();
    
    // Inter-frame gap timeout (Modbus standard is 3.5 char times, ~4ms at 9600 baud)
    // We use a generous 10ms timeout to reset the buffer to sync frames perfectly
    if (millis() - lastRxTime > 10) {
      rxIndex = 0;
    }
    
    lastRxTime = millis();
    
    if (rxIndex < sizeof(rxBuffer)) {
      rxBuffer[rxIndex++] = b;
    }
    
    // Function 06 (Write Single Register) has exactly 8 bytes
    if (rxIndex == 8) {
      if (rxBuffer[0] == SLAVE_ID && rxBuffer[1] == 0x06) {
        // Validate CRC
        uint16_t receivedCrc = (uint16_t)rxBuffer[6] | ((uint16_t)rxBuffer[7] << 8);
        uint16_t calculatedCrc = calculateCRC16(rxBuffer, 6);
        
        if (receivedCrc == calculatedCrc) {
          // Valid frame received! Toggle status LED
          digitalWrite(STATUS_LED_PIN, !digitalRead(STATUS_LED_PIN));
          
          uint16_t address = (uint16_t)rxBuffer[2] << 8 | rxBuffer[3];
          uint16_t value = (uint16_t)rxBuffer[4] << 8 | rxBuffer[5];
          
          // Address 0 to 7 correspond to Relay 1 to 8
          if (address < NUM_RELAYS) {
            bool turnOn = (value > 0);
            setRelay(address, turnOn);
          }
          
          // Send response (Echo back the exact same 8 bytes for Function 06)
          Serial.write(rxBuffer, 8);
          Serial.flush();
        }
      }
      // Reset buffer after processing a full 8-byte frame
      rxIndex = 0;
    }
  }
}
