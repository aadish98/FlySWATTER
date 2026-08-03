from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from services.score_service import _apply_wall_clock_xaxis


@pytest.mark.parametrize("duration", [timedelta(hours=2), timedelta(days=1), timedelta(days=7)])
def test_wall_clock_ticks_adapt_to_plot_duration(duration):
    fig, ax = plt.subplots(figsize=(10, 6))
    start = datetime(2026, 8, 3, 9)
    ax.set_xlim(start, start + duration)

    _apply_wall_clock_xaxis(ax)
    fig.canvas.draw()

    labels = [label.get_text() for label in ax.get_xticklabels() if label.get_text()]
    assert 4 <= len(labels) <= 10
    assert all(":" in label and label.endswith(("AM", "PM")) for label in labels)
    plt.close(fig)
