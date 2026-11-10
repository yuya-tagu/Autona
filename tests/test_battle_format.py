"""The battle format switch (ADR-005 S4x).

The sidebar's 1v1 / Team / Crew segment was the window's SetBattleFormat:
it wrote the settings, showed the format's board and told the result's
layout. The switch is core's now (AutonaActions.SetBattleFormat) and the
desktop shows the board from its notice (battleFormatChanges ->
StateManagerBridge.battle_format_changed -> Window._ShowBattleFormat), so
the same switch is reachable without the window:

- ``GET /api/v1/battle-format``  ``{"format", "board"}`` (READ)
- ``PUT /api/v1/battle-format``  ``{"format"}`` -> ``+ "changed"`` (WRITE)

Through Flask's test client against the real route table, on both shells.
An unchanged request writes and tells nothing.
"""
import copy
import threading

import pytest

from src.core import StateSchema as schema
from src.core import WebAccess
from src.core.Actions import AutonaActions
from src.core.ResultService import ResultService
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager
from src.StateManagerBridge import StateManagerBridge
from tests.test_api_v1 import (  # noqa: F401
    _assertRejected, _requestOnWorkerThread, client, webApp)
from tests.test_api_v1_score import _ok, lanServer  # noqa: F401
from tests.test_bootstrap_split import _PRELUDE, _free_port, _probe
from tests.test_web_access import LAN_PEER
from tests.test_web_operations import _goHeadless

URL = "/api/v1/battle-format"


@pytest.fixture
def settings(autonaApp, monkeypatch):
    """The settings in a private copy, not saved, starting on 1v1; the
    result's layout the switch writes (in the harness's runtime directory),
    put back afterwards."""
    monkeypatch.setattr(SettingsManager, "settings",
                        copy.deepcopy(SettingsManager.settings))
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    scoreboard = SettingsManager.settings.setdefault("scoreboard", {})
    scoreboard["battle_format"] = "singles"
    scoreboard["team_mode"] = False
    layout = StateManager.Get(schema.RESULT_LAYOUT_ROOT)
    yield SettingsManager.settings
    if StateManager.Get(schema.RESULT_LAYOUT_ROOT) != layout:
        StateManager.Set(schema.RESULT_LAYOUT_ROOT, layout)
    autonaApp.settle()


@pytest.fixture(params=["desktop", "headless"])
def shell(request, autonaApp, monkeypatch, settings):
    if request.param == "headless":
        _goHeadless(autonaApp, monkeypatch)
    try:
        yield request.param
    finally:
        if request.param == "headless":
            for number in (1, 2, 3):
                StateManager.Unset(f"score.{number}")
            autonaApp.settle()


@pytest.fixture
def told():
    """What the switch told (AutonaActions.battleFormatChanges)."""
    formats = []
    AutonaActions.battleFormatChanges.subscribe(formats.append)
    try:
        yield formats
    finally:
        AutonaActions.battleFormatChanges.unsubscribe(formats.append)


@pytest.fixture
def settingWrites(monkeypatch):
    """Every SettingsManager.Set, as (key, value)."""
    writes = []
    realSet = SettingsManager.Set

    def recordedSet(key, value):
        writes.append((key, copy.deepcopy(value)))
        realSet(key, value)

    monkeypatch.setattr(SettingsManager, "Set", staticmethod(recordedSet))
    return writes


def _put(client, fmt):
    return _ok(client.put(URL, json={"format": fmt}))


# ── the read ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt,board", [
    ("singles", 1), ("team", 2), ("crew", 3)])
def test_the_read_is_the_format_in_force_and_its_board(
        client, settings, fmt, board):
    settings["scoreboard"]["battle_format"] = fmt
    data = _ok(client.get(URL))["data"]
    assert data == {"format": fmt, "board": board}
    # health's activeBoard, which hotkeys and controllers act on.
    assert _ok(client.get("/api/v1/health"))["data"]["activeBoard"] == board


def test_the_read_carries_the_legacy_team_mode(client, settings):
    """A settings file from before battle_format: team_mode decides, as the
    window's resolver did (ResolveBattleFormat)."""
    del settings["scoreboard"]["battle_format"]
    settings["scoreboard"]["team_mode"] = True
    assert _ok(client.get(URL))["data"] == {"format": "team", "board": 2}


# ── the switch ───────────────────────────────────────────────────────────

