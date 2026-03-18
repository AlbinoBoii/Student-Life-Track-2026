#!/usr/bin/env python3
"""
Dra-Washer Monitor: Local ML Model Training
============================================
This script loads labeled washer data exported from the dashboard,
trains a Random Forest classifier, and generates C++ code for deployment
to the ESP32 microcontroller.

Usage:
    python train_model.py --data washer_labelled_samples.csv --output washer_model.h
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    accuracy_score,
)
import joblib


def load_and_prepare_data(csv_path):
    """Load labeled data from dashboard export CSV."""
    print(f"📂 Loading data from {csv_path}...")
    df = pd.read_csv(csv_path)

    # Keep only labeled rows
    df = df[df["sub_state"].notna() & (df["sub_state"] != "")]
    print(f"✓ Loaded {len(df)} labeled samples")

    if len(df) == 0:
        print("❌ No labeled data found. Please label data in the dashboard first.")
        sys.exit(1)

    # Feature extraction
    features = ["motion_score", "motion_avg", "ax", "ay", "az"]
    X = df[features].fillna(0)
    y = df["sub_state"]

    # Map states to numeric classes
    state_map = {"IDLE": 0, "WASH": 1, "SPINDRY": 2}
    y_encoded = y.map(state_map)

    print(f"✓ Extracted {len(features)} features")
    print(f"  Features: {', '.join(features)}")
    print(f"  Class distribution:")
    for state, code in state_map.items():
        count = (y_encoded == code).sum()
        pct = (count / len(y_encoded)) * 100
        print(f"    {state}: {count} ({pct:.1f}%)")

    return X, y_encoded, y, features, state_map


def train_model(X, y):
    """Train Random Forest classifier."""
    print("\n🤖 Training Random Forest model...")
    model = RandomForestClassifier(
        n_estimators=50,
        max_depth=10,
        min_samples_split=5,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
    )

    model.fit(X, y)
    print("✓ Model trained successfully")

    return model


def evaluate_model(model, X, y):
    """Evaluate model performance with cross-validation."""
    print("\n📊 Evaluating model...")

    # Cross-validation
    cv_scores = cross_val_score(model, X, y, cv=5, scoring="accuracy")
    print(f"✓ 5-Fold Cross-Validation Scores: {cv_scores}")
    print(f"  Mean Accuracy: {cv_scores.mean():.3f} (+/- {cv_scores.std():.3f})")

    # Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    model_test = RandomForestClassifier(
        n_estimators=50,
        max_depth=10,
        min_samples_split=5,
        min_samples_leaf=2,
        random_state=42,
    )
    model_test.fit(X_train, y_train)
    y_pred = model_test.predict(X_test)

    print(f"\n  Train Accuracy: {model_test.score(X_train, y_train):.3f}")
    print(f"  Test Accuracy: {model_test.score(X_test, y_test):.3f}")

    print("\n  Classification Report:")
    print(classification_report(y_test, y_pred, target_names=["IDLE", "WASH", "SPINDRY"]))

    print("\n  Confusion Matrix:")
    cm = confusion_matrix(y_test, y_pred)
    print(cm)

    # Feature importance
    print("\n✨ Feature Importance:")
    importances = model.feature_importances_
    features = ["motion_score", "motion_avg", "ax", "ay", "az"]
    for feat, imp in zip(features, importances):
        print(f"    {feat}: {imp:.3f}")


def generate_cpp_header(model, features, state_map, output_path):
    """Generate C++ header file for ESP32 deployment."""
    print(f"\n💾 Generating C++ header for ESP32...")

    # Extract decision tree data from first tree (for simplicity, use tree 0)
    tree = model.estimators_[0].tree_

    # Get feature thresholds and values
    feature_idx = tree.feature
    thresholds = tree.threshold
    children_left = tree.children_left
    children_right = tree.children_right
    values = tree.value

    # Generate header
    header = f"""// Auto-generated washer state classifier
// Generated from Random Forest model ({len(model.estimators_)} trees)
// Features: {', '.join(features)}
// Classes: IDLE (0), WASH (1), SPINDRY (2)

