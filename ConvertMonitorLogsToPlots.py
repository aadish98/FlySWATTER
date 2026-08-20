#!/usr/bin/env python3
"""Parse and plot temperature data from TriKinetics DAM/DEnM Monitor*.txt logs.

TriKinetics DAMSystem3 writes tab-delimited ``MonitorNN.txt`` files with a
fixed 42-column layout (see the DAMSystem3 Software Data Sheet). Each row's
first 10 columns are metadata (index, date, time, status, monitor number,
data type, etc.); columns 11-42 are a 32-entry "channel" block.

For monitors running the Drosophila Environment Monitor (DEnM) data type,
that 32-entry block reports illumination/temperature/humidity statistics
instead of per-tube activity counts:

    channel  1  -> always 0
    channel  2-5  -> Lnow, Lmin, Lavg, Lmax   (illumination, lux)
    channel  6  -> always 0
    channel  7-10 -> Tnow, Tmin, Tavg, Tmax   (temperature, degC x 10)
    channel 11 -> always 0
    channel 12-15 -> Hnow, Hmin, Havg, Hmax   (relative humidity, %)
    channel 16 -> always 0
    channel 17-32 -> 2-minute illumination sub-bins

Channel N lives at file column ``10 + N``, so Tavg is file column 19.

This module only needs the temperature channel, but keeps humidity and
illumination around since they're free to read. By default, light/dark
shading on the plot follows a fixed daily schedule (8 AM-8 PM lights on)
rather than the raw illumination sensor, since transient light events (door
openings, sensor noise) make the sensor-derived shading noisy; pass
``use_illumination_sensor=True`` to opt back into sensor-derived shading.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pandas as pd

from services.temperature_plot import render_temperature_protocol_plot, segments_from_states

# 1-indexed column positions within a Monitor*.txt row.
COL_DATE = 2
COL_TIME = 3
COL_MONITOR_NUM = 6
COL_DATA_TYPE = 8
# Channel offsets within the 32-channel block (channel 1 == file column 11).
COL_ILLUM_AVG = 14  # Channel 4  - Lavg, average illumination over the bin (lux)
COL_TEMP_NOW = 17  # Channel 7  - Tnow, current temperature (degC x 10)
COL_TEMP_AVG = 19  # Channel 9  - Tavg, average temperature over the bin (degC x 10)
COL_HUMIDITY_AVG = 24  # Channel 14 - Havg, average relative humidity over the bin (%)

MIN_EXPECTED_COLUMNS = COL_TEMP_AVG
DEFAULT_LIGHT_THRESHOLD_LUX = 10.0


def read_monitor_temperature_file(path: str | Path) -> pd.DataFrame:
    """Read a TriKinetics DAM/DEnM ``Monitor*.txt`` file and extract temperature.

    Returns a DataFrame sorted by time with columns:
      - ``datetime``: wall-clock timestamp of the reading
      - ``temperature_c``: average temperature over the bin (falls back to the
        instantaneous reading when the average is missing)
      - ``humidity_pct``: average relative humidity, when present
      - ``illumination_lux``: average illumination, when present

    Raises ``ValueError`` if the file doesn't look like a Monitor.txt export
    or contains no usable temperature readings.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Monitor file not found: {file_path}")

    raw = pd.read_csv(file_path, sep="\t", header=None, engine="python", dtype=str)
    if raw.shape[1] < MIN_EXPECTED_COLUMNS:
        raise ValueError(
            f"'{file_path.name}' does not look like a TriKinetics Monitor.txt file "
            f"(expected at least {MIN_EXPECTED_COLUMNS} tab-delimited columns, found {raw.shape[1]})."
        )

    def col(idx_1based: int) -> pd.Series:
        return raw.iloc[:, idx_1based - 1]

    timestamps = pd.to_datetime(
        col(COL_DATE).astype(str).str.strip() + " " + col(COL_TIME).astype(str).str.strip(),
        format="%d %b %y %H:%M:%S",
        errors="coerce",
    )

    temp_avg = pd.to_numeric(col(COL_TEMP_AVG), errors="coerce")
    temp_now = pd.to_numeric(col(COL_TEMP_NOW), errors="coerce")
    temperature_c = temp_avg.where(temp_avg.notna(), temp_now) / 10.0

    out = pd.DataFrame({"datetime": timestamps, "temperature_c": temperature_c})

    if raw.shape[1] >= COL_HUMIDITY_AVG:
        out["humidity_pct"] = pd.to_numeric(col(COL_HUMIDITY_AVG), errors="coerce")
    if raw.shape[1] >= COL_ILLUM_AVG:
        out["illumination_lux"] = pd.to_numeric(col(COL_ILLUM_AVG), errors="coerce")

    out = out.dropna(subset=["datetime", "temperature_c"]).sort_values("datetime").reset_index(drop=True)
    if out.empty:
        raise ValueError(
            f"No usable temperature readings were found in '{file_path.name}'. "
            "Confirm this is a Drosophila Environment Monitor (DEnM) export."
        )
    return out


