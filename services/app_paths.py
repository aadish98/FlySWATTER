"""Resolve runtime paths for source and bundled application launches."""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "FlySWATTER"


@dataclass(frozen=True)
class RuntimePaths:
    project_root: Path
    resource_root: Path
    data_root: Path
    frozen: bool
    used_fallback_data_root: bool


def resolve_runtime_paths(entry_file: str | Path) -> RuntimePaths:
    """Return project/resource/data roots for the current runtime mode."""
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        executable = Path(sys.executable).resolve()
        project_root = _resolve_project_root_for_frozen(executable)
        resource_root = Path(getattr(sys, "_MEIPASS", executable.parent)).resolve()
    else:
        resource_root = Path(entry_file).resolve().parent
        project_root = resource_root

    preferred_data_root = project_root / "Data"
    fallback_data_root = _default_user_data_root()
    data_root, used_fallback = _ensure_writable_data_root(preferred_data_root, fallback_data_root)

    return RuntimePaths(
        project_root=project_root,
        resource_root=resource_root,
        data_root=data_root,
        frozen=frozen,
        used_fallback_data_root=used_fallback,
    )


def _resolve_project_root_for_frozen(executable: Path) -> Path:
    for parent in executable.parents:
        if parent.suffix.lower() == ".app":
            return parent.parent
    return executable.parent


def _default_user_data_root() -> Path:
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        return home / "Library" / "Application Support" / APP_NAME / "Data"
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME / "Data"
        return home / "AppData" / "Roaming" / APP_NAME / "Data"
    return home / f".{APP_NAME.lower()}" / "data"


def _ensure_writable_data_root(preferred: Path, fallback: Path) -> tuple[Path, bool]:
    if _is_writable_directory(preferred):
        return preferred, False
    if preferred != fallback and _is_writable_directory(fallback):
        return fallback, True
    raise PermissionError(
        f"Could not create a writable data directory at '{preferred}' or fallback '{fallback}'."
    )


def _is_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return path.is_dir() and os.access(path, os.W_OK | os.X_OK)
