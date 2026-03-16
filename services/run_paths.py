"""Helpers for constructing per-run output directories."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def build_run_output_dir(data_root: Path, researcher_name: str) -> Path:
    safe_name = researcher_name.strip().replace("/", "_").replace("\\", "_")
    timestamp = datetime.now().strftime("%m-%d-%y T-%I:%M%p")
    output_dir = data_root / safe_name / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir
