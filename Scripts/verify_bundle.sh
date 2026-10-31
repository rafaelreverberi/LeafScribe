#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
APP="$PWD/dist/LeafScribe.app"
ROOT="$PWD/output/verification/bundle-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ROOT"
codesign --verify --deep --strict "$APP"
PATH=/usr/bin:/bin "$APP/Contents/MacOS/LeafScribe" --smoke-test "$ROOT"
grep -q '^PASS:' "$ROOT/result.txt"
cat "$ROOT/result.txt"
printf 'Artifacts: %s\n' "$ROOT"
