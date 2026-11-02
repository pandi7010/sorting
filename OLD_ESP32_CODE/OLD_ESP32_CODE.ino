/*
   TCM VISION
   63 CHANNEL MATRIX EJECTOR CONTROLLER

   ESP32 + 8 x 74HC595

   WIRING
   -----------------------------
   ESP32 GPIO14 -> 74HC595 DS   Pin 14
   ESP32 GPIO13 -> 74HC595 SHCP Pin 11
   ESP32 GPIO12 -> 74HC595 STCP Pin 12

   8 x 74HC595 = 64 outputs
   CH1 ... CH63 = used
   CH64 = unused

   USB SERIAL = 115200

   COMMANDS
   -----------------------------
   FIRE,1,1
   FIRE,63,2

   ON,1
   OFF,1

   CONT_START,1,1,5,0
   CONT_STOP

   AUTO,1,63,1,20

   ALL_OFF
   STOP
   STATUS
*/

#define DATA_PIN   14
#define CLOCK_PIN  13
#define LATCH_PIN  12

#define NUM_CHANNELS 63
#define NUM_REGISTERS 8

#define ACTIVE_LOW true // Set to true if 0=ON and 1=OFF for your hardware

uint8_t shiftData[NUM_REGISTERS];

bool continuousRunning = false;
bool stopRequested = false;


// =====================================================
// SHIFT REGISTER UPDATE
// =====================================================

void updateShiftRegisters()
{
  digitalWrite(LATCH_PIN, LOW);

  // Last register first
  for (int i = NUM_REGISTERS - 1; i >= 0; i--)
  {
    shiftOut(
      DATA_PIN,
      CLOCK_PIN,
      MSBFIRST,
      shiftData[i]
    );
  }

  digitalWrite(LATCH_PIN, HIGH);
}


// =====================================================
// ALL OUTPUT OFF
// =====================================================

void allOff()
{
  for (int i = 0; i < NUM_REGISTERS; i++)
  {
    shiftData[i] = ACTIVE_LOW ? 255 : 0;
  }

  updateShiftRegisters();
}


// =====================================================
// SET CHANNEL
// channel = 1 ... 63
// =====================================================

void setChannel(int channel, bool state)
{
  if (channel < 1 || channel > NUM_CHANNELS)
    return;

  int index = channel - 1;

  int regIndex = index / 8;
  int bitIndex = index % 8;

  bool hardwareState = ACTIVE_LOW ? !state : state;

  if (hardwareState)
  {
    shiftData[regIndex] |=
      (1 << bitIndex);
  }
  else
  {
    shiftData[regIndex] &=
      ~(1 << bitIndex);
  }

  updateShiftRegisters();
}


// =====================================================
// SINGLE FIRE
// =====================================================

void fireChannel(
  int channel,
  unsigned long onTime
)
{
  if (channel < 1 || channel > NUM_CHANNELS)
    return;

  allOff();

  setChannel(
    channel,
    true
  );

  delay(onTime);

  setChannel(
    channel,
    false
  );

  Serial.print("FIRED,");
  Serial.print(channel);
  Serial.print(",");
  Serial.println(onTime);
}


// =====================================================
// CONTINUOUS FIRE
//
// channel
// onTime
// gapTime
// startDelay
// =====================================================

void continuousFire(
  int channel,
  unsigned long onTime,
  unsigned long gapTime,
  unsigned long startDelay
)
{
  if (channel < 1 || channel > NUM_CHANNELS)
    return;

  if (startDelay > 0)
  {
    delay(startDelay);
  }

  continuousRunning = true;
  stopRequested = false;

  Serial.print("CONT_STARTED,");
  Serial.println(channel);

  while (continuousRunning)
  {
    // Check STOP command before pulse
    checkSerialDuringRun();

    if (stopRequested)
      break;

    // ON
    setChannel(
      channel,
      true
    );

    delay(onTime);

    // OFF
    setChannel(
      channel,
      false
    );

    if (stopRequested)
      break;

    // GAP
    unsigned long gapStart =
      millis();

    while (
      millis() - gapStart < gapTime
    )
    {
      checkSerialDuringRun();

      if (stopRequested)
        break;

      delay(1);
    }
  }

  allOff();

  continuousRunning = false;
  stopRequested = false;

  Serial.println("CONT_STOPPED");
}


