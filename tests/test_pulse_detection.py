"""Detection is measured on the RMS envelope of an oscillating stimulus.

The traps these guard against are all ways of describing a burst as longer than
it is: bridging quiet gaps, chasing a decay into the noise, or locking onto a
periodic artefact that happens to run through the expected window.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ConvertAcclLogsToPlots import quantify_scheduled_pulses, rms_envelope
from services.models import PulseWindow

_SAMPLE_HZ = 1000.0


def _oscillation(t, onset: float, duration: float, amplitude: float, hz: float = 90.0):
    """A vibration burst: a carrier that only exists between onset and offset."""
    active = (t >= onset) & (t <= onset + duration)
    return active * amplitude * np.sin(2 * np.pi * hz * t)


def _frame(start: datetime, signal: np.ndarray, sample_hz: float = _SAMPLE_HZ) -> pd.DataFrame:
    times = start + pd.to_timedelta(np.arange(signal.size) / sample_hz, unit="s")
    return pd.DataFrame(
        {
            "AbsoluteTime": times,
            "Vibration_Accel": np.abs(signal),
            "SourceFile": "synthetic.csv",
        }
    )


def _window(start: datetime, seconds: float) -> PulseWindow:
    return PulseWindow(
        start_iso=start.isoformat(), end_iso=(start + timedelta(seconds=seconds)).isoformat()
    )


def _quiet(t, rng, level: float = 0.012):
    """Ambient vibration: an oscillation of its own, not a DC offset."""
    return level * rng.normal(size=t.size)


def test_rms_envelope_follows_the_amplitude_of_an_oscillation():
    t = np.arange(0, 2.0, 1 / _SAMPLE_HZ)
    signal = np.abs(_oscillation(t, 1.0, 0.3, 0.5))
    stamps = (t * 1e9).astype(np.int64)
    envelope = rms_envelope(signal, stamps, 0.025)
    inside = (t >= 1.05) & (t <= 1.25)
    outside = (t < 0.9) | (t > 1.45)
    # A rectified sine of amplitude A has RMS A/sqrt(2).
    assert np.allclose(envelope[inside].mean(), 0.5 / np.sqrt(2), rtol=0.05)
    assert envelope[outside].max() < 0.01


def test_rms_envelope_is_unchanged_by_surrounding_data():
    """A window's numbers must not move when a wider span is analysed."""
    t = np.arange(0, 6.0, 1 / _SAMPLE_HZ)
    signal = np.abs(_oscillation(t, 3.0, 0.3, 0.5)) + 0.01
    stamps = (t * 1e9).astype(np.int64)
    middle = (t >= 2.0) & (t <= 4.0)
    whole = rms_envelope(signal, stamps, 0.025)[middle]
    alone = rms_envelope(signal[middle], stamps[middle], 0.025)
    # The running sum starts from a different sample either way, so the two
    # agree to rounding rather than bit for bit.
    interior = slice(50, -50)
    np.testing.assert_allclose(whole[interior], alone[interior], rtol=1e-12)


def test_measured_duration_matches_the_burst_not_the_window():
    rng = np.random.default_rng(0)
    start = datetime(2026, 8, 12, 12, 0, 0)
    t = np.arange(0, 600.0, 1 / _SAMPLE_HZ)
    signal = _quiet(t, rng) + _oscillation(t, 300.0, 0.4, 0.18)
    # A window minutes wide, as the GUI produces when the schedule is uncertain.
    results = quantify_scheduled_pulses(_frame(start, signal), [_window(start + timedelta(seconds=120), 360)])
    row = results[0]
    assert row["Detection Status"] == "detected"
    assert 0.4 <= row["Duration (s)"] <= 0.6
    assert 179.9 <= row["Pulse Start (s from window)"] <= 180.2


def test_a_burst_is_not_bridged_to_a_separate_knock():
    """The old detector merged anything within seconds, which is what turned a
    fraction-of-a-second stimulus into a span minutes long."""
    rng = np.random.default_rng(1)
    start = datetime(2026, 8, 12, 12, 0, 0)
    t = np.arange(0, 400.0, 1 / _SAMPLE_HZ)
    signal = (
        _quiet(t, rng)
        + _oscillation(t, 200.0, 0.3, 0.18)
        + _oscillation(t, 202.0, 0.2, 0.10)
        + _oscillation(t, 197.5, 0.2, 0.10)
    )
    row = quantify_scheduled_pulses(_frame(start, signal), [_window(start + timedelta(seconds=100), 200)])[0]
    assert row["Detection Status"] == "detected"
    assert row["Duration (s)"] < 0.6
    assert 99.9 <= row["Pulse Start (s from window)"] <= 100.2