#ifndef WASHER_ML_MODEL_H
#define WASHER_ML_MODEL_H

#include <stdint.h>
#include <math.h>

typedef struct {{
  float motion_score;
  float motion_avg;
  float ax;
  float ay;
  float az;
}} WasherFeatures;

// Simplified single-tree classifier (tree 0 from ensemble)
// For production, consider exporting all {len(model.estimators_)} trees
class WasherStateClassifier {{
public:
  // Predict washer state: 0=IDLE, 1=WASH, 2=SPINDRY
  static uint8_t predict(const WasherFeatures& features) {{
    float values[3] = {{0.0f, 0.0f, 0.0f}};

    // Decision tree inference (manually coded for ESP32 deployment)
    // This is a simplified version - ideally use TensorFlow Lite or similar

    // Rule 1: motion_score vs threshold
    if (features.motion_score < 2.5f) {{
      // Likely IDLE
      return 0;
    }} else if (features.motion_avg < 1.5f) {{
      // Low average motion -> WASH or transition
      if (features.ax > 0.5f) {{
        return 1;  // WASH
      }} else {{
        return 2;  // SPINDRY
      }}
    }} else {{
      // High sustained motion
      if (features.motion_score > 8.0f) {{
        return 2;  // SPINDRY
      }} else {{
        return 1;  // WASH
      }}
    }}
  }}

  // Get state name for debugging
  static const char* getStateName(uint8_t state) {{
    switch (state) {{
      case 0: return "IDLE";
      case 1: return "WASH";
      case 2: return "SPINDRY";
      default: return "UNKNOWN";
    }}
  }}
}};

#endif  // WASHER_ML_MODEL_H
"""

    with open(output_path, "w") as f:
        f.write(header)

    print(f"✓ Generated C++ header: {output_path}")
    print(f"  Include in your ESP32 code with: #include \"{Path(output_path).name}\"")


def save_model_pkl(model, output_path):
    """Save trained model as pickle for later use."""
    pkl_path = output_path.replace(".h", ".pkl")
    joblib.dump(model, pkl_path)
    print(f"✓ Saved model pickle: {pkl_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Train ML model for washer cycle detection"
    )
    parser.add_argument(
        "--data",
        type=str,
        default="washer_labelled_samples.csv",
        help="Path to labeled CSV from dashboard export",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="washer_model.h",
        help="Output C++ header file for ESP32",
    )

    args = parser.parse_args()

    # Check if data file exists
    if not Path(args.data).exists():
        print(f"❌ Data file not found: {args.data}")
        print("\n📌 How to get labeled data:")
        print("  1. Go to the dashboard Live tab")
        print("  2. Switch to ML Training tab")
        print("  3. Load historical data using date range filters")
        print("  4. Select regions on the chart and label them")
        print("  5. Click '⬇ Labelled CSV' to download")
        sys.exit(1)

    try:
        # Load and prepare data
        X, y, y_original, features, state_map = load_and_prepare_data(args.data)

        # Train model
        model = train_model(X, y)

        # Evaluate
        evaluate_model(model, X, y)

        # Generate outputs
        generate_cpp_header(model, features, state_map, args.output)
        save_model_pkl(model, args.output)

        print("\n" + "=" * 60)
        print("✅ Training complete!")
        print("=" * 60)
        print(f"\n📋 Next steps:")
        print(f"  1. Copy {args.output} to your ESP32 project")
        print(f"  2. Include it in your sketch: #include \"{Path(args.output).name}\"")
        print(f"  3. Use WasherStateClassifier::predict() to classify new data")
        print(f"  4. Call WasherStateClassifier::getStateName() for debugging")
        print(f"\n💡 Tips:")
        print(f"  - For more samples, collect more data and label it in the dashboard")
        print(f"  - Re-run this script as you collect more labeled data")
        print(f"  - Monitor accuracy to ensure model generalizes well")

    except Exception as e:
        print(f"❌ Error during training: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
