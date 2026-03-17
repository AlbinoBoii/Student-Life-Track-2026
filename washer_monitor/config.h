#pragma once
#include <Arduino.h>

// =========================
// DEVICE / PINS
// =========================
constexpr char DEVICE_NAME[] = "ESP32 Washer Monitor";
constexpr int SDA_PIN = 21;
constexpr int SCL_PIN = 22;

// =========================
// DETECTION TUNING
// =========================
constexpr unsigned long SAMPLE_INTERVAL_MS = 100;   // 10 Hz
constexpr float EMA_ALPHA                  = 0.18f;

constexpr float START_THRESHOLD            = 250.0f;
constexpr float STOP_THRESHOLD             = 250.0f;

constexpr unsigned long START_CONFIRM_MS   = 15000UL; // 15s
constexpr unsigned long STOP_CONFIRM_MS    = 15000UL;  // 15s

constexpr bool SEND_START_ALERT            = true;

// =========================
// RETRY / STATUS
// =========================
constexpr unsigned long WIFI_RETRY_MS      = 10000UL;
constexpr unsigned long TELEGRAM_RETRY_MS  = 15000UL;
constexpr unsigned long STATUS_PRINT_MS    = 1000UL;


// =========================
// SERIAL LOGGING
// =========================
constexpr bool SERIAL_LOG_ENABLED         = true;
constexpr unsigned long SERIAL_LOG_MS     = 100;   // log every 100 ms

// =========================
// AZURE (data sink)
// =========================
constexpr bool   AZURE_ENABLED         = true;  // azure kill switch
constexpr int    AZURE_BATCH_SIZE      = 25;     // samples per POST batch
constexpr unsigned long AZURE_FLUSH_MS = 2500UL; // max time between POSTs