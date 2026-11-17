"""What the release bundles of Qt (ADR-005 S-final SF-9).

dependencies/autona.spec keeps out what PyInstaller's Qt hooks collect but
the app never opens: the QML modules (Python excludes), and the debug
build's WebEngine resources, Qt's own translations, WebEngine locales other
than en-US / ja and Git for Windows' OpenSSL (a filter on the analysed TOCs).
scripts/validate_release_zip.ps1 (check 5) fails a release zip that lacks a
file the runtime needs or ships a trimmed one.

The build does not run here (it takes minutes), so this module pins the
spec at TEXT level, like tests/test_apply_update_robocopy.py:

- the Qt modules the app imports (src/, main.py, autona_splash.py) are the
  allowlist, and the spec excludes none of them nor what they import;
- with every module of the spec's excludes absent, the app's modules, qtpy
  and qdarktheme still import (qtpy only tries QtOpenGL / QtOpenGLWidgets /
  QtDataVisualization), and load nothing of Qt outside the bundle;
- the spec's filter and the validator's forbidden patterns agree on the
  installed PySide6's files, the validator forbids the excluded modules'
  own .pyd files, and nothing the validator requires is filtered, forbidden
  or absent from the installed PySide6 (a Qt upgrade that renames one fails
  here, before a build);
- the OpenSSL the spec drops is not the one Python's ssl module loads.

The validator's check 5 itself runs on synthetic zips (Windows PowerShell).
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
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
# The validator's pattern for the excluded modules' own .pyd files: its
# alternatives are the spec's PySide6 excludes, exactly.
EXCLUDED_PYD_PATTERN = (r"^PySide6/Qt(Quick|Qml|QuickWidgets|Positioning|UiTools|"
                        r"DataVisualization|OpenGL|OpenGLWidgets)\.pyd$")
# The validator's patterns for the QML family. The spec's filter does not
# drop these: excluding the module whose hook collects them does.
EXCLUDED_WITH_MODULE = {
    r"^PySide6/pyside6qml\.abi3\.dll$": "PySide6.QtQml",
    "^PySide6/qml/": "PySide6.QtQml",
    "^PySide6/plugins/qmltooling/": "PySide6.QtQml",
    "^PySide6/Qt6Quick3D": "PySide6.QtQuick",
    "^PySide6/Qt63D": "PySide6.QtQuick",
    "^PySide6/Qt6Graphs": "PySide6.QtQuick",
}
# The folders the spec's filter decides on by folder (paths under
# _internal/, lower case). Elsewhere it drops only the OpenSSL file names
# (_DROPPED_NAMES, matched in any folder), which the installed PySide6 has
# in plugins/tls alone.
FILTERED_FOLDERS = {"", "pyside6/resources", "pyside6/translations",
                    "pyside6/translations/qtwebengine_locales",
                    "pyside6/plugins/tls"}
# Top-level DLLs of a build, beside the installed PySide6's files. These are
# names written here, not read from an install: the QtNetwork hook finds Git
# for Windows' OpenSSL on PATH at build time.
TOP_LEVEL_DLLS = ("libssl-3-x64.dll", "libcrypto-3-x64.dll", "libssl-3.dll",
                  "libcrypto-3.dll", "libffi-8.dll", "python312.dll")
# What the validator requires that PyInstaller writes (not in the installed
# PySide6): qt.conf, the prefix Qt finds its plugins and resources from.
WRITTEN_BY_PYINSTALLER = {"PySide6/qt.conf"}
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
                and target.id in ("_KEPT_WEBENGINE_LOCALES", "_DROPPED_NAMES")
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


# A PowerShell string literal, either quote ('...' is verbatim, "..." expands).
_PS_STRING = r"""(?:'([^']*)'|"([^"]*)")"""


