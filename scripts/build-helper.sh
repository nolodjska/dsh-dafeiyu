#!/usr/bin/env bash
# Build the macOS BigFish helper as a single-file PyInstaller binary.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${DSH_DAFEIYU_BUILD_PYTHON:-python3}"
ENTRY="$PROJECT_ROOT/runtime/helper.py"
ASSETS="$PROJECT_ROOT/assets"
ARCH="$(uname -m)"
if [[ "$ARCH" != "arm64" ]]; then
  echo "This build targets macOS Apple Silicon (arm64); detected $ARCH" >&2
  exit 1
fi
OUTPUT="$PROJECT_ROOT/runtime/bin/darwin-$ARCH"
WORK="$PROJECT_ROOT/.build/helper"

mkdir -p "$OUTPUT" "$WORK"

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --console \
  --name dsh-dafeiyu-helper \
  --distpath "$OUTPUT" \
  --workpath "$WORK" \
  --specpath "$WORK" \
  --add-data "$ASSETS:assets" \
  --paths "$PROJECT_ROOT/runtime" \
  "$ENTRY"

echo "$OUTPUT/dsh-dafeiyu-helper"
