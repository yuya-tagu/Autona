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

The second contract pinned here is the direction of the dependency
(ADR-005 S0): src/core imports nothing from `src` outside `src.core`. Being
Qt-free is necessary but not sufficient -- a core module that reaches back
into `src/` for a Qt-free helper still ties the core to the application
package, so it cannot be lifted out (headless start, a separate test
harness) without dragging the rest along. The last of those reverse
dependencies (the dict helper and the Top 8 / commentary state models) moved
into src/core; this keeps a new one from appearing.
"""
import ast
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


def src_modules_outside_core():
    # `src` itself is the parent package and is always loaded on the way to
    # src.core; anything else under it is the application, not the core.
    return sorted(
        name for name in sys.modules
        if name.startswith("src.")
        and name != "src.core" and not name.startswith("src.core.")
    )


# Nothing may have pulled Qt in before the first core import, or the check
# below would be measuring the harness rather than src.core.
assert not qt_modules(), f"Qt was already imported: {qt_modules()}"
assert not src_modules_outside_core(), \
    f"src modules were already imported: {src_modules_outside_core()}"

for module in CORE_MODULES:
    importlib.import_module(module)
    leaked = qt_modules()
    assert not leaked, f"{module} imported Qt: {leaked}"
    outside = src_modules_outside_core()
    assert not outside, f"{module} imported src outside src.core: {outside}"

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
        "src.core is no longer Qt-free, or imports src outside src.core\n"
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


def _package_parts(path):
    """The package a source file's relative imports resolve against.

    Dropping the last path part covers both cases: a module resolves against
    its package, and a package's ``__init__`` against the package itself.
    """
    return list(path.relative_to(PROJECT_ROOT).with_suffix("").parts)[:-1]


def _imported_names(path):
    """(line, absolute dotted name) for every module an import can load.

    Every import statement in the file, including ones nested in a function
    body: a lazy import is still a dependency, it only fails later. For
    ``from M import a, b`` both ``M`` and ``M.a`` / ``M.b`` are yielded,
    because ``a`` may be a submodule -- that is the ``from .. import X``
    form the old reverse dependencies used.
    """
    package = _package_parts(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:len(package) - (node.level - 1)]
                if node.module:
                    base = base + node.module.split(".")
                module = ".".join(base)
            else:
                module = node.module
            yield node.lineno, module
            for alias in node.names:
                if alias.name != "*":
                    yield node.lineno, f"{module}.{alias.name}"


def _is_src_outside_core(name):
    parts = name.split(".")
    return parts[0] == "src" and len(parts) > 1 and parts[1] != "core"


def test_no_core_module_imports_src_outside_core():
    """src/core depends on nothing in `src` but itself (ADR-005 S0).

    The import check in the subprocess above sees what actually got loaded;
    this one reads every import statement, so it also catches a lazy import
    inside a function that the module import never executes, and it names
    the line. Relative imports are resolved, so ``from .. import X`` and
    ``from ..Helpers.X import y`` count the same as ``import src.X``.
    """
    offenders = {}
    for path in _core_source_files():
        for number, name in _imported_names(path):
            if _is_src_outside_core(name):
                # One line per statement: `from ..X import y` yields both
                # src.X and src.X.y, and the first one already says it all.
                offenders.setdefault(
                    f"{path.relative_to(PROJECT_ROOT)}:{number}", name)
    offenders = [f"{where}: {name}" for where, name in offenders.items()]

    assert not offenders, (
        "a core module imports from src outside src/core; move the Qt-free "
        "dependency into src/core (or inject it from bootstrap_core() as a "
        "callable, like AutonaActions' match-text formatter):\n  "
        + "\n  ".join(offenders))
