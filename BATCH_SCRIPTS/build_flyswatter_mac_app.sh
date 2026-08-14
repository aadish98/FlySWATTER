#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOCAL_BUILD_ROOT="${TMPDIR:-/tmp}/flyswatter-pyinstaller"
WORK_PATH="${LOCAL_BUILD_ROOT}/work"
DIST_PATH="${LOCAL_BUILD_ROOT}/dist"
BUILD_VENV="${PROJECT_DIR}/build/.macapp-venv"
ICON_PNG="${PROJECT_DIR}/assets/flyswatter_icon-new.png"

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

if [[ ! -f "${ICON_PNG}" ]]; then
  echo "Error: app icon not found at ${ICON_PNG}"
  exit 1
fi

# Content-hash the PNG so CFBundleIconFile changes whenever the artwork changes.
# PyInstaller's PNG→icns helper hashes the *path*, not contents, which leaves macOS
# showing a stale Dock/Finder icon after rebuilds.
ICON_HASH="$(shasum -a 256 "${ICON_PNG}" | awk '{print substr($1,1,12)}')"
ICONSET_PATH="${PROJECT_DIR}/build/flyswatter.iconset"
ICNS_PATH="${PROJECT_DIR}/build/flyswatter-${ICON_HASH}.icns"
APP_VERSION="$("${BUILD_VENV}/bin/python" - <<'PY'
import re
from pathlib import Path
text = Path("pyproject.toml").read_text(encoding="utf-8")
match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
print(match.group(1) if match else "0.1.0")
PY
)"
BUILD_ID="${APP_VERSION}.${ICON_HASH}.$(date +%Y%m%d%H%M%S)"

echo "Building app icon (.icns) from ${ICON_PNG}..."
rm -rf "${PROJECT_DIR}/FlySWATTER.app" "${PROJECT_DIR}/FlySWATTER" "${WORK_PATH}" "${DIST_PATH}"
rm -rf "${ICONSET_PATH}"
rm -f "${PROJECT_DIR}/build"/flyswatter-*.icns
mkdir -p "${ICONSET_PATH}"

# Prefer Apple's iconutil (multi-resolution). Fall back to Pillow if unavailable.
if command -v iconutil >/dev/null 2>&1 && command -v sips >/dev/null 2>&1; then
  for size in 16 32 128 256 512; do
    sips -z "${size}" "${size}" "${ICON_PNG}" --out "${ICONSET_PATH}/icon_${size}x${size}.png" >/dev/null
    sips -z "$((size * 2))" "$((size * 2))" "${ICON_PNG}" --out "${ICONSET_PATH}/icon_${size}x${size}@2x.png" >/dev/null
  done
  xattr -cr "${ICONSET_PATH}" 2>/dev/null || true
  if ! iconutil -c icns "${ICONSET_PATH}" -o "${ICNS_PATH}"; then
    echo "iconutil failed; falling back to Pillow for .icns conversion..."
    "${BUILD_VENV}/bin/python" - "${ICON_PNG}" "${ICNS_PATH}" <<'PY'
import sys
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
img = Image.open(src).convert("RGBA")
img.save(dst, format="ICNS", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
PY
  fi
else
  "${BUILD_VENV}/bin/python" - "${ICON_PNG}" "${ICNS_PATH}" <<'PY'
import sys
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
img = Image.open(src).convert("RGBA")
img.save(dst, format="ICNS", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
PY
fi

if [[ ! -f "${ICNS_PATH}" ]]; then
  echo "Error: failed to create ${ICNS_PATH}"
  exit 1
fi

export FLYSWATTER_ICON="${ICNS_PATH}"
export FLYSWATTER_VERSION="${APP_VERSION}"
export FLYSWATTER_BUILD="${BUILD_ID}"

echo "Clearing extended attributes that can break codesign..."
# .git is skipped: loose objects are read-only, so clearing attributes on them
# fails, and nothing under .git is ever copied into the bundle anyway.
find "${PROJECT_DIR}" -name .git -prune -o -print0 \
  | xargs -0 xattr -c 2>/dev/null || true

echo "Building FlySWATTER.app with local temporary paths..."
"${BUILD_VENV}/bin/python" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "${DIST_PATH}" \
  --workpath "${WORK_PATH}" \
  "${PROJECT_DIR}/flyswatter_gui.spec"

if [[ ! -d "${DIST_PATH}/FlySWATTER.app" ]]; then
  echo "Error: build did not produce ${DIST_PATH}/FlySWATTER.app"
  exit 1
fi

echo "Copying app bundle to project root..."
ditto "${DIST_PATH}/FlySWATTER.app" "${PROJECT_DIR}/FlySWATTER.app"
xattr -cr "${PROJECT_DIR}/FlySWATTER.app"

APP_BUNDLE="${PROJECT_DIR}/FlySWATTER.app"
INFO_PLIST="${APP_BUNDLE}/Contents/Info.plist"

# Ensure Finder uses the icon basename without extension (Apple convention).
ICON_BASENAME="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "${INFO_PLIST}" 2>/dev/null || true)"
if [[ -n "${ICON_BASENAME}" && "${ICON_BASENAME}" == *.icns ]]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile ${ICON_BASENAME%.icns}" "${INFO_PLIST}"
fi

echo "Refreshing Launch Services / icon caches so the new icon appears..."
touch "${APP_BUNDLE}" "${INFO_PLIST}"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
if [[ -x "${LSREGISTER}" ]]; then
  "${LSREGISTER}" -f "${APP_BUNDLE}" >/dev/null || true
fi
rm -rf "${HOME}/Library/Caches/com.apple.iconservices.store" 2>/dev/null || true
rm -f "${HOME}/Library/Caches/com.apple.dock.iconcache" 2>/dev/null || true
find "${HOME}/Library/Caches" -name 'com.apple.iconservices*' -prune -exec rm -rf {} + 2>/dev/null || true
killall Dock 2>/dev/null || true

echo
echo "Build complete:"
echo "  ${APP_BUNDLE}"
echo "  version ${APP_VERSION} (build ${BUILD_ID})"
echo "Quit any running FlySWATTER instance before launching the new build."
echo "If Finder still shows the old icon, move the app or toggle its view and wait a moment."
