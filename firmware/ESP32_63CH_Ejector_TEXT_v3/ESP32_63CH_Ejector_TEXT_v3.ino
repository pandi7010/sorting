#include <Arduino.h>

// ============================================================
// LAMBTON / TCM VISION - ESP32 63 CHANNEL EJECTOR - TEXT v3.0
// Based on the proven old TCM firmware. SAME pins, SAME commands.
// 8 x 74HC595, CH1..CH63 valid, CH64 permanently OFF.
// GPIO14 = DATA, GPIO13 = CLOCK, GPIO12 = LATCH, GPIO19 = OE.
// Serial = 115200.
//
// Changes vs old firmware (all small and safe):
//  1. FIRE_MULTI shifts the registers ONCE for all channels
//     (old code shifted once per channel).
//  2. HOLD_ON auto-turns OFF after 10 s (safety).
//  3. New: VERSION command.
//  4. New aliases for older UIs: EJ:<ch>:<ms>  and  SA:<b0>:..:<b7>[:<ms>]
//  Everything else is exactly like the old firmware.
//  Upload tip: unplug the 74HC595 ribbon while uploading (GPIO12).
// ============================================================

constexpr uint8_t NUM_REGS = 8;
constexpr uint8_t NUM_CH = 63;
constexpr int DATA_PIN = 14;
constexpr int CLOCK_PIN = 13;
constexpr int LATCH_PIN = 12;
constexpr int OE_PIN = 19;       // 74HC595 OE is active LOW
constexpr int STATUS_LED = 2;

// Change ONLY this if the pneumatic driver input is active-low.
constexpr bool OUTPUT_ACTIVE_HIGH = true;

constexpr uint32_t DEFAULT_PULSE_MS = 5;
constexpr uint32_t MAX_PULSE_MS = 5000;
constexpr uint32_t MAX_GAP_MS = 60000;
constexpr uint32_t HOLD_MAX_MS = 10000;   // HOLD auto-off after 10 s

const char* FW_NAME = "TCM_EJECTOR_TEXT_V3.0";

uint8_t shiftBytes[NUM_REGS] = {0};
uint64_t pulseMask = 0;          // timed outputs
uint64_t holdMask = 0;           // stable-wire HOLD outputs
uint32_t pulseOffUs[NUM_CH] = {0};
uint32_t holdStartMs[NUM_CH] = {0};

char rx[256];
size_t rxLen = 0;

enum RunMode : uint8_t { IDLE, CONTINUOUS, REPEAT, SEQUENCE };
enum Phase : uint8_t { PH_PULSE, PH_GAP };
RunMode mode = IDLE;
Phase phase = PH_PULSE;

uint8_t runCh = 1;
uint8_t endCh = 63;
uint32_t pulseMs = DEFAULT_PULSE_MS;
uint32_t gapMs = 5;
uint32_t repeatTotal = 0;
uint32_t repeatDone = 0;
uint32_t phaseDeadlineUs = 0;

static inline uint64_t bitOf(uint8_t ch) { return 1ULL << (ch - 1); }
static bool validCh(int ch) { return ch >= 1 && ch <= NUM_CH; }
static uint32_t clampPulse(uint32_t v) { return v == 0 ? DEFAULT_PULSE_MS : (v > MAX_PULSE_MS ? MAX_PULSE_MS : v); }
static uint32_t clampGap(uint32_t v) { return v > MAX_GAP_MS ? MAX_GAP_MS : v; }
static bool reached(uint32_t now, uint32_t deadline) { return (int32_t)(now - deadline) >= 0; }

void shiftWrite() {
  // Same sequence as the old proven firmware.
  digitalWrite(OE_PIN, HIGH);
  digitalWrite(LATCH_PIN, LOW);

  // Byte 0 = CH1..CH8. Send byte 7 first so byte 0 lands in the first 595.
  for (int i = NUM_REGS - 1; i >= 0; --i) {
    uint8_t v = shiftBytes[i];
    for (int b = 7; b >= 0; --b) {
      digitalWrite(DATA_PIN, (v & (1U << b)) ? HIGH : LOW);
      digitalWrite(CLOCK_PIN, HIGH);
      digitalWrite(CLOCK_PIN, LOW);
    }
  }

  digitalWrite(LATCH_PIN, HIGH);
  digitalWrite(LATCH_PIN, LOW);
  digitalWrite(OE_PIN, LOW);
}

void rebuild() {
  uint64_t m = pulseMask | holdMask;
  for (uint8_t i = 0; i < NUM_REGS; ++i) {
    uint8_t b = (uint8_t)((m >> (8 * i)) & 0xFFULL);
    if (i == 7) b &= 0x7F; // CH64 always OFF
    shiftBytes[i] = OUTPUT_ACTIVE_HIGH ? b : (uint8_t)~b;
  }
  if (!OUTPUT_ACTIVE_HIGH) shiftBytes[7] |= 0x80;
}

