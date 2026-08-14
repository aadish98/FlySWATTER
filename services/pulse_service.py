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
    clear_memory_between_files,
    median_centered_offsets_from_frame,
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
    if plot_frame.empty:
        ax.text(0.5, 0.5, "No data within selected time window.", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        fig.tight_layout()
        fig.savefig(output_path, dpi=300)
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
