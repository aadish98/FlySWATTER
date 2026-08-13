from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.models import FolderWindowSummary, PulseWindow
from services.pulse_service import normalize_pulse_windows, run_pulse_analysis


def test_normalize_pulse_windows_sorts_and_drops_invalid():
    windows = normalize_pulse_windows(
        [
            {"start_iso": "2026-08-11T16:00:00", "end_iso": "2026-08-11T16:05:00"},
            {"start_iso": "2026-08-11T12:00:00", "end_iso": "2026-08-11T12:05:00"},
            {"start_iso": "2026-08-11T14:00:00", "end_iso": "2026-08-11T13:00:00"},
            {"start_iso": "bad", "end_iso": "2026-08-11T12:05:00"},
        ]
    )
    assert [window.start_iso for window in windows] == [
        "2026-08-11T12:00:00",
        "2026-08-11T16:00:00",
    ]


def test_time_window_screen_add_remove_and_order():
    from PySide6.QtWidgets import QApplication

    from gui.screens.time_window_screen import TimeWindowScreen

    app = QApplication.instance() or QApplication([])
    screen = TimeWindowScreen()
    summary = FolderWindowSummary(
        display_name="demo",
        manifest_path=Path("manifest.json"),
        start_ts_iso="2026-08-11T10:00:00",
        end_ts_iso="2026-08-11T18:00:00",
        csv_files=[Path("a.csv"), Path("b.csv")],
    )
    screen.set_summary(summary)
    assert screen.pulse_window_count() == 1
    screen.add_pulse_window()
    screen.add_pulse_window()
    assert screen.pulse_window_count() == 3
    starts = [datetime.fromisoformat(item["start_iso"]) for item in screen.pulse_windows()]
    assert starts == sorted(starts)
    assert starts[1] - starts[0] == timedelta(hours=2)
    screen.remove_pulse_window_at(0)
    assert screen.pulse_window_count() == 2
    labels = [row.title_label.text() for row in screen._pulse_rows]
    assert "Pulse 1" in labels
    app.processEvents()


def _write_synthetic_run(tmp_path: Path) -> Path:
    folder = tmp_path / "08-11-2026 T-12.00pm"
    data_dir = folder / "08112026"
    data_dir.mkdir(parents=True)
    sample_hz = 200
    duration_s = 30
    n = sample_hz * duration_s
    t_ms = np.arange(n) * (1000.0 / sample_hz)
    x = np.full(n, 512, dtype=float)
    y = np.full(n, 512, dtype=float)
    z = np.full(n, 512, dtype=float)
    pulse = (t_ms >= 15000) & (t_ms <= 16500)
    x[pulse] = 620
    csv_path = data_dir / "synthetic_part001.csv"
    pd.DataFrame({"t_ms": t_ms, "sample": np.arange(n), "X": x, "Y": y, "Z": z}).to_csv(csv_path, index=False)
    manifest = {
        "platform": "Zantiks",
        "speed": "R24",
        "start_iso": "2026-08-11T12:00:00",
        "end_iso": "2026-08-11T12:00:30",
        "parts": [
            {
                "path": "08112026/synthetic_part001.csv",
                "created_iso": "2026-08-11T12:00:00",
            }
        ],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def test_run_pulse_analysis_requires_pulse_windows(tmp_path: Path):
    folder = _write_synthetic_run(tmp_path)
    with pytest.raises(ValueError, match="expected pulse window"):
        run_pulse_analysis(folder, tmp_path / "out")


def test_run_pulse_analysis_uses_stable_scheduled_labels(tmp_path: Path):
    folder = _write_synthetic_run(tmp_path)
    output_dir = tmp_path / "out"
    result = run_pulse_analysis(
        folder,
        output_dir,
        window_start_iso="2026-08-11T12:00:00",
        window_end_iso="2026-08-11T12:00:30",
        pulse_windows=[
            PulseWindow(start_iso="2026-08-11T12:00:14", end_iso="2026-08-11T12:00:18"),
            {"start_iso": "2026-08-11T12:00:22", "end_iso": "2026-08-11T12:00:26"},
        ],
    )
    workbook = pd.read_excel(result.aggregated_workbook)
    pulse_rows = workbook[workbook["Order"].astype(str).str.startswith("Pulse")]
    assert list(pulse_rows["PulseIndex"]) == [1, 2]
    assert list(pulse_rows["Detection Status"]) == ["detected", "not detected"]
    assert result.total_pulses == 1
    assert result.aggregated_plot.exists()
