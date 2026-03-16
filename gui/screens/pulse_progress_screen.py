"""Progress screen for pulse-metrics analysis."""

from __future__ import annotations

from gui.screens.progress_screen_base import ProgressScreenBase


class PulseProgressScreen(ProgressScreenBase):
    def __init__(self, parent=None) -> None:
        super().__init__("Computing Pulse Metrics", parent=parent)
