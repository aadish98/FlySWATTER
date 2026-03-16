"""Lightweight dual-handle horizontal range slider widget."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from gui.theme import DARK_THEME


class RangeSliderWidget(QWidget):
    rangeChanged = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 1
        self._lower = 0
        self._upper = 1
        self._active_handle: str | None = None
        self._track_margin = 14
        self._handle_radius = 8
        self.setMinimumHeight(44)
        self.setMouseTracking(True)

    def set_bounds(self, minimum: int, maximum: int) -> None:
        minimum = int(minimum)
        maximum = int(maximum)
        if maximum <= minimum:
            maximum = minimum + 1
        self._minimum = minimum
        self._maximum = maximum
        self.set_range(self._lower, self._upper, emit=False)

    def set_range(self, lower: int, upper: int, *, emit: bool = True) -> None:
        lower = max(self._minimum, min(int(lower), self._maximum))
        upper = max(self._minimum, min(int(upper), self._maximum))
        if lower > upper:
            lower, upper = upper, lower
        changed = lower != self._lower or upper != self._upper
        self._lower = lower
        self._upper = upper
        self.update()
        if changed and emit:
            self.rangeChanged.emit(self._lower, self._upper)

    def lower_value(self) -> int:
        return self._lower

    def upper_value(self) -> int:
        return self._upper

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        center_y = self.height() / 2
        left = float(self._track_margin)
        right = float(max(self.width() - self._track_margin, self._track_margin + 1))
        lower_x = self._value_to_x(self._lower)
        upper_x = self._value_to_x(self._upper)

        painter.setPen(QPen(QColor(DARK_THEME.border), 6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(left, center_y), QPointF(right, center_y))

        painter.setPen(QPen(QColor(DARK_THEME.accent), 6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(lower_x, center_y), QPointF(upper_x, center_y))

        painter.setPen(QPen(QColor(DARK_THEME.accent_hover), 1))
        painter.setBrush(QColor(DARK_THEME.surface_alt))
        painter.drawEllipse(QPointF(lower_x, center_y), self._handle_radius, self._handle_radius)
        painter.drawEllipse(QPointF(upper_x, center_y), self._handle_radius, self._handle_radius)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        lower_x = self._value_to_x(self._lower)
        upper_x = self._value_to_x(self._upper)
        click_x = float(event.position().x())
        d_lower = abs(click_x - lower_x)
        d_upper = abs(click_x - upper_x)
        self._active_handle = "lower" if d_lower <= d_upper else "upper"
        self._update_active_from_position(click_x)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._active_handle is None:
            return
        self._update_active_from_position(float(event.position().x()))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._active_handle = None
        event.accept()

    def _update_active_from_position(self, x_pos: float) -> None:
        value = self._x_to_value(x_pos)
        if self._active_handle == "lower":
            self.set_range(value, self._upper)
        elif self._active_handle == "upper":
            self.set_range(self._lower, value)

    def _value_to_x(self, value: int) -> float:
        span = self._maximum - self._minimum
        if span <= 0:
            return float(self._track_margin)
        available = max(self.width() - (2 * self._track_margin), 1)
        ratio = (value - self._minimum) / span
        return float(self._track_margin + (ratio * available))

    def _x_to_value(self, x_pos: float) -> int:
        left = float(self._track_margin)
        right = float(max(self.width() - self._track_margin, self._track_margin + 1))
        clamped = min(max(x_pos, left), right)
        ratio = (clamped - left) / (right - left)
        value = self._minimum + ratio * (self._maximum - self._minimum)
        return int(round(value))
