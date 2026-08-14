"""Schedule editor for pulse-metrics analysis."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QPointF, QDateTime, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui.theme import DARK_THEME
from services.models import FolderWindowSummary

_DEFAULT_SEARCH_RADIUS_MINUTES = 5
_DEFAULT_PULSE_SPACING = timedelta(hours=2)
_DATE_TIME_FORMAT = "MMM d, yyyy  h:mm AP"


def _to_qdatetime(value: datetime) -> QDateTime:
    return QDateTime(value)


class _PulseTimeRow(QFrame):
    removed = Signal(object)
    timeChanged = Signal()

    def __init__(
        self,
        *,
        range_start_dt: datetime,
        range_end_dt: datetime,
        pulse_dt: datetime,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pulseTimeRow")
        self.setStyleSheet(
            f"""
            QFrame#pulseTimeRow {{
                background: {DARK_THEME.surface_alt};
                border: 1px solid {DARK_THEME.border};
                border-radius: 10px;
            }}
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(12)

        self.title_label = QLabel("Pulse")
        self.title_label.setMinimumWidth(62)
        self.title_label.setStyleSheet("font-weight: 700;")

        self.time_edit = QDateTimeEdit()
        self.time_edit.setDisplayFormat(_DATE_TIME_FORMAT)
        self.time_edit.setCalendarPopup(True)
        self.time_edit.setMinimumDateTime(_to_qdatetime(range_start_dt))
        self.time_edit.setMaximumDateTime(_to_qdatetime(range_end_dt))
        self.time_edit.setDateTime(_to_qdatetime(pulse_dt))
        self.time_edit.setMinimumWidth(230)
        self.time_edit.dateTimeChanged.connect(lambda _value: self.timeChanged.emit())

        remove_button = QPushButton("Remove")
        remove_button.setToolTip("Remove this expected pulse")
        remove_button.clicked.connect(lambda: self.removed.emit(self))

        layout.addWidget(self.title_label)
        layout.addWidget(self.time_edit, 1)
        layout.addWidget(remove_button)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def pulse_datetime(self) -> datetime:
        return self.time_edit.dateTime().toPython()


