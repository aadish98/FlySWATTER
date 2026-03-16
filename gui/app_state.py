"""Application state shared across GUI screens."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from services.models import PulseAnalysisResult, ScoreAnalysisResult


@dataclass
class AppState:
    project_root: Path
    resource_root: Path
    data_root: Path
    researcher_name: str = ""
    selected_score_file: Optional[Path] = None
    selected_pulse_folder: Optional[Path] = None
    genotype_order: List[str] = field(default_factory=list)
    genotype_mapping: Dict[str, List[str]] = field(default_factory=dict)
    score_sleep_minutes: int = 5
    score_result: Optional[ScoreAnalysisResult] = None
    pulse_result: Optional[PulseAnalysisResult] = None

    def reset_score_flow(self) -> None:
        self.selected_score_file = None
        self.genotype_order = []
        self.genotype_mapping = {}
        self.score_sleep_minutes = 5
        self.score_result = None

    def reset_pulse_flow(self) -> None:
        self.selected_pulse_folder = None
        self.pulse_result = None
