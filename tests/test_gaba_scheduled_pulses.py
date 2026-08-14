"""Regression tests against a recorded arousal run, when it is available.

The run's speed setting is `..._P150_P400_P2000_...`, so the three stimuli of
each day are 150 ms, 400 ms and 2000 ms of motor drive. That gives the one
thing synthetic data cannot: a known answer for how long a pulse lasts, across
a thirteen-fold range of durations.

11 August is a negative control. Its windows contain no stimulus at all, only a
periodic artefact that fires every twenty seconds, so nothing in them may be
measured as a pulse-shaped event.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ConvertAcclLogsToPlots import (
    find_manifest_path,
    load_manifest,
    parse_manifest_start_iso,
    quantify_scheduled_pulses,
)
from services.pulse_service import _CONTEXT_PADDING_MINUTES, _RunAggregator, _to_naive_ts

_RUN_NAME = "08-10-2026 T-3.54pm"
_CANDIDATE_ROOTS = [
    Path(
        "/Volumes/umms-rallada/UM Lab Users/Farheen/Arousal Experiments/"
        f"GABA_Arousal_run1/{_RUN_NAME}"
    ),
    Path.home() / "Downloads" / _RUN_NAME,
]
GABA_ROOT = next((root for root in _CANDIDATE_ROOTS if root.exists()), None)

_NOMINAL_S = [0.150, 0.400, 2.000]
# Windows are deliberately twenty minutes wide. The stimulus is a fraction of a
# second, and the failure this guards against is a detector that describes the
# window rather than the burst inside it.
_STIMULUS_DAY = {
    "name": "stimulus",
    "parts": ("045", "047", "049"),
    "windows": [
        {"start_iso": "2026-08-12T11:45:00", "end_iso": "2026-08-12T12:05:00"},
        {"start_iso": "2026-08-12T13:45:00", "end_iso": "2026-08-12T14:05:00"},
        {"start_iso": "2026-08-12T15:45:00", "end_iso": "2026-08-12T16:05:00"},
    ],
    "onsets": [
        "2026-08-12T11:57:21.0",
        "2026-08-12T13:55:19.0",
        "2026-08-12T15:55:50.5",
    ],
}
_CONTROL_DAY = {
    "name": "control",
    "parts": ("021", "023", "025"),
    "windows": [
        {"start_iso": "2026-08-11T11:58:00", "end_iso": "2026-08-11T12:03:00"},
        {"start_iso": "2026-08-11T13:58:00", "end_iso": "2026-08-11T14:06:00"},
        {"start_iso": "2026-08-11T15:58:00", "end_iso": "2026-08-11T16:04:00"},
    ],
}

pytestmark = pytest.mark.skipif(
    GABA_ROOT is None, reason="recorded arousal run is not available locally"
)


_CACHE: dict[str, list[dict]] = {}


def _quantify(day) -> list[dict]:
    """Reading a day of compressed logs costs a minute, so do it once."""
    if day["name"] not in _CACHE:
        _CACHE[day["name"]] = _quantify_uncached(day)
    return _CACHE[day["name"]]


def _quantify_uncached(day) -> list[dict]:
    manifest_path = Path(find_manifest_path(str(GABA_ROOT)))
    manifest = load_manifest(str(manifest_path))
    manifest_start = _to_naive_ts(parse_manifest_start_iso(manifest, str(manifest_path)))
    windows = day["windows"]
    padding = pd.Timedelta(minutes=_CONTEXT_PADDING_MINUTES)
    aggregator = _RunAggregator(
        detail_spans=[
            (pd.Timestamp(window["start_iso"]) - padding, pd.Timestamp(window["end_iso"]) + padding)
            for window in windows
        ],
        plot_start=pd.Timestamp(windows[0]["start_iso"]),
        plot_end=pd.Timestamp(windows[-1]["end_iso"]),
        peak_bucket_seconds=300.0,
    )
    for part in day["parts"]:
        aggregator.add_file(
            next(GABA_ROOT.rglob(f"*part{part}.csv.gz")), manifest_start_ts=manifest_start
        )
    return [
        quantify_scheduled_pulses(
            aggregator.take_detail_frame(index),
            [window],
            exclude_windows=windows,
            start_index=index + 1,
        )[0]
        for index, window in enumerate(windows)
    ]


def test_measured_duration_tracks_the_programmed_pulse_length():
    rows = _quantify(_STIMULUS_DAY)
    assert [row["PulseIndex"] for row in rows] == [1, 2, 3]
    assert all(row["Detection Status"] == "detected" for row in rows)
    for row, nominal in zip(rows, _NOMINAL_S):
        # Never shorter than the drive, and the overshoot is the motor's own
        # ring-down rather than a detector chaining unrelated events together.
        assert nominal <= row["Duration (s)"] <= nominal + 0.25, row["Order"]


def test_pulses_are_found_where_the_stimulus_actually_fired():
    rows = _quantify(_STIMULUS_DAY)
    for row, onset in zip(rows, _STIMULUS_DAY["onsets"]):
        start = pd.Timestamp(row["Pulse Start ts"])
        assert abs((start - pd.Timestamp(onset)).total_seconds()) < 1.0, row["Order"]
        assert pd.Timestamp(row["Pulse End ts"]) > start


def test_a_real_stimulus_stands_well_clear_of_the_recording_around_it():
    rows = _quantify(_STIMULUS_DAY)
    for row in rows:
        assert row["Signal-to-Noise (x)"] > 25, row["Order"]
        assert row["Peak Force (g)"] > 0.10, row["Order"]
        assert row["Background Check"] != "matches background", row["Order"]


def test_a_day_without_the_stimulus_produces_nothing_pulse_shaped():
    rows = _quantify(_CONTROL_DAY)
    stimulus_snr = min(row["Signal-to-Noise (x)"] for row in _quantify(_STIMULUS_DAY))
    for row, nominal in zip(rows, _NOMINAL_S):
        # The loudest thing in these windows is ambient: it lasts many seconds
        # and never approaches the separation a real stimulus shows.
        assert row["Signal-to-Noise (x)"] < 25, row["Order"]
        assert row["Duration (s)"] > nominal + 0.25, row["Order"]
    assert max(row["Signal-to-Noise (x)"] for row in rows) < stimulus_snr / 2
