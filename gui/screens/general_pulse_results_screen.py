"""Results screen for general pulse metrics."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.theme import DARK_THEME
from gui.widgets.image_preview_card import ImagePreviewCard
from services.models import GeneralPulseAnalysisResult

_CLOCK_FORMAT = "MMM d, yyyy  h:mm:ss AP"
_TABLE_STATS = ("n", "Mean", "SD", "Median")


def _format_value(stat: str, value: float) -> str:
    if value is None or not np.isfinite(value):
        return "\u2013"
    if stat == "n":
        return f"{int(value)}"
    return f"{value:.4g}"


class GeneralPulseResultsScreen(QWidget):
    downloadRequested = Signal(str)
    restartRequested = Signal()
    editWindowsRequested = Signal()
    rerenderRequested = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._manifest_start: datetime | None = None
        self._result: GeneralPulseAnalysisResult | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(12)

        title = QLabel("General Pulse Metrics Results")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)

        self.stats_table = QTableWidget()
        self.stats_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stats_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setMaximumHeight(300)
        self.stats_table.setMinimumHeight(190)

        self.tabs = QTabWidget()
        self.aggregated_card = ImagePreviewCard("Aggregated Pulse Plot", None)
        self.distributions_card = ImagePreviewCard("Pulse Metric Distributions", None)
        self.aggregated_card.downloadRequested.connect(self.downloadRequested)
        self.distributions_card.downloadRequested.connect(self.downloadRequested)
        self.tabs.addTab(self.aggregated_card, "Aggregated plot")
        self.tabs.addTab(self.distributions_card, "Distributions")

        clock_row = QHBoxLayout()
        clock_label = QLabel("Recording start")
        clock_label.setStyleSheet("font-weight: 700;")
        self.clock_edit = QDateTimeEdit()
        self.clock_edit.setDisplayFormat(_CLOCK_FORMAT)
        self.clock_edit.setCalendarPopup(True)
        self.clock_edit.setMinimumWidth(250)
        self.apply_button = QPushButton("Apply and re-render")
        self.apply_button.setToolTip("Relabel every time with this recording start; no logs are re-read")
        self.apply_button.clicked.connect(self._emit_rerender)
        self.clock_status_label = QLabel("")
        self.clock_status_label.setStyleSheet(f"color: {DARK_THEME.text_secondary};")
        clock_row.addWidget(clock_label)
        clock_row.addWidget(self.clock_edit)
        clock_row.addWidget(self.apply_button)
        clock_row.addWidget(self.clock_status_label, 1)

        self.note_label = QLabel("Plots, workbook and zip are saved together in the results folder.")
        self.note_label.setStyleSheet(f"font-size: 12px; color: {DARK_THEME.text_secondary};")

        buttons = QHBoxLayout()
        self.download_zip_button = QPushButton("Download Pulse Metrics")
        self.edit_windows_button = QPushButton("Edit windows")
        self.edit_windows_button.setToolTip("Change the analysis windows and run again")
        self.restart_button = QPushButton("Restart")
        self.download_zip_button.clicked.connect(lambda: self.downloadRequested.emit("general_zip"))
        self.edit_windows_button.clicked.connect(self.editWindowsRequested)
        self.restart_button.clicked.connect(self.restartRequested)
        buttons.addWidget(self.download_zip_button)
        buttons.addStretch(1)
        buttons.addWidget(self.edit_windows_button)
        buttons.addWidget(self.restart_button)

        layout.addWidget(title)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.stats_table)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(clock_row)
        layout.addWidget(self.note_label)
        layout.addLayout(buttons)

    def set_result(self, result: GeneralPulseAnalysisResult) -> None:
        self._result = result
        self._manifest_start = datetime.fromisoformat(result.manifest_start_iso)
        window_count = len(result.windows)
        self.summary_label.setText(
            f"Saved results to: {result.output_dir}\n"
            f"Files processed: {len(result.processed_files)}\n"
            f"Pulses detected: {result.total_pulses} across {window_count} "
            f"window{'s' if window_count != 1 else ''}\n"
            f"Analyzed: {result.analyzed_window_label}"
        )
        self._fill_table(result)
        self._replace_card("aggregated_card", 0, "Aggregated Pulse Plot", result.aggregated_plot)
        self._replace_card("distributions_card", 1, "Pulse Metric Distributions", result.distributions_plot)
        self.set_clock_offset(result.clock_offset_seconds)
        self.set_busy(False)

    def set_clock_offset(self, offset_seconds: float) -> None:
        if self._manifest_start is None:
            return
        corrected = self._manifest_start + timedelta(seconds=float(offset_seconds))
        self.clock_edit.setDateTime(QDateTime(corrected))
        if abs(offset_seconds) < 0.5:
            self.clock_status_label.setText("Using the logger's recorded start")
        else:
            self.clock_status_label.setText(f"Corrected by {offset_seconds:+.0f} s")

    def clock_offset_seconds(self) -> float:
        if self._manifest_start is None:
            return 0.0
        return float((self.clock_edit.dateTime().toPython() - self._manifest_start).total_seconds())

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.apply_button.setEnabled(not busy)
        self.edit_windows_button.setEnabled(not busy)
        self.download_zip_button.setEnabled(not busy)
        if busy and message:
            self.clock_status_label.setText(message)

    def _emit_rerender(self) -> None:
        self.rerenderRequested.emit(self.clock_offset_seconds())

    def _replace_card(self, attribute: str, index: int, title: str, path) -> None:
        old_card = getattr(self, attribute)
        card = ImagePreviewCard(title, path)
        card.downloadRequested.connect(self.downloadRequested)
        current = self.tabs.currentIndex()
        label = self.tabs.tabText(index)
        self.tabs.removeTab(index)
        old_card.deleteLater()
        self.tabs.insertTab(index, card, label)
        self.tabs.setCurrentIndex(current if current >= 0 else 0)
        setattr(self, attribute, card)

    def _fill_table(self, result: GeneralPulseAnalysisResult) -> None:
        blocks = list(result.summaries)
        metrics = list(next(iter(result.summaries.values())).keys()) if blocks else []
        headers = ["Metric"]
        for block in blocks:
            headers.extend(f"{block}\n{stat}" for stat in _TABLE_STATS)
        self.stats_table.clear()
        self.stats_table.setColumnCount(len(headers))
        self.stats_table.setRowCount(len(metrics))
        self.stats_table.setHorizontalHeaderLabels(headers)
        for row, metric in enumerate(metrics):
            self.stats_table.setItem(row, 0, QTableWidgetItem(metric))
            for block_index, block in enumerate(blocks):
                for stat_index, stat in enumerate(_TABLE_STATS):
                    value = result.summaries[block][metric][stat]
                    item = QTableWidgetItem(_format_value(stat, value))
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.stats_table.setItem(row, 1 + block_index * len(_TABLE_STATS) + stat_index, item)
        self.stats_table.resizeColumnsToContents()
        self.stats_table.resizeRowsToContents()
