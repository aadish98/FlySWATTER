"""Callable pulse-metrics service used by both CLI and GUI flows."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from ConvertAcclLogsToPlots import (
    calculate_adaptive_threshold,
    calculate_median_centered_offsets,
    calculate_net_force,
    count_peaks_above_threshold,
    find_manifest_path,
    find_pulses_improved,
    load_manifest,
    parse_manifest_start_iso,
    smooth_signal,
)
from services.models import FolderWindowSummary, PulseAnalysisResult
from services.output_packaging import create_zip_from_paths
from services.power_management import prevent_sleep

ProgressCallback = Optional[Callable[[int, str], None]]


def _has_csv_suffix(path: Path) -> bool:
    lower_name = path.name.lower()
    return lower_name.endswith(".csv") or lower_name.endswith(".csv.gz")


def _manifest_relative_path(raw_path: str) -> Path:
    normalized = raw_path.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part and part != "."]
    return Path(*parts) if parts else Path(raw_path)


def _manifest_csv_files(folder: Path, manifest: Dict[str, object]) -> List[Path]:
    parts = manifest.get("parts")
    if not isinstance(parts, list):
        return []
    files: List[Path] = []
    seen = set()
    for entry in parts:
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            continue
        candidate = folder / _manifest_relative_path(raw_path)
        if not candidate.is_file() or not _has_csv_suffix(candidate):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        files.append(candidate)
    return files


def discover_csv_files(folder_path: str | Path, *, manifest: Optional[Dict[str, object]] = None) -> List[Path]:
    root = Path(folder_path)
    if manifest is not None:
        manifest_files = _manifest_csv_files(root, manifest)
        if manifest_files:
            return manifest_files
    return sorted(
        [
            path
            for path in root.rglob("*")
            if path.is_file() and _has_csv_suffix(path)
        ]
    )


def _manifest_folder_bounds(
    manifest: Dict[str, object],
    manifest_start_ts: pd.Timestamp,
) -> Tuple[pd.Timestamp, Optional[pd.Timestamp]]:
    start_ts = _to_naive_ts(manifest_start_ts)
    end_iso = manifest.get("end_iso")
    if isinstance(end_iso, str):
        end_ts = pd.to_datetime(end_iso, errors="coerce")
        if not pd.isna(end_ts):
            end_ts_naive = _to_naive_ts(end_ts)
            if end_ts_naive >= start_ts:
                return start_ts, end_ts_naive
    duration_s = manifest.get("target_duration_s")
    try:
        duration_val = float(duration_s)
    except (TypeError, ValueError):
        return start_ts, None
    if duration_val <= 0:
        return start_ts, None
    return start_ts, _to_naive_ts(start_ts + pd.to_timedelta(duration_val, unit="s"))


def _manifest_file_spans(
    folder: Path,
    manifest: Dict[str, object],
    manifest_start_ts: pd.Timestamp,
) -> Dict[Path, Tuple[pd.Timestamp, pd.Timestamp]]:
    parts = manifest.get("parts")
    if not isinstance(parts, list):
        return {}
    folder_start, folder_end = _manifest_folder_bounds(manifest, manifest_start_ts)
    starts: List[Tuple[Path, pd.Timestamp]] = []
    seen = set()
    for entry in parts:
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        created_iso = entry.get("created_iso")
        if not isinstance(raw_path, str) or not isinstance(created_iso, str):
            continue
        file_path = folder / _manifest_relative_path(raw_path)
        if not file_path.is_file() or not _has_csv_suffix(file_path):
            continue
        if file_path in seen:
            continue
        created_ts = pd.to_datetime(created_iso, errors="coerce")
        if pd.isna(created_ts):
            continue
        seen.add(file_path)
        starts.append((file_path, _to_naive_ts(created_ts)))
    starts.sort(key=lambda item: item[1])
    if not starts:
        return {}

    spans: Dict[Path, Tuple[pd.Timestamp, pd.Timestamp]] = {}
    for idx, (file_path, file_start) in enumerate(starts):
        if idx + 1 < len(starts):
            file_end = starts[idx + 1][1]
        elif folder_end is not None and folder_end >= file_start:
            file_end = folder_end
        else:
            file_end = max(file_start, folder_start)
        spans[file_path] = (file_start, file_end)
    return spans


def get_folder_window_summary(
    folder_path: str | Path,
    *,
    progress_callback: ProgressCallback = None,
) -> FolderWindowSummary:
    folder = Path(folder_path)
    manifest_path = Path(find_manifest_path(str(folder)))
    manifest = load_manifest(str(manifest_path))
    manifest_start_ts = parse_manifest_start_iso(manifest, str(manifest_path))
    csv_files = discover_csv_files(folder, manifest=manifest)
    if not csv_files:
        raise ValueError("No accelerometer log files were found in the selected folder.")
    _emit(progress_callback, 30, "Loaded manifest metadata for selected run.")

    min_ts, max_ts = _manifest_folder_bounds(manifest, manifest_start_ts)
    if max_ts is None:
        # Fallback path for older/incomplete manifests: read first/last file only.
        first_start, _ = _file_window_bounds(csv_files[0], manifest_start_ts)
        _, last_end = _file_window_bounds(csv_files[-1], manifest_start_ts)
        min_ts, max_ts = first_start, last_end
        _emit(progress_callback, 80, "Manifest bounds missing. Estimated range from first/last log file.")
    else:
        _emit(progress_callback, 80, "Computed range from manifest start/end timestamps.")

    return FolderWindowSummary(
        display_name=folder.name,
        manifest_path=manifest_path,
        start_ts_iso=min_ts.to_pydatetime().isoformat(),
        end_ts_iso=max_ts.to_pydatetime().isoformat(),
        csv_files=csv_files,
    )


def _select_candidate_files(
    folder: Path,
    manifest: Dict[str, object],
    manifest_start_ts: pd.Timestamp,
    csv_files: List[Path],
    *,
    window_start: Optional[pd.Timestamp],
    window_end: Optional[pd.Timestamp],
    pad_minutes: int = 10,
) -> List[Path]:
    padded_start = window_start - pd.Timedelta(minutes=pad_minutes) if window_start is not None else None
    padded_end = window_end + pd.Timedelta(minutes=pad_minutes) if window_end is not None else None
    if padded_start is None and padded_end is None:
        return list(csv_files)

    selected_files: List[Path] = []
    file_spans = _manifest_file_spans(folder, manifest, manifest_start_ts)
    for file_path in csv_files:
        span = file_spans.get(file_path)
        if span is None:
            file_start, file_end = _file_window_bounds(file_path, manifest_start_ts)
        else:
            file_start, file_end = span
        if padded_start is not None and file_end < padded_start:
            continue
        if padded_end is not None and file_start > padded_end:
            continue
        selected_files.append(file_path)
    return selected_files


def estimate_window_file_count(
    folder_path: str | Path,
    *,
    window_start_iso: Optional[str] = None,
    window_end_iso: Optional[str] = None,
) -> int:
    folder = Path(folder_path)
    manifest_path = Path(find_manifest_path(str(folder)))
    manifest = load_manifest(str(manifest_path))
    manifest_start_ts = parse_manifest_start_iso(manifest, str(manifest_path))
    csv_files = discover_csv_files(folder, manifest=manifest)
    if not csv_files:
        return 0
    window_start = _to_naive_ts(pd.to_datetime(window_start_iso)) if window_start_iso else None
    window_end = _to_naive_ts(pd.to_datetime(window_end_iso)) if window_end_iso else None
    return len(
        _select_candidate_files(
            folder,
            manifest,
            manifest_start_ts,
            csv_files,
            window_start=window_start,
            window_end=window_end,
        )
    )


def run_pulse_analysis(
    folder_path: str | Path,
    output_dir: str | Path,
    *,
    window_start_iso: Optional[str] = None,
    window_end_iso: Optional[str] = None,
    n_max: int = 10,
    n_mins_bucket: int = 5,
    min_pulse_gs: float = 0.0000005,
    threshold: Optional[float] = None,
    progress_callback: ProgressCallback = None,
) -> PulseAnalysisResult:
    folder = Path(folder_path)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    manifest_path = Path(find_manifest_path(str(folder)))
    manifest = load_manifest(str(manifest_path))
    manifest_start_ts = parse_manifest_start_iso(manifest, str(manifest_path))
    csv_files = discover_csv_files(folder, manifest=manifest)
    if not csv_files:
        raise ValueError("No accelerometer log files were found in the selected folder.")

    window_start = _to_naive_ts(pd.to_datetime(window_start_iso)) if window_start_iso else None
    window_end = _to_naive_ts(pd.to_datetime(window_end_iso)) if window_end_iso else None
    candidate_files = _select_candidate_files(
        folder,
        manifest,
        manifest_start_ts,
        csv_files,
        window_start=window_start,
        window_end=window_end,
    )
    padded_start = window_start - pd.Timedelta(minutes=10) if window_start is not None else None
    padded_end = window_end + pd.Timedelta(minutes=10) if window_end is not None else None
    if not candidate_files:
        raise ValueError("No log files overlapped the selected time window.")

    all_frames = []
    all_pulse_rows = []
    processed_files = []
    started_at = time.monotonic()

    with prevent_sleep():
        for index, file_path in enumerate(candidate_files, start=1):
            frame, pulse_rows = _analyze_single_file(
                file_path,
                manifest_start_ts=manifest_start_ts,
                window_start=padded_start,
                window_end=padded_end,
                threshold=threshold,
                min_pulse_gs=min_pulse_gs,
            )
            if frame is not None and not frame.empty:
                all_frames.append(frame)
            all_pulse_rows.extend(pulse_rows)
            processed_files.append(file_path)
            elapsed = max(time.monotonic() - started_at, 0.001)
            avg_per_file = elapsed / index
            remaining = len(candidate_files) - index
            eta_seconds = int(round(avg_per_file * remaining))
            percent = int((index / len(candidate_files)) * 80) + 10
            _emit(
                progress_callback,
                percent,
                f"Processed {index}/{len(candidate_files)} log files. Rough ETA: {eta_seconds}s",
            )

        _emit(progress_callback, 90, "Rendering aggregated pulse metrics plot...")
        combined_frame = pd.concat(all_frames, ignore_index=True) if all_frames else pd.DataFrame()
        aggregated_plot = output_path / f"Pulse_Metrics_Aggregated_{folder.name.replace(' ', '_')}.png"
        _plot_aggregated_frame(
            combined_frame,
            aggregated_plot,
            n_max=n_max,
            n_mins_bucket=n_mins_bucket,
            threshold=threshold,
            speed_setting=str(manifest.get("speed", "unknown")),
            earliest_start_ts=_to_naive_ts(manifest_start_ts),
        )

        _emit(progress_callback, 96, "Writing aggregated pulse metrics workbook...")
        workbook_path = output_path / f"Pulse_Metrics_Aggregated_{folder.name.replace(' ', '_')}.xlsx"
        pulse_df = _build_pulse_metrics_df(all_pulse_rows)
        with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
            pulse_df.to_excel(writer, index=False, sheet_name="Pulses")

        zip_path = output_path / f"Pulse_Metrics_{folder.name.replace(' ', '_')}.zip"
        create_zip_from_paths(zip_path, [aggregated_plot, workbook_path], base_dir=output_path)

    display_start = window_start if window_start is not None else (combined_frame["AbsoluteTime"].min() if not combined_frame.empty else _to_naive_ts(manifest_start_ts))
    display_end = window_end if window_end is not None else (combined_frame["AbsoluteTime"].max() if not combined_frame.empty else _to_naive_ts(manifest_start_ts))
    window_label = f"{pd.to_datetime(display_start).strftime('%m-%d-%Y %I:%M %p')} to {pd.to_datetime(display_end).strftime('%m-%d-%Y %I:%M %p')}"
    _emit(progress_callback, 100, "Pulse metrics analysis complete.")
    return PulseAnalysisResult(
        output_dir=output_path,
        aggregated_plot=aggregated_plot,
        aggregated_workbook=workbook_path,
        zip_path=zip_path,
        processed_files=processed_files,
        analyzed_window_label=window_label,
        total_pulses=len([row for row in all_pulse_rows if str(row.get("Order", "")).startswith("Pulse")]),
    )


def _emit(callback: ProgressCallback, value: int, message: str) -> None:
    if callback is not None:
        callback(value, message)


def _file_window_bounds(file_path: Path, manifest_start_ts: pd.Timestamp):
    t_ms = pd.read_csv(file_path, usecols=["t_ms"])["t_ms"]
    t_ms = pd.to_numeric(t_ms, errors="coerce").dropna()
    if t_ms.empty:
        raise ValueError(f"No valid t_ms rows found in {file_path.name}")
    start = _to_naive_ts(manifest_start_ts + pd.to_timedelta(float(t_ms.min()), unit="ms"))
    end = _to_naive_ts(manifest_start_ts + pd.to_timedelta(float(t_ms.max()), unit="ms"))
    return start, end


def _analyze_single_file(
    file_path: Path,
    *,
    manifest_start_ts: pd.Timestamp,
    window_start: Optional[pd.Timestamp],
    window_end: Optional[pd.Timestamp],
    threshold: Optional[float],
    min_pulse_gs: float,
):
    data = pd.read_csv(file_path)
    if "t_ms" not in data.columns:
        raise ValueError(f"Missing required column 't_ms' in {file_path.name}")

    t_ms = pd.to_numeric(data["t_ms"], errors="coerce")
    if t_ms.isna().any():
        raise ValueError(f"Found non-numeric t_ms rows in {file_path.name}")

    abs_time = pd.Series(manifest_start_ts + pd.to_timedelta(t_ms, unit="ms")).apply(_to_naive_ts)
    data["AbsoluteTime"] = abs_time
    if window_start is not None:
        data = data[data["AbsoluteTime"] >= window_start]
    if window_end is not None:
        data = data[data["AbsoluteTime"] <= window_end]
    if data.empty:
        return None, []

    x_offset, y_offset, z_offset = calculate_median_centered_offsets(str(file_path))
    v_ref = 3.0
    sensitivity = 0.3
    data["ElapsedSeconds"] = (pd.to_numeric(data["t_ms"], errors="coerce") - float(pd.to_numeric(data["t_ms"], errors="coerce").min())) / 1000.0
    data["X_Voltage"] = data["X"] * v_ref / 1023.0
    data["Y_Voltage"] = data["Y"] * v_ref / 1023.0
    data["Z_Voltage"] = data["Z"] * v_ref / 1023.0
    data["X_Accel"] = (data["X_Voltage"] - x_offset) / sensitivity
    data["Y_Accel"] = (data["Y_Voltage"] - y_offset) / sensitivity
    data["Z_Accel"] = ((data["Z_Voltage"] - z_offset) / sensitivity) + 1.0
    data["Z_Vib_Accel"] = data["Z_Accel"]
    data["Smoothed_X_Accel"] = smooth_signal(data["X_Accel"].values, window_size=5)
    data["Smoothed_Y_Accel"] = smooth_signal(data["Y_Accel"].values, window_size=5)
    data["Smoothed_Z_Accel"] = smooth_signal(data["Z_Vib_Accel"].values, window_size=5)
    data["Vibration_Accel"] = np.sqrt(
        data["Smoothed_X_Accel"] ** 2
        + data["Smoothed_Y_Accel"] ** 2
        + data["Smoothed_Z_Accel"] ** 2
    )

    plot_data = pd.DataFrame(
        {
            "AbsoluteTime": data["AbsoluteTime"],
            "Time": data["ElapsedSeconds"],
            "Smoothed_X_Accel": data["Smoothed_X_Accel"],
            "Smoothed_Y_Accel": data["Smoothed_Y_Accel"],
            "Smoothed_Z_Accel": data["Smoothed_Z_Accel"],
            "Vibration_Accel": data["Vibration_Accel"],
            "SourceFile": file_path.name,
        }
    )

    data_for_pulses = plot_data.copy()
    pulses = find_pulses_improved(
        data_for_pulses,
        adaptive_threshold=True,
        smoothing=True,
        min_pulse_duration=0.1,
        min_gap_for_pulse=10.0,
        manual_threshold=threshold,
        extend_pulse_window=0.5,
    )
    actual_threshold = threshold if threshold is not None else calculate_adaptive_threshold(plot_data["Vibration_Accel"].values, window_size=30, noise_factor=4.0)
    pulse_rows = []
    for idx, (pulse_start, pulse_end) in enumerate(pulses, start=1):
        net_force = calculate_net_force(plot_data, pulse_start, pulse_end)
        if net_force < min_pulse_gs:
            continue
        mask = (plot_data["Time"] >= pulse_start) & (plot_data["Time"] <= pulse_end)
        vals = plot_data.loc[mask, "Vibration_Accel"].values
        n_vibs = count_peaks_above_threshold(vals, actual_threshold) if len(vals) >= 3 else 0
        peak_g = float(np.max(vals)) if len(vals) > 0 else 0.0
        start_ts = plot_data.loc[mask, "AbsoluteTime"].iloc[0] if mask.any() else plot_data["AbsoluteTime"].iloc[0]
        end_ts = plot_data.loc[mask, "AbsoluteTime"].iloc[-1] if mask.any() else plot_data["AbsoluteTime"].iloc[-1]
        pulse_rows.append(
            {
                "Order": f"Pulse {len(pulse_rows) + 1}",
                "PulseIndex": len(pulse_rows) + 1,
                "Pulse Start (s)": float(pulse_start),
                "Pulse End (s)": float(pulse_end),
                "Duration (s)": float(max(pulse_end - pulse_start, 0.0)),
                "Net Force (g·s)": float(net_force),
                "Peak Force (g)": float(peak_g),
                "# peaks": int(n_vibs),
                "Frequency (Hz)": (float(n_vibs) / float(max(pulse_end - pulse_start, 1e-6))),
                "SpeedSetting": str(file_path.parent.parent.name if file_path.parent != file_path.parent.parent else file_path.parent.name),
                "SourceFile": file_path.name,
                "Pulse Start ts": pd.to_datetime(start_ts).isoformat(),
                "Pulse End ts": pd.to_datetime(end_ts).isoformat(),
            }
        )
    return plot_data, pulse_rows


def _plot_aggregated_frame(
    combined_frame: pd.DataFrame,
    output_path: Path,
    *,
    n_max: int,
    n_mins_bucket: int,
    threshold: Optional[float],
    speed_setting: str,
    earliest_start_ts: Optional[pd.Timestamp],
) -> None:
    fig = Figure(figsize=(10, 6))
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    if combined_frame.empty:
        ax.text(0.5, 0.5, "No data within selected time window.", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        fig.tight_layout()
        fig.savefig(output_path, dpi=300)
        return

    combined_frame = combined_frame.sort_values("AbsoluteTime").reset_index(drop=True)
    spacing_seconds = n_mins_bucket * 60.0
    elapsed_from_start = (combined_frame["AbsoluteTime"] - combined_frame["AbsoluteTime"].iloc[0]).dt.total_seconds()
    candidates = combined_frame.assign(ElapsedFromStart=elapsed_from_start).sort_values(by="Vibration_Accel", ascending=False)
    chosen_rows = []
    for _, row in candidates.iterrows():
        if not chosen_rows:
            chosen_rows.append(row)
        else:
            if all(abs(chosen["ElapsedFromStart"] - row["ElapsedFromStart"]) >= spacing_seconds for chosen in chosen_rows):
                chosen_rows.append(row)
        if len(chosen_rows) == n_max:
            break
    chosen_points = pd.DataFrame(chosen_rows)

    ax.plot(combined_frame["AbsoluteTime"], combined_frame["Vibration_Accel"], label="Vibration Stimulus (g)", linewidth=1.0, color="black")
    ax.plot(combined_frame["AbsoluteTime"], combined_frame["Smoothed_X_Accel"], label="X Accel", linewidth=0.5)
    ax.plot(combined_frame["AbsoluteTime"], combined_frame["Smoothed_Y_Accel"], label="Y Accel", linewidth=0.5)
    ax.plot(combined_frame["AbsoluteTime"], combined_frame["Smoothed_Z_Accel"], label="Z Accel (adj. for gravity)", linewidth=0.5)

    if not chosen_points.empty:
        ax.scatter(chosen_points["AbsoluteTime"], chosen_points["Vibration_Accel"], color="red", s=20, zorder=5)
        for _, row in chosen_points.iterrows():
            ax.text(row["AbsoluteTime"], row["Vibration_Accel"], f"{row['Vibration_Accel']:.2f}", va="bottom", ha="center", fontsize=8)

    data_for_pulses = combined_frame.copy()
    data_for_pulses["Time"] = elapsed_from_start
    pulses = find_pulses_improved(
        data_for_pulses,
        adaptive_threshold=True,
        smoothing=True,
        min_pulse_duration=0.1,
        min_gap_for_pulse=10.0,
        manual_threshold=threshold,
        extend_pulse_window=0.5,
    )
    qualified_pulses = []
    for start, end in pulses:
        net_force = calculate_net_force(data_for_pulses, start, end)
        if net_force >= 0.0000005:
            qualified_pulses.append((start, end))
    for idx, (pulse_start, pulse_end) in enumerate(qualified_pulses, start=1):
        x_start = combined_frame["AbsoluteTime"].iloc[0] + pd.to_timedelta(pulse_start, unit="s")
        x_end = combined_frame["AbsoluteTime"].iloc[0] + pd.to_timedelta(pulse_end, unit="s")
        ax.axvspan(x_start, x_end, color="grey", alpha=0.15, label="Stimulus Window" if idx == 1 else None)
        ax.axvline(x=x_start, color="grey", linestyle="--", linewidth=0.8, alpha=0.5)
        ax.axvline(x=x_end, color="grey", linestyle="--", linewidth=0.8, alpha=0.5)
        pulse_window = (elapsed_from_start >= pulse_start) & (elapsed_from_start <= pulse_end)
        local_max = float(combined_frame.loc[pulse_window, "Vibration_Accel"].max()) if pulse_window.any() else float(combined_frame["Vibration_Accel"].max())
        y_low, y_high = ax.get_ylim()
        y_gap = max(y_high - y_low, 1e-6)
        label_y = local_max + (0.08 * y_gap)
        if label_y > y_high * 0.995:
            ax.set_ylim(y_low, label_y + (0.08 * y_gap))
        ax.text(
            x_start + (x_end - x_start) / 2,
            label_y,
            f"P{idx}",
            ha="center",
            va="bottom",
            fontsize=6,
            color="black",
            backgroundcolor="white",
            alpha=0.8,
        )

    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    try:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%-I:%M%p"))
    except Exception:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%I:%M%p"))
    ax.set_xlabel("Local Time")
    ax.set_ylabel("Acceleration (g)")
    date_label = None
    combined_start = pd.to_datetime(combined_frame["AbsoluteTime"].iloc[0])
    try:
        combined_start = _to_naive_ts(combined_start)
    except Exception:
        pass
    if earliest_start_ts is not None:
        try:
            earliest_naive = _to_naive_ts(earliest_start_ts)
            day_index = (combined_start.date() - earliest_naive.date()).days + 1
        except Exception:
            day_index = 1
    else:
        day_index = 1
    try:
        date_label = f"{combined_start.strftime('%m/%d/%y')}, Day {day_index}"
    except Exception:
        date_label = None
    title_extra = f" | {date_label}" if date_label else ""
    ax.set_title(f"Vibration Intensity vs Local Time | Speed Settings: {speed_setting}{title_extra}")
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        label_order = [
            "Vibration Stimulus (g)",
            "X Accel",
            "Y Accel",
            "Z Accel (adj. for gravity)",
            "Stimulus Window",
        ]
        ordered_pairs = sorted(
            zip(labels, handles),
            key=lambda item: label_order.index(item[0]) if item[0] in label_order else 999,
        )
        ordered_labels, ordered_handles = zip(*ordered_pairs)
        ax.legend(ordered_handles, ordered_labels, loc="lower left", fontsize="x-small", ncol=1, frameon=True)
    fig.tight_layout()
    ax.grid(True, linestyle="--", alpha=0.5)
    fig.savefig(output_path, dpi=300)


def _build_pulse_metrics_df(pulse_rows: List[Dict[str, object]]) -> pd.DataFrame:
    if pulse_rows:
        out_df = pd.DataFrame(pulse_rows)
    else:
        out_df = pd.DataFrame(
            columns=[
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
            ]
        )
    for column in ["Duration (s)", "Net Force (g·s)", "Peak Force (g)", "# peaks"]:
        if column in out_df.columns:
            out_df[column] = pd.to_numeric(out_df[column], errors="coerce")
    avg_row = {
        "Order": "Avg",
        "PulseIndex": np.nan,
        "Pulse Start (s)": np.nan,
        "Pulse End (s)": np.nan,
        "Duration (s)": float(out_df["Duration (s)"].mean(skipna=True)) if "Duration (s)" in out_df else np.nan,
        "Net Force (g·s)": float(out_df["Net Force (g·s)"].mean(skipna=True)) if "Net Force (g·s)" in out_df else np.nan,
        "Peak Force (g)": float(out_df["Peak Force (g)"].mean(skipna=True)) if "Peak Force (g)" in out_df else np.nan,
        "# peaks": float(out_df["# peaks"].mean(skipna=True)) if "# peaks" in out_df else np.nan,
        "Frequency (Hz)": float(out_df["Frequency (Hz)"].mean(skipna=True)) if "Frequency (Hz)" in out_df else np.nan,
        "SpeedSetting": "",
        "SourceFile": "",
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
        "Frequency (Hz)": float(out_df["Frequency (Hz)"].std(skipna=True, ddof=1)) if "Frequency (Hz)" in out_df and out_df["Frequency (Hz)"].count() > 1 else np.nan,
        "SpeedSetting": "",
        "SourceFile": "",
    }
    return pd.concat([out_df, pd.DataFrame([avg_row, std_row])], ignore_index=True)


def _to_naive_ts(timestamp: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(timestamp)
    try:
        return ts.tz_localize(None)
    except TypeError:
        return ts
