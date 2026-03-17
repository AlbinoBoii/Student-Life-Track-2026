#include "wifi_manager.h"

#include <WiFi.h>
#include <time.h>
#include "config.h"
#include "secrets.h"

static unsigned long lastWiFiTryMs = 0;

bool connectWiFi(unsigned long timeoutMs) {
  Serial.print("[WiFi] Connecting to ");
  Serial.println(WIFI_SSID);

  WiFi.disconnect(true);
  WiFi.mode(WIFI_STA);

  WiFi.begin(
    WIFI_SSID,
    WPA2_AUTH_PEAP,
    EAP_IDENTITY,
    EAP_USERNAME,
    EAP_PASSWORD
  );

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < timeoutMs) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[WiFi] Connected. IP: ");
    Serial.println(WiFi.localIP());
    
    // Sync time via NTP
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    Serial.println("[WiFi] NTP time sync started.");
    
    return true;
  } else {
    Serial.print("[WiFi] Failed. Status: ");
    Serial.println(WiFi.status());
    return false;
  }
}

void ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  unsigned long now = millis();
  if (now - lastWiFiTryMs >= WIFI_RETRY_MS) {
    lastWiFiTryMs = now;
    connectWiFi(15000);
  }
}

bool isWiFiConnected() {
  return WiFi.status() == WL_CONNECTED;
}