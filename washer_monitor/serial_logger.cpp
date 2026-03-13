#include "serial_logger.h"

#include "config.h"
#include "mpu_sensor.h"
#include "washer_logic.h"

static unsigned long lastSerialLogMs = 0;

void serialLoggerBegin() {
  if (!SERIAL_LOG_ENABLED) return;

  Serial.println("CSV_HEADER,esp_ms,ax,ay,az,motion,state");
}

void serialLoggerLogSample() {
  if (!SERIAL_LOG_ENABLED) return;

  unsigned long now = millis();
  if (now - lastSerialLogMs < SERIAL_LOG_MS) return;
  lastSerialLogMs = now;

  SensorSample s = getLatestSample();

  Serial.print("DATA,");
  Serial.print(s.esp_ms);
  Serial.print(",");
  Serial.print(s.ax);
  Serial.print(",");
  Serial.print(s.ay);
  Serial.print(",");
  Serial.print(s.az);
  Serial.print(",");
  Serial.print(s.motion, 2);
  Serial.print(",");
  Serial.println(washerStateName());
}