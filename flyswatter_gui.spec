# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("arousal_score_well_mapping.xlsx", "."),
]
datas += collect_data_files("matplotlib", include_py_files=False)

hiddenimports = [
    "ConvertAcclLogsToPlots",
    "ScoreArousability",
    "openpyxl.styles",
]
hiddenimports += collect_submodules("matplotlib.backends")


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
    icon=None,
    bundle_identifier="edu.umich.rallada.flyswatter",
)
