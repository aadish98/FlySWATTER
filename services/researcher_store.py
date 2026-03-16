"""Persistence helpers for researcher names used in the GUI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List


def get_researcher_store_path(data_root: Path) -> Path:
    return data_root / "researchers.json"


def load_researcher_names(data_root: Path) -> List[str]:
    store_path = get_researcher_store_path(data_root)
    if not store_path.exists():
        return []
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
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
        store_path.write_text(
            json.dumps({"researchers": names}, indent=2),
            encoding="utf-8",
        )
    return names
