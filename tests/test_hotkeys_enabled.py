"""
Tests for the hotkeys.hotkeys_enabled setting (AutonaHotkeys).

Win32 RegisterHotKey grabs a combination system-wide, so checking the setting
only inside HotkeyTriggered() is not enough: disabling hotkeys would still
swallow Ctrl+Shift+1 etc. for every other application. SetupHotkeys() must
therefore skip registration while disabled, and ApplyEnabledSetting() must
release the existing grabs the moment the checkbox is unticked.

AutonaHotkeys uses relative imports (SettingsManager, Helpers), so conftest's
direct_import can't be used; a fake parent package is registered instead
(agent-pitfalls #1). SettingsManager does file I/O relative to CWD at import
time, so the import happens inside a temp working directory.

The user32 calls are mocked: the tests assert the registration *decisions*,
never the Windows API behaviour.
"""
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

if sys.platform != "win32":
    # ctypes.windll only exists on Windows; the module can't even be imported.
    pytest.skip("Windows-only global hotkeys", allow_module_level=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

_WORK_DIR = tempfile.mkdtemp(prefix="autona_hotkeys_enabled_test_")
_ORIGINAL_CWD = os.getcwd()


def _import_hotkeys():
    os.chdir(_WORK_DIR)
    os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)
    try:
        pkg = types.ModuleType("autona_hk_enabled_test_pkg")
        pkg.__path__ = [str(SRC_ROOT)]
        sys.modules["autona_hk_enabled_test_pkg"] = pkg
        spec = importlib.util.spec_from_file_location(
            "autona_hk_enabled_test_pkg.AutonaHotkeys",
            SRC_ROOT / "AutonaHotkeys.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["autona_hk_enabled_test_pkg.AutonaHotkeys"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.chdir(_ORIGINAL_CWD)


_mod = _import_hotkeys()


class _EnabledFlag:
    """Mutable holder so a test can flip the setting mid-scenario."""

    def __init__(self, value=True):
        self.value = value

    def get(self, key, default=None):
        if key == "hotkeys.hotkeys_enabled":
            return self.value
        return default


@pytest.fixture
def enabled():
    """Control hotkeys.hotkeys_enabled; other keys fall back to the default."""
    flag = _EnabledFlag(True)
    with patch.object(_mod.SettingsManager, "Get", side_effect=flag.get):
        yield flag


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
def hotkeys(enabled, user32):
    hk = _mod.AutonaHotkeys()
    hk.loaded_keys = {
        "reset_scores": "Ctrl+Shift+R",
        "swap_teams": "Ctrl+Shift+S",
    }
    return hk


def test_apply_disabled_releases_os_grabs(hotkeys, user32, enabled):
    """Unticking the checkbox must unregister, not merely ignore events."""
    hotkeys.SetupHotkeys()
    registered_ids = list(hotkeys._registered_ids.keys())
    assert len(registered_ids) == 2
    user32.RegisterHotKey.reset_mock()
    user32.UnregisterHotKey.reset_mock()

    enabled.value = False
    hotkeys.ApplyEnabledSetting()

    unregistered = [c[0][1] for c in user32.UnregisterHotKey.call_args_list]
    assert sorted(unregistered) == sorted(registered_ids)
    assert hotkeys._registered_ids == {}
    assert user32.RegisterHotKey.call_count == 0


def test_apply_enabled_registers(hotkeys, user32, enabled):
    """Re-ticking the checkbox re-registers every configured key."""
    enabled.value = True

    hotkeys.ApplyEnabledSetting()

    assert user32.RegisterHotKey.call_count == 2
    assert sorted(hotkeys._registered_ids.values()) == [
        "reset_scores", "swap_teams"]


def test_setup_registers_nothing_while_disabled(hotkeys, user32, enabled):
    """ReloadHotkeys() path: saving a shortcut while disabled must not grab."""
    enabled.value = False

    hotkeys.SetupHotkeys()

    assert user32.RegisterHotKey.call_count == 0
    assert hotkeys._registered_ids == {}


def test_resume_after_disabled_while_suspended(hotkeys, user32, enabled):
    """Disabling during a capture: resuming must not re-grab the keys."""
    hotkeys.SetupHotkeys()
    hotkeys.SuspendCapture()
    enabled.value = False
    user32.RegisterHotKey.reset_mock()

    hotkeys.ResumeCapture()

    assert user32.RegisterHotKey.call_count == 0
    assert hotkeys._registered_ids == {}
    assert hotkeys._capture_suspended is False


def test_apply_enabled_while_suspended_defers_to_resume(hotkeys, user32, enabled):
    """The suspend guard wins; ResumeCapture() does the actual registration."""
    hotkeys.SetupHotkeys()
    hotkeys.SuspendCapture()
    user32.RegisterHotKey.reset_mock()

    hotkeys.ApplyEnabledSetting()

    assert user32.RegisterHotKey.call_count == 0
    assert hotkeys._registered_ids == {}

    hotkeys.ResumeCapture()

    assert user32.RegisterHotKey.call_count == 2
    assert sorted(hotkeys._registered_ids.values()) == [
        "reset_scores", "swap_teams"]


def test_hotkey_triggered_still_guards_on_enabled(hotkeys, enabled):
    """The in-handler check stays as a defensive second layer."""
    fakeSignals = MagicMock()
    with patch.object(_mod.AutonaHotkeys, "signals", fakeSignals):
        enabled.value = True
        hotkeys.HotkeyTriggered("reset_scores", "Ctrl+Shift+R")
        assert fakeSignals.reset_scores.emit.call_count == 1

        enabled.value = False
        hotkeys.HotkeyTriggered("reset_scores", "Ctrl+Shift+R")
        assert fakeSignals.reset_scores.emit.call_count == 1
