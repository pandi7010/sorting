#include <Arduino.h>

#define NUM_SHIFT_REGISTERS 8
#define TOTAL_CHANNELS      63
#define ACTIVE_LOW          false // 0=OFF, 1=ON (matches original firmware)

#define SHIFT_DATA_PIN    14
#define SHIFT_CLOCK_PIN   13
#define SHIFT_LATCH_PIN   12
#define SHIFT_OE_PIN      19
#define STATUS_LED_PIN    2

#define DEFAULT_PULSE_MS  5
#define MAX_PULSE_MS      5000

uint8_t shiftData[NUM_SHIFT_REGISTERS] = {0};

// State per channel for currently executing events (from queue or manual)
unsigned long channelFireTime[TOTAL_CHANNELS] = {0};
uint32_t channelPulseMs[TOTAL_CHANNELS] = {0};
bool channelActive[TOTAL_CHANNELS] = {false};
bool channelHold[TOTAL_CHANNELS] = {false};

// Non-blocking serial Rx buffer
char rxBuffer[128];
uint8_t rxIndex = 0;

// Test/Continuous modes
enum Mode {
  MODE_NORMAL,
  MODE_CONT,
  MODE_REPEAT,
  MODE_TEST
};
Mode currentMode = MODE_NORMAL;

// Mode Variables
int testStartCh = 1;
int testEndCh = 63;
int testCurrentCh = 1;
uint32_t testPulseMs = 1;
uint32_t testGapMs = 5;
uint32_t testCount = 0;
uint32_t testCurrentCount = 0;
unsigned long testLastAction = 0;
bool testWaitingGap = false;

// =============================================================================
// EVENT QUEUE (FIFO)
// =============================================================================
#define QUEUE_SIZE 256

struct EjectEvent {
  uint64_t channel_mask;
  uint32_t pulse_ms;
};

EjectEvent eventQueue[QUEUE_SIZE];
int queueHead = 0; // Where we write next
int queueTail = 0; // Where we read next

// Stats
uint32_t stat_rx_commands = 0;
uint32_t stat_queued_events = 0;
uint32_t stat_completed_events = 0;
uint32_t stat_queue_overflows = 0;
uint32_t stat_invalid_commands = 0;

void clearQueue() {
  queueHead = 0;
  queueTail = 0;
}

bool enqueueEvent(uint64_t mask, uint32_t pulse) {
  int nextHead = (queueHead + 1) % QUEUE_SIZE;
  if (nextHead == queueTail) {
    stat_queue_overflows++;
    return false; // Full
  }
  eventQueue[queueHead].channel_mask = mask;
  eventQueue[queueHead].pulse_ms = pulse;
  queueHead = nextHead;
  stat_queued_events++;
  return true;
}

bool isQueueEmpty() {
  return (queueHead == queueTail);
}

// =============================================================================
// HARDWARE OUTPUT
// =============================================================================

void updateShiftRegisters() {
  digitalWrite(SHIFT_LATCH_PIN, LOW);
  for (int i = NUM_SHIFT_REGISTERS - 1; i >= 0; i--) {
    shiftOut(SHIFT_DATA_PIN, SHIFT_CLOCK_PIN, MSBFIRST, shiftData[i]);
  }
  digitalWrite(SHIFT_LATCH_PIN, HIGH);
}

void clearAllValves() {
  for (int i = 0; i < NUM_SHIFT_REGISTERS; i++) {
    shiftData[i] = ACTIVE_LOW ? 255 : 0;
  }
  for (int i = 0; i < TOTAL_CHANNELS; i++) {
    channelActive[i] = false;
    channelHold[i] = false;
  }
  updateShiftRegisters();
  digitalWrite(STATUS_LED_PIN, LOW);
}

