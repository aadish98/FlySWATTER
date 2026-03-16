"""Folder upload screen for pulse-metrics logs."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.widgets.path_drop_widget import PathDropWidget


class PulseUploadScreen(QWidget):
    backRequested = Signal()
    submitRequested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(50, 50, 50, 50)
        layout.setSpacing(16)

        header = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.backRequested)
        header.addWidget(self.back_button)
        header.addStretch(1)

        title = QLabel("Upload Accelerometer Log Folder")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")

        instructions = QLabel(
            "Drop the original accelerometer log folder here or browse to select it. Folder and log filenames must remain unchanged."
        )
        instructions.setWordWrap(True)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("No folder selected")
        self.path_edit.textChanged.connect(self._sync_submit)
        browse_button = QPushButton("Browse Folder")
        browse_button.clicked.connect(self._browse_folder)

        row = QHBoxLayout()
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse_button)

        self.drop_widget = PathDropWidget("Drop the accelerometer folder here", expect_directory=True)
        self.drop_widget.pathDropped.connect(self.path_edit.setText)

        buttons = QHBoxLayout()
        self.submit_button = QPushButton("Continue")
        self.submit_button.setEnabled(False)
        self.submit_button.clicked.connect(self._emit_submit)
        buttons.addStretch(1)
        buttons.addWidget(self.submit_button)

        layout.addLayout(header)
        layout.addWidget(title)
        layout.addWidget(instructions)
        layout.addLayout(row)
        layout.addWidget(self.drop_widget, alignment=Qt.AlignHCenter)
        layout.addStretch(1)
        layout.addLayout(buttons)

    def set_selected_path(self, path: str = "") -> None:
        self.path_edit.setText(path)

    def _browse_folder(self) -> None:
        folder_path = QFileDialog.getExistingDirectory(self, "Select Accelerometer Folder")
        if folder_path:
            self.path_edit.setText(folder_path)

    def _sync_submit(self) -> None:
        self.submit_button.setEnabled(bool(self.path_edit.text().strip()))

    def _emit_submit(self) -> None:
        self.submitRequested.emit(self.path_edit.text().strip())
