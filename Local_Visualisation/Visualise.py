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
        script_dir = os.path.dirname(os.path.abspath(__file__))
        search_pattern = os.path.join(script_dir, "washer_log_*.csv")
        log_files = glob.glob(search_pattern)
        if not log_files:
            print(f"No washer_log CSV files found in {script_dir}.")
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

        # Rename new Azure CSV columns to legacy names for compatibility
        rename_map = {
            'ts_ms': 'esp_ms',
            'motion_score': 'motion',
            'motion_avg': 'avg_motion_10s'
        }
        df = df.rename(columns=rename_map)

        # Ensure data types are correct
        for col in ['esp_ms', 'ax', 'ay', 'az', 'motion', 'avg_motion_10s']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # Use received_at (converted to datetime) for the X-axis if available
        if 'received_at' in df.columns and not df['received_at'].isna().all():
            df['received_at'] = pd.to_datetime(df['received_at'])
            x_col = df['received_at']
            x_label = 'Time Received'
        # Fallback to esp_ms (converted to minutes relative to start) if available
        elif 'esp_ms' in df.columns and not df['esp_ms'].isna().all():
            x_col = (df['esp_ms'] - df['esp_ms'].iloc[0]) / 60000.0
            x_label = 'Time (minutes since start)'
        else:
            x_col = df.index
            x_label = 'Sample Index'

        file_basename = os.path.basename(target_file)
        
        # Set distinct contrasting colors based on index rather than hardcoding names
        base_color = colors_motion[idx % len(colors_motion)]
        avg_color = colors_avg[idx % len(colors_avg)]

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
