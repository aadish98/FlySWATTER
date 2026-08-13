#!/usr/bin/env python3

import os
import json
import gc
from pathlib import Path
import ctypes
import numpy as np
import pandas as pd
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


# numpy 2.0 renamed trapz to trapezoid; keep both working.
_trapezoid = getattr(np, "trapezoid", None) or np.trapz


def calculate_baseline_adjusted_area(time_s, signal, baseline: float) -> float:
    """Integrate excess acceleration above a local baseline, in g·s."""
    if time_s is None or signal is None:
        return 0.0
    time_arr = np.asarray(time_s, dtype=float)
    signal_arr = np.asarray(signal, dtype=float)
    if time_arr.size < 2 or signal_arr.size < 2:
        return 0.0
    excess = np.clip(signal_arr - float(baseline), 0.0, None)
    return float(_trapezoid(excess, time_arr))


def count_prominent_peaks(signal, time_s, prominence: float, min_distance_s: float = 0.05) -> int:
    """Count local maxima with explicit prominence and temporal separation."""
    signal_arr = np.asarray(signal, dtype=float)
    time_arr = np.asarray(time_s, dtype=float)
    if signal_arr.size < 3 or time_arr.size < 3:
        return 0
    dt = float(np.median(np.diff(time_arr))) if time_arr.size > 1 else 0.001
    distance = max(int(round(min_distance_s / max(dt, 1e-6))), 1)
    peaks, _ = find_peaks(signal_arr, prominence=max(float(prominence), 0.0), distance=distance)
    return int(len(peaks))


def _robust_center_and_mad(values) -> tuple[float, float]:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return 0.0, 0.002
    median_val = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median_val)))
    if mad < 1e-6:
        mad = 0.002
    keep = np.abs(arr - median_val) <= (6.0 * mad)
    if int(keep.sum()) >= 50 and int(keep.sum()) < arr.size:
        trimmed = arr[keep]
        median_val = float(np.median(trimmed))
        mad = float(np.median(np.abs(trimmed - median_val)))
        if mad < 1e-6:
            mad = 0.002
    return median_val, mad


def estimate_local_baseline(
    abs_time,
    signal,
    window_start,
    window_end,
    baseline_seconds: float = 60.0,
    exclude_spans=None,
    restrict_mask=None,
):
    """Estimate quiet baseline immediately before a scheduled window.

    Any other expected pulse windows are excluded so a neighbouring stimulus
    cannot inflate the baseline used here. `restrict_mask` keeps the estimate
    within one log file, since each file is offset-corrected independently and
    its noise floor shifts at the boundary.
    """
    abs_index = pd.to_datetime(pd.Series(abs_time))
    signal_arr = np.asarray(signal, dtype=float)
    window_start_ts = pd.Timestamp(window_start)
    window_end_ts = pd.Timestamp(window_end)
    pre_start = window_start_ts - pd.Timedelta(seconds=baseline_seconds)
    pre_mask = (abs_index >= pre_start) & (abs_index < window_start_ts)
    pre_mask = _apply_exclusions(pre_mask, abs_index, exclude_spans, keep_span=(window_start_ts, window_end_ts))
    if restrict_mask is not None:
        pre_mask = pre_mask & pd.Series(np.asarray(restrict_mask, dtype=bool), index=pre_mask.index)
    pre_vals = signal_arr[pre_mask.to_numpy()]
    if pre_vals.size >= 50:
        return _robust_center_and_mad(pre_vals)
    win_mask = (abs_index >= window_start_ts) & (abs_index <= window_end_ts)
    win_vals = signal_arr[win_mask.to_numpy()]
    if win_vals.size == 0:
        return 0.0, 0.002
    cutoff = np.percentile(win_vals, 40)
    quiet = win_vals[win_vals <= cutoff]
    if quiet.size < 10:
        quiet = win_vals
    return _robust_center_and_mad(quiet)


def _apply_exclusions(mask, abs_index, exclude_spans, keep_span=None):
    """Drop samples that fall inside any excluded span."""
    if not exclude_spans:
        return mask
    for span_start, span_end in exclude_spans:
        if keep_span is not None and (span_start, span_end) == keep_span:
            continue
        mask = mask & ~((abs_index >= span_start) & (abs_index <= span_end))
    return mask