void apply() {
  rebuild();
  shiftWrite();
  digitalWrite(STATUS_LED, (pulseMask | holdMask) ? HIGH : LOW);
}

void allOff() {
  mode = IDLE;
  pulseMask = 0;
  holdMask = 0;
  memset(pulseOffUs, 0, sizeof(pulseOffUs));
  memset(holdStartMs, 0, sizeof(holdStartMs));
  for (uint8_t i = 0; i < NUM_REGS; ++i) shiftBytes[i] = OUTPUT_ACTIVE_HIGH ? 0x00 : 0xFF;
  if (!OUTPUT_ACTIVE_HIGH) shiftBytes[7] |= 0x80;
  shiftWrite();
  digitalWrite(STATUS_LED, LOW);
}

// Set a pulse without shifting (caller must call apply()).
void armPulse(uint8_t ch, uint32_t ms) {
  if (!validCh(ch)) return;
  ms = clampPulse(ms);
  pulseMask |= bitOf(ch);
  pulseOffUs[ch - 1] = micros() + ms * 1000UL;
}

void startPulse(uint8_t ch, uint32_t ms) {
  if (!validCh(ch)) return;
  armPulse(ch, ms);
  apply();
}

void updatePulses() {
  const uint32_t now = micros();
  const uint32_t nowMs = millis();
  bool changed = false;
  for (uint8_t ch = 1; ch <= NUM_CH; ++ch) {
    const uint64_t bit = bitOf(ch);
    if ((pulseMask & bit) && reached(now, pulseOffUs[ch - 1])) {
      pulseMask &= ~bit;
      pulseOffUs[ch - 1] = 0;
      changed = true;
    }
    if ((holdMask & bit) && (nowMs - holdStartMs[ch - 1] >= HOLD_MAX_MS)) {
      holdMask &= ~bit;
      changed = true;
      Serial.print("EVENT:HOLD_TIMEOUT:"); Serial.println(ch);
    }
  }
  if (changed) apply();
}

void stopModeOnly() {
  mode = IDLE;
  phase = PH_PULSE;
  repeatTotal = 0;
  repeatDone = 0;
}

void singleFire(uint8_t ch, uint32_t ms) {
  if (!validCh(ch)) { Serial.println("ERR:INVALID_CHANNEL"); return; }
  stopModeOnly();
  holdMask = 0;
  startPulse(ch, ms);
  Serial.print("OK:FIRE:"); Serial.println(ch);
}

void multiFire(const char* csv, uint32_t ms) {
  ms = clampPulse(ms);
  bool any = false;
  char tmp[200];
  strncpy(tmp, csv, sizeof(tmp) - 1); tmp[sizeof(tmp) - 1] = 0;
  char* p = strtok(tmp, ",");
  while (p) {
    int ch = atoi(p);
    if (validCh(ch)) { armPulse((uint8_t)ch, ms); any = true; }
    p = strtok(nullptr, ",");
  }
  if (any) apply();   // one shift for all channels
  Serial.println(any ? "OK:FIRE_MULTI" : "ERR:NO_VALID_CHANNEL");
}

void maskFire(uint64_t mask, uint32_t ms) {
  ms = clampPulse(ms);
  bool any = false;
  for (uint8_t ch = 1; ch <= NUM_CH; ++ch) {
    if (mask & bitOf(ch)) { armPulse(ch, ms); any = true; }
  }
  if (any) apply();
}

void startContinuous(uint8_t ch, uint32_t p, uint32_t g) {
  if (!validCh(ch)) { Serial.println("ERR:INVALID_CHANNEL"); return; }
  p = clampPulse(p); g = clampGap(g);
  pulseMask = 0; holdMask = 0;
  mode = CONTINUOUS; phase = PH_PULSE;
  runCh = ch; pulseMs = p; gapMs = g;
  startPulse(runCh, pulseMs);
  phaseDeadlineUs = micros() + pulseMs * 1000UL;
  Serial.print("OK:CONT:"); Serial.println(ch);
}

void startRepeat(uint8_t ch, uint32_t p, uint32_t g, uint32_t count) {
  if (!validCh(ch)) { Serial.println("ERR:INVALID_CHANNEL"); return; }
  if (count == 0) { Serial.println("ERR:COUNT_ZERO"); return; }
  p = clampPulse(p); g = clampGap(g);
  pulseMask = 0; holdMask = 0;
  mode = REPEAT; phase = PH_PULSE;
  runCh = ch; pulseMs = p; gapMs = g;
  repeatTotal = count; repeatDone = 1;
  startPulse(runCh, pulseMs);
  phaseDeadlineUs = micros() + pulseMs * 1000UL;
  Serial.print("OK:REPEAT:"); Serial.println(ch);
}

