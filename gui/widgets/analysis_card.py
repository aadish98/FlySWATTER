"""Clickable analysis card used on the Choose Analysis screen."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout

from gui.theme import DARK_THEME


class AnalysisCard(QFrame):
    clicked = Signal()

    def __init__(self, title: str, icon_kind: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.icon_kind = icon_kind
        self._hovered = False
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumSize(300, 310)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setObjectName("analysisCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)

        self.icon_label = QLabel(alignment=Qt.AlignCenter)
        self.icon_label.setFixedHeight(170)
        self.icon_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.title_label = QLabel(title, alignment=Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setMinimumHeight(52)
        self.title_label.setStyleSheet(f"color: {DARK_THEME.text_primary};")
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        self.title_label.setFont(font)

        layout.addWidget(self.icon_label)
        layout.addWidget(self.title_label)
        layout.addStretch(1)
        self._refresh_icon()

    def enterEvent(self, event) -> None:  # pragma: no cover - GUI behavior
        self._hovered = True
        self._refresh_icon()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # pragma: no cover - GUI behavior
        self._hovered = False
        self._refresh_icon()
        self.update()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:  # pragma: no cover - GUI behavior
        self._refresh_icon()
        super().resizeEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - GUI behavior
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI behavior
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        bg = QColor(DARK_THEME.surface_elevated if self._hovered else DARK_THEME.surface)
        border = QColor(DARK_THEME.accent if self._hovered else DARK_THEME.border)
        shadow = QColor(0, 0, 0, 120)
        shadow_rect = rect.translated(0, 4).adjusted(2, 2, -2, -2)
        shadow_path = QPainterPath()
        shadow_path.addRoundedRect(shadow_rect, 28, 28)
        card_path = QPainterPath()
        card_path.addRoundedRect(rect, 28, 28)
        painter.setPen(Qt.NoPen)
        painter.setBrush(shadow)
        painter.drawPath(shadow_path)
        painter.setBrush(bg)
        painter.setPen(QPen(border, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(card_path)

    def _refresh_icon(self) -> None:
        available_width = max(self.width() - 84, 120)
        available_height = max(self.icon_label.height() - 16, 120)
        size = max(min(available_width, available_height, 170), 120)
        self.icon_label.setPixmap(_build_icon(self.icon_kind, size, self._hovered))


@lru_cache(maxsize=16)
def _build_icon(icon_kind: str, size: int, hovered: bool) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    fg = QColor(DARK_THEME.text_primary if hovered else DARK_THEME.text_secondary)
    accent = QColor(DARK_THEME.accent_hover if hovered else DARK_THEME.accent)
    painter.setPen(QPen(fg, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)

    if icon_kind == "score":
        _draw_sleep_fly(painter, QRectF(18, 32, size - 36, size - 54), fg, accent)
    else:
        _draw_pulse_fly(painter, QRectF(18, 24, size - 36, size - 36), fg, accent)

    painter.end()
    return pixmap


def _draw_sleep_fly(painter: QPainter, rect: QRectF, fg: QColor, accent: QColor) -> None:
    center = rect.center()
    body_rect = QRectF(center.x() - 14, center.y() - 24, 28, 48)
    head_rect = QRectF(center.x() - 10, center.y() - 42, 20, 18)
    left_wing = QRectF(center.x() - 36, center.y() - 30, 34, 26)
    right_wing = QRectF(center.x() + 2, center.y() - 30, 34, 26)
    painter.drawEllipse(left_wing)
    painter.drawEllipse(right_wing)
    painter.drawEllipse(body_rect)
    painter.drawEllipse(head_rect)
    painter.drawLine(center.x() - 24, center.y() + 6, center.x() - 42, center.y() + 18)
    painter.drawLine(center.x() + 24, center.y() + 6, center.x() + 42, center.y() + 18)
    painter.drawLine(center.x() - 18, center.y() + 18, center.x() - 34, center.y() + 34)
    painter.drawLine(center.x() + 18, center.y() + 18, center.x() + 34, center.y() + 34)

    painter.setPen(QPen(accent, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    font = QFont()
    font.setBold(True)
    font.setPointSize(18)
    painter.setFont(font)
    painter.drawText(QRect(int(rect.right()) - 50, int(rect.top()) - 8, 50, 50), Qt.AlignCenter, "Z")
    font.setPointSize(13)
    painter.setFont(font)
    painter.drawText(QRect(int(rect.right()) - 18, int(rect.top()) + 16, 32, 32), Qt.AlignCenter, "Z")


def _draw_pulse_fly(painter: QPainter, rect: QRectF, fg: QColor, accent: QColor) -> None:
    center = rect.center()
    box_rect = QRectF(center.x() - 28, center.y() - 14, 56, 40)
    painter.drawRect(box_rect)
    painter.drawLine(center.x() - 46, center.y() + 26, center.x() + 46, center.y() + 26)

    body_rect = QRectF(center.x() - 12, center.y() - 4, 24, 28)
    head_rect = QRectF(center.x() - 8, center.y() - 16, 16, 14)
    left_wing = QRectF(center.x() - 28, center.y() - 10, 24, 18)
    right_wing = QRectF(center.x() + 4, center.y() - 10, 24, 18)
    painter.drawEllipse(left_wing)
    painter.drawEllipse(right_wing)
    painter.drawEllipse(body_rect)
    painter.drawEllipse(head_rect)

    painter.setPen(QPen(accent, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    start = QPointF(rect.left() + 10, center.y() + 5)
    mid = QPointF(center.x() - 48, center.y() + 5)
    end = QPointF(center.x() - 34, center.y() + 5)
    painter.drawLine(start, mid)
    painter.drawLine(mid, end)
    painter.drawLine(end, QPointF(end.x() - 9, end.y() - 6))
    painter.drawLine(end, QPointF(end.x() - 9, end.y() + 6))
    font = QFont()
    font.setBold(True)
    font.setPointSize(15)
    painter.setFont(font)
    painter.drawText(QRect(int(rect.left()), int(center.y()) - 24, 28, 24), Qt.AlignCenter, "F")
