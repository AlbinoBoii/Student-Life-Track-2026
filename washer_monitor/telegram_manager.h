#pragma once
#include <Arduino.h>

void queueTelegram(const String& text);
void processPendingTelegram();
bool hasPendingTelegram();