void startSequence(uint8_t startCh, uint8_t stopCh, uint32_t p, uint32_t g) {
  if (!validCh(startCh) || !validCh(stopCh) || startCh > stopCh) {
    Serial.println("ERR:INVALID_SEQUENCE"); return;
  }
  p = clampPulse(p); g = clampGap(g);
  pulseMask = 0; holdMask = 0;
  mode = SEQUENCE; phase = PH_PULSE;
  runCh = startCh; endCh = stopCh; pulseMs = p; gapMs = g;
  startPulse(runCh, pulseMs);
  phaseDeadlineUs = micros() + pulseMs * 1000UL;
  Serial.print("OK:TEST:"); Serial.print(startCh); Serial.print("-"); Serial.println(stopCh);
}

void updateMode() {
  if (mode == IDLE) return;
  const uint32_t now = micros();
  if (!reached(now, phaseDeadlineUs)) return;

  if (phase == PH_PULSE) {
    pulseMask &= ~bitOf(runCh);
    pulseOffUs[runCh - 1] = 0;
    apply();

    if (mode == REPEAT && repeatDone >= repeatTotal) {
      mode = IDLE; Serial.println("DONE:REPEAT"); return;
    }
    if (mode == SEQUENCE && runCh >= endCh) {
      mode = IDLE; Serial.println("DONE:TEST"); return;
    }

    phase = PH_GAP;
    phaseDeadlineUs = now + gapMs * 1000UL;
    return;
  }

  // End of GAP -> start next pulse.
  if (mode == CONTINUOUS) {
    phase = PH_PULSE;
    startPulse(runCh, pulseMs);
    phaseDeadlineUs = micros() + pulseMs * 1000UL;
  } else if (mode == REPEAT) {
    ++repeatDone;
    phase = PH_PULSE;
    startPulse(runCh, pulseMs);
    phaseDeadlineUs = micros() + pulseMs * 1000UL;
  } else if (mode == SEQUENCE) {
    ++runCh;
    phase = PH_PULSE;
    startPulse(runCh, pulseMs);
    phaseDeadlineUs = micros() + pulseMs * 1000UL;
  }
}

void holdOn(uint8_t ch) {
  if (!validCh(ch)) { Serial.println("ERR:INVALID_CHANNEL"); return; }
  stopModeOnly();
  pulseMask &= ~bitOf(ch);
  pulseOffUs[ch - 1] = 0;
  holdMask |= bitOf(ch);
  holdStartMs[ch - 1] = millis();
  apply();
  Serial.print("OK:HOLD_ON:"); Serial.println(ch);
}

void holdOff(uint8_t ch) {
  if (!validCh(ch)) { Serial.println("ERR:INVALID_CHANNEL"); return; }
  holdMask &= ~bitOf(ch);
  apply();
  Serial.print("OK:HOLD_OFF:"); Serial.println(ch);
}

