"""Persistence helpers for researcher names used in the GUI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List


def get_researcher_store_path(data_root: Path) -> Path:
    return data_root / "researchers.json"


def load_researcher_names(data_root: Path) -> List[str]:
    store_path = get_researcher_store_path(data_root)
    try:
        if not store_path.exists():
            return []
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A missing or unreadable name list must never stop the app from opening.
        return []
    names = payload.get("researchers", [])
    if not isinstance(names, list):
        return []
    return [str(name).strip() for name in names if str(name).strip()]


def save_researcher_name(data_root: Path, researcher_name: str) -> List[str]:
    clean_name = researcher_name.strip()
    if not clean_name:
        raise ValueError("Researcher name cannot be blank")
    store_path = get_researcher_store_path(data_root)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    names = load_researcher_names(data_root)
    lowered = {name.casefold(): name for name in names}
    if clean_name.casefold() not in lowered:
        names.append(clean_name)
        names = sorted(set(names), key=str.casefold)
        try:
            _write_text_atomic(store_path, json.dumps({"researchers": names}, indent=2))
        except OSError:
            # The name list is a convenience; a read-only/blocked store must
            # never stop the user from continuing into an analysis.
            pass
    return names


def _write_text_atomic(path: Path, text: str) -> None:
    """Write via a new temp file and rename over the target.

    macOS can refuse in-place writes to an existing file (EPERM, e.g. files
    carrying another app's com.apple.provenance) even when the directory is
    writable. Creating a new file and replacing the old one only needs
    directory write access.
    """
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
