/*
  =============================================================================
  LAMBTON OPTICAL SORTER - 63-CHANNEL EJECTOR FIRMWARE  v2.0
  Board : ESP32 Dev Module (30-pin)          Serial: USB, 921600 baud
  Replaces (does NOT overwrite): ESP32_63CH_ShiftRegister.ino
  =============================================================================

  WHAT IS NEW IN v2.0
  -------------------
  1. Binary protocol (fixed-size 64-bit channel mask + CRC-16 + sequence number
     + acknowledgement). Firing 1 or 63 channels is the same 24-byte message,
     so the old FIRE_MULTI 64-character truncation bug cannot happen.
  2. Old TEXT commands still work (PING, FIRE_MULTI, FIRE, EJ, SA, TEST, CONT,
     HOLD_ON/OFF, STOP, CLEAR, ALL_OFF, STATUS) - parsed safely with a 256-char
     buffer; over-long lines are REPORTED (ERR:LINE_TOO_LONG), never cut silently.
  3. 500 ms SAFETY WATCHDOG. Once the PC talks binary, it must send something at
     least every 500 ms. If it stops: all valves OFF, 74HC595 outputs disabled
     (OE HIGH), state = TRIPPED until the PC sends ARM.
  4. Channel REMAP table (logical channel 1..63 -> physical output 0..63),
     stored in flash. Lets you fix crossed wires in software.
  5. TEST mode on the ESP32: single channel (repeat N times) and sweep a..b, with
     adjustable pulse and gap; prints  CH -> OUT -> CHIP/Q  for every step.
  6. Pulse timing in microseconds (old firmware: 1 ms steps). Overlapping fire
     requests on the same valve are MERGED (window extended) instead of queued.
  7. Safe pins: GPIO 12 (boot strapping pin) is no longer used. Hardware SPI.
  8. Power-up safety: outputs stay disabled (OE HIGH) until the shift registers
     contain zeros. Needs the 10k pull-up described below.

  WIRING (PIN_PROFILE 2 = NEW, recommended)  -- see WIRING_PHASE1.md
  -------------------------------------------------------------------------
  ESP32 GPIO 23 (VSPI MOSI) -> 74HCT245 A1 -> B1 -> 74HC595 #1 pin 14 (DS / SER)
  ESP32 GPIO 18 (VSPI SCK)  -> 74HCT245 A2 -> B2 -> all 595 pin 11 (SH_CP / SRCLK)
  ESP32 GPIO 21 (LATCH)     -> 74HCT245 A3 -> B3 -> all 595 pin 12 (ST_CP / RCLK)
  ESP32 GPIO 22 (OE)        -> 74HCT245 A4 -> B4 -> all 595 pin 13 (/OE)
        + 10 kOhm resistor from GPIO 22 net to 3.3 V  (keeps outputs OFF at boot)
  74HCT245: VCC=5 V (pin 20), GND (pin 10), DIR pin 1 -> 5 V, /OE pin 19 -> GND
  ESP32 GPIO 2  -> on-board LED (status)
  ESP32 GPIO 27 -> TIMING DEBUG pin (HIGH while any valve is ON) - logic analyser
  ESP32 GPIO 32 -> "PC OK" output (HIGH when ARMED) - optional link to relay board
  Common GND: ESP32, 74HCT245, 74HC595, drive board logic, 24 V supply 0 V.

  PIN_PROFILE 1 = LEGACY wiring (GPIO 14 data, 13 clock, 12 latch, 19 OE),
  only for testing before rewiring. GPIO 12 boot risk remains in that profile.
  =============================================================================
*/

#include <Arduino.h>
#include <SPI.h>
#include <Preferences.h>
#include <esp_task_wdt.h>

// =============================================================================
// BUILD OPTIONS
// =============================================================================
#ifndef PIN_PROFILE
#define PIN_PROFILE        2         // 2 = new safe pins (recommended), 1 = legacy wiring
#endif
#define ACTIVE_LOW         false     // false: bit 1 = valve ON (same as current firmware)
#define SERIAL_BAUD        921600
#define SPI_HZ             2000000   // 2 MHz is safe with short wires + HCT buffer

#define FW_VERSION         0x0200    // v2.0
#define NUM_CHANNELS       63        // logical channels 1..63
#define NUM_OUTPUTS        64        // 8 x 74HC595
#define NUM_REGS           8

// Defaults (changeable with CONFIG message)
#define DEFAULT_WDT_MS        500
#define DEFAULT_MIN_PULSE_US  200
#define DEFAULT_MAX_PULSE_US  200000UL   // 200 ms
#define DEFAULT_HOLD_MAX_MS   10000      // HOLD_ON auto-off after 10 s

// =============================================================================
// PINS
// =============================================================================
#if PIN_PROFILE == 2
  #define PIN_DATA    23
  #define PIN_CLOCK   18
  #define PIN_LATCH   21
  #define PIN_OE      22
  #define USE_HW_SPI  1
#else
  #define PIN_DATA    14
  #define PIN_CLOCK   13
  #define PIN_LATCH   12   // WARNING: boot strapping pin (legacy only)
  #define PIN_OE      19
  #define USE_HW_SPI  0
#endif
#define PIN_LED       2
#define PIN_DEBUG     27
#define PIN_PC_OK     32

// =============================================================================
// PROTOCOL CONSTANTS (must match ejector_link.py)
// =============================================================================
#define SOF1 0xA5
#define SOF2 0x5A
#define MAX_LEN 60                  // LEN = TYPE + SEQ(2) + payload

