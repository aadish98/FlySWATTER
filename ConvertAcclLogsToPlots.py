#!/usr/bin/env python3

import os
import json
import gc
from pathlib import Path
import ctypes
from dataclasses import dataclass
from typing import List, Optional
import numpy as np
import pandas as pd
from scipy.ndimage import median_filter
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
    data = pd.read_csv(filepath, usecols=["X", "Y", "Z"])
    return median_centered_offsets_from_frame(data, V_ref=V_ref, sensitivity=sensitivity)


def median_centered_offsets_from_frame(data, V_ref=3.0, sensitivity=0.3):
    """Offsets for an already-loaded frame, so callers need not re-read the file."""
    # Convert ADC values to voltage
    x_voltage = data["X"] * V_ref / 1023.0
    y_voltage = data["Y"] * V_ref / 1023.0
    z_voltage = data["Z"] * V_ref / 1023.0

    # Calculate offsets as the median voltage (this centers the median on 0)
    X_offset = x_voltage.median()
    Y_offset = y_voltage.median()
    # For Z, add +sensitivity to counter the +1g added in plotting so median -> 0
    Z_offset = z_voltage.median() + sensitivity

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

# Envelope amplitudes below this are treated as zero spread, which only happens
# on synthetic constant signals.
_MIN_SCALE_G = 1e-6
# Granularity of the noise-floor tracker. One second is far longer than any
# stimulus and far shorter than the drift it has to follow.
_FLOOR_BLOCK_SECONDS = 1.0


