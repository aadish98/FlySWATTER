Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Resolve-Path (Join-Path $scriptDir "..")
$buildVenv = Join-Path $projectDir "build\.winexe-venv"
$workPath = Join-Path $projectDir "build\pyinstaller_windows"
$outputExe = Join-Path $projectDir "FlySWATTER.exe"

if ($env:OS -ne "Windows_NT") {
    throw "This script must be run on Windows."
}

function Get-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{
            Exe = "py"
            Args = @("-3.10")
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{
            Exe = "python"
            Args = @()
        }
    }
    throw "Could not find 'py' or 'python' on PATH."
}

$pythonCmd = Get-PythonCommand

Write-Host "Preparing local Windows build environment..."
New-Item -ItemType Directory -Force -Path (Join-Path $projectDir "build") | Out-Null
if (-not (Test-Path (Join-Path $buildVenv "Scripts\python.exe"))) {
    & $pythonCmd.Exe @($pythonCmd.Args + @("-m", "venv", $buildVenv))
}

$venvPython = Join-Path $buildVenv "Scripts\python.exe"

Write-Host "Installing dependencies from pyproject.toml..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e ".[dev]"

Write-Host "Removing prior Windows build artifacts..."
if (Test-Path $workPath) {
    Remove-Item -Recurse -Force $workPath
}
if (Test-Path $outputExe) {
    Remove-Item -Force $outputExe
}

$entrypoint = Join-Path $projectDir "flyswatter_gui.py"
$mappingFile = Join-Path $projectDir "arousal_score_well_mapping.xlsx"
$filenameGuide = Join-Path $projectDir "zantiks_filename_format.html"
$iconPng = Join-Path $projectDir "assets\flyswatter_icon-new.png"
$iconFile = Join-Path $projectDir "build\flyswatter.ico"

if (-not (Test-Path $iconPng)) {
    throw "App icon not found at $iconPng"
}

# Build a multi-size .ico from the source PNG. PyInstaller's PNG→ICO helper hashes the
# *path* (not contents), so Explorer can keep showing a stale icon after rebuilds.
Write-Host "Building app icon (.ico) from $iconPng..."
& $venvPython -c @"
from pathlib import Path
from PIL import Image

png = Path(r'$iconPng')
ico = Path(r'$iconFile')
ico.parent.mkdir(parents=True, exist_ok=True)
img = Image.open(png).convert('RGBA')
sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
img.save(ico, sizes=sizes)
print(f'Wrote {ico}')
"@

Write-Host "Building FlySWATTER.exe in project root..."
& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "FlySWATTER" `
    --distpath $projectDir `
    --workpath $workPath `
    --icon $iconFile `
    --hidden-import "ConvertAcclLogsToPlots" `
    --hidden-import "ScoreArousability" `
    --hidden-import "openpyxl.styles" `
    --collect-submodules "matplotlib.backends" `
    --add-data "$mappingFile;." `
    --add-data "$filenameGuide;." `
    $entrypoint

if (-not (Test-Path $outputExe)) {
    throw "Build completed but FlySWATTER.exe was not found at $outputExe"
}

Write-Host ""
Write-Host "Build complete:"
Write-Host "  $outputExe"
