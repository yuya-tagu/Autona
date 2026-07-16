"""Encoding contract for the external update helper.

apply_update.ps1 is run by Windows PowerShell 5.1 (powershell.exe, see
_LaunchHelper in src/AutonaUpdater.py). PS 5.1 decodes a .ps1 with no BOM using
the ANSI codepage — 932 on the Japanese Windows this ships to — so the moment
the helper gained Japanese strings for its install window, a BOM-less file
started rendering them as mojibake. The failure is invisible in review and only
shows up on an end user's screen mid-update, so pin it here.

Also pins the window title, which is a contract between the helper and the
startup gate in main.py (FindWindowW looks it up by exact title).
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HELPER = PROJECT_ROOT / "assets" / "apply_update.ps1"
UTF8_BOM = b"\xef\xbb\xbf"

# Must match _focus_install_window() in main.py.
WINDOW_TITLE = "Autona Updater"


def test_helper_exists():
    assert HELPER.is_file(), f"{HELPER} is missing"


def test_helper_starts_with_utf8_bom():
    head = HELPER.read_bytes()[:3]
    assert head == UTF8_BOM, (
        "assets/apply_update.ps1 must be UTF-8 with BOM or Windows PowerShell "
        f"5.1 decodes its Japanese strings as cp932 mojibake (got {head!r})"
    )


def test_helper_is_valid_utf8():
    HELPER.read_bytes().decode("utf-8-sig")


def test_helper_declares_the_window_title_main_py_looks_up():
    body = HELPER.read_bytes().decode("utf-8-sig")
    assert f'$UI_TITLE = "{WINDOW_TITLE}"' in body

    main_py = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    assert f'FindWindowW(None, "{WINDOW_TITLE}")' in main_py


def test_startup_gate_shows_no_modal_while_an_update_is_in_flight():
    """A modal here keeps Autona.exe alive, and a running image is locked
    against writes — robocopy then cannot replace the launcher, the helper
    gives up, and the user sees a silent rollback to the old version."""
    main_py = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    gate = main_py.split("if _update_in_flight():")[1].split("import src")[0]
    assert "MessageBoxW" not in gate
