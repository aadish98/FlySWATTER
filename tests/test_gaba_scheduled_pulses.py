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
from services.pulse_service import _prepare_file_frame, _to_naive_ts

GABA_ROOT = Path(
    "/Volumes/umms-rallada/UM Lab Users/Farheen/Arousal Experiments/"
    "GABA_Arousal_run1/08-10-2026 T-3.54pm"
)


@pytest.mark.skipif(not GABA_ROOT.exists(), reason="GABA arousal run is not mounted")
def test_gaba_day2_scheduled_windows_keep_true_pulses():
    manifest_path = Path(find_manifest_path(str(GABA_ROOT)))
    manifest = load_manifest(str(manifest_path))
    manifest_start = _to_naive_ts(parse_manifest_start_iso(manifest, str(manifest_path)))
    part_files = {
        "021": next(GABA_ROOT.rglob("*part021.csv.gz")),
        "023": next(GABA_ROOT.rglob("*part023.csv.gz")),
        "025": next(GABA_ROOT.rglob("*part025.csv.gz")),
    }
    frames = []
    for path in part_files.values():
        frame = _prepare_file_frame(
            path,
            manifest_start_ts=manifest_start,
            window_start=None,
            window_end=None,
        )
        if frame is not None:
            frames.append(frame)
    combined = pd.concat(frames, ignore_index=True)
    windows = [
        {"start_iso": "2026-08-11T11:58:00", "end_iso": "2026-08-11T12:03:00"},
        {"start_iso": "2026-08-11T13:58:00", "end_iso": "2026-08-11T14:06:00"},
        {"start_iso": "2026-08-11T15:58:00", "end_iso": "2026-08-11T16:04:00"},
    ]
    results = quantify_scheduled_pulses(combined, windows)
    assert [row["PulseIndex"] for row in results] == [1, 2, 3]
    assert all(row["Detection Status"] == "detected" for row in results)
    assert all(row["Baseline-Adjusted Area (g·s)"] > 0.001 for row in results)
    noon, two_pm, four_pm = results
    noon_span = (pd.Timestamp(noon["pulse_start"]), pd.Timestamp(noon["pulse_end"]))
    two_span = (pd.Timestamp(two_pm["pulse_start"]), pd.Timestamp(two_pm["pulse_end"]))
    four_span = (pd.Timestamp(four_pm["pulse_start"]), pd.Timestamp(four_pm["pulse_end"]))
    assert noon_span[0] <= pd.Timestamp("2026-08-11T12:00:47.500") <= noon_span[1]
    assert two_span[0] <= pd.Timestamp("2026-08-11T14:03:26") <= two_span[1]
    assert four_span[0] <= pd.Timestamp("2026-08-11T16:00:47.800") <= four_span[1]
    assert four_pm["Peak Force (g)"] >= 0.04
    assert four_pm["Duration (s)"] < 30
    assert two_pm["Duration (s)"] < 240

    # The 2 PM stimulus is a sustained ramp that ends abruptly, so it stands
    # clear of the surrounding recording. Noon and 4 PM sit inside a periodic
    # background train, which the background check has to surface rather than
    # report as a confident detection.
    assert two_pm["Background Check"] == "distinct"
    assert noon["Background Check"] in {"marginal", "matches background"}
    assert four_pm["Background Check"] == "matches background"
    assert four_pm["Background Peak Max (g)"] > four_pm["Peak Force (g)"]
