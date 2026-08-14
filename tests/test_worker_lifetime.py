"""Guards against the worker being collected while its job is still running."""

from __future__ import annotations

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from gui.workers import FunctionWorker


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_progress_survives_dropped_reference(qt_app):
    """Emitting progress must not raise once the caller drops the worker."""
    seen = []

    def job(progress_callback=None):
        # Simulate the GUI dropping its only reference mid-run.
        gc.collect()
        progress_callback(50, "halfway")
        seen.append("finished job")
        return "result"

    worker = FunctionWorker(job)
    worker.run()
    assert seen == ["finished job"]


def test_run_does_not_raise_when_signals_are_gone(qt_app):
    """A destroyed signal carrier must not propagate out of run()."""

    def job(progress_callback=None):
        progress_callback(10, "working")
        return "done"

    worker = FunctionWorker(job)

    class DeadSignals:
        def __getattr__(self, name):
            raise RuntimeError("Signal source has been deleted")

    worker.signals = DeadSignals()
    worker.run()  # must not raise


def test_main_window_keeps_workers_alive(qt_app):
    from gui.main_window import FlySwatterMainWindow

    window = FlySwatterMainWindow.__new__(FlySwatterMainWindow)
    window._active_workers = set()

    worker = FunctionWorker(lambda progress_callback=None: None)
    FlySwatterMainWindow._track_worker(window, worker)
    assert FlySwatterMainWindow._analysis_in_progress(window) is True

    worker_id = id(worker)
    del worker
    gc.collect()
    # The tracking set is the only remaining reference and must hold it.
    assert len(window._active_workers) == 1
    assert id(next(iter(window._active_workers))) == worker_id
