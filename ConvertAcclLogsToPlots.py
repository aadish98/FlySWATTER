#!/usr/bin/env python3

import os
import re
import json
import gc
from pathlib import Path
import ctypes
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # So it does not require a GUI environment
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from scipy.signal import find_peaks

try:
    _LIBC = ctypes.CDLL("libc.so.6")
except Exception:
    _LIBC = None

################################################################################
# Helper Functions
################################################################################
def clear_memory_between_files():
    """Best-effort memory release between large file iterations."""
    gc.collect()
    if _LIBC is not None:
        try:
            _LIBC.malloc_trim(0)
        except Exception:
            pass


def calculate_median_centered_offsets(filepath, V_ref=3.0, sensitivity=0.3):
    """
    Calculate x, y, z offsets that would center the median of each component on 0.
    Note: Z acceleration in plotting is computed as ((Z_V - Z_offset)/sensitivity) + 1.0.
    To center Z on 0 (not 1) after that +1 adjustment, set Z_offset = median(Z_V) + sensitivity.
    """
    data = pd.read_csv(filepath)
    
    # Convert ADC values to voltage
    data["X_Voltage"] = data["X"] * V_ref / 1023.0
    data["Y_Voltage"] = data["Y"] * V_ref / 1023.0
    data["Z_Voltage"] = data["Z"] * V_ref / 1023.0
    
    # Calculate offsets as the median voltage (this centers the median on 0)
    X_offset = data["X_Voltage"].median()
    Y_offset = data["Y_Voltage"].median()
    # For Z, add +sensitivity to counter the +1g added in plotting so median -> 0
    Z_offset = data["Z_Voltage"].median() + sensitivity
    
    return X_offset, Y_offset, Z_offset



def count_peaks_above_threshold(values, threshold):
    """
    Counts local maxima in 'values' that are >= threshold.
    A local maximum is any value[i] >= threshold and also
    >= the neighbors value[i-1] and value[i+1].
    """
    count = 0
    # We skip the very first and last points to avoid index errors
    for i in range(1, len(values) - 1):
        if (
            values[i] >= threshold and
            values[i] >= values[i - 1] and
            values[i] >= values[i + 1]
        ):
            count += 1
    return count


