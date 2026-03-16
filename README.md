# FlySWATTER

FlySWATTER is a desktop app for:

- **Scoring Zantiks Fly Behavior Data**
- **Computing Pulse Metrics from Accelerometer Log Data**

To learn how to set up an accelerometer logger system on Zantiks (or similar vibration-stimulus platforms such as vortexer), see: https://github.com/aadish98/Acceleration-Logger-GUI

---

## Prerequisites

Install these before proceeding:

| Requirement | Download |
|---|---|
| **Git** | macOS: https://git-scm.com/download/mac · Windows: https://git-scm.com/download/win |
| **Python 3.10+** | https://www.python.org/downloads/ |

> **Windows Python installer tip:** Check **"Add python.exe to PATH"** during installation.

---

## Download the project

You only need to do this once per machine.

### macOS

1. Open **Finder**, navigate to where you want the project (e.g. Desktop).
2. Right-click the folder and select **New Terminal at Folder**.
3. Run:

```bash
git clone https://github.com/aadish98/FlySWATTER.git
cd FlySWATTER
```

### Windows

1. Open **File Explorer**, navigate to where you want the project (e.g. Desktop).
2. Right-click inside the folder and select **Open in Terminal** (or **Open PowerShell window here**).
3. Run:

```powershell
git clone https://github.com/aadish98/FlySWATTER.git
cd FlySWATTER
```

After cloning, you should see a `FlySWATTER` folder containing `README.md`, `BATCH_SCRIPTS/`, and other project files.

---

## macOS

### Option A — Run the packaged app

If `FlySWATTER.app` already exists in the project folder, double-click it to launch.

> First launch: macOS may block unsigned apps. Right-click the app and choose **Open**, then click **Open** in the dialog.

### Option B — Build `FlySWATTER.app` from source

Open a terminal **inside the `FlySWATTER` folder** (right-click the folder in Finder -> **New Terminal at Folder**), then run:

```bash
bash BATCH_SCRIPTS/build_flyswatter_mac_app.sh
```

When the script finishes, `FlySWATTER.app` will appear in the project folder. Double-click it to launch.

### Option C — Run directly from source (advanced)

Open a terminal inside the `FlySWATTER` folder, then run:

```bash
python3 -m pip install -e .
python3 flyswatter_gui.py
```

### Data output location (macOS)

FlySWATTER saves output to `Data/` inside the project folder. If that location is not writable, it falls back to:

```text
~/Library/Application Support/FlySWATTER/Data
```

---

## Windows

### Option A — Run the packaged app

If `FlySWATTER.exe` already exists in the project folder, double-click it to launch.

### Option B — Build `FlySWATTER.exe` from source

Open a terminal **inside the `FlySWATTER` folder** (right-click the folder in File Explorer -> **Open in Terminal**), then run:

```powershell
powershell -ExecutionPolicy Bypass -File BATCH_SCRIPTS\build_flyswatter_windows_exe.ps1
```

When the script finishes, `FlySWATTER.exe` will appear in the project folder. Double-click it to launch.

### Option C — Run directly from source (advanced)

Open a terminal inside the `FlySWATTER` folder, then run:

```powershell
py -3.10 -m pip install -e .
py -3.10 flyswatter_gui.py
```

### Data output location (Windows)

FlySWATTER saves output to `Data\` inside the project folder. If that location is not writable, it falls back to:

```text
%APPDATA%\FlySWATTER\Data
```

---

## Output structure

All scored/processed data is saved under:

```text
Data/<ResearcherName>/<MM-DD-YY T-HH:MMAM/PM>/
```

Researcher names are stored in `Data/researchers.json`.

---

## Developer commands

Install dev dependencies:

```bash
python3 -m pip install -e ".[dev]"
```

Run tests:

```bash
python3 -m pytest
```
