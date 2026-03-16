"""Time-window selection screen for pulse-metrics analysis."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from services.models import FolderWindowSummary
from gui.theme import DARK_THEME
from gui.widgets.range_slider_widget import RangeSliderWidget


class TimeWindowScreen(QWidget):
    backRequested = Signal()
    continueRequested = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._range_start_dt: datetime | None = None
        self._range_end_dt: datetime | None = None
        self._selected_start_dt: datetime | None = None
        self._selected_end_dt: datetime | None = None
        self._max_slider_step = 1
        self._step_minutes = 5
        self._updating_controls = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(50, 50, 50, 50)
        layout.setSpacing(18)

        header = QHBoxLayout()
        back_button = QPushButton("Back")
        back_button.clicked.connect(self.backRequested)
        header.addWidget(back_button)
        header.addStretch(1)

        title = QLabel("Select Time Window to Analyze")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(f"font-size: 13px; color: {DARK_THEME.text_secondary};")

        available_row = QHBoxLayout()
        self.available_start_label = QLabel("Available start: -")
        self.available_end_label = QLabel("Available end: -")
        self.available_start_label.setStyleSheet(f"font-weight: 600; color: {DARK_THEME.text_primary};")
        self.available_end_label.setStyleSheet(f"font-weight: 600; color: {DARK_THEME.text_primary};")
        available_row.addWidget(self.available_start_label)
        available_row.addStretch(1)
        available_row.addWidget(self.available_end_label)

        self.range_slider = RangeSliderWidget()
        self.range_slider.rangeChanged.connect(self._handle_slider_changed)

        selected_row = QHBoxLayout()
        self.selected_start_label = QLabel("Selected start: -")
        self.selected_end_label = QLabel("Selected end: -")
        self.selected_start_label.setStyleSheet(f"font-weight: 600; color: {DARK_THEME.text_primary};")
        self.selected_end_label.setStyleSheet(f"font-weight: 600; color: {DARK_THEME.text_primary};")
        selected_row.addWidget(self.selected_start_label)
        selected_row.addStretch(1)
        selected_row.addWidget(self.selected_end_label)

        self.slider_help_label = QLabel("Drag the two slider handles to choose start and end times.")
        self.slider_help_label.setStyleSheet(f"font-size: 12px; color: {DARK_THEME.text_muted};")

        buttons = QHBoxLayout()
        continue_button = QPushButton("Start Analysis")
        continue_button.clicked.connect(self._emit_continue)
        buttons.addStretch(1)
        buttons.addWidget(continue_button)

        layout.addLayout(header)
        layout.addWidget(title)
        layout.addWidget(self.summary_label)
        layout.addLayout(available_row)
        layout.addWidget(self.range_slider)
        layout.addWidget(self.slider_help_label)
        layout.addLayout(selected_row)
        layout.addStretch(1)
        layout.addLayout(buttons)

    def set_summary(self, summary: FolderWindowSummary) -> None:
        start_dt = datetime.fromisoformat(summary.start_ts_iso)
        end_dt = datetime.fromisoformat(summary.end_ts_iso)
        if end_dt < start_dt:
            end_dt = start_dt
        self._range_start_dt = start_dt
        self._range_end_dt = end_dt
        total_seconds = max((end_dt - start_dt).total_seconds(), 0.0)
        self._max_slider_step = max(int(math.ceil(total_seconds / (self._step_minutes * 60))), 1)
        self.range_slider.set_bounds(0, self._max_slider_step)
        self.range_slider.set_range(0, self._max_slider_step, emit=False)
        self._sync_controls_from_slider()
        self.available_start_label.setText(f"Available start: {self._format_dt(start_dt)}")
        self.available_end_label.setText(f"Available end: {self._format_dt(end_dt)}")
        self.summary_label.setText(
            f"Folder: {summary.display_name}\nAvailable range: {start_dt.strftime('%m-%d-%Y %I:%M %p')} to {end_dt.strftime('%m-%d-%Y %I:%M %p')}\nLog files found: {len(summary.csv_files)}"
        )

    def _emit_continue(self) -> None:
        start_dt = self._selected_start_dt or self._range_start_dt or datetime.now()
        end_dt = self._selected_end_dt or self._range_end_dt or start_dt
        if end_dt < start_dt:
            end_dt = start_dt
        start_iso = start_dt.isoformat()
        end_iso = end_dt.isoformat()
        self.continueRequested.emit(start_iso, end_iso)

    def _handle_slider_changed(self, _lower: int, _upper: int) -> None:
        if self._updating_controls:
            return
        self._sync_controls_from_slider()

    def _sync_controls_from_slider(self) -> None:
        start_dt = self._step_to_datetime(self.range_slider.lower_value())
        end_dt = self._step_to_datetime(self.range_slider.upper_value())
        self._selected_start_dt = start_dt
        self._selected_end_dt = end_dt
        self.selected_start_label.setText(f"Selected start: {self._format_dt(start_dt)}")
        self.selected_end_label.setText(f"Selected end: {self._format_dt(end_dt)}")

    def _step_to_datetime(self, step: int) -> datetime:
        if self._range_start_dt is None:
            return datetime.now()
        resolved = self._range_start_dt + timedelta(minutes=self._step_minutes * int(step))
        if self._range_end_dt is not None and resolved > self._range_end_dt:
            return self._range_end_dt
        return resolved

    @staticmethod
    def _format_dt(value: datetime) -> str:
        return value.strftime("%m-%d-%Y %I:%M %p")
