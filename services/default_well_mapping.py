"""Default genotype-to-well mapping used by the GUI."""

from __future__ import annotations

from copy import deepcopy
from typing import Dict, List

PLATE_ROWS = ["A", "B", "C", "D", "E", "F", "G", "H"]
PLATE_COLUMNS = [str(i) for i in range(1, 13)]
PLATE_WELLS = [f"{row}{col}" for row in PLATE_ROWS for col in PLATE_COLUMNS]

DEFAULT_GENOTYPE_ORDER = [
    "R85C10Gal4;TrpA1",
    "R85C10Gal4xISO31",
    "Empty Gal4xTrpA1",
]

DEFAULT_MAPPING: Dict[str, List[str]] = {
    "R85C10Gal4;TrpA1": [
        "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11", "A12",
        "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11", "B12",
        "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8",
    ],
    "R85C10Gal4xISO31": [
        "C9", "C10", "C11", "C12", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8",
        "D9", "D10", "D11", "D12", "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8",
        "E9", "E10", "E11", "E12", "F1", "F2", "F3", "F4",
    ],
    "Empty Gal4xTrpA1": [
        "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12", "G1", "G2", "G3", "G4",
        "G5", "G6", "G7", "G8", "G9", "G10", "G11", "G12", "H1", "H2", "H3", "H4",
        "H5", "H6", "H7", "H8", "H9", "H10", "H11", "H12",
    ],
}


def get_default_mapping() -> Dict[str, List[str]]:
    """Return a deep copy of the bundled well mapping."""
    return deepcopy(DEFAULT_MAPPING)


def get_default_genotype_order() -> List[str]:
    """Return a copy of the bundled genotype display order."""
    return list(DEFAULT_GENOTYPE_ORDER)


def validate_default_mapping() -> None:
    """Ensure the bundled mapping still covers the full 96-well plate."""
    wells = []
    for genotype in DEFAULT_GENOTYPE_ORDER:
        wells.extend(DEFAULT_MAPPING.get(genotype, []))
    if len(wells) != 96:
        raise ValueError(f"Expected 96 mapped wells, found {len(wells)}")
    if len(set(wells)) != 96:
        raise ValueError("Default mapping contains duplicate wells")
    if sorted(wells) != sorted(PLATE_WELLS):
        raise ValueError("Default mapping does not cover the full 96-well plate")
