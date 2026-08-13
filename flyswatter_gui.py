#!/usr/bin/env python3
"""Launch the FlySWATTER desktop GUI."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

# OpenBLAS (used by numpy) is not thread-safe under Qt when MAX_THREADS > 1.
# Force single-threaded BLAS to prevent Bus errors in worker threads.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from PySide6.QtWidgets import QApplication, QMessageBox

from gui.main_window import FlySwatterMainWindow
from gui.theme import apply_forced_dark_theme
from services.app_paths import resolve_runtime_paths


def _write_crash_log(data_root: Path, text: str) -> Path | None:
    for directory in (data_root / "Logs", Path(tempfile.gettempdir())):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            log_path = directory / f"FlySWATTER-crash-{datetime.now():%Y%m%d-%H%M%S}.log"
            log_path.write_text(text, encoding="utf-8")
            return log_path
        except OSError:
            continue
    return None


def _install_crash_handler(data_root: Path) -> None:
    """Record unhandled errors instead of letting the app disappear.

    A bundled app has nowhere to print a traceback, so without this an
    unexpected error looks like an unexplained crash to the user.
    """

    def handle(exc_type, exc_value, exc_tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stderr.write(text)
        log_path = _write_crash_log(data_root, text)
        try:
            box = QMessageBox()
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("FlySWATTER encountered an error")
            box.setText(str(exc_value) or exc_type.__name__)
            if log_path is not None:
                box.setInformativeText(f"Details were saved to:\n{log_path}")
            box.setDetailedText(text)
            box.exec()
        except Exception:
            pass

    sys.excepthook = handle


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("FlySWATTER")
    apply_forced_dark_theme(app)
    runtime_paths = resolve_runtime_paths(__file__)
    _install_crash_handler(runtime_paths.data_root)
    window = FlySwatterMainWindow(
        project_root=runtime_paths.project_root,
        data_root=runtime_paths.data_root,
        resource_root=runtime_paths.resource_root,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