void executeChannelMask(uint64_t mask, uint32_t pulseMs) {
  if (pulseMs == 0) pulseMs = DEFAULT_PULSE_MS;
  if (pulseMs > MAX_PULSE_MS) pulseMs = MAX_PULSE_MS;

  bool changed = false;
  unsigned long now = millis();
  
  for(int i=0; i<TOTAL_CHANNELS; i++) {
    if (mask & (1ULL << i)) {
      int byteIdx = i / 8;
      int bitIdx = i % 8;
      
      if (ACTIVE_LOW) {
        shiftData[byteIdx] &= ~(1 << bitIdx);
      } else {
        shiftData[byteIdx] |= (1 << bitIdx);
      }
      
      channelActive[i] = true;
      channelFireTime[i] = now;
      channelPulseMs[i] = pulseMs;
      changed = true;
    }
  }
  if(changed) {
    updateShiftRegisters();
    digitalWrite(STATUS_LED_PIN, HIGH);
  }
}

// =============================================================================
// COMMAND PARSER
// =============================================================================

void processCommand(const char* cmd) {
  stat_rx_commands++;
  
  // 1. PING
  if (strcmp(cmd, "PING") == 0) {
    Serial.println("ACK:PING");
  }
  
  // 2. STATUS
  else if (strcmp(cmd, "STATUS") == 0) {
    int qDepth = (queueHead - queueTail + QUEUE_SIZE) % QUEUE_SIZE;
    Serial.print("EJECTOR_READY");
    Serial.print(" QUEUE="); Serial.print(qDepth);
    Serial.print(" RX="); Serial.print(stat_rx_commands);
    Serial.print(" DONE="); Serial.print(stat_completed_events);
    Serial.print(" OVERFLOW="); Serial.println(stat_queue_overflows);
  }
  
  // 3. STOP
  else if (strcmp(cmd, "STOP") == 0) {
    currentMode = MODE_NORMAL;
    clearQueue();
    clearAllValves();
    Serial.println("ACK:STOP");
  }
  
  // 4. ALL_OFF
  else if (strcmp(cmd, "ALL_OFF") == 0) {
    currentMode = MODE_NORMAL;
    clearQueue();
    clearAllValves();
    Serial.println("ACK:ALL_OFF");
  }
  
  // 5. FIRE_MULTI:<ch1>,<ch2>...:<pulse_ms>
  else if (strncmp(cmd, "FIRE_MULTI:", 11) == 0) {
    char temp[64];
    strncpy(temp, cmd + 11, sizeof(temp)-1);
    temp[sizeof(temp)-1] = '\0';
    
    char* colon = strchr(temp, ':');
    if (colon) {
      *colon = '\0';
      uint32_t pulse = atoi(colon + 1);
      
      uint64_t mask = 0;
      char* token = strtok(temp, ",");
      while (token) {
        int ch = atoi(token);
        if (ch >= 1 && ch <= TOTAL_CHANNELS) {
          mask |= (1ULL << (ch - 1));
        }
        token = strtok(NULL, ",");
      }
      
      if (mask != 0) {
        if (enqueueEvent(mask, pulse)) {
          Serial.println("ACK:FIRE_MULTI");
        } else {
          Serial.println("BUSY");
        }
      }
    } else {
      stat_invalid_commands++;
    }
  }

  // 6. FIRE:<channel>:<pulse_ms>
  else if (strncmp(cmd, "FIRE:", 5) == 0) {
    char temp[64];
    strncpy(temp, cmd + 5, sizeof(temp)-1);
    temp[sizeof(temp)-1] = '\0';
    
    char* t1 = strtok(temp, ":");
    char* t2 = strtok(NULL, ":");
    
    if (t1 && t2) {
      int ch = atoi(t1);
      uint32_t pulse = atoi(t2);
      
      if (ch >= 1 && ch <= TOTAL_CHANNELS) {
        uint64_t mask = (1ULL << (ch - 1));
        if (enqueueEvent(mask, pulse)) {
          Serial.print("ACK:FIRE:"); Serial.println(ch);
        } else {
          Serial.println("BUSY");
        }
      }
    } else {
      stat_invalid_commands++;
    }
  }
  
  // 7. CONT:<channel>:<pulse_ms>:<gap_ms>
  else if (strncmp(cmd, "CONT:", 5) == 0) {
    char temp[64];
    strncpy(temp, cmd + 5, sizeof(temp)-1);
    temp[sizeof(temp)-1] = '\0';
    
    char* t1 = strtok(temp, ":");
    char* t2 = strtok(NULL, ":");
    char* t3 = strtok(NULL, ":");
    
    if (t1 && t2 && t3) {
      int ch = atoi(t1);
      uint32_t pulse = atoi(t2);
      uint32_t gap = atoi(t3);
      if (ch >= 1 && ch <= TOTAL_CHANNELS) {
        clearQueue();
        clearAllValves();
        currentMode = MODE_CONT;
        testCurrentCh = ch;
        testPulseMs = pulse;
        testGapMs = gap;
        testWaitingGap = false;
        executeChannelMask((1ULL << (ch - 1)), pulse);
        Serial.print("ACK:CONT:"); Serial.println(ch);
      }
    } else {
      stat_invalid_commands++;
    }
  }
  
  // 8. TEST:1:63:<pulse_ms>:<gap_ms>
  else if (strncmp(cmd, "TEST:", 5) == 0) {
    char temp[64];
    strncpy(temp, cmd + 5, sizeof(temp)-1);
    temp[sizeof(temp)-1] = '\0';
    
    char* t1 = strtok(temp, ":");
    char* t2 = strtok(NULL, ":");
    char* t3 = strtok(NULL, ":");
    char* t4 = strtok(NULL, ":");
    
    if (t1 && t2 && t3 && t4) {
      int sCh = atoi(t1);
      int eCh = atoi(t2);
      uint32_t pulse = atoi(t3);
      uint32_t gap = atoi(t4);
      
      if (sCh >= 1 && eCh <= TOTAL_CHANNELS && sCh <= eCh) {
        clearQueue();
        clearAllValves();
        currentMode = MODE_TEST;
        testStartCh = sCh;
        testEndCh = eCh;
        testCurrentCh = sCh;
        testPulseMs = pulse;
        testGapMs = gap;
        testWaitingGap = false;
        executeChannelMask((1ULL << (testCurrentCh - 1)), pulse);
        Serial.print("ACK:TEST:"); Serial.print(sCh); Serial.print(":"); Serial.println(eCh);
      }
    } else {
      stat_invalid_commands++;
    }
  }
  
  // 9. HOLD_ON:<channel>
  else if (strncmp(cmd, "HOLD_ON:", 8) == 0) {
    int ch = atoi(cmd + 8);
    if (ch >= 1 && ch <= TOTAL_CHANNELS) {
      int idx = ch - 1;
      int byteIdx = idx / 8;
      int bitIdx = idx % 8;
      
      if (ACTIVE_LOW) {
        shiftData[byteIdx] &= ~(1 << bitIdx);
      } else {
        shiftData[byteIdx] |= (1 << bitIdx);
      }
      
      channelActive[idx] = true;
      channelHold[idx] = true;
      
      updateShiftRegisters();
      digitalWrite(STATUS_LED_PIN, HIGH);
      Serial.print("ACK:HOLD_ON:"); Serial.println(ch);
    }
  }
  
  // 10. HOLD_OFF:<channel>
  else if (strncmp(cmd, "HOLD_OFF:", 9) == 0) {
    int ch = atoi(cmd + 9);
    if (ch >= 1 && ch <= TOTAL_CHANNELS) {
      int idx = ch - 1;
      int byteIdx = idx / 8;
      int bitIdx = idx % 8;
      
      if (ACTIVE_LOW) {
        shiftData[byteIdx] |= (1 << bitIdx);
      } else {
        shiftData[byteIdx] &= ~(1 << bitIdx);
      }
      
      channelActive[idx] = false;
      channelHold[idx] = false;
      
      updateShiftRegisters();
      Serial.print("ACK:HOLD_OFF:"); Serial.println(ch);
    }
  }
  else {
    stat_invalid_commands++;
  }
}

