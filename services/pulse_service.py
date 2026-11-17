"""Callable pulse-metrics service used by both CLI and GUI flows."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from ConvertAcclLogsToPlots import (
    clear_memory_between_files,
    detect_all_pulses,
    median_centered_offsets_from_frame,
    find_manifest_path,
    load_manifest,
    parse_manifest_start_iso,
    quantify_scheduled_pulses,
    smooth_signal,
)
from services.models import (
    AnalysisWindow,
    FolderWindowSummary,
    GeneralPulseAnalysisResult,
    GeneralWindowResult,
    PulseAnalysisResult,
    PulseWindow,
)
from services.output_packaging import create_zip_from_paths
from services.plot_axes import apply_wall_clock_xaxis
from services.power_management import prevent_sleep

ProgressCallback = Optional[Callable[[int, str], None]]

# Must cover the detector's background context window on either side.
_CONTEXT_PADDING_MINUTES = 25

_PLOT_DECIMATION_BUCKETS = 4000
_PLOT_SERIES_COLUMNS = (
    "Vibration_Accel",
    "Smoothed_X_Accel",
    "Smoothed_Y_Accel",
    "Smoothed_Z_Accel",
)
_RAW_COLUMNS = ["t_ms", "X", "Y", "Z"]
# The logger writes 10-bit ADC counts, so int16 holds X/Y/Z exactly at a
# quarter of the memory pandas would otherwise infer.
_RAW_DTYPES = {"t_ms": "int64", "X": "int16", "Y": "int16", "Z": "int16"}
_V_REF = 3.0
_SENSITIVITY = 0.3


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
    pad_minutes: int = _CONTEXT_PADDING_MINUTES,
) -> List[Path]:
    padded_start = window_start - pd.Timedelta(minutes=pad_minutes) if window_start is not None else None
    padded_end = window_end + pd.Timedelta(minutes=pad_minutes) if window_end is not None else None
    file_spans = _manifest_file_spans(folder, manifest, manifest_start_ts)
    if padded_start is None and padded_end is None and not file_spans:
        return list(csv_files)

    selected_files: List[Tuple[pd.Timestamp, Path]] = []
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
        selected_files.append((file_start, file_path))
    # The run is analysed as a stream that releases each pulse window once the
    # reader has moved past it, which is only sound if files arrive in order.
    selected_files.sort(key=lambda item: (item[0], item[1].name))
    return [file_path for _, file_path in selected_files]


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
    if not candidate_files:
        raise ValueError("No log files overlapped the selected time window.")

    plot_start, plot_end = _resolve_plot_bounds(
        manifest,
        manifest_start_ts,
        candidate_files,
        window_start=window_start,
        window_end=window_end,
    )
    # Keep full resolution beyond each window so it retains its pre-window
    # baseline and its surrounding background comparison. That padding is for
    # analysis only and never reaches the plot.
    padding = pd.Timedelta(minutes=_CONTEXT_PADDING_MINUTES)
    aggregator = _RunAggregator(
        detail_spans=[
            (pd.Timestamp(window.start_iso) - padding, pd.Timestamp(window.end_iso) + padding)
            for window in scheduled_windows
        ],
        plot_start=plot_start,
        plot_end=plot_end,
        peak_bucket_seconds=max(n_mins_bucket * 60.0, 1e-6),
    )

    processed_files = []
    started_at = time.monotonic()
    speed_setting = str(manifest.get("speed", "unknown"))
    rows_by_window: Dict[int, Dict[str, object]] = {}

    def quantify_windows(indices: List[int]) -> None:
        for index in indices:
            # One window at a time, but every other window is still declared so
            # a neighbouring stimulus cannot leak into this one's baseline or
            # background comparison.
            row = quantify_scheduled_pulses(
                aggregator.take_detail_frame(index),
                [scheduled_windows[index]],
                exclude_windows=scheduled_windows,
                start_index=index + 1,
                manual_threshold=threshold,
            )[0]
            row["SpeedSetting"] = speed_setting
            rows_by_window[index] = row

    with prevent_sleep():
        for index, file_path in enumerate(candidate_files, start=1):
            aggregator.add_file(file_path, manifest_start_ts=manifest_start_ts)
            quantify_windows(aggregator.settled_span_indices())
            clear_memory_between_files()
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
        quantify_windows(aggregator.remaining_span_indices())
        pulse_rows = [rows_by_window[index] for index in sorted(rows_by_window)]

        _emit(progress_callback, 92, "Rendering aggregated pulse metrics plot...")
        aggregated_plot = output_path / f"Pulse_Metrics_Aggregated_{folder.name.replace(' ', '_')}.png"
        _plot_aggregated_frame(
            aggregator.plot_frame(),
            aggregated_plot,
            pulse_rows=pulse_rows,
            chosen_points=aggregator.peak_points(
                n_max=n_max, spacing_seconds=max(n_mins_bucket * 60.0, 1e-6)
            ),
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

    window_label = f"{pd.to_datetime(plot_start).strftime('%m-%d-%Y %I:%M %p')} to {pd.to_datetime(plot_end).strftime('%m-%d-%Y %I:%M %p')}"
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


def _resolve_plot_bounds(
    manifest: Dict[str, object],
    manifest_start_ts: pd.Timestamp,
    candidate_files: List[Path],
    *,
    window_start: Optional[pd.Timestamp],
    window_end: Optional[pd.Timestamp],
) -> Tuple[pd.Timestamp, pd.Timestamp]:
    """Fix the plot's time axis before any sample is read.

    The envelope is bucketed against these bounds as files stream past, so they
    have to be known up front rather than derived from the data afterwards.
    """
    folder_start, folder_end = _manifest_folder_bounds(manifest, manifest_start_ts)
    plot_start = window_start if window_start is not None else folder_start
    plot_end = window_end if window_end is not None else folder_end
    if plot_end is None:
        _, plot_end = _file_window_bounds(candidate_files[-1], manifest_start_ts)
    return plot_start, max(plot_start, plot_end)


def _file_window_bounds(file_path: Path, manifest_start_ts: pd.Timestamp):
    t_ms = pd.read_csv(file_path, usecols=["t_ms"])["t_ms"]
    t_ms = pd.to_numeric(t_ms, errors="coerce").dropna()
    if t_ms.empty:
        raise ValueError(f"No valid t_ms rows found in {file_path.name}")
    start = _to_naive_ts(manifest_start_ts + pd.to_timedelta(float(t_ms.min()), unit="ms"))
    end = _to_naive_ts(manifest_start_ts + pd.to_timedelta(float(t_ms.max()), unit="ms"))
    return start, end


def _read_raw_samples(file_path: Path) -> pd.DataFrame:
    """Load only the accelerometer columns, in the narrowest dtypes that fit."""
    try:
        return pd.read_csv(file_path, usecols=_RAW_COLUMNS, dtype=_RAW_DTYPES)
    except ValueError:
        # Either a column is missing or a value does not fit the narrow dtypes.
        # Re-read with inference so the specific problem can be named.
        data = pd.read_csv(file_path)
    missing = [column for column in _RAW_COLUMNS if column not in data.columns]
    if missing:
        raise ValueError(f"Missing required column(s) {', '.join(missing)} in {file_path.name}")
    data = data[_RAW_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if data["t_ms"].isna().any():
        raise ValueError(f"Found non-numeric t_ms rows in {file_path.name}")
    return data


def _file_signal_arrays(
    file_path: Path,
    *,
    manifest_start_ts: pd.Timestamp,
    keep_start: Optional[pd.Timestamp],
    keep_end: Optional[pd.Timestamp],
) -> Optional[Tuple[np.ndarray, Dict[str, np.ndarray]]]:
    """Derive the acceleration traces for one log file.

    Returns nanosecond timestamps plus one array per plotted series, restricted
    to [keep_start, keep_end], or None when the file contributes nothing to
    that range. The traces stay float64: peak counting resolves ties between
    neighbouring samples, so float32 rounding shifts the reported peak counts.
    """
    data = _read_raw_samples(file_path)
    base_ts = _to_naive_ts(manifest_start_ts)
    # Offsets are medians over the whole file, so they must be taken before any
    # windowing. Reusing the loaded frame avoids reading the file a second time.
    x_offset, y_offset, z_offset = median_centered_offsets_from_frame(data)

    t_ms = data["t_ms"].to_numpy()
    order = None
    if t_ms.size > 1 and not bool(np.all(t_ms[1:] >= t_ms[:-1])):
        order = np.argsort(t_ms, kind="stable")
        t_ms = t_ms[order]

    # Select the window in t_ms space; building timestamps for rows we are about
    # to discard is the single most expensive step in the whole analysis.
    low = 0
    high = t_ms.size
    if keep_start is not None:
        low = int(np.searchsorted(t_ms, (keep_start - base_ts).total_seconds() * 1000.0, side="left"))
    if keep_end is not None:
        high = int(np.searchsorted(t_ms, (keep_end - base_ts).total_seconds() * 1000.0, side="right"))
    if high <= low:
        return None

    def axis(name: str) -> np.ndarray:
        values = data[name].to_numpy()
        if order is not None:
            values = values[order]
        return values[low:high].astype(np.float64)

    x_accel = (axis("X") * _V_REF / 1023.0 - x_offset) / _SENSITIVITY
    y_accel = (axis("Y") * _V_REF / 1023.0 - y_offset) / _SENSITIVITY
    z_accel = (axis("Z") * _V_REF / 1023.0 - z_offset) / _SENSITIVITY + 1.0

    smoothed_x = smooth_signal(x_accel, window_size=5)
    smoothed_y = smooth_signal(y_accel, window_size=5)
    smoothed_z = smooth_signal(z_accel, window_size=5)
    del x_accel, y_accel, z_accel

    times_ns = np.int64(pd.Timestamp(base_ts).value) + np.rint(
        t_ms[low:high].astype(np.float64) * 1e6
    ).astype(np.int64)
    series = {
        "Smoothed_X_Accel": smoothed_x,
        "Smoothed_Y_Accel": smoothed_y,
        "Smoothed_Z_Accel": smoothed_z,
        "Vibration_Accel": np.sqrt(smoothed_x**2 + smoothed_y**2 + smoothed_z**2),
    }
    return times_ns, series


def _prepare_file_frame(
    file_path: Path,
    *,
    manifest_start_ts: pd.Timestamp,
    window_start: Optional[pd.Timestamp],
    window_end: Optional[pd.Timestamp],
):
    """Full-resolution frame for a single log file.

    The aggregated run analysis streams instead; this stays for callers that
    genuinely want one file's samples in hand.
    """
    signals = _file_signal_arrays(
        file_path,
        manifest_start_ts=manifest_start_ts,
        keep_start=window_start,
        keep_end=window_end,
    )
    if signals is None:
        return None
    times_ns, series = signals
    return pd.DataFrame(
        {
            "AbsoluteTime": times_ns.view("datetime64[ns]"),
            "Time": (times_ns - times_ns[0]) / 1e9,
            "Smoothed_X_Accel": series["Smoothed_X_Accel"],
            "Smoothed_Y_Accel": series["Smoothed_Y_Accel"],
            "Smoothed_Z_Accel": series["Smoothed_Z_Accel"],
            "Vibration_Accel": series["Vibration_Accel"],
            "SourceFile": file_path.name,
        }
    )


def _segment_starts(bucket: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Bucket ids and the offset where each run of equal ids begins."""
    if bucket.size == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    starts = np.flatnonzero(np.concatenate(([True], bucket[1:] != bucket[:-1])))
    return bucket[starts], starts