// =====================================================
// SERIAL CHECK DURING CONTINUOUS MODE
// =====================================================

void checkSerialDuringRun()
{
  if (!Serial.available())
    return;

  String cmd =
    Serial.readStringUntil('\n');

  cmd.trim();

  if (cmd == "CONT_STOP")
  {
    stopRequested = true;
    continuousRunning = false;

    allOff();

    Serial.println("STOPPED");
  }

  else if (cmd == "STOP")
  {
    stopRequested = true;
    continuousRunning = false;

    allOff();

    Serial.println("STOPPED");
  }

  else if (cmd == "ALL_OFF")
  {
    stopRequested = true;
    continuousRunning = false;

    allOff();

    Serial.println("ALL_OFF_OK");
  }
}


// =====================================================
// AUTO 1 -> 63
//
// startChannel
// endChannel
// onTime
// gapTime
// =====================================================

void autoSequence(
  int startChannel,
  int endChannel,
  unsigned long onTime,
  unsigned long gapTime
)
{
  if (startChannel < 1)
    startChannel = 1;

  if (endChannel > NUM_CHANNELS)
    endChannel = NUM_CHANNELS;

  if (startChannel > endChannel)
  {
    Serial.println("AUTO_ERROR");
    return;
  }

  Serial.println("AUTO_STARTED");

  stopRequested = false;

  for (
    int ch = startChannel;
    ch <= endChannel;
    ch++
  )
  {
    if (stopRequested)
      break;

    Serial.print("AUTO_CHANNEL,");
    Serial.println(ch);

    fireChannel(
      ch,
      onTime
    );

    if (gapTime > 0)
    {
      delay(gapTime);
    }

    if (Serial.available())
    {
      String cmd =
        Serial.readStringUntil('\n');

      cmd.trim();

      if (
        cmd == "STOP" ||
        cmd == "ALL_OFF"
      )
      {
        stopRequested = true;
        break;
      }
    }
  }

  allOff();

  stopRequested = false;

  Serial.println("AUTO_FINISHED");
}


// =====================================================
// COMMAND PROCESSOR
// =====================================================

