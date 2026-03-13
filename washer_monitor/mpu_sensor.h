#pragma once
#include <Arduino.h>

struct SensorSample {
  unsigned long esp_ms;
  int16_t ax;
  int16_t ay;
  int16_t az;
  float motion;
};

void setupMPU();
void sampleMotion();
float getMotionEma();
SensorSample getLatestSample();