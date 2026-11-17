"""Data models shared across services and GUI screens."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ValidationResult:
    valid: bool
    message: str = ""
    details: List[str] = field(default_factory=list)
    help_document: Optional[str] = None


@dataclass
class ScoreAnalysisResult:
    output_dir: Path
    arousal_workbook: Path
    arousal_plot_paths: List[Path]
    protocol_plot: Optional[Path]
    sleep_totals_workbook: Optional[Path]
    sleep_profile_plot: Optional[Path]
    sleep_pct_plot: Optional[Path]
    sleep_individual_dir: Optional[Path]
    arousal_zip: Path
    sleep_zip: Path
    preview_plot_paths: List[Path]
    genotype_counts: Dict[str, int]
    temperature_source: str = "Zantiks (INT_TEMP1)"


@dataclass
class PulseWindow:
    start_iso: str
    end_iso: str


@dataclass
class PulseAnalysisResult:
    output_dir: Path
    aggregated_plot: Path
    aggregated_workbook: Path
    zip_path: Path
    processed_files: List[Path]
    analyzed_window_label: str
    total_pulses: int


@dataclass
class FolderWindowSummary:
    display_name: str
    manifest_path: Path
    start_ts_iso: str
    end_ts_iso: str
    csv_files: List[Path]


@dataclass
class AnalysisWindow:
    """One user-defined stretch of the recording to analyze, in corrected clock time."""

    start_iso: str
    end_iso: str
    label: str = ""


@dataclass
class GeneralWindowResult:
    """Everything needed to summarize and redraw one analysis window.

    Timestamps here are on the manifest's own clock, so a later clock
    correction only has to shift them.
    """

    label: str
    start_iso: str
    end_iso: str
    pulse_rows: List[Dict[str, Any]]
    plot_frame: Any
    peak_points: Any
    file_count: int = 0


@dataclass
class GeneralPulseAnalysisResult:
    output_dir: Path
    aggregated_plot: Path
    distributions_plot: Path
    workbook: Path
    zip_path: Path
    processed_files: List[Path]
    analyzed_window_label: str
    total_pulses: int
    # Block label (each window, then "Combined") -> metric -> statistic -> value.
    summaries: Dict[str, Dict[str, Dict[str, float]]]
    windows: List[GeneralWindowResult]
    clock_offset_seconds: float
    manifest_start_iso: str
    folder_name: str
    speed_setting: str = "unknown"