// PC -> ESP32
#define T_HEARTBEAT   0x01          // flags u8, pc_time_us u32
#define T_FIRE_NOW    0x11          // mask u64, pulse_us u32, delay_us u32
#define T_CONFIG      0x20          // max_pulse_us u32, min_pulse_us u32, wdt_ms u16, hold_max_ms u16
#define T_REMAP_SET   0x21          // start u8 (0-based logical), count u8, entries[count]
#define T_CMD         0x30          // code u8, arg u16
#define T_TEST        0x31          // mode u8, ch_a u8, ch_b u8, pulse_us u32, gap_us u32, repeat u16
#define T_STATUS_REQ  0x40
#define T_REMAP_REQ   0x41
// ESP32 -> PC
#define T_ACK         0x80          // acked_seq u16, result u8, state u8, esp_us u32
#define T_STATUS      0x81
#define T_EVENT       0x82          // code u8, arg u16, esp_us u32
#define T_REMAP       0x83          // start u8, count u8, entries[count]

// CMD codes
#define C_STOP_ALL      1
#define C_ARM           4
#define C_HOLD_ON       5
#define C_HOLD_OFF      6
#define C_DISARM        8
#define C_RESET_COUNTS  9
#define C_REMAP_SAVE    10
#define C_REMAP_DEFAULT 11

// Results
#define RES_OK            0
#define RES_LATE          1
#define RES_PARTIAL_BUSY  2
#define RES_BUSY          3
#define RES_BAD_PARAM     4
#define RES_TRIPPED       6
#define RES_UNKNOWN_TYPE  7

// States
#define S_DISARMED 0
#define S_ARMED    1
#define S_TRIPPED  2

// Events
#define E_BOOT         1
#define E_WDT_TRIP     2
#define E_TEST_STEP    3
#define E_TEST_DONE    4
#define E_HOLD_TIMEOUT 5
#define E_ARMED        6

// =============================================================================
// STATE
// =============================================================================
Preferences prefs;

uint8_t  remapTable[NUM_CHANNELS];          // logical 0..62 -> physical 0..63, 0xFF = disabled

bool     outActive[NUM_OUTPUTS];
bool     outHold[NUM_OUTPUTS];
uint32_t outEndUs[NUM_OUTPUTS];
uint32_t outHoldStartMs[NUM_OUTPUTS];
bool     pendValid[NUM_OUTPUTS];
uint32_t pendStartUs[NUM_OUTPUTS];
uint32_t pendEndUs[NUM_OUTPUTS];
uint64_t lastOutMask = 0;

uint8_t  state = S_DISARMED;
uint32_t lastBinaryRxMs = 0;
uint32_t wdtMs = DEFAULT_WDT_MS;
uint32_t minPulseUs = DEFAULT_MIN_PULSE_US;
uint32_t maxPulseUs = DEFAULT_MAX_PULSE_US;
uint32_t holdMaxMs = DEFAULT_HOLD_MAX_MS;

// counters
uint32_t c_rx_ok = 0, c_crc_err = 0, c_len_err = 0, c_unknown = 0, c_seq_gaps = 0;
uint32_t c_fired = 0, c_busy = 0, c_wdt_trips = 0, c_text_cmds = 0, c_line_too_long = 0;
uint32_t c_disabled_hits = 0, c_late = 0;

uint16_t txSeq = 0;
uint16_t lastRxSeq = 0;
bool     haveRxSeq = false;

// binary parser
enum { P_SOF1, P_SOF2, P_LEN, P_BODY, P_CRC1, P_CRC2 } pState = P_SOF1;
uint8_t  pLen = 0, pIdx = 0;
uint8_t  pBody[MAX_LEN];
uint16_t pCrcRx = 0;
uint32_t pLastByteUs = 0;

// text parser
char     tBuf[256];
uint16_t tIdx = 0;
bool     tOverflow = false;

// test mode
bool     testActive = false;
uint8_t  testMode = 0;        // 1 = single repeat, 2 = sweep
uint8_t  testCh = 0, testEndCh = 0;      // logical 0-based
uint32_t testPulseUs = 0, testGapUs = 0;
uint32_t testRepeatLeft = 0;  // 0xFFFFFFFF = forever
uint32_t testNextUs = 0;

// LED
uint32_t ledLastMs = 0;
bool     ledState = false;

// =============================================================================
// HELPERS
// =============================================================================
static inline bool reached(uint32_t now, uint32_t t) { return (int32_t)(now - t) >= 0; }
static inline uint32_t later(uint32_t a, uint32_t b) { return ((int32_t)(a - b) >= 0) ? a : b; }

uint16_t crc16(const uint8_t* d, uint16_t n) {           // CRC-16/CCITT-FALSE
  uint16_t crc = 0xFFFF;
  for (uint16_t i = 0; i < n; i++) {
    crc ^= (uint16_t)d[i] << 8;
    for (uint8_t b = 0; b < 8; b++) crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : (crc << 1);
  }
  return crc;
}

static inline uint16_t rd16(const uint8_t* p) { return p[0] | ((uint16_t)p[1] << 8); }
static inline uint32_t rd32(const uint8_t* p) { return p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24); }
static inline uint64_t rd64(const uint8_t* p) { return (uint64_t)rd32(p) | ((uint64_t)rd32(p + 4) << 32); }
static inline void wr16(uint8_t* p, uint16_t v) { p[0] = v; p[1] = v >> 8; }
static inline void wr32(uint8_t* p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }

