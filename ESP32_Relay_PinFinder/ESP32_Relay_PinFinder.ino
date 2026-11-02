/*
  =============================================================================
  ESP32 RELAY PIN FINDER V3 - ACTIVE HIGH RELAY BOARD
  
  கண்டுபிடிப்பு: உங்கள் board ACTIVE HIGH!
  HIGH = Relay ON, LOW = Relay OFF
  
  ஒவ்வொரு pin-ஐயும் HIGH பண்ணி 3 sec ON → OFF
  Serial Monitor-ல எந்த GPIO என்று பாருங்கள்
  =============================================================================
*/

#include <Arduino.h>

// 30-Pin ESP32 எல்லா output GPIO pins
const uint8_t TEST_PINS[] = {
  4, 5, 
  12, 13, 14, 15, 16, 17, 18, 19, 
  21, 22, 23, 
  25, 26, 27, 
  32, 33
};
const uint8_t NUM_PINS = sizeof(TEST_PINS) / sizeof(TEST_PINS[0]);

int currentPin = 0;

void setup() {
  Serial.begin(115200);
  delay(2000);

  // எல்லா pins LOW = எல்லா relays OFF (Active High board)
  for (uint8_t i = 0; i < NUM_PINS; i++) {
    pinMode(TEST_PINS[i], OUTPUT);
    digitalWrite(TEST_PINS[i], LOW);  // ALL RELAYS OFF
  }

  Serial.println();
  Serial.println("=============================================");
  Serial.println("  RELAY PIN FINDER V3 (ACTIVE HIGH BOARD)");
  Serial.println("  All relays OFF now (all pins LOW)");
  Serial.println("=============================================");
  Serial.println();
  Serial.println("Oru neram oru relay mattum ON aagum.");
  Serial.println("Enna GPIO number, enna Physical Relay");
  Serial.println("nu note pannunga!");
  Serial.println();
  delay(3000);
}

void loop() {
  // எல்லா pins OFF
  for (uint8_t i = 0; i < NUM_PINS; i++) {
    digitalWrite(TEST_PINS[i], LOW);
  }
  delay(500);

  // தற்போதைய pin-ஐ ON பண்ணு (HIGH = ON for active-high)
  uint8_t pin = TEST_PINS[currentPin];
  
  Serial.print(">>> GPIO ");
  if (pin < 10) Serial.print(" ");
  Serial.print(pin);
  Serial.print(" = ON   (");
  Serial.print(currentPin + 1);
  Serial.print("/");
  Serial.print(NUM_PINS);
  Serial.println(")  <-- Enna Relay ON aagutu?");
  
  digitalWrite(pin, HIGH);  // RELAY ON
  delay(3000);              // 3 sec ON

  digitalWrite(pin, LOW);   // RELAY OFF
  Serial.print("    GPIO ");
  if (pin < 10) Serial.print(" ");
  Serial.print(pin);
  Serial.println(" = OFF");
  Serial.println("---------------------------------------------");
  
  delay(1000); // 1 sec gap

  currentPin++;
  if (currentPin >= NUM_PINS) {
    Serial.println();
    Serial.println("=============================================");
    Serial.println("  18 pins test COMPLETE! Restarting...");
    Serial.println("=============================================");
    Serial.println();
    currentPin = 0;
    delay(5000);
  }
}