def test_a_dip_inside_one_burst_does_not_split_it():
    """A motor ramp dips without stopping, so sub-bridge gaps stay one pulse."""
    rng = np.random.default_rng(2)
    start = datetime(2026, 8, 12, 12, 0, 0)
    t = np.arange(0, 400.0, 1 / _SAMPLE_HZ)
    signal = (
        _quiet(t, rng)
        + _oscillation(t, 200.0, 0.5, 0.18)
        + _oscillation(t, 200.52, 0.5, 0.18)
    )
    row = quantify_scheduled_pulses(_frame(start, signal), [_window(start + timedelta(seconds=100), 200)])[0]
    assert row["Detection Status"] == "detected"
    assert 1.0 <= row["Duration (s)"] <= 1.2


def test_duration_is_independent_of_amplitude():
    """Boundaries sit at a fraction of each burst's own peak, so a loud pulse
    and a faint one of the same length measure the same length."""
    rng = np.random.default_rng(3)
    start = datetime(2026, 8, 12, 12, 0, 0)
    t = np.arange(0, 400.0, 1 / _SAMPLE_HZ)
    durations = []
    for amplitude in (0.04, 0.20, 1.00):
        signal = _quiet(t, rng) + _oscillation(t, 200.0, 0.8, amplitude)
        row = quantify_scheduled_pulses(
            _frame(start, signal), [_window(start + timedelta(seconds=100), 200)]
        )[0]
        assert row["Detection Status"] == "detected"
        durations.append(row["Duration (s)"])
    assert max(durations) - min(durations) < 0.1
    assert all(0.75 <= duration <= 1.0 for duration in durations)


def test_quiet_noise_is_not_detected():
    rng = np.random.default_rng(4)
    start = datetime(2026, 8, 12, 12, 0, 0)
    t = np.arange(0, 400.0, 1 / _SAMPLE_HZ)
    row = quantify_scheduled_pulses(
        _frame(start, _quiet(t, rng)), [_window(start + timedelta(seconds=100), 200)]
    )[0]
    assert row["Detection Status"] == "not detected"
    assert row["PulseIndex"] == 1
    assert np.isnan(row["Duration (s)"])


def test_steady_mains_hum_is_not_detected():
    start = datetime(2026, 8, 12, 12, 0, 0)
    t = np.arange(0, 400.0, 1 / _SAMPLE_HZ)
    signal = 0.02 * np.sin(2 * np.pi * 60.0 * t)
    row = quantify_scheduled_pulses(_frame(start, signal), [_window(start + timedelta(seconds=100), 200)])[0]
    assert row["Detection Status"] == "not detected"


