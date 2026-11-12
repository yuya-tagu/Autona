"""Tint the Windows 11 title bar to the app's palette.

Windows' dark title bar is neutral (32,32,32) while the app's sidebar, cards
and status bar use the blue-tinted ``bg_secondary``; the mismatch is visible.
``DWMWA_CAPTION_COLOR`` (Windows 11 build 22000+) lets a window pick its own
caption color. Everything here is best effort: on Windows 10, non-Windows
platforms, or any failure it silently does nothing.

Applied to every top-level window by one app-level event filter
(``TitleBarThemer``), so settings / guide / About dialogs need no code.
"""

from __future__ import annotations

import sys
import weakref

from loguru import logger
from qtpy.QtCore import QObject, Qt

from .. import ui_constants

DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def _colorref(hex_color: str) -> int:
    """'#RRGGBB' -> COLORREF 0x00BBGGRR."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def _dwm_set(hwnd: int, attribute: int, colorref: int) -> bool:
    import ctypes
    from ctypes import wintypes

    value = wintypes.DWORD(colorref)
    hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd), wintypes.DWORD(attribute),
        ctypes.byref(value), ctypes.sizeof(value))
    return hr == 0


def ApplyTitleBarTheme(widget) -> bool:
    """Set caption, text and border colors of a top-level widget's title bar.

    Returns True if the caption color was applied. Never raises.
    """
    if sys.platform != "win32":
        return False
    try:
        p = ui_constants.palette()
        hwnd = int(widget.winId())
        ok = _dwm_set(hwnd, DWMWA_CAPTION_COLOR, _colorref(p["bg_secondary"]))
        if ok:
            _dwm_set(hwnd, DWMWA_TEXT_COLOR, _colorref(p["text_primary"]))
            _dwm_set(hwnd, DWMWA_BORDER_COLOR, _colorref(p["border_default"]))
        return ok
    except Exception:
        logger.opt(exception=True).debug("Could not theme the title bar.")
        return False


_THEMED_TYPES = (Qt.WindowType.Window, Qt.WindowType.Dialog)


class TitleBarThemer(QObject):
    """Themes each normal window / dialog once, when it first takes focus.

    Connected to ``QGuiApplication.focusWindowChanged`` (no per-event filter
    cost). Popups, tooltips and splash screens are skipped so Windows 11 menu
    borders keep their own colors.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._done = weakref.WeakSet()

    def OnFocusWindowChanged(self, window) -> None:
        self.Apply(window)

    def Apply(self, window) -> bool:
        """Theme a QWindow once; returns True if it was applied now."""
        try:
            if window is None or window in self._done:
                return False
            if (window.type() & Qt.WindowType.WindowType_Mask) not in _THEMED_TYPES:
                return False
            self._done.add(window)
            return ApplyTitleBarTheme(window)
        except Exception:
            logger.opt(exception=True).debug("Title bar themer failed.")
            return False


def Install(app) -> TitleBarThemer:
    """Wire the themer to the application's focus changes."""
    themer = TitleBarThemer(app)
    app.focusWindowChanged.connect(themer.OnFocusWindowChanged)
    return themer