void command(char* s) {
  while (*s == ' ' || *s == '\t') ++s;
  if (!*s) return;

  if (!strcmp(s, "PING")) { Serial.println("PONG"); return; }
  if (!strcmp(s, "STATUS")) { Serial.println("EJECTOR_READY"); return; }
  if (!strcmp(s, "VERSION")) { Serial.println(FW_NAME); return; }
  if (!strcmp(s, "STOP")) { allOff(); Serial.println("STOPPED"); return; }
  if (!strcmp(s, "ALL_OFF")) { allOff(); Serial.println("ALL_OFF_OK"); return; }

  if (!strncmp(s, "FIRE_MULTI:", 11)) {
    char w[200]; strncpy(w, s + 11, sizeof(w)-1); w[sizeof(w)-1] = 0;
    char* colon = strchr(w, ':');
    if (!colon) { Serial.println("ERR:FIRE_MULTI_FORMAT"); return; }
    *colon = 0; uint32_t p = strtoul(colon + 1, nullptr, 10);
    multiFire(w, p); return;
  }

  if (!strncmp(s, "FIRE:", 5)) {
    char w[192]; strncpy(w, s + 5, sizeof(w)-1); w[sizeof(w)-1] = 0;
    char* a = strtok(w, ":"); char* b = strtok(nullptr, ":");
    char* c = strtok(nullptr, ":"); char* d = strtok(nullptr, ":");
    if (!a || !b) { Serial.println("ERR:FIRE_FORMAT"); return; }
    int ch = atoi(a); uint32_t p = strtoul(b, nullptr, 10);
    if (!c) { singleFire((uint8_t)ch, p); return; }
    if (d) { uint32_t g = strtoul(c, nullptr, 10); uint32_t n = strtoul(d, nullptr, 10); startRepeat((uint8_t)ch, p, g, n); return; }
    Serial.println("ERR:FIRE_FORMAT"); return;
  }

  if (!strncmp(s, "EJ:", 3)) {                       // EJ:<ch>:<ms>  (older UI)
    char w[64]; strncpy(w, s + 3, sizeof(w)-1); w[sizeof(w)-1] = 0;
    char* a = strtok(w, ":"); char* b = strtok(nullptr, ":");
    if (!a) { Serial.println("ERR:EJ_FORMAT"); return; }
    int ch = atoi(a);
    uint32_t p = b ? strtoul(b, nullptr, 10) : DEFAULT_PULSE_MS;
    if (!validCh(ch)) { Serial.println("ERR:INVALID_CHANNEL"); return; }
    stopModeOnly();
    startPulse((uint8_t)ch, p);
    Serial.print("ACK:EJ_FIRED:"); Serial.println(ch);
    return;
  }

  if (!strncmp(s, "SA:", 3)) {                       // SA:b0:..:b7[:ms]  (older UI)
    uint64_t mask = 0;
    char* p = s + 3;
    int i = 0;
    for (; i < NUM_REGS && *p; ++i) {
      char* e;
      unsigned long b = strtoul(p, &e, 10);
      mask |= (uint64_t)(b & 0xFF) << (8 * i);
      p = (*e == ':') ? e + 1 : e;
    }
    uint32_t ms = (*p) ? strtoul(p, nullptr, 10) : DEFAULT_PULSE_MS;
    mask &= (1ULL << NUM_CH) - 1;
    if (mask) { stopModeOnly(); maskFire(mask, ms); }
    Serial.println("OK:SA");
    return;
  }

  if (!strncmp(s, "CONT:", 5)) {
    char w[128]; strncpy(w, s + 5, sizeof(w)-1); w[sizeof(w)-1] = 0;
    char* a = strtok(w, ":"); char* b = strtok(nullptr, ":"); char* c = strtok(nullptr, ":");
    if (!a || !b || !c) { Serial.println("ERR:CONT_FORMAT"); return; }
    startContinuous((uint8_t)atoi(a), strtoul(b, nullptr, 10), strtoul(c, nullptr, 10)); return;
  }

  if (!strncmp(s, "TEST:", 5)) {
    char w[128]; strncpy(w, s + 5, sizeof(w)-1); w[sizeof(w)-1] = 0;
    char* a = strtok(w, ":"); char* b = strtok(nullptr, ":"); char* c = strtok(nullptr, ":"); char* d = strtok(nullptr, ":");
    if (!a || !b || !c || !d) { Serial.println("ERR:TEST_FORMAT"); return; }
    startSequence((uint8_t)atoi(a), (uint8_t)atoi(b), strtoul(c, nullptr, 10), strtoul(d, nullptr, 10)); return;
  }

  if (!strncmp(s, "HOLD_ON:", 8)) { holdOn((uint8_t)atoi(s + 8)); return; }
  if (!strncmp(s, "HOLD_OFF:", 9)) { holdOff((uint8_t)atoi(s + 9)); return; }

  Serial.print("ERR:UNKNOWN:"); Serial.println(s);
}

void readSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (rxLen) {
        rx[rxLen] = 0;
        command(rx);
        rxLen = 0;
      }
    } else if (rxLen < sizeof(rx) - 1) {
      rx[rxLen++] = c;
    } else {
      rxLen = 0;
      Serial.println("ERR:RX_OVERFLOW");
    }
  }
}

void setup() {
  pinMode(DATA_PIN, OUTPUT);
  pinMode(CLOCK_PIN, OUTPUT);
  pinMode(LATCH_PIN, OUTPUT);
  pinMode(OE_PIN, OUTPUT);
  pinMode(STATUS_LED, OUTPUT);

  digitalWrite(OE_PIN, HIGH);   // outputs disabled during boot
  digitalWrite(DATA_PIN, LOW);
  digitalWrite(CLOCK_PIN, LOW);
  digitalWrite(LATCH_PIN, LOW);
  digitalWrite(STATUS_LED, LOW);

  Serial.begin(115200);
  delay(100);
  allOff();

  Serial.println("TCM_EJECTOR_READY");
  Serial.println(FW_NAME);
  Serial.println("CH1-CH63 VALID");
  Serial.println("CH64 INVALID");
  Serial.println("BAUD=115200");
}

void loop() {
  updatePulses();
  readSerial();
  updateMode();
}
