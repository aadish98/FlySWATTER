# FlySWATTER — Fly Sleep-Wake Arousal Threshold Testing & Evaluation Resource

Desktop app for scoring Zantiks fly behavior data and computing pulse metrics from accelerometer logs.

Accelerometer logger setup guide: https://github.com/aadish98/Acceleration-Logger-GUI

---

## Quick start — macOS

1. Install [Git](https://git-scm.com/download/mac) and [Python 3.10+](https://www.python.org/downloads/).
2. Right-click the folder where you want the project (e.g. Desktop) -> **New Terminal at Folder**.
3. Run these commands one at a time:

```bash
git clone https://github.com/aadish98/FlySWATTER.git
cd FlySWATTER
bash BATCH_SCRIPTS/build_flyswatter_mac_app.sh
```

4. When finished, double-click `FlySWATTER.app` to launch.

> macOS may block the first launch. Right-click the app -> **Open** -> **Open**.

---

## Quick start — Windows

1. Install [Git](https://git-scm.com/download/win) and [Python 3.10+](https://www.python.org/downloads/) (check **"Add python.exe to PATH"** during install).
2. Right-click the folder where you want the project (e.g. Desktop) -> **Open in Terminal** (Windows 11) or **Shift + right-click** -> **Open PowerShell window here** (Windows 10).
3. Run these commands one at a time:

```powershell
git clone https://github.com/aadish98/FlySWATTER.git
cd FlySWATTER
powershell -ExecutionPolicy Bypass -File BATCH_SCRIPTS\build_flyswatter_windows_exe.ps1
```

4. When finished, double-click `FlySWATTER.exe` to launch.

---

## Data output

Output is saved to `Data/` inside the project folder.

Fallback if not writable:
- macOS: `~/Library/Application Support/FlySWATTER/Data`
- Windows: `%APPDATA%\FlySWATTER\Data`

---

## Run from source (advanced)

**macOS:**

```bash
python3 -m pip install -e .
python3 flyswatter_gui.py
```

**Windows:**

```powershell
py -3.10 -m pip install -e .
py -3.10 flyswatter_gui.py
```
