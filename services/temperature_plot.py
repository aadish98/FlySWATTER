"""Shared renderer for the "Temperature and Light/Dark Protocol" plot style.

Both the Zantiks-driven protocol plot (``services/score_service.py``) and the
standalone monitor-log temperature plot (``ConvertMonitorLogsToPlots.py``)
render the exact same chart: a red temperature trace over wall-clock time,
shaded day/night bands, dashed midnight gridlines, and (optionally) annotated
pulse markers. This module holds that rendering logic in one place so both
callers stay visually identical.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Sequence, Tuple

import matplotlib.dates as mdates
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from matplotlib.ticker import MultipleLocator

from services.plot_axes import apply_wall_clock_xaxis

LightSegment = Tuple[datetime, datetime, Optional[bool]]
PulseAnnotation = Tuple[datetime, str]

# Labeled x-axis gradations, in hours-of-day. The finer ladder adds 2 AM/2 PM
# and 4 AM/4 PM marks on top of midnight/8 AM/noon/8 PM; it's only used for
# shorter spans so long multi-day runs don't end up with overlapping labels.
_FINE_GRADATION_HOURS = (0, 2, 4, 8, 12, 14, 16, 20)
_COARSE_GRADATION_HOURS = (0, 8, 12, 20)
_FINE_GRADATION_MAX_DAYS = 4.0


def segments_from_states(times: Sequence[datetime], states: Sequence[Optional[bool]]) -> List[LightSegment]:
    """Collapse a per-sample light/dark state series into contiguous segments."""
    segments: List[LightSegment] = []
    if not times:
        return segments
    seg_start = times[0]
    seg_state = states[0]
    for idx in range(1, len(times)):
        if states[idx] != seg_state:
            if seg_state is not None:
                segments.append((seg_start, times[idx], seg_state))
            seg_start = times[idx]
            seg_state = states[idx]
    if seg_state is not None:
        segments.append((seg_start, times[-1], seg_state))
    return segments


def render_temperature_protocol_plot(
    times: Sequence[datetime],
    temperatures: Sequence[float],
    *,
    xlim: Optional[Tuple[datetime, datetime]] = None,
    light_dark_segments: Iterable[LightSegment] = (),
    pulse_annotations: Iterable[PulseAnnotation] = (),
    title: str = "Temperature and Light/Dark Protocol Over Time",
    temperature_label: str = "Internal Temp (\u00b0C)",
) -> Figure:
    """Build the protocol-style figure. Caller is responsible for saving it."""
    fig = Figure(figsize=(14, 8))
    FigureCanvasAgg(fig)
    ax1 = fig.subplots()

    has_temp = any(value is not None and value == value for value in temperatures)
    if has_temp:
        ax1.plot(times, temperatures, color="tab:red", linewidth=1.8, label=temperature_label)
        ax1.yaxis.set_major_locator(MultipleLocator(1))
    ax1.set_ylabel("Temperature (\u00b0C)")

    resolved_xlim = xlim
    if resolved_xlim is None and len(times) > 1:
        resolved_xlim = (times[0], times[-1])

    if resolved_xlim is not None:
        x0, x1 = resolved_xlim
        ax1.set_xlim(x0, x1)
        first_day = datetime.combine(x0.date(), datetime.min.time())
        last_day = datetime.combine(x1.date(), datetime.min.time())
        day_cursor = first_day
        while day_cursor <= last_day:
            if x0 <= day_cursor <= x1:
                ax1.axvline(day_cursor, color="black", linestyle="--", linewidth=1.0, alpha=0.35, zorder=1)
            day_cursor += timedelta(days=1)

    for seg_start, seg_end, is_light in light_dark_segments:
        if is_light is None:
            continue
        ax1.axvspan(seg_start, seg_end, facecolor="#ffe066" if is_light else "#001f3f", alpha=0.18, zorder=0)

    pulse_annotations = list(pulse_annotations)
    if pulse_annotations:
        x0_num, x1_num = ax1.get_xlim()
        for idx, (pulse_x, label) in enumerate(pulse_annotations):
            px_num = mdates.date2num(pulse_x)
            frac = (px_num - x0_num) / (x1_num - x0_num) if (x1_num - x0_num) else 0.5
            dx = 12 if frac < 0.08 else (-12 if frac > 0.92 else 0)
            dy = [-50, -70, -90][idx % 3]
            ax1.annotate(
                label,
                xy=(pulse_x, -0.02),
                xycoords=("data", "axes fraction"),
                xytext=(dx, dy),
                textcoords="offset points",
                ha="center",
                va="top",
                fontsize=8,
                clip_on=False,
                arrowprops=dict(arrowstyle="-|>", lw=0.8, color="black"),
            )

    apply_wall_clock_xaxis(ax1)
    duration_days = None
    if resolved_xlim is not None:
        duration_days = (resolved_xlim[1] - resolved_xlim[0]).total_seconds() / 86400.0
    gradation_hours = (
        _FINE_GRADATION_HOURS
        if duration_days is None or duration_days <= _FINE_GRADATION_MAX_DAYS
        else _COARSE_GRADATION_HOURS
    )
    ax1.xaxis.set_major_locator(mdates.HourLocator(byhour=list(gradation_hours)))
    ax1.legend(
        handles=[
            Patch(facecolor="#ffe066", edgecolor="none", alpha=0.35, label="Day (lights on)"),
            Patch(facecolor="#001f3f", edgecolor="none", alpha=0.35, label="Night (lights off)"),
        ],
        loc="upper right",
        bbox_to_anchor=(1.0, -0.28),
        borderaxespad=0.0,
        fontsize=8,
        title="Light Cycle",
        title_fontsize=8,
        framealpha=0.9,
    )
    ax1.set_xlabel("Wall Clock Time")
    ax1.set_title(title)
    ax1.grid(True, axis="x", which="major", alpha=0.4)
    ax1.grid(True, axis="x", which="minor", alpha=0.2)
    fig.tight_layout(rect=[0, 0.32, 1, 0.96])
    return fig
