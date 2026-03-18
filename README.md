# 🧺 Dra-Washer Monitor – Smart Washing Machine Cycle Detection

A complete end-to-end IoT solution that transforms your **ordinary washing machine** into a smart appliance. Using an ESP32 microcontroller and vibration sensors, the system detects wash cycles in real-time and provides a powerful dashboard for monitoring and ML-based cycle classification.

**Perfect for**: Dorm laundry tracking, shared housing, smart home enthusiasts, IoT/ML learning projects.

---

## 🚀 Quick Start (5 Minutes)

### 1. **Access the Dashboard**
- Navigate to: `https://<your-azure-function-url>/api/dashboard`
- You'll see live streaming data from your ESP32

### 2. **Check Device Status**
- Look at the top-right corner for the **Online/Offline indicator**
- 🟢 Green = Device connected and sending data
- 🔴 Red = No data for 10+ minutes

### 3. **Explore Three Tabs**
- **Live**: Real-time washing machine vibration (refreshes every 10 seconds)
- **Historical**: Browse past cycles with date/time filters
- **ML Training**: Label historical data to train custom models

---

## 📋 Table of Contents

- [What is the Dra-Washer Monitor?](#what-is-the-dra-washer-monitor)
- [System Architecture](#system-architecture)
- [Getting Started](#getting-started)
- [Using the Dashboard](#using-the-dashboard)
- [ML Training Pipeline](#ml-training-pipeline)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

---

## 💡 What is the Dra-Washer Monitor?

The **Dra-Washer Monitor** is a four-part system:

1. **Edge Device (ESP32)**: Collects vibration data via an accelerometer attached to the washing machine
2. **Cloud Backend (Azure)**: Stores data securely and provides a REST API
3. **Web Dashboard**: Real-time monitoring, historical analysis, and **interactive ML training**
4. **ML Pipeline**: Train custom models on your washing machine's unique vibration signature and deploy them back to the ESP32

### Why This Matters
- 🎯 **Solves a real problem**: Know when your laundry is done without constant checking
- 🔬 **Learn full-stack IoT**: Signal processing, cloud architecture, ML, and embedded systems
- 📊 **Your data, your model**: Train on your specific washing machine, not cloud-based generic models
- 💰 **Ultra-affordable**: Using existing hardware; only Azure Table Storage costs pennies per month

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        YOUR WASHING MACHINE                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  ESP32 + MPU6050 Accelerometer (10Hz sampling)          │   │
│  │  • Raw acceleration (ax, ay, az)                         │   │
│  │  • Compute motion_score & motion_avg                     │   │
│  │  • State machine (IDLE/RUNNING/SPINDRY)                  │   │
│  └──────────────┬───────────────────────────────────────────┘   │
└─────────────────┼──────────────────────────────────────────────┘
                  │ HTTP POST (every 2.5 sec during cycles)
                  │ Batched sensor data + device_id
                  ▼
        ┌─────────────────────┐
        │  Azure Functions    │
        │  /api/ingest → POST │
        │  /api/samples → GET │
        │  /api/label → PATCH │
        │  /api/dashboard→GET │
        └──────────┬──────────┘
                   │
        ┌──────────▼──────────┐
        │ Azure Table Storage │
        │   (washersensordata)│
        │  Stores all telemetry
        │  & user labels      │
        └──────────┬──────────┘
                   │
        ┌──────────▼──────────────────┐
        │   Your Web Browser (or app) │
        │                             │
        │  📊 Live Tab                │
        │    - Real-time charts       │
        │    - Device status          │
        │                             │
        │  📈 Historical Tab          │
        │    - Date range filters     │
        │    - Motion analysis        │
        │                             │
        │  🤖 ML Training Tab         │
        │    - Label cycles           │
        │    - Export CSV             │
        └──────────┬──────────────────┘
                   │
        ┌──────────▼──────────────────┐
        │   Local ML Training         │
        │   (train_model.py)          │
        │                             │
        │  1. Load labeled CSV        │
        │  2. Train Random Forest     │
        │  3. Evaluate metrics        │
        │  4. Generate C++ header     │
        └──────────┬──────────────────┘
                   │
        ┌──────────▼──────────────────┐
        │  Deploy to ESP32            │
        │  washer_model.h             │
        │  ↓                          │
        │  Custom cycle detection!    │
        └─────────────────────────────┘
```

---

## 🛠️ Getting Started

### Prerequisites
- **ESP32 Development Board** (any variant)
- **MPU6050 Accelerometer** module
- **Micro USB cable** for ESP32 programming
- **WiFi network** with internet access
- **Azure account** (for cloud backend)
- **Python 3.8+** (for ML training)

### Step 1: Deploy Azure Backend

1. **Clone this repository**
   ```bash
   git clone https://github.com/yourusername/Student-Life-Track-2026.git
   cd Student-Life-Track-2026
   ```

2. **Deploy Azure Functions** (`Azure/function_app.py`)
   - Use Azure CLI or Azure Portal
   - Create a storage account for Table Storage
   - Set environment variables:
     ```
     AZURE_STORAGE_CONNECTION_STRING = <your-connection-string>
     INGEST_API_KEY = <choose-a-strong-secret>
     ```
   - Note the function URL: `https://<function-name>.azurewebsites.net`

### Step 2: Flash ESP32 Firmware

1. **Open Arduino IDE** and install ESP32 board support
   ```
   Board Manager → ESP32 by Espressif
   ```

2. **Open `washer_monitor/washer_monitor.ino`**

3. **Edit `washer_monitor/config.h`** with your credentials:
   ```cpp
   #define WIFI_SSID "Your_WiFi_Name"
   #define WIFI_PASSWORD "Your_Password"
   #define API_ENDPOINT "https://<function-name>.azurewebsites.net/api/ingest"
   #define INGEST_API_KEY "your-api-key-from-above"
   ```

4. **Connect MPU6050 to ESP32:**
   ```
   MPU6050 → ESP32
   VCC     → 3.3V
   GND     → GND
   SCL     → GPIO 22
   SDA     → GPIO 21
   ```

5. **Upload to ESP32:**
   - Select Board: "ESP32 Dev Module" (or your variant)
   - Select COM port
   - Click Upload

### Step 3: Monitor Live Data

1. Open your browser: `https://<function-name>.azurewebsites.net/api/dashboard`
2. Tap/vibrate the ESP32 to see live motion data
3. Check the device status indicator (top-right)

---

## 📊 Using the Dashboard

### Live Tab
**Real-time monitoring of your washing machine**

```
Controls:
  • Device ID: Filter by device (default: "ESP32 Washer Monitor")
  • Refresh Rate: 5s, 10s, 30s, or 1 minute
  • Backgrounds: Toggle state coloring

Display:
  • Motion Score chart (blue line)
  • 10-second average motion (orange line)
  • Colored backgrounds (green=IDLE, red=RUNNING)
  • Statistics: Total samples, avg motion, running %, etc.
```

### Historical Tab
**Review and analyze past cycles**

```
Filters:
  • Date range: Set "Since" and "Until" timestamps
  • State filter: Show only IDLE or RUNNING cycles
  • Limit: Max 10,000 samples

Actions:
  • Load Data: Fetch matching samples
  • ⬇ CSV: Export as CSV for external analysis
```

### ML Training Tab
**Build custom cycle detection for your machine**

```
Workflow:
  1. Set date filters for data you want to label
  2. Click "📂 Load Data" to load that period
  3. Click-and-drag on the motion chart to select a time range
  4. Choose label: IDLE, WASH, or SPINDRY
  5. Click "Apply Label"
  6. Repeat until you've labeled 30-50 samples of each state
  7. Click "⬇ Labelled CSV" to download

What Each Label Means:
  • IDLE: Machine at rest (baseline vibration)
  • WASH: Main wash cycle (moderate vibration)
  • SPINDRY: High-speed spin-dry (intense vibration)
```

---

## 🤖 ML Training Pipeline

### Overview

Transform your labeled data into a custom cycle detector:

```
Labeled CSV
    ↓
  [train_model.py]
    ↓
  Random Forest
  Classifier
    ↓
  Accuracy Metrics
    ↓
  C++ Code Generator
    ↓
  washer_model.h
    ↓
  Deploy to ESP32
    ↓
  Custom Cycle Detection! 🎉
```

### Quick Start: Training Your Model

#### Step 1: Label Data in Dashboard
1. Go to **ML Training** tab
2. Select historical data with date filters
3. Click "📂 Load Data"
4. Click-drag to select regions and label them
5. Repeat until you have 30-50 samples per class
6. Click "⬇ Labelled CSV"

#### Step 2: Install Python Dependencies
```bash
cd Student-Life-Track-2026
pip install -r requirements.txt

# Or manually:
pip install pandas scikit-learn joblib numpy matplotlib
```

#### Step 3: Train the Model
```bash
# Basic (looks for washer_labelled_samples.csv)
python train_model.py

# Or specify custom paths
python train_model.py --data my_data.csv --output my_model.h
```

#### Step 4: Review Results
The script outputs:
- ✅ Cross-validation accuracy (5-fold)
- ✅ Test set accuracy
- ✅ Confusion matrix
- ✅ Feature importance
- ✅ Classification report

Example output:
```
Mean Accuracy: 0.942 (+/- 0.025)
Test Accuracy: 0.956

Feature Importance:
    motion_score: 0.450
    motion_avg: 0.380
    ax: 0.085
    ay: 0.062
    az: 0.023
```

#### Step 5: Deploy to ESP32
```bash
# Copy the generated header
cp washer_model.h washer_monitor/washer_model.h

# Edit washer_monitor.ino to include and use it:
#include "washer_model.h"

// In your loop:
WasherFeatures features = {
  motion_score: current_score,
  motion_avg: current_avg,
  ax, ay, az
};
uint8_t state = WasherStateClassifier::predict(features);
```

Then upload to ESP32 and enjoy autonomous cycle detection! 🎉

### Re-training

As you collect more data:
1. Label additional samples in the dashboard
2. Export updated CSV
3. Run `train_model.py` again
4. Copy new `washer_model.h`
5. Re-upload to ESP32

Each re-training improves accuracy!

---

## 📁 Project Structure

```
Student-Life-Track-2026/
│
├── 📄 README.md                      ← You are here!
├── 📄 PROJECT_STORY.md               ← Hackathon pitch
│
├── 🔷 Azure/
│   ├── function_app.py               ← Cloud backend (POST/GET/PATCH endpoints)
│   ├── requirements.txt
│   └── (deployment configs)
│
├── 🔷 washer_monitor/                ← ESP32 Firmware
│   ├── washer_monitor.ino            ← Main sketch
│   ├── config.h                      ← WiFi, API credentials
│   ├── mpu_sensor.cpp                ← Accelerometer driver
│   ├── signal_processing.cpp         ← EMA filter, motion detection
│   ├── wifi_manager.cpp              ← WiFi + HTTP client
│   ├── azure_poster.cpp              ← JSON batching & POST
│   └── washer_model.h                ← Generated ML model (after training)
│
├── 🐍 train_model.py                 ← ML training script
├── requirements.txt                  ← Python dependencies
│
└── 📊 washer_labelled_samples.csv     ← Your labeled data (from dashboard)
```

### Key Files Explained

**Azure/function_app.py** - Cloud API
- `POST /api/ingest` – Device sends batched sensor data
- `GET /api/samples` – Dashboard fetches data (supports filtering)
- `PATCH /api/label` – Dashboard labels samples
- `GET /api/dashboard` – Returns self-contained HTML dashboard

**washer_monitor/washer_monitor.ino** - ESP32 Main Loop
- Reads MPU6050 at 10Hz
- Computes motion metrics
- POSTs data every 2.5 seconds during cycles

**train_model.py** - ML Trainer
- Loads labeled CSV from dashboard
- Trains Random Forest (50 trees, max depth 10)
- Evaluates with cross-validation
- Generates C++ header for ESP32

---

## 🐛 Troubleshooting

### Dashboard Issues

#### "Cannot connect to dashboard"
- ✓ Check Azure Functions deployment status
- ✓ Verify CORS is enabled (if accessing from different domain)
- ✓ Check connection string and API key are set

#### Data shows as "Offline"
- ✓ ESP32 device: Check WiFi connection
- ✓ Verify INGEST_API_KEY matches dashboard backend
- ✓ Check Azure Table Storage quota (rarely hit)
- ✓ Look at ESP32 serial monitor for error messages

#### Charts not updating in Live tab
- ✓ Check browser console for JavaScript errors
- ✓ Verify device is sending data (check last timestamp)
- ✓ Try refreshing the page

### ESP32 Issues

#### ESP32 won't connect to WiFi
- ✓ Verify SSID and password in `config.h`
- ✓ Check if 5GHz WiFi (use 2.4GHz instead)
- ✓ Open serial monitor (9600 baud) to see error messages
- ✓ Re-upload firmware after changes

#### No data appearing in dashboard
- ✓ Check that ESP32 is powered and running
- ✓ Look at serial output for HTTP POST errors
- ✓ Verify API_ENDPOINT URL is correct
- ✓ Verify INGEST_API_KEY matches Azure backend

#### Accelerometer readings are wrong
- ✓ Check MPU6050 wiring (I2C: SCL=GPIO22, SDA=GPIO21)
- ✓ Verify I2C pull-up resistors are present (4.7kΩ typical)
- ✓ Run I2C scanner to verify MPU6050 is detected
- ✓ Check for loose connections

### ML Training Issues

#### "No labeled data found"
- ✓ Make sure you labeled data in the dashboard's ML tab
- ✓ Click "⬇ Labelled CSV" to download the right file
- ✓ Verify CSV has a `sub_state` column with IDLE/WASH/SPINDRY

#### Low model accuracy
- ✓ Label more samples (aim for 50+ per class)
- ✓ Ensure labels are accurate (watch the chart when labeling)
- ✓ Collect data under different conditions
- ✓ Check for imbalanced datasets

#### Python dependency errors
```bash
# Make sure you're using Python 3.8+
python --version

# Reinstall all dependencies
pip install -r requirements.txt --force-reinstall
```

---

## 🚀 Advanced Usage

### Custom Azure Deployment

See `Azure/function_app.py` for endpoint details. You can:
- Add custom filtering logic in `/api/samples`
- Implement data retention policies
- Add user authentication (currently open/anonymous)
- Scale to multiple devices

### ESP32 Customization

Modify `washer_monitor/config.h` to tune:
- Sampling frequency (currently 10Hz)
- EMA filter alpha (motion smoothing)
- State detection thresholds
- HTTP batch interval

### Model Improvements

The `train_model.py` script can be extended to:
- Support more than 3 classes
- Export to TensorFlow Lite for better ESP32 performance
- Use neural networks instead of Random Forest
- Perform hyperparameter tuning (GridSearchCV)

---

## 📚 Learning Resources

- **IoT Architecture**: See PROJECT_STORY.md for design decisions
- **Signal Processing**: Read about EMA filters and motion detection
- **ML Fundamentals**: Random Forest classifiers, cross-validation, feature importance
- **Embedded Systems**: Arduino IDE, ESP32 capabilities, I2C protocol
- **Cloud Services**: Azure Functions, Table Storage, REST APIs

---

## 🤝 Contributing

We welcome contributions! Areas for improvement:

- [ ] Support for multiple washer types/models
- [ ] Deep learning models (TensorFlow Lite)
- [ ] Mobile app (React Native)
- [ ] Energy consumption tracking
- [ ] Automated data collection heuristics
- [ ] Better feature engineering pipeline

---

## 📝 License

[Your License Here]

---

## 🙋 Support & Questions

- 📖 Check this README first
- 🐛 Search existing GitHub issues
- 💬 Open a new issue with details
- ✉️ Email: [contact info]

---

## 🎉 Credits

Built with ❤️ for students who are tired of forgetting their laundry.

**Technologies**: ESP32, Azure Functions, Chart.js, scikit-learn, Random Forest

---

**Happy laundry monitoring! 🧺**

*Last updated: March 2026*