def test_the_switch_writes_the_settings_tells_and_answers(
        autonaApp, client, shell, told, settingWrites):
    before = StateManager.revision
    body = _put(client, "team")
    revision = StateManager.revision
    autonaApp.settle()

    assert body["data"] == {"format": "team", "board": 2, "changed": True}
    # The state's revision once the switch is over: the result's layout it
    # may have published is in it.
    assert before <= body["revision"] == revision
    assert ("scoreboard.battle_format", "team") in settingWrites
    assert ("scoreboard.team_mode", True) in settingWrites
    assert told == ["team"]
    assert AutonaActions.instance.ActiveBoard() == 2
    assert _ok(client.get(URL))["data"] == {"format": "team", "board": 2}

    # Crew mirrors team_mode as not-team, as the window's switch did.
    _put(client, "crew")
    assert SettingsManager.Get("scoreboard.team_mode") is False
    assert told == ["team", "crew"]


def test_the_format_in_force_writes_and_tells_nothing(
        autonaApp, client, shell, told, settingWrites):
    before = StateManager.revision
    body = _put(client, "singles")
    autonaApp.settle()

    assert body["data"] == {"format": "singles", "board": 1,
                            "changed": False}
    assert settingWrites == []
    assert told == []
    assert StateManager.revision == before


def test_the_legacy_team_mode_in_force_is_unchanged_too(
        autonaApp, client, settings, told, settingWrites):
    del settings["scoreboard"]["battle_format"]
    settings["scoreboard"]["team_mode"] = True
    assert _put(client, "team")["data"]["changed"] is False
    assert settingWrites == [] and told == []


def test_the_switch_tells_the_result_s_layout(
        autonaApp, client, shell):
    ResultService.instance.SetLayout("horizontal")
    _put(client, "crew")
    autonaApp.settle()
    assert ResultService.instance.Layout() == "team"
    assert StateManager.Get(schema.RESULT_LAYOUT_ROOT) == "team"

    _put(client, "singles")
    autonaApp.settle()
    assert StateManager.Get(schema.RESULT_LAYOUT_ROOT) == "horizontal"


@pytest.mark.parametrize("body", [
    {}, {"format": None}, {"format": "doubles"}, {"format": "Team"},
    {"format": True}, {"format": 2}, {"format": ["team"]},
    {"format": {"team": 1}}])
def test_anything_but_a_format_is_refused_before_anything_is_written(
        autonaApp, client, shell, told, settingWrites, body):
    _assertRejected(client.put(URL, json=body))
    assert settingWrites == [] and told == []
    assert AutonaActions.instance.ResolveBattleFormat() == "singles"


def test_a_body_that_is_not_an_object_is_refused(client, settings, told):
    _assertRejected(client.put(URL, data="team",
                               content_type="application/json"))
    _assertRejected(client.put(URL, json=["team"]))
    assert told == []


def test_core_refuses_what_is_not_a_format(settings, told, settingWrites):
    for value in ("doubles", None, 3, ["team"]):
        with pytest.raises(ValueError):
            AutonaActions.instance.SetBattleFormat(value)
    assert settingWrites == [] and told == []
    # A bool is team / singles, as the window's switch took one.
    assert AutonaActions.instance.SetBattleFormat(True) is True
    assert AutonaActions.instance.ResolveBattleFormat() == "team"
    assert AutonaActions.instance.SetBattleFormat(False) is True
    assert told == ["team", "singles"]


# ── the desktop shows it ─────────────────────────────────────────────────

def _showBoards(fmt):
    # What Window._ShowBattleFormat does with the manager. The window is not
    # built in this process (src/Autona.py is the app's start, agent-pitfalls
    # #1); the real one is, in a process of its own, by
    # test_the_real_window_s_segment_and_board_follow_every_switch below.
    from src.AutonaScoreboardManager import AutonaScoreboardManager
    AutonaScoreboardManager.instance.SetBattleFormat(fmt)


def test_the_desktop_shows_the_board_from_the_notice(
        autonaApp, client, settings):
    autonaApp.dropAllBoards()
    bridge = StateManagerBridge.instance
    bridge.battle_format_changed.connect(_showBoards)
    try:
        _put(client, "team")
        autonaApp.settle()
        board = autonaApp.manager.scoreboards.get(2)
        assert board is not None
        assert not board.isHidden()

        _put(client, "crew")
        autonaApp.settle()
        assert autonaApp.manager.scoreboards[2].isHidden()
        assert not autonaApp.manager.scoreboards[3].isHidden()
    finally:
        bridge.battle_format_changed.disconnect(_showBoards)
        autonaApp.dropAllBoards()