void sendFrame(uint8_t type, const uint8_t* payload, uint8_t plen) {
  uint8_t f[MAX_LEN + 8];
  uint8_t len = 3 + plen;
  f[0] = SOF1; f[1] = SOF2; f[2] = len; f[3] = type;
  wr16(&f[4], txSeq++);
  if (plen) memcpy(&f[6], payload, plen);
  uint16_t crc = crc16(&f[2], len + 1);
  wr16(&f[6 + plen], crc);
  Serial.write(f, 8 + plen);
}

void sendAck(uint16_t seq, uint8_t result) {
  uint8_t p[8];
  wr16(&p[0], seq); p[2] = result; p[3] = state; wr32(&p[4], micros());
  sendFrame(T_ACK, p, 8);
}

void sendEvent(uint8_t code, uint16_t arg) {
  uint8_t p[7];
  p[0] = code; wr16(&p[1], arg); wr32(&p[3], micros());
  sendFrame(T_EVENT, p, 7);
}

void sendStatus() {
  uint8_t p[3 + 12 * 4];
  wr16(&p[0], FW_VERSION); p[2] = state;
  uint32_t c[12] = {c_rx_ok, c_crc_err, c_len_err, c_unknown, c_seq_gaps, c_fired,
                    c_busy, c_wdt_trips, c_text_cmds, c_line_too_long, c_disabled_hits, c_late};
  for (int i = 0; i < 12; i++) wr32(&p[3 + 4 * i], c[i]);
  sendFrame(T_STATUS, p, sizeof(p));
}

void sendRemap() {
  for (uint8_t start = 0; start < NUM_CHANNELS; start += 32) {
    uint8_t count = min(32, NUM_CHANNELS - start);
    uint8_t p[2 + 32];
    p[0] = start; p[1] = count;
    memcpy(&p[2], &remapTable[start], count);
    sendFrame(T_REMAP, p, 2 + count);
  }
}

// =============================================================================
// SHIFT REGISTER OUTPUT
// =============================================================================
void writeOutputs(uint64_t physMask) {
  uint8_t bytes[NUM_REGS];
  for (int i = 0; i < NUM_REGS; i++) {
    uint8_t b = (physMask >> (8 * i)) & 0xFF;
    bytes[i] = ACTIVE_LOW ? (uint8_t)~b : b;
  }
  digitalWrite(PIN_LATCH, LOW);
#if USE_HW_SPI
  SPI.beginTransaction(SPISettings(SPI_HZ, MSBFIRST, SPI_MODE0));
  for (int i = NUM_REGS - 1; i >= 0; i--) SPI.transfer(bytes[i]);   // byte 7 -> chip 8 ... byte 0 -> chip 1
  SPI.endTransaction();
#else
  for (int i = NUM_REGS - 1; i >= 0; i--) {
    for (uint8_t b = 0; b < 8; b++) {
      digitalWrite(PIN_DATA, (bytes[i] >> (7 - b)) & 1);
      delayMicroseconds(1);
      digitalWrite(PIN_CLOCK, HIGH); delayMicroseconds(1);
      digitalWrite(PIN_CLOCK, LOW);  delayMicroseconds(1);
    }
  }
#endif
  digitalWrite(PIN_LATCH, HIGH);
  digitalWrite(PIN_DEBUG, physMask ? HIGH : LOW);
}

void outputsEnable(bool en) { digitalWrite(PIN_OE, en ? LOW : HIGH); }   // /OE is active LOW

void clearAll() {
  for (int p = 0; p < NUM_OUTPUTS; p++) { outActive[p] = false; outHold[p] = false; pendValid[p] = false; }
  testActive = false;
  writeOutputs(0);
  lastOutMask = 0;
}

// =============================================================================
// REMAP TABLE
// =============================================================================
void remapDefault() { for (int c = 0; c < NUM_CHANNELS; c++) remapTable[c] = c; }

void remapLoad() {
  prefs.begin("ejector", true);
  size_t n = prefs.getBytes("remap", remapTable, NUM_CHANNELS);
  prefs.end();
  if (n != NUM_CHANNELS) remapDefault();
  for (int c = 0; c < NUM_CHANNELS; c++)
    if (remapTable[c] >= NUM_OUTPUTS && remapTable[c] != 0xFF) remapTable[c] = c;
}

bool remapSave() {
  prefs.begin("ejector", false);
  size_t n = prefs.putBytes("remap", remapTable, NUM_CHANNELS);
  prefs.end();
  return n == NUM_CHANNELS;
}

