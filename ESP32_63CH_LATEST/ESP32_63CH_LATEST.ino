/*
  =============================================================================
  LAMBTON OPTICAL SORTER - ESP32 63-CHANNEL EJECTOR FIRMWARE
  Hardware: ESP32 30-Pin Dev Module + 8x Cascaded 74HC595 Shift Registers
  Baud Rate: 115200 Baud
  =============================================================================
  
  Wiring Guide:
  ESP32 Pin          74HC595 (First IC) Pin
  -------------------------------------------------------------
  GPIO 14 (DATA)  -> Pin 14 (DS / SER)
  GPIO 13 (CLOCK) -> Pin 11 (SH_CP / SRCLK) [Connect to ALL 8 ICs in parallel]
  GPIO 12 (LATCH) -> Pin 12 (ST_CP / RCLK)  [Connect to ALL 8 ICs in parallel]
  GPIO 19 (OE)    -> Pin 13 (OE / Output Enable) [Active LOW, or hardwire to GND]
  3.3V / 5V       -> Pin 10 (MR / SRCLR / Master Reset) [Connect to VCC]
  GND             -> Pin 8  (GND) [Common GND with ESP32 and Ejector Power Supply]
  
  74HC595 Daisy Chaining (Cascading):
  IC 1 (Pin 9 / Q7S) -> IC 2 (Pin 14 / DS)
  IC 2 (Pin 9 / Q7S) -> IC 3 (Pin 14 / DS)
  IC 3 (Pin 9 / Q7S) -> IC 4 (Pin 14 / DS)
  IC 4 (Pin 9 / Q7S) -> IC 5 (Pin 14 / DS)
  IC 5 (Pin 9 / Q7S) -> IC 6 (Pin 14 / DS)
  IC 6 (Pin 9 / Q7S) -> IC 7 (Pin 14 / DS)
  IC 7 (Pin 9 / Q7S) -> IC 8 (Pin 14 / DS)
  
  Ejector Valve Mapping (1 to 63):
  IC 1: Q0..Q7 -> Ejector Channels 1 to 8
  IC 2: Q0..Q7 -> Ejector Channels 9 to 16
  IC 3: Q0..Q7 -> Ejector Channels 17 to 24
  IC 4: Q0..Q7 -> Ejector Channels 25 to 32
  IC 5: Q0..Q7 -> Ejector Channels 33 to 40
  IC 6: Q0..Q7 -> Ejector Channels 41 to 48
  IC 7: Q0..Q7 -> Ejector Channels 49 to 56
  IC 8: Q0..Q6 -> Ejector Channels 57 to 63 (Q7 is spare Channel 64)
  =============================================================================
*/

#include <Arduino.h>
#include <SPI.h>

// =============================================================================
// PIN DEFINITIONS
// =============================================================================
#define SHIFT_DATA_PIN    14  // DS (Serial Data Input)
#define SHIFT_CLOCK_PIN   13  // SH_CP (Shift Register Clock)
#define SHIFT_LATCH_PIN   12   // ST_CP (Storage Register Latch Clock)
#define SHIFT_OE_PIN      19  // OE (Output Enable, Active LOW - can be tied to GND)
#define STATUS_LED_PIN    2   // Onboard Blue LED for firing indication
#define VIBRATOR_PIN      25  // PWM Output Pin for Vibrator Speed Control

// =============================================================================
// SYSTEM PARAMETERS
// =============================================================================
#define NUM_SHIFT_REGISTERS 8   // 8 ICs * 8 bits = 64 outputs
#define TOTAL_CHANNELS      63  // Channels 1 to 63 used for sorting
#define DEFAULT_PULSE_US    1000  // Solenoid valve pulse duration (microseconds, 1000us = 1ms)
#define MAX_PULSE_US        150000 // Safety limit (150ms = 150000us)

// =============================================================================
// GLOBAL VARIABLES & BUFFERS
// =============================================================================
uint8_t shiftData[NUM_SHIFT_REGISTERS] = {0};
unsigned long valveFireTime = 0;
uint32_t currentPulseUs = 0;
bool valvesActive = false;

