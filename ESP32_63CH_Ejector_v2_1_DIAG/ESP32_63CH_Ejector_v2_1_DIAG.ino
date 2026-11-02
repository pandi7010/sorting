/*
  LAMBTON EJECTOR - HARDWARE DIAGNOSIS  v2.1-DIAG   (legacy pins, text commands, 115200 baud)
  NOT for production. Use it to find out WHY the 74HC595 board does not respond.

  Output code = the OLD working firmware (ESP32_63CH_ShiftRegister): same pins, Arduino shiftOut(MSBFIRST),
  chip 8 first, latch LOW -> shift -> HIGH, OE driven LOW (enabled) from the start, ACTIVE_LOW false.
  No watchdog, no heartbeat, no binary protocol, GPIO 27 / 32 are NOT touched.

  Serial Monitor: 115200 baud, "Newline". Type HELP.
  Pins:  DATA GPIO14 -> chip1 pin14 (DS) | CLOCK GPIO13 -> all pin11 (SH_CP) | LATCH GPIO12 -> all pin12 (ST_CP)
         OE GPIO19 -> all pin13 (/OE, LOW = outputs enabled) | LED GPIO2
*/
#include <Arduino.h>

#define NUM_REGS   8
#define PIN_DATA   14
#define PIN_CLOCK  13
#define PIN_LATCH  12
#define PIN_OE     19
#define PIN_LED     2
#define ACTIVE_LOW false

uint8_t sr[NUM_REGS] = {0};
char buf[64];
uint8_t idx = 0;
int walkCh = 0;                       // 0 = off
uint32_t walkMs = 500, walkNext = 0;

void shiftAll() {                     // identical to updateShiftRegisters() of the old firmware
  digitalWrite(PIN_LATCH, LOW);
  for (int i = NUM_REGS - 1; i >= 0; i--) shiftOut(PIN_DATA, PIN_CLOCK, MSBFIRST, ACTIVE_LOW ? (uint8_t)~sr[i] : sr[i]);
  digitalWrite(PIN_LATCH, HIGH);
}

void setAll(uint8_t v) { for (int i = 0; i < NUM_REGS; i++) sr[i] = v; shiftAll(); }

void setChannel(int ch) {             // ch 1..63 -> chip (ch-1)/8+1, output Q(ch-1)%8; others OFF
  for (int i = 0; i < NUM_REGS; i++) sr[i] = 0;
  if (ch >= 1 && ch <= 63) sr[(ch - 1) / 8] = 1 << ((ch - 1) % 8);
  shiftAll();
}

void help() {
  Serial.println("DIAG commands (each answers OK ...):");
  Serial.println("  OE0 / OE1      /OE pin (GPIO19) LOW = outputs enabled / HIGH = disabled");
  Serial.println("  DAT0 DAT1      DATA  pin GPIO14 static level  -> measure chip1 pin14");
  Serial.println("  CLK0 CLK1      CLOCK pin GPIO13 static level  -> measure pin11");
  Serial.println("  LAT0 LAT1      LATCH pin GPIO12 static level  -> measure pin12");
  Serial.println("  ALLON / ALLOFF shift 1s / 0s into all 8 chips and latch  -> every Q = VCC / 0 V");
  Serial.println("  CH n           only channel n (1-63) ON, held            -> measure that Q pin");
  Serial.println("  WALK [ms]      channels 1..63 one after the other (default 500 ms each); WALK 0 stops");
  Serial.println("  LED0 LED1      on-board LED");
  Serial.println("  PINS           print pins and levels");
}

void pins() {
  Serial.printf("DATA GPIO%d=%d  CLOCK GPIO%d=%d  LATCH GPIO%d=%d  OE GPIO%d=%d (LOW=enabled)  walk=%d\n",
                PIN_DATA, digitalRead(PIN_DATA), PIN_CLOCK, digitalRead(PIN_CLOCK), PIN_LATCH, digitalRead(PIN_LATCH),
                PIN_OE, digitalRead(PIN_OE), walkCh);
}

