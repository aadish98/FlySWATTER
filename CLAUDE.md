# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

FlySWATTER (Fly Sleep-Wake Arousal Threshold Testing & Evaluation Resource) is a macOS/Windows desktop GUI application for analyzing Drosophila behavioral data from Zantiks experimental systems. It performs two analyses:

- **Sleep/Arousal Scoring**: Processes Zantiks behavior exports to score fly sleep and arousal states
- **Pulse Metrics Analysis**: Processes accelerometer logs to detect and quantify movement pulses

Output: Excel workbooks, PNG plots, and zip archives saved to a `Data/` directory.

## Commands

```bash
# Install (development)
pip install -e ".[dev]"

# Run from source
python3 flyswatter_gui.py

# Run tests
pytest tests/

# Build macOS app
bash BATCH_SCRIPTS/build_flyswatter_mac_app.sh

# Build Windows exe (PowerShell)
powershell -ExecutionPolicy Bypass -File BATCH_SCRIPTS\build_flyswatter_windows_exe.ps1
```

## Architecture

### Entry Point → GUI → Services → Analysis

**`flyswatter_gui.py`** — sets `OPENBLAS_NUM_THREADS=1` (numpy/Qt compatibility), creates QApplication with dark theme, launches main window.

**`gui/main_window.py`** — central hub using `QStackedWidget` to manage 11 screens as a state machine. Owns `AppState`, runs analysis in background threads via `workers.py`, and routes navigation signals.

**`gui/app_state.py`** — shared mutable state passed between screens: researcher name, selected files, genotype mappings, and analysis results (`ScoreAnalysisResult`, `PulseAnalysisResult`).

**`gui/screens/`** — 11 screen classes, two independent flows:
- Score flow: `welcome → choose_analysis → score_upload → well_mapping → sleep_definition → score_progress → score_results`
- Pulse flow: `welcome → choose_analysis → pulse_upload → time_window → pulse_progress → pulse_results`

**`services/score_service.py`** — orchestrates sleep/arousal scoring: validates input, calls `ScoreArousability.py`, builds Excel workbooks and plots.

**`services/pulse_service.py`** — orchestrates pulse analysis: discovers CSVs, calls `ConvertAcclLogsToPlots.py`, builds aggregated metrics and plots.

**`ScoreArousability.py`** and **`ConvertAcclLogsToPlots.py`** — core algorithm modules at the project root. These contain the scientific signal-processing logic and are called directly by the services layer.

**`services/app_paths.py`** — detects PyInstaller frozen vs. source execution and resolves correct paths for resources and data output. Data writes to `Data/` in project root, with fallback to `~/Library/Application Support/FlySWATTER/Data` (macOS) or `%APPDATA%\FlySWATTER\Data` (Windows).

**`services/validators.py`** — strict regex-based validation of Zantiks filenames and accelerometer folder structures before analysis begins.

### Key Data Flow

```
User selects file/folder
  → validators.py validates format
  → main_window.py spawns FunctionWorker thread
  → score_service.py or pulse_service.py orchestrates
  → ScoreArousability.py or ConvertAcclLogsToPlots.py runs algorithm
  → results written to Data/ directory
  → results screen displays preview + download button
```

## PyInstaller Bundling

`flyswatter_gui.spec` controls the build. It bundles `arousal_score_well_mapping.xlsx` as a data file and includes hidden imports for `ConvertAcclLogsToPlots`, `ScoreArousability`, and `openpyxl`. When running bundled, `app_paths.py` detects `sys.frozen` and adjusts all path resolution accordingly.

The macOS build script creates a venv at `build/.macapp-venv` and clears extended attributes for codesign compatibility. Windows uses `--onefile`.
