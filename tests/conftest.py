"""Test infrastructure for Autona.

Since Step 3 of the startup redesign, `src/__init__.py` is empty and no src
module builds anything at import time, so tests import normally
(`from src.X import Y`). The old workaround -- loading a single .py by file
path to dodge the package chain -- is gone with it, and so is the helper
this module used to export for it.

The one thing still worth guarding is import side effects themselves; that
is tests/test_import_side_effects.py.
"""
import sys
from pathlib import Path

# Keep test runs from writing __pycache__/*.pyc for src modules. main.py
# sets this for the app, but pytest imports src files without it and the
# leftover .pyc can mask source edits when Windows mtimes lag
# (agent-pitfalls #12).
sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
