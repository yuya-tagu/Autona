"""Title bar theming (DWMWA_CAPTION_COLOR): right attributes/COLORREF, never raises."""

from unittest import mock

from qtpy.QtCore import Qt

from src import ui_constants
from src.Helpers import AutonaTitleBar as TB


class _Widget:
    def winId(self):
        return 1234


def test_colorref_is_bgr():
    assert TB._colorref("#191B20") == 0x201B19
    assert TB._colorref("#010203") == 0x030201


def test_applies_palette_colors_with_the_right_attributes():
    calls = []
    p = ui_constants.palette()
    with mock.patch.object(TB.sys, "platform", "win32"), \
            mock.patch.object(TB, "_dwm_set",
                              side_effect=lambda h, a, c: calls.append((h, a, c)) or True):
        assert TB.ApplyTitleBarTheme(_Widget()) is True
    assert calls == [
        (1234, 35, TB._colorref(p["bg_secondary"])),
        (1234, 36, TB._colorref(p["text_primary"])),
        (1234, 34, TB._colorref(p["border_default"])),
    ]


def test_caption_failure_skips_the_rest_quietly():
    with mock.patch.object(TB.sys, "platform", "win32"), \
            mock.patch.object(TB, "_dwm_set", return_value=False) as m:
        assert TB.ApplyTitleBarTheme(_Widget()) is False
    assert m.call_count == 1


def test_exception_is_swallowed():
    with mock.patch.object(TB.sys, "platform", "win32"), \
            mock.patch.object(TB, "_dwm_set", side_effect=OSError("boom")):
        assert TB.ApplyTitleBarTheme(_Widget()) is False


def test_non_windows_does_nothing():
    with mock.patch.object(TB.sys, "platform", "linux"), \
            mock.patch.object(TB, "_dwm_set") as m:
        assert TB.ApplyTitleBarTheme(_Widget()) is False
    m.assert_not_called()


class _Win:
    def __init__(self, t):
        self._t = t

    def type(self):
        return self._t

    def winId(self):
        return 7


def _applied(themer, win):
    with mock.patch.object(TB, "ApplyTitleBarTheme", return_value=True) as m:
        themer.Apply(win)
    return m.call_count


def test_windows_and_dialogs_are_themed_popups_are_not():
    t = TB.TitleBarThemer()
    assert _applied(t, _Win(Qt.WindowType.Window)) == 1
    assert _applied(t, _Win(Qt.WindowType.Dialog)) == 1
    for kind in (Qt.WindowType.Popup, Qt.WindowType.ToolTip,
                 Qt.WindowType.SplashScreen):
        assert _applied(t, _Win(kind)) == 0
    assert _applied(t, None) == 0


def test_the_same_window_is_themed_once():
    t = TB.TitleBarThemer()
    w = _Win(Qt.WindowType.Window)
    assert _applied(t, w) == 1
    assert _applied(t, w) == 0
