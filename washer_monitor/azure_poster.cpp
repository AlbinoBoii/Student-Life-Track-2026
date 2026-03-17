/*
 * azure_poster.cpp – Optional Azure data-sink module
 *
 * Buffers accelerometer samples and POSTs them in JSON batches to an
 * Azure Function endpoint.  Completely optional: controlled by
 * AZURE_ENABLED in config.h.  If the POST fails the buffer is
 * silently discarded – Telegram alerts are unaffected.
 */

#include "azure_poster.h"

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

#include "config.h"
#include "secrets.h"
#include "mpu_sensor.h"
#include "washer_logic.h"

// ---- internal state ----
static unsigned long lastFlushMs = 0;
static int           bufCount   = 0;
static unsigned long seqCounter = 0;

// Simple ring-buffer for raw samples (fixed max size).
struct AzureSample {
  unsigned long ts_ms;
  int16_t ax, ay, az;
  float   motion;
  const char* state;
  int     rssi;
};

static AzureSample sampleBuf[60]; // room for 2× AZURE_BATCH_SIZE

// ---- boot-id (random on each boot) ----
static char bootId[13]; // 12 hex chars + NUL

static void generateBootId() {
  const char hex[] = "0123456789abcdef";
  for (int i = 0; i < 12; i++) {
    bootId[i] = hex[random(16)];
  }
  bootId[12] = '\0';
}

// ---- public API ----

void initAzurePoster() {
  if (!AZURE_ENABLED) return;
  generateBootId();
  bufCount   = 0;
  seqCounter = 0;
  lastFlushMs = millis();

  Serial.print("[Azure] Poster enabled. boot_id=");
  Serial.println(bootId);
}

void bufferSampleForAzure() {
  if (!AZURE_ENABLED) return;
  if (bufCount >= AZURE_BATCH_SIZE) return; // buffer full, wait for flush

  SensorSample s = getLatestSample();

  sampleBuf[bufCount].ts_ms  = s.esp_ms;
  sampleBuf[bufCount].ax     = s.ax;
  sampleBuf[bufCount].ay     = s.ay;
  sampleBuf[bufCount].az     = s.az;
  sampleBuf[bufCount].motion = s.motion;
  sampleBuf[bufCount].state  = washerStateName();
  sampleBuf[bufCount].rssi   = (WiFi.status() == WL_CONNECTED) ? WiFi.RSSI() : 0;

  bufCount++;
}

void flushAzureBatch() {
  if (!AZURE_ENABLED) return;
  if (bufCount == 0) return;

  unsigned long now = millis();

  // Only flush when batch is full OR timer has elapsed
  bool batchFull   = (bufCount >= AZURE_BATCH_SIZE);
  bool timerElapsed = (now - lastFlushMs >= AZURE_FLUSH_MS);

  if (!batchFull && !timerElapsed) return;

  lastFlushMs = now;

  // Need WiFi
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[Azure] No WiFi, discarding batch");
    bufCount = 0;
    return;
  }

  // Build JSON
  String json = "{\"device_id\":\"";
  json += DEVICE_NAME;
  json += "\",\"boot_id\":\"";
  json += bootId;
  json += "\",\"seq_no\":";
  json += String(seqCounter);
  json += ",\"samples\":[";

  for (int i = 0; i < bufCount; i++) {
    if (i > 0) json += ",";
    json += "{\"ts_ms\":";
    json += String(sampleBuf[i].ts_ms);
    json += ",\"ax\":";
    json += String(sampleBuf[i].ax);
    json += ",\"ay\":";
    json += String(sampleBuf[i].ay);
    json += ",\"az\":";
    json += String(sampleBuf[i].az);
    json += ",\"motion_score\":";
    json += String(sampleBuf[i].motion, 2);
    json += ",\"state\":\"";
    json += sampleBuf[i].state;
    json += "\",\"wifi_rssi_dbm\":";
    json += String(sampleBuf[i].rssi);
    json += "}";
  }

  json += "]}";

  int samplesSent = bufCount;
  bufCount = 0;
  seqCounter++;

  // POST to Azure Function
  WiFiClientSecure client;
  client.setInsecure(); // Azure Functions use valid certs, but ESP32 cert store is limited

  HTTPClient http;
  http.setConnectTimeout(8000);
  http.setTimeout(8000);

  String url = String(AZURE_FUNCTION_URL);

  if (!http.begin(client, url)) {
    Serial.println("[Azure] HTTP begin failed");
    return;
  }

  http.addHeader("Content-Type", "application/json");
  http.addHeader("x-api-key", AZURE_API_KEY);

  int code = http.POST(json);
  http.end();

  Serial.print("[Azure] POST ");
  Serial.print(samplesSent);
  Serial.print(" samples → HTTP ");
  Serial.println(code);
}
