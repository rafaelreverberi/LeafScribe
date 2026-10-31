#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
Scripts/build_app.sh
Scripts/verify_bundle.sh
APP="$PWD/dist/LeafScribe.app"
RELEASE_DIR="$PWD/dist/release"
mkdir -p "$RELEASE_DIR"
# Only this freshly assembled bundle is distributed, never a user's library.
python3 - "$APP" <<'PY'
import pathlib, sys
app = pathlib.Path(sys.argv[1])
for path in app.rglob('*'):
    if path.is_file() and (path.suffix.lower() in {'.epub', '.pdf', '.sqlite', '.db'} or 'iTunesMetadata' in path.name):
        raise SystemExit('Unexpected document/database in release bundle')
PY
STAGING="$RELEASE_DIR/.LeafScribe-macOS-arm64.tmp.zip"
trap 'rm -f "$STAGING"' EXIT
ditto -c -k --sequesterRsrc --keepParent "$APP" "$STAGING"
mv "$STAGING" "$RELEASE_DIR/LeafScribe-macOS-arm64.zip"
(cd "$RELEASE_DIR" && shasum -a 256 LeafScribe-macOS-arm64.zip > SHA256SUMS)
printf 'Release assets: %s\n' "$RELEASE_DIR"
