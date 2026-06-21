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

for module in ("pandas", "openpyxl", "yfinance"):
    if importlib.util.find_spec(module) is None:
        raise SystemExit(
            f"Missing dependency: {module}. Install with: "
            f"{sys.executable} -m pip install -r {script_dir / 'requirements.txt'}"
        )

import main
import html_charts

if not html_charts.CHARTJS_PATH.exists():
    raise SystemExit(f"Missing Chart.js asset: {html_charts.CHARTJS_PATH}")

print("XTB portfolio review skill tools are importable.")
PY
