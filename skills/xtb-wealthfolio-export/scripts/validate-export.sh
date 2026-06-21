#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="python3"
fi

"$PYTHON_BIN" - <<PY
import importlib.util
import sys
from pathlib import Path

script_dir = Path("$SCRIPT_DIR")
sys.path.insert(0, str(script_dir))

for module in ("pandas", "openpyxl"):
    if importlib.util.find_spec(module) is None:
        raise SystemExit(
            f"Missing dependency: {module}. Install with: "
            f"{sys.executable} -m pip install -r {script_dir / 'requirements.txt'}"
        )

import exporter

required = ["date", "symbol", "quantity", "activityType", "unitPrice", "currency", "fee", "amount"]
if exporter.FIELDS != required:
    raise SystemExit(f"Unexpected Wealthfolio fields: {exporter.FIELDS}")

print("XTB Wealthfolio export skill tools are importable.")
PY
