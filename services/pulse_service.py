"""Callable pulse-metrics service used by both CLI and GUI flows."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from ConvertAcclLogsToPlots import (
    calculate_median_centered_offsets,
    find_manifest_path,
    load_manifest,
    parse_manifest_start_iso,
    quantify_scheduled_pulses,
    smooth_signal,
)
from services.models import FolderWindowSummary, PulseAnalysisResult, PulseWindow
from services.output_packaging import create_zip_from_paths
from services.plot_axes import apply_wall_clock_xaxis
from services.power_management import prevent_sleep

ProgressCallback = Optional[Callable[[int, str], None]]

# Must cover the detector's background context window on either side.
_CONTEXT_PADDING_MINUTES = 25


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


def normalize_pulse_windows(raw_windows) -> List[PulseWindow]:
    windows: List[PulseWindow] = []
    for item in raw_windows or []:
        if isinstance(item, PulseWindow):
            start_iso, end_iso = item.start_iso, item.end_iso
        elif isinstance(item, dict):
            start_iso = item.get("start_iso")
            end_iso = item.get("end_iso")
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            start_iso, end_iso = item
        else:
            continue
        start_ts = pd.to_datetime(start_iso, errors="coerce")
        end_ts = pd.to_datetime(end_iso, errors="coerce")
        if pd.isna(start_ts) or pd.isna(end_ts):
            continue
        start_ts = _to_naive_ts(start_ts)
        end_ts = _to_naive_ts(end_ts)
        if end_ts <= start_ts:
            continue
        windows.append(
            PulseWindow(
                start_iso=start_ts.to_pydatetime().isoformat(),
                end_iso=end_ts.to_pydatetime().isoformat(),
            )
        )
    windows.sort(key=lambda window: window.start_iso)
    return windows


def run_pulse_analysis(
    folder_path: str | Path,
    output_dir: str | Path,
    *,
    window_start_iso: Optional[str] = None,
    window_end_iso: Optional[str] = None,
    pulse_windows=None,
    n_max: int = 10,
    n_mins_bucket: int = 5,
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

    scheduled_windows = normalize_pulse_windows(pulse_windows)
    if not scheduled_windows:
        raise ValueError("At least one expected pulse window is required.")

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
    # Read beyond the requested range so each window keeps its pre-window
    # baseline and its surrounding background comparison. The padding is for
    # analysis only and is trimmed back out before plotting.
    padding = pd.Timedelta(minutes=_CONTEXT_PADDING_MINUTES)
    padded_start = window_start - padding if window_start is not None else None
    padded_end = window_end + padding if window_end is not None else None
    if not candidate_files:
        raise ValueError("No log files overlapped the selected time window.")

    all_frames = []
    processed_files = []
    started_at = time.monotonic()

    with prevent_sleep():
        for index, file_path in enumerate(candidate_files, start=1):
            frame = _prepare_file_frame(
                file_path,
                manifest_start_ts=manifest_start_ts,
                window_start=padded_start,
                window_end=padded_end,
            )
            if frame is not None and not frame.empty:
                all_frames.append(frame)
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

        _emit(progress_callback, 88, "Quantifying scheduled pulse windows...")
        combined_frame = pd.concat(all_frames, ignore_index=True) if all_frames else pd.DataFrame()
        pulse_rows = quantify_scheduled_pulses(
            combined_frame,
            scheduled_windows,
            manual_threshold=threshold,
        )
        speed_setting = str(manifest.get("speed", "unknown"))
        for row in pulse_rows:
            row["SpeedSetting"] = speed_setting

        _emit(progress_callback, 92, "Rendering aggregated pulse metrics plot...")
        aggregated_plot = output_path / f"Pulse_Metrics_Aggregated_{folder.name.replace(' ', '_')}.png"
        plot_frame = combined_frame
        if not plot_frame.empty:
            if window_start is not None:
                plot_frame = plot_frame[plot_frame["AbsoluteTime"] >= window_start]
            if window_end is not None:
                plot_frame = plot_frame[plot_frame["AbsoluteTime"] <= window_end]
        _plot_aggregated_frame(
            plot_frame,
            aggregated_plot,
            pulse_rows=pulse_rows,
            n_max=n_max,
            n_mins_bucket=n_mins_bucket,
            speed_setting=speed_setting,
            earliest_start_ts=_to_naive_ts(manifest_start_ts),
        )

        _emit(progress_callback, 96, "Writing aggregated pulse metrics workbook...")
        workbook_path = output_path / f"Pulse_Metrics_Aggregated_{folder.name.replace(' ', '_')}.xlsx"
        pulse_df = _build_pulse_metrics_df(pulse_rows)
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
        total_pulses=len([row for row in pulse_rows if row.get("Detection Status") == "detected"]),
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


def _prepare_file_frame(
    file_path: Path,
    *,
    manifest_start_ts: pd.Timestamp,
    window_start: Optional[pd.Timestamp],
    window_end: Optional[pd.Timestamp],
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
        return None

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

    return pd.DataFrame(
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


def _plot_aggregated_frame(
    combined_frame: pd.DataFrame,
    output_path: Path,
    *,
    pulse_rows: List[Dict[str, object]],
    n_max: int,
    n_mins_bucket: int,
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

    expected_labeled = False
    detected_labeled = False
    suspect_labeled = False
    for row in pulse_rows:
        expected_start = row.get("expected_start")
        expected_end = row.get("expected_end")
        if expected_start is not None and expected_end is not None:
            ax.axvspan(
                expected_start,
                expected_end,
                color="#4f8cff",
                alpha=0.10,
                label="Expected Window" if not expected_labeled else None,
            )
            expected_labeled = True
        pulse_start = row.get("pulse_start")
        pulse_end = row.get("pulse_end")
        background_check = str(row.get("Background Check") or "")
        stands_out = background_check in {"distinct", "marginal", "no comparison"}
        if row.get("detected") and pulse_start is not None and pulse_end is not None:
            span_color = "grey" if stands_out else "#d98c00"
            if stands_out:
                span_label = "Detected Pulse" if not detected_labeled else None
                detected_labeled = True
            else:
                span_label = "Matches Background" if not suspect_labeled else None
                suspect_labeled = True
            ax.axvspan(pulse_start, pulse_end, color=span_color, alpha=0.18, label=span_label)
            ax.axvline(x=pulse_start, color=span_color, linestyle="--", linewidth=0.8, alpha=0.5)
            ax.axvline(x=pulse_end, color=span_color, linestyle="--", linewidth=0.8, alpha=0.5)
            label_x = pulse_start + (pulse_end - pulse_start) / 2
            pulse_window = (combined_frame["AbsoluteTime"] >= pulse_start) & (combined_frame["AbsoluteTime"] <= pulse_end)
        else:
            label_x = expected_start + (expected_end - expected_start) / 2 if expected_start is not None and expected_end is not None else combined_frame["AbsoluteTime"].iloc[0]
            pulse_window = (
                (combined_frame["AbsoluteTime"] >= expected_start) & (combined_frame["AbsoluteTime"] <= expected_end)
                if expected_start is not None and expected_end is not None
                else pd.Series(False, index=combined_frame.index)
            )
        local_max = float(combined_frame.loc[pulse_window, "Vibration_Accel"].max()) if pulse_window.any() else float(combined_frame["Vibration_Accel"].max())
        y_low, y_high = ax.get_ylim()
        y_gap = max(y_high - y_low, 1e-6)
        label_y = local_max + (0.08 * y_gap)
        if label_y > y_high * 0.995:
            ax.set_ylim(y_low, label_y + (0.08 * y_gap))
        pulse_index = int(row.get("PulseIndex") or 0)
        if not row.get("detected"):
            label = f"P{pulse_index} (n.d.)"
        elif background_check == "matches background":
            label = f"P{pulse_index} (bg)"
        elif background_check == "marginal":
            label = f"P{pulse_index} (marginal)"
        else:
            label = f"P{pulse_index}"
        ax.text(
            label_x,
            label_y,
            label,
            ha="center",
            va="bottom",
            fontsize=7,
            color="black",
            backgroundcolor="white",
            alpha=0.8,
        )

    apply_wall_clock_xaxis(ax)
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
            "Expected Window",
            "Detected Pulse",
            "Matches Background",
        ]
        ordered_pairs = sorted(
            zip(labels, handles),
            key=lambda item: label_order.index(item[0]) if item[0] in label_order else 999,
        )
        ordered_labels, ordered_handles = zip(*ordered_pairs)
        ax.legend(ordered_handles, ordered_labels, loc="lower left", fontsize="x-small", ncol=1, frameon=True)
    fig.tight_layout()
    ax.grid(True, axis="x", which="major", linestyle="--", alpha=0.45)
    ax.grid(True, axis="x", which="minor", linestyle="--", alpha=0.2)
    ax.grid(True, axis="y", linestyle="--", alpha=0.5)
    fig.savefig(output_path, dpi=300)


_PULSE_EXPORT_COLUMNS = [
    "Order",
    "PulseIndex",
    "Detection Status",
    "Background Check",
    "Expected Start ts",
    "Expected End ts",
    "Pulse Start ts",
    "Pulse End ts",
    "Pulse Start (s from window)",
    "Pulse End (s from window)",
    "Duration (s)",
    "Baseline (g)",
    "Threshold (g)",
    "Baseline-Adjusted Area (g·s)",
    "Peak Force (g)",
    "Background Peak p99 (g)",
    "Background Peak Max (g)",
    "Background Peak Count",
    "Peak / Background p99",
    "# peaks",
    "Frequency (Hz)",
    "SpeedSetting",
    "SourceFile",
]

_PULSE_TEXT_COLUMNS = {
    "Order",
    "Detection Status",
    "Background Check",
    "SpeedSetting",
    "SourceFile",
    "Expected Start ts",
    "Expected End ts",
    "Pulse Start ts",
    "Pulse End ts",
}


def _build_pulse_metrics_df(pulse_rows: List[Dict[str, object]]) -> pd.DataFrame:
    if pulse_rows:
        out_df = pd.DataFrame(pulse_rows)
    else:
        out_df = pd.DataFrame(columns=_PULSE_EXPORT_COLUMNS)
    export_df = out_df.reindex(columns=[column for column in _PULSE_EXPORT_COLUMNS if column in out_df.columns or column in _PULSE_EXPORT_COLUMNS])
    for column in _PULSE_EXPORT_COLUMNS:
        if column not in export_df.columns:
            export_df[column] = np.nan
    export_df = export_df[_PULSE_EXPORT_COLUMNS]
    numeric_columns = [
        "Duration (s)",
        "Baseline (g)",
        "Threshold (g)",
        "Baseline-Adjusted Area (g·s)",
        "Peak Force (g)",
        "# peaks",
        "Frequency (Hz)",
    ]
    for column in numeric_columns:
        export_df[column] = pd.to_numeric(export_df[column], errors="coerce")
    detected_df = export_df[export_df["Detection Status"] == "detected"] if "Detection Status" in export_df else export_df
    avg_row = {column: "" if column in _PULSE_TEXT_COLUMNS else np.nan for column in _PULSE_EXPORT_COLUMNS}
    avg_row["Order"] = "Avg"
    std_row = dict(avg_row)
    std_row["Order"] = "Std dv"
    for column in numeric_columns:
        avg_row[column] = float(detected_df[column].mean(skipna=True)) if column in detected_df and detected_df[column].notna().any() else np.nan
        std_row[column] = float(detected_df[column].std(skipna=True, ddof=1)) if column in detected_df and detected_df[column].count() > 1 else np.nan
    return pd.concat([export_df, pd.DataFrame([avg_row, std_row])], ignore_index=True)


def _to_naive_ts(timestamp: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(timestamp)
    if ts.tz is not None:
        return pd.Timestamp(ts.to_pydatetime().replace(tzinfo=None))
    return ts