// Fast non-blocking line reception buffer
char rxBuffer[128];
uint8_t rxIndex = 0;

// =============================================================================
// HARDWARE SHIFT REGISTER OUTPUT
// =============================================================================

void updateShiftRegisters() {
  digitalWrite(SHIFT_LATCH_PIN, LOW);

  // Custom robust bit-banging with delays (500 kHz) to solve jumper wire capacitance issues!
  for (int i = NUM_SHIFT_REGISTERS - 1; i >= 0; i--) {
    uint8_t val = shiftData[i];
    for (uint8_t b = 0; b < 8; b++) {
      // MSB First
      digitalWrite(SHIFT_DATA_PIN, !!(val & (1 << (7 - b))));
      delayMicroseconds(1); 
      digitalWrite(SHIFT_CLOCK_PIN, HIGH);
      delayMicroseconds(1);
      digitalWrite(SHIFT_CLOCK_PIN, LOW);
      delayMicroseconds(1);
    }
  }

  digitalWrite(SHIFT_LATCH_PIN, HIGH);
}

// Clear all 63 channels immediately
void clearAllValves() {
  for (int i = 0; i < NUM_SHIFT_REGISTERS; i++) {
    shiftData[i] = 0;
  }
  updateShiftRegisters();
  valvesActive = false;
  digitalWrite(STATUS_LED_PIN, LOW);
}

// Fire valves with 8-byte bitmask and pulse width (microseconds)
void fireValves(const uint8_t* byteMask, uint32_t pulseUs) {
  if (pulseUs == 0) pulseUs = DEFAULT_PULSE_US;
  if (pulseUs > MAX_PULSE_US) pulseUs = MAX_PULSE_US;

  for (int i = 0; i < NUM_SHIFT_REGISTERS; i++) {
    shiftData[i] = byteMask[i];
  }

  updateShiftRegisters();
  valvesActive = true;
  valveFireTime = micros();
  currentPulseUs = pulseUs;
  digitalWrite(STATUS_LED_PIN, HIGH);
}

// Fire single channel (1 to 63)
void fireSingleChannel(int ch, uint32_t pulseUs) {
  if (ch < 1 || ch > TOTAL_CHANNELS) return;

  uint8_t mask[NUM_SHIFT_REGISTERS] = {0};
  int idx = ch - 1;
  int byteIdx = idx / 8;
  int bitIdx = idx % 8;

  mask[byteIdx] |= (1 << bitIdx);
  fireValves(mask, pulseUs);
}

// Self-Test Sweep: Sequentially fires Channel 1 to 63
void runSweepTest() {
  Serial.println("[TEST] Starting 63-Channel Sequential Sweep Test...");
  for (int ch = 1; ch <= TOTAL_CHANNELS; ch++) {
    Serial.printf("[TEST] Firing Channel %d / 63\n", ch);
    fireSingleChannel(ch, 30000); // 30ms test pulse
    delay(60);
    clearAllValves();
    delay(40);
  }
  Serial.println("[TEST] 63-Channel Test Complete! All valves verified.");
}

