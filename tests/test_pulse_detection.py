from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ConvertAcclLogsToPlots import quantify_scheduled_pulses
from services.models import PulseWindow


def _frame_from_signal(start: datetime, signal: np.ndarray, sample_hz: float = 200.0) -> pd.DataFrame:
    n = len(signal)
    times = [start + timedelta(seconds=i / sample_hz) for i in range(n)]
    return pd.DataFrame(
        {
            "AbsoluteTime": times,
            "Vibration_Accel": signal,
            "SourceFile": "synthetic.csv",
        }
    )


def _window(start: datetime, seconds: float) -> PulseWindow:
    end = start + timedelta(seconds=seconds)
    return PulseWindow(start_iso=start.isoformat(), end_iso=end.isoformat())


def test_quiet_noise_is_not_detected():
    rng = np.random.default_rng(0)
    start = datetime(2026, 8, 11, 12, 0, 0)
    signal = 0.020 + 0.003 * rng.normal(size=2000)
    df = _frame_from_signal(start, signal)
    results = quantify_scheduled_pulses(df, [_window(start + timedelta(seconds=2), 6)])
    assert len(results) == 1
    assert results[0]["Detection Status"] == "not detected"
    assert results[0]["PulseIndex"] == 1


def test_sixty_hz_contamination_is_not_detected():
    start = datetime(2026, 8, 11, 14, 0, 0)
    t = np.arange(0, 10, 1 / 200.0)
    signal = 0.018 + 0.008 * np.sin(2 * np.pi * 60.0 * t)
    df = _frame_from_signal(start, signal, sample_hz=200.0)
    results = quantify_scheduled_pulses(df, [_window(start + timedelta(seconds=2), 6)])
    assert results[0]["Detection Status"] == "not detected"


def test_fragmented_true_response_is_grouped():
    start = datetime(2026, 8, 11, 12, 0, 0)
    t = np.arange(0, 12, 1 / 200.0)
    signal = np.full(t.shape, 0.018)
    first = (t >= 4.0) & (t <= 4.4)
    second = (t >= 5.1) & (t <= 5.6)
    signal[first] += 0.045
    signal[second] += 0.050
    df = _frame_from_signal(start, signal, sample_hz=200.0)
    results = quantify_scheduled_pulses(
        df,
        [_window(start + timedelta(seconds=3.5), 3.0)],
        cluster_gap_s=2.5,
    )
    assert results[0]["Detection Status"] == "detected"
    assert results[0]["Duration (s)"] > 1.0
    assert results[0]["Peak Force (g)"] > 0.05


def test_weak_locally_distinct_pulse_is_detected():
    rng = np.random.default_rng(1)
    start = datetime(2026, 8, 11, 16, 0, 0)
    t = np.arange(0, 20, 1 / 200.0)
    signal = 0.012 + 0.002 * rng.normal(size=t.size)
    pulse = (t >= 10.0) & (t <= 11.4)
    envelope = np.sin(np.pi * (t[pulse] - 10.0) / 1.4)
    signal[pulse] += 0.032 * envelope
    df = _frame_from_signal(start, signal, sample_hz=200.0)
    results = quantify_scheduled_pulses(df, [_window(start + timedelta(seconds=9), 4)])
    assert results[0]["Detection Status"] == "detected"
    assert results[0]["Baseline-Adjusted Area (g·s)"] > 0.01
    assert results[0]["Peak Force (g)"] > 0.03


def test_detected_pulse_offsets_are_relative_to_window():
    start = datetime(2026, 8, 11, 12, 0, 0)
    t = np.arange(0, 20, 1 / 200.0)
    signal = np.full(t.shape, 0.012)
    pulse = (t >= 10.0) & (t <= 11.0)
    signal[pulse] += 0.040
    df = _frame_from_signal(start, signal, sample_hz=200.0)
    window_start = start + timedelta(seconds=8)
    results = quantify_scheduled_pulses(df, [_window(window_start, 6)])
    row = results[0]
    assert row["Detection Status"] == "detected"
    # Pulse begins 2 s into a window that itself starts 8 s into the recording.
    assert 1.5 <= row["Pulse Start (s from window)"] <= 2.5
    assert row["Pulse End (s from window)"] > row["Pulse Start (s from window)"]


def test_periodic_background_is_flagged_not_distinct():
    start = datetime(2026, 8, 11, 12, 0, 0)
    t = np.arange(0, 900, 1 / 100.0)
    signal = np.full(t.shape, 0.012)
    # A spike train every 20 s that runs straight through the expected window.
    for onset in np.arange(5.0, 900.0, 20.0):
        spike = (t >= onset) & (t <= onset + 0.3)
        signal[spike] += 0.040
    df = _frame_from_signal(start, signal, sample_hz=100.0)
    window_start = start + timedelta(seconds=440)
    results = quantify_scheduled_pulses(df, [_window(window_start, 30)], context_minutes=5.0)
    row = results[0]
    assert row["Detection Status"] == "detected"
    assert row["Background Check"] == "matches background"
    assert row["Background Peak Count"] > 10


def test_distinct_pulse_is_marked_distinct():
    rng = np.random.default_rng(3)
    start = datetime(2026, 8, 11, 12, 0, 0)
    t = np.arange(0, 900, 1 / 100.0)
    signal = 0.012 + 0.002 * rng.normal(size=t.size)
    onset = 450.0
    pulse = (t >= onset) & (t <= onset + 1.0)
    signal[pulse] += 0.060
    df = _frame_from_signal(start, signal, sample_hz=100.0)
    window_start = start + timedelta(seconds=445)
    results = quantify_scheduled_pulses(df, [_window(window_start, 20)], context_minutes=5.0)
    row = results[0]
    assert row["Detection Status"] == "detected"
    assert row["Background Check"] == "distinct"


def test_short_strong_pulse_beats_long_noise_blob():
    start = datetime(2026, 8, 11, 16, 0, 0)
    t = np.arange(0, 20, 1 / 200.0)
    signal = np.full(t.shape, 0.012)
    pulse = (t >= 4.0) & (t <= 5.2)
    blob = (t >= 10.0) & (t <= 16.0)
    signal[pulse] += 0.032 * np.sin(np.pi * (t[pulse] - 4.0) / 1.2)
    signal[blob] += 0.016
    df = _frame_from_signal(start, signal, sample_hz=200.0)
    results = quantify_scheduled_pulses(df, [_window(start + timedelta(seconds=2), 16)])
    assert results[0]["Detection Status"] == "detected"
    pulse_start = pd.Timestamp(results[0]["Pulse Start ts"])
    assert pulse_start.second < 8


def test_empty_window_is_not_detected():
    start = datetime(2026, 8, 11, 12, 0, 0)
    signal = np.full(400, 0.02)
    df = _frame_from_signal(start, signal)
    later = start + timedelta(minutes=30)
    results = quantify_scheduled_pulses(df, [_window(later, 5)])
    assert results[0]["Detection Status"] == "not detected"
    assert results[0]["Order"] == "Pulse 1"