# The real window, in a process of its own: importing src/Autona.py is the
# app's start (bootstrap_core() + bootstrap_gui()), which this process has
# already done its own way (tests/conftest.py), and Window() is built once.
# As tests/test_bootstrap_split.py's probes: a throwaway working directory
# shaped like the app's, offscreen, the result as one JSON line. What the
# window would reach out to is kept in: no global hotkeys, no start.gg
# character download, Qt's own settings (geometry, the sidebar's page) in a
# file there instead of this PC's, and the event loop is never run -- so the
# web server it starts once the window is painted is never built (its port
# and the LAN answer are given all the same).
_WINDOW_SCRIPT = _PRELUDE + r'''
from pathlib import Path

Path("user_data/settings.json").write_text(json.dumps({
    "general": {"webserver_port": int(sys.argv[2]),
                "webserver_listen_lan": False},
    "hotkeys": {"hotkeys_enabled": False},
    "scoreboard": {"battle_format": "team"},
}), encoding="utf-8")

from qtpy.QtCore import QSettings, Qt
from qtpy.QtWidgets import QApplication

# As main.py: before the QApplication, for the web views.
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
app = QApplication([])

from src import Autona
from src.AutonaGameAssetManager import AutonaGameAssetManager
from src.AutonaScoreboardManager import AutonaScoreboardManager
from src.core.Actions import AutonaActions
from src.core.SettingsManager import SettingsManager

qtSettings = str(Path("qt_settings.ini").resolve())
Autona.QSettings = lambda *args: QSettings(
    qtSettings, QSettings.Format.IniFormat)
AutonaGameAssetManager.instance.DownloadStartGGCharacters = lambda: None

window = Autona.Window()
manager = AutonaScoreboardManager.instance
segment = window.sidebar._fmtButtons

def shown():
    """The segment's checked format, the boards the SCORE page shows, the
    boards built, and the format in force."""
    return {
        "segment": sorted(fmt for fmt, button in segment.items()
                          if button.isChecked()),
        "shown": sorted(number for number, board in manager.scoreboards.items()
                        if board.isVisibleTo(manager)),
        "built": sorted(manager.scoreboards),
        "format": SettingsManager.Get("scoreboard.battle_format"),
        "active": AutonaActions.instance.ActiveBoard(),
    }

steps = [["launch", shown()]]

# The sidebar's own segment: a click is core's switch, and the window shows
# the board from its notice.
segment["crew"].click()
steps.append(["segment crew", shown()])

# A click on the format in force: nothing to switch, nothing told.
segment["crew"].click()
steps.append(["segment crew again", shown()])

# From elsewhere -- v1's PUT, through the real route table (on this thread,
# the GUI thread, as the board host runs it) -- and core directly: the
# segment follows as the board does.
from src.AutonaApiV1 import api_v1
from src.AutonaWebServer import _build_web_app

web, _socketio = _build_web_app()
web.register_blueprint(api_v1)
answer = web.test_client().put("/api/v1/battle-format",
                               json={"format": "singles"})
steps.append(["v1 singles", dict(shown(), status=answer.status_code)])

AutonaActions.instance.SetBattleFormat("team")
steps.append(["core team", shown()])

report(steps)
# The app's own way out: the window's close is the shutdown (it saves Qt's
# settings to the file above), then the interpreter's exit joins what still
# writes. os._exit() is not one: it unloads Qt under the live window and
# crashes there (0xC0000005).
window.close()
'''


def test_the_real_window_s_segment_and_board_follow_every_switch(tmp_path):
    steps = dict(_probe(tmp_path, _WINDOW_SCRIPT, str(_free_port())))

    # The launch builds and shows the persisted format's board only.
    assert steps["launch"] == {"segment": ["team"], "shown": [2],
                               "built": [2], "format": "team", "active": 2}
    assert steps["segment crew"] == {"segment": ["crew"], "shown": [3],
                                     "built": [2, 3], "format": "crew",
                                     "active": 3}
    assert steps["segment crew again"] == steps["segment crew"]
    assert steps["v1 singles"] == {"segment": ["singles"], "shown": [1],
                                   "built": [1, 2, 3], "format": "singles",
                                   "active": 1, "status": 200}
    assert steps["core team"] == {"segment": ["team"], "shown": [2],
                                  "built": [1, 2, 3], "format": "team",
                                  "active": 2}