def rms_envelope(signal, times_ns, window_s: float = 0.025) -> np.ndarray:
    """Centred RMS amplitude of `signal` over a `window_s` stretch of tape.

    A vibration stimulus is an oscillation: the trace swings back through its
    own baseline every few milliseconds, so no level threshold applied to the
    raw trace can say where a burst begins or ends. Its runs above any
    threshold are single cycles a few milliseconds long. The RMS envelope
    collapses that oscillation into the amplitude curve the eye follows on the
    plot, and every decision below is made on that curve instead.

    The window is measured in time rather than in samples. The logger stamps
    whole milliseconds while sampling faster than 1 kHz, so any sample-rate
    estimate lands on one of two neighbouring values depending on how much data
    was handed in, and a window counted in samples would quietly change width
    with the size of the frame.
    """
    values = np.asarray(signal, dtype=float)
    stamps = np.asarray(times_ns, dtype=np.int64)
    if values.size < 2:
        return np.abs(values)
    half = max(int(window_s * 1e9) // 2, 1)
    low = np.searchsorted(stamps, stamps - half, side="left")
    high = np.searchsorted(stamps, stamps + half, side="right")
    cumulative = np.concatenate(([0.0], np.cumsum(np.square(values))))
    return np.sqrt((cumulative[high] - cumulative[low]) / (high - low))


def _sampling_interval(time_s) -> float:
    """Seconds per sample, tolerant of the logger's tied millisecond stamps."""
    arr = np.asarray(time_s, dtype=float)
    if arr.size < 2:
        return 0.001
    diffs = np.diff(arr)
    positive = diffs[diffs > 0]
    if positive.size == 0:
        return 0.001
    return float(np.median(positive))


def _block_starts(times_ns, block_seconds: float = _FLOOR_BLOCK_SECONDS) -> np.ndarray:
    """Offsets where each whole-clock-second block of samples begins.

    Blocks are cut on the recording's own clock rather than by sample count, so
    a given second of tape always falls in the same block however much of the
    run was handed in.
    """
    stamps = np.asarray(times_ns, dtype=np.int64)
    if stamps.size == 0:
        return np.empty(0, dtype=np.int64)
    block_id = stamps // max(int(block_seconds * 1e9), 1)
    return np.flatnonzero(np.concatenate(([True], block_id[1:] != block_id[:-1])))


def estimate_noise_floor(envelope, times_ns, floor_window_s: float = 120.0):
    """Track the slowly drifting noise floor of an envelope, and its spread.

    Ambient vibration changes over a multi-day recording as incubators cycle
    and people move around the room, and an expected window can be an hour
    wide, so a single baseline measured just before the window describes the
    wrong stretch of recording. A rolling median follows the drift, and a
    stimulus never moves it because a burst lasts seconds out of the minutes
    each median is taken over.

    Blocks are cut on the recording's own clock rather than by sample count, so
    a given second of tape always forms the same block. That is what lets one
    window quantified on its own agree with the same window quantified inside
    the whole run.

    Returns per-sample (floor, scale) arrays, where scale is a robust standard
    deviation derived from the interquartile range.
    """
    values = np.asarray(envelope, dtype=float)
    size = values.size
    if size == 0:
        return values.copy(), values.copy()
    starts = _block_starts(times_ns)
    ends = np.append(starts[1:], size)
    blocks = starts.size
    if blocks < 3:
        low, mid_value, high = np.percentile(values, [25, 50, 75])
        floor = np.full(size, float(mid_value))
        scale = np.full(size, max(float(high - low) / 1.349, _MIN_SCALE_G))
        return floor, scale

    quartiles = np.empty((blocks, 3), dtype=float)
    for position in range(blocks):
        quartiles[position] = np.percentile(
            values[starts[position] : ends[position]], (25, 50, 75)
        )
    mid = quartiles[:, 1]
    spread = (quartiles[:, 2] - quartiles[:, 0]) / 1.349
    span = max(int(round(floor_window_s / _FLOOR_BLOCK_SECONDS)) | 1, 3)
    if span < blocks:
        mid = median_filter(mid, size=span, mode="nearest")
        spread = median_filter(spread, size=span, mode="nearest")
    else:
        mid = np.full(blocks, float(np.median(mid)))
        spread = np.full(blocks, float(np.median(spread)))
    index = np.repeat(np.arange(blocks), ends - starts)
    return mid[index], np.maximum(spread, _MIN_SCALE_G)[index]


def find_burst_bounds(time_s, envelope, anchor: int, edge_level: float, bridge_gap_s: float):
    """Sample bounds of the burst containing `anchor`.

    Stretches separated by less than `bridge_gap_s` are one burst, since a
    motor ramp dips without stopping. That gap is deliberately short: bridging
    whole seconds chains a real stimulus to unrelated knocks either side of it,
    which is how a 300 ms pulse ends up reported as minutes long.
    """
    above = np.asarray(envelope, dtype=float) >= float(edge_level)
    anchor = int(anchor)
    if not above[anchor]:
        return anchor, anchor
    padded = np.concatenate(([False], above, [False]))
    delta = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(delta == 1)
    ends = np.flatnonzero(delta == -1) - 1
    time_arr = np.asarray(time_s, dtype=float)
    if starts.size > 1:
        gaps = time_arr[starts[1:]] - time_arr[ends[:-1]]
        group = np.concatenate(([0], np.cumsum(gaps > float(bridge_gap_s))))
    else:
        group = np.zeros(1, dtype=np.int64)
    which = int(np.searchsorted(starts, anchor, side="right")) - 1
    members = np.flatnonzero(group == group[which])
    return int(starts[members[0]]), int(ends[members[-1]])


def count_prominent_peaks(signal, time_s, prominence: float, min_distance_s: float = 0.002) -> int:
    """Count local maxima with explicit prominence and temporal separation."""
    signal_arr = np.asarray(signal, dtype=float)
    time_arr = np.asarray(time_s, dtype=float)
    if signal_arr.size < 3 or time_arr.size < 3:
        return 0
    dt = _sampling_interval(time_arr)
    distance = max(int(round(min_distance_s / max(dt, 1e-6))), 1)
    peaks, _ = find_peaks(signal_arr, prominence=max(float(prominence), 0.0), distance=distance)
    return int(len(peaks))


def _apply_exclusions(mask, abs_index, exclude_spans, keep_span=None):
    """Drop samples that fall inside any excluded span."""
    if not exclude_spans:
        return mask
    for span_start, span_end in exclude_spans:
        if keep_span is not None and (span_start, span_end) == keep_span:
            continue
        mask = mask & ~((abs_index >= span_start) & (abs_index <= span_end))
    return mask


def summarize_background_bursts(
    abs_time,
    excess,
    window_start,
    window_end,
    *,
    exclude_spans=None,
    burst_span=None,
    guard_seconds: float = 5.0,
    context_minutes: float = 20.0,
    level: float = 0.0,
):
    """How often the recording around a burst produces a burst of its own.

    Returns (count, p99, max) over per-block maxima of the envelope excess in
    the recording either side of the window, plus the rest of the window itself
    once the candidate burst and a guard around it are removed. Everything but
    the candidate counts as background, because a stimulus has to stand out
    from the stretch of recording it sits in, and a noisy quarter of an hour is
    usually noisy for the whole window rather than politely stopping at its
    edge. Other scheduled windows are dropped so a neighbouring stimulus is
    never mistaken for background. `count` is the number of blocks that would
    themselves have cleared this pulse's detection level.
    """
    abs_index = pd.to_datetime(pd.Series(abs_time))
    excess_arr = np.asarray(excess, dtype=float)
    window_start_ts = pd.Timestamp(window_start)
    window_end_ts = pd.Timestamp(window_end)
    context = pd.Timedelta(minutes=context_minutes)
    ctx_mask = (abs_index >= window_start_ts - context) & (abs_index <= window_end_ts + context)
    if burst_span is None:
        ctx_mask = ctx_mask & ~((abs_index >= window_start_ts) & (abs_index <= window_end_ts))
    else:
        guard = pd.Timedelta(seconds=guard_seconds)
        ctx_mask = ctx_mask & ~(
            (abs_index >= pd.Timestamp(burst_span[0]) - guard)
            & (abs_index <= pd.Timestamp(burst_span[1]) + guard)
        )
    ctx_mask = _apply_exclusions(
        ctx_mask, abs_index, exclude_spans, keep_span=(window_start_ts, window_end_ts)
    )
    keep = ctx_mask.to_numpy()
    ctx_vals = excess_arr[keep]
    starts = _block_starts(abs_index.to_numpy().astype("int64")[keep])
    if starts.size < 3:
        return 0, float("nan"), float("nan")
    block_max = np.maximum.reduceat(ctx_vals, starts)
    return (
        int(np.count_nonzero(block_max >= float(level))),
        float(np.percentile(block_max, 99)),
        float(np.max(block_max)),
    )


def _parse_window_bounds(windows):
    """Normalize window objects/dicts to naive (start, end) timestamp pairs."""
    parsed = []
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
        parsed.append((window_start, window_end))
    return parsed


@dataclass
class _SignalTracks:
    """A frame's vibration trace with the envelope and noise tracks derived from it."""

    abs_time: pd.Series
    signal: np.ndarray
    origin: pd.Timestamp
    time_s: np.ndarray
    sources: Optional[np.ndarray]
    envelope: np.ndarray
    floor: np.ndarray
    scale: np.ndarray
    excess: np.ndarray


def _build_signal_tracks(
    df: pd.DataFrame, *, envelope_window_s: float, floor_window_s: float, caller: str
) -> _SignalTracks:
    # A window that no log file covers still has to report a row, so an empty
    # frame is carried through instead of short-circuiting the call.
    if df is None or df.empty:
        df = pd.DataFrame(
            {
                "AbsoluteTime": pd.Series(dtype="datetime64[ns]"),
                "Vibration_Accel": pd.Series(dtype="float64"),
            }
        )
    if "Vibration_Accel" not in df.columns:
        raise ValueError(f"{caller} requires Vibration_Accel.")
    if "AbsoluteTime" not in df.columns:
        raise ValueError(f"{caller} requires AbsoluteTime.")

    work = df[["AbsoluteTime", "Vibration_Accel"]].copy()
    if "SourceFile" in df.columns:
        work["SourceFile"] = df["SourceFile"]
    # The logger stamps time in whole milliseconds while sampling faster than
    # 1 kHz, so a third of the samples tie. Only a stable sort keeps them in
    # acquisition order; an unstable one reorders ties differently depending on
    # how much data was handed in, which moves the reported areas and peak
    # counts around.
    work = work.sort_values("AbsoluteTime", kind="stable").reset_index(drop=True)
    abs_time = pd.to_datetime(work["AbsoluteTime"])
    if getattr(abs_time.dt, "tz", None) is not None:
        abs_time = pd.to_datetime(abs_time.dt.strftime("%Y-%m-%d %H:%M:%S.%f"))
    work["AbsoluteTime"] = abs_time
    signal = work["Vibration_Accel"].to_numpy(dtype=float)
    origin = abs_time.iloc[0] if abs_time.size else pd.Timestamp(0)
    time_s = (abs_time - origin).dt.total_seconds().to_numpy(dtype=float)
    sources = work["SourceFile"].to_numpy() if "SourceFile" in work.columns else None

    stamps = abs_time.to_numpy().astype("int64")
    envelope = rms_envelope(signal, stamps, envelope_window_s)
    floor_track, scale_track = estimate_noise_floor(envelope, stamps, floor_window_s)
    return _SignalTracks(
        abs_time=abs_time,
        signal=signal,
        origin=origin,
        time_s=time_s,
        sources=sources,
        envelope=envelope,
        floor=floor_track,
        scale=scale_track,
        excess=envelope - floor_track,
    )


def _measure_burst(
    *,
    signal,
    time_s,
    excess_track,
    start_idx: int,
    end_idx: int,
    noise_floor: float,
    sigma: float,
    peak_min_distance_s: float,
) -> dict:
    """Peak force, area, peak count and peak rate of one burst.

    Shared by the scheduled and the general detector so a burst is measured
    identically whichever way it was found.
    """
    span = slice(start_idx, end_idx + 1)
    duration_s = float(time_s[end_idx] - time_s[start_idx])
    peak_force = float(signal[span].max())
    area = float(_trapezoid(np.clip(excess_track[span], 0.0, None), time_s[span]))
    # Prominence scales with this burst, so the count means the same thing for
    # a faint pulse and a violent one.
    n_peaks = count_prominent_peaks(
        signal[span],
        time_s[span],
        max(0.25 * (peak_force - noise_floor), 2.0 * sigma),
        peak_min_distance_s,
    )
    return {
        "duration_s": duration_s,
        "peak_force": peak_force,
        "area": area,
        "n_peaks": int(n_peaks),
        "peak_rate": float(n_peaks) / max(duration_s, 1e-6),
    }


def _local_burst_bounds(time_s, envelope, anchor: int, edge_level: float, bridge_gap_s: float):
    """`find_burst_bounds` on a neighbourhood of the anchor instead of the frame.

    Walking the whole frame for every burst is quadratic on a busy recording.
    The neighbourhood doubles until the burst no longer touches its edges, so
    the answer is the one the whole-frame search gives.
    """
    size = len(envelope)
    half = max(int(round(30.0 / max(_sampling_interval(time_s[max(anchor - 50, 0) : anchor + 50]), 1e-6))), 100)
    while True:
        low = max(anchor - half, 0)
        high = min(anchor + half + 1, size)
        start, end = find_burst_bounds(
            time_s[low:high], envelope[low:high], anchor - low, edge_level, bridge_gap_s
        )
        touches_low = start == 0 and low > 0
        touches_high = end == high - low - 1 and high < size
        if not (touches_low or touches_high):
            return start + low, end + low
        half *= 2


def detect_all_pulses(
    df: pd.DataFrame,
    *,
    core_start=None,
    core_end=None,
    envelope_window_s: float = 0.025,
    floor_window_s: float = 120.0,
    detection_sigma_factor: float = 6.0,
    min_amplitude_g: float = 0.004,
    edge_fraction: float = 0.15,
    edge_sigma_factor: float = 3.0,
    bridge_gap_s: float = 0.05,
    min_duration_s: float = 0.04,
    peak_min_distance_s: float = 0.002,
):
    """Find and measure every vibration burst in `df`, with no schedule.

    Uses the scheduled detector's own envelope, noise floor, thresholds and
    burst measurements, but scans the whole frame rather than looking inside
    expected windows. Each stretch of envelope that clears the detection level
    is anchored at its strongest sample and extended to the burst's own edge
    level; bursts that end up overlapping are merged, and any shorter than
    `min_duration_s` are dropped as glitches.

    `df` should reach beyond [core_start, core_end) so the noise floor is
    well-defined at its edges. Only bursts whose onset lies inside that span
    are returned, which lets a long recording be cut into tiles without a burst
    being counted by two of them. Returns dicts keyed like the scheduled rows,
    in time order, with PulseIndex numbered from 1 within this call.
    """
    tracks = _build_signal_tracks(
        df,
        envelope_window_s=envelope_window_s,
        floor_window_s=floor_window_s,
        caller="Pulse detection",
    )
    envelope, floor_track, scale_track, excess = (
        tracks.envelope,
        tracks.floor,
        tracks.scale,
        tracks.excess,
    )
    time_s = tracks.time_s
    if envelope.size < 3:
        return []

    detect_excess = np.maximum(detection_sigma_factor * scale_track, min_amplitude_g)
    above = excess >= detect_excess
    padded = np.concatenate(([False], above, [False]))
    delta = np.diff(padded.astype(np.int8))
    run_starts = np.flatnonzero(delta == 1)
    run_ends = np.flatnonzero(delta == -1) - 1

    # (start, end, anchor) per burst, merged on the fly while walking in order.
    bursts: List[List[int]] = []
    last_end = -1
    for run_start, run_end in zip(run_starts, run_ends):
        if run_start <= last_end:
            continue
        anchor = int(run_start + np.argmax(excess[run_start : run_end + 1]))
        sigma = float(scale_track[anchor])
        peak_excess = float(excess[anchor])
        edge_level = float(floor_track[anchor]) + max(edge_fraction * peak_excess, edge_sigma_factor * sigma)
        edge_level = min(edge_level, float(envelope[anchor]))
        start_idx, end_idx = _local_burst_bounds(time_s, envelope, anchor, edge_level, bridge_gap_s)
        last_end = max(last_end, end_idx)
        if float(time_s[end_idx] - time_s[start_idx]) < min_duration_s:
            continue
        if bursts and start_idx <= bursts[-1][1]:
            previous = bursts[-1]
            previous[1] = max(previous[1], end_idx)
            if excess[anchor] > excess[previous[2]]:
                previous[2] = anchor
        else:
            bursts.append([start_idx, end_idx, anchor])

    keep_start = pd.Timestamp(core_start) if core_start is not None else None
    keep_end = pd.Timestamp(core_end) if core_end is not None else None
    results = []
    for start_idx, end_idx, anchor in bursts:
        pulse_start_ts = tracks.origin + pd.to_timedelta(time_s[start_idx], unit="s")
        pulse_end_ts = tracks.origin + pd.to_timedelta(time_s[end_idx], unit="s")
        if keep_start is not None and pulse_start_ts < keep_start:
            continue
        if keep_end is not None and pulse_start_ts >= keep_end:
            continue
        noise_floor = float(floor_track[anchor])
        sigma = float(scale_track[anchor])
        peak_amplitude = float(envelope[anchor])
        peak_excess = peak_amplitude - noise_floor
        edge_level = min(
            noise_floor + max(edge_fraction * peak_excess, edge_sigma_factor * sigma),
            peak_amplitude,
        )
        measured = _measure_burst(
            signal=tracks.signal,
            time_s=time_s,
            excess_track=excess,
            start_idx=start_idx,
            end_idx=end_idx,
            noise_floor=noise_floor,
            sigma=sigma,
            peak_min_distance_s=peak_min_distance_s,
        )
        index = len(results) + 1
        results.append(
            {
                "Order": f"Pulse {index}",
                "PulseIndex": index,
                "Detection Status": "detected",
                "Pulse Start ts": pulse_start_ts.isoformat(),
                "Pulse End ts": pulse_end_ts.isoformat(),
                "Duration (s)": measured["duration_s"],
                "Peak Force (g)": measured["peak_force"],
                "Peak Amplitude (g RMS)": peak_amplitude,
                "Noise Floor (g RMS)": noise_floor,
                "Detection Threshold (g RMS)": noise_floor + float(detect_excess[anchor]),
                "Edge Threshold (g RMS)": float(edge_level),
                "Signal-to-Noise (x)": peak_excess / max(sigma, _MIN_SCALE_G),
                "Area Above Noise (g·s)": measured["area"],
                "# peaks": measured["n_peaks"],
                "Peak Rate (Hz)": measured["peak_rate"],
                "SourceFile": str(tracks.sources[anchor]) if tracks.sources is not None else "",
                "detected": True,
                "pulse_start": pulse_start_ts,
                "pulse_end": pulse_end_ts,
            }
        )
    return results


def quantify_scheduled_pulses(
    df: pd.DataFrame,
    windows,
    *,
    exclude_windows=None,
    start_index: int = 1,
    envelope_window_s: float = 0.025,
    floor_window_s: float = 120.0,
    detection_sigma_factor: float = 6.0,
    min_amplitude_g: float = 0.004,
    edge_fraction: float = 0.15,
    edge_sigma_factor: float = 3.0,
    bridge_gap_s: float = 0.05,
    min_duration_s: float = 0.04,
    peak_min_distance_s: float = 0.002,
    context_minutes: float = 20.0,
    background_margin: float = 1.25,
    manual_threshold: float | None = None,
):
    """Quantify at most one vibration burst inside each expected time window.

    Everything is measured on the RMS envelope of the vibration magnitude, not
    on the magnitude itself. The stimulus is an oscillation, so the raw trace
    only stays above any level for a few milliseconds at a time; describing a
    burst from those fragments needs gaps of seconds to be bridged, which glues
    the stimulus to whatever else happened nearby. The envelope is the
    amplitude curve the eye reads off the plot, and a burst is a single
    contiguous excursion of it.

    Per window the strongest excursion above the tracked noise floor is taken
    as the candidate. It counts as a pulse when it clears the floor by both
    `detection_sigma_factor` sigma and `min_amplitude_g`. Its extent runs to
    where the envelope falls back to `edge_fraction` of the peak excursion, so
    a loud pulse and a quiet one are measured at the same point on their own
    decay rather than against a fixed level.

    Each result also carries a background comparison drawn from the recording
    either side of the window, because no window-local threshold can tell a
    stimulus apart from a periodic artefact running through the window.

    `min_duration_s` has to stay above `envelope_window_s`: the envelope spreads
    a single bad ADC reading across exactly one window, so anything narrower
    than that carries no energy and is a glitch rather than a pulse.

    `df` only has to cover the windows being quantified plus their background
    context. Callers that hand over one window at a time must still pass every
    scheduled window as `exclude_windows`, so a neighbouring stimulus is not
    mistaken for background, and `start_index` so numbering stays continuous.
    """
    tracks = _build_signal_tracks(
        df,
        envelope_window_s=envelope_window_s,
        floor_window_s=floor_window_s,
        caller="Scheduled pulse quantification",
    )
    abs_time = tracks.abs_time
    signal = tracks.signal
    origin = tracks.origin
    time_s = tracks.time_s
    sources = tracks.sources
    envelope = tracks.envelope
    floor_track = tracks.floor
    scale_track = tracks.scale
    excess_track = tracks.excess

    parsed_windows = _parse_window_bounds(windows)
    excluded_windows = (
        _parse_window_bounds(exclude_windows) if exclude_windows is not None else parsed_windows
    )

    results = []
    for index, (window_start, window_end) in enumerate(parsed_windows, start=int(start_index)):
        win_mask = ((abs_time >= window_start) & (abs_time <= window_end)).to_numpy()
        win_idx = np.flatnonzero(win_mask)

        detected = False
        pulse_start_ts = None
        pulse_end_ts = None
        offset_start_s = np.nan
        offset_end_s = np.nan
        duration_s = np.nan
        peak_force = np.nan
        peak_amplitude = np.nan
        noise_floor = np.nan
        detect_level = np.nan
        edge_level = np.nan
        snr = np.nan
        area = np.nan
        n_peaks = np.nan
        peak_rate = np.nan
        source_file = ""
        bg_count, bg_p99, bg_max = 0, float("nan"), float("nan")
        background_ratio = float("nan")

        if win_idx.size >= 3:
            # Of the excursions large enough to be a pulse at all, take the one
            # that stands out most from its own surroundings rather than the
            # tallest. Over a window an hour wide the tallest is as likely to be
            # someone knocking the bench during a busy few minutes, while the
            # stimulus is the one that towers over a quiet stretch.
            if manual_threshold is not None:
                eligible = win_idx[envelope[win_idx] >= float(manual_threshold)]
            else:
                eligible = win_idx[excess_track[win_idx] >= min_amplitude_g]
            if eligible.size:
                anchor = int(eligible[int(np.argmax(excess_track[eligible] / scale_track[eligible]))])
            else:
                anchor = int(win_idx[int(np.argmax(excess_track[win_idx]))])
            noise_floor = float(floor_track[anchor])
            sigma = float(scale_track[anchor])
            peak_amplitude = float(envelope[anchor])
            peak_excess = peak_amplitude - noise_floor
            snr = peak_excess / max(sigma, _MIN_SCALE_G)
            if manual_threshold is not None:
                detect_level = float(manual_threshold)
            else:
                detect_level = noise_floor + max(detection_sigma_factor * sigma, min_amplitude_g)

            if peak_amplitude >= detect_level:
                if manual_threshold is not None:
                    edge_level = float(manual_threshold)
                else:
                    edge_level = noise_floor + max(
                        edge_fraction * peak_excess, edge_sigma_factor * sigma
                    )
                edge_level = min(edge_level, peak_amplitude)
                # Bounds are found across the whole frame rather than inside the
                # window, so a burst that starts a moment before the expected
                # window is measured whole instead of being clipped by it.
                start_idx, end_idx = find_burst_bounds(
                    time_s, envelope, anchor, edge_level, bridge_gap_s
                )
                duration_s = float(time_s[end_idx] - time_s[start_idx])
                if duration_s >= min_duration_s:
                    detected = True
                    pulse_start_ts = origin + pd.to_timedelta(time_s[start_idx], unit="s")
                    pulse_end_ts = origin + pd.to_timedelta(time_s[end_idx], unit="s")
                    offset_start_s = (pulse_start_ts - window_start).total_seconds()
                    offset_end_s = (pulse_end_ts - window_start).total_seconds()
                    measured = _measure_burst(
                        signal=signal,
                        time_s=time_s,
                        excess_track=excess_track,
                        start_idx=start_idx,
                        end_idx=end_idx,
                        noise_floor=noise_floor,
                        sigma=sigma,
                        peak_min_distance_s=peak_min_distance_s,
                    )
                    peak_force = measured["peak_force"]
                    area = measured["area"]
                    n_peaks = measured["n_peaks"]
                    peak_rate = measured["peak_rate"]
                    if sources is not None:
                        source_file = str(sources[anchor])
            if not detected:
                duration_s = np.nan
                if sources is not None:
                    source_file = str(sources[anchor])

            level = (
                detect_level - noise_floor
                if manual_threshold is not None
                else max(detection_sigma_factor * sigma, min_amplitude_g)
            )
            bg_count, bg_p99, bg_max = summarize_background_bursts(
                abs_time,
                excess_track,
                window_start,
                window_end,
                exclude_spans=excluded_windows,
                burst_span=(pulse_start_ts, pulse_end_ts) if detected else None,
                context_minutes=context_minutes,
                level=level,
            )
            # Both sides of the comparison are excursions above the local floor,
            # so a drifting floor cannot make a pulse look big. Clearing the
            # loudest background event by a hair is not evidence of a stimulus,
            # hence the margin.
            if detected and np.isfinite(bg_p99):
                background_ratio = peak_excess / max(bg_p99, 1e-9)

        if not detected or not np.isfinite(bg_max):
            background_check = "no comparison"
        elif peak_amplitude - noise_floor > bg_max and background_ratio >= background_margin:
            background_check = "distinct"
        elif peak_amplitude - noise_floor > bg_p99:
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
                "Peak Force (g)": float(peak_force) if detected else np.nan,
                "Peak Amplitude (g RMS)": float(peak_amplitude),
                "Noise Floor (g RMS)": float(noise_floor),
                "Detection Threshold (g RMS)": float(detect_level),
                "Edge Threshold (g RMS)": float(edge_level) if detected else np.nan,
                "Signal-to-Noise (x)": float(snr),
                "Area Above Noise (g·s)": float(area) if detected else np.nan,
                "Background Burst p99 (g RMS)": float(bg_p99),
                "Background Burst Max (g RMS)": float(bg_max),
                "Background Burst Count": int(bg_count),
                "Peak / Background p99": float(background_ratio),
                "# peaks": int(n_peaks) if detected else np.nan,
                "Peak Rate (Hz)": float(peak_rate) if detected else np.nan,
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
                        help="Manual RMS envelope threshold (g) for pulse detection. "
                             "If not specified, the local noise floor sets it.")
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
