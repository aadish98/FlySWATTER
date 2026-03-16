#!/usr/bin/env python3
import argparse
import os
import re
import pandas as pd
from typing import List, Tuple, Optional
from datetime import datetime, timedelta
from pathlib import Path

def load_mapping(path):
    """Load the genotype→wells mapping.
    Assumes first column is 'Genotype' and all other columns list wells (one per cell).
    Returns (mapping, genotype_order) where genotype_order is sorted by 'Order' column if present."""
    df = pd.read_excel(path)
    if 'Genotype' not in df.columns:
        raise ValueError("Mapping file must have a 'Genotype' column")
    
    # Sort by Order column if present
    if 'Order' in df.columns:
        df = df.sort_values('Order').reset_index(drop=True)
    
    mapping = {}
    genotype_order = []
    for _, row in df.iterrows():
        geno = row['Genotype']
        # Exclude 'Genotype' and 'Order' columns when getting wells
        exclude_cols = ['Genotype', 'Order']
        wells = [w for w in row.drop(exclude_cols, errors='ignore').dropna().tolist()]
        mapping[geno] = wells
        genotype_order.append(geno)
    
    return mapping, genotype_order

def sanitize_excel_sheet_name(name: str, used_names: Optional[set] = None) -> str:
    """Return an Excel-compatible sheet name (<=31 chars, unique when requested)."""
    invalid_chars = [":", "\\", "/", "?", "*", "[", "]"]
    clean = str(name)
    for ch in invalid_chars:
        clean = clean.replace(ch, "_")
    clean = clean.strip()
    if not clean:
        clean = "Sheet"

    max_len = 31
    if len(clean) > max_len:
        clean = clean[:max_len]

    if used_names is None:
        return clean

    candidate = clean
    suffix = 1
    while candidate in used_names:
        suffix_txt = f"_{suffix}"
        base_len = max_len - len(suffix_txt)
        candidate = f"{clean[:base_len]}{suffix_txt}"
        suffix += 1
    used_names.add(candidate)
    return candidate

def _ensure_numeric(df, cols: List[str]):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

def _read_zantiks_file(path: str) -> pd.DataFrame:
    """Robustly read Zantiks CSV/XLSX where the first few lines are metadata.
    Detect the header row by looking for canonical column names like 'RUNTIME' and 'TIME_BIN'.
    """
    expected_markers = {"RUNTIME", "VIB", "DAY", "HOUR", "TIME_BIN"}

    if path.lower().endswith(".csv"):
        # Find header line index by scanning file text for the first line that contains all markers
        header_idx = None
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    # quick check: all markers appear in this line
                    if all(m in line for m in expected_markers):
                        header_idx = i
                        break
        except OSError as e:
            raise e
        if header_idx is None:
            # Fallback to 3 (historical default) if nothing detected
            header_idx = 3
        df = pd.read_csv(path, header=header_idx, low_memory=False)
        return df

    # Excel: read a small portion to find header row
    xdf = pd.read_excel(path, header=None)
    header_idx = None
    for i in range(min(10, len(xdf))):
        vals = set(str(v) for v in xdf.iloc[i].tolist())
        if expected_markers.issubset(vals):
            header_idx = i
            break
    if header_idx is None:
        header_idx = 3
    return pd.read_excel(path, header=header_idx)


