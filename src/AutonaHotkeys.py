import ctypes
import ctypes.wintypes
import platform
import traceback
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from loguru import logger

from .SettingsManager import SettingsManager
from .Helpers.AutonaDictHelper import deep_clone

# Win32 constants
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

# Virtual key codes for special keys
_VK_MAP = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "tab": 0x09, "return": 0x0D, "enter": 0x0D,
    "escape": 0x1B, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}

user32 = ctypes.windll.user32


def _parse_shortcut(shortcut_str):
    """Parse 'Ctrl+F1' style string into (modifiers, vk) tuple."""
    parts = shortcut_str.lower().split("+")
    modifiers = MOD_NOREPEAT
    vk = 0

    for part in parts:
        part = part.strip()
        if part == "ctrl":
            modifiers |= MOD_CONTROL
        elif part == "alt":
            modifiers |= MOD_ALT
        elif part == "shift":
            modifiers |= MOD_SHIFT
        elif part in _VK_MAP:
            vk = _VK_MAP[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            logger.warning(f"Unknown key in shortcut: {part}")
            return None

    if vk == 0:
        return None
    return (modifiers, vk)


class _HotkeyFilter(QAbstractNativeEventFilter):
    """Receives WM_HOTKEY from the Windows message loop."""

    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._callback(msg.wParam)
                return True, 0
        return False, 0


class AutonaHotkeysSignals(QObject):
    team1_score_up = Signal()
    team1_score_down = Signal()
    team2_score_up = Signal()
    team2_score_down = Signal()
    reset_scores = Signal()
    load_set = Signal()
    swap_teams = Signal()
    refresh_phase_group = Signal()
    limit_export = Signal()


class AutonaHotkeys(QObject):
    instance: "AutonaHotkeys" = None
    signals = AutonaHotkeysSignals()
    parent: QWidget = None
    shortcuts = {}

    keys = {
        "load_set": "Ctrl+O",
        "team1_score_up": "Ctrl+F1",
        "team1_score_down": "Ctrl+F2",
        "team2_score_up": "Ctrl+F3",
        "team2_score_down": "Ctrl+F4",
        "reset_scores": "Ctrl+R",
        "swap_teams": "Ctrl+S",
        "refresh_phase_group": "Ctrl+P",
        "limit_export": "Ctrl+B",
    }

    loaded_keys = {}

    def __init__(self) -> None:
        super().__init__()
        self._registered_ids = {}  # hotkey_id -> action_key
        self._next_id = 1
        self._filter = None
        self.LoadUserHotkeys()

    def UiMounted(self, parent):
        self.parent = parent
        self.SetupHotkeys()

    def ReloadHotkeys(self):
        self.LoadUserHotkeys()
        self.SetupHotkeys()

    def SetupHotkeys(self):
        self._unregisterAll()

        if platform.system() != "Windows":
            logger.warning("Global hotkeys only supported on Windows")
            return

        # Install native event filter once
        if self._filter is None:
            self._filter = _HotkeyFilter(self._onHotkey)
            QApplication.instance().installNativeEventFilter(self._filter)

        for action_key, shortcut_str in self.loaded_keys.items():
            parsed = _parse_shortcut(shortcut_str)
            if parsed is None:
                logger.warning(
                    f"Could not parse hotkey: {shortcut_str} for {action_key}")
                continue

            modifiers, vk = parsed
            hotkey_id = self._next_id
            self._next_id += 1

            if user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
                self._registered_ids[hotkey_id] = action_key
                logger.debug(
                    f"Registered hotkey {shortcut_str} -> {action_key} (id={hotkey_id})")
            else:
                logger.warning(
                    f"Failed to register hotkey {shortcut_str} for {action_key}")

        logger.info(
            f"Global hotkeys registered: {len(self._registered_ids)} keys")

    def _unregisterAll(self):
        for hotkey_id in list(self._registered_ids.keys()):
            user32.UnregisterHotKey(None, hotkey_id)
        self._registered_ids.clear()

    def _onHotkey(self, hotkey_id):
        action_key = self._registered_ids.get(hotkey_id)
        if action_key:
            self.HotkeyTriggered(
                action_key, self.loaded_keys.get(action_key, ""))

    def HotkeyTriggered(self, k, v):
        if not SettingsManager.Get("hotkeys.hotkeys_enabled", True) == False:
            logger.info(f"Activated {k} by pressing {v}")
            getattr(self.signals, k).emit()

    def LoadUserHotkeys(self):
        user_keys = SettingsManager.Get("hotkeys", {})
        self.loaded_keys = deep_clone(self.keys)
        self.loaded_keys.update(
            (k, v) for k, v in user_keys.items() if k in self.keys)
        logger.info("User hotkeys loaded")


AutonaHotkeys.instance = AutonaHotkeys()