// =============================================================================
// FIRING  (logical mask: bit 0 = channel 1)
// =============================================================================
uint8_t fireLogical(uint64_t mask, uint32_t pulseUs, uint32_t delayUs) {
  if (pulseUs < minPulseUs) pulseUs = minPulseUs;
  if (pulseUs > maxPulseUs) pulseUs = maxPulseUs;
  uint32_t now = micros();
  uint32_t start = now + delayUs;
  uint32_t end = start + pulseUs;
  bool anyBusy = false, anyOk = false;

  for (int c = 0; c < NUM_CHANNELS; c++) {
    if (!(mask & (1ULL << c))) continue;
    uint8_t p = remapTable[c];
    if (p == 0xFF) { c_disabled_hits++; continue; }

    if (delayUs == 0) {                                   // fire now
      if (outActive[p]) outEndUs[p] = later(outEndUs[p], end);
      else { outActive[p] = true; outEndUs[p] = end; }
      anyOk = true;
    } else if (outActive[p] && !reached(outEndUs[p], start)) {   // overlaps running pulse -> extend
      outEndUs[p] = later(outEndUs[p], end);
      anyOk = true;
    } else if (!pendValid[p]) {
      pendValid[p] = true; pendStartUs[p] = start; pendEndUs[p] = end;
      anyOk = true;
    } else if (reached(pendEndUs[p], start) && reached(end, pendStartUs[p])) {
      // new window overlaps or touches the pending window -> merge into one longer window
      if ((int32_t)(start - pendStartUs[p]) < 0) pendStartUs[p] = start;
      pendEndUs[p] = later(pendEndUs[p], end);
      anyOk = true;
    } else {
      c_busy++; anyBusy = true;
    }
  }
  if (anyBusy) return anyOk ? RES_PARTIAL_BUSY : RES_BUSY;
  return RES_OK;
}

void setHold(int ch1, bool on) {
  if (ch1 < 1 || ch1 > NUM_CHANNELS) return;
  uint8_t p = remapTable[ch1 - 1];
  if (p == 0xFF) return;
  outHold[p] = on;
  outActive[p] = on;
  if (on) outHoldStartMs[p] = millis();
}

void serviceOutputs() {
  uint32_t now = micros();
  uint32_t nowMs = millis();
  uint64_t m = 0;
  for (int p = 0; p < NUM_OUTPUTS; p++) {
    if (pendValid[p] && reached(now, pendStartUs[p])) {
      outEndUs[p] = outActive[p] ? later(outEndUs[p], pendEndUs[p]) : pendEndUs[p];
      outActive[p] = true;
      pendValid[p] = false;
    }
    if (outHold[p]) {
      if (nowMs - outHoldStartMs[p] > holdMaxMs) {
        outHold[p] = false; outActive[p] = false;
        Serial.printf("EVENT:HOLD_TIMEOUT OUT %d\n", p);
        sendEvent(E_HOLD_TIMEOUT, p);
      }
    } else if (outActive[p] && reached(now, outEndUs[p])) {
      outActive[p] = false;
    }
    if (outActive[p]) m |= (1ULL << p);
  }
  if (m != lastOutMask) {
    c_fired += __builtin_popcountll(m & ~lastOutMask);
    writeOutputs(m);
    lastOutMask = m;
  }
}

// =============================================================================
// STATE MACHINE / WATCHDOG
// =============================================================================
void setArmed() {
  if (state != S_ARMED) {
    clearAll();
    outputsEnable(true);
    state = S_ARMED;
    sendEvent(E_ARMED, 0);
  }
}

void trip() {
  clearAll();
  outputsEnable(false);              // hardware off, independent of register contents
  state = S_TRIPPED;
  c_wdt_trips++;
  sendEvent(E_WDT_TRIP, 0);
  Serial.println("EVENT:WDT_TRIP all valves OFF (no PC message for 500 ms). Send ARM to resume.");
}

void serviceWatchdog() {
  if (state == S_ARMED && (millis() - lastBinaryRxMs) > wdtMs) trip();
  digitalWrite(PIN_PC_OK, state == S_ARMED ? HIGH : LOW);
}

// =============================================================================
// TEST MODE
// =============================================================================
void printChannelInfo(int ch0) {
  uint8_t p = remapTable[ch0];
  if (p == 0xFF) Serial.printf("[TEST] CH %d -> DISABLED in remap table\n", ch0 + 1);
  else Serial.printf("[TEST] CH %d -> OUT %d -> CHIP %d, Q%d\n", ch0 + 1, p, p / 8 + 1, p % 8);
}

void startTest(uint8_t mode, int chA1, int chB1, uint32_t pulseUs, uint32_t gapUs, uint32_t repeat) {
  if (chA1 < 1) chA1 = 1;
  if (chB1 > NUM_CHANNELS) chB1 = NUM_CHANNELS;
  if (chA1 > NUM_CHANNELS || chB1 < chA1) { Serial.println("ERR:TEST_RANGE"); return; }
  clearAll();
  testActive = true;
  testMode = mode;
  testCh = chA1 - 1;
  testEndCh = chB1 - 1;
  testPulseUs = pulseUs;
  testGapUs = gapUs;
  testRepeatLeft = repeat;
  testNextUs = micros();
  Serial.printf("[TEST] START mode=%s ch %d..%d pulse=%lu us gap=%lu us\n",
                mode == 1 ? "SINGLE" : "SWEEP", chA1, chB1, (unsigned long)pulseUs, (unsigned long)gapUs);
}

void serviceTest() {
  if (!testActive) return;
  uint32_t now = micros();
  if (!reached(now, testNextUs)) return;

  if (testMode == 1) {                                    // single channel, repeat
    if (testRepeatLeft == 0) { testActive = false; Serial.println("[TEST] DONE"); sendEvent(E_TEST_DONE, 0); return; }
    if (testRepeatLeft != 0xFFFFFFFF) testRepeatLeft--;
  } else {                                                // sweep
    if (testCh > testEndCh) { testActive = false; Serial.println("[TEST] DONE"); sendEvent(E_TEST_DONE, 0); return; }
  }
  fireLogical(1ULL << testCh, testPulseUs, 0);
  printChannelInfo(testCh);
  uint8_t p = remapTable[testCh];
  sendEvent(E_TEST_STEP, (uint16_t)(testCh + 1) | ((uint16_t)p << 8));
  testNextUs = now + testPulseUs + testGapUs;
  if (testMode == 2) testCh++;
}

