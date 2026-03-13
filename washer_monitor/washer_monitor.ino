#include <WiFi.h>

#include "config.h"
#include "wifi_manager.h"
#include "telegram_manager.h"
#include "mpu_sensor.h"
#include "washer_logic.h"
#include "serial_logger.h"

static unsigned long lastSampleMs = 0;
static unsigned long lastStatusMs = 0;

void printStatus() {
  Serial.print("[STATUS] wifi=");
  Serial.print(isWiFiConnected() ? "UP" : "DOWN");
  Serial.print(" ip=");

  if (isWiFiConnected()) {
    Serial.print(WiFi.localIP());
  } else {
    Serial.print("no-ip");
  }

  Serial.print(" motion=");
  Serial.print(getMotionEma(), 2);
  Serial.print(" state=");
  Serial.print(washerStateName());
  Serial.print(" pendingTelegram=");
  Serial.println(hasPendingTelegram() ? "YES" : "NO");
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("Booting...");

  setupMPU();
  initWasherLogic();
  serialLoggerBegin();

  connectWiFi(30000);
  queueTelegram(String(DEVICE_NAME) + ": boot complete.");
}

void loop() {
  unsigned long now = millis();

  ensureWiFi();
  processPendingTelegram();

  if (now - lastSampleMs >= SAMPLE_INTERVAL_MS) {
    lastSampleMs = now;
    sampleMotion();
    serialLoggerLogSample();
  }

  if (now - lastStatusMs >= STATUS_PRINT_MS) {
    lastStatusMs = now;
    printStatus();
  }
}