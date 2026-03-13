#include "mpu_sensor.h"

#include <Wire.h>
#include <MPU6050.h>
#include <math.h>

#include "config.h"
#include "washer_logic.h"

static MPU6050 mpu;
static float motionEma = 0.0f;
static float lastMagnitude = 0.0f;
static bool firstSample = true;

static SensorSample latestSample = {0, 0, 0, 0, 0.0f};

void setupMPU() {
  Wire.begin(SDA_PIN, SCL_PIN);

  Serial.println("[MPU6050] Initializing...");
  mpu.initialize();

  if (mpu.testConnection()) {
    Serial.println("[MPU6050] Connected!");
  } else {
    Serial.println("[MPU6050] Connection failed");
  }
}

void sampleMotion() {
  int16_t ax, ay, az;
  mpu.getAcceleration(&ax, &ay, &az);

  float magnitude = sqrtf(
    (float)ax * (float)ax +
    (float)ay * (float)ay +
    (float)az * (float)az
  );

  if (firstSample) {
    lastMagnitude = magnitude;
    firstSample = false;
  } else {
    float delta = fabsf(magnitude - lastMagnitude);
    lastMagnitude = magnitude;
    motionEma = EMA_ALPHA * delta + (1.0f - EMA_ALPHA) * motionEma;
    updateWasherState(motionEma);
  }

  latestSample.esp_ms = millis();
  latestSample.ax = ax;
  latestSample.ay = ay;
  latestSample.az = az;
  latestSample.motion = motionEma;
}

float getMotionEma() {
  return motionEma;
}

SensorSample getLatestSample() {
  return latestSample;
}