import pandas as pd
import matplotlib.pyplot as plt
import glob
import os
import sys

def main():
    # If a filename is passed as argument, use it. Otherwise, find the latest.
    if len(sys.argv) > 1:
        target_file = sys.argv[1]
    else:
        log_files = glob.glob("washer_log_*.csv")
        if not log_files:
            print("No washer_log CSV files found in the current directory.")
            print("Run the script from the directory containing the logs or specify the file:")
            print("python Visualise.py <path_to_csv>")
            sys.exit(1)
        target_file = max(log_files, key=os.path.getctime)
    
    print(f"Visualizing file: {target_file}")

    # Read the CSV
    try:
        df = pd.read_csv(target_file)
    except Exception as e:
        print(f"Error reading {target_file}: {e}")
        sys.exit(1)

    # Ensure data types are correct
    for col in ['esp_ms', 'ax', 'ay', 'az', 'motion']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # Use esp_ms (converted to minutes relative to start) for the X-axis if available
    if 'esp_ms' in df.columns and not df['esp_ms'].isna().all():
        x_col = (df['esp_ms'] - df['esp_ms'].iloc[0]) / 60000.0
        x_label = 'Time (minutes since start)'
    else:
        x_col = df.index
        x_label = 'Sample Index'

    # Create a figure with two subplots: one for motion, one for x, y, z acceleration
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    # Plot Motion
    if 'motion' in df.columns:
        ax1.plot(x_col, df['motion'], label='Motion Value', color='purple', alpha=0.8)
        ax1.set_ylabel('Motion Value')
        ax1.set_ylim(0, 1000)
        ax1.set_title(f'Washer Motion Data: {os.path.basename(target_file)}')
        ax1.grid(True, linestyle='--', alpha=0.6)
        ax1.legend()

    # Plot ax, ay, az
    if all(col in df.columns for col in ['ax', 'ay', 'az']):
        ax2.plot(x_col, df['ax'], label='X Acceleration', alpha=0.7)
        ax2.plot(x_col, df['ay'], label='Y Acceleration', alpha=0.7)
        ax2.plot(x_col, df['az'], label='Z Acceleration', alpha=0.7)
        ax2.set_ylabel('Acceleration')
        ax2.set_xlabel(x_label)
        ax2.grid(True, linestyle='--', alpha=0.6)
        ax2.legend()

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()

