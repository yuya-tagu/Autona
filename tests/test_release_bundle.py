"""What the release bundles of Qt (ADR-005 S-final SF-9).

dependencies/autona.spec keeps out what PyInstaller's Qt hooks collect but
the app never opens: the QML modules (Python excludes), and the debug
build's WebEngine resources, Qt's own translations, WebEngine locales other
than en-US / ja and Git for Windows' OpenSSL (a filter on the analysed TOCs).
scripts/validate_release_zip.ps1 (check 5) fails a release zip that lacks a
file the runtime needs or ships a trimmed one.

Neither runs here -- the build takes minutes and the validator needs a zip
-- so this module pins them at TEXT level, like
tests/test_apply_update_robocopy.py:

- the Qt modules the app imports (src/, main.py, autona_splash.py) are the
  allowlist, and the spec excludes none of them nor what they import;
- with every module of the spec's excludes absent, the app's modules, qtpy
  and qdarktheme still import (qtpy only tries QtOpenGL / QtOpenGLWidgets /
  QtDataVisualization), and load nothing of Qt outside the bundle;
- the spec's filter and the validator's forbidden patterns agree on the
  installed PySide6's files, and nothing the validator requires is
  filtered, forbidden or absent from the installed PySide6 (a Qt upgrade
  that renames one fails here, before a build);
- the OpenSSL the spec drops is not the one Python's ssl module loads.
"""
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC = PROJECT_ROOT / "dependencies" / "autona.spec"
VALIDATOR = PROJECT_ROOT / "scripts" / "validate_release_zip.ps1"

# The Qt modules the app imports: src/, main.py and autona_splash.py.
APP_QT_MODULES = {"QtCore", "QtGui", "QtWidgets", "QtNetwork",
                  "QtWebEngineCore", "QtWebEngineWidgets"}
# What those import of Qt themselves: QtWebEngineCore / QtWebEngineWidgets
# import QtPrintSupport, QtWebChannel and QtNetwork, qdarktheme imports QtSvg.
DEPENDENCY_QT_MODULES = {"QtPrintSupport", "QtWebChannel", "QtSvg"}
# qtpy's helpers the app may import besides its Qt modules.
QTPY_HELPERS = {"shiboken"}
# The validator's patterns for the QML family. The spec's filter does not
# drop these: excluding the module whose hook collects them does.
EXCLUDED_WITH_MODULE = {
    "^PySide6/qml/": "PySide6.QtQml",
    "^PySide6/plugins/qmltooling/": "PySide6.QtQml",
    "^PySide6/Qt6Quick3D": "PySide6.QtQuick",
    "^PySide6/Qt63D": "PySide6.QtQuick",
    "^PySide6/Qt6Graphs": "PySide6.QtQuick",
}
# The folders the spec's filter decides on (paths under _internal/, lower
# case); outside them it must drop nothing.
FILTERED_FOLDERS = {"", "pyside6/resources", "pyside6/translations",
                    "pyside6/translations/qtwebengine_locales",
                    "pyside6/plugins/tls"}
# Top-level DLLs of a build, beside the installed PySide6's files.
TOP_LEVEL_DLLS = ("libssl-3-x64.dll", "libcrypto-3-x64.dll", "libssl-3.dll",
                  "libcrypto-3.dll", "libffi-8.dll", "python312.dll")
# The size of _internal/ before SF-9 (the 2026-10-08 build).
PRE_SF9_INTERNAL_MIB = 571


def _spec_tree():
    return ast.parse(SPEC.read_text(encoding="utf-8"))


def _spec_excludes():
    for node in ast.walk(_spec_tree()):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Analysis":
            for keyword in node.keywords:
                if keyword.arg == "excludes":
                    return ast.literal_eval(keyword.value)
    raise AssertionError("no excludes= in the spec's Analysis(...)")


def _spec_filter():
    """The spec's `_unused_qt_file`, compiled with the constants it reads
    only (the rest of the spec needs PyInstaller)."""
    wanted = []
    for node in _spec_tree().body:
        if isinstance(node, ast.FunctionDef) and node.name == "_unused_qt_file":
            wanted.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name)
                and target.id in ("_KEPT_WEBENGINE_LOCALES", "_DROPPED_FILES")
                for target in node.targets):
            wanted.append(node)
    assert len(wanted) == 3, "the spec's filter or its two constants moved"
    namespace = {}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(SPEC), "exec"),
         namespace)
    return namespace["_unused_qt_file"]


def _validator_text():
    return VALIDATOR.read_bytes().decode("utf-8-sig")


def _validator_block(name, opening):
    m = re.search(r"^\$%s = %s\s*$(.*?)^[)}]\s*$" % (name, re.escape(opening)),
                  _validator_text(), re.M | re.S)
    assert m, f"no ${name} block in {VALIDATOR.name}"
    return m.group(1)


