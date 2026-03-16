# FlySWATTER

FlySWATTER is a desktop app for:

- **Scoring Zantiks Fly Behavior Data**
- **Computing Pulse Metrics from Accelerometer Log Data**

To learn how to set-up an accelerometer logger system on Zantiks (or similar vibration-stimulus platforms such as vortexer), see: https://github.com/aadish98/Acceleration-Logger-GUI

## macOS

### Run packaged app

If `FlySWATTER.app` is present in the project root, double-click it.

### Build `FlySWATTER.app`

Run on macOS from the project root:

```bash
bash BATCH_SCRIPTS/build_flyswatter_mac_app.sh
```

Output:

```text
FlySWATTER.app
```

Notes:

- Build must be done on macOS.
- Internal unsigned builds may require first-run right-click **Open**.

### Run from source (technical)

Requires Python 3.10+:

```bash
python3 -m pip install -e .
python3 flyswatter_gui.py
```

### Data path behavior on macOS

Primary output location:

```text
Data/
```

If `Data/` in the project root is not writable, fallback location is:

```text
~/Library/Application Support/FlySWATTER/Data
```

## Windows

### Run packaged app

If `FlySWATTER.exe` is present in the project root, double-click it.

### Build `FlySWATTER.exe`

Run on Windows from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File BATCH_SCRIPTS/build_flyswatter_windows_exe.ps1
```

Output:

```text
FlySWATTER.exe
```

Notes:

- Build must be done on Windows.
- Script creates a local build venv under `build/.winexe-venv`.

### Run from source (technical)

Requires Python 3.10+:

```powershell
py -3.10 -m pip install -e .
py -3.10 flyswatter_gui.py
```

### Data path behavior on Windows

Primary output location:

```text
Data/
```

If `Data/` in the project root is not writable, fallback location is:

```text
%APPDATA%/FlySWATTER/Data
```

## Shared Output Structure

- `Data/<ResearcherName>/<MM-DD-YY T-HH:MMAM/PM>/`
- `Data/researchers.json`

## Minimal Dev Commands

Install dev tooling:

```bash
python3 -m pip install -e ".[dev]"
```

Run tests:

```bash
python3 -m pytest
```
