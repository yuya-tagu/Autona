"""Contract between the diff download (AutonaUpdater.py) and the external
helper (apply_update.ps1).

Both sides are checked at TEXT level: importing AutonaUpdater boots Qt and
every singleton (agent-pitfalls #1), and the helper is PowerShell anyway.

The invariants here are the ones whose breakage is silent in review and only
explodes on an end user's machine mid-update:
- the staging marker filename must match on both sides
- the helper must check the marker BEFORE the zip existence check (a diff
  download produces no zip; wrong order fails every diff update)
- the Python side must sniff the payload helper for marker support before
  committing to the diff (an old helper would otherwise fail loudly on the
  missing zip)
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKER = "staging_prebuilt.marker"


def _updater_text() -> str:
    return (PROJECT_ROOT / "src" / "AutonaUpdater.py").read_text(
        encoding="utf-8")


def _helper_text() -> str:
    return (PROJECT_ROOT / "assets" / "apply_update.ps1").read_bytes().decode(
        "utf-8-sig")


def test_marker_name_matches_on_both_sides():
    assert f'_STAGING_MARKER_NAME = "{MARKER}"' in _updater_text()
    assert f'"{MARKER}"' in _helper_text()


def test_helper_checks_marker_before_zip():
    """A diff download produces no update.zip. If the helper tests the zip
    first, every diff update dies at 'update zip not found'."""
    helper = _helper_text()
    marker_check = helper.index("Test-Path -LiteralPath $MarkerPath")
    zip_missing = helper.index("update zip not found")
    assert marker_check < zip_missing


def test_helper_cleans_up_the_marker():
    """A marker surviving Invoke-Cleanup would make the NEXT full-zip update
    skip extraction and mirror a stale staging."""
    helper = _helper_text()
    cleanup = helper.index("function Invoke-Cleanup")
    cleanup_end = helper.index("function Stop-UpdateAndExit")
    assert "$MarkerPath" in helper[cleanup:cleanup_end]


def test_python_side_sniffs_helper_for_marker_support():
    """The payload helper of the release being installed must understand the
    marker protocol; the updater verifies that by searching the downloaded
    helper bytes for the marker name and falls back to the full download
    otherwise."""
    updater = _updater_text()
    assert "_STAGING_MARKER_NAME.encode" in updater


def test_download_lock_is_not_the_startup_gate_lock():
    """The diff download holds its own lock so CleanupUpdateLeftovers skips
    the workdir, but it must NOT reuse update_in_progress.lock — that one
    also blocks app startup via the main.py gate, and starting the app while
    a background download runs is legitimate."""
    updater = _updater_text()
    assert '_DOWNLOAD_LOCK_NAME = "download_in_progress.lock"' in updater
    main_py = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    assert "download_in_progress.lock" not in main_py
