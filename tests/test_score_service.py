from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pytest

from services.plot_axes import wall_clock_label_interval_hours
from services.score_service import _apply_wall_clock_xaxis


def _naive_tick_datetimes(ax, which: str):
    locator = ax.xaxis.get_minor_locator() if which == "minor" else ax.xaxis.get_major_locator()
    values = locator()
    ticks = []
    for value in values:
        dt_val = mdates.num2date(value)
        if getattr(dt_val, "tzinfo", None) is not None:
            dt_val = dt_val.replace(tzinfo=None)
        ticks.append(dt_val)
    return ticks


@pytest.mark.parametrize(
    ("duration", "expected_interval"),
    [
        (timedelta(hours=2), 1),
        (timedelta(days=1), 2),
        (timedelta(days=7), 12),
    ],
)
def test_wall_clock_ticks_use_half_hour_gradations(duration, expected_interval):
    fig, ax = plt.subplots(figsize=(10, 6))
    start = datetime(2026, 8, 3, 9)
    end = start + duration
    ax.set_xlim(start, end)

    _apply_wall_clock_xaxis(ax)
    fig.canvas.draw()

    minor_ticks = [dt for dt in _naive_tick_datetimes(ax, "minor") if start <= dt <= end]
    major_ticks = [dt for dt in _naive_tick_datetimes(ax, "major") if start <= dt <= end]
    labels = [label.get_text() for label in ax.get_xticklabels() if label.get_text()]

    assert minor_ticks
    assert all(dt.minute in (0, 30) and dt.second == 0 and dt.microsecond == 0 for dt in minor_ticks)
    assert any(dt.minute == 30 for dt in minor_ticks)
    assert all(dt.minute == 0 for dt in major_ticks)
    assert wall_clock_label_interval_hours(duration) == expected_interval
    assert 2 <= len(labels) <= 16
    assert all(":" in label and label.endswith(("AM", "PM")) for label in labels)
    if duration >= timedelta(days=1):
        assert any("/" in label for label in labels)
    else:
        assert all("/" not in label for label in labels)
    plt.close(fig)