// =============================================================================
// BINARY MESSAGE HANDLER
// =============================================================================
void handleBinary(uint8_t type, uint16_t seq, const uint8_t* pl, uint8_t plen) {
  c_rx_ok++;
  if (haveRxSeq && seq != (uint16_t)(lastRxSeq + 1)) c_seq_gaps += (uint16_t)(seq - lastRxSeq - 1);
  lastRxSeq = seq; haveRxSeq = true;
  lastBinaryRxMs = millis();

  bool isCmd = (type == T_CMD && plen >= 3);
  uint8_t code = isCmd ? pl[0] : 0;

  // State transitions
  if (state == S_DISARMED && !(isCmd && code == C_DISARM)) setArmed();
  if (state == S_TRIPPED) {
    bool allowed = (type == T_STATUS_REQ || type == T_REMAP_REQ ||
                    (isCmd && (code == C_ARM || code == C_STOP_ALL || code == C_DISARM || code == C_RESET_COUNTS)));
    if (!allowed) { sendAck(seq, RES_TRIPPED); return; }
  }

  switch (type) {
    case T_HEARTBEAT:
      sendAck(seq, RES_OK);
      break;

    case T_FIRE_NOW: {
      if (plen != 16) { sendAck(seq, RES_BAD_PARAM); break; }
      uint8_t r = fireLogical(rd64(&pl[0]), rd32(&pl[8]), rd32(&pl[12]));
      sendAck(seq, r);
      break;
    }

    case T_CONFIG: {
      if (plen != 12) { sendAck(seq, RES_BAD_PARAM); break; }
      maxPulseUs = constrain(rd32(&pl[0]), 1000UL, 1000000UL);
      minPulseUs = constrain(rd32(&pl[4]), 50UL, 10000UL);
      wdtMs      = constrain(rd16(&pl[8]), 100, 2000);
      holdMaxMs  = constrain(rd16(&pl[10]), 100, 60000);
      sendAck(seq, RES_OK);
      break;
    }

    case T_REMAP_SET: {
      if (plen < 2) { sendAck(seq, RES_BAD_PARAM); break; }
      uint8_t start = pl[0], count = pl[1];
      if (plen != 2 + count || start + count > NUM_CHANNELS) { sendAck(seq, RES_BAD_PARAM); break; }
      for (int i = 0; i < count; i++) {
        uint8_t v = pl[2 + i];
        if (v >= NUM_OUTPUTS && v != 0xFF) { sendAck(seq, RES_BAD_PARAM); return; }
      }
      memcpy(&remapTable[start], &pl[2], count);
      clearAll();
      sendAck(seq, RES_OK);
      break;
    }

    case T_REMAP_REQ:
      sendAck(seq, RES_OK);
      sendRemap();
      break;

    case T_CMD: {
      if (plen != 3) { sendAck(seq, RES_BAD_PARAM); break; }
      uint16_t arg = rd16(&pl[1]);
      switch (code) {
        case C_STOP_ALL:      clearAll(); break;
        case C_ARM:           if (state == S_TRIPPED) { state = S_DISARMED; } setArmed(); break;
        case C_HOLD_ON:       setHold(arg, true); break;
        case C_HOLD_OFF:      setHold(arg, false); break;
        case C_DISARM:        clearAll(); outputsEnable(true); state = S_DISARMED; break;
        case C_RESET_COUNTS:  c_rx_ok = c_crc_err = c_len_err = c_unknown = c_seq_gaps = 0;
                              c_fired = c_busy = c_text_cmds = c_line_too_long = c_disabled_hits = c_late = 0; break;
        case C_REMAP_SAVE:    sendAck(seq, remapSave() ? RES_OK : RES_BAD_PARAM); return;
        case C_REMAP_DEFAULT: remapDefault(); clearAll(); break;
        default:              sendAck(seq, RES_BAD_PARAM); return;
      }
      sendAck(seq, RES_OK);
      break;
    }

    case T_TEST: {
      if (plen != 13) { sendAck(seq, RES_BAD_PARAM); break; }
      uint8_t mode = pl[0];
      uint16_t rep = rd16(&pl[11]);
      if (mode == 0) { clearAll(); sendAck(seq, RES_OK); break; }        // mode 0 = stop test
      startTest(mode, pl[1], pl[2], rd32(&pl[3]), rd32(&pl[7]), rep == 0xFFFF ? 0xFFFFFFFF : rep);
      sendAck(seq, testActive ? RES_OK : RES_BAD_PARAM);
      break;
    }

    case T_STATUS_REQ:
      sendStatus();
      break;

    default:
      c_unknown++;
      sendAck(seq, RES_UNKNOWN_TYPE);
  }
}

// =============================================================================
// TEXT COMMANDS (legacy + manual diagnostics from Serial Monitor)
// =============================================================================
// Parse "a,b,c" channel list starting at p; stops at ':' or end. Returns pointer after list.
const char* parseChannelList(const char* p, uint64_t& mask) {
  while (*p && *p != ':') {
    char* e;
    long ch = strtol(p, &e, 10);
    if (e == p) { p++; continue; }
    if (ch >= 1 && ch <= NUM_CHANNELS) mask |= (1ULL << (ch - 1));
    p = e;
    if (*p == ',') p++;
  }
  return p;
}