def test_the_notice_reaches_the_gui_thread_from_a_request_thread(
        autonaApp, client, settings):
    """The desktop runs the switch on its GUI thread (the board host's
    Run), so the window's slot -- which builds and shows a board -- runs
    there whatever thread the request came in on."""
    threads = []
    switched = []

    def seen(fmt):
        threads.append((fmt, threading.current_thread()))

    def switching(fmt):
        # Core's notice, on the thread that switched: the board host's Run
        # put the switch on the GUI thread, so the window's slot is direct
        # and has shown the board before the request answers.
        switched.append((fmt, threading.current_thread()))

    bridge = StateManagerBridge.instance
    bridge.battle_format_changed.connect(seen)
    AutonaActions.battleFormatChanges.subscribe(switching)
    try:
        body = _requestOnWorkerThread(
            lambda: client.put(URL, json={"format": "team"}))
        autonaApp.settle()
    finally:
        AutonaActions.battleFormatChanges.unsubscribe(switching)
        bridge.battle_format_changed.disconnect(seen)
    assert body.status_code == 200
    assert switched == [("team", threading.main_thread())]
    assert threads == [("team", threading.main_thread())]


def test_headless_switches_without_a_window(autonaApp, client, settings,
                                            monkeypatch, told):
    _goHeadless(autonaApp, monkeypatch)
    try:
        assert _put(client, "crew")["data"]["changed"] is True
        assert told == ["crew"]
        assert autonaApp.manager.scoreboards == {}
        assert AutonaActions.instance.ActiveBoard() == 3
    finally:
        for number in (1, 2, 3):
            StateManager.Unset(f"score.{number}")
        autonaApp.settle()


# ── the web server's notice (ADR-005 S4z) ────────────────────────────────

@pytest.fixture
def notices(monkeypatch):
    """The `battle_format` notices the web server sends, the bridge's
    signal connected to it the way WebServer() connects it (the harness
    builds no web server), the flusher driven without its thread. Yields a
    call that flushes and returns what was sent since the last one."""
    from src.AutonaWebServer import WebServer
    from tests.test_webserver_delta_debounce import FORMAT, _Debounce
    monkeypatch.setattr(WebServer, "_held_notices", set())
    monkeypatch.setattr(WebServer, "_follow_notices", set())
    monkeypatch.setattr(WebServer, "_battle_format_notice", False)
    debounce = _Debounce(monkeypatch)
    bridge = StateManagerBridge.instance
    bridge.battle_format_changed.connect(WebServer.on_battle_format_change)

    def sent():
        debounce._fireUntil(None)
        found = [(event, args) for _ms, event, args, _kw in debounce.sent
                 if event == FORMAT]
        debounce.sent.clear()
        return found

    try:
        yield sent
    finally:
        bridge.battle_format_changed.disconnect(
            WebServer.on_battle_format_change)


def test_a_switch_is_one_notice_and_no_switch_is_none(
        autonaApp, client, shell, notices, told):
    _put(client, "team")
    autonaApp.settle()
    assert notices() == [("battle_format", [{}])]

    # The format in force: nothing to tell.
    assert _put(client, "team")["data"]["changed"] is False
    autonaApp.settle()
    assert notices() == []

    _put(client, "crew")
    _put(client, "singles")
    autonaApp.settle()
    # Two switches before a flush: one notice (the page reads the format).
    assert notices() == [("battle_format", [{}])]
    assert told == ["team", "crew", "singles"]


# ── who may ──────────────────────────────────────────────────────────────

def test_the_read_is_open_and_the_switch_needs_this_pc_or_the_token(
        autonaApp, webApp, settings, lanServer, told):
    assert WebAccess.RouteAccess("api_v1.get_battle_format") == WebAccess.READ
    assert WebAccess.RouteAccess("api_v1.battle_format_set") == WebAccess.WRITE
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER

    assert peer.get(URL).status_code == 200
    refused = peer.put(URL, json={"format": "team"})
    assert refused.status_code == 401
    assert told == []
    assert AutonaActions.instance.ResolveBattleFormat() == "singles"

    token = WebAccess.RegenerateToken()
    assert peer.put(URL, json={"format": "team"},
                    headers={"X-Autona-Token": token}).status_code == 200
    assert told == ["team"]
