"""Import side-effect regression guard for the migrated singletons.

Step 3 of the startup redesign moves every singleton construction out of
module level and into src/bootstrap.py. This test pins down what has already
been migrated: importing StateManager / AutonaHotkeys must not build a
QApplication, must not create the AutonaHotkeys instance, and must not touch
the filesystem below the current working directory (./out).

It runs in a subprocess because the checks are about a *fresh* interpreter:
once any other test has imported these modules, the side effects (or their
absence) can no longer be observed. conftest's direct_import() cannot be used
either -- both modules use relative imports, so they need a parent package.
The subprocess therefore registers a bare types.ModuleType("src") with a
__path__ pointing at src/, which makes `src.StateManager` importable *without*
executing src/__init__.py (that would pull in Autona.py and boot the whole
app; agent-pitfalls #1).
"""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

# Modules whose import must be side-effect free, in migration order.
_CHECK_SCRIPT = r'''
import importlib
import os
import sys
import types

sys.dont_write_bytecode = True

SRC_ROOT = sys.argv[1]

# Fake parent package: gives the relative imports inside the modules a
# package to resolve against without running src/__init__.py.
pkg = types.ModuleType("src")
pkg.__path__ = [SRC_ROOT]
sys.modules["src"] = pkg

state_manager = importlib.import_module("src.StateManager").StateManager
hotkeys = importlib.import_module("src.AutonaHotkeys").AutonaHotkeys

from qtpy.QtWidgets import QApplication

assert QApplication.instance() is None, "import created a QApplication"
assert hotkeys.instance is None, "import created AutonaHotkeys.instance"
assert not os.path.exists("./out"), "import created ./out"

# Positive control: the work simply moved to the explicit entry point, so
# the test cannot pass just because the code stopped working.
state_manager.Initialize()
assert os.path.isdir("./out"), "Initialize() did not create ./out"

print("OK")
'''


def test_core_modules_have_no_import_side_effects(tmp_path):
    # SettingsManager is not migrated yet: it reads/writes
    # ./user_data/settings.json at import time, and StateManager imports it.
    # Pre-creating the directory keeps that known remaining side effect from
    # failing the run; it is deliberately not asserted against here.
    (tmp_path / "user_data").mkdir()

    env = dict(os.environ)
    env["QT_API"] = "pyside6"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    # Never let a headless CI/agent shell pop a real Qt platform plugin.
    env.setdefault("QT_QPA_PLATFORM", "offscreen")

    result = subprocess.run(
        [sys.executable, "-B", "-c", _CHECK_SCRIPT, str(SRC_ROOT)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"side-effect check failed\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}")
    assert "OK" in result.stdout