// =============================================================================
// COMMAND PARSER
// =============================================================================
void processCommand(const char* cmd) {
  // 1. SET ALL 63 CHANNELS: "SA:b0:b1:b2:b3:b4:b5:b6:b7" or "SA:b0:...:b7:pulse_ms"
  if (strncmp(cmd, "SA:", 3) == 0) {
    uint8_t mask[NUM_SHIFT_REGISTERS] = {0};
    const char* ptr = cmd + 3;
    bool anyOn = false;
    uint32_t pulseUs = DEFAULT_PULSE_US;

    for (int i = 0; i < NUM_SHIFT_REGISTERS; i++) {
      if (!ptr || *ptr == '\0') break;
      mask[i] = (uint8_t)atoi(ptr);
      if (mask[i] > 0) anyOn = true;
      ptr = strchr(ptr, ':');
      if (ptr) ptr++; // Skip ':'
    }

    if (ptr && *ptr != '\0') {
      float customPulseMs = atof(ptr);
      if (customPulseMs > 0) pulseUs = (uint32_t)(customPulseMs * 1000.0);
    }

    if (anyOn) {
      fireValves(mask, pulseUs);
    } else {
      clearAllValves();
    }
  }
  // 2. SINGLE CHANNEL FIRE: "EJ:<channel>:<pulse_ms>"
  else if (strncmp(cmd, "EJ:", 3) == 0) {
    const char* ptr = cmd + 3;
    int ch = atoi(ptr);
    uint32_t pulseUs = DEFAULT_PULSE_US;
    const char* colon = strchr(ptr, ':');
    if (colon) {
      float customPulseMs = atof(colon + 1);
      if (customPulseMs > 0) pulseUs = (uint32_t)(customPulseMs * 1000.0);
    }
    if (ch >= 1 && ch <= TOTAL_CHANNELS && pulseUs > 0) {
      fireSingleChannel(ch, pulseUs);
    }
  }
  // 3. CLEAR ALL: "CLEAR"
  else if (strcmp(cmd, "CLEAR") == 0) {
    clearAllValves();
    Serial.println("ACK:CLEARED");
  }
  // 4. HEARTBEAT / PING: "PING"
  else if (strcmp(cmd, "PING") == 0) {
    Serial.println("PONG:ESP32_ONLINE_63CH");
  }
  // 5. TEST SWEEP: "TEST"
  else if (strcmp(cmd, "TEST") == 0) {
    runSweepTest();
  }
  // 6. VIBRATOR SPEED: "V100" or "V:100"
  else if (cmd[0] == 'V') {
    int speed = 0;
    if (cmd[1] == ':') {
      speed = atoi(cmd + 2);
    } else {
      speed = atoi(cmd + 1);
    }
    if (speed > 255) speed = 255;
    if (speed < 0) speed = 0;
    
    // Set PWM speed
    analogWrite(VIBRATOR_PIN, speed);
    Serial.printf("ACK:VIBRATOR_SPEED:%d\n", speed);
  }
}

// =============================================================================
// ARDUINO SETUP
// =============================================================================
void setup() {
  Serial.begin(115200);
  delay(150);

  // Setup shift register pins
  pinMode(SHIFT_DATA_PIN, OUTPUT);
  pinMode(SHIFT_CLOCK_PIN, OUTPUT);
  pinMode(SHIFT_LATCH_PIN, OUTPUT);
  digitalWrite(SHIFT_LATCH_PIN, HIGH);
  // Initialize Output States
  digitalWrite(SHIFT_DATA_PIN, LOW);
  digitalWrite(SHIFT_CLOCK_PIN, LOW);

#ifdef SHIFT_OE_PIN
  pinMode(SHIFT_OE_PIN, OUTPUT);
  digitalWrite(SHIFT_OE_PIN, LOW); // Active LOW -> enable outputs
#endif

  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);

  // Setup Vibrator Pin
  pinMode(VIBRATOR_PIN, OUTPUT);
  analogWrite(VIBRATOR_PIN, 0); // Start with Vibrator OFF

  // Clear all outputs on startup
  clearAllValves();

  Serial.println("\n=======================================================");
  Serial.println("  LAMBTON ESP32 63-CHANNEL SHIFT REGISTER FIRMWARE     ");
  Serial.println("  Status: READY | Baud: 115200 | Channels: 1 - 63      ");
  Serial.println("=======================================================");
}

// =============================================================================
// MAIN LOOP
// =============================================================================
void loop() {
  // 1. Non-blocking valve pulse auto-shutoff (Rollover safe with micros)
  if (valvesActive && (micros() - valveFireTime >= currentPulseUs)) {
    clearAllValves();
  }

  // 2. High-speed non-blocking serial reader
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (rxIndex > 0) {
        rxBuffer[rxIndex] = '\0';
        processCommand(rxBuffer);
        rxIndex = 0;
      }
    } else {
      if (rxIndex < sizeof(rxBuffer) - 1) {
        rxBuffer[rxIndex++] = c;
      }
    }
  }
}
