"""Background worker helpers for long-running GUI actions."""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int, str)


class FunctionWorker(QRunnable):
    def __init__(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.func(*self.args, progress_callback=self.report_progress, **self.kwargs)
        except Exception as exc:  # pragma: no cover - surfaced in GUI runtime
            tb = traceback.format_exc()
            self.signals.error.emit(f"{exc}\n\n{tb}")
        else:
            self.signals.finished.emit(result)

    def report_progress(self, value: int, message: str) -> None:
        self.signals.progress.emit(value, message)
