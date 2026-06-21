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

PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" - <<PY
import importlib.util
from pathlib import Path

script = Path("$SCRIPT_DIR") / "exporter.py"
spec = importlib.util.spec_from_file_location("xtb_portfolio_performance_exporter", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.PORTFOLIO_FIELDS[0] == "Date"
assert module.ACCOUNT_FIELDS[0] == "Date"
print("Portfolio Performance exporter loaded")
PY
