import pandas as pd
import matplotlib.pyplot as plt
import glob
import os
import sys

def main():
    # If filenames are passed as arguments, use them. Otherwise, find all.
    if len(sys.argv) > 1:
        target_files = sys.argv[1:]
    else:
        log_files = glob.glob("washer_log_*.csv")
        if not log_files:
            print("No washer_log CSV files found in the current directory.")
            print("Usage: python Visualise.py [path_to_csv1] [path_to_csv2] ...")
            sys.exit(1)
        target_files = sorted(log_files) # Sort to have consistent order
    
    print(f"Visualizing files: {', '.join(target_files)}")

    # Create a figure with two subplots: one for motion, one for x, y, z acceleration
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    colors_motion = ['purple', 'blue', 'green', 'orange']
    colors_avg = ['red', 'darkred', 'crimson', 'pink']
    
    x_label = 'Sample Index'

    for idx, target_file in enumerate(target_files):
        # Read the CSV
        try:
            df = pd.read_csv(target_file)
        except Exception as e:
            print(f"Error reading {target_file}: {e}")
            continue

        # Ensure data types are correct
        for col in ['esp_ms', 'ax', 'ay', 'az', 'motion', 'avg_motion_10s']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # Use esp_ms (converted to minutes relative to start) for the X-axis if available
        if 'esp_ms' in df.columns and not df['esp_ms'].isna().all():
            x_col = (df['esp_ms'] - df['esp_ms'].iloc[0]) / 60000.0
            x_label = 'Time (minutes since start)'
        else:
            x_col = df.index
            x_label = 'Sample Index'

        file_basename = os.path.basename(target_file)
        
        # Set specific colors based on filenames
        if file_basename == "washer_log_20260313_231123.csv":
            base_color = "blue"  # Bright blue
        elif file_basename == "washer_log_20260314_014951.csv":
            base_color = "red"   # Bright red
        else:
            base_color = colors_motion[idx % len(colors_motion)]
            
        avg_color = "green"  # Average value always green

        # Plot Motion
        if 'motion' in df.columns:
            # Make the raw motion slightly more transparent if we have average
            alpha_val = 0.3 if 'avg_motion_10s' in df.columns else 0.8
            ax1.plot(x_col, df['motion'], label=f'Motion: {file_basename}', color=base_color, alpha=alpha_val)
            
            if 'avg_motion_10s' in df.columns:
                ax1.plot(x_col, df['avg_motion_10s'], label=f'Avg (10s): {file_basename}', color=avg_color, linewidth=2)

        # Plot ax, ay, az
        if all(col in df.columns for col in ['ax', 'ay', 'az']):
            ax2.plot(x_col, df['ax'], label=f'X: {file_basename}', alpha=0.5)
            ax2.plot(x_col, df['ay'], label=f'Y: {file_basename}', alpha=0.5)
            ax2.plot(x_col, df['az'], label=f'Z: {file_basename}', alpha=0.5)

    ax1.set_ylabel('Motion Value')
    ax1.set_ylim(0, 9000)
    ax1.set_title('Washer Motion Data Overlay')
    ax1.grid(True, linestyle='--', alpha=0.6)
    
    # Put legend outside if it gets too large, but upper right is fine for 2 files
    ax1.legend(loc='upper right', fontsize='small')

    ax2.set_ylabel('Acceleration')
    ax2.set_xlabel(x_label)
    ax2.grid(True, linestyle='--', alpha=0.6)
    ax2.legend(loc='upper right', fontsize='small')

    # Set x-axis limit starting from 10 mins 30s (10.5 minutes)
    plt.xlim(left=0)

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()
