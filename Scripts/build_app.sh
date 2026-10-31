#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${CONFIGURATION:-release}"
APP_VERSION="$(cat VERSION)"
[[ "$APP_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Invalid VERSION" >&2; exit 1; }
# Packaging is performed on the target CPU architecture. No shell is used by the app at runtime.
if [ ! -x .build/packaging/bin/python ]; then
    uv venv .build/packaging --python "${PYTHON_FOR_BUILD:-python3}"
fi
uv pip install --python .build/packaging/bin/python 'pyinstaller==6.16.0' 'pyinstaller-hooks-contrib==2026.9'
.build/packaging/bin/python -m PyInstaller --noconfirm --clean --onefile \
    --name leafscribe-engine --paths Engine/vendor --paths Engine \
    --exclude-module ssl --exclude-module _ssl \
    --distpath .build/helper-dist --workpath .build/helper-work --specpath .build Engine/main.py
cp THIRD_PARTY_NOTICES.md Apps/macOS/Resources/THIRD_PARTY_NOTICES.md
swift build -c "$MODE"
BIN_DIR="$(swift build -c "$MODE" --show-bin-path)"
APP="$PWD/dist/LeafScribe.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/Helpers"
cp "$BIN_DIR/LeafScribe" "$APP/Contents/MacOS/LeafScribe"
# Both SwiftPM build systems are supported by checking the actual resource name.
for resource in "$BIN_DIR"/LeafScribe_LeafScribe.bundle "$BIN_DIR"/LeafScribe_LeafScribe.resources; do
    if [ -d "$resource" ]; then
        rm -rf "$APP/Contents/Resources/$(basename "$resource")"
        cp -R "$resource" "$APP/Contents/Resources/"
    fi
done
rm -rf "$APP/Contents/Helpers/leafscribe-engine"
cp .build/helper-dist/leafscribe-engine "$APP/Contents/Helpers/"
cp -R Licenses "$APP/Contents/Resources/"
cp LICENSE THIRD_PARTY_NOTICES.md "$APP/Contents/Resources/"
# The packaged Python interpreter's own license is authoritative for that build.
.build/packaging/bin/python - "$APP/Contents/Resources/Licenses" <<'PY'
import pathlib, shutil, sys
base = pathlib.Path(sys.base_prefix)
target = pathlib.Path(sys.argv[1])
candidates = [base / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/LICENSE.txt', base / 'LICENSE.txt', base / 'LICENSE']
license = next((p for p in candidates if p.is_file()), candidates[0])
if not license.is_file():
    raise SystemExit('Python distribution license missing; add it before packaging')
shutil.copyfile(license, target / 'Python-LICENSE.txt')
PY
swift Scripts/make_icon.swift .build/LeafScribe.iconset
iconutil -c icns .build/LeafScribe.iconset -o "$APP/Contents/Resources/LeafScribe.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>org.leafscribe.mac</string>
<key>CFBundleName</key><string>LeafScribe</string>
<key>CFBundleDisplayName</key><string>LeafScribe</string>
<key>CFBundleExecutable</key><string>LeafScribe</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>$APP_VERSION</string>
<key>CFBundleVersion</key><string>3</string>
<key>CFBundleIconFile</key><string>LeafScribe</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSPrincipalClass</key><string>NSApplication</string>
<key>NSHumanReadableCopyright</key><string>Copyright © 2026 LeafScribe contributors. Includes beook-export © 2026 JoGe07.</string>
</dict></plist>
PLIST
# Ad-hoc signing for local use; a distributor may replace this with Developer ID.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
printf 'Built %s\n' "$APP"