void processCommand(String cmd)
{
  cmd.trim();

  if (cmd.length() == 0)
    return;


  // -----------------------------------------------
  // STATUS
  // -----------------------------------------------

  if (cmd == "STATUS")
  {
    Serial.println("ESP32_READY");
    return;
  }


  // -----------------------------------------------
  // ALL OFF
  // -----------------------------------------------

  if (cmd == "ALL_OFF")
  {
    continuousRunning = false;
    stopRequested = true;

    allOff();

    Serial.println("ALL_OFF_OK");

    return;
  }


  // -----------------------------------------------
  // STOP
  // -----------------------------------------------

  if (cmd == "STOP")
  {
    continuousRunning = false;
    stopRequested = true;

    allOff();

    Serial.println("STOPPED");

    return;
  }


  // -----------------------------------------------
  // ON,CHANNEL
  // -----------------------------------------------

  if (cmd.startsWith("ON,"))
  {
    int channel =
      cmd.substring(3).toInt();

    setChannel(
      channel,
      true
    );

    Serial.print("ON_OK,");
    Serial.println(channel);

    return;
  }


  // -----------------------------------------------
  // OFF,CHANNEL
  // -----------------------------------------------

  if (cmd.startsWith("OFF,"))
  {
    int channel =
      cmd.substring(4).toInt();

    setChannel(
      channel,
      false
    );

    Serial.print("OFF_OK,");
    Serial.println(channel);

    return;
  }


  // -----------------------------------------------
  // FIRE,CHANNEL,ON_TIME
  // -----------------------------------------------

  if (cmd.startsWith("FIRE,"))
  {
    int p1 =
      cmd.indexOf(',');

    int p2 =
      cmd.indexOf(
        ',',
        p1 + 1
      );

    if (p1 > 0 && p2 > 0)
    {
      int channel =
        cmd.substring(
          p1 + 1,
          p2
        ).toInt();

      unsigned long onTime =
        cmd.substring(
          p2 + 1
        ).toInt();

      fireChannel(
        channel,
        onTime
      );
    }

    return;
  }


  // -----------------------------------------------
  // CONT_START,CHANNEL,ON,GAP,DELAY
  // -----------------------------------------------

  if (cmd.startsWith("CONT_START,"))
  {
    int p1 =
      cmd.indexOf(',');

    int p2 =
      cmd.indexOf(',', p1 + 1);

    int p3 =
      cmd.indexOf(',', p2 + 1);

    int p4 =
      cmd.indexOf(',', p3 + 1);

    if (
      p1 > 0 &&
      p2 > 0 &&
      p3 > 0 &&
      p4 > 0
    )
    {
      int channel =
        cmd.substring(
          p1 + 1,
          p2
        ).toInt();

      unsigned long onTime =
        cmd.substring(
          p2 + 1,
          p3
        ).toInt();

      unsigned long gapTime =
        cmd.substring(
          p3 + 1,
          p4
        ).toInt();

      unsigned long startDelay =
        cmd.substring(
          p4 + 1
        ).toInt();

      continuousFire(
        channel,
        onTime,
        gapTime,
        startDelay
      );
    }

    return;
  }


  // -----------------------------------------------
  // CONT_STOP
  // -----------------------------------------------

  if (cmd == "CONT_STOP")
  {
    continuousRunning = false;
    stopRequested = true;

    allOff();

    Serial.println("CONT_STOPPED");

    return;
  }


  // -----------------------------------------------
  // AUTO,START,END,ON,GAP
  // -----------------------------------------------

  if (cmd.startsWith("AUTO,"))
  {
    int p1 =
      cmd.indexOf(',');

    int p2 =
      cmd.indexOf(',', p1 + 1);

    int p3 =
      cmd.indexOf(',', p2 + 1);

    int p4 =
      cmd.indexOf(',', p3 + 1);

    if (
      p1 > 0 &&
      p2 > 0 &&
      p3 > 0 &&
      p4 > 0
    )
    {
      int startChannel =
        cmd.substring(
          p1 + 1,
          p2
        ).toInt();

      int endChannel =
        cmd.substring(
          p2 + 1,
          p3
        ).toInt();

      unsigned long onTime =
        cmd.substring(
          p3 + 1,
          p4
        ).toInt();

      unsigned long gapTime =
        cmd.substring(
          p4 + 1
        ).toInt();

      autoSequence(
        startChannel,
        endChannel,
        onTime,
        gapTime
      );
    }

    return;
  }


  Serial.println("UNKNOWN_COMMAND");
}


// =====================================================
// SETUP
// =====================================================

void setup()
{
  pinMode(
    DATA_PIN,
    OUTPUT
  );

  pinMode(
    CLOCK_PIN,
    OUTPUT
  );

  pinMode(
    LATCH_PIN,
    OUTPUT
  );

  digitalWrite(
    DATA_PIN,
    LOW
  );

  digitalWrite(
    CLOCK_PIN,
    LOW
  );

  digitalWrite(
    LATCH_PIN,
    LOW
  );

  Serial.begin(115200);

  delay(500);

  allOff();

  Serial.println(
    "TCM_63CH_ESP32_READY"
  );
}


// =====================================================
// LOOP
// =====================================================

void loop()
{
  if (Serial.available())
  {
    String cmd =
      Serial.readStringUntil('\n');

    processCommand(cmd);
  }
}
