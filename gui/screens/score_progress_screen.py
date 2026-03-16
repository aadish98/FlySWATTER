"""Progress screen for behavior scoring."""

from __future__ import annotations

from gui.screens.progress_screen_base import ProgressScreenBase


class ScoreProgressScreen(ProgressScreenBase):
    def __init__(self, parent=None) -> None:
        super().__init__("Generating Sleep and Arousal Results", parent=parent)
