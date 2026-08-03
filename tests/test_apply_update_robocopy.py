"""Copy policy of the external update helper (assets/apply_update.ps1).

The helper mirrors the release over the install dir in two robocopy passes, and
the flags of each pass are load-bearing in opposite directions:

- ``_internal`` MUST be mirrored with ``/MIR`` (purge). That tree is owned 100%
  by the release. Without a purge, packages dropped from a newer build's
  PyInstaller ``excludes`` survive as leftover binary-only directories, Python
  imports them as namespace packages, and they shadow the new PYZ — the
  v1.4.1 -> v1.5.0 auto-update left a gutted ``_internal/numpy`` behind, which
  defeated deepdiff's ``except ImportError`` fallback and made the app
  unstartable. A full-zip install never hit this (clean tree), so only
  auto-updated machines broke.
- Everything else MUST NOT purge: user-side assets (layout/, user_data/,
  uploaded avatars, ...) live under the install root and are absent from the
  release zip. That pass therefore uses ``/E`` plus ``/XD`` for the already
  mirrored ``_internal``.

Asserted at TEXT level like tests/test_diff_update_contract.py — the helper is
PowerShell, and running it would touch a real install dir.
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HELPER = PROJECT_ROOT / "assets" / "apply_update.ps1"


def _helper_text() -> str:
    return HELPER.read_bytes().decode("utf-8-sig")


def _pass_arguments(label: str) -> str:
    """The robocopy argument format string of the named Invoke-RobocopyPass."""
    helper = _helper_text()
    m = re.search(
        r'Invoke-RobocopyPass\s+"%s"\s+\(\'([^\']*)\'' % re.escape(label),
        helper)
    assert m, f'no Invoke-RobocopyPass "{label}" call in {HELPER.name}'
    return m.group(1)


def test_internal_pass_purges():
    args = _pass_arguments("_internal")
    assert "/MIR" in args, (
        "_internal must be mirrored with /MIR or stale packages from a "
        "previous version survive and shadow the new PYZ as namespace packages"
    )


def test_internal_pass_targets_the_internal_trees():
    helper = _helper_text()
    assert '$StagingInternal, $InstallInternal)' in helper
    assert '(Join-Path $Staging "_internal")' in helper
    assert '(Join-Path $InstallDir "_internal")' in helper


def test_root_pass_never_purges_and_skips_internal():
    args = _pass_arguments("root")
    assert "/E" in args
    assert "/XD" in args, "_internal is already mirrored; exclude it here"
    assert "/PURGE" not in args, (
        "purging the install root would delete user-side assets the release "
        "zip does not ship (layout/, user_data/, uploaded avatars)"
    )
    assert "/MIR" not in args, "/MIR implies /PURGE — same data loss"


def test_internal_pass_runs_before_the_root_pass():
    """_internal is the runtime the relaunched exe loads: fail before the
    launcher is swapped, never leave a new exe on an old runtime."""
    helper = _helper_text()
    assert (helper.index('Invoke-RobocopyPass "_internal"')
            < helper.index('Invoke-RobocopyPass "root"'))


def test_missing_staging_internal_aborts_instead_of_mirroring():
    """/MIR from a missing source would DELETE the installed runtime, so an
    unexpected staging layout has to fail like the missing-exe guard does."""
    helper = _helper_text()
    guard = helper.index("staging has no _internal")
    assert guard < helper.index('Invoke-RobocopyPass "_internal"')
    assert "Stop-UpdateAndExit" in helper[guard - 200:guard + 200]


def test_success_still_logs_robocopy_ok():
    """The release runbook greps update_log.txt for
    'waiting for pid -> robocopy ok -> update complete'."""
    assert 'Write-Log "robocopy ok' in _helper_text()
