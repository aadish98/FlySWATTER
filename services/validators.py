"""Validation helpers for GUI input selection."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List

from services.models import ValidationResult

ZANTIKS_SCORE_PATTERN = re.compile(
    r"^\d{6}_arousal_experiment_script_2Day_protocol-\d{8}T\d{6}\.(csv|xlsx)$",
    re.IGNORECASE,
)
ACCEL_FOLDER_PATTERN = re.compile(r"^\d{2}-\d{2}-\d{4} T-\d{1,2}\.\d{2}(am|pm)$", re.IGNORECASE)
ACCEL_FILE_PATTERN = re.compile(
    r"^Zantiks_.+_\d{12}_part\d{3}\.csv(\.gz)?$",
    re.IGNORECASE,
)


def validate_researcher_name(name: str) -> ValidationResult:
    clean_name = name.strip()
    if not clean_name:
        return ValidationResult(False, "Researcher name is required.")
    invalid_chars = set('/\\:*?"<>|')
    if any(ch in invalid_chars for ch in clean_name):
        return ValidationResult(False, "Researcher name contains unsupported characters.")
    return ValidationResult(True)


def validate_score_file(path: str | Path) -> ValidationResult:
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return ValidationResult(False, "Select a valid Zantiks behavior data file.")
    if not ZANTIKS_SCORE_PATTERN.match(file_path.name):
        return ValidationResult(
            False,
            "The Zantiks filename appears to have been modified.",
            [
                "Please re-download the Zantiks file from the system.",
                "Upload it again without changing the filename.",
            ],
        )
    return ValidationResult(True)


def validate_manifest(path: Path) -> ValidationResult:
    if not path.exists():
        return ValidationResult(False, "Could not find manifest.json in the selected log folder.")
    return ValidationResult(True)


def validate_accel_folder(path: str | Path, csv_files: Iterable[Path] | None = None) -> ValidationResult:
    folder_path = Path(path)
    if not folder_path.exists() or not folder_path.is_dir():
        return ValidationResult(False, "Select a valid accelerometer log folder.")
    if not ACCEL_FOLDER_PATTERN.match(folder_path.name):
        return ValidationResult(
            False,
            "The folder name for the accelerometer logs appears to have been modified.",
            [
                "Please re-download the log files without changing the folder name.",
                "Upload the original folder again without modifying names.",
            ],
        )
    manifest_result = validate_manifest(folder_path / "manifest.json")
    if not manifest_result.valid:
        return manifest_result

    discovered_files: List[Path]
    if csv_files is None:
        discovered_files = sorted(
            [
                child
                for child in folder_path.rglob("*")
                if child.is_file() and (child.name.lower().endswith(".csv") or child.name.lower().endswith(".csv.gz"))
            ]
        )
    else:
        discovered_files = list(csv_files)

    if not discovered_files:
        return ValidationResult(False, "No accelerometer log files were found in the selected folder.")

    invalid_names = [file_path.name for file_path in discovered_files if not ACCEL_FILE_PATTERN.match(file_path.name)]
    if invalid_names:
        details = [
            "Please re-download the log files without changing folder or file names.",
            f"Unexpected file: {invalid_names[0]}",
        ]
        return ValidationResult(
            False,
            "One or more accelerometer log filenames appear to have been modified.",
            details,
        )

    return ValidationResult(True)
