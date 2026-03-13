#include "telegram_manager.h"

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

#include "config.h"
#include "secrets.h"

static bool pendingTelegram = false;
static String pendingTelegramText = "";
static unsigned long lastTelegramTryMs = 0;

static String urlEncode(const String& input) {
  const char* hex = "0123456789ABCDEF";
  String out;
  out.reserve(input.length() * 3);

  for (size_t i = 0; i < input.length(); i++) {
    uint8_t c = static_cast<uint8_t>(input[i]);

    bool safe =
      (c >= 'a' && c <= 'z') ||
      (c >= 'A' && c <= 'Z') ||
      (c >= '0' && c <= '9') ||
      c == '-' || c == '_' || c == '.' || c == '~';

    if (safe) {
      out += char(c);
    } else {
      out += '%';
      out += hex[(c >> 4) & 0x0F];
      out += hex[c & 0x0F];
    }
  }

  return out;
}

static bool sendTelegram(const String& text) {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }

  WiFiClientSecure client;
  client.setInsecure();

  HTTPClient http;
  http.setConnectTimeout(10000);
  http.setTimeout(10000);

  String url = "https://api.telegram.org/bot" + String(BOT_TOKEN) + "/sendMessage";
  String body = "chat_id=" + urlEncode(String(CHAT_ID)) +
                "&text=" + urlEncode(text);

  if (!http.begin(client, url)) {
    Serial.println("[Telegram] begin failed");
    return false;
  }

  http.addHeader("Content-Type", "application/x-www-form-urlencoded");
  int code = http.POST(body);
  String payload = http.getString();
  http.end();

  Serial.print("[Telegram] HTTP code: ");
  Serial.println(code);

  if (payload.length() > 0) {
    Serial.println(payload);
  }

  return code == 200;
}

void queueTelegram(const String& text) {
  pendingTelegram = true;
  pendingTelegramText = text;
  lastTelegramTryMs = 0;
}

void processPendingTelegram() {
  if (!pendingTelegram) return;
  if (WiFi.status() != WL_CONNECTED) return;

  unsigned long now = millis();
  if (lastTelegramTryMs != 0 && now - lastTelegramTryMs < TELEGRAM_RETRY_MS) {
    return;
  }

  lastTelegramTryMs = now;

  if (sendTelegram(pendingTelegramText)) {
    Serial.println("[Telegram] sent");
    pendingTelegram = false;
    pendingTelegramText = "";
  } else {
    Serial.println("[Telegram] failed, will retry");
  }
}

bool hasPendingTelegram() {
  return pendingTelegram;
}