def summarize_background_peaks(
    abs_time,
    signal,
    window_start,
    window_end,
    *,
    exclude_spans=None,
    restrict_mask=None,
    context_minutes: float = 20.0,
    min_height: float = 0.0,
    min_separation_s: float = 5.0,
):
    """Describe peak amplitudes surrounding a window, ignoring expected windows.

    Returns (count, p99, max). This is what tells us whether an in-window peak
    is actually a stimulus or just the largest sample of an ongoing periodic
    background, which a window-local threshold alone cannot distinguish.
    """
    abs_index = pd.to_datetime(pd.Series(abs_time))
    signal_arr = np.asarray(signal, dtype=float)
    window_start_ts = pd.Timestamp(window_start)
    window_end_ts = pd.Timestamp(window_end)
    context = pd.Timedelta(minutes=context_minutes)
    ctx_mask = (abs_index >= window_start_ts - context) & (abs_index <= window_end_ts + context)
    ctx_mask = ctx_mask & ~((abs_index >= window_start_ts) & (abs_index <= window_end_ts))
    ctx_mask = _apply_exclusions(ctx_mask, abs_index, exclude_spans)
    if restrict_mask is not None:
        same_file = ctx_mask & pd.Series(np.asarray(restrict_mask, dtype=bool), index=ctx_mask.index)
        if int(same_file.sum()) >= 100:
            ctx_mask = same_file
    ctx_vals = signal_arr[ctx_mask.to_numpy()]
    if ctx_vals.size < 100:
        return 0, float("nan"), float("nan")
    ctx_times = abs_index[ctx_mask]
    elapsed = (ctx_times - ctx_times.iloc[0]).dt.total_seconds().to_numpy(dtype=float)
    dt = float(np.median(np.diff(elapsed))) if elapsed.size > 1 else 0.001
    distance = max(int(round(min_separation_s / max(dt, 1e-6))), 1)
    peaks, props = find_peaks(ctx_vals, height=max(min_height, 0.0), distance=distance)
    if peaks.size == 0:
        return 0, float("nan"), float("nan")
    heights = np.asarray(props["peak_heights"], dtype=float)
    return int(heights.size), float(np.percentile(heights, 99)), float(np.max(heights))


def _threshold_runs(time_s, signal, threshold: float):
    time_arr = np.asarray(time_s, dtype=float)
    signal_arr = np.asarray(signal, dtype=float)
    above = signal_arr >= float(threshold)
    if not above.any():
        return []
    padded = np.concatenate([[False], above, [False]])
    delta = np.diff(padded.astype(int))
    starts = np.where(delta == 1)[0]
    ends = np.where(delta == -1)[0] - 1
    return [(float(time_arr[start]), float(time_arr[end])) for start, end in zip(starts, ends)]


def _expand_run_edges(time_s, signal, start_idx: int, end_idx: int, expand_thr: float, max_extend_s: float = 3.0, quiet_s: float = 0.08):
    """Extend a detected run slightly to capture the oscillatory tail."""
    time_arr = np.asarray(time_s, dtype=float)
    signal_arr = np.asarray(signal, dtype=float)
    last_above = int(start_idx)
    for idx in range(int(start_idx) - 1, -1, -1):
        if time_arr[start_idx] - time_arr[idx] > max_extend_s:
            break
        if signal_arr[idx] >= expand_thr:
            last_above = idx
        elif time_arr[last_above] - time_arr[idx] >= quiet_s:
            break
    start_idx = last_above
    last_above = int(end_idx)
    for idx in range(int(end_idx) + 1, signal_arr.size):
        if time_arr[idx] - time_arr[end_idx] > max_extend_s:
            break
        if signal_arr[idx] >= expand_thr:
            last_above = idx
        elif time_arr[idx] - time_arr[last_above] >= quiet_s:
            break
    return start_idx, last_above