def test_a_single_sample_glitch_is_not_a_pulse():
    """One bad ADC reading towers over the trace but carries no energy, and the
    envelope divides it across the averaging window."""
    rng = np.random.default_rng(5)
    start = datetime(2026, 8, 12, 12, 0, 0)
    t = np.arange(0, 400.0, 1 / _SAMPLE_HZ)
    signal = _quiet(t, rng)
    signal[t.size // 2] = 0.9
    row = quantify_scheduled_pulses(_frame(start, signal), [_window(start + timedelta(seconds=100), 200)])[0]
    assert row["Detection Status"] == "not detected"


def test_a_drifting_noise_floor_does_not_become_a_pulse():
    """Ambient vibration ramps up for minutes as equipment cycles; the tracked
    floor follows it, so only the burst on top of it is a pulse."""
    rng = np.random.default_rng(6)
    start = datetime(2026, 8, 12, 12, 0, 0)
    t = np.arange(0, 900.0, 1 / _SAMPLE_HZ)
    drift = 0.008 + 0.02 * np.clip((t - 300.0) / 300.0, 0.0, 1.0)
    signal = drift * rng.normal(size=t.size) + _oscillation(t, 700.0, 0.5, 0.25)
    row = quantify_scheduled_pulses(_frame(start, signal), [_window(start + timedelta(seconds=60), 800)])[0]
    assert row["Detection Status"] == "detected"
    assert 639.9 <= row["Pulse Start (s from window)"] <= 640.3
    assert row["Duration (s)"] < 0.8


def test_periodic_artefact_running_through_the_window_is_flagged():
    rng = np.random.default_rng(7)
    start = datetime(2026, 8, 12, 12, 0, 0)
    t = np.arange(0, 1800.0, 1 / _SAMPLE_HZ)
    signal = _quiet(t, rng)
    for onset in np.arange(10.0, 1800.0, 20.0):
        signal = signal + _oscillation(t, onset, 0.4, 0.06)
    row = quantify_scheduled_pulses(
        _frame(start, signal), [_window(start + timedelta(seconds=880), 60)], context_minutes=10.0
    )[0]
    assert row["Detection Status"] == "detected"
    assert row["Background Check"] == "matches background"
    assert row["Background Burst Count"] > 10


def test_a_real_stimulus_stands_clear_of_that_same_artefact():
    rng = np.random.default_rng(8)
    start = datetime(2026, 8, 12, 12, 0, 0)
    t = np.arange(0, 1800.0, 1 / _SAMPLE_HZ)
    signal = _quiet(t, rng)
    for onset in np.arange(10.0, 1800.0, 20.0):
        signal = signal + _oscillation(t, onset, 0.4, 0.06)
    signal = signal + _oscillation(t, 900.0, 0.4, 0.40)
    row = quantify_scheduled_pulses(
        _frame(start, signal), [_window(start + timedelta(seconds=880), 60)], context_minutes=10.0
    )[0]
    assert row["Detection Status"] == "detected"
    assert row["Background Check"] == "distinct"
    assert row["Peak / Background p99"] > 2.0
    assert row["Duration (s)"] < 0.7


def test_reported_metrics_describe_the_burst():
    rng = np.random.default_rng(9)
    start = datetime(2026, 8, 12, 12, 0, 0)
    t = np.arange(0, 400.0, 1 / _SAMPLE_HZ)
    signal = _quiet(t, rng) + _oscillation(t, 200.0, 1.0, 0.30, hz=120.0)
    row = quantify_scheduled_pulses(_frame(start, signal), [_window(start + timedelta(seconds=100), 200)])[0]
    assert row["Detection Status"] == "detected"
    assert 0.28 <= row["Peak Force (g)"] <= 0.35
    # A rectified 120 Hz carrier peaks twice a cycle.
    assert 200 <= row["Peak Rate (Hz)"] <= 260
    assert row["Signal-to-Noise (x)"] > 20
    # Area is amplitude above the floor integrated over roughly a second.
    assert 0.15 <= row["Area Above Noise (g·s)"] <= 0.25
    assert pd.Timestamp(row["Pulse End ts"]) > pd.Timestamp(row["Pulse Start ts"])


def test_manual_threshold_overrides_the_tracked_floor():
    rng = np.random.default_rng(10)
    start = datetime(2026, 8, 12, 12, 0, 0)
    t = np.arange(0, 400.0, 1 / _SAMPLE_HZ)
    signal = _quiet(t, rng) + _oscillation(t, 200.0, 0.5, 0.06)
    window = [_window(start + timedelta(seconds=100), 200)]
    frame = _frame(start, signal)
    assert quantify_scheduled_pulses(frame, window)[0]["Detection Status"] == "detected"
    row = quantify_scheduled_pulses(frame, window, manual_threshold=0.5)[0]
    assert row["Detection Status"] == "not detected"


def test_empty_window_is_not_detected():
    start = datetime(2026, 8, 12, 12, 0, 0)
    rng = np.random.default_rng(11)
    t = np.arange(0, 10.0, 1 / _SAMPLE_HZ)
    frame = _frame(start, _quiet(t, rng))
    row = quantify_scheduled_pulses(frame, [_window(start + timedelta(minutes=30), 5)])[0]
    assert row["Detection Status"] == "not detected"
    assert row["Order"] == "Pulse 1"
    assert row["Background Check"] == "no comparison"
