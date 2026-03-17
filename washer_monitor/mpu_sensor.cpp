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

// 10-second rolling average (100 samples at 10Hz)
#define MOTION_HIST_LEN 100
static float motionHistory[MOTION_HIST_LEN];
static int histIdx = 0;
static int histCount = 0;
static float motionSum = 0.0f;
static float motionAvg10s = 0.0f;

static SensorSample latestSample = {0, 0, 0, 0, 0.0f, 0.0f};

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
    
    // Update 10-second moving average
    if (histCount == MOTION_HIST_LEN) {
      motionSum -= motionHistory[histIdx];
    } else {
      histCount++;
    }
    motionHistory[histIdx] = motionEma;
    motionSum += motionEma;
    histIdx = (histIdx + 1) % MOTION_HIST_LEN;
    
    motionAvg10s = motionSum / (float)histCount;

    // Use moving average for state detection!
    updateWasherState(motionAvg10s);
  }

  latestSample.esp_ms = millis();
  latestSample.ax = ax;
  latestSample.ay = ay;
  latestSample.az = az;
  latestSample.motion = motionEma;
  latestSample.motion_avg = motionAvg10s;
}

float getMotionEma() {
  return motionEma;
}

SensorSample getLatestSample() {
  return latestSample;
}