"""Compatibility entry point for the XTB portfolio review skill.

The canonical implementation lives in
`skills/xtb-portfolio-review/scripts/main.py` so the skill folder can be copied
and used standalone by an LLM agent. This shim preserves the historical repo
API: `import main` and `python main.py`.
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
    / "main.py"
)


def _load_impl():
    script_dir = _IMPL_PATH.parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    spec = importlib.util.spec_from_file_location(__name__, _IMPL_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load XTB portfolio implementation at {_IMPL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[__name__] = module
    spec.loader.exec_module(module)
    return module


_impl = _load_impl()
