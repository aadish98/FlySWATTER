"""General (unscheduled) pulse metrics: detection inside user-chosen windows."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import services.pulse_service as pulse_service
from services.models import AnalysisWindow, FolderWindowSummary
from services.pulse_service import (
    normalize_analysis_windows,
    rerender_general_pulse_outputs,
    run_general_pulse_analysis,
)
from services.validators import validate_accel_folder_general

_SAMPLE_HZ = 400
_FILE_MINUTES = 20
_FILES = 6
_RUN_START = "2026-08-11T12:00:00"

# (onset s from run start, duration s, counts added to X)
_WINDOW_ONE_PULSES = [
    (400.0, 2.0, 90.0),
    (650.0, 2.0, 90.0),
    (1199.0, 2.0, 90.0),  # straddles the first log-file boundary
    (1500.0, 2.0, 90.0),
    (2099.0, 2.0, 90.0),  # straddles a detection tile boundary
    (2600.0, 2.0, 90.0),
]
_WINDOW_TWO_PULSES = [
    (4300.0, 4.0, 90.0),
    (5000.0, 4.0, 90.0),
    (6500.0, 4.0, 90.0),
]


def _write_run(tmp_path: Path, pulses, *, folder_name: str = "any folder name", file_prefix: str = "my log") -> Path:
    folder = tmp_path / folder_name
    data_dir = folder / "data"
    data_dir.mkdir(parents=True)
    rng = np.random.default_rng(7)
    per_file = _SAMPLE_HZ * _FILE_MINUTES * 60
    parts = []
    for index in range(_FILES):
        offset = index * per_file
        t_ms = (np.arange(offset, offset + per_file) * (1000.0 / _SAMPLE_HZ)).astype(np.int64)
        counts = 512 + rng.normal(scale=0.6, size=per_file)
        for onset, duration, height in pulses:
            active = (t_ms >= onset * 1000) & (t_ms <= (onset + duration) * 1000)
            counts[active] += height
        name = f"{file_prefix}_part{index + 1:03d}.csv"
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
                "path": f"data/{name}",
                "created_iso": (pd.Timestamp(_RUN_START) + pd.Timedelta(minutes=_FILE_MINUTES * index)).isoformat(),
            }
        )
    manifest = {
        "platform": "custom rig",
        "speed": "unit test",
        "start_iso": _RUN_START,
        "end_iso": (pd.Timestamp(_RUN_START) + pd.Timedelta(minutes=_FILE_MINUTES * _FILES)).isoformat(),
        "parts": parts,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def _window(start_s: float, end_s: float, label: str = "", *, shift_s: float = 0.0) -> dict[str, str]:
    start = pd.Timestamp(_RUN_START)
    return {
        "start_iso": (start + pd.Timedelta(seconds=start_s + shift_s)).isoformat(),
        "end_iso": (start + pd.Timedelta(seconds=end_s + shift_s)).isoformat(),
        "label": label,
    }


@pytest.fixture(scope="module")
def run_folder(tmp_path_factory) -> Path:
    return _write_run(tmp_path_factory.mktemp("general"), _WINDOW_ONE_PULSES + _WINDOW_TWO_PULSES)


@pytest.fixture(scope="module")
def two_window_result(run_folder, tmp_path_factory):
    return run_general_pulse_analysis(
        run_folder,
        tmp_path_factory.mktemp("out"),
        windows=[_window(4200, 6900, "Late"), _window(300, 3000, "Early")],
    )


def _pulses_sheet(result) -> pd.DataFrame:
    return pd.read_excel(result.workbook, sheet_name="Pulses")


def test_general_validator_ignores_names_but_checks_columns(tmp_path: Path):
    good = _write_run(tmp_path, _WINDOW_ONE_PULSES, folder_name="whatever", file_prefix="nothing like zantiks")
    assert validate_accel_folder_general(good).valid

    missing_manifest = tmp_path / "no manifest"
    missing_manifest.mkdir()
    (missing_manifest / "a.csv").write_text("t_ms,X,Y,Z\n1,2,3,4\n", encoding="utf-8")
    assert "manifest" in validate_accel_folder_general(missing_manifest).message.lower()

    bad_columns = tmp_path / "bad columns"
    bad_columns.mkdir()
    (bad_columns / "manifest.json").write_text('{"start_iso": "2026-01-01T00:00:00"}', encoding="utf-8")
    (bad_columns / "a.csv").write_text("t_ms,X,Y\n1,2,3\n", encoding="utf-8")
    result = validate_accel_folder_general(bad_columns)
    assert not result.valid
    assert "Z" in " ".join(result.details)

    assert not validate_accel_folder_general(tmp_path / "does not exist").valid


def test_normalize_analysis_windows_sorts_labels_and_rejects_overlap():
    windows = normalize_analysis_windows(
        [
            {"start_iso": "2026-08-11T16:00:00", "end_iso": "2026-08-11T17:00:00"},
            {"start_iso": "2026-08-11T12:00:00", "end_iso": "2026-08-11T13:00:00", "label": "Baseline"},
            {"start_iso": "2026-08-11T14:00:00", "end_iso": "2026-08-11T13:00:00"},
            AnalysisWindow("2026-08-11T13:00:00", "2026-08-11T14:00:00", "Baseline"),
        ]
    )
    assert [window.start_iso for window in windows] == [
        "2026-08-11T12:00:00",
        "2026-08-11T13:00:00",
        "2026-08-11T16:00:00",
    ]
    assert [window.label for window in windows] == ["Baseline", "Baseline (2)", "Window 3"]

    with pytest.raises(ValueError, match="overlap"):
        normalize_analysis_windows(
            [
                {"start_iso": "2026-08-11T12:00:00", "end_iso": "2026-08-11T13:00:00"},
                {"start_iso": "2026-08-11T12:30:00", "end_iso": "2026-08-11T14:00:00"},
            ]
        )


def test_windows_sorted_and_counts_exact(two_window_result):
    result = two_window_result
    assert [window.label for window in result.windows] == ["Early", "Late"]
    assert [len(window.pulse_rows) for window in result.windows] == [6, 3]
    assert result.total_pulses == 9

    pulses = _pulses_sheet(result)
    assert len(pulses) == 9
    assert list(pulses["Window"]) == ["Early"] * 6 + ["Late"] * 3
    assert list(pulses["Pulse #"]) == list(range(1, 10))
    assert not pulses["Pulse Start ts"].duplicated().any()

    run_start = pd.Timestamp(_RUN_START)
    onsets = [onset for onset, _, _ in _WINDOW_ONE_PULSES + _WINDOW_TWO_PULSES]
    detected = (pd.to_datetime(pulses["Pulse Start ts"]) - run_start).dt.total_seconds().to_numpy()
    np.testing.assert_allclose(detected, onsets, atol=0.15)


def test_means_and_durations_match_injected_values(two_window_result):
    pulses = _pulses_sheet(two_window_result)
    early = pulses[pulses["Window"] == "Early"]
    late = pulses[pulses["Window"] == "Late"]
    np.testing.assert_allclose(early["Duration (s)"], 2.0, atol=0.15)
    np.testing.assert_allclose(late["Duration (s)"], 4.0, atol=0.15)

    onsets = np.array([onset for onset, _, _ in _WINDOW_ONE_PULSES])
    summary = two_window_result.summaries["Early"]
    assert summary["Inter-Pulse Interval (s)"]["n"] == 5
    assert summary["Inter-Pulse Interval (s)"]["Mean"] == pytest.approx(np.diff(onsets).mean(), abs=0.15)
    assert summary["Inter-Pulse Interval (s)"]["SD"] == pytest.approx(np.diff(onsets).std(ddof=1), abs=0.3)
    assert summary["Duration (s)"]["Mean"] == pytest.approx(2.0, abs=0.15)
    assert two_window_result.summaries["Late"]["Duration (s)"]["Mean"] == pytest.approx(4.0, abs=0.15)


def test_intervals_never_cross_the_gap_between_windows(two_window_result):
    pulses = _pulses_sheet(two_window_result)
    firsts = pulses[pulses["PulseIndex"] == 1]
    assert len(firsts) == 2
    assert firsts["Inter-Pulse Interval (s)"].isna().all()
    assert firsts["Gap From Previous (s)"].isna().all()
    assert pulses.loc[pulses["Window"] == "Late", "Inter-Pulse Interval (s)"].dropna().size == 2


def test_combined_summary_equals_pooled_pulses(two_window_result):
    pulses = _pulses_sheet(two_window_result)
    combined = two_window_result.summaries["Combined"]
    assert combined["Duration (s)"]["n"] == 9
    assert combined["Duration (s)"]["Mean"] == pytest.approx(pulses["Duration (s)"].mean())
    assert combined["Duration (s)"]["SD"] == pytest.approx(pulses["Duration (s)"].std(ddof=1))
    assert combined["Inter-Pulse Interval (s)"]["n"] == 7
    assert combined["Inter-Pulse Interval (s)"]["Mean"] == pytest.approx(
        pulses["Inter-Pulse Interval (s)"].mean()
    )


def test_workbook_sheets_and_outputs(two_window_result):
    result = two_window_result
    sheets = pd.ExcelFile(result.workbook).sheet_names
    assert sheets == ["Summary", "Pulses", "Hourly", "Windows", "Settings"]
    windows = pd.read_excel(result.workbook, sheet_name="Windows")
    assert list(windows["Pulses"]) == [6, 3]
    summary = pd.read_excel(result.workbook, sheet_name="Summary")
    assert set(summary["Window"].dropna()) == {"Early", "Late", "Combined"}
    for path in (result.aggregated_plot, result.distributions_plot, result.zip_path):
        assert path.exists() and path.stat().st_size > 0


def test_single_window_restricts_results(run_folder, tmp_path: Path):
    result = run_general_pulse_analysis(run_folder, tmp_path, windows=[_window(300, 1800, "Only")])
    assert result.total_pulses == 4
    assert list(result.summaries) == ["Only"]


def test_window_without_pulses_reports_zero(run_folder, tmp_path: Path):
    result = run_general_pulse_analysis(
        run_folder, tmp_path, windows=[_window(7000, 7150, "Quiet"), _window(300, 1000, "Active")]
    )
    assert [len(window.pulse_rows) for window in result.windows] == [2, 0]
    assert result.summaries["Quiet"]["Duration (s)"]["n"] == 0
    windows = pd.read_excel(result.workbook, sheet_name="Windows")
    assert list(windows["Pulses"]) == [2, 0]


def test_run_rejects_overlapping_or_missing_windows(run_folder, tmp_path: Path):
    with pytest.raises(ValueError, match="overlap"):
        run_general_pulse_analysis(
            run_folder, tmp_path, windows=[_window(300, 2000), _window(1500, 3000)]
        )
    with pytest.raises(ValueError, match="analysis window"):
        run_general_pulse_analysis(run_folder, tmp_path, windows=[])


def test_clock_offset_shifts_windows_and_exported_timestamps(run_folder, tmp_path: Path):
    shift = 187.0
    plain = run_general_pulse_analysis(run_folder, tmp_path / "plain", windows=[_window(300, 3000, "A")])
    shifted = run_general_pulse_analysis(
        run_folder,
        tmp_path / "shifted",
        windows=[_window(300, 3000, "A", shift_s=shift)],
        clock_offset_seconds=shift,
    )
    # The same stretch of recording is analyzed whatever the clock says.
    assert shifted.total_pulses == plain.total_pulses == 6
    plain_ts = pd.to_datetime(_pulses_sheet(plain)["Pulse Start ts"])
    shifted_ts = pd.to_datetime(_pulses_sheet(shifted)["Pulse Start ts"])
    assert list((shifted_ts - plain_ts).dt.total_seconds()) == [shift] * 6
    np.testing.assert_allclose(
        _pulses_sheet(plain)["Duration (s)"], _pulses_sheet(shifted)["Duration (s)"], rtol=1e-9
    )


def test_rerender_shifts_timestamps_without_reading_logs(run_folder, tmp_path: Path, monkeypatch):
    result = run_general_pulse_analysis(run_folder, tmp_path, windows=[_window(300, 3000, "A")])
    before = pd.to_datetime(_pulses_sheet(result)["Pulse Start ts"])

    def fail(*_args, **_kwargs):
        raise AssertionError("re-rendering must not read log files")

    monkeypatch.setattr(pulse_service, "_read_raw_samples", fail)
    monkeypatch.setattr(pulse_service, "_file_signal_arrays", fail)
    updated = rerender_general_pulse_outputs(result, 3600.0)

    after = pd.to_datetime(_pulses_sheet(updated)["Pulse Start ts"])
    assert list((after - before).dt.total_seconds()) == [3600.0] * len(before)
    assert updated.clock_offset_seconds == 3600.0
    assert updated.total_pulses == result.total_pulses
    assert updated.aggregated_plot.exists() and updated.zip_path.exists()
    settings = pd.read_excel(updated.workbook, sheet_name="Settings").set_index("Parameter")["Value"]
    assert float(settings["Clock offset (s)"]) == 3600.0


def test_analysis_range_screen_add_remove_and_validation():
    from PySide6.QtCore import QDateTime
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import QApplication

    from gui.screens.analysis_range_screen import AnalysisRangeScreen

    app = QApplication.instance() or QApplication([])
    screen = AnalysisRangeScreen()
    screen.set_summary(
        FolderWindowSummary(
            display_name="demo",
            manifest_path=Path("manifest.json"),
            start_ts_iso="2026-08-11T10:00:00",
            end_ts_iso="2026-08-11T20:00:00",
            csv_files=[Path("a.csv")],
        )
    )
    # Starts with the whole recording as one window, which cannot be removed.
    assert screen.window_count() == 1
    assert not screen._window_rows[0].remove_button.isEnabled()

    screen.add_window()
    screen.add_window()
    assert screen.window_count() == 3
    windows = screen.analysis_windows()
    starts = [datetime.fromisoformat(item["start_iso"]) for item in windows]
    assert starts == sorted(starts)
    assert all(row.remove_button.isEnabled() for row in screen._window_rows)

    screen.remove_window_at(0)
    assert screen.window_count() == 2
    assert [row.title_label.text() for row in screen._window_rows] == ["Window 1", "Window 2"]

    # Make the windows overlap: Start Analysis is blocked and a reason is shown.
    first, second = screen._window_rows
    first.set_range(datetime(2026, 8, 11, 10, 0), datetime(2026, 8, 11, 14, 0))
    second.set_range(datetime(2026, 8, 11, 12, 0), datetime(2026, 8, 11, 16, 0))
    assert not screen.start_button.isEnabled()
    assert "overlap" in screen.status_label.text().lower()

    second.set_range(datetime(2026, 8, 11, 14, 0), datetime(2026, 8, 11, 16, 0))
    assert screen.start_button.isEnabled()
    first.label_edit.setText("Baseline")

    spy = QSignalSpy(screen.continueRequested)
    screen.start_button.click()
    assert spy.count() == 1
    emitted_windows, offset = spy.at(0)
    assert len(emitted_windows) == 2
    assert emitted_windows[0]["label"] == "Baseline"
    assert offset == 0.0

    # Moving the recording start corrects the clock for every analysis window.
    screen.clock_edit.setDateTime(QDateTime(datetime(2026, 8, 11, 10, 5)))
    assert screen.clock_offset_seconds() == 300.0
    app.processEvents()


def test_results_screen_shows_every_window_and_requests_rerender(two_window_result):
    from PySide6.QtCore import QDateTime
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import QApplication

    from gui.screens.general_pulse_results_screen import GeneralPulseResultsScreen

    app = QApplication.instance() or QApplication([])
    screen = GeneralPulseResultsScreen()
    screen.set_result(two_window_result)

    # Metric column, then n / mean / SD / median for each window and Combined.
    assert screen.stats_table.columnCount() == 1 + 4 * 3
    assert screen.stats_table.rowCount() == 9
    header = screen.stats_table.horizontalHeaderItem(1).text()
    assert header.startswith("Early")
    assert screen.clock_offset_seconds() == 0.0

    spy = QSignalSpy(screen.rerenderRequested)
    screen.clock_edit.setDateTime(QDateTime(datetime(2026, 8, 11, 12, 1, 0)))
    screen.apply_button.click()
    assert spy.count() == 1
    assert spy.at(0)[0] == 60.0
    app.processEvents()


def test_main_window_general_flow_uses_general_validation(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from gui.main_window import FlySwatterMainWindow

    app = QApplication.instance() or QApplication([])
    window = FlySwatterMainWindow(tmp_path, tmp_path / "Data", tmp_path)
    window.choose_screen.general_card.clicked.emit()
    assert window._general_mode is True
    assert window.stack.currentWidget() is window.pulse_upload_screen
    assert "any accelerometer log folder" in window.pulse_upload_screen.instructions_label.text().lower()

    window.choose_screen.pulse_card.clicked.emit()
    assert window._general_mode is False
    assert "unchanged" in window.pulse_upload_screen.instructions_label.text().lower()
    app.processEvents()
