"""Image preview card with optional download button and click-to-zoom."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPalette, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
)


class ImageZoomDialog(QDialog):
    """Full-resolution image viewer opened by clicking an ImagePreviewCard."""

    def __init__(self, title: str, pixmap: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self._source_pixmap = pixmap
        self._zoom_factor = 1.0
        self._zoom_min = 0.08
        self._zoom_max = 6.0
        self._manual_zoom = False
        self.setWindowTitle(title)
        screen = QGuiApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            self.resize(int(geom.width() * 0.9), int(geom.height() * 0.9))
        else:
            self.resize(1000, 700)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.setContentsMargins(12, 10, 12, 0)
        controls.setSpacing(10)
        hint = QLabel("Scroll to zoom (gentle), double-click to fit.")
        self.zoom_label = QLabel("")
        self.zoom_label.setMinimumWidth(72)
        self.zoom_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        fit_button = QPushButton("Fit to Window")
        fit_button.clicked.connect(self._fit_to_window)
        controls.addWidget(hint)
        controls.addStretch(1)
        controls.addWidget(self.zoom_label)
        controls.addWidget(fit_button)
        layout.addLayout(controls)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.viewport().installEventFilter(self)
        self.label = QLabel(alignment=Qt.AlignCenter)
        self.label.installEventFilter(self)
        self.scroll.setWidget(self.label)
        layout.addWidget(self.scroll)

        QTimer.singleShot(0, self._fit_to_window)

    def eventFilter(self, obj, event) -> bool:
        if obj in (self.scroll.viewport(), self.label):
            if event.type() == event.Type.Wheel and event.angleDelta().y():
                self._manual_zoom = True
                cursor_pos = event.position()
                if obj is self.label:
                    cursor_pos = QPointF(self.label.mapTo(self.scroll.viewport(), cursor_pos.toPoint()))
                self._zoom_by_wheel_steps(event.angleDelta().y() / 120.0, cursor_pos)
                return True
            if event.type() == event.Type.MouseButtonDblClick and event.button() == Qt.LeftButton:
                self._fit_to_window()
                return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._manual_zoom:
            self._fit_to_window()

    def _fit_to_window(self) -> None:
        viewport = self.scroll.viewport().size()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        fit_scale = min(
            viewport.width() / max(self._source_pixmap.width(), 1),
            viewport.height() / max(self._source_pixmap.height(), 1),
        )
        fit_scale = min(fit_scale, 1.0)
        self._zoom_min = max(0.01, min(0.08, fit_scale * 0.5))
        self._manual_zoom = False
        self._set_zoom(fit_scale)

    def _zoom_by_wheel_steps(self, steps: float, cursor_pos) -> None:
        old_zoom = self._zoom_factor
        zoom_per_step = 1.08
        new_zoom = max(self._zoom_min, min(self._zoom_max, old_zoom * (zoom_per_step**steps)))
        if abs(new_zoom - old_zoom) < 1e-6:
            return

        h_scroll = self.scroll.horizontalScrollBar()
        v_scroll = self.scroll.verticalScrollBar()
        content_x = (h_scroll.value() + cursor_pos.x()) / old_zoom
        content_y = (v_scroll.value() + cursor_pos.y()) / old_zoom
        self._set_zoom(new_zoom)
        h_scroll.setValue(int(content_x * self._zoom_factor - cursor_pos.x()))
        v_scroll.setValue(int(content_y * self._zoom_factor - cursor_pos.y()))

    def _set_zoom(self, value: float) -> None:
        self._zoom_factor = max(self._zoom_min, min(self._zoom_max, value))
        target_width = max(1, int(self._source_pixmap.width() * self._zoom_factor))
        target_height = max(1, int(self._source_pixmap.height() * self._zoom_factor))
        scaled = self._source_pixmap.scaled(
            target_width,
            target_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.label.setPixmap(scaled)
        self.label.resize(scaled.size())
        self.zoom_label.setText(f"{round(self._zoom_factor * 100)}%")


class ImagePreviewCard(QFrame):
    downloadRequested = Signal(str)

    def __init__(
        self,
        title: str,
        image_path: Path | None,
        button_label: str = "Download High-Res Copy",
        description: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self.image_path = Path(image_path) if image_path else None
        self._source_pixmap: QPixmap | None = None
        palette = self.palette()
        panel_bg = palette.color(QPalette.Base).name()
        border = palette.color(QPalette.Mid).name()
        text = palette.color(QPalette.Text).name()
        image_bg = palette.color(QPalette.AlternateBase).name()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ border: 1px solid {border}; border-radius: 16px; background: {panel_bg}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.title_label = QLabel(title)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {text};")
        layout.addWidget(self.title_label)

        if description:
            self.description_label = QLabel(description)
            self.description_label.setWordWrap(True)
            self.description_label.setStyleSheet(f"font-size: 12px; color: {text};")
            layout.addWidget(self.description_label)

        self.image_label = QLabel(alignment=Qt.AlignCenter)
        self.image_label.setMinimumHeight(180)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image_label.setStyleSheet(f"background: {image_bg}; border-radius: 12px; color: {text};")
        self.image_label.setCursor(Qt.PointingHandCursor)
        self.image_label.installEventFilter(self)
        layout.addWidget(self.image_label)

        self.download_button = QPushButton(button_label)
        self.download_button.clicked.connect(self._emit_download)
        layout.addWidget(self.download_button, alignment=Qt.AlignRight)
        self._load_preview()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.image_label and event.type() == event.Type.MouseButtonRelease:
            if event.button() == Qt.LeftButton and self._source_pixmap is not None:
                self._show_zoom()
                return True
        return super().eventFilter(obj, event)

    def _show_zoom(self) -> None:
        if self._source_pixmap is None:
            return
        dialog = ImageZoomDialog(self._title, self._source_pixmap, self)
        dialog.exec()

    def _load_preview(self) -> None:
        self._source_pixmap = None
        if self.image_path is None or not self.image_path.exists():
            self.image_label.setText("Preview not available for this result.")
            self.image_label.setCursor(Qt.ArrowCursor)
            self.download_button.setEnabled(False)
            return
        pixmap = QPixmap(str(self.image_path))
        if pixmap.isNull():
            self.image_label.setText(str(self.image_path))
            self.image_label.setCursor(Qt.ArrowCursor)
            return
        self._source_pixmap = pixmap
        self._refresh_preview()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if self._source_pixmap is None:
            return
        target = self.image_label.size()
        if target.width() <= 0 or target.height() <= 0:
            return
        scaled = self._source_pixmap.scaled(
            max(target.width() - 8, 1),
            max(target.height() - 8, 1),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    def _emit_download(self) -> None:
        if self.image_path:
            self.downloadRequested.emit(str(self.image_path))