def _merge_intervals(intervals, max_gap: float):
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda item: item[0])
    merged = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        if start - merged[-1][1] <= max_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def quantify_scheduled_pulses(
    df: pd.DataFrame,
    windows,
    *,
    baseline_seconds: float = 60.0,
    min_duration_s: float = 0.08,
    cluster_gap_s: float = 2.5,
    min_area_gs: float = 0.0008,
    min_abs_prominence: float = 0.010,
    noise_sigma_factor: float = 4.0,
    peak_min_distance_s: float = 0.05,
    context_minutes: float = 20.0,
    background_margin: float = 1.25,
    manual_threshold: float | None = None,
):
    """Quantify at most one pulse inside each expected time window.

    Uses the immediate pre-window baseline rather than a global magnitude
    threshold, groups nearby fragments, and returns "not detected" when no
    candidate clears local prominence/duration/area checks.

    Each result also carries a background comparison drawn from the surrounding
    recording, because a window-local threshold cannot by itself tell a real
    stimulus apart from a periodic artifact that runs through the window.
    """
    if df is None or df.empty or "Vibration_Accel" not in df.columns:
        return []
    if "AbsoluteTime" not in df.columns:
        raise ValueError("Scheduled pulse quantification requires AbsoluteTime.")

    work = df[["AbsoluteTime", "Vibration_Accel"]].copy()
    if "SourceFile" in df.columns:
        work["SourceFile"] = df["SourceFile"]
    work = work.sort_values("AbsoluteTime").reset_index(drop=True)
    abs_time = pd.to_datetime(work["AbsoluteTime"])
    if getattr(abs_time.dt, "tz", None) is not None:
        abs_time = pd.to_datetime(abs_time.dt.strftime("%Y-%m-%d %H:%M:%S.%f"))
    work["AbsoluteTime"] = abs_time
    signal = work["Vibration_Accel"].to_numpy(dtype=float)
    origin = abs_time.iloc[0]
    time_s = (abs_time - origin).dt.total_seconds().to_numpy(dtype=float)

    parsed_windows = []
    for window in windows or []:
        if hasattr(window, "start_iso"):
            window_start = pd.Timestamp(window.start_iso)
            window_end = pd.Timestamp(window.end_iso)
        else:
            window_start = pd.Timestamp(window["start_iso"])
            window_end = pd.Timestamp(window["end_iso"])
        if window_start.tz is not None:
            window_start = pd.Timestamp(window_start.to_pydatetime().replace(tzinfo=None))
        if window_end.tz is not None:
            window_end = pd.Timestamp(window_end.to_pydatetime().replace(tzinfo=None))
        parsed_windows.append((window_start, window_end))

    results = []
    for index, (window_start, window_end) in enumerate(parsed_windows, start=1):
        win_mask = (abs_time >= window_start) & (abs_time <= window_end)
        win_time = time_s[win_mask.to_numpy()]
        win_signal = signal[win_mask.to_numpy()]
        source_file = ""
        same_source = None
        if "SourceFile" in work.columns and win_mask.any():
            source_file = str(work.loc[win_mask, "SourceFile"].iloc[0])
            same_source = (work["SourceFile"] == source_file).to_numpy()

        baseline, mad = estimate_local_baseline(
            abs_time,
            signal,
            window_start,
            window_end,
            baseline_seconds=baseline_seconds,
            exclude_spans=parsed_windows,
            restrict_mask=same_source,
        )
        noise_sigma = 1.4826 * mad
        local_threshold = baseline + max(noise_sigma_factor * noise_sigma, min_abs_prominence)
        threshold = float(manual_threshold) if manual_threshold is not None else local_threshold
        peak_prominence = max(3.0 * noise_sigma, 0.008)

        detected = False
        pulse_start_ts = None
        pulse_end_ts = None
        offset_start_s = np.nan
        offset_end_s = np.nan
        duration_s = 0.0
        peak_force = 0.0
        area = 0.0
        n_peaks = 0
        frequency = 0.0

        if win_time.size >= 3:
            peak_idx = int(np.argmax(win_signal))
            peak_force = float(win_signal[peak_idx])
            peak_time = float(win_time[peak_idx])
            if peak_force >= threshold and peak_force - baseline >= min_abs_prominence:
                runs = _merge_intervals(_threshold_runs(win_time, win_signal, threshold), cluster_gap_s)
                chosen = next((run for run in runs if run[0] <= peak_time <= run[1]), None)
                if chosen is None and runs:
                    chosen = min(runs, key=lambda run: min(abs(peak_time - run[0]), abs(peak_time - run[1])))
                    if min(abs(peak_time - chosen[0]), abs(peak_time - chosen[1])) > 0.25:
                        chosen = None
                if chosen is not None:
                    cluster_mask = (win_time >= chosen[0]) & (win_time <= chosen[1])
                    cluster_idxs = np.flatnonzero(cluster_mask)
                    if cluster_idxs.size:
                        start_idx = int(cluster_idxs[0])
                        end_idx = int(cluster_idxs[-1])
                        expand_thr = baseline + max(2.5 * noise_sigma, 0.012)
                        expand_thr = min(expand_thr, threshold)
                        start_idx, end_idx = _expand_run_edges(
                            win_time, win_signal, start_idx, end_idx, expand_thr
                        )
                        start_s = float(win_time[start_idx])
                        end_s = float(win_time[end_idx])
                        cluster_time = win_time[start_idx : end_idx + 1]
                        cluster_signal = win_signal[start_idx : end_idx + 1]
                        duration_s = float(end_s - start_s)
                        area = calculate_baseline_adjusted_area(cluster_time, cluster_signal, baseline)
                        if duration_s >= min_duration_s and area >= min_area_gs:
                            detected = True
                            pulse_start_ts = origin + pd.to_timedelta(start_s, unit="s")
                            pulse_end_ts = origin + pd.to_timedelta(end_s, unit="s")
                            offset_start_s = (pulse_start_ts - window_start).total_seconds()
                            offset_end_s = (pulse_end_ts - window_start).total_seconds()
                            n_peaks = count_prominent_peaks(
                                cluster_signal, cluster_time, peak_prominence, peak_min_distance_s
                            )
                            frequency = float(n_peaks) / float(max(duration_s, 1e-6))
                            if "SourceFile" in work.columns:
                                source_idx = work.index[win_mask][peak_idx]
                                source_file = str(work.loc[source_idx, "SourceFile"])

        bg_count, bg_p99, bg_max = summarize_background_peaks(
            abs_time,
            signal,
            window_start,
            window_end,
            exclude_spans=parsed_windows,
            restrict_mask=same_source,
            context_minutes=context_minutes,
            min_height=baseline + max(2.0 * noise_sigma, 0.5 * min_abs_prominence),
        )
        # Compare excursions above baseline, not raw peak heights, so the
        # comparison is not dominated by a baseline offset. Clearing the
        # background maximum by a hair is not evidence of a stimulus, hence
        # the margin requirement.
        background_ratio = float("nan")
        if detected and np.isfinite(bg_p99):
            bg_excess = max(bg_p99 - baseline, 1e-9)
            background_ratio = (peak_force - baseline) / bg_excess
        if not detected or not np.isfinite(bg_max):
            background_check = "no comparison"
        elif peak_force > bg_max and background_ratio >= background_margin:
            background_check = "distinct"
        elif peak_force > bg_p99:
            background_check = "marginal"
        else:
            background_check = "matches background"

        results.append(
            {
                "Order": f"Pulse {index}",
                "PulseIndex": index,
                "Detection Status": "detected" if detected else "not detected",
                "Background Check": background_check,
                "Expected Start ts": window_start.isoformat(),
                "Expected End ts": window_end.isoformat(),
                "Pulse Start ts": pulse_start_ts.isoformat() if pulse_start_ts is not None else "",
                "Pulse End ts": pulse_end_ts.isoformat() if pulse_end_ts is not None else "",
                "Pulse Start (s from window)": float(offset_start_s) if detected else np.nan,
                "Pulse End (s from window)": float(offset_end_s) if detected else np.nan,
                "Duration (s)": float(duration_s) if detected else np.nan,
                "Baseline (g)": float(baseline),
                "Threshold (g)": float(threshold),
                "Baseline-Adjusted Area (g·s)": float(area) if detected else np.nan,
                "Peak Force (g)": float(peak_force) if detected else np.nan,
                "Background Peak p99 (g)": float(bg_p99),
                "Background Peak Max (g)": float(bg_max),
                "Background Peak Count": int(bg_count),
                "Peak / Background p99": float(background_ratio),
                "# peaks": int(n_peaks) if detected else np.nan,
                "Frequency (Hz)": float(frequency) if detected else np.nan,
                "SourceFile": source_file,
                "detected": detected,
                "expected_start": window_start,
                "expected_end": window_end,
                "pulse_start": pulse_start_ts,
                "pulse_end": pulse_end_ts,
            }
        )
    return results


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
    parser.add_argument("--threshold", type=float, default=None,
                        help="Manual threshold (g) for pulse detection. If not specified, uses adaptive thresholding.")
    parser.add_argument(
        "--pulse-window",
        action="append",
        default=[],
        metavar="START_ISO,END_ISO",
        help="Expected pulse window as START_ISO,END_ISO. Repeat once per scheduled pulse.",
    )
    parser.add_argument("--force-all", action="store_true",
                        help="Retained for CLI compatibility; aggregated GUI service always recomputes outputs.")

    args = parser.parse_args()
    pulse_windows = []
    for raw_window in args.pulse_window:
        parts = [part.strip() for part in str(raw_window).split(",")]
        if len(parts) != 2:
            raise ValueError(f"Invalid --pulse-window value: {raw_window}")
        pulse_windows.append({"start_iso": parts[0], "end_iso": parts[1]})
    output_dir = Path(args.data_dir).resolve() / 'aggregated_results'
    result = run_pulse_analysis(
        args.data_dir,
        output_dir,
        n_max=args.n_max,
        n_mins_bucket=args.n_mins_bucket,
        threshold=args.threshold,
        pulse_windows=pulse_windows,
    )
    print(f"Aggregated plot: {result.aggregated_plot}")
    print(f"Aggregated workbook: {result.aggregated_workbook}")
    print(f"Zip bundle: {result.zip_path}")
