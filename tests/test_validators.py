from pathlib import Path

from services.validators import (
    validate_accel_folder,
    validate_researcher_name,
    validate_score_file,
)


def test_blank_researcher_name_is_rejected():
    result = validate_researcher_name("   ")
    assert not result.valid


def test_valid_score_file_name_is_accepted(tmp_path: Path):
    file_path = tmp_path / "020226_arousal_experiment_script_2Day_protocol-20260206T184805.csv"
    file_path.write_text("RUNTIME,VIB,DAY,HOUR,TIME_BIN\n", encoding="utf-8")
    result = validate_score_file(file_path)
    assert result.valid


def test_renamed_score_file_is_rejected(tmp_path: Path):
    file_path = tmp_path / "renamed_file.csv"
    file_path.write_text("RUNTIME,VIB,DAY,HOUR,TIME_BIN\n", encoding="utf-8")
    result = validate_score_file(file_path)
    assert not result.valid
    assert "modified" in result.message.lower()


def test_valid_accelerometer_folder_is_accepted(tmp_path: Path):
    folder = tmp_path / "03-02-2026 T-1.01pm"
    folder.mkdir()
    (folder / "manifest.json").write_text('{"platform":"Zantiks","speed":"R24","start_iso":"2026-03-02T13:01:00"}', encoding="utf-8")
    data_dir = folder / "03022026"
    data_dir.mkdir()
    (data_dir / "Zantiks_R24E12AD;R85C10DBD_RUN3 G4 P150,400,2000_260302130138_part001.csv.gz").write_text("placeholder", encoding="utf-8")
    result = validate_accel_folder(folder)
    assert result.valid


def test_renamed_accelerometer_folder_is_rejected(tmp_path: Path):
    folder = tmp_path / "renamed_folder"
    folder.mkdir()
    (folder / "manifest.json").write_text('{"platform":"Zantiks","speed":"R24","start_iso":"2026-03-02T13:01:00"}', encoding="utf-8")
    (folder / "bad.csv").write_text("placeholder", encoding="utf-8")
    result = validate_accel_folder(folder)
    assert not result.valid
