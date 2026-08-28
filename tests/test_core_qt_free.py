"""The core layer must never import Qt (ADR-004 D1, headless core Step 1).

`src/core` holds the state layer and, as the migration proceeds, the domain
logic and the state schema. Its whole value rests on one property: it can be
imported, exercised and tested without a Qt binding -- no QApplication, no
GUI thread, none of the Shiboken wrapper-lifetime hazards
(agent-pitfalls #13-#15). That property is invisible in review (one
`from qtpy.QtCore import Signal` slipped into a core module breaks it and
nothing else fails), so it is pinned here instead.

Same subprocess technique as tests/test_import_side_effects.py, and for the
same reason: the check is about a *fresh* interpreter. Once any other test
has imported a widget module, PySide6 is in sys.modules for the rest of the
session and the question can no longer be asked.

The import is deliberately done with QT_API set and PySide6 installed, so a
pass means "core does not import Qt", not "Qt was unavailable".

Every module under src/core is discovered from the filesystem rather than
listed here. A list would only cover the modules somebody remembered to add
to it, while the module at risk is always the newest one -- and a new core
module that nothing imports yet would otherwise be checked by nothing at all.
Verified by construction: a throwaway core module importing qtpy fails this,
and so does one that imports a Qt-carrying module without naming Qt itself
(the case the source-reading check below cannot see).
"""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = PROJECT_ROOT / "src" / "core"

# Qt shows up under several top-level names: the binding itself (PySide6),
# the abstraction layer the app imports through (qtpy), and the binding
# generator's runtime (shiboken6). Submodules are covered by the prefix
# match -- `PySide6.QtCore` starts with `PySide6`.
_QT_ROOTS = ("PySide6", "PyQt5", "PyQt6", "qtpy", "shiboken6", "shiboken2")


def _core_source_files():
    """Every source file under src/core, __pycache__ excluded."""
    return [path for path in sorted(CORE_ROOT.rglob("*.py"))
            if "__pycache__" not in path.parts]


def _core_modules():
    """Dotted names for every module under src/core, packages first.

    Discovered rather than listed: a hand-written list only pins the modules
    somebody remembered to add to it, and the one module that matters is
    always the new one. Sorting by depth imports each package before its
    submodules, so a leak is attributed to the module that actually caused
    it instead of to whichever import happened to pull it in.
    """
    modules = []
    for path in _core_source_files():
        relative = path.relative_to(PROJECT_ROOT)
        parts = list(relative.parts)
        if parts[-1] == "__init__.py":
            parts.pop()            # src/core/__init__.py -> src.core
        else:
            parts[-1] = parts[-1][:-len(".py")]
        modules.append(".".join(parts))
    return sorted(set(modules), key=lambda name: (name.count("."), name))


_CHECK_SCRIPT = r'''
import importlib
import sys

sys.dont_write_bytecode = True

QT_ROOTS = %(qt_roots)r
CORE_MODULES = %(core_modules)r

assert CORE_MODULES, "no core modules were discovered; the enumeration broke"


def qt_modules():
    return sorted(
        name for name in sys.modules
        if name.split(".")[0] in QT_ROOTS
    )


# Nothing may have pulled Qt in before the first core import, or the check
# below would be measuring the harness rather than src.core.
assert not qt_modules(), f"Qt was already imported: {qt_modules()}"

for module in CORE_MODULES:
    importlib.import_module(module)
    leaked = qt_modules()
    assert not leaked, f"{module} imported Qt: {leaked}"

# The state layer publishes through plain callbacks, so its notifications
# must be observable without Qt. Exercised rather than asserted structurally:
# a Signal-shaped attribute would satisfy `hasattr`, but only a real callback
# channel delivers here.
from src.core.StateManager import StateManager

seen = []
StateManager.observers.state_big_change.subscribe(lambda: seen.append("big"))
StateManager.observers.state_big_change.notify()
assert seen == ["big"], f"core notification did not reach the subscriber: {seen}"
assert not qt_modules(), f"using the core observers imported Qt: {qt_modules()}"

# Positive control: Qt *is* installed and importable in this subprocess, so
# the assertions above mean "core does not import it", not "it was missing".
import qtpy.QtCore  # noqa: F401

assert qt_modules(), "qtpy did not register in sys.modules"

print("OK")
'''


def test_the_core_package_imports_without_qt(tmp_path):
    script = _CHECK_SCRIPT % {
        "qt_roots": _QT_ROOTS,
        # Enumerated here, in the pytest process, so the subprocess does no
        # discovery of its own: walking the tree is not the thing under test.
        "core_modules": tuple(_core_modules()),
    }

    env = dict(os.environ)
    env["QT_API"] = "pyside6"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")

    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "src.core is no longer Qt-free\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert "OK" in result.stdout


def test_no_core_module_mentions_a_qt_binding():
    """Fast structural check, run before the subprocess pays for a fork.

    The import check above is the real contract -- it catches an *indirect*
    leak, where a core module imports something innocuous that imports Qt,
    which no amount of reading source lines can see. This one only reads the
    direct import statements, but it does so in milliseconds and names the
    offending line, so it is the one that usually tells a developer what
    they just broke.
    """
    offenders = []
    for path in _core_source_files():
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not (stripped.startswith("import ")
                    or stripped.startswith("from ")):
                continue
            if any(root in stripped for root in _QT_ROOTS):
                offenders.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{number}: {stripped}")

    assert not offenders, (
        "a core module imports a Qt binding; the Qt-facing part belongs in an "
        "adapter (src/StateManagerBridge.py is the worked example):\n  "
        + "\n  ".join(offenders))
