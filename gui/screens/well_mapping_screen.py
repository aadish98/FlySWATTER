"""Interactive genotype-to-well mapping editor."""

from __future__ import annotations

from collections import OrderedDict
from typing import Dict, List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.theme import DARK_THEME
from gui.widgets.well_plate_widget import PALETTE, WellPlateWidget, blend_with_surface, build_genotype_color_lookup

_DARK = DARK_THEME.text_primary
_MID = DARK_THEME.text_secondary
_MUTED = DARK_THEME.text_muted

_HEADING_STYLE = f"font-size: 16px; font-weight: 700; color: {_DARK};"
_HELP_STYLE = f"color: {_MID};"
_LABEL_STYLE = f"color: {_DARK}; font-weight: 600;"

_BUTTON_STYLE = f"""
    QPushButton {{
        border: 1px solid {DARK_THEME.border};
        border-radius: 12px;
        padding: 8px 14px;
        background: {DARK_THEME.surface_elevated};
        color: {_DARK};
        font-weight: 600;
    }}
    QPushButton:hover {{ background: {DARK_THEME.surface_alt}; }}
    QPushButton:pressed {{ background: {DARK_THEME.accent_pressed}; }}
    QPushButton:disabled {{ border-color: {DARK_THEME.border}; background: {DARK_THEME.window}; color: {_MUTED}; }}
"""

_COMBO_STYLE = f"""
    QComboBox {{
        border: 1px solid {DARK_THEME.border};
        border-radius: 12px;
        padding: 8px 12px;
        min-height: 24px;
        background: {DARK_THEME.surface};
        color: {_DARK};
    }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{
        border: 1px solid {DARK_THEME.border};
        selection-background-color: {DARK_THEME.accent};
        background: {DARK_THEME.surface};
        color: {_DARK};
    }}
    QComboBox:disabled {{ border-color: {DARK_THEME.border}; background: {DARK_THEME.window}; color: {_MUTED}; }}
"""

_PANEL_STYLE = """
    QFrame#genotypesPanel, QFrame#selectionPanel {
        background: %s;
        border: 1px solid %s;
        border-radius: 20px;
    }
""" % (DARK_THEME.surface, DARK_THEME.border)


