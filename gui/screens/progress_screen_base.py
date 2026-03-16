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
        self._estimated_seconds_max = 0

    def start_indeterminate(self, message: str = "Processing...") -> None:
        """Show a spinning busy indicator with no percentage."""
        self._tick_timer.stop()
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(message)

    def start_timed(
        self,
        estimated_seconds: int,
        message: str = "Processing...",
        *,
        estimated_seconds_max: int | None = None,
    ) -> None:
        """Show progress driven by a wall-clock timer heuristic.

        When *estimated_seconds_max* is provided the remaining-time label
        displays a range (e.g. "~3 min – 8 min remaining") and the progress
        bar advances using the midpoint of the two bounds.
        """
        self._tick_timer.stop()
        self._elapsed_seconds = 0
        self._estimated_seconds = max(estimated_seconds, 1)
        self._estimated_seconds_max = (
            max(estimated_seconds_max, self._estimated_seconds)
            if estimated_seconds_max is not None
            else 0
        )
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

    @staticmethod
    def _fmt_duration(seconds: int) -> str:
        mins, secs = divmod(seconds, 60)
        if mins:
            return f"{mins}m {secs:02d}s"
        return f"{secs}s"

    def _tick(self) -> None:
        self._elapsed_seconds += 1

        if self._estimated_seconds_max:
            midpoint = (self._estimated_seconds + self._estimated_seconds_max) // 2
            pct = min(int((self._elapsed_seconds / midpoint) * 100), 95)
            remaining_lo = max(self._estimated_seconds - self._elapsed_seconds, 0)
            remaining_hi = max(self._estimated_seconds_max - self._elapsed_seconds, 0)
            self.progress_bar.setValue(pct)
            if remaining_lo == 0 and remaining_hi == 0:
                self.status_label.setText("Processing\u2026 wrapping up")
            elif remaining_lo == 0:
                self.status_label.setText(
                    f"Processing\u2026 up to ~{self._fmt_duration(remaining_hi)} remaining"
                )
            else:
                self.status_label.setText(
                    f"Processing\u2026 ~{self._fmt_duration(remaining_lo)}"
                    f" \u2013 {self._fmt_duration(remaining_hi)} remaining"
                )
        else:
            remaining = max(self._estimated_seconds - self._elapsed_seconds, 0)
            pct = min(int((self._elapsed_seconds / self._estimated_seconds) * 100), 95)
            self.progress_bar.setValue(pct)
            self.status_label.setText(
                f"Processing\u2026 estimated {self._fmt_duration(remaining)} remaining"
            )
