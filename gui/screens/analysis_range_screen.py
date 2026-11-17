"""Analysis-window editor for general pulse metrics.

Unlike the scheduled flow, nothing here says when a pulse should happen. The
user marks one or more stretches of the recording, and every pulse inside them
is found and summarized. Windows can be added and removed freely, and the
recording's start time can be corrected for a drifted logger clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.theme import DARK_THEME
from gui.widgets.range_slider_widget import RangeSliderWidget
from services.models import AnalysisWindow, FolderWindowSummary

_DATE_TIME_FORMAT = "MMM d, yyyy  h:mm AP"
_CLOCK_FORMAT = "MMM d, yyyy  h:mm:ss AP"
_DEFAULT_WINDOW_LENGTH = timedelta(hours=1)
_ONE_MINUTE = timedelta(minutes=1)


def _to_qdatetime(value: datetime) -> QDateTime:
    return QDateTime(value)


def _format_dt(value: datetime) -> str:
    return _to_qdatetime(value).toString(_DATE_TIME_FORMAT)


def _format_length(delta: timedelta) -> str:
    total_minutes = max(int(round(delta.total_seconds() / 60.0)), 0)
    hours, minutes = divmod(total_minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


class _AnalysisWindowRow(QFrame):
    """One editable analysis window: label, slider, and start/end fields."""

    removed = Signal(object)
    changed = Signal()

    def __init__(
        self,
        *,
        bounds_start: datetime,
        bounds_end: datetime,
        start: datetime,
        end: datetime,
        label: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._bounds_start = bounds_start
        self._bounds_end = bounds_end
        self._syncing = False
        self.setObjectName("analysisWindowRow")
        self._apply_style(conflict=False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.title_label = QLabel("Window")
        self.title_label.setMinimumWidth(78)
        self.title_label.setStyleSheet("font-weight: 700;")
        self.label_edit = QLineEdit(label)
        self.label_edit.setPlaceholderText("Label (optional)")
        self.label_edit.setMaximumWidth(220)
        self.label_edit.textChanged.connect(lambda _text: self.changed.emit())
        self.duration_label = QLabel("")
        self.duration_label.setStyleSheet(f"color: {DARK_THEME.text_secondary};")
        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip("Remove this analysis window")
        self.remove_button.clicked.connect(lambda: self.removed.emit(self))
        top.addWidget(self.title_label)
        top.addWidget(self.label_edit)
        top.addStretch(1)
        top.addWidget(self.duration_label)
        top.addWidget(self.remove_button)

        self.range_slider = RangeSliderWidget()
        self.range_slider.rangeChanged.connect(self._handle_slider_changed)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.start_edit = self._make_edit()
        self.end_edit = self._make_edit()
        bottom.addWidget(QLabel("Start"))
        bottom.addWidget(self.start_edit, 1)
        bottom.addWidget(QLabel("End"))
        bottom.addWidget(self.end_edit, 1)

        layout.addLayout(top)
        layout.addWidget(self.range_slider)
        layout.addLayout(bottom)

        self.set_bounds(bounds_start, bounds_end, start=start, end=end)

    def _make_edit(self) -> QDateTimeEdit:
        edit = QDateTimeEdit()
        edit.setDisplayFormat(_DATE_TIME_FORMAT)
        edit.setCalendarPopup(True)
        edit.setMinimumWidth(210)
        edit.dateTimeChanged.connect(self._handle_edit_changed)
        return edit

    def _apply_style(self, *, conflict: bool) -> None:
        border = DARK_THEME.error if conflict else DARK_THEME.border
        self.setStyleSheet(
            f"""
            QFrame#analysisWindowRow {{
                background: {DARK_THEME.surface_alt};
                border: 1px solid {border};
                border-radius: 10px;
            }}
            """
        )

    # -- values ---------------------------------------------------------

    def window_range(self) -> tuple[datetime, datetime]:
        return self.start_edit.dateTime().toPython(), self.end_edit.dateTime().toPython()

    def label_text(self) -> str:
        return self.label_edit.text().strip()

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)
        self.label_edit.setPlaceholderText(text)

    def set_removable(self, removable: bool) -> None:
        self.remove_button.setEnabled(removable)

    def set_conflict(self, conflict: bool) -> None:
        self._apply_style(conflict=conflict)

    def set_bounds(self, bounds_start: datetime, bounds_end: datetime, *, start: datetime, end: datetime) -> None:
        """Re-fit the row to a recording range, then place the window in it."""
        self._bounds_start = bounds_start
        self._bounds_end = max(bounds_end, bounds_start)
        total_minutes = max(int(round((self._bounds_end - self._bounds_start).total_seconds() / 60.0)), 1)
        self._syncing = True
        try:
            for edit in (self.start_edit, self.end_edit):
                edit.setMinimumDateTime(_to_qdatetime(self._bounds_start))
                edit.setMaximumDateTime(_to_qdatetime(self._bounds_end))
            self.range_slider.set_bounds(0, total_minutes)
        finally:
            self._syncing = False
        self.set_range(start, end, emit=False)

    def set_range(self, start: datetime, end: datetime, *, emit: bool = True) -> None:
        self._syncing = True
        try:
            self.start_edit.setDateTime(_to_qdatetime(start))
            self.end_edit.setDateTime(_to_qdatetime(end))
            if end > start:
                self.range_slider.set_range(self._to_step(start), self._to_step(end), emit=False)
        finally:
            self._syncing = False
        self._refresh_duration()
        if emit:
            self.changed.emit()

    # -- slider / edit sync ----------------------------------------------

    def _to_step(self, value: datetime) -> int:
        return int(round((value - self._bounds_start).total_seconds() / 60.0))

    def _from_step(self, step: int) -> datetime:
        return min(self._bounds_start + timedelta(minutes=int(step)), self._bounds_end)

    def _handle_slider_changed(self, lower: int, upper: int) -> None:
        if self._syncing:
            return
        self.set_range(self._from_step(lower), self._from_step(upper))

    def _handle_edit_changed(self, _value) -> None:
        if self._syncing:
            return
        start, end = self.window_range()
        if end > start:
            self._syncing = True
            try:
                self.range_slider.set_range(self._to_step(start), self._to_step(end), emit=False)
            finally:
                self._syncing = False
        self._refresh_duration()
        self.changed.emit()

    def _refresh_duration(self) -> None:
        start, end = self.window_range()
        if end > start:
            self.duration_label.setText(_format_length(end - start))
        else:
            self.duration_label.setText("end before start")


class _WindowTimeline(QWidget):
    """Read-only overview of every analysis window across the recording."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._start: datetime | None = None
        self._end: datetime | None = None
        self._items: list[tuple[datetime, datetime, str, bool]] = []
        self.setMinimumHeight(78)

    def set_data(
        self, start: datetime, end: datetime, items: list[tuple[datetime, datetime, str, bool]]
    ) -> None:
        self._start = start
        self._end = end
        self._items = items
        self.update()

    def paintEvent(self, _event) -> None:
        if self._start is None or self._end is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        left, right = 18.0, float(max(self.width() - 18, 19))
        line_y = 30.0
        span = max((self._end - self._start).total_seconds(), 1.0)

        painter.setPen(QPen(QColor(DARK_THEME.border), 4, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(left, line_y), QPointF(right, line_y))

        font = QFont(painter.font())
        font.setPointSize(9)
        painter.setFont(font)
        for start, end, text, invalid in self._items:
            x0 = left + min(max((start - self._start).total_seconds() / span, 0.0), 1.0) * (right - left)
            x1 = left + min(max((end - self._start).total_seconds() / span, 0.0), 1.0) * (right - left)
            color = QColor(DARK_THEME.error if invalid else DARK_THEME.accent)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x0, line_y - 8, max(x1 - x0, 3.0), 16), 4, 4)
            painter.setPen(QColor(DARK_THEME.text_secondary))
            painter.drawText(QRectF(x0 - 20, line_y + 12, max(x1 - x0, 3.0) + 40, 18), Qt.AlignHCenter, text)

        painter.setPen(QColor(DARK_THEME.text_muted))
        painter.drawText(0, 56, self.width() // 2, 20, Qt.AlignLeft, _format_dt(self._start))
        painter.drawText(self.width() // 2, 56, self.width() // 2, 20, Qt.AlignRight, _format_dt(self._end))


class AnalysisRangeScreen(QWidget):
    backRequested = Signal()
    # (windows as dicts with start_iso / end_iso / label, clock offset in seconds)
    continueRequested = Signal(list, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._manifest_start: datetime | None = None
        self._manifest_end: datetime | None = None
        self._window_rows: list[_AnalysisWindowRow] = []
        self._valid = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(50, 30, 50, 30)
        layout.setSpacing(12)

        header = QHBoxLayout()
        back_button = QPushButton("Back")
        back_button.clicked.connect(self.backRequested)
        header.addWidget(back_button)
        header.addStretch(1)

        title = QLabel("Choose Analysis Windows")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        subtitle = QLabel(
            "Every pulse inside each window is found and summarized. Add a window for each stretch you "
            "want analyzed separately, such as a stimulation block and a baseline."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {DARK_THEME.text_secondary};")

        summary_panel = self._make_panel("recordingSummary")
        summary_layout = QVBoxLayout(summary_panel)
        summary_layout.setContentsMargins(14, 10, 14, 10)
        self.summary_label = QLabel("No recording selected")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(f"font-weight: 600; color: {DARK_THEME.text_primary};")
        summary_layout.addWidget(self.summary_label)

        clock_panel = self._make_panel("clockPanel")
        clock_layout = QVBoxLayout(clock_panel)
        clock_layout.setContentsMargins(14, 10, 14, 10)
        clock_layout.setSpacing(6)
        clock_title = QLabel("Recording start (clock correction)")
        clock_title.setStyleSheet("font-weight: 700;")
        clock_row = QHBoxLayout()
        self.clock_edit = QDateTimeEdit()
        self.clock_edit.setDisplayFormat(_CLOCK_FORMAT)
        self.clock_edit.setCalendarPopup(True)
        self.clock_edit.setMinimumWidth(250)
        self.clock_edit.dateTimeChanged.connect(self._handle_clock_changed)
        reset_button = QPushButton("Reset")
        reset_button.setToolTip("Use the start time recorded by the logger")
        reset_button.clicked.connect(self._reset_clock)
        self.clock_note_label = QLabel("")
        self.clock_note_label.setStyleSheet(f"color: {DARK_THEME.text_secondary};")
        clock_row.addWidget(self.clock_edit)
        clock_row.addWidget(reset_button)
        clock_row.addWidget(self.clock_note_label, 1)
        clock_hint = QLabel(
            "If the logger's clock was off, enter the true time the recording started. Window times and "
            "all results are then shown on the corrected clock."
        )
        clock_hint.setWordWrap(True)
        clock_hint.setStyleSheet(f"color: {DARK_THEME.text_muted};")
        clock_layout.addWidget(clock_title)
        clock_layout.addLayout(clock_row)
        clock_layout.addWidget(clock_hint)

        self.timeline = _WindowTimeline()

        list_header = QHBoxLayout()
        list_title = QLabel("Analysis windows")
        list_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        whole_button = QPushButton("Whole recording")
        whole_button.setToolTip("Replace all windows with one covering the entire recording")
        whole_button.clicked.connect(self.use_whole_recording)
        add_button = QPushButton("Add window")
        add_button.clicked.connect(self.add_window)
        list_header.addWidget(list_title)
        list_header.addStretch(1)
        list_header.addWidget(whole_button)
        list_header.addWidget(add_button)

        self.window_list = QWidget()
        self.window_list_layout = QVBoxLayout(self.window_list)
        self.window_list_layout.setContentsMargins(0, 0, 0, 0)
        self.window_list_layout.setSpacing(8)
        self.window_list_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.window_list)
        scroll.setMinimumHeight(170)

        footer = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.start_button = QPushButton("Start Analysis")
        self.start_button.clicked.connect(self._emit_continue)
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.start_button)

        layout.addLayout(header)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(summary_panel)
        layout.addWidget(clock_panel)
        layout.addWidget(self.timeline)
        layout.addLayout(list_header)
        layout.addWidget(scroll, 1)
        layout.addLayout(footer)

    @staticmethod
    def _make_panel(name: str) -> QFrame:
        panel = QFrame()
        panel.setObjectName(name)
        panel.setStyleSheet(
            f"""
            QFrame#{name} {{
                background: {DARK_THEME.surface};
                border: 1px solid {DARK_THEME.border};
                border-radius: 12px;
            }}
            """
        )
        return panel

    # -- public API -------------------------------------------------------

    def set_summary(self, summary: FolderWindowSummary) -> None:
        """Load a recording; starts with one window covering all of it."""
        self._manifest_start = datetime.fromisoformat(summary.start_ts_iso)
        self._manifest_end = max(datetime.fromisoformat(summary.end_ts_iso), self._manifest_start)
        self.summary_label.setText(
            f"{summary.display_name}  \u2022  {len(summary.csv_files)} log files\n"
            f"Logger clock: {_format_dt(self._manifest_start)}  \u2192  {_format_dt(self._manifest_end)}"
        )
        self._set_clock(self._manifest_start)
        self.clear_windows()
        self.add_window()

    def set_windows(self, windows, clock_offset_seconds: float = 0.0) -> None:
        """Restore earlier windows (corrected clock), e.g. when editing after a run."""
        if self._manifest_start is None:
            return
        self._set_clock(self._manifest_start + timedelta(seconds=float(clock_offset_seconds)))
        self.clear_windows()
        for item in windows or []:
            if isinstance(item, AnalysisWindow):
                start_iso, end_iso, label = item.start_iso, item.end_iso, item.label
            else:
                start_iso, end_iso, label = item["start_iso"], item["end_iso"], item.get("label", "")
            self.add_window(
                start=datetime.fromisoformat(start_iso), end=datetime.fromisoformat(end_iso), label=label
            )
        if not self._window_rows:
            self.add_window()

    def window_count(self) -> int:
        return len(self._window_rows)

    def clock_offset_seconds(self) -> float:
        if self._manifest_start is None:
            return 0.0
        corrected = self.clock_edit.dateTime().toPython()
        return float((corrected - self._manifest_start).total_seconds())

    def analysis_windows(self) -> list[dict[str, str]]:
        windows = []
        for row in self._window_rows:
            start, end = row.window_range()
            windows.append({"start_iso": start.isoformat(), "end_iso": end.isoformat(), "label": row.label_text()})
        windows.sort(key=lambda item: item["start_iso"])
        return windows

    def add_window(
        self,
        _checked: bool = False,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        label: str = "",
    ) -> None:
        bounds = self._bounds()
        if bounds is None:
            return
        bounds_start, bounds_end = bounds
        if start is None or end is None:
            if not self._window_rows:
                start, end = bounds_start, bounds_end
            else:
                latest_end = max(row.window_range()[1] for row in self._window_rows)
                start = latest_end
                end = start + _DEFAULT_WINDOW_LENGTH
                if end > bounds_end:
                    end = bounds_end
                    start = max(bounds_end - _DEFAULT_WINDOW_LENGTH, bounds_start)
        start, end = self._clamp(start, end, bounds_start, bounds_end)
        row = _AnalysisWindowRow(
            bounds_start=bounds_start,
            bounds_end=bounds_end,
            start=start,
            end=end,
            label=label,
        )
        row.removed.connect(self._remove_row)
        row.changed.connect(self._refresh)
        self.window_list_layout.insertWidget(len(self._window_rows), row)
        self._window_rows.append(row)
        self._refresh()

    def remove_window_at(self, index: int) -> None:
        if 0 <= index < len(self._window_rows):
            self._remove_row(self._window_rows[index])

    def clear_windows(self) -> None:
        for row in list(self._window_rows):
            self._detach_row(row)
        self._refresh()

    def use_whole_recording(self) -> None:
        bounds = self._bounds()
        if bounds is None:
            return
        self.clear_windows()
        self.add_window(start=bounds[0], end=bounds[1])

    # -- internals --------------------------------------------------------

    def _bounds(self) -> tuple[datetime, datetime] | None:
        if self._manifest_start is None or self._manifest_end is None:
            return None
        offset = timedelta(seconds=self.clock_offset_seconds())
        return self._manifest_start + offset, self._manifest_end + offset

    @staticmethod
    def _clamp(start: datetime, end: datetime, low: datetime, high: datetime) -> tuple[datetime, datetime]:
        start = min(max(start, low), high)
        end = min(max(end, low), high)
        if end <= start:
            end = min(start + _ONE_MINUTE, high)
            if end <= start:
                start = max(end - _ONE_MINUTE, low)
        return start, end

    def _set_clock(self, value: datetime) -> None:
        self.clock_edit.blockSignals(True)
        self.clock_edit.setDateTime(_to_qdatetime(value))
        self.clock_edit.blockSignals(False)
        self._update_clock_note()

    def _reset_clock(self) -> None:
        if self._manifest_start is not None:
            self.clock_edit.setDateTime(_to_qdatetime(self._manifest_start))

    def _update_clock_note(self) -> None:
        offset = self.clock_offset_seconds()
        if abs(offset) < 0.5:
            self.clock_note_label.setText("No correction")
            return
        sign = "+" if offset > 0 else "\u2212"
        self.clock_note_label.setText(f"Correction {sign}{_format_length(timedelta(seconds=abs(offset)))}")

    def _handle_clock_changed(self, _value) -> None:
        """Re-fit every window to the corrected recording range."""
        self._update_clock_note()
        bounds = self._bounds()
        if bounds is None:
            return
        adjusted = 0
        for row in self._window_rows:
            start, end = row.window_range()
            new_start, new_end = self._clamp(start, end, bounds[0], bounds[1])
            if (new_start, new_end) != (start, end):
                adjusted += 1
            row.set_bounds(bounds[0], bounds[1], start=new_start, end=new_end)
        self._refresh()
        if adjusted:
            self.status_label.setText(
                f"{adjusted} window{'s' if adjusted != 1 else ''} adjusted to fit the corrected recording. "
                + self.status_label.text()
            )

    def _remove_row(self, row: _AnalysisWindowRow, *, refresh: bool = True) -> None:
        if len(self._window_rows) <= 1 and refresh:
            return
        self._detach_row(row)
        if refresh:
            self._refresh()

    def _detach_row(self, row: _AnalysisWindowRow) -> None:
        if row not in self._window_rows:
            return
        self._window_rows.remove(row)
        self.window_list_layout.removeWidget(row)
        row.deleteLater()

    def _refresh(self) -> None:
        ordered = sorted(self._window_rows, key=lambda row: row.window_range())
        for index, row in enumerate(ordered, start=1):
            row.set_title(f"Window {index}")
            row.set_removable(len(self._window_rows) > 1)
        titles = {id(row): f"Window {index}" for index, row in enumerate(ordered, start=1)}

        problems: list[str] = []
        flagged: set[int] = set()
        for row in ordered:
            start, end = row.window_range()
            if end <= start:
                problems.append(f"{titles[id(row)]} ends before it starts.")
                flagged.add(id(row))
        valid_rows = [row for row in ordered if row.window_range()[1] > row.window_range()[0]]
        for previous, current in zip(valid_rows, valid_rows[1:]):
            if current.window_range()[0] < previous.window_range()[1]:
                problems.append(f"{titles[id(previous)]} and {titles[id(current)]} overlap.")
                flagged.update({id(previous), id(current)})
        for row in self._window_rows:
            row.set_conflict(id(row) in flagged)

        self._valid = bool(self._window_rows) and not problems
        self.start_button.setEnabled(self._valid)
        count = len(self._window_rows)
        if problems:
            self.status_label.setText(" ".join(problems))
            self.status_label.setStyleSheet(f"color: {DARK_THEME.error};")
        else:
            self.status_label.setText(f"{count} window{'s' if count != 1 else ''}")
            self.status_label.setStyleSheet(f"color: {DARK_THEME.text_muted};")

        bounds = self._bounds()
        if bounds is not None:
            self.timeline.set_data(
                bounds[0],
                bounds[1],
                [
                    (*row.window_range(), titles[id(row)], id(row) in flagged)
                    for row in ordered
                ],
            )

    def _emit_continue(self) -> None:
        if not self._valid:
            return
        self.continueRequested.emit(self.analysis_windows(), self.clock_offset_seconds())
