"""File upload screen for Zantiks behavior data."""

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

from gui.theme import DARK_THEME
from gui.widgets.path_drop_widget import PathDropWidget


class ScoreUploadScreen(QWidget):
    backRequested = Signal()
    submitRequested = Signal(str, str)

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

        title = QLabel("Upload Zantiks Behavior Data")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")

        instructions = QLabel(
            "Drag and drop a Zantiks file here, or browse to select it. "
            "The filename must match the original exported format. "
            "If the name is rejected, open the filename guide from the error dialog."
        )
        instructions.setWordWrap(True)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("No file selected")
        self.path_edit.textChanged.connect(self._sync_submit)
        browse_button = QPushButton("Browse File")
        browse_button.clicked.connect(self._browse_file)

        row = QHBoxLayout()
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse_button)

        self.drop_widget = PathDropWidget("Drop a Zantiks CSV or XLSX file here")
        self.drop_widget.pathDropped.connect(self.path_edit.setText)

        monitor_title = QLabel("Optional: Monitor Temperature Log")
        monitor_title.setStyleSheet("font-size: 15px; font-weight: 600; margin-top: 8px;")

        monitor_instructions = QLabel(
            "If you have a matching TriKinetics DAM/DEnM Monitor .txt log, upload it here to "
            "plot temperature from that log instead of the Zantiks file's internal sensor. "
            "The only requirement is that it be a .txt file."
        )
        monitor_instructions.setWordWrap(True)
        monitor_instructions.setStyleSheet(f"color: {DARK_THEME.text_secondary}; font-size: 12px;")

        self.monitor_path_edit = QLineEdit()
        self.monitor_path_edit.setPlaceholderText("No monitor log selected (optional)")
        monitor_browse_button = QPushButton("Browse Monitor File")
        monitor_browse_button.clicked.connect(self._browse_monitor_file)
        monitor_clear_button = QPushButton("Clear")
        monitor_clear_button.clicked.connect(lambda: self.monitor_path_edit.setText(""))

        monitor_row = QHBoxLayout()
        monitor_row.addWidget(self.monitor_path_edit, 1)
        monitor_row.addWidget(monitor_browse_button)
        monitor_row.addWidget(monitor_clear_button)

        self.monitor_drop_widget = PathDropWidget("Drop a Monitor .txt log here")
        self.monitor_drop_widget.pathDropped.connect(self.monitor_path_edit.setText)

        buttons = QHBoxLayout()
        self.submit_button = QPushButton("Submit")
        self.submit_button.setEnabled(False)
        self.submit_button.clicked.connect(self._emit_submit)
        buttons.addStretch(1)
        buttons.addWidget(self.submit_button)

        drop_row = QHBoxLayout()
        drop_row.addStretch(1)
        drop_row.addWidget(self.drop_widget)
        drop_row.addWidget(self.monitor_drop_widget)
        drop_row.addStretch(1)

        layout.addLayout(header)
        layout.addWidget(title)
        layout.addWidget(instructions)
        layout.addLayout(row)
        layout.addWidget(monitor_title)
        layout.addWidget(monitor_instructions)
        layout.addLayout(monitor_row)
        layout.addLayout(drop_row)
        layout.addStretch(1)
        layout.addLayout(buttons)

    def set_selected_path(self, path: str = "") -> None:
        self.path_edit.setText(path)

    def set_selected_monitor_path(self, path: str = "") -> None:
        self.monitor_path_edit.setText(path)

    def _browse_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Zantiks File",
            "",
            "Data Files (*.csv *.xlsx);;All Files (*)",
        )
        if file_path:
            self.path_edit.setText(file_path)

    def _browse_monitor_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Monitor Temperature Log",
            "",
            "Text Files (*.txt);;All Files (*)",
        )
        if file_path:
            self.monitor_path_edit.setText(file_path)

    def _sync_submit(self) -> None:
        self.submit_button.setEnabled(bool(self.path_edit.text().strip()))

    def _emit_submit(self) -> None:
        self.submitRequested.emit(self.path_edit.text().strip(), self.monitor_path_edit.text().strip())
