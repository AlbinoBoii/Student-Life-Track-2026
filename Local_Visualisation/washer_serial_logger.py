import csv
import serial
import time
from datetime import datetime
from collections import deque

import matplotlib.pyplot as plt

PORT = "/dev/cu.usbserial-0001"
BAUD = 115200
MAX_POINTS = 300

filename = f"washer_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

ser = serial.Serial(PORT, BAUD, timeout=1)

motion_data = deque(maxlen=MAX_POINTS)
state_data = deque(maxlen=MAX_POINTS)
time_data = deque(maxlen=MAX_POINTS)

plt.ion()
fig, ax = plt.subplots()

header_written = False
sample_count = 0

print(f"Logging to {filename}")
print("Press Ctrl+C to stop")

with open(filename, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["laptop_time_iso", "esp_ms", "ax", "ay", "az", "motion", "state"])

    try:
        while True:
            raw = ser.readline().decode(errors="ignore").strip()
            if not raw:
                continue

            if raw.startswith("["):
                print(raw)
                continue

            if raw.startswith("CSV_HEADER"):
                print(raw)
                continue

            if not raw.startswith("DATA,"):
                print(raw)
                continue

            parts = raw.split(",")
            if len(parts) != 7:
                continue

            _, esp_ms, ax_val, ay_val, az_val, motion, state = parts

            now_iso = datetime.now().isoformat()
            writer.writerow([now_iso, esp_ms, ax_val, ay_val, az_val, motion, state])
            f.flush()

            esp_ms = int(esp_ms)
            motion = float(motion)

            motion_data.append(motion)
            state_data.append(state)
            time_data.append(esp_ms)
            sample_count += 1

            if sample_count % 5 == 0:
                ax.clear()
                ax.plot(list(time_data), list(motion_data))
                ax.set_title(f"Live Motion Score | Latest state: {state}")
                ax.set_xlabel("ESP32 millis()")
                ax.set_ylabel("Motion")
                plt.pause(0.001)

            if sample_count % 20 == 0:
                latest_state = state_data[-1]
                latest_motion = motion_data[-1]

                if latest_state == "RUNNING":
                    interpretation = "Machine likely active."
                else:
                    interpretation = "Machine likely idle or finished."

                print(f"[INTERPRET] motion={latest_motion:.2f} state={latest_state} -> {interpretation}")

    except KeyboardInterrupt:
        print("\nStopped logging.")
    finally:
        ser.close()