// Lidar speed control for the STL27L, on an ESP8266 (ESP-12F).
//
// The lidar spins at 10 Hz on its own. This board drives its PWM wire so it
// spins at TARGET_HZ instead (faster spin = more looks at a fast ball). It
// reads the speed in the lidar's own data packets (the data wire goes to the
// PC adapter AND to this board) and adjusts the PWM duty until the speed is
// right (PI loop), as the STL27L manual asks for.
//
// Wiring (lidar 4-pin cable: 1 Tx, 2 PWM, 3 GND, 4 P5V):
//   lidar Tx   -> PC USB-serial RX (as now)  AND  ESP GPIO13 (D7)
//   lidar PWM  -> ESP GPIO4 (D2)             (take it off GND first)
//   lidar GND  -> PC adapter GND             AND  ESP GND
//   lidar P5V  -> 5 V (as now)
//
// LED on = speed locked within 0.2 Hz. LED off = adjusting, or no data seen.

#include <ESP8266WiFi.h>

const float TARGET_HZ = 13.0;       // spin speed; factory default 10, datasheet max about 13

const int PWM_PIN = 4;              // GPIO4 = D2 -> lidar PWM
const int LED_PIN = 2;              // on-board LED, active low
const uint32_t PWM_FREQ = 30000;    // manual: 20-50 kHz, 30 kHz recommended
const int RANGE = 1000;             // duty steps (0.1 %)
const int START_DUTY = 500;         // 50 % for > 100 ms switches the lidar to external speed control
const int MIN_DUTY = 200;
const int MAX_DUTY = 1000;
const float KP = 20.0;              // duty steps per Hz of error
const float KI = 50.0;              // duty steps per Hz of error, per second
const uint32_t STEP_MS = 100;       // control step

const int PACKET_SIZE = 47;         // header 0x54, 0x2C, speed (deg/s, 2 bytes), ..., crc8

uint8_t crcTable[256];
uint8_t packet[PACKET_SIZE];
int fill = 0;

float speedSum = 0;                 // deg/s, summed over the packets of one control step
int speedCount = 0;
float integral = START_DUTY;
uint32_t lastStep = 0;


void readLidar() {

  while (Serial.available()) {

    uint8_t b = Serial.read();

    // wait for the 0x54 0x2C header
    if (fill == 0 && b != 0x54) continue;
    if (fill == 1 && b != 0x2C) {
      fill = (b == 0x54) ? 1 : 0;
      continue;
    }

    packet[fill++] = b;

    if (fill < PACKET_SIZE) continue;
    fill = 0;

    uint8_t crc = 0;
    for (int i = 0; i < PACKET_SIZE - 1; i++) crc = crcTable[crc ^ packet[i]];

    if (crc == packet[PACKET_SIZE - 1]) {
      speedSum += packet[2] | (packet[3] << 8);
      speedCount++;
    }
  }
}


void setup() {

  WiFi.mode(WIFI_OFF);              // radio off: less power, nothing interrupts the serial reading
  WiFi.forceSleepBegin();

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);

  // same crc8 as lib.py (polynomial 0x4D)
  for (int i = 0; i < 256; i++) {
    uint8_t c = i;
    for (int k = 0; k < 8; k++) c = (c & 0x80) ? (c << 1) ^ 0x4D : c << 1;
    crcTable[i] = c;
  }

  analogWriteFreq(PWM_FREQ);
  analogWriteRange(RANGE);
  analogWrite(PWM_PIN, START_DUTY);

  Serial.setRxBufferSize(1024);
  Serial.begin(921600);
  Serial.swap();                    // RX on GPIO13 (D7); GPIO3 stays free for flashing

  delay(300);                       // hold 50 % so the lidar switches to external control
  while (Serial.available()) Serial.read();

  lastStep = millis();
}


void loop() {

  readLidar();

  uint32_t now = millis();
  if (now - lastStep < STEP_MS) return;

  float dt = (now - lastStep) / 1000.0;
  lastStep = now;

  // no data (wire loose): keep the duty as it is
  if (speedCount == 0) {
    digitalWrite(LED_PIN, HIGH);
    return;
  }

  float hz = speedSum / speedCount / 360.0;
  speedSum = 0;
  speedCount = 0;

  float error = TARGET_HZ - hz;
  integral = constrain(integral + KI * error * dt, (float)MIN_DUTY, (float)MAX_DUTY);
  int duty = constrain((int)(integral + KP * error), MIN_DUTY, MAX_DUTY);

  analogWrite(PWM_PIN, duty);
  digitalWrite(LED_PIN, fabs(error) < 0.2 ? LOW : HIGH);
}
