/*
  ESP32 - 63-Channel Ejector & Indicator Tester
  =============================================
  Controls 63 physical ejector indicator lights through 8 cascaded
  74HC595 shift registers.

  Hardware Wiring (ESP32 -> 74HC595):
    - 74HC595 Pin 14 (Data / SER)    -> ESP32 GPIO 13
    - 74HC595 Pin 12 (Latch / RCLK)   -> ESP32 GPIO 12
    - 74HC595 Pin 11 (Clock / SRCLK)  -> ESP32 GPIO 14
    - 74HC595 Pin 16 (VCC)           -> ESP32 5V (or VIN)
    - 74HC595 Pin 10 (/MR / SRCLR)   -> ESP32 5V (HIGH = not reset, Crucial!)
    - 74HC595 Pin 8  (GND)           -> ESP32 GND
    - 74HC595 Pin 13 (/OE)           -> ESP32 GND (LOW = outputs active, Crucial!)

  Daisy-chaining (all 8 ICs):
    - IC1 Pin 9 (Q7') -> IC2 Pin 14 (SER), IC2 Pin 9 -> IC3 Pin 14, up to IC8.
    - Pins 11, 12, 10, 13, 16, 8 connected in parallel across all ICs.
    - 100 nF decoupling capacitor across Pin 16 & Pin 8 at each IC.

  Serial Protocol (115200 baud, 8N1):
    'P'        -> Status Ping (responds with ACK: ESP32_ALIVE)
    'A'        -> ALL 63 Channels ON (Continuous hold)
    0 / 'R'/'S'-> ALL OFF
    'W'        -> Enable Safety Watchdog (250ms auto-off)
    'X'        -> Disable Watchdog (Continuous hold)
    'T'        -> Diagnostic test: Turn ON IC1 (CH 01-08)
    1..63      -> Turn Channel n ON
    101..163   -> Turn Channel n OFF (channel + 100)
*/

#include <Arduino.h>
#include "driver/gpio.h"

// User-Specified ESP32 Pin Configuration
constexpr uint8_t DATA_PIN = 13;   // ESP32 GPIO 13 -> 74HC595 Pin 14 (SER)
constexpr uint8_t LATCH_PIN = 12;  // ESP32 GPIO 12 -> 74HC595 Pin 12 (RCLK)
constexpr uint8_t CLOCK_PIN = 14;  // ESP32 GPIO 14 -> 74HC595 Pin 11 (SRCLK)
constexpr uint8_t STATUS_LED = 2;  // ESP32 DevKit built-in status LED (GPIO 2)

constexpr uint8_t NUM_ICS = 8;
constexpr uint8_t NUM_CHANNELS = 63; // Channels 1..63 used; channel 64 reserved

uint8_t outputBytes[NUM_ICS] = {0};
bool active[NUM_CHANNELS + 1] = {false};
uint32_t onSince[NUM_CHANNELS + 1] = {0};
bool watchdogEnabled = false;        // Default to continuous hold for testing
constexpr uint16_t WATCHDOG_MS = 250;

// Shift out bits with paced microsecond setup and pulse shaping.
// Immediately returns data to LOW while clock is held HIGH so high-speed
// clock edge reflections/ringing can NEVER clock in extra adjacent '1' bits.
// High-speed precision microsecond bit-banging (~3us per bit -> <0.2ms for all 64 channels)
void customShiftOut(uint8_t dataPin, uint8_t clockPin, uint8_t bitOrder, uint8_t val) {
  for (uint8_t i = 0; i < 8; ++i) {
    bool bitVal = (bitOrder == LSBFIRST) ? (val & (1 << i)) : (val & (1 << (7 - i)));
    
    // 1. Assert data line and minimal setup time
    digitalWrite(dataPin, bitVal ? HIGH : LOW);
    delayMicroseconds(1);
    
    // 2. Rising clock edge latches data bit
    digitalWrite(clockPin, HIGH);
    delayMicroseconds(1);
    
    // 3. Clear data line back to LOW while clock is HIGH (reflection/ringing immunity)
    digitalWrite(dataPin, LOW);
    
    // 4. Clock falling edge
    digitalWrite(clockPin, LOW);
    delayMicroseconds(1);
  }
}

void writeRegisters() {
  digitalWrite(LATCH_PIN, LOW);
  digitalWrite(CLOCK_PIN, LOW);
  delayMicroseconds(1);
  // Shift from IC8 down to IC1 so the first byte shifted ends up in IC8
  for (int8_t i = NUM_ICS - 1; i >= 0; --i) {
    customShiftOut(DATA_PIN, CLOCK_PIN, MSBFIRST, outputBytes[i]);
  }
  digitalWrite(DATA_PIN, LOW);
  digitalWrite(CLOCK_PIN, LOW);
  delayMicroseconds(1);
  digitalWrite(LATCH_PIN, HIGH);
  delayMicroseconds(2);
  digitalWrite(LATCH_PIN, LOW);
}

void allOff() {
  for (uint8_t i = 0; i < NUM_ICS; ++i) {
    outputBytes[i] = 0;
  }
  for (uint8_t ch = 1; ch <= NUM_CHANNELS; ++ch) {
    active[ch] = false;
    onSince[ch] = 0;
  }
  writeRegisters();
  digitalWrite(STATUS_LED, LOW);
}

void allOn() {
  // Turn ON channels 1..56 (IC1 through IC7, all 8 bits = 0xFF)
  for (uint8_t i = 0; i < NUM_ICS - 1; ++i) {
    outputBytes[i] = 0xFF;
  }
  // IC8: Channels 57..63 ON (bits 0..6 = 1), Channel 64 (bit 7) reserved = 0
  outputBytes[7] = 0x7F;

  for (uint8_t ch = 1; ch <= NUM_CHANNELS; ++ch) {
    active[ch] = true;
    onSince[ch] = millis();
  }
  writeRegisters();
  digitalWrite(STATUS_LED, HIGH);
}

