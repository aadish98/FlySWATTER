# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("arousal_score_well_mapping.xlsx", "."),
    ("zantiks_filename_format.html", "."),
]
datas += collect_data_files("matplotlib", include_py_files=False)

hiddenimports = [
    "ConvertAcclLogsToPlots",
    "ConvertMonitorLogsToPlots",
    "ScoreArousability",
    "openpyxl.styles",
]
hiddenimports += collect_submodules("matplotlib.backends")

# Build scripts set these so icon/version changes invalidate macOS icon caches.
# Fallback keeps `pyinstaller flyswatter_gui.spec` working without the wrapper.
icon_file = os.environ.get("FLYSWATTER_ICON", "assets/flyswatter_icon-new.png")
app_version = os.environ.get("FLYSWATTER_VERSION", "0.1.0")
app_build = os.environ.get("FLYSWATTER_BUILD", app_version)


a = Analysis(
    ["flyswatter_gui.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FlySWATTER",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FlySWATTER",
)

app = BUNDLE(
    coll,
    name="FlySWATTER.app",
    icon=icon_file,
    bundle_identifier="edu.umich.rallada.flyswatter",
    info_plist={
        "CFBundleShortVersionString": app_version,
        "CFBundleVersion": app_build,
    },
)
