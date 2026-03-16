"""Centralized forced-dark theme tokens and application helper."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class ThemeTokens:
    window: str
    surface: str
    surface_alt: str
    surface_elevated: str
    border: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    error: str


DARK_THEME = ThemeTokens(
    window="#1b1f26",
    surface="#232a33",
    surface_alt="#2a3340",
    surface_elevated="#313d4d",
    border="#455365",
    text_primary="#e8edf6",
    text_secondary="#c7d2e4",
    text_muted="#9aa8bf",
    accent="#4f8cff",
    accent_hover="#6ea2ff",
    accent_pressed="#2f63c9",
    error="#ff7b7b",
)


def build_dark_palette(tokens: ThemeTokens = DARK_THEME) -> QPalette:
    """Return a cross-platform dark palette used by the full application."""
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(tokens.window))
    palette.setColor(QPalette.WindowText, QColor(tokens.text_primary))
    palette.setColor(QPalette.Base, QColor(tokens.surface))
    palette.setColor(QPalette.AlternateBase, QColor(tokens.surface_alt))
    palette.setColor(QPalette.ToolTipBase, QColor(tokens.surface_alt))
    palette.setColor(QPalette.ToolTipText, QColor(tokens.text_primary))
    palette.setColor(QPalette.Text, QColor(tokens.text_primary))
    palette.setColor(QPalette.Button, QColor(tokens.surface_elevated))
    palette.setColor(QPalette.ButtonText, QColor(tokens.text_primary))
    palette.setColor(QPalette.BrightText, QColor(tokens.error))
    palette.setColor(QPalette.Highlight, QColor(tokens.accent))
    # Keep selected text readable on bright accent highlights.
    palette.setColor(QPalette.HighlightedText, QColor(tokens.window))
    palette.setColor(QPalette.PlaceholderText, QColor(tokens.text_muted))
    palette.setColor(QPalette.Mid, QColor(tokens.border))
    palette.setColor(QPalette.Dark, QColor(tokens.window))
    palette.setColor(QPalette.Light, QColor(tokens.surface_alt))

    disabled_text = QColor(tokens.text_muted)
    palette.setColor(QPalette.Disabled, QPalette.Text, disabled_text)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, disabled_text)
    palette.setColor(QPalette.Disabled, QPalette.WindowText, disabled_text)
    return palette


def apply_forced_dark_theme(app: QApplication, tokens: ThemeTokens = DARK_THEME) -> None:
    """Apply a deterministic dark theme independent of host OS settings."""
    app.setStyle("Fusion")
    app.setPalette(build_dark_palette(tokens))
    app.setStyleSheet(
        f"""
        QToolTip {{
            color: {tokens.text_primary};
            background-color: {tokens.surface_alt};
            border: 1px solid {tokens.border};
            padding: 4px 6px;
        }}
        """
    )
