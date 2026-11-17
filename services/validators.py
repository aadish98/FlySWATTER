"""Validation helpers for GUI input selection."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, List

from services.models import ValidationResult

ZANTIKS_SCORE_PATTERN = re.compile(
    r"^(\d{6}|\d{2}-\d{2}-\d{4})_arousal_experiment_script_\d+Day_protocol-\d{8}T\d{6}\.(csv|xlsx)$",
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
                "Please re-download the Zantiks file from the system, or restore the original filename format.",
                "Open the filename guide for the required format and which part to edit for clock drift.",
            ],
            help_document="zantiks_filename_format.html",
        )
    return ValidationResult(True)


def validate_monitor_file(path: str | Path) -> ValidationResult:
    """Validate an optional monitor temperature log upload.

    The monitor log is only used for temperature plotting, so its only
    structural requirement is that it's a .txt file (unlike the strict
    filename checks applied to the primary Zantiks upload).
    """
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return ValidationResult(False, "Select a valid monitor temperature log file.")
    if file_path.suffix.lower() != ".txt":
        return ValidationResult(False, "The monitor temperature log must be a .txt file.")
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


GENERAL_REQUIRED_COLUMNS = ("t_ms", "X", "Y", "Z")


def _find_general_manifest(folder_path: Path) -> Path | None:
    """The logger writes manifest.json beside the data folder or one level up."""
    for candidate in (folder_path / "manifest.json", folder_path.parent / "manifest.json"):
        if candidate.is_file():
            return candidate
    return None


def validate_accel_folder_general(path: str | Path) -> ValidationResult:
    """Validate an accelerometer log folder without any naming conventions.

    Pulse detection reads timing from manifest.json and samples from the
    t_ms/X/Y/Z columns, so those are the only things that have to be right.
    Folder and file names are never inspected.
    """
    import pandas as pd

    folder_path = Path(path)
    if not folder_path.exists() or not folder_path.is_dir():
        return ValidationResult(False, "Select a valid accelerometer log folder.")

    manifest_path = _find_general_manifest(folder_path)
    if manifest_path is None:
        return ValidationResult(False, "Could not find manifest.json in the selected log folder.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ValidationResult(False, "manifest.json could not be read.", [str(exc)])
    if not isinstance(manifest, dict) or "start_iso" not in manifest:
        return ValidationResult(False, "manifest.json is missing the recording start time (start_iso).")

    discovered_files = sorted(
        child
        for child in folder_path.rglob("*")
        if child.is_file() and (child.name.lower().endswith(".csv") or child.name.lower().endswith(".csv.gz"))
    )
    if not discovered_files:
        return ValidationResult(False, "No accelerometer log files were found in the selected folder.")

    try:
        header = pd.read_csv(discovered_files[0], nrows=0)
    except Exception as exc:  # unreadable or corrupt file
        return ValidationResult(
            False,
            "Could not read the first accelerometer log file.",
            [f"{discovered_files[0].name}: {exc}"],
        )
    missing = [column for column in GENERAL_REQUIRED_COLUMNS if column not in header.columns]
    if missing:
        return ValidationResult(
            False,
            "The accelerometer log files are missing required columns.",
            [
                f"Missing column(s): {', '.join(missing)}",
                f"Expected columns: {', '.join(GENERAL_REQUIRED_COLUMNS)}",
            ],
        )
    return ValidationResult(True)