class _PulseTimeline(QWidget):
    """Compact, read-only overview of pulse positions in the recording."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._start: datetime | None = None
        self._end: datetime | None = None
        self._pulses: list[datetime] = []
        self.setMinimumHeight(86)

    def set_data(self, start: datetime, end: datetime, pulses: list[datetime]) -> None:
        self._start = start
        self._end = end
        self._pulses = sorted(pulses)
        self.update()

    def paintEvent(self, _event) -> None:
        if self._start is None or self._end is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        left, right = 18.0, float(max(self.width() - 18, 19))
        line_y = 32.0

        painter.setPen(QPen(QColor(DARK_THEME.border), 4, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(left, line_y), QPointF(right, line_y))

        span = max((self._end - self._start).total_seconds(), 1.0)
        painter.setPen(QPen(QColor(DARK_THEME.accent_hover), 2))
        painter.setBrush(QColor(DARK_THEME.accent))
        for pulse in self._pulses:
            ratio = min(max((pulse - self._start).total_seconds() / span, 0.0), 1.0)
            x_pos = left + ratio * (right - left)
            painter.drawEllipse(QPointF(x_pos, line_y), 6, 6)

        painter.setPen(QColor(DARK_THEME.text_muted))
        painter.drawText(0, 55, self.width() // 2, 24, Qt.AlignLeft, TimeWindowScreen._format_dt(self._start))
        painter.drawText(
            self.width() // 2,
            55,
            self.width() // 2,
            24,
            Qt.AlignRight,
            TimeWindowScreen._format_dt(self._end),
        )


class TimeWindowScreen(QWidget):
    backRequested = Signal()
    continueRequested = Signal(str, str, list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._range_start_dt: datetime | None = None
        self._range_end_dt: datetime | None = None
        self._pulse_rows: list[_PulseTimeRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(50, 30, 50, 30)
        layout.setSpacing(12)

        header = QHBoxLayout()
        back_button = QPushButton("Back")
        back_button.clicked.connect(self.backRequested)
        header.addWidget(back_button)
        header.addStretch(1)

        title = QLabel("Schedule Expected Pulses")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        subtitle = QLabel(
            "Enter the scheduled time of each pulse. FlySWATTER will search around those times automatically."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {DARK_THEME.text_secondary};")

        summary_panel = QFrame()
        summary_panel.setObjectName("recordingSummary")
        summary_panel.setStyleSheet(
            f"""
            QFrame#recordingSummary {{
                background: {DARK_THEME.surface};
                border: 1px solid {DARK_THEME.border};
                border-radius: 12px;
            }}
            """
        )
        summary_layout = QVBoxLayout(summary_panel)
        summary_layout.setContentsMargins(14, 10, 14, 10)
        self.summary_label = QLabel("No recording selected")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(f"font-weight: 600; color: {DARK_THEME.text_primary};")
        summary_layout.addWidget(self.summary_label)

        search_row = QHBoxLayout()
        search_label = QLabel("Search")
        search_label.setStyleSheet("font-weight: 700;")
        self.search_radius_input = QSpinBox()
        self.search_radius_input.setRange(1, 120)
        self.search_radius_input.setValue(_DEFAULT_SEARCH_RADIUS_MINUTES)
        self.search_radius_input.setSuffix(" min")
        self.search_radius_input.setFixedWidth(92)
        self.search_radius_input.valueChanged.connect(self._refresh_schedule)
        search_help = QLabel("before and after each scheduled time")
        search_help.setStyleSheet(f"color: {DARK_THEME.text_secondary};")
        search_row.addWidget(search_label)
        search_row.addWidget(self.search_radius_input)
        search_row.addWidget(search_help)
        search_row.addStretch(1)

        self.timeline = _PulseTimeline()

        series_panel = QFrame()
        series_panel.setObjectName("seriesPanel")
        series_panel.setStyleSheet(
            f"""
            QFrame#seriesPanel {{
                background: {DARK_THEME.surface};
                border: 1px solid {DARK_THEME.border};
                border-radius: 12px;
            }}
            """
        )
        series_layout = QVBoxLayout(series_panel)
        series_layout.setContentsMargins(14, 10, 14, 12)
        series_layout.setSpacing(8)
        series_title = QLabel("Create a repeating schedule")
        series_title.setStyleSheet("font-weight: 700;")
        series_controls = QHBoxLayout()
        self.series_start_edit = QDateTimeEdit()
        self.series_start_edit.setDisplayFormat(_DATE_TIME_FORMAT)
        self.series_start_edit.setCalendarPopup(True)
        self.series_start_edit.setMinimumWidth(210)
        self.series_interval_input = QDoubleSpinBox()
        self.series_interval_input.setRange(0.25, 48.0)
        self.series_interval_input.setSingleStep(0.25)
        self.series_interval_input.setValue(2.0)
        self.series_interval_input.setSuffix(" hr")
        self.series_interval_input.setFixedWidth(90)
        self.series_count_input = QSpinBox()
        self.series_count_input.setRange(1, 100)
        self.series_count_input.setValue(4)
        self.series_count_input.setSuffix(" pulses")
        self.series_count_input.setFixedWidth(100)
        generate_button = QPushButton("Replace schedule")
        generate_button.clicked.connect(self._replace_with_series)
        series_controls.addWidget(QLabel("First pulse"))
        series_controls.addWidget(self.series_start_edit, 1)
        series_controls.addWidget(QLabel("every"))
        series_controls.addWidget(self.series_interval_input)
        series_controls.addWidget(QLabel("for"))
        series_controls.addWidget(self.series_count_input)
        series_controls.addWidget(generate_button)
        series_layout.addWidget(series_title)
        series_layout.addLayout(series_controls)

        pulse_header = QHBoxLayout()
        pulse_title = QLabel("Pulse schedule")
        pulse_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        add_button = QPushButton("Add pulse")
        add_button.clicked.connect(self.add_pulse_window)
        pulse_header.addWidget(pulse_title)
        pulse_header.addStretch(1)
        pulse_header.addWidget(add_button)

        self.pulse_list = QWidget()
        self.pulse_list_layout = QVBoxLayout(self.pulse_list)
        self.pulse_list_layout.setContentsMargins(0, 0, 0, 0)
        self.pulse_list_layout.setSpacing(8)
        self.pulse_list_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.pulse_list)
        scroll.setMinimumHeight(150)

        footer = QHBoxLayout()
        self.schedule_status_label = QLabel("")
        self.schedule_status_label.setStyleSheet(f"color: {DARK_THEME.text_muted};")
        continue_button = QPushButton("Start Analysis")
        continue_button.clicked.connect(self._emit_continue)
        footer.addWidget(self.schedule_status_label)
        footer.addStretch(1)
        footer.addWidget(continue_button)

        layout.addLayout(header)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(summary_panel)
        layout.addLayout(search_row)
        layout.addWidget(self.timeline)
        layout.addWidget(series_panel)
        layout.addLayout(pulse_header)
        layout.addWidget(scroll, 1)
        layout.addLayout(footer)

    def set_summary(self, summary: FolderWindowSummary) -> None:
        start_dt = datetime.fromisoformat(summary.start_ts_iso)
        end_dt = max(datetime.fromisoformat(summary.end_ts_iso), start_dt)
        self._range_start_dt = start_dt
        self._range_end_dt = end_dt
        self.summary_label.setText(
            f"{summary.display_name}  •  {len(summary.csv_files)} log files\n"
            f"{self._format_dt(start_dt)}  →  {self._format_dt(end_dt)}"
        )
        self.series_start_edit.setMinimumDateTime(_to_qdatetime(start_dt))
        self.series_start_edit.setMaximumDateTime(_to_qdatetime(end_dt))
        self.series_start_edit.setDateTime(_to_qdatetime(start_dt))
        possible_count = int((end_dt - start_dt) / _DEFAULT_PULSE_SPACING) + 1
        self.series_count_input.setValue(max(1, min(possible_count, 100)))
        self.clear_pulse_windows()
        self.add_pulse_window(pulse_dt=start_dt)

    def add_pulse_window(self, _checked: bool = False, *, pulse_dt: datetime | None = None) -> None:
        if self._range_start_dt is None or self._range_end_dt is None:
            return
        if pulse_dt is None:
            pulse_dt = self._default_next_pulse_time()
        pulse_dt = min(max(pulse_dt, self._range_start_dt), self._range_end_dt)
        row = _PulseTimeRow(
            range_start_dt=self._range_start_dt,
            range_end_dt=self._range_end_dt,
            pulse_dt=pulse_dt,
        )
        row.removed.connect(self._remove_pulse_row)
        row.timeChanged.connect(self._refresh_schedule)
        self.pulse_list_layout.insertWidget(len(self._pulse_rows), row)
        self._pulse_rows.append(row)
        self._refresh_schedule()

    def clear_pulse_windows(self) -> None:
        for row in list(self._pulse_rows):
            self._remove_pulse_row(row, refresh=False)
        self._refresh_schedule()

    def remove_pulse_window_at(self, index: int) -> None:
        if 0 <= index < len(self._pulse_rows):
            self._remove_pulse_row(self._pulse_rows[index])

    def pulse_window_count(self) -> int:
        return len(self._pulse_rows)

    def pulse_windows(self) -> list[dict[str, str]]:
        if self._range_start_dt is None or self._range_end_dt is None:
            return []
        radius = timedelta(minutes=self.search_radius_input.value())
        windows = []
        for row in self._pulse_rows:
            pulse_dt = row.pulse_datetime()
            start_dt = max(pulse_dt - radius, self._range_start_dt)
            end_dt = min(pulse_dt + radius, self._range_end_dt)
            windows.append({"start_iso": start_dt.isoformat(), "end_iso": end_dt.isoformat()})
        windows.sort(key=lambda item: item["start_iso"])
        return windows

    def _replace_with_series(self) -> None:
        if self._range_start_dt is None or self._range_end_dt is None:
            return
        first = self.series_start_edit.dateTime().toPython()
        spacing = timedelta(hours=self.series_interval_input.value())
        count = self.series_count_input.value()
        pulse_times = [first + spacing * index for index in range(count)]
        outside_count = sum(pulse_dt > self._range_end_dt for pulse_dt in pulse_times)
        if outside_count:
            QMessageBox.warning(
                self,
                "Schedule exceeds recording",
                f"{outside_count} pulse time(s) would occur after the recording ends. "
                "Choose fewer pulses, an earlier first pulse, or a shorter interval.",
            )
            return
        self.clear_pulse_windows()
        for pulse_dt in pulse_times:
            self.add_pulse_window(pulse_dt=pulse_dt)

    def _remove_pulse_row(self, row: _PulseTimeRow, *, refresh: bool = True) -> None:
        if row not in self._pulse_rows:
            return
        self._pulse_rows.remove(row)
        self.pulse_list_layout.removeWidget(row)
        row.deleteLater()
        if refresh:
            self._refresh_schedule()

    def _refresh_schedule(self) -> None:
        ordered = sorted(self._pulse_rows, key=lambda row: row.pulse_datetime())
        for index, row in enumerate(ordered, start=1):
            row.set_title(f"Pulse {index}")
        pulse_times = [row.pulse_datetime() for row in ordered]
        if self._range_start_dt is not None and self._range_end_dt is not None:
            self.timeline.set_data(self._range_start_dt, self._range_end_dt, pulse_times)
        radius = self.search_radius_input.value()
        self.schedule_status_label.setText(
            f"{len(pulse_times)} pulse{'s' if len(pulse_times) != 1 else ''}  •  ±{radius} min search window"
        )

    def _default_next_pulse_time(self) -> datetime:
        assert self._range_start_dt is not None
        assert self._range_end_dt is not None
        if not self._pulse_rows:
            return self._range_start_dt
        latest = max(row.pulse_datetime() for row in self._pulse_rows)
        return min(latest + _DEFAULT_PULSE_SPACING, self._range_end_dt)

    def _emit_continue(self) -> None:
        windows = self.pulse_windows()
        if not windows:
            QMessageBox.warning(
                self,
                "Pulse time required",
                "Add at least one expected pulse time before starting analysis.",
            )
            return
        pulse_times = [row.pulse_datetime() for row in self._pulse_rows]
        if len(set(pulse_times)) != len(pulse_times):
            QMessageBox.warning(
                self,
                "Duplicate pulse times",
                "Each expected pulse must have a different scheduled time.",
            )
            return
        start_dt = datetime.fromisoformat(windows[0]["start_iso"])
        end_dt = datetime.fromisoformat(windows[-1]["end_iso"])
        self.continueRequested.emit(start_dt.isoformat(), end_dt.isoformat(), windows)

    @staticmethod
    def _format_dt(value: datetime) -> str:
        return _to_qdatetime(value).toString(_DATE_TIME_FORMAT)
