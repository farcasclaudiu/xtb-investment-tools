"""Compatibility import for the portfolio review chart helpers.

The canonical implementation lives in
`skills/xtb-portfolio-review/scripts/html_charts.py`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_IMPL_PATH = (
    Path(__file__).resolve().parent
    / "skills"
    / "xtb-portfolio-review"
    / "scripts"
    / "html_charts.py"
)


def _load_impl():
    spec = importlib.util.spec_from_file_location(__name__, _IMPL_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load HTML chart implementation at {_IMPL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[__name__] = module
    spec.loader.exec_module(module)
    return module


_load_impl()
