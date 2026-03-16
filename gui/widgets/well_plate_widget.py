"""Interactive 96-well plate widget."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton, QSizePolicy, QSpacerItem, QWidget

from gui.theme import DARK_THEME
from services.default_well_mapping import PLATE_COLUMNS, PLATE_ROWS, PLATE_WELLS

PALETTE = [
    "#255f8f",
    "#246f5e",
    "#7f4b68",
    "#674f90",
    "#6f532f",
    "#3f5a9a",
]


def _hex_to_rgb(color: str) -> Tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[index : index + 2], 16) for index in (0, 2, 4))


def _blend_with_white(color: str, amount: float) -> str:
    red, green, blue = _hex_to_rgb(color)
    blended = (
        round(red + (255 - red) * amount),
        round(green + (255 - green) * amount),
        round(blue + (255 - blue) * amount),
    )
    return "#{:02x}{:02x}{:02x}".format(*blended)


def _blend_with_color(color: str, target: str, amount: float) -> str:
    red, green, blue = _hex_to_rgb(color)
    target_red, target_green, target_blue = _hex_to_rgb(target)
    blended = (
        round(red + (target_red - red) * amount),
        round(green + (target_green - green) * amount),
        round(blue + (target_blue - blue) * amount),
    )
    return "#{:02x}{:02x}{:02x}".format(*blended)


def blend_with_white(color: str, amount: float) -> str:
    return _blend_with_white(color, amount)


def blend_with_surface(color: str, amount: float) -> str:
    return _blend_with_color(color, DARK_THEME.surface, amount)


def build_genotype_color_lookup(genotype_order: Sequence[str]) -> Dict[str, str]:
    return {
        genotype: PALETTE[index % len(PALETTE)]
        for index, genotype in enumerate(genotype_order)
    }


class WellButton(QPushButton):
    def __init__(self, well_id: str, parent=None) -> None:
        super().__init__(well_id, parent)
        self.well_id = well_id
        self.setCheckable(True)
        self.setMinimumSize(44, 44)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


class WellPlateWidget(QWidget):
    selectionChanged = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._genotype_order: List[str] = []
        self._mapping: Dict[str, List[str]] = {}
        self._selected_wells: set[str] = set()
        self._buttons: Dict[str, WellButton] = {}

        grid = QGridLayout(self)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

        for col_idx, col in enumerate(PLATE_COLUMNS, start=1):
            label = QLabel(col, alignment=Qt.AlignCenter)
            label.setStyleSheet("font-weight: 700;")
            grid.addWidget(label, 0, col_idx)

        for row_idx, row in enumerate(PLATE_ROWS, start=1):
            grid.setRowMinimumHeight(row_idx, 44)
            label = QLabel(row, alignment=Qt.AlignCenter)
            label.setStyleSheet("font-weight: 700;")
            grid.addWidget(label, row_idx, 0)
            for col_idx, col in enumerate(PLATE_COLUMNS, start=1):
                well_id = f"{row}{col}"
                button = WellButton(well_id)
                button.clicked.connect(lambda checked=False, wid=well_id: self._handle_click(wid))
                grid.addWidget(button, row_idx, col_idx)
                self._buttons[well_id] = button

        # Let the spare vertical space collect below the plate instead of between
        # the column headers and the first row of wells.
        grid.addItem(
            QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding),
            len(PLATE_ROWS) + 1,
            0,
            1,
            len(PLATE_COLUMNS) + 1,
        )

    def set_plate_mapping(self, mapping: Dict[str, List[str]], genotype_order: List[str]) -> None:
        self._mapping = {genotype: list(wells) for genotype, wells in mapping.items()}
        self._genotype_order = list(genotype_order)
        self._selected_wells.clear()
        self._refresh_styles()

    def assign_well(self, well_id: str, genotype: Optional[str]) -> None:
        for wells in self._mapping.values():
            if well_id in wells:
                wells.remove(well_id)
        if genotype:
            self._mapping.setdefault(genotype, []).append(well_id)
        self._refresh_styles()

    def mapping(self) -> Dict[str, List[str]]:
        return {genotype: list(wells) for genotype, wells in self._mapping.items()}

    def selected_well(self) -> Optional[str]:
        selected_wells = self.selected_wells()
        return selected_wells[0] if selected_wells else None

    def selected_wells(self) -> List[str]:
        return [well_id for well_id in PLATE_WELLS if well_id in self._selected_wells]

    def clear_selection(self) -> None:
        self._selected_wells.clear()
        self._refresh_styles()
        self.selectionChanged.emit([])

    def _handle_click(self, well_id: str) -> None:
        if well_id in self._selected_wells:
            self._selected_wells.remove(well_id)
        else:
            self._selected_wells.add(well_id)
        selected_wells = self.selected_wells()
        self._refresh_styles()
        self.selectionChanged.emit(selected_wells)

    def _refresh_styles(self) -> None:
        color_lookup = build_genotype_color_lookup(self._genotype_order)
        assigned_lookup = {}
        has_selection = bool(self._selected_wells)
        for genotype, wells in self._mapping.items():
            for well in wells:
                assigned_lookup[well] = genotype

        for well_id in PLATE_WELLS:
            button = self._buttons[well_id]
            genotype = assigned_lookup.get(well_id)
            is_selected = well_id in self._selected_wells
            background = color_lookup.get(genotype, DARK_THEME.surface_alt)
            border = DARK_THEME.border
            text_color = DARK_THEME.text_primary
            font_weight = 600

            if is_selected:
                border = DARK_THEME.accent
                text_color = DARK_THEME.text_primary
                font_weight = 700
            elif has_selection:
                background = blend_with_surface(background, 0.72) if genotype else DARK_THEME.window
                border = DARK_THEME.border
                text_color = DARK_THEME.text_muted

            button.setChecked(is_selected)
            button.setStyleSheet(
                f"""
                QPushButton {{
                    border: 2px solid {border};
                    border-radius: 22px;
                    background: {background};
                    color: {text_color};
                    font-weight: {font_weight};
                }}
                """
            )
