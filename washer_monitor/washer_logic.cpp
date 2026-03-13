#include "washer_logic.h"

#include "config.h"
#include "telegram_manager.h"

static WasherState washerState = IDLE_STATE;
static unsigned long transitionStartMs = 0;
static unsigned long cycleStartMs = 0;

void initWasherLogic() {
  washerState = IDLE_STATE;
  transitionStartMs = 0;
  cycleStartMs = 0;
}

void updateWasherState(float motionScore) {
  unsigned long now = millis();

  if (washerState == IDLE_STATE) {
    if (motionScore >= START_THRESHOLD) {
      if (transitionStartMs == 0) {
        transitionStartMs = now;
      } else if (now - transitionStartMs >= START_CONFIRM_MS) {
        washerState = RUNNING_STATE;
        cycleStartMs = now;
        transitionStartMs = 0;

        Serial.println("[STATE] RUNNING");
        if (SEND_START_ALERT) {
          queueTelegram(String(DEVICE_NAME) + ": washer started.");
        }
      }
    } else {
      transitionStartMs = 0;
    }
  } else {
    if (motionScore <= STOP_THRESHOLD) {
      if (transitionStartMs == 0) {
        transitionStartMs = now;
      } else if (now - transitionStartMs >= STOP_CONFIRM_MS) {
        washerState = IDLE_STATE;
        transitionStartMs = 0;

        unsigned long runtimeMin = (now - cycleStartMs) / 60000UL;

        Serial.println("[STATE] DONE / IDLE");
        queueTelegram(
          String(DEVICE_NAME) +
          ": washer appears DONE. Approx runtime = " +
          String(runtimeMin) + " min."
        );
      }
    } else {
      transitionStartMs = 0;
    }
  }
}

WasherState getWasherState() {
  return washerState;
}

const char* washerStateName() {
  return (washerState == RUNNING_STATE) ? "RUNNING" : "IDLE";
}