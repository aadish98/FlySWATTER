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

## App updates (agent trigger)

**Read `AGENTS.md` first.** It is the source of truth for the update flow and for the rule that users must not edit the app source (custom scripts go in `user_scripts/`).

Whenever the user asks to update the app (e.g. "update the app", "pull the latest", "rebuild with latest"), do this without asking for confirmation:

0. Save custom scripts into `user_scripts/` and stash accidental edits to tracked files, as described in `AGENTS.md` section 3.
1. `git pull --ff-only` to fetch the latest code from GitHub.
2. Rebuild for the user's OS:
   - **macOS:** `bash BATCH_SCRIPTS/build_flyswatter_mac_app.sh`
   - **Windows:** `powershell -ExecutionPolicy Bypass -File BATCH_SCRIPTS\build_flyswatter_windows_exe.ps1`
3. Tell the user where the new build is (`FlySWATTER.app` or `FlySWATTER.exe`) and to quit any running instance before launching it.

If the macOS build script fails on `xattr` under `.git`, finish the PyInstaller build/copy steps manually (skip project-wide `xattr -cr`, then build and copy the app).

## Architecture

### Entry Point → GUI → Services → Analysis

**`flyswatter_gui.py`** — sets `OPENBLAS_NUM_THREADS=1` (numpy/Qt compatibility), creates QApplication with dark theme, launches main window.

**`gui/main_window.py`** — central hub using `QStackedWidget` to manage 11 screens as a state machine. Owns `AppState`, runs analysis in background threads via `workers.py`, and routes navigation signals.

**`gui/app_state.py`** — shared mutable state passed between screens: researcher name, selected files, genotype mappings, and analysis results (`ScoreAnalysisResult`, `PulseAnalysisResult`, `GeneralPulseAnalysisResult`, general-mode windows and clock offset).

**`gui/screens/`** — 13 screen classes, three independent flows:
- Score flow: `welcome → choose_analysis → score_upload → well_mapping → sleep_definition → score_progress → score_results`
- Pulse flow: `welcome → choose_analysis → pulse_upload → time_window → pulse_progress → pulse_results`
- General Pulse Metrics flow (third card; `MainWindow._general_mode` reuses `pulse_upload` and `pulse_progress`): `welcome → choose_analysis → pulse_upload → analysis_range → pulse_progress → general_pulse_results`. `analysis_range_screen.py` lets users add/remove non-overlapping analysis windows (range slider + start/end edits) and enter a "Recording start (clock correction)". `general_pulse_results_screen.py` shows per-window + Combined stats, the two plots, an "Edit windows" button, and "Apply and re-render" for changing the clock offset without re-reading logs.

**`services/score_service.py`** — orchestrates sleep/arousal scoring: validates input, calls `ScoreArousability.py`, builds Excel workbooks and plots. Optionally accepts a `monitor_file` (TriKinetics DAM/DEnM `Monitor*.txt` log); when supplied, temperature (the protocol plot line + per-pulse `Temperature (°C)` values) is taken from the monitor log instead of the Zantiks file's `INT_TEMP1` column.

**`services/pulse_service.py`** — orchestrates pulse analysis: discovers CSVs, calls `ConvertAcclLogsToPlots.py`, builds aggregated metrics and plots.

**General Pulse Metrics (in `services/pulse_service.py`)** — `run_general_pulse_analysis(folder, output_dir, windows=[AnalysisWindow...], clock_offset_seconds)` detects every pulse in each window with `detect_all_pulses` (`ConvertAcclLogsToPlots.py`; tiled 30-min cores with 5-min padding, one `_RunAggregator` per window, general-mode defaults 8σ / 0.1 s), and reports per-pulse metrics plus n/mean/SD/median/min/max/CV per window and Combined (workbook sheets Summary, Pulses, Hourly, Windows, Settings; aggregated + distributions PNGs; zip). Results are stored in manifest-clock time; `clock_offset_seconds` is applied only at render time, so `rerender_general_pulse_outputs(result, offset)` regenerates outputs without reading logs. Windows are entered in corrected-clock time and the service subtracts the offset before reading. Helpers: `normalize_analysis_windows` (sort, label, reject overlap), `estimate_general_file_count`. Input is validated by `validators.validate_accel_folder_general` (manifest + `t_ms`,`X`,`Y`,`Z` columns, no filename rules). Result model: `GeneralPulseAnalysisResult` / `GeneralWindowResult` in `services/models.py`.

**`ScoreArousability.py`** and **`ConvertAcclLogsToPlots.py`** — core algorithm modules at the project root. These contain the scientific signal-processing logic and are called directly by the services layer.

**`ConvertMonitorLogsToPlots.py`** — parses TriKinetics DAM/DEnM `Monitor*.txt` environment-monitor logs (`read_monitor_temperature_file`) and renders a standalone temperature protocol plot (`plot_monitor_temperature`, runnable via `python3 ConvertMonitorLogsToPlots.py Monitor65.txt`). Also exposes `get_monitor_temperature_at` for interpolating a temperature at an arbitrary wall-clock time, used by `score_service.py` to override per-pulse temperatures when a monitor file is supplied.

**`services/temperature_plot.py`** — shared matplotlib renderer (`render_temperature_protocol_plot`) for the "Temperature and Light/Dark Protocol" chart style, used by both `score_service._plot_protocol` and `ConvertMonitorLogsToPlots.plot_monitor_temperature` so the two stay visually identical.

**`services/app_paths.py`** — detects PyInstaller frozen vs. source execution and resolves correct paths for resources and data output. Data writes to `Data/` in project root, with fallback to `~/Library/Application Support/FlySWATTER/Data` (macOS) or `%APPDATA%\FlySWATTER\Data` (Windows).

**`services/validators.py`** — strict regex-based validation of Zantiks filenames and accelerometer folder structures before analysis begins. The optional monitor temperature log only requires a `.txt` extension (`validate_monitor_file`).

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

`flyswatter_gui.spec` controls the build. It bundles `arousal_score_well_mapping.xlsx` as a data file and includes hidden imports for `ConvertAcclLogsToPlots`, `ConvertMonitorLogsToPlots`, `ScoreArousability`, and `openpyxl`. When running bundled, `app_paths.py` detects `sys.frozen` and adjusts all path resolution accordingly.

The macOS build script creates a venv at `build/.macapp-venv` and clears extended attributes for codesign compatibility. Windows uses `--onefile`.