def get_monitor_temperature_at(
    monitor_df: pd.DataFrame,
    when: datetime,
    max_gap_seconds: float = 3600.0,
) -> Optional[float]:
    """Linearly interpolate the monitor temperature at a given wall-clock time.

    Returns ``None`` when ``when`` falls more than ``max_gap_seconds`` outside
    the monitor log's covered time range, rather than silently extrapolating.
    """
    if monitor_df is None or monitor_df.empty:
        return None
    ts = monitor_df["datetime"].astype("datetime64[s]").astype(np.int64).to_numpy(dtype=float)
    when_ts = float(np.datetime64(when, "s").astype(np.int64))
    if when_ts < ts[0] - max_gap_seconds or when_ts > ts[-1] + max_gap_seconds:
        return None
    temps = monitor_df["temperature_c"].to_numpy(dtype=float)
    return float(np.interp(when_ts, ts, temps))


DEFAULT_LIGHT_START_HOUR = 8
DEFAULT_LIGHT_END_HOUR = 20


def _is_light_from_lux(value: Optional[float], threshold: float) -> Optional[bool]:
    if value is None or pd.isna(value):
        return None
    return float(value) >= threshold


def _is_light_from_schedule(when: datetime, light_start_hour: int, light_end_hour: int) -> bool:
    return light_start_hour <= when.hour < light_end_hour


def plot_monitor_temperature(
    monitor_df: pd.DataFrame,
    output_path: str | Path,
    *,
    title: str = "Temperature and Light/Dark Protocol Over Time (Monitor Log)",
    light_start_hour: int = DEFAULT_LIGHT_START_HOUR,
    light_end_hour: int = DEFAULT_LIGHT_END_HOUR,
    use_illumination_sensor: bool = False,
    light_threshold_lux: float = DEFAULT_LIGHT_THRESHOLD_LUX,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Path:
    """Render the monitor log's temperature trace in the "protocol plot" style.

    Day/night shading defaults to a fixed daily schedule (lights on from
    ``light_start_hour`` to ``light_end_hour``, 8 AM-8 PM by default) since
    that's the standard Zantiks light cycle and it avoids the noisy,
    door-opening/transient-driven shading the raw illumination sensor
    otherwise produces. Pass ``use_illumination_sensor=True`` to instead
    derive light/dark from the monitor's own illumination channel (lux >=
    ``light_threshold_lux`` counts as "light").
    """
    df = monitor_df
    if start is not None:
        df = df[df["datetime"] >= start]
    if end is not None:
        df = df[df["datetime"] <= end]
    df = df.sort_values("datetime").reset_index(drop=True)
    if df.empty:
        raise ValueError("No monitor temperature readings fall within the requested time window.")

    times = df["datetime"].tolist()
    temperatures = df["temperature_c"].tolist()

    if use_illumination_sensor and "illumination_lux" in df.columns:
        states = [_is_light_from_lux(value, light_threshold_lux) for value in df["illumination_lux"].tolist()]
    else:
        states = [_is_light_from_schedule(t, light_start_hour, light_end_hour) for t in times]
    segments = segments_from_states(times, states)

    fig = render_temperature_protocol_plot(
        times,
        temperatures,
        light_dark_segments=segments,
        title=title,
        temperature_label="Monitor Temp (\u00b0C)",
    )

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_file, dpi=300, bbox_inches="tight")
    return output_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a temperature/light-dark protocol plot from a TriKinetics Monitor*.txt log."
    )
    parser.add_argument("monitor_file", help="Path to a MonitorNN.txt file (DEnM environment monitor export)")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output PNG path (defaults to Monitor_Temp_<name>.png next to the input file)",
    )
    parser.add_argument(
        "--light-start-hour",
        type=int,
        default=DEFAULT_LIGHT_START_HOUR,
        help="Hour (0-23) lights turn on each day (default: 8, i.e. 8 AM)",
    )
    parser.add_argument(
        "--light-end-hour",
        type=int,
        default=DEFAULT_LIGHT_END_HOUR,
        help="Hour (0-23) lights turn off each day (default: 20, i.e. 8 PM)",
    )
    parser.add_argument(
        "--use-illumination-sensor",
        action="store_true",
        help="Derive light/dark shading from the monitor's illumination channel instead of the fixed schedule",
    )
    parser.add_argument(
        "--light-threshold-lux",
        type=float,
        default=DEFAULT_LIGHT_THRESHOLD_LUX,
        help="With --use-illumination-sensor, lux threshold above which a reading counts as 'light' (default: 10.0)",
    )
    parser.add_argument("--title", default=None, help="Custom plot title")
    parser.add_argument(
        "--last-hours",
        type=float,
        default=None,
        help="Only plot the most recent N hours of the log (default: the entire log)",
    )
    args = parser.parse_args()

    input_path = Path(args.monitor_file)
    output_path = Path(args.output) if args.output else input_path.with_name(f"Monitor_Temp_{input_path.stem}.png")

    monitor_df = read_monitor_temperature_file(input_path)
    kwargs = {
        "light_start_hour": args.light_start_hour,
        "light_end_hour": args.light_end_hour,
        "use_illumination_sensor": args.use_illumination_sensor,
        "light_threshold_lux": args.light_threshold_lux,
    }
    if args.title:
        kwargs["title"] = args.title
    if args.last_hours:
        kwargs["start"] = monitor_df["datetime"].max() - timedelta(hours=args.last_hours)
    result_path = plot_monitor_temperature(monitor_df, output_path, **kwargs)
    print(f"Monitor temperature plot: {result_path}")


if __name__ == "__main__":
    main()