def find_manifest_path(data_dir: str) -> str:
    """
    Find manifest.json in expected new-schema locations.
    Search order: data_dir, then parent of data_dir.
    """
    candidates = [
        os.path.join(data_dir, "manifest.json"),
        os.path.join(os.path.dirname(os.path.abspath(data_dir)), "manifest.json"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise ValueError(
        f"Could not find manifest.json for data_dir={data_dir}. "
        "Expected at data_dir or its parent directory."
    )


def load_manifest(manifest_path: str) -> dict:
    """
    Load and validate required fields from new logger manifest.
    """
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        raise ValueError(f"Failed to read manifest at {manifest_path}: {e}") from e

    required = ["platform", "speed", "start_iso"]
    missing = [k for k in required if k not in manifest]
    if missing:
        raise ValueError(
            f"Manifest missing required field(s) {missing}: {manifest_path}"
        )
    return manifest


def parse_manifest_start_iso(manifest: dict, manifest_path: str) -> pd.Timestamp:
    start_iso = manifest.get("start_iso")
    ts = pd.to_datetime(start_iso, errors="coerce")
    if pd.isna(ts):
        raise ValueError(
            f"Manifest start_iso is invalid ({start_iso}) in {manifest_path}"
        )
    return ts


def calc_logging_duration(filepath: str) -> float:
    """
    Reads new-schema CSV and computes logging duration from `t_ms` in seconds.
    """
    df = pd.read_csv(filepath, usecols=["t_ms"])
    t_ms = pd.to_numeric(df["t_ms"], errors="coerce").dropna()
    nrows = len(t_ms)
    if nrows < 2:
        raise ValueError(
            f"Need at least 2 valid t_ms rows in {os.path.basename(filepath)}"
        )

    t_min = float(t_ms.min())
    t_max = float(t_ms.max())
    duration_sec = (t_max - t_min) / 1000.0
    if duration_sec <= 0:
        raise ValueError(
            f"Non-positive t_ms duration in {os.path.basename(filepath)} "
            f"(min={t_min}, max={t_max})"
        )

    sampling_freq = nrows / duration_sec
    print(f"Sampling Frequency: {sampling_freq:.2f} Hz (from t_ms)")
    print(f"Logging Duration: {duration_sec/60.0:.2f} mins (from t_ms)")
    return duration_sec


def calculate_net_force(data: pd.DataFrame, start_time: float, end_time: float) -> float:
    """
    Computes net force (integrated total acceleration over time)
    between start_time and end_time (in seconds).
    data must have columns ["Time", "Vibration_Accel"].
    """
    window = data[(data["Time"] >= start_time) & (data["Time"] <= end_time)]

    if len(window) < 2:
        print(f"Warning: Not enough data points in the window {start_time}–{end_time}.")
        return 0.0

    dt = window["Time"].diff().mean()  # Average time step (in seconds)
    net_force = (window["Vibration_Accel"] * dt).sum()
    return net_force


def calculate_adaptive_threshold(signal_values, window_size=30, noise_factor=3.0):
    """
    Calculate adaptive threshold based on local noise levels.
    Uses median absolute deviation (MAD) as robust estimate of noise.
    """
    if len(signal_values) < window_size:
        return np.percentile(signal_values, 95)
    
    # Calculate MAD for noise estimation
    median_val = np.median(signal_values)
    mad = np.median(np.abs(signal_values - median_val))
    
    # Adaptive threshold: median + (noise_factor * MAD)
    threshold = median_val + noise_factor * mad
    
    # Ensure minimum threshold
    min_threshold = 0.035  # minimum 0.05g
    return max(threshold, min_threshold)


def smooth_signal(signal_values, window_size=5):
    """
    Apply moving average smoothing to reduce high-frequency noise.
    Window size of 5 is ~4-7ms smoothing at typical sampling rates.
    """
    if window_size < 2 or len(signal_values) < window_size:
        return signal_values
    
    # Use convolution for efficient moving average
    kernel = np.ones(window_size) / window_size
    smoothed = np.convolve(signal_values, kernel, mode='same')
    return smoothed


def find_pulses_improved(df, adaptive_threshold=True, smoothing=True, 
                         min_pulse_duration=0.1, max_gap_for_cluster=0.5, 
                         min_gap_for_pulse=10.0, manual_threshold=None,
                         extend_pulse_window=0.5):
    """
    Improved pulse detection for high-precision stepper motor signals in noisy environments.
    
    Args:
        df: DataFrame with 'Time' and 'Vibration_Accel' columns
        adaptive_threshold: Use adaptive thresholding based on local noise
        smoothing: Apply smoothing to reduce high-frequency noise
        min_pulse_duration: Minimum duration (seconds) for a valid pulse
        max_gap_for_cluster: Max gap (seconds) to consider within same pulse
        min_gap_for_pulse: Min gap (seconds) between separate pulses
        manual_threshold: Manual threshold (g) to use. If provided, overrides adaptive_threshold.
        extend_pulse_window: Time (seconds) to extend pulse boundaries forward/backward to capture
                            full oscillatory response (default: 0.5s)
    
    Returns:
        List of (start_time, end_time) tuples representing detected pulses
    """
    signal = df["Vibration_Accel"].values
    time = df["Time"].values
    
    # Apply smoothing if requested
    if smoothing and len(signal) > 10:
        signal = smooth_signal(signal, window_size=5)
    
    # Calculate threshold (manual, adaptive, or fixed)
    if manual_threshold is not None:
        threshold = manual_threshold
        print(f"Manual threshold: {threshold:.4f} g")
    elif adaptive_threshold:
        threshold = calculate_adaptive_threshold(signal, window_size=30, noise_factor=4.0)
        print(f"Adaptive threshold: {threshold:.4f} g (based on noise analysis)")
    else:
        threshold = 0.06
        print(f"Fixed threshold: {threshold} g")
    
    # NEW APPROACH: Use peak detection to find individual pulses
    # Find local maxima (peaks) above threshold
    
    # Find peaks above threshold
    peaks, properties = find_peaks(signal, height=threshold, distance=int(0.1 / np.mean(np.diff(time)) if len(time) > 1 and np.mean(np.diff(time)) > 0 else 10))
    
    if len(peaks) == 0:
        # Fallback to edge detection if no peaks found
        above_threshold = signal >= threshold
        if not above_threshold.any():
            return []
        
        rising_edge_idx = np.where(np.diff(above_threshold.astype(int)) == 1)[0]
        falling_edge_idx = np.where(np.diff(above_threshold.astype(int)) == -1)[0]
        
        if above_threshold[0]:
            rising_edge_idx = np.concatenate([[0], rising_edge_idx])
        if above_threshold[-1]:
            falling_edge_idx = np.concatenate([falling_edge_idx, [len(signal) - 1]])
        
        if len(rising_edge_idx) == 0 or len(falling_edge_idx) == 0:
            return []
        
        if len(rising_edge_idx) > len(falling_edge_idx):
            rising_edge_idx = rising_edge_idx[:len(falling_edge_idx)]
        elif len(falling_edge_idx) > len(rising_edge_idx):
            falling_edge_idx = falling_edge_idx[:len(rising_edge_idx)]
        
        candidate_pulses = []
        for i in range(len(rising_edge_idx)):
            start_idx = rising_edge_idx[i]
            end_idx = falling_edge_idx[i]
            duration = time[end_idx] - time[start_idx]
            if duration >= min_pulse_duration:
                candidate_pulses.append((time[start_idx], time[end_idx]))
        
        return candidate_pulses
    
    # For each peak, find the pulse boundaries
    dt = np.mean(np.diff(time)) if len(time) > 1 else 0.001
    extend_samples = int(extend_pulse_window / dt) if dt > 0 else 0
    quiet_threshold = threshold * 0.3  # Lower threshold for finding quiet periods
    
    candidate_pulses = []
    
    for peak_idx in peaks:
        peak_time = time[peak_idx]
        
        # Find start: go backward until we hit a quiet period or reach extend_samples
        start_idx = peak_idx
        for j in range(peak_idx - 1, max(0, peak_idx - extend_samples * 2), -1):
            if signal[j] < quiet_threshold:
                # Found quiet period - this is likely the start
                start_idx = j
                break
            start_idx = j
        
        # Find end: go forward until we hit a quiet period or reach extend_samples
        end_idx = peak_idx
        for j in range(peak_idx + 1, min(len(signal), peak_idx + extend_samples * 2)):
            if signal[j] < quiet_threshold:
                # Found quiet period - this is likely the end
                end_idx = j
                break
            end_idx = j
        
        # Ensure minimum duration
        duration = time[end_idx] - time[start_idx]
        if duration >= min_pulse_duration:
            candidate_pulses.append((time[start_idx], time[end_idx]))
    
    if not candidate_pulses:
        return []
    
    # Sort by start time
    candidate_pulses.sort(key=lambda x: x[0])
    
    # Merge only if pulses are very close together (less than max_gap_for_cluster)
    # This prevents merging distinct pulses that are just close
    merged_pulses = []
    current_start, current_end = candidate_pulses[0]
    
    for pulse_start, pulse_end in candidate_pulses[1:]:
        gap = pulse_start - current_end
        
        # Only merge if gap is very small (within same pulse cluster)
        # Use max_gap_for_cluster instead of min_gap_for_pulse for merging
        if gap < max_gap_for_cluster:
            current_end = pulse_end
        else:
            # Save current pulse and start new one
            merged_pulses.append((current_start, current_end))
            current_start = pulse_start
            current_end = pulse_end
    
    # Don't forget the last pulse
    merged_pulses.append((current_start, current_end))
    
    return merged_pulses


def save_topN_vibrations_plot(filepath: str,
                              plotname: str,
                              speed_setting: str,
                              X_offset: float,
                              Y_offset: float,
                              Z_offset: float,
                              duration_mins: float,
                              N_Max: int,
                              N_MINS_BUCKET: float,
                              manifest_start_ts=None,
                              earliest_start_ts=None,
                              min_pulse_gs: float = 0.001,
                              threshold: float = None):
    """
    Reads the CSV, computes acceleration values, identifies top N peaks
    spaced by N_MINS_BUCKET minutes, and saves the plot to 'plotname'.
    Then also finds *multiple* pulses using find_pulses (with threshold parameter),
    shades each pulse region, and reports its net force.
    """

    # Constants
    V_ref = 3.0
    sensitivity = 0.3

    # Read CSV
    data = pd.read_csv(filepath)
    if "t_ms" not in data.columns:
        raise ValueError(
            f"Missing required column 't_ms' in new-schema CSV: {os.path.basename(filepath)}"
        )
    if manifest_start_ts is None:
        raise ValueError("manifest_start_ts is required for new-schema plotting.")

    t_ms = pd.to_numeric(data["t_ms"], errors="coerce")
    if t_ms.isna().any():
        bad_rows = int(t_ms.isna().sum())
        raise ValueError(
            f"Found {bad_rows} non-numeric t_ms rows in {os.path.basename(filepath)}"
        )

    t_ms_min = float(t_ms.min())
    elapsed_seconds = (t_ms - t_ms_min) / 1000.0
    data["ElapsedSeconds"] = elapsed_seconds

    ts_abs = manifest_start_ts + pd.to_timedelta(t_ms, unit="ms")
    base_ts = ts_abs.iloc[0]

    # x-axis for plotting: always local wall time from manifest start + t_ms
    use_datetime_axis = True
    try:
        base_ts_naive = base_ts.tz_localize(None)
        x_plot = ts_abs.dt.tz_localize(None)
    except Exception:
        base_ts_naive = base_ts
        x_plot = ts_abs

    # Print auto-calibrated offsets
    print(f'Xo = {X_offset:.4f}, Y0 = {Y_offset:.4f}, Z0 = {Z_offset:.4f}')

    # Convert raw ADC data to voltage, then to acceleration
    data["X_Voltage"] = data["X"] * V_ref / 1023.0
    data["Y_Voltage"] = data["Y"] * V_ref / 1023.0
    data["Z_Voltage"] = data["Z"] * V_ref / 1023.0

    data["X_Accel"] = (data["X_Voltage"] - X_offset) / sensitivity
    data["Y_Accel"] = (data["Y_Voltage"] - Y_offset) / sensitivity

    # R code adds +1 g to Z after offset
    data["Z_Accel"] = ((data["Z_Voltage"] - Z_offset) / sensitivity) + 1.0

    # For convenience, define Z_Vib_Accel = Z_Accel
    data["Z_Vib_Accel"] = data["Z_Accel"]

    # Apply smoothing to reduce high-frequency noise
    # This improves pulse detection accuracy in noisy environments
    smoothing_window = 5  # ~4-7ms smoothing at typical 1.3kHz sampling rate
    data["Smoothed_X_Accel"] = smooth_signal(data["X_Accel"].values, window_size=smoothing_window)
    data["Smoothed_Y_Accel"] = smooth_signal(data["Y_Accel"].values, window_size=smoothing_window)
    data["Smoothed_Z_Accel"] = smooth_signal(data["Z_Vib_Accel"].values, window_size=smoothing_window)

    # Compute a "Vibration_Accel" = sqrt(X^2 + Y^2 + Z^2)
    data["Vibration_Accel"] = np.sqrt(
        data["Smoothed_X_Accel"]**2 +
        data["Smoothed_Y_Accel"]**2 +
        data["Smoothed_Z_Accel"]**2
    )

    # Determine the actual threshold that will be used for pulse detection
    if threshold is not None:
        actual_threshold = threshold
    else:
        # Calculate adaptive threshold (same logic as in find_pulses_improved)
        signal = data["Vibration_Accel"].values
        actual_threshold = calculate_adaptive_threshold(signal, window_size=30, noise_factor=4.0)
    
    # Print how many local maxima >= threshold
    peak_count = count_peaks_above_threshold(data["Vibration_Accel"].values, actual_threshold)
    print(f"Number of local maxima in Vibration_Accel >= {actual_threshold:.4f}g: {peak_count}")

    # Identify top N_Max high values spaced by N_MINS_BUCKET minutes
    spacing_seconds = N_MINS_BUCKET * 60.0
    candidates = data.sort_values(by="Vibration_Accel", ascending=False)

    chosen_rows = []
    for _, row in candidates.iterrows():
        current_time = row.get("ElapsedSeconds", np.nan)
        # Check if this candidate is at least 'spacing_seconds' away from all chosen points
        if not chosen_rows:
            chosen_rows.append(row)
        else:
            too_close = any(abs(chosen.get("ElapsedSeconds", np.nan) - current_time) < spacing_seconds
                            for chosen in chosen_rows)
            if not too_close:
                chosen_rows.append(row)
        if len(chosen_rows) == N_Max:
            break
    chosen_points = pd.DataFrame(chosen_rows)

    # Prepare data for plotting
    plot_data = pd.DataFrame({
        "Time": data["ElapsedSeconds"],
        "Smoothed_X_Accel": data["Smoothed_X_Accel"],
        "Smoothed_Y_Accel": data["Smoothed_Y_Accel"],
        "Smoothed_Z_Accel": data["Smoothed_Z_Accel"],
        "Vibration_Accel": data["Vibration_Accel"]
    })

    # Create figure
    plt.figure(figsize=(10, 6))

    # Plot the main "Vibration_Accel"
    plt.plot(x_plot, plot_data["Vibration_Accel"],
             label="Vibration Stimulus (g)", linewidth=1.0, color="black")
    # Plot X, Y, Z
    plt.plot(x_plot, plot_data["Smoothed_X_Accel"],
             label="X Accel", linewidth=0.5)
    plt.plot(x_plot, plot_data["Smoothed_Y_Accel"],
             label="Y Accel", linewidth=0.5)
    plt.plot(x_plot, plot_data["Smoothed_Z_Accel"],
             label="Z Accel (adj. for gravity)", linewidth=0.5)

    # Highlight top N peak points in red
    # scatter uses matching x-axis series
    if use_datetime_axis and base_ts_naive is not None:
        scatter_x = base_ts_naive + pd.to_timedelta(chosen_points.get("ElapsedSeconds", 0.0), unit="s")
    else:
        scatter_x = chosen_points.get("ElapsedSeconds", chosen_points.get("Time", None))
    plt.scatter(scatter_x, chosen_points["Vibration_Accel"],
                color="red", s=20, zorder=5)
    # Annotate chosen points
    for _, row in chosen_points.iterrows():
        plt.text(scatter_x.loc[row.name] if hasattr(scatter_x, 'loc') else row.get("ElapsedSeconds", row.get("Time", 0.0)), row["Vibration_Accel"],
                 f"{row['Vibration_Accel']:.2f}",
                 va="bottom", ha="center", fontsize=8, color="black")

    ###################################################################
    # NEW: find *multiple* pulses and shade each region + net force
    ###################################################################
    # Use seconds for pulse detection/merging
    data_for_pulses = data.copy()
    data_for_pulses["Time"] = data_for_pulses["ElapsedSeconds"]
    pulses = find_pulses_improved(data_for_pulses, 
                                 adaptive_threshold=True, 
                                 smoothing=True,
                                 min_pulse_duration=0.1,
                                 min_gap_for_pulse=10.0,
                                 manual_threshold=threshold,
                                 extend_pulse_window=0.5)  # Extend 0.5s to capture full response
    # Keep only pulses whose integrated area (g·s) >= min_pulse_gs
    qualified_pulses = []
    for (st, en) in pulses:
        net_f = calculate_net_force(plot_data, st, en)
        if net_f >= min_pulse_gs:
            qualified_pulses.append((st, en, net_f))
    print(f"Found {len(qualified_pulses)} pulse(s) exceeding {min_pulse_gs:g} g·s.")

    # Intelligent de-overlap: manage per-side tiers and horizontal spacing
    MAX_TIERS = 5
    left_slots = [[] for _ in range(MAX_TIERS)]   # each slot holds placed label_x ranges
    right_slots = [[] for _ in range(MAX_TIERS)]

    # Collect per-pulse metrics for Excel output
    pulse_rows = []

    for idx, (pulse_start, pulse_end, net_f) in enumerate(qualified_pulses, start=1):

        # Shade the pulse region
        if use_datetime_axis and base_ts_naive is not None:
            x_start = base_ts_naive + pd.to_timedelta(pulse_start, unit="s")
            x_end = base_ts_naive + pd.to_timedelta(pulse_end, unit="s")
        else:
            x_start = pulse_start
            x_end = pulse_end
        plt.axvspan(x_start, x_end,
                    color='grey', alpha=0.15,
                    label="Stimulus Window" if idx == 1 else None)

        # Vertical lines (optional)
        plt.axvline(x=x_start, color='grey', linestyle='--', linewidth=0.8, alpha=0.5)
        plt.axvline(x=x_end,   color='grey', linestyle='--', linewidth=0.8, alpha=0.5)

        # Removed wall-time/seconds boundary labels at start/end

        # Optionally place label to the LEFT of the pulse bar to avoid overlap
        # Compute 'n' vibrations (local maxima >= threshold) and duration in seconds (3 s.f.)
        try:
            mask = (plot_data["Time"] >= pulse_start) & (plot_data["Time"] <= pulse_end)
            vals = plot_data.loc[mask, "Vibration_Accel"].values
            n_vibs = count_peaks_above_threshold(vals, actual_threshold) if len(vals) >= 3 else 0
        except Exception:
            n_vibs = 0
        dur_s = max(pulse_end - pulse_start, 0.0)
        # Peak g within pulse (2 s.f.)
        try:
            peak_g = float(np.max(vals)) if len(vals) > 0 else 0.0
        except Exception:
            peak_g = 0.0

        # Choose label side (left/right of pulse) based on available space and avoid overlap
        min_x = float(plot_data["Time"].min())
        max_x = float(plot_data["Time"].max())
        avail_left = max(pulse_start - min_x, 0.0)
        avail_right = max(max_x - pulse_end, 0.0)
        # Base offset: 30% of available space on chosen side, bounded by [10s, 60s]
        if avail_left >= avail_right and avail_left > 0:
            side = 'left'
            offset_s = max(min(avail_left * 0.3, 60.0), 10.0)
            if use_datetime_axis and base_ts_naive is not None:
                label_x = x_start - pd.to_timedelta(offset_s, unit="s")
                min_ts = base_ts_naive + pd.to_timedelta(min_x, unit="s")
                if label_x < min_ts:
                    label_x = min_ts
            else:
                label_x = max(pulse_start - offset_s, min_x)
        else:
            side = 'right'
            offset_s = max(min(avail_right * 0.3, 60.0), 10.0) if avail_right > 0 else 10.0
            if use_datetime_axis and base_ts_naive is not None:
                label_x = x_end + pd.to_timedelta(offset_s, unit="s")
                max_ts = base_ts_naive + pd.to_timedelta(max_x, unit="s")
                if label_x > max_ts:
                    label_x = max_ts
            else:
                label_x = min(pulse_end + offset_s, max_x)

        # Convert label_x to numeric seconds for collision logic
        if use_datetime_axis and base_ts_naive is not None:
            try:
                label_x_num = (label_x - base_ts_naive).total_seconds()
            except Exception:
                label_x_num = float(pulse_start if side == 'left' else pulse_end)
        else:
            label_x_num = float(label_x)

        # Enforce minimum spacing between labels in the same tier by nudging and tiering up
        axis_span = max(max_x - min_x, 1e-6)
        min_dx = 0.02 * axis_span  # require >=2% of axis width spacing
        max_attempts = MAX_TIERS
        tier_idx = 0
        while max_attempts > 0:
            slots = left_slots if side == 'left' else right_slots
            occupied = slots[tier_idx]
            conflict = any((label_x_num >= a and label_x_num <= b) or (a >= label_x_num and a <= label_x_num + 0.0) for (a, b) in occupied)
            if not conflict:
                # reserve a small range around label_x_num
                occupied.append((label_x_num - min_dx / 2.0, label_x_num + min_dx / 2.0))
                break
            # nudge outward and try next tier
            nudge = min_dx * (1.0 + 0.3 * tier_idx)
            if side == 'left':
                label_x_num = max(label_x_num - nudge, min_x)
            else:
                label_x_num = min(label_x_num + nudge, max_x)
            tier_idx = (tier_idx + 1) % MAX_TIERS
            max_attempts -= 1

        # Map possibly nudged numeric label_x back to datetime if needed
        if use_datetime_axis and base_ts_naive is not None:
            label_x = base_ts_naive + pd.to_timedelta(label_x_num, unit="s")

        # Place label above local pulse max to avoid overlapping the plotted line
        try:
            local_max = float(np.max(vals)) if len(vals) > 0 else 0.0
        except Exception:
            local_max = 0.0
        ax = plt.gca()
        ylim_lo, ylim_hi = ax.get_ylim()
        gap = max(ylim_hi - ylim_lo, 1e-6)
        label_y = local_max + 0.08 * gap
        # Apply vertical tiering based on assigned tier index
        tier_offset = tier_idx * (0.06 * gap)
        label_y += tier_offset
        # Ensure label_y is within view; expand ylim if needed
        if label_y > ylim_hi * 0.995:
            ax.set_ylim(ylim_lo, label_y + 0.1 * gap)

        # Arrow target: center of pulse window
        if use_datetime_axis and base_ts_naive is not None:
            try:
                x_center = x_start + (x_end - x_start) / 2
            except Exception:
                x_center = x_start
        else:
            x_center = (pulse_start + pulse_end) / 2.0

        # Minimal in-plot tag at pulse center; place well above peak to avoid overlap
        try:
            inner_x = x_center
            ax_inner = plt.gca()
            ylim_lo_inner, ylim_hi_inner = ax_inner.get_ylim()
            gap_inner = max(ylim_hi_inner - ylim_lo_inner, 1e-6)
            # Offset the label significantly above the local peak
            inner_y = local_max + 0.15 * gap_inner
            # Ensure label is within view; expand ylim if needed
            if inner_y > ylim_hi_inner * 0.995:
                ax_inner.set_ylim(ylim_lo_inner, inner_y + 0.08 * gap_inner)
            plt.text(inner_x, inner_y, f"P{idx}", ha='center', va='bottom', fontsize=6,
                     color='black', backgroundcolor='white', alpha=0.8)
        except Exception:
            pass

        # Append row for Excel
        row = {
            "Order": f"Pulse {idx}",
            "PulseIndex": int(idx),
            "Pulse Start (s)": float(pulse_start),
            "Pulse End (s)": float(pulse_end),
            "Duration (s)": float(dur_s),
            "Net Force (g·s)": float(net_f),
            "Peak Force (g)": float(peak_g),
            "# peaks": int(n_vibs) if isinstance(n_vibs, (int, np.integer)) else int(n_vibs) if str(n_vibs).isdigit() else n_vibs,
            "SpeedSetting": str(speed_setting),
            "SourceFile": os.path.basename(filepath),
        }
        if use_datetime_axis and base_ts_naive is not None:
            try:
                row["Pulse Start ts"] = (base_ts_naive + pd.to_timedelta(pulse_start, unit="s")).isoformat()
                row["Pulse End ts"] = (base_ts_naive + pd.to_timedelta(pulse_end, unit="s")).isoformat()
            except Exception:
                pass
        pulse_rows.append(row)

    ###################################################################

    # Axis formatting and labels
    date_label = None
    if use_datetime_axis and base_ts_naive is not None:
        ax = plt.gca()
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        try:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%-I:%M%p"))
        except Exception:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%I:%M%p"))
        plt.xlabel("Local Time")
        if earliest_start_ts is not None:
            try:
                try:
                    earliest_naive = earliest_start_ts.tz_localize(None)
                except Exception:
                    earliest_naive = earliest_start_ts
                day_index = (base_ts_naive.date() - earliest_naive.date()).days + 1
            except Exception:
                day_index = 1
        else:
            day_index = 1
        date_label = f"{base_ts_naive.strftime('%m/%d/%y')}, Day {day_index}"
    else:
        plt.xlabel("Time (seconds)")

    title_extra = f" | {date_label}" if date_label else ""
    plt.title(f"Vibration Intensity vs Local Time | Speed Settings: {speed_setting}{title_extra}")
    plt.ylabel("Acceleration (g)")

    # Reorder legend
    handles, labels = plt.gca().get_legend_handles_labels()
    label_order = [
        "Vibration Stimulus (g)",
        "X Accel",
        "Y Accel",
        "Z Accel (adj. for gravity)",
        "Stimulus Window"
    ]
    # Sort them in the desired order
    ordered = sorted(zip(labels, handles), key=lambda x: label_order.index(x[0]) if x[0] in label_order else 999)
    labels, handles = zip(*ordered)
    plt.legend(handles, labels, loc="lower left", fontsize="x-small", ncol=1, frameon=True)

    # Table removed from plot by request

    plt.tight_layout()
    plt.grid(True, linestyle="--", alpha=0.5)

    # Save plot
    plt.savefig(plotname, dpi=300)
    plt.close()
    print(f"Plot saved: {plotname}")

    # Write Pulse Metrics Excel alongside the PNG
    try:
        results_dir = os.path.join(os.path.dirname(filepath), "Results")
        os.makedirs(results_dir, exist_ok=True)
        base_name = os.path.basename(filepath)
        # Handle both .csv and .csv.gz extensions
        if base_name.lower().endswith('.csv.gz'):
            base_noext = base_name[:-7]  # Remove .csv.gz
        else:
            base_noext = os.path.splitext(base_name)[0]  # Remove .csv
        out_xlsx = os.path.join(results_dir, f"Pulse_Metrics_{base_noext}.xlsx")

        # Build DataFrame (ensure headers exist even if empty)
        if pulse_rows:
            out_df = pd.DataFrame(pulse_rows)
        else:
            out_df = pd.DataFrame(columns=[
                "Order",
                "PulseIndex",
                "Pulse Start (s)",
                "Pulse End (s)",
                "Duration (s)",
                "Net Force (g·s)",
                "Peak Force (g)",
                "# peaks",
                "Frequency (Hz)",
                "SpeedSetting",
                "SourceFile",
                "Pulse Start ts",
                "Pulse End ts",
            ])

        # Append summary rows (Avg, Std dv) and include numeric PulseIndex
        try:
            # Ensure numeric types for calculations
            for col in ["Duration (s)", "Net Force (g·s)", "Peak Force (g)", "# peaks"]:
                if col in out_df.columns:
                    out_df[col] = pd.to_numeric(out_df[col], errors="coerce")
            if "PulseIndex" not in out_df.columns:
                # Derive from 'Order' if missing
                try:
                    out_df["PulseIndex"] = out_df["Order"].astype(str).str.extract(r"(\d+)").astype(float)
                except Exception:
                    out_df["PulseIndex"] = np.nan

            avg_row = {
                "Order": "Avg",
                "PulseIndex": np.nan,
                "Pulse Start (s)": np.nan,
                "Pulse End (s)": np.nan,
                "Duration (s)": float(out_df["Duration (s)"].mean(skipna=True)) if "Duration (s)" in out_df else np.nan,
                "Net Force (g·s)": float(out_df["Net Force (g·s)"].mean(skipna=True)) if "Net Force (g·s)" in out_df else np.nan,
                "Peak Force (g)": float(out_df["Peak Force (g)"].mean(skipna=True)) if "Peak Force (g)" in out_df else np.nan,
                "# peaks": float(out_df["# peaks"].mean(skipna=True)) if "# peaks" in out_df else np.nan,
                "SpeedSetting": str(speed_setting),
                "SourceFile": os.path.basename(filepath),
            }
            std_row = {
                "Order": "Std dv",
                "PulseIndex": np.nan,
                "Pulse Start (s)": np.nan,
                "Pulse End (s)": np.nan,
                "Duration (s)": float(out_df["Duration (s)"].std(skipna=True, ddof=1)) if "Duration (s)" in out_df and out_df["Duration (s)"].count() > 1 else np.nan,
                "Net Force (g·s)": float(out_df["Net Force (g·s)"].std(skipna=True, ddof=1)) if "Net Force (g·s)" in out_df and out_df["Net Force (g·s)"].count() > 1 else np.nan,
                "Peak Force (g)": float(out_df["Peak Force (g)"].std(skipna=True, ddof=1)) if "Peak Force (g)" in out_df and out_df["Peak Force (g)"].count() > 1 else np.nan,
                "# peaks": float(out_df["# peaks"].std(skipna=True, ddof=1)) if "# peaks" in out_df and out_df["# peaks"].count() > 1 else np.nan,
                "SpeedSetting": str(speed_setting),
                "SourceFile": os.path.basename(filepath),
            }
            out_df = pd.concat([out_df, pd.DataFrame([avg_row, std_row])], ignore_index=True)
        except Exception:
            pass

        # Add frequency column immediately after '# peaks': Frequency = peaks / duration
        try:
            if "# peaks" in out_df.columns and "Duration (s)" in out_df.columns:
                peaks_num = pd.to_numeric(out_df["# peaks"], errors="coerce")
                dur_num = pd.to_numeric(out_df["Duration (s)"], errors="coerce")
                freq_hz = peaks_num / dur_num.replace(0, np.nan)
                out_df["Frequency (Hz)"] = freq_hz
                peaks_idx = out_df.columns.get_loc("# peaks")
                ordered_cols = [c for c in out_df.columns if c != "Frequency (Hz)"]
                ordered_cols.insert(peaks_idx + 1, "Frequency (Hz)")
                out_df = out_df[ordered_cols]
        except Exception:
            pass

        # Write Excel (let pandas choose engine)
        with pd.ExcelWriter(out_xlsx, engine=None) as writer:
            out_df.to_excel(writer, index=False, sheet_name="Pulses")
        print(f"Pulse metrics saved: {out_xlsx}")
    except Exception as e:
        print(f"Failed to write Pulse metrics Excel ({e}). Attempting CSV fallback...")
        try:
            csv_fallback = re.sub(r"\\.xlsx$", ".csv", out_xlsx, flags=re.IGNORECASE)
            out_df.to_csv(csv_fallback, index=False)
            print(f"Pulse metrics saved (CSV): {csv_fallback}")
        except Exception as e2:
            print(f"Failed to write Pulse metrics CSV: {e2}")
################################################################################
# Main Script
################################################################################

if __name__ == "__main__":
    import argparse
    from services.pulse_service import run_pulse_analysis

    parser = argparse.ArgumentParser(
        description="Convert acceleration log CSVs to vibration intensity plots."
    )
    parser.add_argument("data_dir", help="Directory containing CSV files to process")
    parser.add_argument("--n-max", type=int, default=10,
                        help="Maximum number of top peaks to highlight (default: 10)")
    parser.add_argument("--n-mins-bucket", type=int, default=5,
                        help="Minimum spacing in minutes between highlighted peaks (default: 5)")
    parser.add_argument("--min-pulse-gs", type=float, default=0.0000005,
                        help="Minimum integrated area (g·s) to qualify a pulse (default: 0.0000005)")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Manual threshold (g) for pulse detection. If not specified, uses adaptive thresholding.")
    parser.add_argument("--force-all", action="store_true",
                        help="Retained for CLI compatibility; aggregated GUI service always recomputes outputs.")

    args = parser.parse_args()
    output_dir = Path(args.data_dir).resolve() / 'aggregated_results'
    result = run_pulse_analysis(
        args.data_dir,
        output_dir,
        n_max=args.n_max,
        n_mins_bucket=args.n_mins_bucket,
        min_pulse_gs=args.min_pulse_gs,
        threshold=args.threshold,
    )
    print(f"Aggregated plot: {result.aggregated_plot}")
    print(f"Aggregated workbook: {result.aggregated_workbook}")
    print(f"Zip bundle: {result.zip_path}")
