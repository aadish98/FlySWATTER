"""Drag-and-drop selector used for file and folder inputs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout

from gui.theme import DARK_THEME


class PathDropWidget(QFrame):
    pathDropped = Signal(str)

    def __init__(self, description: str, expect_directory: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.expect_directory = expect_directory
        self.setAcceptDrops(True)
        self.setObjectName("pathDropWidget")
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumSize(280, 230)
        self.setMaximumWidth(420)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setProperty("dragActive", False)
        self.setStyleSheet(
            f"""
            QFrame#pathDropWidget {{
                border: 2px dashed {DARK_THEME.border};
                border-radius: 18px;
                background: {DARK_THEME.surface};
            }}
            QFrame#pathDropWidget[dragActive="true"] {{
                border: 2px dashed {DARK_THEME.accent};
                background: {DARK_THEME.surface_elevated};
            }}
            QLabel#dropTitle {{
                color: {DARK_THEME.text_primary};
                font-size: 17px;
                font-weight: 700;
            }}
            QLabel#dropDescription {{
                color: {DARK_THEME.text_secondary};
                font-size: 13px;
            }}
            QLabel#dropHint {{
                color: {DARK_THEME.text_muted};
                font-size: 12px;
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 20, 18, 20)
        layout.setSpacing(8)

        drop_target = "folder" if self.expect_directory else "file"
        self.title_label = QLabel(f"Drop {drop_target} here", alignment=Qt.AlignCenter)
        self.title_label.setObjectName("dropTitle")

        self.label = QLabel(description, alignment=Qt.AlignCenter)
        self.label.setObjectName("dropDescription")
        self.label.setWordWrap(True)

        self.hint_label = QLabel("Drag from Finder or use the Browse button", alignment=Qt.AlignCenter)
        self.hint_label.setObjectName("dropHint")
        self.hint_label.setWordWrap(True)

        layout.addStretch(1)
        layout.addWidget(self.title_label)
        layout.addWidget(self.label)
        layout.addWidget(self.hint_label)
        layout.addStretch(1)

    def _accepted_path(self, event) -> Path | None:
        urls = event.mimeData().urls()
        if not urls or not urls[0].isLocalFile():
            return None
        path = Path(urls[0].toLocalFile())
        if self.expect_directory and not path.is_dir():
            return None
        if not self.expect_directory and not path.is_file():
            return None
        return path

    def _set_drag_active(self, active: bool) -> None:
        self.setProperty("dragActive", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # pragma: no cover - GUI behavior
        if self._accepted_path(event) is not None:
            self._set_drag_active(True)
            event.acceptProposedAction()
        else:
            self._set_drag_active(False)
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # pragma: no cover - GUI behavior
        self._set_drag_active(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # pragma: no cover - GUI behavior
        self._set_drag_active(False)
        path = self._accepted_path(event)
        if path is None:
            event.ignore()
            return
        self.pathDropped.emit(str(path))
        event.acceptProposedAction()
