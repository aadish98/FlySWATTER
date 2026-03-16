"""Shared progress screen widget with indeterminate and timer-based modes."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget


class ProgressScreenBase(QWidget):
    def __init__(self, title_text: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(60, 60, 60, 60)
        layout.setSpacing(18)

        self.title = QLabel(title_text, alignment=Qt.AlignCenter)
        self.title.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.status_label = QLabel("Preparing analysis...", alignment=Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)

        layout.addStretch(1)
        layout.addWidget(self.title)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addStretch(1)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)
        self._elapsed_seconds = 0
        self._estimated_seconds = 0

    def start_indeterminate(self, message: str = "Processing...") -> None:
        """Show a spinning busy indicator with no percentage."""
        self._tick_timer.stop()
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(message)

    def start_timed(self, estimated_seconds: int, message: str = "Processing...") -> None:
        """Show progress driven by a wall-clock timer heuristic."""
        self._tick_timer.stop()
        self._elapsed_seconds = 0
        self._estimated_seconds = max(estimated_seconds, 1)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.status_label.setText(message)
        self._tick_timer.start()

    def finish(self, message: str = "Complete.") -> None:
        """Stop any animation / timer and show 100%."""
        self._tick_timer.stop()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.status_label.setText(message)

    def _tick(self) -> None:
        self._elapsed_seconds += 1
        remaining = max(self._estimated_seconds - self._elapsed_seconds, 0)
        pct = min(int((self._elapsed_seconds / self._estimated_seconds) * 100), 95)
        self.progress_bar.setValue(pct)
        mins, secs = divmod(remaining, 60)
        self.status_label.setText(f"Processing\u2026 estimated {mins}m {secs:02d}s remaining")