uint32_t msToUs(const char* p, uint32_t defUs) {
  if (!p || !*p) return defUs;
  float ms = strtof(p, nullptr);
  return ms > 0 ? (uint32_t)(ms * 1000.0f) : defUs;
}

void printStatusText() {
  Serial.printf("STATUS FW=%d.%d STATE=%s RX_OK=%lu CRC_ERR=%lu LEN_ERR=%lu UNKNOWN=%lu SEQ_GAPS=%lu "
                "FIRED=%lu BUSY=%lu WDT_TRIPS=%lu TEXT=%lu LINE_TOO_LONG=%lu DISABLED_HITS=%lu\n",
                FW_VERSION >> 8, FW_VERSION & 0xFF,
                state == S_ARMED ? "ARMED" : state == S_TRIPPED ? "TRIPPED" : "DISARMED",
                (unsigned long)c_rx_ok, (unsigned long)c_crc_err, (unsigned long)c_len_err, (unsigned long)c_unknown,
                (unsigned long)c_seq_gaps, (unsigned long)c_fired, (unsigned long)c_busy, (unsigned long)c_wdt_trips,
                (unsigned long)c_text_cmds, (unsigned long)c_line_too_long, (unsigned long)c_disabled_hits);
}

void printMap() {
  Serial.println("MAP  logical CH -> physical OUT (CHIP, Q)");
  for (int c = 0; c < NUM_CHANNELS; c++) {
    uint8_t p = remapTable[c];
    if (p == 0xFF) Serial.printf("MAP CH %2d -> DISABLED\n", c + 1);
    else Serial.printf("MAP CH %2d -> OUT %2d (CHIP %d, Q%d)%s\n", c + 1, p, p / 8 + 1, p % 8, p == c ? "" : "  *remapped*");
  }
}

void printHelp() {
  Serial.println("TEXT COMMANDS: PING | STATUS | STOP | CLEAR | ALL_OFF | HELP");
  Serial.println("  FIRE:<ch>:<ms>[:<gap_ms>:<count>]   EJ:<ch>:<ms>   FM:<16 hex mask>:<ms>");
  Serial.println("  FIRE_MULTI:<ch,ch,...>:<ms>          SA:<b0>:..:<b7>[:<ms>]");
  Serial.println("  TEST  (sweep 1-63, 30 ms / 70 ms)    TEST:<from>:<to>:<pulse_ms>:<gap_ms>");
  Serial.println("  CONT:<ch>:<pulse_ms>:<gap_ms>        HOLD_ON:<ch>   HOLD_OFF:<ch>");
  Serial.println("  MAP?   MAP:<ch>:<out|-1>   MAP_SWAP:<chA>:<chB>   MAP_SAVE   MAP_DEFAULT");
}