void setChannel(uint8_t ch, bool on) {
  if (ch < 1 || ch > NUM_CHANNELS) return;
  const uint8_t idx = ch - 1;
  const uint8_t ic = idx / 8;
  const uint8_t bit = idx % 8;
  if (on) {
    outputBytes[ic] |= uint8_t(1U << bit);
    active[ch] = true;
    onSince[ch] = millis();
  } else {
    outputBytes[ic] &= uint8_t(~(1U << bit));
    active[ch] = false;
    onSince[ch] = 0;
  }
}

void setup() {
  digitalWrite(DATA_PIN, LOW);
  digitalWrite(CLOCK_PIN, LOW);
  digitalWrite(LATCH_PIN, LOW);
  digitalWrite(STATUS_LED, LOW);

  pinMode(DATA_PIN, OUTPUT);
  pinMode(CLOCK_PIN, OUTPUT);
  pinMode(LATCH_PIN, OUTPUT);
  pinMode(STATUS_LED, OUTPUT);

  // Set lowest drive strength (~5mA) to soften fast rising/falling edges.
  // This drastically reduces high-frequency reflections and ringing across 8 parallel IC clock inputs.
  gpio_set_drive_capability((gpio_num_t)DATA_PIN, GPIO_DRIVE_CAP_0);
  gpio_set_drive_capability((gpio_num_t)CLOCK_PIN, GPIO_DRIVE_CAP_0);
  gpio_set_drive_capability((gpio_num_t)LATCH_PIN, GPIO_DRIVE_CAP_0);

  Serial.begin(115200);
  allOff();

  Serial.println("ESP32_63CH_EJECTOR_TESTER_READY");
  Serial.println("Pins: Data=GPIO13, Latch=GPIO12, Clock=GPIO14 | 8x 74HC595");
}

void loop() {
  bool changed = false;

  while (Serial.available() > 0) {
    const uint8_t b = uint8_t(Serial.read());

    // 1. Status Ping (Uppercase 'P' = 80; avoids collisions)
    if (b == 'P') {
      Serial.println("ACK: ESP32_ALIVE");
      digitalWrite(STATUS_LED, HIGH);
      delay(30);
      digitalWrite(STATUS_LED, LOW);
    }
    // 2. ALL 63 ON Continuous (Uppercase 'A' = 65)
    else if (b == 'A') {
      watchdogEnabled = false; // Hold continuous signal
      allOn();
      changed = false;
      Serial.println("ACK: ALL 63 ON (CONTINUOUS)");
    }
    // 3. ALL OFF (0 or uppercase 'R'/'S')
    else if (b == 0 || b == 'R' || b == 'S') {
      allOff();
      changed = false;
      Serial.println("ACK: ALL OFF");
    }
    // 4. Watchdog Controls ('W' = Enable, 'X' = Disable)
    else if (b == 'W') {
      watchdogEnabled = true;
      Serial.println("ACK: WATCHDOG ENABLED (250MS AUTO-OFF)");
    } else if (b == 'X') {
      watchdogEnabled = false;
      Serial.println("ACK: WATCHDOG DISABLED (CONTINUOUS HOLD)");
    }
    // 5. Diagnostic Test: IC 1 (Uppercase 'T' = 84)
    else if (b == 'T') {
      for (uint8_t ch = 1; ch <= 8; ++ch) {
        setChannel(ch, true);
      }
      changed = true;
      Serial.println("ACK: DIAGNOSTIC TEST - IC1 (CH 01-08) ON");
    }
    // 6. Individual Channel ON (1..63) - Zero-overhead instant execution
    else if (b >= 1 && b <= NUM_CHANNELS) {
      setChannel(b, true);
      changed = true;
      // Live silent mode: zero serial UART blocking delay!
    }
    // 7. Individual Channel OFF (101..163) - Zero-overhead instant execution
    else if (b >= 101 && b <= 100 + NUM_CHANNELS) {
      const uint8_t ch = b - 100;
      setChannel(ch, false);
      changed = true;
      // Live silent mode: zero serial UART blocking delay!
    }
    else {
      // Ignore newlines / carriage returns
      if (b != '\r' && b != '\n' && b != ' ') {
        Serial.print("WARN: UNKNOWN CMD ");
        Serial.println(b);
      }
    }
  }

  if (changed) {
    writeRegisters();
    bool anyActive = false;
    for (uint8_t ch = 1; ch <= NUM_CHANNELS; ++ch) {
      if (active[ch]) { anyActive = true; break; }
    }
    digitalWrite(STATUS_LED, anyActive ? HIGH : LOW);
  }

  // Safety watchdog if enabled
  if (watchdogEnabled) {
    const uint32_t now = millis();
    bool watchdogChanged = false;
    for (uint8_t ch = 1; ch <= NUM_CHANNELS; ++ch) {
      if (active[ch] && uint32_t(now - onSince[ch]) >= WATCHDOG_MS) {
        setChannel(ch, false);
        watchdogChanged = true;
        Serial.print("WARN: CH ");
        Serial.print(ch);
        Serial.println(" watchdog OFF");
      }
    }
    if (watchdogChanged) {
      writeRegisters();
      bool anyActive = false;
      for (uint8_t ch = 1; ch <= NUM_CHANNELS; ++ch) {
        if (active[ch]) { anyActive = true; break; }
      }
      digitalWrite(STATUS_LED, anyActive ? HIGH : LOW);
    }
  }
}