def _parse_start_datetime_from_filename(path: str) -> Optional[datetime]:
    """Extract start datetime token (YYYYMMDDTHHMMSS) from input filename.

    Example:
    020226_arousal_experiment_script_2Day_protocol-20260206T184805.csv
    -> 2026-02-06 18:48:05
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    matches = re.findall(r"(\d{8}T\d{6})", stem)
    if not matches:
        return None
    token = matches[-1]  # Use the last token if multiple are present
    try:
        return datetime.strptime(token, "%Y%m%dT%H%M%S")
    except ValueError:
        return None

def detect_pulses_by_row(df: pd.DataFrame) -> List[Tuple[int, int, int, int, float, float]]:
    """
    Return list of (start_row, end_row, day, hour, int_temp1, start_runtime) for pulses defined as:
    1 or more contiguous ROWS where VIB > 0 within the same DAY and HOUR.
    Row contiguity follows the original file order (no sorting).
    """
    required_cols = ["VIB", "DAY", "HOUR"]
    for c in required_cols:
        if c not in df.columns:
            raise KeyError(f"Required column '{c}' not found")

    int_temp1_col = "INT_TEMP1" if "INT_TEMP1" in df.columns else None
    has_runtime = "RUNTIME" in df.columns

    local = df.copy()
    local["VIB"] = pd.to_numeric(local["VIB"], errors="coerce").fillna(0)
    local["DAY"] = pd.to_numeric(local["DAY"], errors="coerce")
    local["HOUR"] = pd.to_numeric(local["HOUR"], errors="coerce")
    if int_temp1_col:
        local[int_temp1_col] = pd.to_numeric(local[int_temp1_col], errors="coerce")
    if has_runtime:
        local["RUNTIME"] = pd.to_numeric(local["RUNTIME"], errors="coerce")

    # Keep only rows with valid timing
    local = local.dropna(subset=["DAY", "HOUR"]).copy()

    pulses: List[Tuple[int, int, int, int, float, float]] = []

    in_pulse = False
    pulse_start_row = None
    pulse_day = None
    pulse_hour = None
    pulse_int_temp1 = None
    pulse_start_runtime = None
    prev_row_idx = None

    # Iterate in original order (no sorting) to respect row contiguity
    for row_idx, row in enumerate(local.itertuples(index=False), start=0):
        cur_day = int(row.DAY) if pd.notna(row.DAY) else None
        cur_hour = int(row.HOUR) if pd.notna(row.HOUR) else None
        cur_int_temp1 = float(getattr(row, int_temp1_col)) if int_temp1_col and pd.notna(getattr(row, int_temp1_col)) else float('nan')
        cur_runtime = float(row.RUNTIME) if has_runtime and pd.notna(row.RUNTIME) else float('nan')
        vib_pos = row.VIB > 0

        if not in_pulse:
            if vib_pos:
                in_pulse = True
                pulse_start_row = row_idx
                pulse_day = cur_day
                pulse_hour = cur_hour
                pulse_int_temp1 = cur_int_temp1
                pulse_start_runtime = cur_runtime
        else:
            if (not vib_pos) or (cur_day != pulse_day) or (cur_hour != pulse_hour):
                pulses.append((pulse_start_row, prev_row_idx, pulse_day, pulse_hour, pulse_int_temp1, pulse_start_runtime))
                in_pulse = False
                if vib_pos:
                    in_pulse = True
                    pulse_start_row = row_idx
                    pulse_day = cur_day
                    pulse_hour = cur_hour
                    pulse_int_temp1 = cur_int_temp1
                    pulse_start_runtime = cur_runtime

        prev_row_idx = row_idx

    if in_pulse and pulse_start_row is not None:
        pulses.append((pulse_start_row, prev_row_idx, pulse_day, pulse_hour, pulse_int_temp1, pulse_start_runtime))

    return pulses

# Removed coverage/clamping helpers to simplify logic under 1s-per-row assumption


def _clamp_row_window(start_row: int, end_row: int, total_rows: int) -> Optional[Tuple[int, int]]:
    """Clamp an inclusive row window to the available [0, total_rows-1] range."""
    if total_rows <= 0:
        return None
    s = 0 if start_row < 0 else start_row
    e = total_rows - 1 if end_row > total_rows - 1 else end_row
    if e < s:
        return None
    return s, e

# Removed nominal step and coverage checks

# Replace the body of score_genotype_timebins with this simpler logic
def score_genotype_timerows(
    df: pd.DataFrame,
    wells: List[str],
    pulse_start_row: int,
    pulse_end_row: int,
    pre_sec: int,
    post_sec: int,
) -> Optional[Tuple[int, List[str], int, List[str], float, Tuple[int, int], Tuple[int, int]]]:
    """
    Row-based scoring under 1s-per-row assumption with clamping:
    - Pre window: [start_row - pre_sec, start_row - 1]
    - Arousal window: [end_row + 1, end_row + post_sec]
      (first second after the vibration ends, for post_sec seconds)
    - Clamp both windows to [0, len(df)-1]
    - Asleep: at least one pre sample and all observed pre samples == 0.
    - Awoken: among asleep, any observed arousal sample > 0.
    Returns windows actually used for reporting.
    """
    total_rows = len(df)
    pre_start = pulse_start_row - pre_sec
    pre_end = pulse_start_row - 1
    arousal_start = pulse_end_row + 1
    arousal_end = pulse_end_row + post_sec

    pre_window = _clamp_row_window(pre_start, pre_end, total_rows)
    arousal_window = _clamp_row_window(arousal_start, arousal_end, total_rows)
    if pre_window is None or arousal_window is None:
        return None

    ps, pe = pre_window
    as_, ae = arousal_window
    pre_win = df.iloc[ps:pe+1]
    arousal_win = df.iloc[as_:ae+1]

    if pre_win.empty or arousal_win.empty:
        return None

    asleep: List[str] = []
    for w in wells:
        if w not in df.columns:
            continue
        s = pre_win[w].dropna()
        if len(s) == 0:
            continue
        if (s == 0).all():
            asleep.append(w)

    awoken: List[str] = []
    for w in asleep:
        s2 = arousal_win[w].dropna()
        if len(s2) == 0:
            continue
        if (s2 > 0).any():
            awoken.append(w)

    n_asleep = len(asleep)
    n_awoken = len(awoken)
    pct = (n_awoken / n_asleep * 100) if n_asleep else float("nan")
    return n_asleep, asleep, n_awoken, awoken, pct, pre_window, arousal_window

def main():
    from services.score_service import run_score_analysis_from_mapping_file

    p = argparse.ArgumentParser(
        description="Score arousability from Zantiks output (TIME_BIN-aware)."
    )
    p.add_argument("input_file", help="Zantiks .xlsx file to score")
    p.add_argument(
        "--mapping",
        default="arousal_score_well_mapping.xlsx",
        help="Genotype→well mapping Excel file",
    )
    p.add_argument(
        "--pre-sec",
        type=int,
        default=300,
        help="Seconds to observe BEFORE pulse start (default: 300)",
    )
    p.add_argument(
        "--post-sec",
        type=int,
        default=120,
        help="Seconds for AROUSAL window AFTER pulse ends (default: 120)",
    )
    p.add_argument(
        "--max-pulses",
        type=int,
        default=0,
        help="If >0, only score the first N pulses",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Handle dry runs by adding dummy data when no real data is available",
    )
    args = p.parse_args()

    output_dir = Path(args.input_file).resolve().parent / 'results'
    result = run_score_analysis_from_mapping_file(
        args.input_file,
        args.mapping,
        output_dir,
        pre_sec=args.pre_sec,
        post_sec=args.post_sec,
        max_pulses=args.max_pulses,
        dry_run=args.dry_run,
    )
    print(f"Arousal workbook: {result.arousal_workbook}")
    if result.protocol_plot is not None:
        print(f"Protocol plot: {result.protocol_plot}")
    if result.sleep_profile_plot is not None:
        print(f"Sleep profile plot: {result.sleep_profile_plot}")
    print(f"Arousal zip: {result.arousal_zip}")
    print(f"Sleep zip: {result.sleep_zip}")

if __name__ == "__main__":
    main()
