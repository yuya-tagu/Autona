"""
Tests for AutonaHotkeys capture suspension (SuspendCapture / ResumeCapture).

Win32 RegisterHotKey grabs a combination system-wide, so while the settings
hotkey field records a key the registrations must be released -- otherwise
pressing an assigned shortcut fires the action and the QKeySequenceEdit never
sees it. ReloadHotkeys() also runs on every save, i.e. while the field still
has focus, so SetupHotkeys() must stay a no-op (registration-wise) until the
capture ends.

Importing the module is enough to drive it: neither AutonaHotkeys nor the
SettingsManager it pulls in builds anything or touches the filesystem at
import time (pinned by tests/test_import_side_effects.py).

The user32 calls are mocked: the tests assert the registration *decisions*,
never the Windows API behaviour.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

if sys.platform != "win32":
    # ctypes.windll only exists on Windows; the module can't even be imported.
    pytest.skip("Windows-only global hotkeys", allow_module_level=True)

from src import AutonaHotkeys as _mod


@pytest.fixture
def user32():
    """Stub the Windows API so no real hotkey is ever registered."""
    fake = MagicMock()
    fake.RegisterHotKey.return_value = 1
    fake.UnregisterHotKey.return_value = 1
    with patch.object(_mod, "user32", fake):
        # SetupHotkeys installs a native event filter on the running
        # QApplication; there is none in a unit test.
        with patch.object(_mod, "QApplication", MagicMock()):
            yield fake


@pytest.fixture
def hotkeys(user32):
    hk = _mod.AutonaHotkeys()
    hk.loaded_keys = {
        "reset_scores": "Ctrl+Shift+R",
        "swap_teams": "Ctrl+Shift+S",
    }
    return hk


def test_setup_registers_when_not_suspended(hotkeys, user32):
    hotkeys.SetupHotkeys()

    assert user32.RegisterHotKey.call_count == 2
    assert sorted(hotkeys._registered_ids.values()) == [
        "reset_scores", "swap_teams"]


def test_suspend_unregisters_and_blocks_setup(hotkeys, user32):
    hotkeys.SetupHotkeys()
    user32.RegisterHotKey.reset_mock()

    hotkeys.SuspendCapture()

    assert user32.UnregisterHotKey.call_count == 2
    assert hotkeys._registered_ids == {}

    # ReloadHotkeys() fires on every save while the field still has focus.
    hotkeys.SetupHotkeys()

    assert user32.RegisterHotKey.call_count == 0
    assert hotkeys._registered_ids == {}


def test_resume_restores_registration(hotkeys, user32):
    hotkeys.SetupHotkeys()
    hotkeys.SuspendCapture()
    user32.RegisterHotKey.reset_mock()

    hotkeys.ResumeCapture()

    assert user32.RegisterHotKey.call_count == 2
    assert sorted(hotkeys._registered_ids.values()) == [
        "reset_scores", "swap_teams"]
    assert hotkeys._capture_suspended is False


def test_resume_uses_keys_loaded_while_suspended(hotkeys, user32):
    hotkeys.SetupHotkeys()
    hotkeys.SuspendCapture()

    # What ReloadHotkeys() does after a successful save: refresh loaded_keys,
    # then SetupHotkeys() (which stays suspended).
    hotkeys.loaded_keys = {"reset_scores": "Ctrl+Shift+F5"}
    hotkeys.SetupHotkeys()
    user32.RegisterHotKey.reset_mock()

    hotkeys.ResumeCapture()

    assert user32.RegisterHotKey.call_count == 1
    assert list(hotkeys._registered_ids.values()) == ["reset_scores"]
    # F5 = 0x74, with Ctrl|Shift|NOREPEAT modifiers.
    _, _, modifiers, vk = user32.RegisterHotKey.call_args[0]
    assert vk == 0x74
    assert modifiers == (_mod.MOD_CONTROL | _mod.MOD_SHIFT | _mod.MOD_NOREPEAT)


def test_double_suspend_is_idempotent(hotkeys, user32):
    hotkeys.SetupHotkeys()
    hotkeys.SuspendCapture()
    user32.UnregisterHotKey.reset_mock()

    hotkeys.SuspendCapture()

    assert user32.UnregisterHotKey.call_count == 0
    assert hotkeys._capture_suspended is True

    # A single resume still restores everything.
    hotkeys.ResumeCapture()
    assert hotkeys._capture_suspended is False
    assert len(hotkeys._registered_ids) == 2


def test_double_resume_is_idempotent(hotkeys, user32):
    hotkeys.SetupHotkeys()
    hotkeys.SuspendCapture()
    hotkeys.ResumeCapture()
    registered = dict(hotkeys._registered_ids)
    user32.RegisterHotKey.reset_mock()
    user32.UnregisterHotKey.reset_mock()

    hotkeys.ResumeCapture()

    assert user32.RegisterHotKey.call_count == 0
    assert user32.UnregisterHotKey.call_count == 0
    assert hotkeys._registered_ids == registered


def test_resume_without_suspend_does_nothing(hotkeys, user32):
    hotkeys.ResumeCapture()

    assert user32.RegisterHotKey.call_count == 0
    assert hotkeys._capture_suspended is False
