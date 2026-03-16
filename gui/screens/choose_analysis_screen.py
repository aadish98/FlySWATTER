"""Choose Analysis screen."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gui.widgets.analysis_card import AnalysisCard


class ChooseAnalysisScreen(QWidget):
    backRequested = Signal()
    scoreRequested = Signal()
    pulseRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(50, 50, 50, 50)
        layout.setSpacing(24)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        back_button = QPushButton("Back")
        back_button.clicked.connect(self.backRequested)
        header.addWidget(back_button)
        header.addStretch(1)

        title = QLabel("Choose Analysis", alignment=Qt.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: 700;")

        cards_row = QHBoxLayout()
        cards_row.setSpacing(28)

        self.score_card = AnalysisCard("Score Zantiks Behavior Data", "score")
        self.pulse_card = AnalysisCard("Compute Pulse Metrics", "pulse")
        self.score_card.clicked.connect(self.scoreRequested)
        self.pulse_card.clicked.connect(self.pulseRequested)

        cards_row.addStretch(1)
        cards_row.addWidget(self.score_card)
        cards_row.addWidget(self.pulse_card)
        cards_row.addStretch(1)

        layout.addLayout(header)
        layout.addWidget(title)
        layout.addLayout(cards_row)
        layout.addStretch(1)
