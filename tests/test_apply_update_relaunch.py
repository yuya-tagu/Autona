"""Relaunch vs. the update lock in the external update helper
(assets/apply_update.ps1).

The helper holds ``update_in_progress.lock`` (FileShare.None) while it mirrors
the new release over the install dir, and main.py's startup gate exits
silently (``sys.exit(0)``) while that lock is held. Two orderings follow, and
both have failed on real machines:

- Every launch of the app from the helper must come AFTER the lock is
  released. On the v1.8.1 -> v2.0.0 E2E (2026-11-16) the helper started the
  new exe, held its window for 1.5 s and only then released the lock in
  Invoke-Cleanup. A warm exe reaches the gate in ~0.25 s, so it saw the lock
  and quit: update_log.txt said "update complete", 2.0.0 was installed, and
  the app never came back (not one line in its log). The fallback relaunch in
  Stop-UpdateAndExit had the same order.
- The release must not move up past the robocopy passes either: an app started
  mid-mirror purges the staging dir (v1.3.0 E2E, robocopy exit 11).

Asserted at TEXT level like tests/test_apply_update_robocopy.py — running the
helper would touch a real install dir.
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HELPER = PROJECT_ROOT / "assets" / "apply_update.ps1"

# A statement that is only the call (the definition line and comments that
# mention the name do not match).
_RELEASE_CALL = re.compile(r"(?m)^[ \t]*Remove-UpdateLock[ \t]*$")
_FUNCTION = re.compile(r"(?m)^[ \t]*function[ \t]+([\w-]+)")
# Any statement that starts the app, whatever the form. The keyword has to
# come before any '#' on the line, so comments naming $ExePath do not count.
_EXE_LAUNCH = re.compile(
    r"(?m)^[^#\n]*?"
    r"(?:Start-Process\b[^#\n]*?|\]::Start\([^#\n]*?|Invoke-Item\b[^#\n]*?|&\s*)"
    r"\$ExePath\b")
# The install run itself: the top-level try after the last function.
_TOP_LEVEL_TRY = re.compile(r"(?m)^try \{")


def _helper_text() -> str:
    return HELPER.read_bytes().decode("utf-8-sig")


def _main_start(helper: str) -> int:
    starts = [m.start() for m in _TOP_LEVEL_TRY.finditer(helper)]
    assert starts, f"no top-level try in {HELPER.name}"
    return starts[-1]


def _function_body(helper: str, name: str) -> str:
    """From `function <name>` to the next function (or the install run)."""
    decls = [(m.group(1), m.start()) for m in _FUNCTION.finditer(helper)]
    names = [n for n, _ in decls]
    assert name in names, f"no function {name} in {HELPER.name}"
    i = names.index(name)
    end = decls[i + 1][1] if i + 1 < len(decls) else _main_start(helper)
    return helper[decls[i][1]:end]


def _scope_start(helper: str, index: int) -> int:
    """Start of the function (or of the install run) containing `index`."""
    bounds = [m.start() for m in _FUNCTION.finditer(helper)]
    bounds.append(_main_start(helper))
    before = [b for b in bounds if b < index]
    assert before, "launch outside any function and before the install run"
    return max(before)


def test_the_released_lock_is_the_one_main_py_gates_on():
    assert 'Join-Path $PSScriptRoot "update_in_progress.lock"' in _helper_text()
    main_py = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    assert '"autona_update", "update_in_progress.lock"' in main_py
    assert "sys.exit(0)" in main_py.split("if _update_in_flight():")[1].split(
        "from src import Autona")[0]


def test_both_relaunches_are_found():
    """Guards the scan below against passing vacuously: the success relaunch
    (step 4) and the fallback relaunch (Stop-UpdateAndExit)."""
    helper = _helper_text()
    launches = [m.start() for m in _EXE_LAUNCH.finditer(helper)]
    main_start = _main_start(helper)
    fallback = _function_body(helper, "Stop-UpdateAndExit")
    assert any(i > main_start for i in launches), "step 4 relaunch not found"
    assert _EXE_LAUNCH.search(fallback), "fallback relaunch not found"


def test_every_app_launch_releases_the_lock_first():
    helper = _helper_text()
    releases = [m.start() for m in _RELEASE_CALL.finditer(helper)]
    for launch in (m.start() for m in _EXE_LAUNCH.finditer(helper)):
        scope = _scope_start(helper, launch)
        line_no = helper.count("\n", 0, launch) + 1
        assert any(scope < r < launch for r in releases), (
            f"{HELPER.name}:{line_no} launches the app without calling "
            "Remove-UpdateLock before it in the same function: main.py's "
            "startup gate sees the held lock and the app exits silently")


def test_the_install_run_keeps_the_lock_through_the_mirror():
    """Releasing before the robocopy passes lets a manual launch run on (and
    purge the staging dir of) a half-mirrored install."""
    helper = _helper_text()
    main_start = _main_start(helper)
    root_pass = helper.index('Invoke-RobocopyPass "root"')
    run_releases = [m.start() for m in _RELEASE_CALL.finditer(helper)
                    if m.start() > main_start]
    assert run_releases, "the install run never releases the lock"
    assert min(run_releases) > root_pass


def test_cleanup_still_releases_and_the_release_is_idempotent():
    """Invoke-Cleanup keeps the release as a backstop (the workdir holding the
    lock file cannot be deleted otherwise), so it runs twice on every path."""
    helper = _helper_text()
    assert _RELEASE_CALL.search(_function_body(helper, "Invoke-Cleanup"))
    body = _function_body(helper, "Remove-UpdateLock")
    assert "$script:LockStream.Dispose(); $script:LockStream = $null" in body
    assert "if (Test-Path -LiteralPath $script:LockPath)" in body
    assert "-ErrorAction SilentlyContinue" in body
    assert "catch {}" in body
