"""Results screen for pulse metrics outputs."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gui.theme import DARK_THEME
from gui.widgets.image_preview_card import ImagePreviewCard
from services.models import PulseAnalysisResult


class PulseResultsScreen(QWidget):
    downloadRequested = Signal(str)
    restartRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)

        title = QLabel("Pulse Metrics Results")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.preview_card = ImagePreviewCard("Aggregated Pulse Metrics Plot", None)
        self.preview_card.downloadRequested.connect(self.downloadRequested)
        self.download_note_label = QLabel("Above plot is included in the download.")
        self.download_note_label.setStyleSheet(
            f"font-size: 12px; color: {DARK_THEME.text_secondary};"
        )

        buttons = QHBoxLayout()
        self.download_zip_button = QPushButton("Download Pulse Metrics")
        self.restart_button = QPushButton("Restart")
        self.download_zip_button.clicked.connect(lambda: self.downloadRequested.emit("pulse_zip"))
        self.restart_button.clicked.connect(self.restartRequested)
        buttons.addWidget(self.download_zip_button)
        buttons.addStretch(1)
        buttons.addWidget(self.restart_button)

        layout.addWidget(title)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.preview_card, 1)
        layout.addWidget(self.download_note_label)
        layout.addLayout(buttons)

    def set_result(self, result: PulseAnalysisResult) -> None:
        self.summary_label.setText(
            f"Saved results to: {result.output_dir}\nFiles processed: {len(result.processed_files)}\nPulses detected: {result.total_pulses}\nAnalyzed window: {result.analyzed_window_label}"
        )
        self.layout().removeWidget(self.preview_card)
        self.preview_card.deleteLater()
        self.preview_card = ImagePreviewCard("Aggregated Pulse Metrics Plot", result.aggregated_plot)
        self.preview_card.downloadRequested.connect(self.downloadRequested)
        self.layout().insertWidget(2, self.preview_card, 1)
