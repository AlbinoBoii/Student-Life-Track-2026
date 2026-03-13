#pragma once
#include <Arduino.h>

bool connectWiFi(unsigned long timeoutMs);
void ensureWiFi();
bool isWiFiConnected();