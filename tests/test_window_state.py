"""What the main window keeps between launches (ADR-005 S-final SF-5b-3).

The window is the shared view alone: it reopens on the page shown last
(QSettings ``lastPanel``, a panel's name; a page of the Help & Settings menu
is never one, the page before it is) with the AppShell's sidebar hidden or
not as it was (``navCollapsed``). The Qt sidebar's keys (``sidebarIndex_v22``
/ ``sidebarCollapsed_v22``) are read into those once and removed: the index
through a fixed table of the Qt sidebar's items at that version.

src/AutonaWindowState.py's functions over a settings object (a dict-backed
stand-in, and the real QSettings in an .ini file), and the real window in a
process of its own (importing src/Autona.py is the app's start) reading and
saving them.
"""
import pytest
from qtpy.QtCore import QSettings

from src import AutonaWindowState as state
from src.core import WebAccess
from tests.test_bootstrap_split import _PRELUDE, _free_port, _probe


class _Settings:
    """QSettings' contains / value / setValue / remove over a dict, values
    kept as given (the registry hands ints and bools back as they were
    written)."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def contains(self, key):
        return key in self.values

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def remove(self, key):
        self.values.pop(key, None)


def test_the_tables_are_panels_and_the_legacy_one_is_fixed():
    # The Qt sidebar's items at STATE_VERSION 22, in its order (the
    # AppShell's navigation is the same today: tests/test_app_shell.py).
    assert state.LEGACY_SIDEBAR_PANELS == (
        "event", "stream", "scoreboard", "board", "thumbnail", "result",
        "top8", "commentary", "statsPreview", "stats", "playerBrowser")
    assert state.MENU_PAGES == (
        "settings", "api", "companion", "obs", "about", "assets")
    for panel in (*state.LEGACY_SIDEBAR_PANELS, *state.MENU_PAGES):
        assert WebAccess.IsWebPanel(panel), panel
    assert set(state.LEGACY_SIDEBAR_PANELS).isdisjoint(state.MENU_PAGES)
    assert state.DEFAULT_PANEL == state.LEGACY_SIDEBAR_PANELS[0]


@pytest.mark.parametrize("index, panel", [
    (0, "event"), (4, "thumbnail"), (10, "playerBrowser"), ("7", "commentary"),
    # Out of the table (a menu page's index was never saved; Settings was
    # the first after the items), or not a number: the first item, as the Qt
    # window stayed on it.
    (11, "event"), (-1, "event"), (99, "event"), ("x", "event"),
    (None, "event"), (True, "event")])
def test_the_qt_sidebars_index_names_its_item(index, panel):
    assert state.LegacyPanel(index) == panel


def test_the_qt_sidebars_index_is_read_once_and_removed():
    settings = _Settings({"sidebarIndex_v22": 4})

    assert state.RestorePanel(settings) == "thumbnail"
    assert settings.values == {"lastPanel": "thumbnail"}
    # Read once: the next launch reads lastPanel.
    settings.setValue("lastPanel", "stats")
    assert state.RestorePanel(settings) == "stats"


def test_a_page_saved_already_wins_over_the_qt_sidebars_index():
    # (A launch of this version, then an older one writing the old key.)
    settings = _Settings({"sidebarIndex_v22": 4, "lastPanel": "top8"})

    assert state.RestorePanel(settings) == "top8"
    assert settings.values == {"lastPanel": "top8"}


@pytest.mark.parametrize("saved", [None, "settings", "about", "unknown", 3,
                                   ""])
def test_a_page_not_to_reopen_on_opens_the_first(saved):
    settings = _Settings({} if saved is None else {"lastPanel": saved})
    assert state.RestorePanel(settings) == "event"


@pytest.mark.parametrize("legacy, collapsed", [
    (True, True), (False, False), ("true", True), ("false", False),
    ("True", True), (1, True), (0, False)])
def test_the_qt_sidebars_collapse_is_read_once_and_removed(legacy, collapsed):
    settings = _Settings({"sidebarCollapsed_v22": legacy})

    assert state.RestoreNavCollapsed(settings) is collapsed
    assert settings.values == {"navCollapsed": collapsed}


def test_a_collapse_saved_already_wins_and_none_is_shown():
    settings = _Settings({"sidebarCollapsed_v22": True,
                          "navCollapsed": "false"})
    assert state.RestoreNavCollapsed(settings) is False
    assert settings.values == {"navCollapsed": "false"}
    assert state.RestoreNavCollapsed(_Settings()) is False


def test_a_menu_page_keeps_the_page_before_it():
    assert state.RememberedPanel("stream", "event") == "stream"
    for menu in state.MENU_PAGES:
        assert state.RememberedPanel(menu, "stream") == "stream"
    assert state.RememberedPanel("not-a-panel", "stream") == "stream"


def test_the_window_saves_a_page_to_reopen_on_only():
    settings = _Settings({"lastPanel": "stats"})

    state.SaveWindowState(settings, "settings", True)
    assert settings.values == {"lastPanel": "stats", "navCollapsed": True}

    state.SaveWindowState(settings, "board", 0)
    assert settings.values == {"lastPanel": "board", "navCollapsed": False}


def test_the_real_qsettings_round_trip(tmp_path, qapp):
    # An .ini file (text: ints and bools come back as strings).
    path = str(tmp_path / "qt.ini")
    settings = QSettings(path, QSettings.Format.IniFormat)
    settings.setValue("sidebarIndex_v22", 9)
    settings.setValue("sidebarCollapsed_v22", True)
    settings.setValue("geometry", b"kept")
    settings.sync()

    again = QSettings(path, QSettings.Format.IniFormat)
    assert state.RestorePanel(again) == "stats"
    assert state.RestoreNavCollapsed(again) is True
    state.SaveWindowState(again, "commentary", False)
    again.sync()

    last = QSettings(path, QSettings.Format.IniFormat)
    assert sorted(last.allKeys()) == ["geometry", "lastPanel", "navCollapsed"]
    assert state.RestorePanel(last) == "commentary"
    assert state.RestoreNavCollapsed(last) is False


# The real window, in a process of its own (as tests/test_battle_format.py's
# probe: a throwaway working directory, offscreen, no global hotkeys, no
# start.gg download, Qt's settings in a file there, the event loop never
# run). The Qt sidebar's keys are there from an older version.
_WINDOW_SCRIPT = _PRELUDE + r'''
from pathlib import Path

Path("user_data/settings.json").write_text(json.dumps({
    "general": {"webserver_port": int(sys.argv[2]),
                "webserver_listen_lan": False},
    "hotkeys": {"hotkeys_enabled": False},
}), encoding="utf-8")

from qtpy.QtCore import QSettings, Qt
from qtpy.QtWidgets import QApplication

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
app = QApplication([])

qtSettings = str(Path("qt_settings.ini").resolve())
older = QSettings(qtSettings, QSettings.Format.IniFormat)
older.setValue("sidebarIndex_v22", 7)
older.setValue("sidebarCollapsed_v22", True)
older.sync()
del older

from src import Autona
from src.AutonaGameAssetManager import AutonaGameAssetManager

Autona.QSettings = lambda *args: QSettings(
    qtSettings, QSettings.Format.IniFormat)
AutonaGameAssetManager.instance.DownloadStartGGCharacters = lambda: None

window = Autona.Window()
host = window._webPanelHost
steps = {"launch": {
    "central": window.centralWidget() is host,
    "panel": host.CurrentPanel(),
    "nav": host.NavCollapsed(),
    "remember": window._panelToRemember,
    "size": [window.width() > 400, window.height() > 400],
}}

# The operator moves on (the AppShell's navigation, as the host follows
# its URL), then opens a menu page and shows the sidebar again.
host.ShowPanel("top8")
host.ShowPanel("settings")
host.SetNavCollapsed(False)
steps["moved"] = {"panel": host.CurrentPanel(),
                  "remember": window._panelToRemember}

window.close()
from src import AutonaWindowState
saved = QSettings(qtSettings, QSettings.Format.IniFormat)
steps["saved"] = {"keys": sorted(saved.allKeys()),
                  "panel": AutonaWindowState.RestorePanel(saved),
                  "nav": AutonaWindowState.RestoreNavCollapsed(saved)}
report(steps)
'''


def test_the_real_window_reopens_where_it_was_and_keeps_it(tmp_path):
    steps = _probe(tmp_path, _WINDOW_SCRIPT, str(_free_port()))

    # The Qt sidebar's Commentary item, hidden: the AppShell on that page,
    # its sidebar hidden, the window's one widget.
    assert steps["launch"] == {"central": True, "panel": "commentary",
                               "nav": True, "remember": "commentary",
                               "size": [True, True]}
    assert steps["moved"] == {"panel": "settings", "remember": "top8"}
    # Saved on close under the new keys (the page before the menu page);
    # the old keys are gone.
    assert steps["saved"] == {
        "keys": ["geometry", "lastPanel", "navCollapsed"], "panel": "top8",
        "nav": False}
