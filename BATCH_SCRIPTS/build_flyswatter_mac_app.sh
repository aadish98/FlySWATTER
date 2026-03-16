#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOCAL_BUILD_ROOT="${TMPDIR:-/tmp}/flyswatter-pyinstaller"
WORK_PATH="${LOCAL_BUILD_ROOT}/work"
DIST_PATH="${LOCAL_BUILD_ROOT}/dist"
BUILD_VENV="${PROJECT_DIR}/build/.macapp-venv"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Error: macOS is required to build FlySWATTER.app"
  exit 1
fi

cd "${PROJECT_DIR}"

echo "Preparing local build environment..."
mkdir -p "${PROJECT_DIR}/build"
if [[ ! -x "${BUILD_VENV}/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${BUILD_VENV}"
fi

"${BUILD_VENV}/bin/python" -m pip install --upgrade pip
echo "Installing build/runtime dependencies from pyproject.toml..."
"${BUILD_VENV}/bin/python" -m pip install -e ".[dev]"

echo "Removing previous app bundle artifacts and temporary build folders..."
rm -rf "${PROJECT_DIR}/FlySWATTER.app" "${PROJECT_DIR}/FlySWATTER" "${WORK_PATH}" "${DIST_PATH}"

echo "Clearing extended attributes that can break codesign..."
xattr -cr "${PROJECT_DIR}"

echo "Building FlySWATTER.app with local temporary paths..."
"${BUILD_VENV}/bin/python" -m PyInstaller \
  --noconfirm \
  --distpath "${DIST_PATH}" \
  --workpath "${WORK_PATH}" \
  "${PROJECT_DIR}/flyswatter_gui.spec"

if [[ ! -d "${DIST_PATH}/FlySWATTER.app" ]]; then
  echo "Error: build did not produce ${DIST_PATH}/FlySWATTER.app"
  exit 1
fi

echo "Copying app bundle to project root..."
cp -R "${DIST_PATH}/FlySWATTER.app" "${PROJECT_DIR}/FlySWATTER.app"
xattr -cr "${PROJECT_DIR}/FlySWATTER.app"

echo
echo "Build complete:"
echo "  ${PROJECT_DIR}/FlySWATTER.app"