class _GenotypeRow(QFrame):
    """One row: editable name on top, n=N label bottom-left, Remove bottom-right."""

    changed = Signal()
    removeRequested = Signal(object)

    def __init__(self, genotype_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        self.name_edit = QLineEdit(genotype_name)
        self.name_edit.setFixedHeight(36)
        self.name_edit.textChanged.connect(self.changed)

        self.count_label = QLabel("n = 0")
        self.count_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

        self.remove_button = QPushButton("Remove")
        self.remove_button.setFixedHeight(30)
        self.remove_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.remove_button.clicked.connect(lambda: self.removeRequested.emit(self))

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(6)
        footer.addWidget(self.count_label)
        footer.addStretch(1)
        footer.addWidget(self.remove_button)

        root.addWidget(self.name_edit)
        root.addLayout(footer)
        self.apply_color(PALETTE[0])

    def set_count(self, value: int) -> None:
        self.count_label.setText(f"n = {value}")

    def apply_color(self, base_color: str) -> None:
        panel_bg = blend_with_surface(base_color, 0.64)
        ctrl_bg = blend_with_surface(base_color, 0.74)
        hover_bg = blend_with_surface(base_color, 0.68)
        self.setStyleSheet(
            f"""
            _GenotypeRow {{
                border: 1px solid {DARK_THEME.border};
                border-radius: 14px;
                background: {panel_bg};
            }}
            QLineEdit {{
                border: 1px solid {DARK_THEME.border};
                border-radius: 10px;
                padding: 6px 10px;
                background: {ctrl_bg};
                color: {_DARK};
                selection-background-color: {DARK_THEME.accent};
            }}
            QLabel {{
                border: none;
                background: transparent;
                color: {_DARK};
                font-weight: 700;
                padding: 4px 8px;
            }}
            QPushButton {{
                border: 1px solid {DARK_THEME.border};
                border-radius: 10px;
                padding: 4px 12px;
                background: {ctrl_bg};
                color: {_DARK};
                font-weight: 600;
            }}
            QPushButton:hover {{ background: {hover_bg}; }}
            """
        )


class WellMappingScreen(QWidget):
    backRequested = Signal()
    submitRequested = Signal(dict, list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: List[_GenotypeRow] = []
        self._mapping: Dict[str, List[str]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(14)

        # -- header --
        header = QHBoxLayout()
        back_button = QPushButton("Back")
        back_button.setStyleSheet(_BUTTON_STYLE)
        back_button.clicked.connect(self.backRequested)
        header.addWidget(back_button)
        header.addStretch(1)

        title = QLabel("Confirm Genotype-Well Mapping")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")

        # -- content area: left controls + right plate --
        content = QHBoxLayout()
        content.setSpacing(18)

        # Left side: two stacked panels inside a scroll area so nothing clips.
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setFixedWidth(440)
        left_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        left_inner = QWidget()
        left_inner.setStyleSheet("background: transparent;")
        left_layout = QVBoxLayout(left_inner)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(14)

        # ---- Genotypes panel ----
        genotypes_frame = QFrame()
        genotypes_frame.setObjectName("genotypesPanel")
        genotypes_frame.setStyleSheet(_PANEL_STYLE)
        genotypes_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        gen_layout = QVBoxLayout(genotypes_frame)
        gen_layout.setContentsMargins(16, 16, 16, 16)
        gen_layout.setSpacing(10)

        gen_title = QLabel("Genotypes")
        gen_title.setStyleSheet(_HEADING_STYLE)
        gen_help = QLabel("Edit genotype label in-place.")
        gen_help.setWordWrap(True)
        gen_help.setStyleSheet(_HELP_STYLE)

        self.rows_container = QVBoxLayout()
        self.rows_container.setContentsMargins(0, 0, 0, 0)
        self.rows_container.setSpacing(8)
        rows_widget = QWidget()
        rows_widget.setStyleSheet("background: transparent;")
        rows_widget.setLayout(self.rows_container)

        self.add_genotype_button = QPushButton("Add Genotype")
        self.add_genotype_button.setStyleSheet(_BUTTON_STYLE)
        self.add_genotype_button.clicked.connect(self._add_genotype_row)

        gen_layout.addWidget(gen_title)
        gen_layout.addWidget(gen_help)
        gen_layout.addWidget(rows_widget)
        gen_layout.addWidget(self.add_genotype_button, 0, Qt.AlignLeft)

        # ---- Well Selection panel ----
        selection_frame = QFrame()
        selection_frame.setObjectName("selectionPanel")
        selection_frame.setStyleSheet(_PANEL_STYLE)
        selection_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        sel_layout = QVBoxLayout(selection_frame)
        sel_layout.setContentsMargins(16, 16, 16, 16)
        sel_layout.setSpacing(10)

        sel_title = QLabel("Well Selection")
        sel_title.setStyleSheet(_HEADING_STYLE)
        sel_help = QLabel("Select the well plates you would like to reassign to a different genotype.")
        sel_help.setWordWrap(True)
        sel_help.setStyleSheet(_HELP_STYLE)

        self.selected_well_label = QLabel("# Selected Wells: 0")
        self.selected_well_label.setStyleSheet(_LABEL_STYLE)
        self.deselect_button = QPushButton("Deselect")
        self.deselect_button.setStyleSheet(_BUTTON_STYLE)
        self.deselect_button.clicked.connect(self._deselect_well)
        selected_well_row = QHBoxLayout()
        selected_well_row.setSpacing(10)
        selected_well_row.addWidget(self.selected_well_label, 1)
        selected_well_row.addWidget(self.deselect_button)

        self.assign_label = QLabel("Assign selected wells to")
        self.assign_label.setStyleSheet(_LABEL_STYLE)
        self.assignment_combo = QComboBox()
        self.assignment_combo.setStyleSheet(_COMBO_STYLE)
        self.assignment_combo.setPlaceholderText("Multiple current assignments")

        self.confirm_reassign_button = QPushButton("Confirm Reassignment")
        self.confirm_reassign_button.setStyleSheet(_BUTTON_STYLE)
        self.confirm_reassign_button.clicked.connect(self._confirm_reassignment)

        self.assign_note = QLabel("Choose a genotype above, then confirm to reassign.")
        self.assign_note.setWordWrap(True)
        self.assign_note.setStyleSheet(_HELP_STYLE)

        self.clear_well_button = QPushButton("Clear Selected Wells")
        self.clear_well_button.setStyleSheet(_BUTTON_STYLE)
        self.clear_well_button.clicked.connect(self._clear_selected_well)

        sel_layout.addWidget(sel_title)
        sel_layout.addWidget(sel_help)
        sel_layout.addLayout(selected_well_row)
        sel_layout.addWidget(self.assign_label)
        sel_layout.addWidget(self.assignment_combo)
        sel_layout.addWidget(self.confirm_reassign_button)
        sel_layout.addWidget(self.assign_note)
        sel_layout.addWidget(self.clear_well_button)

        # Assemble left column
        left_layout.addWidget(genotypes_frame)
        left_layout.addWidget(selection_frame)
        left_layout.addStretch(1)
        left_scroll.setWidget(left_inner)

        # Right: well plate
        self.plate = WellPlateWidget()
        self.plate.selectionChanged.connect(self._handle_well_selected)

        content.addWidget(left_scroll)
        content.addWidget(self.plate, 1)

        # -- footer --
        buttons = QHBoxLayout()
        confirm_button = QPushButton("Confirm Mapping")
        confirm_button.setStyleSheet(_BUTTON_STYLE)
        confirm_button.clicked.connect(self._submit)
        buttons.addStretch(1)
        buttons.addWidget(confirm_button)

        outer.addLayout(header)
        outer.addWidget(title)
        outer.addLayout(content, 1)
        outer.addLayout(buttons)
        self._update_selection_controls([])

    # -- public API --

    def set_mapping(self, mapping: Dict[str, List[str]], genotype_order: List[str]) -> None:
        self._mapping = OrderedDict((genotype, list(mapping.get(genotype, []))) for genotype in genotype_order)
        for row in self._rows:
            row.setParent(None)
        self._rows.clear()

        for genotype in genotype_order:
            self._create_row(genotype)

        self._sync_assignment_combo()
        self._refresh_counts()
        self._refresh_row_styles()
        self.plate.set_plate_mapping(self._mapping, self.genotype_names())
        self._update_selection_controls([])

    def genotype_names(self) -> List[str]:
        return [row.name_edit.text().strip() or f"Genotype {idx + 1}" for idx, row in enumerate(self._rows)]

    # -- private helpers --

    def _create_row(self, genotype_name: str) -> None:
        row = _GenotypeRow(genotype_name)
        row.changed.connect(self._handle_genotype_renamed)
        row.removeRequested.connect(self._remove_row)
        self.rows_container.addWidget(row)
        self._rows.append(row)

    def _add_genotype_row(self) -> None:
        self._create_row(f"Genotype {len(self._rows) + 1}")
        self._mapping.setdefault(f"Genotype {len(self._rows)}", [])
        self._handle_genotype_renamed()

    def _remove_row(self, row: _GenotypeRow) -> None:
        if len(self._rows) <= 1:
            QMessageBox.warning(self, "Cannot Remove", "At least one genotype is required.")
            return
        old_name = row.name_edit.text().strip() or f"Genotype {self._rows.index(row) + 1}"
        wells = self._mapping.pop(old_name, [])
        row.setParent(None)
        self._rows.remove(row)
        remaining_name = self.genotype_names()[0]
        self._mapping.setdefault(remaining_name, []).extend(wells)
        self._handle_genotype_renamed()

    def _handle_genotype_renamed(self) -> None:
        old_items = list(self._mapping.items())
        new_mapping: Dict[str, List[str]] = OrderedDict()
        for idx, row in enumerate(self._rows, start=1):
            name = row.name_edit.text().strip() or f"Genotype {idx}"
            wells = list(old_items[idx - 1][1]) if idx - 1 < len(old_items) else []
            new_mapping[name] = list(dict.fromkeys(wells))
        if not new_mapping:
            return
        self._mapping = OrderedDict((name, list(dict.fromkeys(wells))) for name, wells in new_mapping.items())
        self._sync_assignment_combo()
        self._refresh_counts()
        self._refresh_row_styles()
        self.plate.set_plate_mapping(self._mapping, self.genotype_names())
        self._update_selection_controls([])

    def _handle_well_selected(self, selected_wells: List[str]) -> None:
        self._update_selection_controls(selected_wells)

    def _confirm_reassignment(self) -> None:
        selected_wells = self.plate.selected_wells()
        genotype = self.assignment_combo.currentText()
        if not selected_wells or not genotype:
            return
        for well_id in selected_wells:
            if genotype == "Unassigned":
                self.plate.assign_well(well_id, None)
            else:
                self.plate.assign_well(well_id, genotype)
        self._mapping = OrderedDict((name, wells) for name, wells in self.plate.mapping().items())
        self._refresh_counts()
        self.plate.clear_selection()
        self._update_selection_controls([])

    def _clear_selected_well(self) -> None:
        selected_wells = self.plate.selected_wells()
        if not selected_wells:
            return
        for well_id in selected_wells:
            self.plate.assign_well(well_id, None)
        self._mapping = OrderedDict((name, wells) for name, wells in self.plate.mapping().items())
        self._refresh_counts()
        self._update_selection_controls(selected_wells)

    def _deselect_well(self) -> None:
        self.plate.clear_selection()
        self._update_selection_controls([])

    def _sync_assignment_combo(self) -> None:
        current = self.assignment_combo.currentText()
        self.assignment_combo.blockSignals(True)
        self.assignment_combo.clear()
        self.assignment_combo.addItem("Unassigned")
        color_lookup = build_genotype_color_lookup(self.genotype_names())
        for genotype in self.genotype_names():
            self.assignment_combo.addItem(genotype)
            idx = self.assignment_combo.count() - 1
            base = color_lookup.get(genotype, DARK_THEME.surface_alt)
            self.assignment_combo.setItemData(idx, QColor(base), Qt.BackgroundRole)
            self.assignment_combo.setItemData(idx, QColor(_DARK), Qt.ForegroundRole)
        if current and self.assignment_combo.findText(current) >= 0:
            self.assignment_combo.setCurrentText(current)
        self.assignment_combo.blockSignals(False)

    def _refresh_counts(self) -> None:
        for row in self._rows:
            genotype = row.name_edit.text().strip() or "Unnamed Genotype"
            row.set_count(len(self._mapping.get(genotype, [])))

    def _refresh_row_styles(self) -> None:
        color_lookup = build_genotype_color_lookup(self.genotype_names())
        for index, row in enumerate(self._rows):
            genotype = row.name_edit.text().strip() or f"Genotype {index + 1}"
            row.apply_color(color_lookup.get(genotype, PALETTE[index % len(PALETTE)]))

    def _update_selection_controls(self, selected_wells: Sequence[str]) -> None:
        has_selection = bool(selected_wells)
        self.selected_well_label.setText(f"# Selected Wells: {len(selected_wells)}")
        self.deselect_button.setEnabled(has_selection)
        self.assign_label.setEnabled(has_selection)
        self.assignment_combo.setEnabled(has_selection)
        self.confirm_reassign_button.setEnabled(has_selection)
        self.assign_note.setEnabled(has_selection)
        self.clear_well_button.setEnabled(has_selection)
        self.assign_note.setStyleSheet(f"color: {_MID};" if has_selection else f"color: {_MUTED};")
        self.assignment_combo.blockSignals(True)
        if not has_selection:
            self.assignment_combo.setCurrentIndex(-1)
        else:
            selected_genotypes = {self._genotype_for_well(well_id) or "Unassigned" for well_id in selected_wells}
            if len(selected_genotypes) == 1:
                self.assignment_combo.setCurrentText(next(iter(selected_genotypes)))
            else:
                self.assignment_combo.setCurrentIndex(-1)
        self.assignment_combo.blockSignals(False)

    def _genotype_for_well(self, well_id: Optional[str]) -> Optional[str]:
        if well_id is None:
            return None
        for genotype, wells in self._mapping.items():
            if well_id in wells:
                return genotype
        return None

    def _submit(self) -> None:
        genotype_names = self.genotype_names()
        if len({name.casefold() for name in genotype_names}) != len(genotype_names):
            QMessageBox.warning(self, "Duplicate Genotypes", "Please give each genotype a unique name.")
            return
        mapping = OrderedDict((name, list(self._mapping.get(name, []))) for name in genotype_names)
        self.submitRequested.emit(mapping, genotype_names)