void setup() {
  Serial.begin(115200);
  delay(150);

  pinMode(SHIFT_DATA_PIN, OUTPUT);
  pinMode(SHIFT_CLOCK_PIN, OUTPUT);
  pinMode(SHIFT_LATCH_PIN, OUTPUT);
  digitalWrite(SHIFT_LATCH_PIN, HIGH);
  digitalWrite(SHIFT_DATA_PIN, LOW);
  digitalWrite(SHIFT_CLOCK_PIN, LOW);

#ifdef SHIFT_OE_PIN
  pinMode(SHIFT_OE_PIN, OUTPUT);
  digitalWrite(SHIFT_OE_PIN, LOW);
#endif

  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);

  clearAllValves();
  Serial.println("EJECTOR_READY");
}

void loop() {
  unsigned long currentMillis = millis();
  
  // 1. Process active pulse shutoffs
  bool stateChanged = false;
  bool anyActive = false;

  for (int i = 0; i < TOTAL_CHANNELS; i++) {
    if (channelActive[i]) {
      if (channelHold[i]) {
        anyActive = true;
      } else if (currentMillis - channelFireTime[i] >= channelPulseMs[i]) {
        // Turn off
        int byteIdx = i / 8;
        int bitIdx = i % 8;
        if (ACTIVE_LOW) {
          shiftData[byteIdx] |= (1 << bitIdx);
        } else {
          shiftData[byteIdx] &= ~(1 << bitIdx);
        }
        channelActive[i] = false;
        stateChanged = true;
      } else {
        anyActive = true;
      }
    }
  }

  if (stateChanged) {
    updateShiftRegisters();
    if (!anyActive) digitalWrite(STATUS_LED_PIN, LOW);
  }

  // 2. Read Serial
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

  // 3. Dequeue Pending Events
  if (currentMode == MODE_NORMAL && !isQueueEmpty()) {
    // Only dequeue if the required channels are NOT currently active
    // If they are active, wait until they turn off to execute the next pulse.
    uint64_t targetMask = eventQueue[queueTail].channel_mask;
    bool conflict = false;
    for(int i=0; i<TOTAL_CHANNELS; i++) {
      if ((targetMask & (1ULL << i)) && channelActive[i]) {
        conflict = true;
        break;
      }
    }
    
    if (!conflict) {
      uint32_t pulse = eventQueue[queueTail].pulse_ms;
      queueTail = (queueTail + 1) % QUEUE_SIZE;
      stat_completed_events++;
      executeChannelMask(targetMask, pulse);
    }
  }

  // 4. Test Modes (Continuous, Repeat, Auto Test)
  if (currentMode != MODE_NORMAL) {
    if (testWaitingGap) {
      if (currentMillis - testLastAction >= testGapMs) {
        testWaitingGap = false;
        
        if (currentMode == MODE_CONT) {
          executeChannelMask((1ULL << (testCurrentCh - 1)), testPulseMs);
        } 
        else if (currentMode == MODE_REPEAT) {
          if (testCurrentCount < testCount) {
            testCurrentCount++;
            executeChannelMask((1ULL << (testCurrentCh - 1)), testPulseMs);
          } else {
            currentMode = MODE_NORMAL;
          }
        }
        else if (currentMode == MODE_TEST) {
          testCurrentCh++;
          if (testCurrentCh > testEndCh) {
            currentMode = MODE_NORMAL;
          } else {
            executeChannelMask((1ULL << (testCurrentCh - 1)), testPulseMs);
          }
        }
      }
    } else {
      if (!channelActive[testCurrentCh - 1]) {
        testWaitingGap = true;
        testLastAction = currentMillis; 
      }
    }
  }
}
