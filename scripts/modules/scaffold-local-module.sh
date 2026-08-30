#!/usr/bin/env bash
# Creates a minimal SB_MODULES Python file outside the repo.
set -euo pipefail
OUT_DIR="${1:-${HOME}/suggest-bridge-modules}"
CLASS_NAME="${CLASS_NAME:-HelloModule}"
FILE_NAME="${FILE_NAME:-hello_module.py}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="${REPO_ROOT}/examples/local_module_template/hello_module.py"
mkdir -p "$OUT_DIR"
DEST="${OUT_DIR}/${FILE_NAME}"
cp "$TEMPLATE" "$DEST"
echo "Created: $DEST"
echo
echo "Add to .env:"
echo "SB_MODULES=${DEST}:${CLASS_NAME}"
echo
echo "Validate: python -m bot.core.module_loader"
