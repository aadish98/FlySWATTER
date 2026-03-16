"""Data models shared across services and GUI screens."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ValidationResult:
    valid: bool
    message: str = ""
    details: List[str] = field(default_factory=list)


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