def _block_lines(block):
    """The block's lines other than blank ones and comments: each must be one
    entry, so a line the parsers below cannot read fails instead of being
    skipped."""
    return [line for line in block.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def _validator_required():
    lines = _block_lines(_validator_block("qtRequired", "@("))
    found = []
    for line in lines:
        m = re.fullmatch(r"\s*%s,?\s*" % _PS_STRING, line)
        if m:
            found.append(m.group(1) if m.group(1) is not None else m.group(2))
    assert len(found) == len(lines), "a $qtRequired line this test cannot read"
    return found


def _validator_forbidden_reasons():
    """The validator's forbidden patterns (in order) and their reasons."""
    lines = _block_lines(_validator_block("qtForbidden", "[ordered]@{"))
    found = {}
    for line in lines:
        m = re.fullmatch(r"\s*%s\s*=\s*%s\s*" % (_PS_STRING, _PS_STRING), line)
        if m:
            pattern = m.group(1) if m.group(1) is not None else m.group(2)
            found[pattern] = m.group(3) if m.group(3) is not None else m.group(4)
    assert len(found) == len(lines), "a $qtForbidden line this test cannot read"
    return found


def _validator_forbidden():
    return list(_validator_forbidden_reasons())


def _validator_cap():
    cap = re.search(r"^\$internalMaxMiB = (\d+)\s*$", _validator_text(), re.M)
    assert cap, f"no $internalMaxMiB in {VALIDATOR.name}"
    return int(cap.group(1))


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
    # Each group under PySide6/ is real in the installed PySide6: something
    # dropped, and the files the runtime reads kept.
    for folder in ("pyside6/resources", "pyside6/translations",
                   "pyside6/translations/qtwebengine_locales",
                   "pyside6/plugins/tls"):
        assert any(p.lower().rpartition("/")[0] == folder for p in dropped), folder
    assert {"PySide6/translations/qtwebengine_locales/en-US.pak",
            "PySide6/translations/qtwebengine_locales/ja.pak",
            "PySide6/resources/qtwebengine_resources.pak",
            "PySide6/plugins/tls/qschannelbackend.dll",
            "PySide6/plugins/tls/qcertonlybackend.dll"} <= kept
    # The top level is TOP_LEVEL_DLLS, names written in this module, so this
    # pins only the filter's answer for them (Git's pair dropped, Python's
    # kept). That a build carries Python's pair (installed:
    # test_the_dropped_openssl_is_not_pythons) and lacks Git's is the
    # validator's check 5, seen on the 2026-11-16 build.
    assert {p for p in dropped if "/" not in p} == {"libssl-3-x64.dll",
                                                   "libcrypto-3-x64.dll"}
    assert {"libssl-3.dll", "libcrypto-3.dll"} <= kept


def test_the_openssl_names_are_dropped_and_forbidden_in_any_folder():
    # The spec's filter and the validator match Git's OpenSSL and Qt's
    # OpenSSL TLS backend by file name: a hook update may move them.
    unused = _spec_filter()
    forbidden = _validator_forbidden()
    for name in ("libssl-3-x64.dll", "libcrypto-3-x64.dll", "qopensslbackend.dll"):
        for path in (name, "PySide6/" + name, "PySide6/plugins/tls/" + name,
                     "PySide6/plugins/other/" + name.upper()):
            assert unused(path), path
            assert _forbidden_by(forbidden, path), path
    for path in ("libssl-3.dll", "PySide6/libcrypto-3.dll",
                 "PySide6/plugins/tls/qschannelbackend.dll",
                 "PySide6/plugins/tls/qcertonlybackend.dll",
                 "PySide6/notlibssl-3-x64.dll"):
        assert not unused(path), path
        assert not _forbidden_by(forbidden, path), path


def test_every_forbidden_pattern_is_what_the_spec_keeps_out():
    unused = _spec_filter()
    forbidden = _validator_forbidden()
    paths = _bundle_paths()
    by_filter = {p for p in forbidden
                 if any(unused(path) for path in paths if re.search(p, path, re.I))}
    # The rest is what the spec's excludes keep out: the excluded modules
    # themselves, and the QML family.
    assert set(forbidden) - by_filter == set(EXCLUDED_WITH_MODULE) | {EXCLUDED_PYD_PATTERN}
    assert set(EXCLUDED_WITH_MODULE.values()) <= set(_spec_excludes())
    # No pattern is dead: each names a file the installed PySide6 has.
    assert {p for p in forbidden
            if not any(re.search(p, path, re.I) for path in paths)} == set()


def test_the_validator_forbids_the_excluded_modules_themselves():
    # A build that ignored an exclude carries the module's .pyd: the
    # pattern names the spec's PySide6 excludes, no more and no fewer, and
    # each of them is a module of the installed PySide6.
    assert EXCLUDED_PYD_PATTERN in _validator_forbidden()
    alternatives = re.fullmatch(r"\^PySide6/Qt\((.+)\)\\\.pyd\$",
                                EXCLUDED_PYD_PATTERN).group(1).split("|")
    assert {"PySide6.Qt" + name for name in alternatives} == {
        name for name in _spec_excludes() if name.startswith("PySide6.")}
    assert {p for p in _bundle_paths() if re.search(EXCLUDED_PYD_PATTERN, p, re.I)} == {
        f"PySide6/Qt{name}.pyd" for name in alternatives}


def test_what_the_validator_requires_is_kept_and_installed():
    unused = _spec_filter()
    forbidden = _validator_forbidden()
    required = _validator_required()
    assert len(required) == len(set(required))
    assert [p for p in required if unused(p)] == []
    assert {p: _forbidden_by(forbidden, p) for p in required
            if _forbidden_by(forbidden, p)} == {}
    root = _installed_pyside6()
    assert WRITTEN_BY_PYINSTALLER <= set(required)
    assert [p for p in required if p.startswith("PySide6/")
            and p not in WRITTEN_BY_PYINSTALLER
            and not (root / p[len("PySide6/"):]).is_file()] == []
    # The size cap is under the size before SF-9: a build that lost the
    # trimming fails the zip.
    assert 0 < _validator_cap() < PRE_SF9_INTERNAL_MIB


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


_CHECK_5_DRIVER = r"""
param([string]$Validator, [string]$Dir)
foreach ($zip in @(Get-ChildItem -LiteralPath $Dir -Filter *.zip | Sort-Object Name)) {
    try {
        & $Validator -ZipPath $zip.FullName | Out-Null
        [Console]::Out.WriteLine(("CASE {0} OK" -f $zip.BaseName))
    } catch {
        # Parenthesised: inside a method call a bare comma splits arguments.
        [Console]::Out.WriteLine(("CASE {0} THROW {1}" -f $zip.BaseName, $_.Exception.Message))
    }
}
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell")
@pytest.mark.skipif(shutil.which("powershell") is None, reason="no powershell")
def test_check_5_of_the_validator_on_synthetic_zips(tmp_path):
    """Run validate_release_zip.ps1 on zips that pass checks 1-4 (the shared
    Designer runtime and nothing else outside _internal/) and carry every
    required file under _internal/ (empty): one passes, and one lacking a
    required file, one per forbidden pattern and one over the size cap
    each throw for that reason. Check 6 (ADR-006 A7) too: a zip carrying
    the archive clipper's worker (installed by Autona, never shipped) or
    its install's leftovers throws; a file merely named alike does not."""
    required = _validator_required()
    reasons = _validator_forbidden_reasons()
    cap = _validator_cap()
    paths = sorted(_bundle_paths())
    base = ["Autona/layout/designer_runtime/runtime.js",
            "Autona/layout/designer_runtime/runtime.css",
            *("Autona/_internal/" + p for p in required)]
    zips = tmp_path / "zips"
    zips.mkdir()
    expected = {}

    def case(name, entries, expect, big_mib=0):
        with zipfile.ZipFile(zips / f"{name}.zip", "w", zipfile.ZIP_DEFLATED,
                             compresslevel=1) as archive:
            for entry in entries:
                archive.writestr(entry, b"")
            if big_mib:
                with archive.open("Autona/_internal/big.bin", "w") as big:
                    chunk = bytes(1 << 20)
                    for _ in range(big_mib):
                        big.write(chunk)
        expected[name] = expect

    case("ok", base, ("OK",))
    lost = "Autona/_internal/PySide6/Qt6Quick.dll"
    assert lost in base
    case("missing", [e for e in base if e != lost],
         ("THROW", "lacks 1 file(s)", "PySide6/Qt6Quick.dll"))
    for i, pattern in enumerate(reasons):
        sample = next(p for p in paths if re.search(pattern, p, re.I))
        # The validator throws for the first pattern the file matches.
        first = next(p for p in reasons if re.search(p, sample, re.I))
        case(f"forbidden_{i:02d}", [*base, "Autona/_internal/" + sample],
             ("THROW", "keeps out (%s)" % reasons[first], sample))
    case("oversize", base, ("THROW", f"over the {cap} MiB cap"), big_mib=cap + 1)
    for i, entry in enumerate((
            "Autona/vodsplit-worker/vodsplit-worker.exe",
            "Autona/VODSPLIT-WORKER/_internal/python312.dll",
            "Autona/vodsplit-worker.part",
            "Autona/vodsplit-worker.new/vodsplit-worker.exe",
            "Autona/vodsplit-worker.old/deno.exe")):
        case(f"worker_{i}", [*base, entry],
             ("THROW", "archive clipper's worker", entry))
    case("worker_named_alike", [*base, "Autona/vodsplit-worker.txt"], ("OK",))

    driver = tmp_path / "driver.ps1"
    driver.write_text(_CHECK_5_DRIVER, encoding="utf-8")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(driver), "-Validator", str(VALIDATOR), "-Dir", str(zips)],
        capture_output=True, text=True, timeout=300)
    outcomes = {}
    for line in result.stdout.splitlines():
        m = re.match(r"CASE (\S+) (OK|THROW)(?: (.*))?$", line)
        if m:
            outcomes[m.group(1)] = (m.group(2), m.group(3) or "")
    assert set(outcomes) == set(expected), result.stdout + result.stderr
    for name, (status, *fragments) in expected.items():
        got_status, message = outcomes[name]
        assert got_status == status, (name, message)
        for fragment in fragments:
            assert fragment in message, (name, message)
