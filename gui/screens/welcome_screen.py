"""Welcome screen with researcher selection."""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpacerItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class WelcomeScreen(QWidget):
    continueRequested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(60, 60, 60, 60)
        layout.setSpacing(20)

        title = QLabel(
            "FlySWATTER: Fly Sleep-Wake Arousal Threshold Testing Evaluation Resource.",
            alignment=Qt.AlignCenter,
        )
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 24px; font-weight: 700;")

        subtitle = QLabel("Researcher Name (used to backup results):")
        subtitle.setStyleSheet("font-size: 15px; font-weight: 600;")

        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.NoInsert)
        self.combo.lineEdit().setPlaceholderText("Select an existing name or type a new one")
        self.combo.lineEdit().textChanged.connect(self._sync_button_state)

        self.continue_button = QPushButton("Continue")
        self.continue_button.setEnabled(False)
        self.continue_button.setMinimumWidth(180)
        self.continue_button.clicked.connect(self._emit_continue)

        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))
        layout.addWidget(title)
        layout.addSpacing(12)
        layout.addWidget(subtitle)
        layout.addWidget(self.combo)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.continue_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def set_researcher_names(self, names: Iterable[str], selected_name: str = "") -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        for name in names:
            self.combo.addItem(name)
        self.combo.setCurrentText(selected_name)
        self.combo.blockSignals(False)
        self._sync_button_state()

    def _sync_button_state(self) -> None:
        self.continue_button.setEnabled(bool(self.combo.currentText().strip()))

    def _emit_continue(self) -> None:
        self.continueRequested.emit(self.combo.currentText().strip())
