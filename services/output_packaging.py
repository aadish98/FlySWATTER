"""Zip packaging helpers for GUI download actions."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List
from zipfile import ZIP_DEFLATED, ZipFile


def _iter_existing(paths: Iterable[Path]) -> List[Path]:
    return [Path(path) for path in paths if path and Path(path).exists()]


def create_zip_from_paths(zip_path: Path, paths: Iterable[Path], base_dir: Path | None = None) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    existing_paths = _iter_existing(paths)
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in existing_paths:
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file():
                        if base_dir is not None:
                            arcname = child.relative_to(base_dir)
                        else:
                            arcname = child.relative_to(path.parent)
                        archive.write(child, arcname.as_posix())
            else:
                if base_dir is not None:
                    arcname = path.relative_to(base_dir)
                else:
                    arcname = path.name
                archive.write(path, arcname.as_posix())
    return zip_path
