from __future__ import annotations

import sys
from pathlib import Path

from services import app_paths


def test_source_mode_paths_use_entry_parent(tmp_path: Path):
    entry_file = tmp_path / "flyswatter_gui.py"
    entry_file.write_text("# placeholder\n", encoding="utf-8")

    paths = app_paths.resolve_runtime_paths(entry_file)

    assert not paths.frozen
    assert paths.project_root == tmp_path
    assert paths.resource_root == tmp_path
    assert paths.data_root == tmp_path / "Data"
    assert paths.data_root.is_dir()
    assert not paths.used_fallback_data_root


def test_frozen_mode_uses_app_bundle_parent(monkeypatch, tmp_path: Path):
    repo_root = tmp_path / "repo"
    executable = repo_root / "FlySWATTER.app" / "Contents" / "MacOS" / "FlySWATTER"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("", encoding="utf-8")
    meipass = tmp_path / "_MEI12345"
    meipass.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable), raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    paths = app_paths.resolve_runtime_paths(tmp_path / "ignored.py")

    assert paths.frozen
    assert paths.project_root == repo_root
    assert paths.resource_root == meipass.resolve()
    assert paths.data_root == repo_root / "Data"
    assert paths.data_root.is_dir()
    assert not paths.used_fallback_data_root


def test_data_root_falls_back_when_project_root_is_not_writable(monkeypatch, tmp_path: Path):
    entry_file = tmp_path / "project" / "flyswatter_gui.py"
    entry_file.parent.mkdir(parents=True, exist_ok=True)
    entry_file.write_text("# placeholder\n", encoding="utf-8")
    fallback_root = tmp_path / "fallback" / "Data"

    preferred_root = entry_file.parent / "Data"

    def fake_is_writable_directory(path: Path) -> bool:
        if path == preferred_root:
            return False
        path.mkdir(parents=True, exist_ok=True)
        return True

    monkeypatch.setattr(app_paths, "_is_writable_directory", fake_is_writable_directory)
    monkeypatch.setattr(app_paths, "_default_user_data_root", lambda: fallback_root)

    paths = app_paths.resolve_runtime_paths(entry_file)

    assert paths.data_root == fallback_root
    assert paths.used_fallback_data_root