void handleText(char* cmd) {
  // trim
  while (*cmd == ' ') cmd++;
  int n = strlen(cmd);
  while (n > 0 && (cmd[n - 1] == ' ' || cmd[n - 1] == '\r')) cmd[--n] = 0;
  if (n == 0) return;
  c_text_cmds++;

  if (state == S_TRIPPED) {                       // manual diagnostics after a trip
    state = S_DISARMED; outputsEnable(true);
    Serial.println("INFO:TRIPPED -> DISARMED (text command received)");
  }

  if (!strcmp(cmd, "PING"))                           { Serial.printf("ACK:PING PONG:LAMTON_EJECTOR_V%d.%d\n", FW_VERSION >> 8, FW_VERSION & 0xFF); }
  else if (!strcmp(cmd, "STATUS"))                    { printStatusText(); }
  else if (!strcmp(cmd, "HELP"))                      { printHelp(); }
  else if (!strcmp(cmd, "STOP") || !strcmp(cmd, "CLEAR") || !strcmp(cmd, "ALL_OFF") || !strcmp(cmd, "CONT_STOP")) {
    clearAll(); Serial.println("ACK:STOP");
  }
  else if (!strncmp(cmd, "FIRE_MULTI:", 11)) {
    uint64_t mask = 0;
    const char* p = parseChannelList(cmd + 11, mask);
    uint32_t us = msToUs(*p == ':' ? p + 1 : nullptr, 5000);
    if (mask) { uint8_t r = fireLogical(mask, us, 0); Serial.println(r == RES_OK ? "ACK:FIRE_MULTI" : "BUSY"); }
    else Serial.println("ERR:NO_CHANNELS");
  }
  else if (!strncmp(cmd, "FM:", 3)) {                  // FM:<16 hex>:<ms>
    char* e;
    uint64_t mask = strtoull(cmd + 3, &e, 16);
    uint32_t us = msToUs(*e == ':' ? e + 1 : nullptr, 5000);
    mask &= (1ULL << NUM_CHANNELS) - 1;
    if (mask) { fireLogical(mask, us, 0); Serial.println("ACK:FM"); } else Serial.println("ERR:NO_CHANNELS");
  }
  else if (!strncmp(cmd, "FIRE:", 5) || !strncmp(cmd, "EJ:", 3)) {   // FIRE:ch:ms[:gap:count]
    char* p = cmd + (cmd[0] == 'F' ? 5 : 3);
    char* e;
    long ch = strtol(p, &e, 10);
    uint32_t us = msToUs(*e == ':' ? e + 1 : nullptr, 5000);
    char* g = (*e == ':') ? strchr(e + 1, ':') : nullptr;
    if (ch < 1 || ch > NUM_CHANNELS) { Serial.println("ERR:CHANNEL"); return; }
    if (g) {
      uint32_t gapUs = msToUs(g + 1, 100000);
      char* cnt = strchr(g + 1, ':');
      uint32_t count = cnt ? strtoul(cnt + 1, nullptr, 10) : 1;
      startTest(1, ch, ch, us, gapUs, count ? count : 1);
    } else {
      fireLogical(1ULL << (ch - 1), us, 0);
    }
    Serial.printf("ACK:FIRE:%ld\n", ch);
    Serial.printf("ACK:EJ_FIRED:%ld\n", ch);
  }
  else if (!strncmp(cmd, "SA:", 3)) {                  // SA:b0:..:b7[:ms]
    uint64_t mask = 0;
    char* p = cmd + 3;
    for (int i = 0; i < NUM_REGS && *p; i++) {
      char* e;
      unsigned long b = strtoul(p, &e, 10);
      mask |= (uint64_t)(b & 0xFF) << (8 * i);
      p = (*e == ':') ? e + 1 : e;
    }
    uint32_t us = msToUs(*p ? p : nullptr, 1000);
    mask &= (1ULL << NUM_CHANNELS) - 1;
    if (mask) fireLogical(mask, us, 0); else clearAll();
  }
  else if (!strcmp(cmd, "TEST")) { startTest(2, 1, NUM_CHANNELS, 30000, 70000, 0); }
  else if (!strncmp(cmd, "TEST:", 5) || !strncmp(cmd, "AUTO,", 5)) {   // TEST:a:b:pulse:gap
    long v[4] = {1, NUM_CHANNELS, 30000, 70000};      // from, to, pulse_us, gap_us
    char* p = cmd + 5;
    for (int i = 0; i < 4 && *p; i++) {
      char* e;
      float f = strtof(p, &e);
      if (e == p) break;
      v[i] = (i >= 2) ? (long)(f * 1000.0f) : (long)f;  // pulse/gap given in ms
      p = (*e == ':' || *e == ',') ? e + 1 : e;
    }
    startTest(2, v[0], v[1], v[2], v[3], 0);
    Serial.printf("ACK:TEST:%ld:%ld\n", v[0], v[1]);
  }
  else if (!strncmp(cmd, "CONT:", 5)) {                // CONT:ch:pulse:gap  (runs until STOP)
    char* e;
    long ch = strtol(cmd + 5, &e, 10);
    uint32_t pulse = msToUs(*e == ':' ? e + 1 : nullptr, 5000);
    char* g = (*e == ':') ? strchr(e + 1, ':') : nullptr;
    uint32_t gap = msToUs(g ? g + 1 : nullptr, 100000);
    startTest(1, ch, ch, pulse, gap, 0xFFFFFFFF);
    Serial.printf("ACK:CONT:%ld\n", ch);
  }
  else if (!strncmp(cmd, "HOLD_ON:", 8))  { int ch = atoi(cmd + 8); setHold(ch, true);  Serial.printf("ACK:HOLD_ON:%d\n", ch); }
  else if (!strncmp(cmd, "HOLD_OFF:", 9)) { int ch = atoi(cmd + 9); setHold(ch, false); Serial.printf("ACK:HOLD_OFF:%d\n", ch); }
  else if (!strcmp(cmd, "MAP?"))          { printMap(); }
  else if (!strncmp(cmd, "MAP:", 4)) {                 // MAP:ch:out  (out -1 = disable)
    char* e;
    long ch = strtol(cmd + 4, &e, 10);
    long out = (*e == ':') ? strtol(e + 1, nullptr, 10) : -2;
    if (ch < 1 || ch > NUM_CHANNELS || out < -1 || out >= NUM_OUTPUTS) { Serial.println("ERR:MAP"); return; }
    clearAll();
    remapTable[ch - 1] = (out < 0) ? 0xFF : (uint8_t)out;
    Serial.printf("ACK:MAP CH %ld -> %s%ld (not saved; send MAP_SAVE)\n", ch, out < 0 ? "DISABLED " : "OUT ", out);
  }
  else if (!strncmp(cmd, "MAP_SWAP:", 9)) {
    char* e;
    long a = strtol(cmd + 9, &e, 10);
    long b = (*e == ':') ? strtol(e + 1, nullptr, 10) : 0;
    if (a < 1 || a > NUM_CHANNELS || b < 1 || b > NUM_CHANNELS) { Serial.println("ERR:MAP"); return; }
    clearAll();
    uint8_t t = remapTable[a - 1]; remapTable[a - 1] = remapTable[b - 1]; remapTable[b - 1] = t;
    Serial.printf("ACK:MAP_SWAP %ld <-> %ld (not saved; send MAP_SAVE)\n", a, b);
  }
  else if (!strcmp(cmd, "MAP_SAVE"))    { Serial.println(remapSave() ? "ACK:MAP_SAVE" : "ERR:MAP_SAVE"); }
  else if (!strcmp(cmd, "MAP_DEFAULT")) { remapDefault(); clearAll(); Serial.println("ACK:MAP_DEFAULT (not saved; send MAP_SAVE)"); }
  else if (!strncmp(cmd, "V", 1) || !strncmp(cmd, "R:", 2) || !strncmp(cmd, "L:", 2) || !strncmp(cmd, "W", 1) || !strncmp(cmd, "PP:", 3)) {
    // Relay/vibrator/light/wiper commands belong to other boards: ignore quietly.
  }
  else { Serial.printf("ERR:UNKNOWN_COMMAND %s (send HELP)\n", cmd); }
}

