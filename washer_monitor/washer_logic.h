#pragma once
#include <Arduino.h>

enum WasherState {
  IDLE_STATE,
  RUNNING_STATE
};

void initWasherLogic();
void updateWasherState(float motionScore);
WasherState getWasherState();
const char* washerStateName();