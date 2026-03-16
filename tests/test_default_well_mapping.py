from services.default_well_mapping import (
    DEFAULT_GENOTYPE_ORDER,
    PLATE_WELLS,
    get_default_mapping,
    validate_default_mapping,
)


def test_default_mapping_covers_full_plate():
    mapping = get_default_mapping()
    wells = []
    for genotype in DEFAULT_GENOTYPE_ORDER:
        wells.extend(mapping[genotype])
    assert len(wells) == 96
    assert len(set(wells)) == 96
    assert sorted(wells) == sorted(PLATE_WELLS)


def test_default_mapping_validation_passes():
    validate_default_mapping()
