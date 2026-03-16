#!/usr/bin/env python3
"""Launch the FlySWATTER desktop GUI."""

from __future__ import annotations

import os
import sys

# OpenBLAS (used by numpy) is not thread-safe under Qt when MAX_THREADS > 1.
# Force single-threaded BLAS to prevent Bus errors in worker threads.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from PySide6.QtWidgets import QApplication

from gui.main_window import FlySwatterMainWindow
from gui.theme import apply_forced_dark_theme
from services.app_paths import resolve_runtime_paths


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("FlySWATTER")
    apply_forced_dark_theme(app)
    runtime_paths = resolve_runtime_paths(__file__)
    window = FlySwatterMainWindow(
        project_root=runtime_paths.project_root,
        data_root=runtime_paths.data_root,
        resource_root=runtime_paths.resource_root,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