class _RunAggregator:
    """Reduces a streamed run to the two products the analysis actually needs.

    A multi-day run is hundreds of millions of samples, far more than fits in
    memory. Only two things depend on that stream: pulse detection, which needs
    full resolution inside each scheduled window plus its background context,
    and the figure, which cannot resolve more than a few thousand points across
    its whole width. Everything else is dropped as each file is read, so peak
    memory tracks the largest single log file rather than the length of the run.
    """

    def __init__(
        self,
        *,
        detail_spans: List[Tuple[pd.Timestamp, pd.Timestamp]],
        plot_start: pd.Timestamp,
        plot_end: pd.Timestamp,
        peak_bucket_seconds: float,
        plot_buckets: int = _PLOT_DECIMATION_BUCKETS,
    ) -> None:
        self._detail_spans = [(pd.Timestamp(start), pd.Timestamp(end)) for start, end in detail_spans]
        self._detail_parts: List[List[Tuple[np.ndarray, np.ndarray, str]]] = [
            [] for _ in self._detail_spans
        ]
        self._plot_start_ns = int(pd.Timestamp(plot_start).value)
        self._plot_end_ns = int(pd.Timestamp(plot_end).value)
        self._plot_span_ns = max(self._plot_end_ns - self._plot_start_ns, 1)
        self._plot_buckets = max(int(plot_buckets), 1)
        shape = (len(_PLOT_SERIES_COLUMNS), self._plot_buckets)
        self._envelope_min = np.full(shape, np.nan, dtype=np.float32)
        self._envelope_max = np.full(shape, np.nan, dtype=np.float32)
        self._envelope_seen = np.zeros(self._plot_buckets, dtype=bool)
        self._peak_bucket_ns = max(int(peak_bucket_seconds * 1e9), 1)
        self._peaks: Dict[int, Tuple[float, int]] = {}
        self._max_seen_ns: Optional[int] = None
        self._taken: set[int] = set()
        self._read_start = pd.Timestamp(
            min([int(start.value) for start, _ in self._detail_spans] + [self._plot_start_ns])
        )
        self._read_end = pd.Timestamp(
            max([int(end.value) for _, end in self._detail_spans] + [self._plot_end_ns])
        )

    def add_file(self, file_path: Path, *, manifest_start_ts: pd.Timestamp) -> None:
        signals = _file_signal_arrays(
            file_path,
            manifest_start_ts=manifest_start_ts,
            keep_start=self._read_start,
            keep_end=self._read_end,
        )
        if signals is None:
            return
        times_ns, series = signals
        self._max_seen_ns = max(self._max_seen_ns or 0, int(times_ns[-1]))
        self._collect_detail(file_path.name, times_ns, series["Vibration_Accel"])
        self._collect_plot(times_ns, series)

    def settled_span_indices(self) -> List[int]:
        """Spans the reader has passed, which can no longer gain samples.

        Quantifying these as soon as they settle is what keeps memory flat: at
        most a couple of windows are ever held at once, however many pulses the
        run contains.
        """
        if self._max_seen_ns is None:
            return []
        return [
            index
            for index, (_, span_end) in enumerate(self._detail_spans)
            if index not in self._taken and int(span_end.value) <= self._max_seen_ns
        ]

    def remaining_span_indices(self) -> List[int]:
        return [index for index in range(len(self._detail_spans)) if index not in self._taken]

    def take_detail_frame(self, index: int) -> pd.DataFrame:
        """Materialize one window's samples and release the collected parts."""
        parts = self._detail_parts[index]
        self._detail_parts[index] = []
        self._taken.add(index)
        if not parts:
            return pd.DataFrame(
                {
                    "AbsoluteTime": pd.Series(dtype="datetime64[ns]"),
                    "Vibration_Accel": pd.Series(dtype="float64"),
                    "SourceFile": pd.Series(dtype="object"),
                }
            )
        sources = np.repeat(
            np.array([part[2] for part in parts], dtype=object),
            [part[0].size for part in parts],
        )
        return pd.DataFrame(
            {
                "AbsoluteTime": np.concatenate([part[0] for part in parts]).view("datetime64[ns]"),
                "Vibration_Accel": np.concatenate([part[1] for part in parts]),
                "SourceFile": pd.Categorical(sources),
            }
        )

    def plot_frame(self) -> pd.DataFrame:
        """Per-bucket min/max envelope of every plotted series.

        A 300 dpi figure is only a few thousand pixels wide, so two samples per
        bucket render the same as the full trace while preserving peak height.
        """
        indices = np.flatnonzero(self._envelope_seen)
        columns: Dict[str, np.ndarray] = {
            "AbsoluteTime": np.empty(0, dtype="datetime64[ns]"),
        }
        for column in _PLOT_SERIES_COLUMNS:
            columns[column] = np.empty(0, dtype=np.float32)
        if indices.size == 0:
            return pd.DataFrame(columns)

        # Two samples per bucket, drawn inside the bucket, trace the envelope.
        offsets = (indices[:, None] + np.array([0.25, 0.75])) / self._plot_buckets
        edges = self._plot_start_ns + np.rint(offsets * self._plot_span_ns).astype(np.int64)
        columns["AbsoluteTime"] = edges.reshape(-1).view("datetime64[ns]")
        for row, column in enumerate(_PLOT_SERIES_COLUMNS):
            pair = np.stack(
                [self._envelope_min[row, indices], self._envelope_max[row, indices]], axis=1
            )
            columns[column] = pair.reshape(-1)
        return pd.DataFrame(columns)

    def peak_points(self, *, n_max: int, spacing_seconds: float) -> pd.DataFrame:
        """The largest peaks that are at least spacing_seconds apart."""
        spacing_ns = int(max(spacing_seconds, 0.0) * 1e9)
        chosen: List[Tuple[float, int]] = []
        if n_max > 0:
            for value, timestamp_ns in sorted(self._peaks.values(), key=lambda item: -item[0]):
                if all(abs(timestamp_ns - other) >= spacing_ns for _, other in chosen):
                    chosen.append((value, timestamp_ns))
                if len(chosen) == n_max:
                    break
        return pd.DataFrame(
            {
                "AbsoluteTime": np.array([item[1] for item in chosen], dtype=np.int64).view(
                    "datetime64[ns]"
                ),
                "Vibration_Accel": np.array([item[0] for item in chosen], dtype=np.float64),
            }
        )

    def _collect_detail(self, source_name: str, times_ns: np.ndarray, vibration: np.ndarray) -> None:
        for index, (span_start, span_end) in enumerate(self._detail_spans):
            low = int(np.searchsorted(times_ns, int(span_start.value), side="left"))
            high = int(np.searchsorted(times_ns, int(span_end.value), side="right"))
            if high <= low:
                continue
            # Copy, so holding a span does not pin the whole file's arrays.
            self._detail_parts[index].append(
                (times_ns[low:high].copy(), vibration[low:high].copy(), source_name)
            )

    def _collect_plot(self, times_ns: np.ndarray, series: Dict[str, np.ndarray]) -> None:
        low = int(np.searchsorted(times_ns, self._plot_start_ns, side="left"))
        high = int(np.searchsorted(times_ns, self._plot_end_ns, side="right"))
        if high <= low:
            return
        times = times_ns[low:high]
        position = (times - self._plot_start_ns).astype(np.float64) / self._plot_span_ns
        bucket = np.clip((position * self._plot_buckets).astype(np.int64), 0, self._plot_buckets - 1)
        keys, starts = _segment_starts(bucket)
        self._envelope_seen[keys] = True
        for row, column in enumerate(_PLOT_SERIES_COLUMNS):
            values = series[column][low:high]
            self._envelope_min[row, keys] = np.fmin(
                self._envelope_min[row, keys], np.minimum.reduceat(values, starts)
            )
            self._envelope_max[row, keys] = np.fmax(
                self._envelope_max[row, keys], np.maximum.reduceat(values, starts)
            )
        self._collect_peaks(times, series["Vibration_Accel"][low:high])

    def _collect_peaks(self, times_ns: np.ndarray, vibration: np.ndarray) -> None:
        keys, starts = _segment_starts((times_ns - self._plot_start_ns) // self._peak_bucket_ns)
        ends = np.append(starts[1:], times_ns.size)
        for key, start, end in zip(keys, starts, ends):
            offset = int(start) + int(np.argmax(vibration[start:end]))
            value = float(vibration[offset])
            best = self._peaks.get(int(key))
            if best is None or value > best[0]:
                self._peaks[int(key)] = (value, int(times_ns[offset]))


def _plot_aggregated_frame(
    plot_frame: pd.DataFrame,
    output_path: Path,
    *,
    pulse_rows: List[Dict[str, object]],
    chosen_points: pd.DataFrame,
    speed_setting: str,
    earliest_start_ts: Optional[pd.Timestamp],
) -> None:
    fig = Figure(figsize=(10, 6))
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    _draw_aggregated_panel(
        ax,
        plot_frame,
        pulse_rows=pulse_rows,
        chosen_points=chosen_points,
        speed_setting=speed_setting,
        earliest_start_ts=earliest_start_ts,
    )
    fig.tight_layout()
    fig.savefig(output_path, dpi=300)


# Beyond this many pulses per panel, numbered labels and per-pulse edge lines
# only clutter the plot; the markers along the top still show every pulse.
_MAX_LABELED_GENERAL_PULSES = 40


def _draw_aggregated_panel(
    ax,
    plot_frame: pd.DataFrame,
    *,
    pulse_rows: List[Dict[str, object]],
    chosen_points: pd.DataFrame,
    speed_setting: str,
    earliest_start_ts: Optional[pd.Timestamp],
    general: bool = False,
    title_prefix: str = "",
    x_limits: Optional[Tuple[pd.Timestamp, pd.Timestamp]] = None,
    y_limits: Optional[Tuple[float, float]] = None,
) -> None:
    """Draw one vibration panel onto `ax`.

    The scheduled flow draws a single panel with expected windows and numbered
    pulses. With `general` set there is no schedule: every detected pulse is
    shaded and marked along the top, and numbered only while few enough to read.
    """
    if plot_frame.empty:
        ax.text(0.5, 0.5, "No data within selected time window.", ha="center", va="center", transform=ax.transAxes)
        if title_prefix:
            ax.set_title(title_prefix)
        ax.set_axis_off()
        return

    plot_frame = plot_frame.sort_values("AbsoluteTime").reset_index(drop=True)
    series = [
        ("Vibration_Accel", "Vibration Stimulus (g)", 1.0, "black"),
        ("Smoothed_X_Accel", "X Accel", 0.5, None),
        ("Smoothed_Y_Accel", "Y Accel", 0.5, None),
        ("Smoothed_Z_Accel", "Z Accel (adj. for gravity)", 0.5, None),
    ]
    for column, label, linewidth, color in series:
        ax.plot(
            plot_frame["AbsoluteTime"],
            plot_frame[column],
            label=label,
            linewidth=linewidth,
            **({"color": color} if color else {}),
        )

    if not chosen_points.empty:
        ax.scatter(chosen_points["AbsoluteTime"], chosen_points["Vibration_Accel"], color="red", s=20, zorder=5)
        for _, row in chosen_points.iterrows():
            ax.text(row["AbsoluteTime"], row["Vibration_Accel"], f"{row['Vibration_Accel']:.2f}", va="bottom", ha="center", fontsize=8)

    if y_limits is not None:
        ax.set_ylim(y_limits[0], y_limits[1])
    expected_labeled = False
    detected_labeled = False
    suspect_labeled = False
    if general:
        detected_rows = [
            row
            for row in pulse_rows
            if row.get("detected") and row.get("pulse_start") is not None and row.get("pulse_end") is not None
        ]
        y_low, y_high = ax.get_ylim()
        y_gap = max(y_high - y_low, 1e-6)
        marker_y = y_high + 0.04 * y_gap
        if detected_rows:
            ax.set_ylim(y_low, y_high + 0.14 * y_gap)
        label_each = len(detected_rows) <= _MAX_LABELED_GENERAL_PULSES
        for row in detected_rows:
            ax.axvspan(
                row["pulse_start"],
                row["pulse_end"],
                color="grey",
                alpha=0.25,
                label="Detected Pulse" if not detected_labeled else None,
            )
            detected_labeled = True
            middle = row["pulse_start"] + (row["pulse_end"] - row["pulse_start"]) / 2
            ax.plot([middle], [marker_y], marker="v", markersize=4, color="#555555", linestyle="none")
            if label_each:
                ax.axvline(x=row["pulse_start"], color="grey", linestyle="--", linewidth=0.8, alpha=0.5)
                ax.axvline(x=row["pulse_end"], color="grey", linestyle="--", linewidth=0.8, alpha=0.5)
                ax.text(
                    middle,
                    marker_y + 0.02 * y_gap,
                    f"P{int(row.get('PulseIndex') or 0)}",
                    ha="center",
                    va="bottom",
                    fontsize=7,
                    color="black",
                    backgroundcolor="white",
                    alpha=0.8,
                )
        if detected_rows and label_each:
            ax.set_ylim(ax.get_ylim()[0], ax.get_ylim()[1] + 0.06 * y_gap)
        pulse_rows = []
    if x_limits is not None:
        ax.set_xlim(x_limits[0], x_limits[1])
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
        else:
            label_x = expected_start + (expected_end - expected_start) / 2 if expected_start is not None and expected_end is not None else plot_frame["AbsoluteTime"].iloc[0]
        # A pulse lasting a fraction of a second is narrower than a single
        # decimated point on an axis spanning a day, so the label sits above
        # whatever is tallest in the expected window instead of in the span.
        if expected_start is not None and expected_end is not None:
            label_span = (plot_frame["AbsoluteTime"] >= expected_start) & (plot_frame["AbsoluteTime"] <= expected_end)
        else:
            label_span = pd.Series(False, index=plot_frame.index)
        local_max = float(plot_frame.loc[label_span, "Vibration_Accel"].max()) if label_span.any() else float(plot_frame["Vibration_Accel"].max())
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
    combined_start = pd.to_datetime(plot_frame["AbsoluteTime"].iloc[0])
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
    title_lead = f"{title_prefix}\n" if (title_prefix and general) else (f"{title_prefix} | " if title_prefix else "")
    title_kwargs = {"fontsize": 10} if general else {}
    ax.set_title(
        f"{title_lead}Vibration Intensity vs Local Time | Speed Settings: {speed_setting}{title_extra}",
        **title_kwargs,
    )
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
    ax.grid(True, axis="x", which="major", linestyle="--", alpha=0.45)
    ax.grid(True, axis="x", which="minor", linestyle="--", alpha=0.2)
    ax.grid(True, axis="y", linestyle="--", alpha=0.5)


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
    "Peak Force (g)",
    "Peak Amplitude (g RMS)",
    "Noise Floor (g RMS)",
    "Detection Threshold (g RMS)",
    "Edge Threshold (g RMS)",
    "Signal-to-Noise (x)",
    "Area Above Noise (g·s)",
    "Background Burst p99 (g RMS)",
    "Background Burst Max (g RMS)",
    "Background Burst Count",
    "Peak / Background p99",
    "# peaks",
    "Peak Rate (Hz)",
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
        "Peak Force (g)",
        "Peak Amplitude (g RMS)",
        "Noise Floor (g RMS)",
        "Detection Threshold (g RMS)",
        "Edge Threshold (g RMS)",
        "Signal-to-Noise (x)",
        "Area Above Noise (g·s)",
        "# peaks",
        "Peak Rate (Hz)",
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


# ---------------------------------------------------------------------------
# General pulse metrics: every pulse inside user-chosen windows, no schedule.
# ---------------------------------------------------------------------------

_GENERAL_TILE_MINUTES = 30
# Context kept on each side of a tile so the noise floor is well-defined at its
# edges. Only pulses starting inside the tile proper are kept.
_GENERAL_PAD_MINUTES = 5
_GENERAL_PEAK_SPACING_SECONDS = 300.0
_GENERAL_PEAK_MARKERS = 10
_GENERAL_RATE_BIN_MINUTES = 10
# Scanning a whole window, rather than looking for the best excursion inside an
# expected one, meets every noise blip the recording contains. The scheduled
# detector's 6 sigma / 0.04 s picks up a few of those per hour, so the general
# scan asks for a clearer excursion that lasts a little longer.
_GENERAL_DETECTION_SIGMA = 8.0
_GENERAL_MIN_DURATION_S = 0.1

# (name shown in the summary, column in the pulse table)
_GENERAL_METRICS = [
    ("Inter-Pulse Interval (s)", "Inter-Pulse Interval (s)"),
    ("Gap From Previous (s)", "Gap From Previous (s)"),
    ("Duration (s)", "Duration (s)"),
    ("Peak Force (g)", "Peak Force (g)"),
    ("Peaks per Pulse", "# peaks"),
    ("Peak Amplitude (g RMS)", "Peak Amplitude (g RMS)"),
    ("Area Above Noise (g\u00b7s)", "Area Above Noise (g\u00b7s)"),
    ("Peak Rate (Hz)", "Peak Rate (Hz)"),
    ("Signal-to-Noise (x)", "Signal-to-Noise (x)"),
]
_GENERAL_STATS = ("n", "Mean", "SD", "Median", "Min", "Max", "CV (%)")
_COMBINED_LABEL = "Combined"

_GENERAL_PULSE_COLUMNS = [
    "Pulse #",
    "Window",
    "Window #",
    "PulseIndex",
    "Pulse Start ts",
    "Pulse End ts",
    "Time From Window Start (s)",
    "Inter-Pulse Interval (s)",
    "Gap From Previous (s)",
    "Duration (s)",
    "Peak Force (g)",
    "# peaks",
    "Peak Rate (Hz)",
    "Peak Amplitude (g RMS)",
    "Area Above Noise (g\u00b7s)",
    "Signal-to-Noise (x)",
    "Noise Floor (g RMS)",
    "Detection Threshold (g RMS)",
    "Edge Threshold (g RMS)",
    "SourceFile",
]

_GENERAL_DETECTION_SETTINGS = [
    ("Envelope window (s)", 0.025),
    ("Noise floor window (s)", 120.0),
    ("Detection threshold (sigma)", _GENERAL_DETECTION_SIGMA),
    ("Minimum amplitude (g RMS)", 0.004),
    ("Pulse edge (fraction of peak excess)", 0.15),
    ("Pulse edge minimum (sigma)", 3.0),
    ("Gap bridged within one pulse (s)", 0.05),
    ("Minimum pulse duration (s)", _GENERAL_MIN_DURATION_S),
    ("Minimum peak separation (s)", 0.002),
    ("Detection tile (min)", _GENERAL_TILE_MINUTES),
    ("Tile context padding (min)", _GENERAL_PAD_MINUTES),
]


def normalize_analysis_windows(raw_windows) -> List[AnalysisWindow]:
    """Sorted, labeled analysis windows; overlapping windows are an error.

    Overlap would count a pulse in two windows, so it is refused here even
    though the GUI already prevents it.
    """
    parsed = []
    for item in raw_windows or []:
        if isinstance(item, AnalysisWindow):
            start_iso, end_iso, label = item.start_iso, item.end_iso, item.label
        elif isinstance(item, dict):
            start_iso, end_iso, label = item.get("start_iso"), item.get("end_iso"), item.get("label")
        elif isinstance(item, (tuple, list)) and len(item) in (2, 3):
            start_iso, end_iso = item[0], item[1]
            label = item[2] if len(item) == 3 else ""
        else:
            continue
        start_ts = pd.to_datetime(start_iso, errors="coerce")
        end_ts = pd.to_datetime(end_iso, errors="coerce")
        if pd.isna(start_ts) or pd.isna(end_ts):
            continue
        start_ts, end_ts = _to_naive_ts(start_ts), _to_naive_ts(end_ts)
        if end_ts <= start_ts:
            continue
        parsed.append((start_ts, end_ts, str(label or "").strip()))
    parsed.sort(key=lambda item: item[0])
    for previous, current in zip(parsed, parsed[1:]):
        if current[0] < previous[1]:
            raise ValueError(
                "Analysis windows must not overlap "
                f"({previous[0].isoformat()} to {previous[1].isoformat()} overlaps "
                f"{current[0].isoformat()} to {current[1].isoformat()})."
            )

    windows: List[AnalysisWindow] = []
    used = set()
    for index, (start_ts, end_ts, label) in enumerate(parsed, start=1):
        base = label or f"Window {index}"
        unique, suffix = base, 2
        while unique.lower() in used or unique.lower() == _COMBINED_LABEL.lower():
            unique = f"{base} ({suffix})"
            suffix += 1
        used.add(unique.lower())
        windows.append(
            AnalysisWindow(start_iso=start_ts.isoformat(), end_iso=end_ts.isoformat(), label=unique)
        )
    return windows


def estimate_general_file_count(folder_path: str | Path, windows, clock_offset_seconds: float = 0.0) -> int:
    """Log files the general analysis will read, summed across windows."""
    folder = Path(folder_path)
    manifest_path = Path(find_manifest_path(str(folder)))
    manifest = load_manifest(str(manifest_path))
    manifest_start_ts = parse_manifest_start_iso(manifest, str(manifest_path))
    csv_files = discover_csv_files(folder, manifest=manifest)
    if not csv_files:
        return 0
    offset = pd.Timedelta(seconds=float(clock_offset_seconds))
    total = 0
    for window in normalize_analysis_windows(windows):
        total += len(
            _select_candidate_files(
                folder,
                manifest,
                manifest_start_ts,
                csv_files,
                window_start=pd.Timestamp(window.start_iso) - offset,
                window_end=pd.Timestamp(window.end_iso) - offset,
                pad_minutes=_GENERAL_PAD_MINUTES,
            )
        )
    return total


def _analyze_general_window(
    label: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    files: List[Path],
    *,
    manifest_start_ts: pd.Timestamp,
    on_file: Callable[[], None],
) -> GeneralWindowResult:
    """Detect and measure every pulse starting inside [start, end).

    The window is cut into tiles that are detected one at a time as the reader
    moves past them, so memory tracks one tile however long the window is.
    """
    tile = pd.Timedelta(minutes=_GENERAL_TILE_MINUTES)
    pad = pd.Timedelta(minutes=_GENERAL_PAD_MINUTES)
    cores: List[Tuple[pd.Timestamp, pd.Timestamp]] = []
    cursor = start
    while cursor < end:
        cores.append((cursor, min(cursor + tile, end)))
        cursor += tile
    aggregator = _RunAggregator(
        detail_spans=[(core_start - pad, core_end + pad) for core_start, core_end in cores],
        plot_start=start,
        plot_end=end,
        peak_bucket_seconds=_GENERAL_PEAK_SPACING_SECONDS,
    )
    rows_by_tile: Dict[int, List[Dict[str, object]]] = {}

    def detect(indices: List[int]) -> None:
        for index in indices:
            rows_by_tile[index] = detect_all_pulses(
                aggregator.take_detail_frame(index),
                core_start=cores[index][0],
                core_end=cores[index][1],
                detection_sigma_factor=_GENERAL_DETECTION_SIGMA,
                min_duration_s=_GENERAL_MIN_DURATION_S,
            )

    for file_path in files:
        aggregator.add_file(file_path, manifest_start_ts=manifest_start_ts)
        detect(aggregator.settled_span_indices())
        clear_memory_between_files()
        on_file()
    detect(aggregator.remaining_span_indices())

    rows: List[Dict[str, object]] = []
    for index in sorted(rows_by_tile):
        rows.extend(rows_by_tile[index])
    rows.sort(key=lambda row: row["pulse_start"])
    previous: Optional[Dict[str, object]] = None
    for number, row in enumerate(rows, start=1):
        row["PulseIndex"] = number
        row["Order"] = f"Pulse {number}"
        row["Window"] = label
        row["Time From Window Start (s)"] = float((row["pulse_start"] - start).total_seconds())
        # Intervals stay inside the window; the first pulse has no predecessor.
        row["Inter-Pulse Interval (s)"] = (
            float((row["pulse_start"] - previous["pulse_start"]).total_seconds()) if previous else np.nan
        )
        row["Gap From Previous (s)"] = (
            float((row["pulse_start"] - previous["pulse_end"]).total_seconds()) if previous else np.nan
        )
        previous = row

    return GeneralWindowResult(
        label=label,
        start_iso=start.isoformat(),
        end_iso=end.isoformat(),
        pulse_rows=rows,
        plot_frame=aggregator.plot_frame(),
        peak_points=aggregator.peak_points(
            n_max=_GENERAL_PEAK_MARKERS, spacing_seconds=_GENERAL_PEAK_SPACING_SECONDS
        ),
        file_count=len(files),
    )


def run_general_pulse_analysis(
    folder_path: str | Path,
    output_dir: str | Path,
    *,
    windows,
    clock_offset_seconds: float = 0.0,
    progress_callback: ProgressCallback = None,
) -> GeneralPulseAnalysisResult:
    """Find every pulse inside each analysis window and summarize them.

    `windows` are given on the corrected clock (the manifest start plus
    `clock_offset_seconds`); the offset is removed before any log is read.
    """
    folder = Path(folder_path)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    manifest_path = Path(find_manifest_path(str(folder)))
    manifest = load_manifest(str(manifest_path))
    manifest_start_ts = _to_naive_ts(parse_manifest_start_iso(manifest, str(manifest_path)))
    csv_files = discover_csv_files(folder, manifest=manifest)
    if not csv_files:
        raise ValueError("No accelerometer log files were found in the selected folder.")

    analysis_windows = normalize_analysis_windows(windows)
    if not analysis_windows:
        raise ValueError("At least one analysis window is required.")

    offset = pd.Timedelta(seconds=float(clock_offset_seconds))
    plans = []
    for window in analysis_windows:
        start = pd.Timestamp(window.start_iso) - offset
        end = pd.Timestamp(window.end_iso) - offset
        files = _select_candidate_files(
            folder,
            manifest,
            manifest_start_ts,
            csv_files,
            window_start=start,
            window_end=end,
            pad_minutes=_GENERAL_PAD_MINUTES,
        )
        plans.append((window.label, start, end, files))
    if not any(files for _, _, _, files in plans):
        raise ValueError("No log files overlapped the selected analysis windows.")

    total_files = max(sum(len(files) for _, _, _, files in plans), 1)
    started_at = time.monotonic()
    done = {"count": 0}
    processed_files: List[Path] = []
    speed_setting = str(manifest.get("speed", "unknown"))

    def make_on_file(window_label: str) -> Callable[[], None]:
        def on_file() -> None:
            done["count"] += 1
            elapsed = max(time.monotonic() - started_at, 0.001)
            remaining = total_files - done["count"]
            eta_seconds = int(round(elapsed / done["count"] * remaining))
            _emit(
                progress_callback,
                int(done["count"] / total_files * 80) + 5,
                f"{window_label}: processed {done['count']}/{total_files} log files. Rough ETA: {eta_seconds}s",
            )

        return on_file

    window_results: List[GeneralWindowResult] = []
    with prevent_sleep():
        for index, (label, start, end, files) in enumerate(plans, start=1):
            _emit(progress_callback, int(done["count"] / total_files * 80) + 5, f"Analyzing {label}...")
            window_result = _analyze_general_window(
                label,
                start,
                end,
                files,
                manifest_start_ts=manifest_start_ts,
                on_file=make_on_file(label),
            )
            for row in window_result.pulse_rows:
                row["Window #"] = index
            window_results.append(window_result)
            for file_path in files:
                if file_path not in processed_files:
                    processed_files.append(file_path)

        _emit(progress_callback, 88, "Rendering pulse plots and writing workbook...")
        rendered = _render_general_outputs(
            output_path,
            folder_name=folder.name,
            speed_setting=speed_setting,
            windows=window_results,
            manifest_start_ts=manifest_start_ts,
            clock_offset_seconds=float(clock_offset_seconds),
        )

    _emit(progress_callback, 100, "General pulse metrics analysis complete.")
    return GeneralPulseAnalysisResult(
        output_dir=output_path,
        aggregated_plot=rendered["aggregated_plot"],
        distributions_plot=rendered["distributions_plot"],
        workbook=rendered["workbook"],
        zip_path=rendered["zip_path"],
        processed_files=processed_files,
        analyzed_window_label=rendered["window_label"],
        total_pulses=rendered["total_pulses"],
        summaries=rendered["summaries"],
        windows=window_results,
        clock_offset_seconds=float(clock_offset_seconds),
        manifest_start_iso=manifest_start_ts.isoformat(),
        folder_name=folder.name,
        speed_setting=speed_setting,
    )


def rerender_general_pulse_outputs(
    result: GeneralPulseAnalysisResult,
    clock_offset_seconds: float,
    *,
    progress_callback: ProgressCallback = None,
) -> GeneralPulseAnalysisResult:
    """Redraw plots, workbook and zip on a corrected clock without reading any log.

    The windows keep covering the same stretches of data; only the times they
    are labeled with move.
    """
    _emit(progress_callback, 10, "Applying the corrected recording start...")
    rendered = _render_general_outputs(
        result.output_dir,
        folder_name=result.folder_name,
        speed_setting=result.speed_setting,
        windows=result.windows,
        manifest_start_ts=pd.Timestamp(result.manifest_start_iso),
        clock_offset_seconds=float(clock_offset_seconds),
    )
    _emit(progress_callback, 100, "Re-rendered outputs.")
    return replace(
        result,
        aggregated_plot=rendered["aggregated_plot"],
        distributions_plot=rendered["distributions_plot"],
        workbook=rendered["workbook"],
        zip_path=rendered["zip_path"],
        analyzed_window_label=rendered["window_label"],
        total_pulses=rendered["total_pulses"],
        summaries=rendered["summaries"],
        clock_offset_seconds=float(clock_offset_seconds),
    )


def _metric_stats(values) -> Dict[str, float]:
    data = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    count = int(data.size)
    mean = float(data.mean()) if count else np.nan
    sd = float(data.std(ddof=1)) if count > 1 else np.nan
    cv = sd / mean * 100.0 if np.isfinite(sd) and np.isfinite(mean) and mean != 0 else np.nan
    return {
        "n": float(count),
        "Mean": mean,
        "SD": sd,
        "Median": float(data.median()) if count else np.nan,
        "Min": float(data.min()) if count else np.nan,
        "Max": float(data.max()) if count else np.nan,
        "CV (%)": float(cv),
    }


def _general_pulse_frame(windows: List[GeneralWindowResult], offset: pd.Timedelta) -> pd.DataFrame:
    """One row per pulse across all windows, stamped on the corrected clock."""
    records = []
    number = 0
    for window_index, window in enumerate(windows, start=1):
        for row in window.pulse_rows:
            number += 1
            record = {column: row.get(column, np.nan) for column in _GENERAL_PULSE_COLUMNS}
            record["Pulse #"] = number
            record["Window"] = window.label
            record["Window #"] = window_index
            record["PulseIndex"] = row.get("PulseIndex")
            record["Pulse Start ts"] = (pd.Timestamp(row["pulse_start"]) + offset).isoformat()
            record["Pulse End ts"] = (pd.Timestamp(row["pulse_end"]) + offset).isoformat()
            records.append(record)
    frame = pd.DataFrame(records, columns=_GENERAL_PULSE_COLUMNS)
    text_columns = {"Window", "Pulse Start ts", "Pulse End ts", "SourceFile"}
    for column in _GENERAL_PULSE_COLUMNS:
        if column not in text_columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _general_summaries(pulses: pd.DataFrame, labels: List[str]) -> Dict[str, Dict[str, Dict[str, float]]]:
    blocks = list(labels) + ([_COMBINED_LABEL] if len(labels) > 1 else [])
    summaries: Dict[str, Dict[str, Dict[str, float]]] = {}
    for block in blocks:
        subset = pulses if block == _COMBINED_LABEL else pulses[pulses["Window"] == block]
        summaries[block] = {
            name: _metric_stats(subset[column]) for name, column in _GENERAL_METRICS
        }
    return summaries


def _general_summary_frame(summaries: Dict[str, Dict[str, Dict[str, float]]]) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    columns = ["Window", "Metric", *_GENERAL_STATS]
    for position, (block, metrics) in enumerate(summaries.items()):
        if position:
            rows.append({column: np.nan for column in columns})
        for name, stats in metrics.items():
            rows.append({"Window": block, "Metric": name, **stats})
    return pd.DataFrame(rows, columns=columns)


def _general_windows_frame(
    windows: List[GeneralWindowResult], pulses: pd.DataFrame, offset: pd.Timedelta
) -> pd.DataFrame:
    rows = []
    for index, window in enumerate(windows, start=1):
        start = pd.Timestamp(window.start_iso)
        end = pd.Timestamp(window.end_iso)
        seconds = max((end - start).total_seconds(), 1e-9)
        subset = pulses[pulses["Window #"] == index]
        rows.append(
            {
                "Window #": index,
                "Window": window.label,
                "Start": (start + offset).isoformat(),
                "End": (end + offset).isoformat(),
                "Duration (min)": seconds / 60.0,
                "Pulses": int(len(subset)),
                "Pulses per Minute": len(subset) / (seconds / 60.0),
                "Duty Cycle (%)": float(subset["Duration (s)"].sum()) / seconds * 100.0,
                "Log Files": window.file_count,
            }
        )
    return pd.DataFrame(rows)


def _general_hourly_frame(pulses: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Window",
        "Hour Start",
        "Pulses",
        "Mean Interval (s)",
        "Mean Duration (s)",
        "Mean Peak Force (g)",
        "Mean Peaks per Pulse",
    ]
    if pulses.empty:
        return pd.DataFrame(columns=columns)
    work = pulses.copy()
    work["Hour Start"] = pd.to_datetime(work["Pulse Start ts"]).dt.floor("h")
    grouped = work.groupby(["Window #", "Window", "Hour Start"], sort=True)
    hourly = grouped.agg(
        **{
            "Pulses": ("Pulse #", "count"),
            "Mean Interval (s)": ("Inter-Pulse Interval (s)", "mean"),
            "Mean Duration (s)": ("Duration (s)", "mean"),
            "Mean Peak Force (g)": ("Peak Force (g)", "mean"),
            "Mean Peaks per Pulse": ("# peaks", "mean"),
        }
    ).reset_index()
    hourly["Hour Start"] = hourly["Hour Start"].dt.strftime("%Y-%m-%d %H:%M")
    return hourly[columns]


def _general_settings_frame(
    *,
    folder_name: str,
    speed_setting: str,
    manifest_start_ts: pd.Timestamp,
    offset: pd.Timedelta,
    window_count: int,
) -> pd.DataFrame:
    rows = [
        ("Recording folder", folder_name),
        ("Speed setting", speed_setting),
        ("Manifest recording start", manifest_start_ts.isoformat()),
        ("Corrected recording start", (manifest_start_ts + offset).isoformat()),
        ("Clock offset (s)", offset.total_seconds()),
        ("Analysis windows", window_count),
    ] + list(_GENERAL_DETECTION_SETTINGS)
    return pd.DataFrame(rows, columns=["Parameter", "Value"])


def _general_window_label(windows: List[GeneralWindowResult], offset: pd.Timedelta) -> str:
    def fmt(value: str) -> str:
        return (pd.Timestamp(value) + offset).strftime("%m-%d-%Y %I:%M %p")

    return "; ".join(
        f"{window.label}: {fmt(window.start_iso)} to {fmt(window.end_iso)}" for window in windows
    )


def _render_general_outputs(
    output_path: Path,
    *,
    folder_name: str,
    speed_setting: str,
    windows: List[GeneralWindowResult],
    manifest_start_ts: pd.Timestamp,
    clock_offset_seconds: float,
) -> Dict[str, object]:
    """Write the workbook, both plots and the zip for the given clock offset."""
    offset = pd.Timedelta(seconds=float(clock_offset_seconds))
    stem = folder_name.replace(" ", "_")
    output_path.mkdir(parents=True, exist_ok=True)
    aggregated_plot = output_path / f"General_Pulse_Metrics_Aggregated_{stem}.png"
    distributions_plot = output_path / f"General_Pulse_Metrics_Distributions_{stem}.png"
    workbook_path = output_path / f"General_Pulse_Metrics_{stem}.xlsx"
    zip_path = output_path / f"General_Pulse_Metrics_{stem}.zip"

    pulses = _general_pulse_frame(windows, offset)
    summaries = _general_summaries(pulses, [window.label for window in windows])

    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        _general_summary_frame(summaries).to_excel(writer, index=False, sheet_name="Summary")
        pulses.to_excel(writer, index=False, sheet_name="Pulses")
        _general_hourly_frame(pulses).to_excel(writer, index=False, sheet_name="Hourly")
        _general_windows_frame(windows, pulses, offset).to_excel(writer, index=False, sheet_name="Windows")
        _general_settings_frame(
            folder_name=folder_name,
            speed_setting=speed_setting,
            manifest_start_ts=manifest_start_ts,
            offset=offset,
            window_count=len(windows),
        ).to_excel(writer, index=False, sheet_name="Settings")

    _plot_general_aggregated(
        windows,
        aggregated_plot,
        speed_setting=speed_setting,
        earliest_start_ts=manifest_start_ts + offset,
        offset=offset,
    )
    _plot_general_distributions(windows, pulses, summaries, distributions_plot, offset=offset)
    create_zip_from_paths(zip_path, [aggregated_plot, distributions_plot, workbook_path], base_dir=output_path)
    return {
        "aggregated_plot": aggregated_plot,
        "distributions_plot": distributions_plot,
        "workbook": workbook_path,
        "zip_path": zip_path,
        "window_label": _general_window_label(windows, offset),
        "total_pulses": int(len(pulses)),
        "summaries": summaries,
    }


def _shifted_plot_inputs(window: GeneralWindowResult, offset: pd.Timedelta):
    frame = window.plot_frame.copy()
    if not frame.empty:
        frame["AbsoluteTime"] = frame["AbsoluteTime"] + offset
    peaks = window.peak_points.copy()
    if not peaks.empty:
        peaks["AbsoluteTime"] = peaks["AbsoluteTime"] + offset
    rows = []
    for row in window.pulse_rows:
        shifted = dict(row)
        shifted["pulse_start"] = pd.Timestamp(row["pulse_start"]) + offset
        shifted["pulse_end"] = pd.Timestamp(row["pulse_end"]) + offset
        rows.append(shifted)
    return frame, peaks, rows


def _plot_general_aggregated(
    windows: List[GeneralWindowResult],
    output_path: Path,
    *,
    speed_setting: str,
    earliest_start_ts: pd.Timestamp,
    offset: pd.Timedelta,
) -> None:
    """One stacked vibration panel per window, all on the same y scale."""
    count = max(len(windows), 1)
    fig = Figure(figsize=(10, 6) if count == 1 else (10, 4.4 * count))
    FigureCanvasAgg(fig)
    axes = fig.subplots(count, 1, squeeze=False)[:, 0]

    lows, highs = [], []
    for window in windows:
        if window.plot_frame.empty:
            continue
        values = window.plot_frame[list(_PLOT_SERIES_COLUMNS)]
        lows.append(float(np.nanmin(values.to_numpy(dtype=float))))
        highs.append(float(np.nanmax(values.to_numpy(dtype=float))))
    y_limits = None
    if lows:
        low, high = min(lows), max(highs)
        margin = max(high - low, 1e-6) * 0.05
        y_limits = (low - margin, high + margin)

    for ax, window in zip(axes, windows):
        frame, peaks, rows = _shifted_plot_inputs(window, offset)
        _draw_aggregated_panel(
            ax,
            frame,
            pulse_rows=rows,
            chosen_points=peaks,
            speed_setting=speed_setting,
            earliest_start_ts=earliest_start_ts,
            general=True,
            title_prefix=f"{window.label} ({len(window.pulse_rows)} pulses)",
            x_limits=(pd.Timestamp(window.start_iso) + offset, pd.Timestamp(window.end_iso) + offset),
            y_limits=y_limits,
        )
    fig.tight_layout()
    fig.savefig(output_path, dpi=300)


def _plot_general_distributions(
    windows: List[GeneralWindowResult],
    pulses: pd.DataFrame,
    summaries: Dict[str, Dict[str, Dict[str, float]]],
    output_path: Path,
    *,
    offset: pd.Timedelta,
) -> None:
    """Histograms of the key pulse metrics plus pulse rate over time."""
    fig = Figure(figsize=(15, 8.5))
    FigureCanvasAgg(fig)
    axes = fig.subplots(2, 3)
    palette = ["#4f8cff", "#ff7a3d", "#3cb371", "#b36bff", "#e0b100", "#ff5c8a", "#2ec4b6", "#8d99ae"]
    colors = {window.label: palette[index % len(palette)] for index, window in enumerate(windows)}

    histograms = [
        ("Inter-Pulse Interval (s)", "Inter-Pulse Interval (s)", False),
        ("Duration (s)", "Duration (s)", False),
        ("Peak Force (g)", "Peak Force (g)", False),
        ("Peaks per Pulse", "# peaks", True),
        ("Area Above Noise (g\u00b7s)", "Area Above Noise (g\u00b7s)", False),
    ]
    for ax, (name, column, integer) in zip(axes.flat, histograms):
        per_window = {
            window.label: pulses.loc[pulses["Window"] == window.label, column].dropna().to_numpy(dtype=float)
            for window in windows
        }
        pooled = np.concatenate([values for values in per_window.values() if values.size] or [np.empty(0)])
        ax.set_title(name)
        ax.set_ylabel("Pulses")
        if pooled.size == 0:
            ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
            continue
        # A few extreme pulses (e.g. one sustained vibration) would squash every
        # other bar into a single bin, so clip the axis to the bulk of the data
        # and report how many pulses fall beyond it.
        upper = float(pooled.max())
        p99 = float(np.percentile(pooled, 99))
        clipped = upper > 1.5 * p99 and p99 > float(pooled.min())
        if clipped:
            upper = p99 * 1.1
        range_values = pooled[pooled <= upper]
        if integer:
            edges = np.arange(np.floor(range_values.min()) - 0.5, np.ceil(upper) + 1.5, 1.0)
        else:
            edges = np.histogram_bin_edges(
                range_values,
                bins=min(30, max(int(np.sqrt(range_values.size)) + 1, 5)),
                range=(float(range_values.min()), upper),
            )
        if clipped:
            beyond = int(np.sum(pooled > upper))
            ax.text(
                0.98,
                0.02,
                f"{beyond} pulse{'s' if beyond != 1 else ''} beyond axis",
                transform=ax.transAxes,
                ha="right",
                va="bottom",
                fontsize=7,
                style="italic",
                color="#666666",
            )
        for label, values in per_window.items():
            if values.size:
                ax.hist(values, bins=edges, color=colors[label], alpha=0.6, label=label)
        for line, window in enumerate(windows):
            stats = summaries[window.label][name]
            if stats["n"] < 1:
                continue
            sd_text = f" \u00b1 {stats['SD']:.3g}" if np.isfinite(stats["SD"]) else ""
            prefix = f"{window.label}: " if len(windows) > 1 else ""
            ax.text(
                0.98,
                0.97 - 0.075 * line,
                f"{prefix}{stats['Mean']:.3g}{sd_text} (n={int(stats['n'])})",
                transform=ax.transAxes,
                ha="right",
                va="top",
                fontsize=8,
                color=colors[window.label],
            )
        ax.grid(True, axis="y", linestyle="--", alpha=0.4)

    rate_ax = axes.flat[5]
    rate_ax.set_title(f"Pulses per {_GENERAL_RATE_BIN_MINUTES} min")
    rate_ax.set_ylabel("Pulses")
    bin_delta = pd.Timedelta(minutes=_GENERAL_RATE_BIN_MINUTES)
    drew_rate = False
    for window in windows:
        start = pd.Timestamp(window.start_iso)
        end = pd.Timestamp(window.end_iso)
        edges = [start]
        while edges[-1] + bin_delta < end:
            edges.append(edges[-1] + bin_delta)
        edges.append(end)
        starts_ns = np.array(
            [pd.Timestamp(row["pulse_start"]).value for row in window.pulse_rows], dtype=np.int64
        )
        edge_ns = np.array([edge.value for edge in edges], dtype=np.int64)
        counts, _ = np.histogram(starts_ns, bins=edge_ns)
        widths = np.diff(edge_ns) / 86400e9
        shifted_edges = [edge + offset for edge in edges[:-1]]
        rate_ax.bar(
            shifted_edges,
            counts,
            width=widths,
            align="edge",
            color=colors[window.label],
            alpha=0.8,
            label=window.label,
        )
        drew_rate = True
    if drew_rate:
        apply_wall_clock_xaxis(rate_ax)
        rate_ax.set_xlabel("Local Time")
        rate_ax.grid(True, axis="y", linestyle="--", alpha=0.4)
        if len(windows) > 1:
            rate_ax.legend(fontsize="x-small", frameon=True)
    else:
        rate_ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=rate_ax.transAxes)

    if len(windows) > 1:
        handles = [
            Patch(color=colors[window.label], alpha=0.7, label=window.label)
            for window in windows
        ]
        fig.legend(handles=handles, loc="upper right", fontsize="small", ncol=len(handles), frameon=True)
    fig.suptitle("Pulse metric distributions", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(output_path, dpi=200)