def _validator_required():
    return re.findall(r'^\s*"([^"]+)",?\s*$', _validator_block("qtRequired", "@("),
                      re.M)


def _validator_forbidden():
    return re.findall(r"^\s*'([^']+)'\s*=", _validator_block("qtForbidden", "[ordered]@{"),
                      re.M)


def _forbidden_by(patterns, path):
    # PowerShell's -match is case-insensitive.
    return [p for p in patterns if re.search(p, path, re.IGNORECASE)]


def _installed_pyside6():
    import PySide6
    return Path(PySide6.__file__).resolve().parent


def _bundle_paths():
    """Paths under _internal/ a build could carry: every file of the
    installed PySide6 (under PySide6/), and the top-level DLLs."""
    root = _installed_pyside6()
    paths = ["PySide6/" + p.relative_to(root).as_posix()
             for p in root.rglob("*") if p.is_file()]
    return paths + list(TOP_LEVEL_DLLS)


def _qt_names_in(path):
    """The Qt modules a file imports (PySide6 / qtpy), and the qtpy helpers
    outside QTPY_HELPERS (`qtpy.<name>`). Imports anywhere in the file count,
    function-local ones too, and module names written as strings."""
    found = set()

    def add(binding, name):
        if name.startswith("Qt"):
            found.add(name)
        elif binding == "qtpy" and name.islower() and name not in QTPY_HELPERS:
            found.add("qtpy." + name)

    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] in ("PySide6", "qtpy") and len(parts) > 1:
                    add(parts[0], parts[1])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            parts = node.module.split(".")
            if parts[0] not in ("PySide6", "qtpy"):
                continue
            if len(parts) > 1:
                add(parts[0], parts[1])
            else:
                for alias in node.names:
                    add(parts[0], alias.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            m = re.fullmatch(r"(PySide6|qtpy)\.(\w+)(\.\w+)*", node.value)
            if m:
                add(m.group(1), m.group(2))
    return found


def _app_files():
    return [*sorted((PROJECT_ROOT / "src").rglob("*.py")),
            PROJECT_ROOT / "main.py", PROJECT_ROOT / "autona_splash.py"]


def test_the_app_imports_the_allowlisted_qt_modules_only():
    imported = {str(path.relative_to(PROJECT_ROOT)): _qt_names_in(path)
                for path in _app_files()}
    outside = {path: sorted(names - APP_QT_MODULES)
               for path, names in imported.items() if names - APP_QT_MODULES}
    assert outside == {}, (
        "a Qt module outside the allowlist: add it to APP_QT_MODULES only "
        "after checking the release still carries it (dependencies/autona.spec "
        "excludes, scripts/validate_release_zip.ps1's $qtRequired)")
    # The allowlist is exact: what the app imports today.
    assert set().union(*imported.values()) == APP_QT_MODULES


def test_the_spec_excludes_no_qt_module_the_app_or_webengine_imports():
    excludes = set(_spec_excludes())
    needed = {"PySide6." + name for name in APP_QT_MODULES | DEPENDENCY_QT_MODULES}
    assert excludes & needed == set()
    # Every Python module the validator requires is one of those.
    required_modules = {"PySide6." + Path(path).stem
                        for path in _validator_required() if path.endswith(".pyd")}
    assert required_modules == needed


def test_the_app_imports_with_the_spec_excludes_absent(tmp_path):
    """Import every src module (src.Autona aside: its import is the startup
    sequence), qtpy's Qt modules and qdarktheme in a fresh interpreter whose
    imports of the spec's excludes fail, as in the frozen app."""
    script = r"""
import importlib, importlib.abc, json, pathlib, sys
BLOCKED = set(json.loads(sys.argv[1]))
root = pathlib.Path(sys.argv[2])

class Excluded(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if any(name == b or name.startswith(b + ".") for b in BLOCKED):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None

sys.meta_path.insert(0, Excluded())
names = []
for path in sorted((root / "src").rglob("*.py")):
    parts = path.relative_to(root).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    name = ".".join(parts)
    if name != "src.Autona":
        names.append(name)
names += ["qtpy", "qtpy.QtCore", "qtpy.QtGui", "qtpy.QtWidgets",
          "qtpy.QtWebEngineCore", "qtpy.QtWebEngineWidgets", "qtpy.shiboken",
          "qdarktheme"]
failed = {}
for name in names:
    try:
        importlib.import_module(name)
    except Exception as error:
        failed[name] = repr(error)
print(json.dumps({
    "imported": len(names),
    "failed": failed,
    "qt": sorted(m.split(".")[1] for m in sys.modules
                 if m.startswith("PySide6.Qt") and m.count(".") == 1),
}))
"""
    env = dict(os.environ)
    env["QT_API"] = "pyside6"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, json.dumps(_spec_excludes()),
         str(PROJECT_ROOT)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["failed"] == {}
    assert report["imported"] > 50
    assert set(report["qt"]) <= APP_QT_MODULES | DEPENDENCY_QT_MODULES
    assert APP_QT_MODULES <= set(report["qt"])


def test_the_spec_filter_and_the_validator_agree_on_the_qt_files():
    unused = _spec_filter()
    forbidden = _validator_forbidden()
    dropped, kept = set(), set()
    for path in _bundle_paths():
        folder = path.lower().rpartition("/")[0]
        if unused(path):
            assert folder in FILTERED_FOLDERS, path
            dropped.add(path)
        elif folder in FILTERED_FOLDERS:
            kept.add(path)
        # The OS separator in a TOC destination: the same answer.
        assert unused(path.replace("/", "\\")) == unused(path), path
    # Inside the filter's folders, what it drops is what the validator
    # forbids, and the other way round.
    assert {p for p in dropped if not _forbidden_by(forbidden, p)} == set()
    assert {p for p in kept if _forbidden_by(forbidden, p)} == set()
    # Each group is real in the installed PySide6: something dropped, and
    # the files the runtime reads kept.
    for folder in ("pyside6/resources", "pyside6/translations",
                   "pyside6/translations/qtwebengine_locales",
                   "pyside6/plugins/tls", ""):
        assert any(p.lower().rpartition("/")[0] == folder for p in dropped), folder
    assert {"PySide6/translations/qtwebengine_locales/en-US.pak",
            "PySide6/translations/qtwebengine_locales/ja.pak",
            "PySide6/resources/qtwebengine_resources.pak",
            "PySide6/plugins/tls/qschannelbackend.dll",
            "PySide6/plugins/tls/qcertonlybackend.dll",
            "libssl-3.dll", "libcrypto-3.dll"} <= kept


def test_every_forbidden_pattern_is_what_the_spec_keeps_out():
    unused = _spec_filter()
    forbidden = _validator_forbidden()
    paths = _bundle_paths()
    by_filter = {p for p in forbidden
                 if any(unused(path) for path in paths if re.search(p, path, re.I))}
    # The rest is the QML family, which the spec's excludes keep out.
    assert set(forbidden) - by_filter == set(EXCLUDED_WITH_MODULE)
    assert set(EXCLUDED_WITH_MODULE.values()) <= set(_spec_excludes())
    # No pattern is dead: each names a file the installed PySide6 has.
    assert {p for p in forbidden
            if not any(re.search(p, path, re.I) for path in paths)} == set()


def test_what_the_validator_requires_is_kept_and_installed():
    unused = _spec_filter()
    forbidden = _validator_forbidden()
    required = _validator_required()
    assert len(required) == len(set(required))
    assert [p for p in required if unused(p)] == []
    assert {p: _forbidden_by(forbidden, p) for p in required
            if _forbidden_by(forbidden, p)} == {}
    root = _installed_pyside6()
    assert [p for p in required if p.startswith("PySide6/")
            and not (root / p[len("PySide6/"):]).is_file()] == []
    # The size cap is under the size before SF-9: a build that lost the
    # trimming fails the zip.
    cap = re.search(r"^\$internalMaxMiB = (\d+)\s*$", _validator_text(), re.M)
    assert cap and 0 < int(cap.group(1)) < PRE_SF9_INTERNAL_MIB


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DLL names")
def test_the_dropped_openssl_is_not_pythons():
    # The spec drops libssl-3-x64.dll / libcrypto-3-x64.dll (Git for
    # Windows' build, found on PATH by the QtNetwork hook). Python's ssl
    # module must load the other pair, which the validator requires.
    dlls = Path(sys.base_prefix) / "DLLs"
    assert (dlls / "libssl-3.dll").is_file()
    assert (dlls / "libcrypto-3.dll").is_file()
    ssl_module = (dlls / "_ssl.pyd").read_bytes()
    assert b"libssl-3.dll" in ssl_module and b"libcrypto-3.dll" in ssl_module
    assert b"-x64.dll" not in ssl_module


def test_the_filter_applies_to_both_tocs_before_the_collection():
    body = _spec_tree().body

    def index(predicate):
        hits = [i for i, node in enumerate(body) if predicate(node)]
        assert len(hits) == 1
        return hits[0]

    def assigns(node, name):
        return isinstance(node, ast.Assign) and any(
            ast.unparse(target) == name for target in node.targets)

    analysis = index(lambda node: assigns(node, "a"))
    collect = index(lambda node: assigns(node, "coll"))
    for toc in ("a.datas", "a.binaries"):
        at = index(lambda node, toc=toc: assigns(node, toc))
        assert analysis < at < collect, toc
        source = ast.unparse(body[at].value)
        assert source == f"[entry for entry in {toc} if not _unused_qt_file(entry[0])]"
