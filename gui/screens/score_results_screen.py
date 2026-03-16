"""Results screen for behavior scoring outputs."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.theme import DARK_THEME
from gui.widgets.image_preview_card import ImagePreviewCard
from services.models import ScoreAnalysisResult


class ScoreResultsScreen(QWidget):
    downloadRequested = Signal(str)
    computePulseRequested = Signal()
    restartRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)

        title = QLabel("Scored Fly Behaviour Data")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.RichText)
        self.summary_label.setStyleSheet("font-size: 13px;")

        self.tab_widget = QTabWidget()

        self.download_note_label = QLabel("All tabbed plots above are included in the downloads.")
        self.download_note_label.setStyleSheet(
            f"font-size: 12px; color: {DARK_THEME.text_secondary};"
        )

        buttons = QHBoxLayout()
        self.download_sleep_button = QPushButton("Download Sleep Data")
        self.download_arousal_button = QPushButton("Download Arousal Data")
        self.compute_pulse_button = QPushButton("Compute Pulse Metrics")
        self.restart_button = QPushButton("Restart")
        self.compute_pulse_button.setDefault(True)

        self.download_sleep_button.clicked.connect(lambda: self.downloadRequested.emit("sleep_zip"))
        self.download_arousal_button.clicked.connect(lambda: self.downloadRequested.emit("arousal_zip"))
        self.compute_pulse_button.clicked.connect(self.computePulseRequested)
        self.restart_button.clicked.connect(self.restartRequested)

        buttons.addWidget(self.download_sleep_button)
        buttons.addWidget(self.download_arousal_button)
        buttons.addStretch(1)
        buttons.addWidget(self.compute_pulse_button)
        buttons.addWidget(self.restart_button)

        layout.addWidget(title)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.tab_widget, 1)
        layout.addWidget(self.download_note_label)
        layout.addLayout(buttons)

    def set_result(self, result: ScoreAnalysisResult) -> None:
        counts_html = "".join(
            f"<li>{escape(name)}: {count} flies</li>"
            for name, count in result.genotype_counts.items()
        )
        self.summary_label.setText(
            f"<p><b>Output folder:</b> {escape(str(result.output_dir))}</p>"
            f"<p><b>Genotype fly counts:</b></p>"
            f"<ul>{counts_html}</ul>"
        )

        self._clear_tabs()
        previews = [
            (
                "Protocol Plot",
                "Temp / Light / Wall Protocol Plot",
                result.protocol_plot,
                "Shows experiment temperature trace with light/dark periods and pulse timings.",
            ),
            (
                "Arousal Bar Plot",
                "Aggregated Arousal Bar Plot",
                result.arousal_plot_paths[0] if result.arousal_plot_paths else None,
                "Compares percent awoken across genotypes for each delivered pulse.",
            ),
            (
                "Sleep Profile Plot",
                "Sleep Profile - All Genotypes",
                result.sleep_profile_plot,
                "Tracks mean sleep per genotype across time over the full run.",
            ),
        ]
        for tab_label, card_title, path, note in previews:
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.setSpacing(0)

            card = ImagePreviewCard(card_title, path, description=note)
            card.downloadRequested.connect(self.downloadRequested)
            tab_layout.addWidget(card, 1)
            self.tab_widget.addTab(tab, tab_label)

    def _clear_tabs(self) -> None:
        while self.tab_widget.count():
            widget = self.tab_widget.widget(0)
            self.tab_widget.removeTab(0)
            if widget is not None:
                widget.deleteLater()
