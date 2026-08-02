"""Test infrastructure for Autona.

Since Step 3 of the startup redesign, `src/__init__.py` is empty and no src
module builds anything at import time, so tests import normally
(`from src.X import Y`). The old workaround -- loading a single .py by file
path to dodge the package chain -- is gone with it, and so is the helper
this module used to export for it.

The one thing still worth guarding is import side effects themselves; that
is tests/test_import_side_effects.py.
"""
import os
import sys
from pathlib import Path

import pytest

# Keep test runs from writing __pycache__/*.pyc for src modules. main.py
# sets this for the app, but pytest imports src files without it and the
# leftover .pyc can mask source edits when Windows mtimes lag
# (agent-pitfalls #12).
sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

# Same binding as the app (main.py), and no window ever reaches a screen.
os.environ.setdefault("QT_API", "pyside6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """The one Qt application object for the whole test session.

    Qt allows exactly one per process, and a QWidget needs it to be a
    QApplication -- a bare QCoreApplication built by whichever Qt test ran
    first would take every later widget test down with it. So all of them
    share this fixture.
    """
    from qtpy.QtWidgets import QApplication  # local: non-Qt tests skip the cost
    return QApplication.instance() or QApplication([])
