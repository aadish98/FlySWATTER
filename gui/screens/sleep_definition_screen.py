"""Screen for configuring inactivity threshold used to define sleep."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget


class SleepDefinitionScreen(QWidget):
    backRequested = Signal()
    confirmRequested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(50, 50, 50, 50)
        layout.setSpacing(18)

        header = QHBoxLayout()
        back_button = QPushButton("Back")
        back_button.clicked.connect(self.backRequested)
        header.addWidget(back_button)
        header.addStretch(1)

        title = QLabel("Define Sleep Threshold")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")

        row = QHBoxLayout()
        row.setSpacing(10)
        prompt = QLabel("Define sleep as")
        prompt.setStyleSheet("font-size: 16px;")
        self.minutes_input = QLineEdit()
        self.minutes_input.setValidator(QIntValidator(1, 120))
        self.minutes_input.setText("5")
        self.minutes_input.setAlignment(Qt.AlignCenter)
        self.minutes_input.setFixedWidth(70)
        units = QLabel("mins")
        units.setStyleSheet("font-size: 16px;")
        tail = QLabel("of inactivity")
        tail.setStyleSheet("font-size: 16px;")
        row.addStretch(1)
        row.addWidget(prompt)
        row.addWidget(self.minutes_input)
        row.addWidget(units)
        row.addWidget(tail)
        row.addStretch(1)

        buttons = QHBoxLayout()
        confirm_button = QPushButton("Confirm")
        confirm_button.clicked.connect(self._emit_confirm)
        buttons.addStretch(1)
        buttons.addWidget(confirm_button)

        layout.addLayout(header)
        layout.addWidget(title)
        layout.addLayout(row)
        layout.addStretch(1)
        layout.addLayout(buttons)

    def set_minutes(self, minutes: int) -> None:
        self.minutes_input.setText(str(max(1, min(int(minutes), 120))))

    def _emit_confirm(self) -> None:
        text = self.minutes_input.text().strip()
        if not text:
            QMessageBox.warning(self, "Input Required", "Please enter the number of minutes.")
            return
        self.confirmRequested.emit(int(text))
