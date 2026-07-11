"""
Test infrastructure for Autona.

NOTE: src/ modules import chain triggers QApplication + all singletons via
src/__init__.py -> Autona.py. To avoid this in unit tests,
use direct_import() to load individual modules by file path.

See: .claude/architecture/singleton-init-order.md
"""
import importlib.util
import sys
from pathlib import Path

# Keep test runs from writing __pycache__/*.pyc for src modules. main.py
# sets this for the app, but pytest imports src files without it and the
# leftover .pyc can mask source edits when Windows mtimes lag
# (agent-pitfalls #12).
sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"


def direct_import(module_name: str, file_path: str):
    """Import a single .py file without triggering the src package chain.

    Usage:
        mod = direct_import("AutonaDictHelper", "src/Helpers/AutonaDictHelper.py")
        deep_get = mod.deep_get
    """
    abs_path = PROJECT_ROOT / file_path
    spec = importlib.util.spec_from_file_location(module_name, abs_path)
    mod = importlib.util.module_from_spec(spec)
    # Prevent the module from being found via normal import later
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod
