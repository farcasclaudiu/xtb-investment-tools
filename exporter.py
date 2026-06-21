"""Compatibility entry point for the XTB to Wealthfolio export skill.

The canonical implementation lives in
`skills/xtb-wealthfolio-export/scripts/exporter.py` so the skill folder can be
copied and used standalone by an LLM agent. This shim preserves the historical
repo API: `import exporter` and `python exporter.py`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_IMPL_PATH = (
    Path(__file__).resolve().parent
    / "skills"
    / "xtb-wealthfolio-export"
    / "scripts"
    / "exporter.py"
)


def _load_impl():
    module_name = __name__ if __name__ != "__main__" else "_xtb_wealthfolio_exporter_impl"
    script_dir = _IMPL_PATH.parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    spec = importlib.util.spec_from_file_location(module_name, _IMPL_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load XTB Wealthfolio implementation at {_IMPL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    if __name__ != "__main__":
        sys.modules[__name__] = module
    spec.loader.exec_module(module)
    return module


_impl = _load_impl()

if __name__ == "__main__":
    _impl.main_cli()
