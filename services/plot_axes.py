"""Shared matplotlib axis helpers for wall-clock protocol and pulse plots."""

from __future__ import annotations

from datetime import datetime, timedelta

import matplotlib.dates as mdates
from matplotlib.artist import setp as _mpl_setp
from matplotlib.ticker import FuncFormatter

_LABEL_INTERVALS_HOURS = (1, 2, 4, 6, 12, 24)
# Sub-hour plots (single pulse windows) need their own ladder, otherwise an
# hours-only locator leaves them with one tick or none at all.
_LABEL_INTERVALS_MINUTES = (1, 2, 5, 10, 15, 30)
_MAX_MAJOR_LABELS = 16
_MIN_MAJOR_LABELS = 3


def wall_clock_label_interval_hours(duration: timedelta) -> int:
    """Return a major-label interval that keeps long runs readable."""
    hours = max(duration.total_seconds() / 3600.0, 0.0)
    for candidate in _LABEL_INTERVALS_HOURS:
        if hours / candidate <= _MAX_MAJOR_LABELS:
            return candidate
    return 24


def wall_clock_label_interval_minutes(duration: timedelta) -> int:
    """Return a sub-hour major-label interval, in minutes."""
    minutes = max(duration.total_seconds() / 60.0, 0.0)
    for candidate in _LABEL_INTERVALS_MINUTES:
        if minutes / candidate <= _MAX_MAJOR_LABELS:
            return candidate
    return 30


def apply_wall_clock_xaxis(ax) -> None:
    """Label wall-clock time with gradations that suit the plotted duration.

    Plots spanning an hour or more always carry :00/:30 gradations, with major
    labels thinning as the run gets longer. Shorter spans fall back to a
    minute ladder so single pulse windows still get readable ticks.
    """
    x0, x1 = ax.get_xlim()
    start = _to_naive_datetime(mdates.num2date(x0))
    end = _to_naive_datetime(mdates.num2date(x1))
    if end < start:
        start, end = end, start
    duration = end - start
    crosses_midnight = start.date() != end.date()

    if duration >= timedelta(hours=1):
        interval_hours = wall_clock_label_interval_hours(duration)
        ax.xaxis.set_minor_locator(mdates.MinuteLocator(byminute=[0, 30]))
        if interval_hours == 1:
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
        else:
            ax.xaxis.set_major_locator(
                mdates.HourLocator(byhour=list(range(0, 24, interval_hours)))
            )
        show_seconds = False
    else:
        interval_minutes = wall_clock_label_interval_minutes(duration)
        ax.xaxis.set_major_locator(
            mdates.MinuteLocator(byminute=list(range(0, 60, interval_minutes)))
        )
        minor_interval = max(interval_minutes // 5, 1)
        if duration <= timedelta(minutes=_MIN_MAJOR_LABELS * 1):
            ax.xaxis.set_minor_locator(mdates.SecondLocator(bysecond=list(range(0, 60, 15))))
        else:
            ax.xaxis.set_minor_locator(
                mdates.MinuteLocator(byminute=list(range(0, 60, minor_interval)))
            )
        show_seconds = duration < timedelta(minutes=2)

    def _major_fmt(value, _pos):
        dt_val = _to_naive_datetime(mdates.num2date(value))
        pattern = "%I:%M:%S %p" if show_seconds else "%I:%M %p"
        time_part = dt_val.strftime(pattern).lstrip("0")
        if crosses_midnight:
            return f"{dt_val.month}/{dt_val.day} {time_part}"
        return time_part

    ax.xaxis.set_major_formatter(FuncFormatter(_major_fmt))
    _mpl_setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right", rotation_mode="anchor")
    ax.tick_params(axis="x", which="major", length=8, width=1.2)
    ax.tick_params(axis="x", which="minor", length=4, width=0.8)


def _to_naive_datetime(value) -> datetime:
    dt_val = value
    if getattr(dt_val, "tzinfo", None) is not None:
        dt_val = dt_val.replace(tzinfo=None)
    return dt_val