// =============================================================================
// SERIAL INPUT: binary frames and text lines on the same port
// (0xA5 never appears in text, so the two can be told apart)
// =============================================================================
void processByte(uint8_t b) {
  uint32_t now = micros();
  if (pState != P_SOF1 && (now - pLastByteUs) > 20000) {   // half-received frame timed out
    c_len_err++;
    pState = P_SOF1;
  }
  pLastByteUs = now;

  switch (pState) {
    case P_SOF1:
      if (b == SOF1) { pState = P_SOF2; return; }
      // ---- text ----
      if (b == '\n' || b == '\r') {
        if (tOverflow) { c_line_too_long++; Serial.println("ERR:LINE_TOO_LONG"); }
        else if (tIdx > 0) { tBuf[tIdx] = 0; handleText(tBuf); }
        tIdx = 0; tOverflow = false;
      } else if (b >= 0x20 && b < 0x7F) {
        if (tIdx < sizeof(tBuf) - 1) tBuf[tIdx++] = (char)b;
        else tOverflow = true;
      }
      return;
    case P_SOF2:
      pState = (b == SOF2) ? P_LEN : P_SOF1;
      return;
    case P_LEN:
      if (b < 3 || b > MAX_LEN) { c_len_err++; pState = P_SOF1; return; }
      pLen = b; pIdx = 0; pState = P_BODY;
      return;
    case P_BODY:
      pBody[pIdx++] = b;
      if (pIdx >= pLen) pState = P_CRC1;
      return;
    case P_CRC1:
      pCrcRx = b; pState = P_CRC2;
      return;
    case P_CRC2: {
      pCrcRx |= (uint16_t)b << 8;
      pState = P_SOF1;
      uint8_t tmp[MAX_LEN + 1];
      tmp[0] = pLen;
      memcpy(&tmp[1], pBody, pLen);
      if (crc16(tmp, pLen + 1) != pCrcRx) { c_crc_err++; return; }
      handleBinary(pBody[0], rd16(&pBody[1]), &pBody[3], pLen - 3);
      return;
    }
  }
}

// =============================================================================
// SETUP / LOOP
// =============================================================================
void setup() {
  // 1. Outputs disabled FIRST (the 10k pull-up already holds OE high during boot)
  pinMode(PIN_OE, OUTPUT);
  digitalWrite(PIN_OE, HIGH);
  pinMode(PIN_LATCH, OUTPUT); digitalWrite(PIN_LATCH, HIGH);
  pinMode(PIN_DEBUG, OUTPUT); digitalWrite(PIN_DEBUG, LOW);
  pinMode(PIN_PC_OK, OUTPUT); digitalWrite(PIN_PC_OK, LOW);
  pinMode(PIN_LED, OUTPUT);   digitalWrite(PIN_LED, LOW);
#if USE_HW_SPI
  SPI.begin(PIN_CLOCK, -1, PIN_DATA, -1);
#else
  pinMode(PIN_DATA, OUTPUT); pinMode(PIN_CLOCK, OUTPUT);
  digitalWrite(PIN_DATA, LOW); digitalWrite(PIN_CLOCK, LOW);
#endif

  // 2. Shift zeros into all registers, THEN enable outputs
  remapLoad();
  clearAll();
  outputsEnable(true);

  // 3. Serial
  Serial.setRxBufferSize(2048);
  Serial.setTxBufferSize(2048);
  Serial.begin(SERIAL_BAUD);

  // 4. Firmware watchdog: reboot if loop() ever hangs for 2 s
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t wdtCfg = {.timeout_ms = 2000, .idle_core_mask = 0, .trigger_panic = true};
  if (esp_task_wdt_reconfigure(&wdtCfg) != ESP_OK) esp_task_wdt_init(&wdtCfg);
#else
  esp_task_wdt_init(2, true);
#endif
  esp_task_wdt_add(NULL);

  delay(50);
  Serial.printf("\nLAMBTON EJECTOR v%d.%d READY | %d baud | pins: DATA %d CLK %d LATCH %d OE %d | %s\n",
                FW_VERSION >> 8, FW_VERSION & 0xFF, SERIAL_BAUD, PIN_DATA, PIN_CLOCK, PIN_LATCH, PIN_OE,
                PIN_PROFILE == 2 ? "NEW PINS" : "LEGACY PINS (GPIO12 boot risk)");
  Serial.println("Send HELP for text commands.");
  sendEvent(E_BOOT, FW_VERSION);
}

void loop() {
  esp_task_wdt_reset();

  int budget = 256;                         // keep valve timing tight: bounded serial work per pass
  while (budget-- > 0 && Serial.available() > 0) processByte((uint8_t)Serial.read());

  serviceTest();
  serviceOutputs();
  serviceWatchdog();

  // LED: ON while firing; fast blink when TRIPPED; slow blink when ARMED and idle
  uint32_t ms = millis();
  if (lastOutMask) digitalWrite(PIN_LED, HIGH);
  else {
    uint32_t period = (state == S_TRIPPED) ? 100 : (state == S_ARMED ? 1000 : 0);
    if (period == 0) digitalWrite(PIN_LED, LOW);
    else if (ms - ledLastMs >= period) { ledLastMs = ms; ledState = !ledState; digitalWrite(PIN_LED, ledState); }
  }
}