void handle(char* c) {
  walkCh = (strncmp(c, "WALK", 4) == 0) ? walkCh : 0;     // any other command stops the walk
  if (!strcmp(c, "HELP")) help();
  else if (!strcmp(c, "OE0")) { digitalWrite(PIN_OE, LOW);  Serial.println("OK /OE = LOW (outputs enabled)"); }
  else if (!strcmp(c, "OE1")) { digitalWrite(PIN_OE, HIGH); Serial.println("OK /OE = HIGH (outputs disabled)"); }
  else if (!strcmp(c, "DAT0")) { digitalWrite(PIN_DATA, LOW);  Serial.println("OK DATA = 0 V"); }
  else if (!strcmp(c, "DAT1")) { digitalWrite(PIN_DATA, HIGH); Serial.println("OK DATA = HIGH"); }
  else if (!strcmp(c, "CLK0")) { digitalWrite(PIN_CLOCK, LOW);  Serial.println("OK CLOCK = 0 V"); }
  else if (!strcmp(c, "CLK1")) { digitalWrite(PIN_CLOCK, HIGH); Serial.println("OK CLOCK = HIGH"); }
  else if (!strcmp(c, "LAT0")) { digitalWrite(PIN_LATCH, LOW);  Serial.println("OK LATCH = 0 V"); }
  else if (!strcmp(c, "LAT1")) { digitalWrite(PIN_LATCH, HIGH); Serial.println("OK LATCH = HIGH"); }
  else if (!strcmp(c, "ALLON")) { setAll(0xFF); Serial.println("OK all 64 outputs ON (latched)"); }
  else if (!strcmp(c, "ALLOFF")) { setAll(0x00); Serial.println("OK all outputs OFF"); }
  else if (!strncmp(c, "CH ", 3)) {
    int n = atoi(c + 3);
    if (n < 1 || n > 63) { Serial.println("ERR channel 1-63"); return; }
    setChannel(n);
    int q = (n - 1) % 8;                                      // 74HC595: Q0 = pin 15, Q1..Q7 = pins 1..7
    Serial.printf("OK channel %d ON: chip %d, Q%d = IC pin %d\n", n, (n - 1) / 8 + 1, q, q == 0 ? 15 : q);
  }
  else if (!strncmp(c, "WALK", 4)) {
    int ms = (c[4] == ' ') ? atoi(c + 5) : 500;
    if (ms <= 0) { walkCh = 0; setAll(0); Serial.println("OK walk stopped"); return; }
    walkMs = ms; walkCh = 1; walkNext = millis(); Serial.printf("OK walk 1..63, %d ms each\n", ms);
  }
  else if (!strcmp(c, "LED0")) { digitalWrite(PIN_LED, LOW);  Serial.println("OK LED off"); }
  else if (!strcmp(c, "LED1")) { digitalWrite(PIN_LED, HIGH); Serial.println("OK LED on"); }
  else if (!strcmp(c, "PINS")) pins();
  else Serial.printf("ERR unknown '%s' - type HELP\n", c);
}

void setup() {
  Serial.begin(115200);
  delay(150);
  pinMode(PIN_DATA, OUTPUT); pinMode(PIN_CLOCK, OUTPUT); pinMode(PIN_LATCH, OUTPUT);
  digitalWrite(PIN_LATCH, HIGH); digitalWrite(PIN_DATA, LOW); digitalWrite(PIN_CLOCK, LOW);
  pinMode(PIN_OE, OUTPUT); digitalWrite(PIN_OE, LOW);       // like the old firmware: enabled from the start
  pinMode(PIN_LED, OUTPUT); digitalWrite(PIN_LED, LOW);
  setAll(0);
  Serial.println("\nLAMBTON EJECTOR v2.1-DIAG READY (legacy pins, 115200 baud). Type HELP.");
  pins();
}

void loop() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (idx) { buf[idx] = 0; for (char* p = buf; *p; p++) *p = toupper(*p); handle(buf); idx = 0; }
    } else if (idx < sizeof(buf) - 1) buf[idx++] = ch;
  }
  if (walkCh && (int32_t)(millis() - walkNext) >= 0) {
    setChannel(walkCh);
    Serial.printf("WALK channel %d (chip %d, Q%d)\n", walkCh, (walkCh - 1) / 8 + 1, (walkCh - 1) % 8);
    walkCh = (walkCh >= 63) ? 1 : walkCh + 1;
    walkNext = millis() + walkMs;
  }
}
