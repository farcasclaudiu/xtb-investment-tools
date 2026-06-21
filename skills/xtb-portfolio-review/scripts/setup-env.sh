#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-.venv}"
PYTHON_BOOTSTRAP="${PYTHON:-python3}"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON_BOOTSTRAP" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"

echo "Environment ready: $VENV_DIR"
