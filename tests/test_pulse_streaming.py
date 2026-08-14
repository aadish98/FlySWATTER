"""The run is analysed as a stream, so guard what that streaming can break."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ConvertAcclLogsToPlots import quantify_scheduled_pulses
from services.pulse_service import (
    _CONTEXT_PADDING_MINUTES,
    _prepare_file_frame,
    _RunAggregator,
    discover_csv_files,
    run_pulse_analysis,
)

_SAMPLE_HZ = 400
_FILE_MINUTES = 20
_RUN_START = "2026-08-11T12:00:00"


def _write_run(tmp_path: Path, *, files: int = 6, pulse_offsets_s=(1500.0, 4500.0)) -> Path:
    """A multi-file run whose pulses straddle log-file boundaries."""
    folder = tmp_path / "08-11-2026 T-12.00pm"
    data_dir = folder / "08112026"
    data_dir.mkdir(parents=True)
    rng = np.random.default_rng(7)
    per_file = _SAMPLE_HZ * _FILE_MINUTES * 60
    parts = []
    for index in range(files):
        offset = index * per_file
        t_ms = (np.arange(offset, offset + per_file) * (1000.0 / _SAMPLE_HZ)).astype(np.int64)
        counts = 512 + rng.normal(scale=0.6, size=per_file)
        for onset in pulse_offsets_s:
            pulse = (t_ms >= onset * 1000) & (t_ms <= (onset + 2.0) * 1000)
            counts[pulse] += 90.0
        name = f"synthetic_part{index + 1:03d}.csv"
        pd.DataFrame(
            {
                "t_ms": t_ms,
                "X": counts.round().astype(int),
                "Y": np.full(per_file, 512),
                "Z": np.full(per_file, 512),
            }
        ).to_csv(data_dir / name, index=False)
        parts.append(
            {
                "path": f"08112026/{name}",
                "created_iso": (
                    pd.Timestamp(_RUN_START) + pd.Timedelta(minutes=_FILE_MINUTES * index)
                ).isoformat(),
            }
        )
    manifest = {
        "platform": "Zantiks",
        "speed": "R24",
        "start_iso": _RUN_START,
        "end_iso": (pd.Timestamp(_RUN_START) + pd.Timedelta(minutes=_FILE_MINUTES * files)).isoformat(),
        "parts": parts,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def _windows(pulse_offsets_s) -> list[dict[str, str]]:
    start = pd.Timestamp(_RUN_START)
    return [
        {
            "start_iso": (start + pd.Timedelta(seconds=offset - 30)).isoformat(),
            "end_iso": (start + pd.Timedelta(seconds=offset + 30)).isoformat(),
        }
        for offset in pulse_offsets_s
    ]


def test_streaming_run_matches_whole_run_in_memory(tmp_path: Path):
    """Streaming must not change a single reported number.

    The pipeline never holds the whole run any more, so this pins the streamed
    result against the same data quantified as one in-memory frame.
    """
    pulse_offsets = (1500.0, 4500.0)
    folder = _write_run(tmp_path, pulse_offsets_s=pulse_offsets)
    windows = _windows(pulse_offsets)

    result = run_pulse_analysis(folder, tmp_path / "out", pulse_windows=windows)
    streamed = pd.read_excel(result.aggregated_workbook)
    streamed = streamed[streamed["Order"].astype(str).str.startswith("Pulse")]

    manifest_start = pd.Timestamp(_RUN_START)
    combined = pd.concat(
        [
            _prepare_file_frame(path, manifest_start_ts=manifest_start, window_start=None, window_end=None)
            for path in discover_csv_files(folder)
        ],
        ignore_index=True,
    )
    expected = pd.DataFrame(quantify_scheduled_pulses(combined, windows))

    assert list(streamed["Detection Status"]) == list(expected["Detection Status"])
    assert list(streamed["Pulse Start ts"]) == list(expected["Pulse Start ts"])
    assert list(streamed["Pulse End ts"]) == list(expected["Pulse End ts"])
    assert list(streamed["Background Check"]) == list(expected["Background Check"])
    for column in [
        "Peak Force (g)",
        "Noise Floor (g RMS)",
        "Area Above Noise (g\u00b7s)",
        "# peaks",
    ]:
        np.testing.assert_allclose(
            streamed[column].to_numpy(dtype=float),
            expected[column].to_numpy(dtype=float),
            rtol=1e-9,
        )


def test_windows_are_released_once_the_reader_passes_them(tmp_path: Path):
    """Memory is only flat because settled windows are handed back and dropped.

    Without this the aggregator holds every window's samples until the last
    file is read, which is what made a multi-day run exhaust RAM.
    """
    pulse_offsets = tuple(600.0 + 1200.0 * step for step in range(5))
    folder = _write_run(tmp_path, files=6, pulse_offsets_s=pulse_offsets)
    manifest_start = pd.Timestamp(_RUN_START)
    padding = pd.Timedelta(minutes=_CONTEXT_PADDING_MINUTES)
    windows = _windows(pulse_offsets)
    aggregator = _RunAggregator(
        detail_spans=[
            (pd.Timestamp(window["start_iso"]) - padding, pd.Timestamp(window["end_iso"]) + padding)
            for window in windows
        ],
        plot_start=manifest_start,
        plot_end=manifest_start + pd.Timedelta(minutes=_FILE_MINUTES * 6),
        peak_bucket_seconds=300.0,
    )

    held_rows = 0
    for path in discover_csv_files(folder):
        aggregator.add_file(path, manifest_start_ts=manifest_start)
        settled = aggregator.settled_span_indices()
        assert not any(index in settled for index in aggregator._taken)
        for index in settled:
            aggregator.take_detail_frame(index)
        held_rows = max(held_rows, sum(len(parts) for parts in aggregator._detail_parts))

    # Some windows settle mid-run rather than all piling up until the end.
    assert len(aggregator.remaining_span_indices()) < len(windows)
    assert held_rows > 0


def test_tied_timestamps_are_ordered_the_same_however_much_data_is_supplied():
    """Unrelated data elsewhere in the run must not move a window's numbers.

    The logger stamps whole milliseconds while sampling faster than 1 kHz, so a
    large share of samples tie. Sorting those ties with an unstable sort orders
    them differently depending on how big the frame is, which shifted reported
    areas and peak counts purely with the number of log files loaded. The frame
    has to be large for an unstable sort to actually reorder anything.
    """
    start = pd.Timestamp("2026-08-11T12:00:00")
    rng = np.random.default_rng(11)
    n = 2_000_000
    t_ms = (np.arange(n) * (1000.0 / 1300.0)).astype(np.int64)
    signal = 0.012 + 0.002 * rng.normal(size=n)
    # Alternate hard within each tie group so any reordering shows up.
    signal[1::2] += 0.010
    signal[(t_ms >= 20_000) & (t_ms <= 21_500)] += 0.05
    assert pd.Series(t_ms).duplicated().sum() > n // 5

    frame = pd.DataFrame(
        {
            "AbsoluteTime": start + pd.to_timedelta(t_ms, unit="ms"),
            "Vibration_Accel": signal,
            "SourceFile": "synthetic.csv",
        }
    )
    window = [
        {
            "start_iso": (start + pd.Timedelta(seconds=15)).isoformat(),
            "end_iso": (start + pd.Timedelta(seconds=25)).isoformat(),
        }
    ]
    # Data hours away, outside both the baseline and the background context.
    trailing = frame.copy()
    trailing["AbsoluteTime"] = trailing["AbsoluteTime"] + pd.Timedelta(hours=3)
    padded = pd.concat([frame, trailing], ignore_index=True)

    alone = quantify_scheduled_pulses(frame, window, context_minutes=1.0)[0]
    with_padding = quantify_scheduled_pulses(padded, window, context_minutes=1.0)[0]
    for key in ["Area Above Noise (g\u00b7s)", "Peak Force (g)", "# peaks", "Duration (s)"]:
        assert alone[key] == with_padding[key], key
