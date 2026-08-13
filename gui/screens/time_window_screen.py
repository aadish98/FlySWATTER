"""Time-window selection screen for pulse-metrics analysis."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from services.models import FolderWindowSummary
from gui.theme import DARK_THEME
from gui.widgets.range_slider_widget import RangeSliderWidget

_ANALYSIS_STEP_MINUTES = 5
_PULSE_STEP_MINUTES = 1
_DEFAULT_PULSE_MINUTES = 5
_DEFAULT_PULSE_SPACING = timedelta(hours=2)


class _PulseWindowRow(QFrame):
    removed = Signal(object)
    rangeChanged = Signal()

    def __init__(
        self,
        *,
        range_start_dt: datetime,
        range_end_dt: datetime,
        start_dt: datetime,
        end_dt: datetime,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._range_start_dt = range_start_dt
        self._range_end_dt = range_end_dt
        self._step_minutes = _PULSE_STEP_MINUTES
        self._updating = False
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ background: {DARK_THEME.surface_alt}; border: 1px solid {DARK_THEME.border}; border-radius: 8px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.title_label = QLabel("Pulse window")
        self.title_label.setStyleSheet("font-weight: 700;")
        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(lambda: self.removed.emit(self))
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(remove_button)

        self.range_slider = RangeSliderWidget()
        self.range_slider.rangeChanged.connect(self._handle_slider_changed)
        selected_row = QHBoxLayout()
        self.start_label = QLabel("Start: -")
        self.end_label = QLabel("End: -")
        self.start_label.setStyleSheet(f"font-weight: 600; color: {DARK_THEME.text_primary};")
        self.end_label.setStyleSheet(f"font-weight: 600; color: {DARK_THEME.text_primary};")
        selected_row.addWidget(self.start_label)
        selected_row.addStretch(1)
        selected_row.addWidget(self.end_label)

        layout.addLayout(header)
        layout.addWidget(self.range_slider)
        layout.addLayout(selected_row)
        self.set_bounds(range_start_dt, range_end_dt)
        self.set_window(start_dt, end_dt, emit=False)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def set_bounds(self, range_start_dt: datetime, range_end_dt: datetime) -> None:
        self._range_start_dt = range_start_dt
        self._range_end_dt = range_end_dt
        self.range_slider.set_bounds(0, self._max_step())
        start_dt, end_dt = self.window_datetimes()
        self.set_window(start_dt, end_dt, emit=False)

    def set_window(self, start_dt: datetime, end_dt: datetime, *, emit: bool = True) -> None:
        lower = self._datetime_to_step(start_dt)
        upper = self._datetime_to_step(end_dt)
        if upper <= lower:
            upper = min(lower + _DEFAULT_PULSE_MINUTES, self._max_step())
        self._updating = True
        self.range_slider.set_range(lower, upper, emit=False)
        self._updating = False
        self._refresh_labels()
        if emit:
            self.rangeChanged.emit()

    def window_datetimes(self) -> tuple[datetime, datetime]:
        start_dt = self._step_to_datetime(self.range_slider.lower_value())
        end_dt = self._step_to_datetime(self.range_slider.upper_value())
        if end_dt <= start_dt:
            end_dt = min(start_dt + timedelta(minutes=_DEFAULT_PULSE_MINUTES), self._range_end_dt)
        return start_dt, end_dt

    def as_dict(self) -> dict[str, str]:
        start_dt, end_dt = self.window_datetimes()
        return {"start_iso": start_dt.isoformat(), "end_iso": end_dt.isoformat()}

    def _handle_slider_changed(self, _lower: int, _upper: int) -> None:
        if self._updating:
            return
        self._refresh_labels()
        self.rangeChanged.emit()

    def _refresh_labels(self) -> None:
        start_dt, end_dt = self.window_datetimes()
        self.start_label.setText(f"Start: {TimeWindowScreen._format_dt(start_dt)}")
        self.end_label.setText(f"End: {TimeWindowScreen._format_dt(end_dt)}")

    def _max_step(self) -> int:
        total_seconds = max((self._range_end_dt - self._range_start_dt).total_seconds(), 0.0)
        return max(int(math.ceil(total_seconds / (self._step_minutes * 60))), 1)

    def _datetime_to_step(self, value: datetime) -> int:
        clamped = min(max(value, self._range_start_dt), self._range_end_dt)
        minutes = (clamped - self._range_start_dt).total_seconds() / 60.0
        return int(round(minutes / self._step_minutes))

    def _step_to_datetime(self, step: int) -> datetime:
        resolved = self._range_start_dt + timedelta(minutes=self._step_minutes * int(step))
        if resolved > self._range_end_dt:
            return self._range_end_dt
        if resolved < self._range_start_dt:
            return self._range_start_dt
        return resolved


class TimeWindowScreen(QWidget):
    backRequested = Signal()
    continueRequested = Signal(str, str, list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._range_start_dt: datetime | None = None
        self._range_end_dt: datetime | None = None
        self._selected_start_dt: datetime | None = None
        self._selected_end_dt: datetime | None = None
        self._max_slider_step = 1
        self._step_minutes = _ANALYSIS_STEP_MINUTES
        self._updating_controls = False
        self._pulse_rows: list[_PulseWindowRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(50, 36, 50, 36)
        layout.setSpacing(14)

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

        self.slider_help_label = QLabel("Drag the two slider handles to choose the overall analysis range.")
        self.slider_help_label.setStyleSheet(f"font-size: 12px; color: {DARK_THEME.text_muted};")

        pulse_header = QHBoxLayout()
        pulse_title = QLabel("Expected Pulse Windows")
        pulse_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        add_button = QPushButton("Add pulse window")
        add_button.clicked.connect(self.add_pulse_window)
        pulse_header.addWidget(pulse_title)
        pulse_header.addStretch(1)
        pulse_header.addWidget(add_button)

        self.pulse_help_label = QLabel(
            "Add one window around each delivered pulse. Detection only runs inside these windows."
        )
        self.pulse_help_label.setStyleSheet(f"font-size: 12px; color: {DARK_THEME.text_muted};")
        self.pulse_help_label.setWordWrap(True)

        self.pulse_list = QWidget()
        self.pulse_list_layout = QVBoxLayout(self.pulse_list)
        self.pulse_list_layout.setContentsMargins(0, 0, 0, 0)
        self.pulse_list_layout.setSpacing(10)
        self.pulse_list_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.pulse_list)
        scroll.setMinimumHeight(180)

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
        layout.addLayout(pulse_header)
        layout.addWidget(self.pulse_help_label)
        layout.addWidget(scroll, 1)
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
        self.clear_pulse_windows()
        self.add_pulse_window()

    def add_pulse_window(self) -> None:
        if self._range_start_dt is None or self._range_end_dt is None:
            return
        start_dt, end_dt = self._default_next_pulse_window()
        row = _PulseWindowRow(
            range_start_dt=self._range_start_dt,
            range_end_dt=self._range_end_dt,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        row.removed.connect(self._remove_pulse_row)
        self.pulse_list_layout.insertWidget(len(self._pulse_rows), row)
        self._pulse_rows.append(row)
        self._refresh_pulse_titles()

    def clear_pulse_windows(self) -> None:
        for row in list(self._pulse_rows):
            self._remove_pulse_row(row, refresh=False)
        self._refresh_pulse_titles()

    def remove_pulse_window_at(self, index: int) -> None:
        if 0 <= index < len(self._pulse_rows):
            self._remove_pulse_row(self._pulse_rows[index])

    def pulse_window_count(self) -> int:
        return len(self._pulse_rows)

    def pulse_windows(self) -> list[dict[str, str]]:
        windows = [row.as_dict() for row in self._pulse_rows]
        windows.sort(key=lambda item: item["start_iso"])
        return windows

    def _remove_pulse_row(self, row: _PulseWindowRow, *, refresh: bool = True) -> None:
        if row not in self._pulse_rows:
            return
        self._pulse_rows.remove(row)
        self.pulse_list_layout.removeWidget(row)
        row.deleteLater()
        if refresh:
            self._refresh_pulse_titles()

    def _refresh_pulse_titles(self) -> None:
        ordered = sorted(self._pulse_rows, key=lambda row: row.window_datetimes()[0])
        for index, row in enumerate(ordered, start=1):
            row.set_title(f"Pulse {index}")

    def _default_next_pulse_window(self) -> tuple[datetime, datetime]:
        assert self._range_start_dt is not None
        assert self._range_end_dt is not None
        if self._pulse_rows:
            last_start, _last_end = self._pulse_rows[-1].window_datetimes()
            start_dt = last_start + _DEFAULT_PULSE_SPACING
        else:
            start_dt = self._selected_start_dt or self._range_start_dt
        end_dt = start_dt + timedelta(minutes=_DEFAULT_PULSE_MINUTES)
        if end_dt > self._range_end_dt:
            end_dt = self._range_end_dt
            start_dt = max(self._range_start_dt, end_dt - timedelta(minutes=_DEFAULT_PULSE_MINUTES))
        if start_dt < self._range_start_dt:
            start_dt = self._range_start_dt
        if end_dt <= start_dt:
            end_dt = min(start_dt + timedelta(minutes=_DEFAULT_PULSE_MINUTES), self._range_end_dt)
        return start_dt, end_dt

    def _emit_continue(self) -> None:
        start_dt = self._selected_start_dt or self._range_start_dt or datetime.now()
        end_dt = self._selected_end_dt or self._range_end_dt or start_dt
        if end_dt < start_dt:
            end_dt = start_dt
        windows = self.pulse_windows()
        if not windows:
            QMessageBox.warning(self, "Pulse window required", "Add at least one expected pulse window before starting analysis.")
            return
        for index, window in enumerate(windows, start=1):
            pulse_start = datetime.fromisoformat(window["start_iso"])
            pulse_end = datetime.fromisoformat(window["end_iso"])
            if pulse_end <= start_dt or pulse_start >= end_dt:
                QMessageBox.warning(
                    self,
                    "Pulse window outside analysis range",
                    f"Pulse {index} is outside the selected analysis range. Move the window or expand the analysis range.",
                )
                return
        self.continueRequested.emit(start_dt.isoformat(), end_dt.isoformat(), windows)

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
