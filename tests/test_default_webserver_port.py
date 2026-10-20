"""Contract test for the default web server port (ADR-001 Addendum 2026-09-19).

The built-in web server's default port moved 5000 -> 5107: 5000 is Flask's own
default and the port the upstream TournamentStreamHelper family listens on, so
a streaming PC running several tools at once really does collide. There is
deliberately no migration code and no one-time dialog -- existing installs move
to the new default too, and a user who saved ``general.webserver_port`` in
settings.json keeps their value.

That only holds while there is exactly *one* place the number lives. Before the
change, thirteen call sites each spelled ``5000`` as the inline fallback of
``SettingsManager.Get("general.webserver_port", ...)``; any one of them left
behind would keep serving/advertising the old port after the setting is
untouched, and the bug would surface as a blank OBS browser source rather than
as an error. So this test pins the constant's value, pins that both import
paths still expose it, and -- the part that actually catches a regression --
greps ``src/**/*.py`` for a *numeric literal* default on that key.

Runtime-free apart from importing ``src.core.SettingsManager`` (Qt-free and
side-effect-free; ``tests/test_core_qt_free.py`` and
``tests/test_import_side_effects.py`` pin both). ``src`` is never imported
wholesale (agent-pitfalls #1).
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

EXPECTED_PORT = 5107

# Matches an integer literal used as the Get() default for the port setting,
# e.g. Get("general.webserver_port", 5000) -- in either quote style. The
# constant spelling (DEFAULT_WEBSERVER_PORT) does not match.
LITERAL_DEFAULT = re.compile(
    r"""general\.webserver_port["']\s*,\s*\d""")


def _python_sources():
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_the_default_port_constant_is_5107():
    from src.core.SettingsManager import DEFAULT_WEBSERVER_PORT

    assert DEFAULT_WEBSERVER_PORT == EXPECTED_PORT, (
        "The default web server port is a published fact: OBS browser source "
        "URLs, ASS macro URLs and the Web UI dev fallback are all written "
        "against it. Changing it needs a release note "
        "(docs/operations/runtime-operations.md) and an ADR-001 addendum.")


def test_core_settings_manager_exports_the_constant():
    """Call sites import the constant from `src.core.SettingsManager`.

    A missing export would be an ImportError at app start, not at test time.
    """
    from src.core.SettingsManager import DEFAULT_WEBSERVER_PORT

    assert DEFAULT_WEBSERVER_PORT == EXPECTED_PORT


def test_no_source_file_hardcodes_a_port_default():
    offenders = {}
    for path in _python_sources():
        source = path.read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), start=1):
            if LITERAL_DEFAULT.search(line):
                relative = path.relative_to(PROJECT_ROOT).as_posix()
                offenders.setdefault(relative, []).append(number)

    assert offenders == {}, (
        f"Numeric fallback for general.webserver_port found at {offenders}. "
        "Use DEFAULT_WEBSERVER_PORT (src/core/SettingsManager.py) so the "
        "default lives in exactly one place.")


def test_the_web_ui_dev_fallback_matches_the_constant():
    """web/src/env.js hardcodes the port for `npm start` (dev only).

    In a production build the Web UI reads `window.location.port`, so this
    copy only bites during development -- which is exactly why it drifts
    unnoticed. Kept as a text check: the file is not importable from Python.
    """
    env_js = (PROJECT_ROOT / "web" / "src" / "env.js").read_text(
        encoding="utf-8")

    assert str(EXPECTED_PORT) in env_js, (
        "web/src/env.js no longer carries the default port; keep its dev "
        "fallback in sync with DEFAULT_WEBSERVER_PORT.")
