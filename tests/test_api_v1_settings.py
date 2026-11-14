"""The settings page's /api/v1 routes (ADR-005 S4ae).

- ``GET /api/v1/settings``             READ: an allow-list of the settings
  window's settings and the hotkeys (never the access token)
- ``PATCH /api/v1/settings/general``   WRITE: the switches, default colours
- ``PATCH /api/v1/settings/network``   THIS_PC: port, LAN access
- ``PATCH /api/v1/settings/language``  THIS_PC: the three languages
- ``PATCH /api/v1/settings/hotkeys``   THIS_PC: the switch, the shortcuts

Through Flask's test client against the real route tables. Pinned besides
the answers: who may call which (THIS_PC refuses another computer and a
foreign page, token or not; WRITE takes the token; READ needs none), the
same answers from the desktop and the headless shell, nothing written while
settings.json still cannot be opened (read again first when the launch could
not read it), and the hotkeys re-registered on the GUI
thread when the save comes from a request thread (RegisterHotKey binds a
hotkey to the calling thread's message queue). The Windows API is a stub:
no hotkey is ever registered.

Settings are a copy, never saved to disk.
"""
import copy
import json
import threading
from unittest.mock import MagicMock

import pytest

from src.AutonaWebServer import WebServer
from src.core import HotkeyRules, WebAccess
from src.core import SettingsManager as settingsModule
from src.core.AppSettings import AppSettings
from src.core.SettingsManager import DEFAULT_WEBSERVER_PORT, SettingsManager
from tests.test_api_v1 import _assertRejected, client, webApp  # noqa: F401
from tests.test_web_access import LAN_HOST, LAN_HOST_2, LAN_PEER

SETTINGS = "/api/v1/settings"
GENERAL = f"{SETTINGS}/general"
NETWORK = f"{SETTINGS}/network"
LANGUAGE = f"{SETTINGS}/language"
HOTKEYS = f"{SETTINGS}/hotkeys"

PAIRING = "PAIR-SETTINGS-4242"

# A change on each THIS_PC route.
THIS_PC_CHANGES = [
    (NETWORK, {"port": 6123}),
    (LANGUAGE, {"program": "ja-JP"}),
    (HOTKEYS, {"keys": {"load_set": "Ctrl+Alt+O"}}),
]

_SETTINGS_KEYS = ("general.profanity_filter", "general.disable_autoupdate",
                  "general.disable_scoreupdate", "general.disable_export",
                  "general.disable_overwrite",
                  "general.team_1_default_color",
                  "general.team_2_default_color", "general.webserver_port",
                  "general.webserver_listen_lan", "program_language",
                  "game_asset_language", "fg_term_language", "hotkeys")


def _ok(response):
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    # Settings, not state: no revision.
    assert "revision" not in body
    return body["data"]


def _fresh(monkeypatch):
    """The settings a copy with none of the page's keys saved, never written
    to disk (the saves are counted); this run's start taken from it; no
    server bound."""
    settings = copy.deepcopy(SettingsManager.settings)
    for key in _SETTINGS_KEYS:
        parent, _, leaf = key.rpartition(".")
        (settings.get(parent, {}) if parent else settings).pop(leaf, None)
    settings.setdefault("general", {})
    settings.setdefault("designer", {})["figma_pairing_code"] = PAIRING
    monkeypatch.setattr(SettingsManager, "settings", settings)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    saves = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: saves.append(copy.deepcopy(
                            SettingsManager.settings)))
    monkeypatch.setattr(WebServer, "_listening", None)
    # What bootstrap_core() built, again over these settings: the same
    # language list and the same reading of the server.
    real = AppSettings.instance
    monkeypatch.setattr(AppSettings, "instance", AppSettings(
        languages=real._languages, listening=real._listening))
    return saves


@pytest.fixture
def saves(autonaApp, monkeypatch):
    return _fresh(monkeypatch)


@pytest.fixture
def lanServer(monkeypatch):
    """The token kept in the private settings, and the LAN names."""
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", (LAN_HOST, LAN_HOST_2))
    monkeypatch.setattr(WebServer, "_refusals", WebAccess.RefusalLog())


@pytest.fixture
def registry(monkeypatch):
    """The desktop's hotkeys object over a stubbed Windows API: each
    RegisterHotKey recorded with the thread it ran on. The harness holds the
    object off the OS (tests/conftest.py); lifted here, over the stub."""
    from src import AutonaHotkeys as hotkeysModule

    hotkeys = hotkeysModule.AutonaHotkeys.instance
    calls = _Registered()

    def register(hwnd, hotkeyId, modifiers, vk):
        calls.append((threading.current_thread(), modifiers, vk))
        return 1

    fake = MagicMock()
    fake.RegisterHotKey.side_effect = register
    fake.UnregisterHotKey.return_value = 1
    monkeypatch.setattr(hotkeysModule, "user32", fake)
    # SetupHotkeys installs a native event filter on the application.
    monkeypatch.setattr(hotkeysModule, "QApplication", MagicMock())
    monkeypatch.setattr(hotkeys, "_capture_suspended", False)
    monkeypatch.setattr(hotkeys, "_registered_ids", {})
    monkeypatch.setattr(hotkeys, "_filter", hotkeys._filter)
    monkeypatch.setattr(hotkeys, "_next_id", hotkeys._next_id)
    monkeypatch.setattr(hotkeys, "loaded_keys", dict(hotkeys.loaded_keys))
    calls.fake = fake
    calls.hotkeys = hotkeys
    return calls


class _Registered(list):
    """The RegisterHotKey calls, with the stub and the object they went to."""


def _peer(webApp):
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER
    return peer


# ── the read ─────────────────────────────────────────────────────────────

def test_the_read_answers_every_section(client, saves):
    data = _ok(client.get(SETTINGS))

    assert set(data) == {"general", "network", "language", "hotkeys",
                         "defaults", "loadFailed", "movedAside"}
    assert data["loadFailed"] is False
    assert data["movedAside"] is None
    # What the page's "Default" buttons send (S4af).
    from src.core.AppSettings import Defaults
    assert data["defaults"] == dict(Defaults(),
                                    hotkeys={"enabled": HotkeyRules.DEFAULT_ENABLED})
    assert data["defaults"]["hotkeys"] == {"enabled": True}
    assert data["general"]["profanity_filter"] is True
    assert data["general"]["disable_autoupdate"] is True
    assert data["network"] == {"port": DEFAULT_WEBSERVER_PORT,
                               "listenLan": None, "listening": None,
                               "restartPending": False}
    # The locale's table, injected by bootstrap_core().
    codes = [choice["code"] for choice in data["language"]["choices"]]
    assert "ja-JP" in codes and "en-US" in codes
    assert data["language"]["program"] == "default"
    hotkeys = data["hotkeys"]
    # Flask sorts an object's keys: the page orders the actions itself (its
    # labels). The lists keep theirs.
    assert set(hotkeys["keys"]) == set(HotkeyRules.DEFAULT_KEYS)
    assert hotkeys["vocabulary"] == HotkeyRules.Vocabulary()
    # The desktop registers them (bootstrap_gui()).
    assert hotkeys["applied"] is True
    assert saves == []


def test_the_read_needs_no_token_and_never_carries_one(webApp, autonaApp, saves,
                                                       lanServer):
    token = WebAccess.RegenerateToken()

    response = _peer(webApp).get(SETTINGS)

    data = _ok(response)
    text = response.get_data(as_text=True)
    assert token and token not in text
    assert PAIRING not in text
    assert "webserver_token" not in text
    assert set(data) == {"general", "network", "language", "hotkeys",
                         "defaults", "loadFailed", "movedAside"}
    assert data["loadFailed"] is False
    assert data["movedAside"] is None


def test_the_read_shows_what_the_server_is_bound_with(client, saves,
                                                      monkeypatch):
    monkeypatch.setattr(WebServer, "_listening", ("127.0.0.1", 5107, False))
    SettingsManager.settings["general"]["webserver_port"] = 6000

    network = _ok(client.get(SETTINGS))["network"]

    assert network == {"port": 6000, "listenLan": None,
                       "listening": {"port": 5107, "lan": False},
                       "restartPending": True}


# ── the saves ────────────────────────────────────────────────────────────

def test_a_general_save_answers_its_section(client, saves):
    data = _ok(client.patch(GENERAL, json={"profanity_filter": False,
                                           "team_2_default_color": "#ABC"}))

    assert data == _ok(client.get(SETTINGS))["general"]
    assert data["profanity_filter"] is False
    assert data["team_2_default_color"] == "#aabbcc"
    assert len(saves) == 1
    # The same again writes nothing.
    _ok(client.patch(GENERAL, json={"profanity_filter": False}))
    assert len(saves) == 1


def test_a_network_save_is_pending_until_a_restart(client, saves):
    data = _ok(client.patch(NETWORK, json={"port": 6123, "listenLan": True}))

    assert data == {"port": 6123, "listenLan": True, "listening": None,
                    "restartPending": True}
    assert SettingsManager.settings["general"]["webserver_port"] == 6123
    assert SettingsManager.settings["general"]["webserver_listen_lan"] is True


def test_a_language_save_is_pending_until_a_restart(client, saves):
    data = _ok(client.patch(LANGUAGE, json={"program": "ja-JP",
                                            "fgTerm": "default"}))

    assert data["program"] == "ja-JP" and data["fgTerm"] == "default"
    assert data["restartPending"] is True
    # Only what changed: "default" was in effect already.
    assert saves[-1]["program_language"] == "ja-JP"
    assert "fg_term_language" not in saves[-1]


def test_a_hotkey_save_answers_the_hotkeys_and_its_warnings(client, saves):
    data = _ok(client.patch(HOTKEYS, json={
        "enabled": True, "keys": {"load_set": "ctrl+s",
                                  "swap_teams": ""}}))

    assert data["keys"]["load_set"] == {
        "value": "Ctrl+S", "default": "Ctrl+Shift+O", "valid": True,
        "warning": "os_reserved"}
    assert data["keys"]["swap_teams"]["value"] == ""
    assert data["warnings"] == [{"action": "load_set", "code": "os_reserved"}]
    without = dict(data)
    del without["warnings"]
    assert without == _ok(client.get(SETTINGS))["hotkeys"]
    assert SettingsManager.settings["hotkeys"] == {"load_set": "Ctrl+S",
                                                   "swap_teams": ""}


def test_an_empty_keys_object_is_a_save_of_nothing(client, saves):
    data = _ok(client.patch(HOTKEYS, json={"keys": {}}))
    assert data["warnings"] == []
    assert saves == []


def test_a_shortcut_another_action_has_is_409_naming_it(client, saves):
    refused = client.patch(HOTKEYS, json={"keys": {"load_set": "Ctrl+Shift+S"}})

    _assertRejected(refused, "CONFLICT", 409)
    message = refused.get_json()["error"]["message"]
    assert "load_set" in message and "swap_teams" in message
    assert saves == []


@pytest.mark.parametrize("saved, asked", [
    # What Qt's recorder saved, and the page's spelling of the same key:
    # Win32 would refuse the second registration (with a log line only).
    ("Ctrl+Return", "Ctrl+Enter"),
    ("Ctrl+Esc", "Ctrl+Escape"),
    ("Ctrl+PgUp", "Ctrl+PageUp"),
])
def test_the_same_key_spelled_otherwise_is_409(client, saves, saved, asked):
    SettingsManager.settings["hotkeys"] = {"swap_teams": saved}

    refused = client.patch(HOTKEYS, json={"keys": {"load_set": asked}})

    _assertRejected(refused, "CONFLICT", 409)
    assert "swap_teams" in refused.get_json()["error"]["message"]
    assert saves == []


def test_a_key_with_no_modifier_is_saved_and_warned(client, saves):
    data = _ok(client.patch(HOTKEYS, json={"keys": {"load_set": "Space"}}))

    assert data["warnings"] == [{"action": "load_set", "code": "no_modifier"}]
    assert data["keys"]["load_set"]["warning"] == "no_modifier"
    assert SettingsManager.settings["hotkeys"]["load_set"] == "Space"
    # A function key alone is the usual hotkey: the OBS warning, not this.
    data = _ok(client.patch(HOTKEYS, json={"keys": {"load_set": "F9"}}))
    assert data["warnings"] == [{"action": "load_set", "code": "obs_likely"}]


def test_a_key_with_shift_alone_is_saved_and_warned(client, saves):
    # S4af: typing a capital or a symbol in any app would go to Autona.
    data = _ok(client.patch(HOTKEYS, json={"keys": {"load_set": "Shift+K"}}))

    assert data["warnings"] == [{"action": "load_set", "code": "shift_only"}]
    assert data["keys"]["load_set"]["warning"] == "shift_only"
    assert SettingsManager.settings["hotkeys"]["load_set"] == "Shift+K"
    # Not with a function key, nor with Ctrl / Alt too.
    data = _ok(client.patch(HOTKEYS, json={"keys": {"load_set": "Ctrl+Shift+K"}}))
    assert data["warnings"] == []


@pytest.mark.parametrize("path, body", [
    (GENERAL, {}),
    (GENERAL, None),
    (GENERAL, [True]),
    (GENERAL, {"nope": True}),
    (GENERAL, {"qt_scoreboard_panel": True}),
    (GENERAL, {"disable_individual_game_tracker": False}),
    (GENERAL, {"profanity_filter": "false"}),
    (GENERAL, {"profanity_filter": False, "team_1_default_color": "red"}),
    (NETWORK, {}),
    (NETWORK, {"port": 0}),
    (NETWORK, {"port": 65536}),
    (NETWORK, {"port": 70000}),
    (NETWORK, {"port": True}),
    (NETWORK, {"port": "6000"}),
    (NETWORK, {"listenLan": None}),
    (NETWORK, {"port": 6000, "listenLan": "yes"}),
    (LANGUAGE, {}),
    (LANGUAGE, {"program": "xx-XX"}),
    (LANGUAGE, {"program": "ja-JP", "gameAsset": None}),
    (HOTKEYS, {}),
    (HOTKEYS, None),
    (HOTKEYS, {"enabled": "yes"}),
    (HOTKEYS, {"enabled": None}),
    (HOTKEYS, {"keys": None}),
    (HOTKEYS, {"keys": ["Ctrl+A"]}),
    (HOTKEYS, {"other": 1}),
    (HOTKEYS, {"enabled": True, "other": 1}),
    (HOTKEYS, {"keys": {"nope": "Ctrl+A"}}),
    (HOTKEYS, {"keys": {"load_set": 5}}),
    # What Qt's recorder wrote, which the registration never read.
    (HOTKEYS, {"keys": {"load_set": "Ctrl+Esc"}}),
    (HOTKEYS, {"keys": {"load_set": "Ctrl+PgUp"}}),
    (HOTKEYS, {"keys": {"load_set": "Ctrl+Ctrl+O"}}),
    (HOTKEYS, {"enabled": False, "keys": {"load_set": "Ctrl+Alt+O",
                                          "swap_teams": "Win+S"}}),
])
def test_a_request_that_is_not_one_is_400_and_saves_nothing(client, saves,
                                                            path, body):
    before = copy.deepcopy(SettingsManager.settings)

    _assertRejected(client.patch(path, json=body), "INVALID_ARGUMENT", 400)

    assert saves == []
    assert SettingsManager.settings == before


@pytest.mark.parametrize("path, body", [
    (GENERAL, {"profanity_filter": False}),
    *THIS_PC_CHANGES,
    (HOTKEYS, {"enabled": False}),
])
def test_nothing_is_saved_while_settings_json_cannot_be_opened(
        client, saves, monkeypatch, tmp_path, path, body):
    # A write would replace the unread file with this one key -- and drop
    # the access token with the rest. The save reads it again first, and it
    # still cannot be opened (a directory: an OSError other than "not
    # found").
    unopenable = tmp_path / "settings.json"
    unopenable.mkdir()
    monkeypatch.setattr(settingsModule, "SETTINGS_PATH", str(unopenable))
    monkeypatch.setattr(SettingsManager, "loadFailed", True)
    before = copy.deepcopy(SettingsManager.settings)

    _assertRejected(client.patch(path, json=body), "NOT_READY", 409)

    assert saves == []
    assert SettingsManager.settings == before
    # The read still answers, and tells the page why nothing saves.
    assert _ok(client.get(SETTINGS))["loadFailed"] is True


def test_a_save_reads_settings_json_again_when_the_launch_could_not(
        client, saves, monkeypatch, tmp_path):
    # The launch's failure was passing: no 409, the file read again is kept
    # with the change over it, and the page's loadFailed goes false.
    stored = {"general": {"webserver_token": "kept-token-0123456789ab",
                          "profanity_filter": True},
              "program_language": "ja"}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(stored), encoding="utf-8")
    monkeypatch.setattr(settingsModule, "SETTINGS_PATH", str(path))
    monkeypatch.setattr(SettingsManager, "loadFailed", True)

    data = _ok(client.patch(GENERAL, json={"profanity_filter": False}))

    assert data["profanity_filter"] is False
    stored["general"]["profanity_filter"] = False
    assert saves == [stored]
    read = _ok(client.get(SETTINGS))
    assert read["loadFailed"] is False
    assert read["movedAside"] is None


def test_the_read_names_where_a_corrupt_settings_json_was_moved(
        client, saves, monkeypatch, tmp_path):
    # For the settings page (S4af) to tell the user: the backup's file name,
    # not its path (which would show the install folder to a LAN reader).
    path = tmp_path / "settings.json"
    path.write_bytes(b'{"general": {"webserver_po')
    monkeypatch.setattr(settingsModule, "SETTINGS_PATH", str(path))
    monkeypatch.setattr(SettingsManager, "loadFailed", True)

    _ok(client.patch(GENERAL, json={"profanity_filter": False}))

    [backup] = tmp_path.glob("settings.json.unreadable-*.bak")
    read = _ok(client.get(SETTINGS))
    assert read["loadFailed"] is False
    assert read["movedAside"] == backup.name


def test_a_save_is_op_logged_with_its_values_and_never_the_token(
        webApp, autonaApp, saves, lanServer):
    from loguru import logger

    token = WebAccess.RegenerateToken()
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="DEBUG")
    try:
        _ok(_peer(webApp).patch(GENERAL, json={"disable_export": True},
                                headers={"X-Autona-Token": token}))
    finally:
        logger.remove(sink)

    told = [m for m in messages if "op=settings.general" in m]
    assert told and any("disable_export" in m and "True" in m for m in told)
    assert not any(token in m for m in messages)


# ── who may call which ───────────────────────────────────────────────────

def test_the_general_save_takes_the_token_from_another_computer(
        webApp, autonaApp, saves, lanServer):
    peer = _peer(webApp)
    token = WebAccess.RegenerateToken()
    saves.clear()

    refused = peer.patch(GENERAL, json={"disable_export": True})
    assert refused.status_code == 401
    assert saves == []

    _ok(peer.patch(GENERAL, json={"disable_export": True},
                   headers={"X-Autona-Token": token}))
    assert SettingsManager.settings["general"]["disable_export"] is True


def test_this_pc_saves_with_no_token(client, saves, lanServer, registry):
    # This PC's own request (loopback, its own Host, no foreign page).
    for path, body in THIS_PC_CHANGES:
        _ok(client.patch(path, json=body))
    assert SettingsManager.settings["general"]["webserver_port"] == 6123
    assert SettingsManager.settings["program_language"] == "ja-JP"
    assert SettingsManager.settings["hotkeys"]["load_set"] == "Ctrl+Alt+O"


@pytest.mark.parametrize("path, body", THIS_PC_CHANGES)
def test_another_computer_is_refused_even_with_the_token(
        webApp, autonaApp, saves, lanServer, path, body):
    # Who reaches the server, what the program shows and which keys it takes
    # from every app on this PC are not for another computer to change.
    peer = _peer(webApp)
    token = WebAccess.RegenerateToken()
    saves.clear()
    before = copy.deepcopy(SettingsManager.settings)

    for headers in ({}, {"X-Autona-Token": token}):
        refused = peer.patch(path, json=body, headers=headers)
        assert refused.status_code == 403
        assert refused.get_json() == WebAccess.ErrorBody(
            WebAccess.THIS_PC_ONLY)

    assert saves == []
    assert SettingsManager.settings == before


@pytest.mark.parametrize("path, body", THIS_PC_CHANGES)
def test_this_pc_with_a_foreign_page_is_refused_even_with_the_token(
        webApp, autonaApp, saves, lanServer, path, body):
    token = WebAccess.RegenerateToken()
    saves.clear()

    refused = webApp.test_client().patch(
        path, json=body,
        headers={"X-Autona-Token": token, "Origin": "https://evil.example",
                 "Sec-Fetch-Site": "cross-site"})

    assert refused.status_code == 403
    assert saves == []


def test_the_general_save_lets_a_page_with_the_token_in(webApp, autonaApp,
                                                        saves, lanServer):
    # WRITE, unlike THIS_PC: the token is what a foreign page needs.
    token = WebAccess.RegenerateToken()
    page = webApp.test_client()
    foreign = {"Origin": "https://evil.example",
               "Sec-Fetch-Site": "cross-site"}

    assert page.patch(GENERAL, json={"disable_export": True},
                      headers=foreign).status_code == 403
    assert "disable_export" not in SettingsManager.settings["general"]
    _ok(page.patch(GENERAL, json={"disable_export": True},
                   headers=dict(foreign, **{"X-Autona-Token": token})))


@pytest.mark.parametrize("headers, cause", [
    ({"X-Forwarded-For": "203.0.113.5"}, "forwarded"),
    ({"Host": "rebound.example:5107"}, "foreign_host"),
    ({"Origin": "null"}, "foreign_origin"),
    ({"Sec-Fetch-Site": "cross-site"}, "cross_site"),
])
@pytest.mark.parametrize("withToken", [False, True])
@pytest.mark.parametrize("path, body", THIS_PC_CHANGES,
                         ids=["network", "language", "hotkeys"])
def test_only_this_pcs_own_request_gets_through_and_the_log_says_why_not(
        webApp, autonaApp, saves, lanServer, headers, cause, withToken, path,
        body):
    from loguru import logger

    sent = dict(headers)
    if withToken:
        sent["X-Autona-Token"] = WebAccess.RegenerateToken()
    saves.clear()
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="DEBUG")
    try:
        refused = webApp.test_client().patch(path, json=body, headers=sent)
    finally:
        logger.remove(sink)

    assert refused.status_code == 403
    assert refused.get_json() == WebAccess.ErrorBody(WebAccess.THIS_PC_ONLY)
    assert saves == []
    told = [m for m in messages if "refused" in m and path in m]
    assert told and all("this_pc_only" in m for m in told)
    assert all(("(token)" if withToken else f"({cause})") in m for m in told)
    assert not withToken or not any(
        sent["X-Autona-Token"] in m for m in messages)


# ── the desktop and the headless shell ───────────────────────────────────

_SEQUENCE = [
    ("GET", SETTINGS, None),
    ("PATCH", GENERAL, {"disable_overwrite": True,
                        "team_1_default_color": "#123"}),
    ("PATCH", NETWORK, {"port": 6123}),
    ("PATCH", LANGUAGE, {"gameAsset": "ja-JP"}),
    ("PATCH", HOTKEYS, {"enabled": False,
                        "keys": {"load_set": "Ctrl+Alt+O",
                                 "reset_scores": "F5"}}),
    ("PATCH", HOTKEYS, {"keys": {"load_set": "Ctrl+Shift+S"}}),
    ("PATCH", NETWORK, {"port": 0}),
    ("GET", SETTINGS, None),
]


def _answers(client):
    answers = []
    for method, path, body in _SEQUENCE:
        response = client.open(path, method=method, json=body)
        answers.append((response.status_code, response.get_json()))
    return answers, copy.deepcopy(SettingsManager.settings)


def _withoutApplied(answers):
    # `hotkeys.applied` is the one the shells answer differently.
    stripped = json.loads(json.dumps(answers))
    for _, body in stripped:
        data = (body or {}).get("data")
        if isinstance(data, dict):
            for section in (data, data.get("hotkeys")):
                if isinstance(section, dict) and "vocabulary" in section:
                    section.pop("applied")
    return stripped


def test_the_desktop_and_the_headless_shell_answer_the_same(
        autonaApp, client, monkeypatch):
    from tests.test_web_operations import _goHeadless

    _fresh(monkeypatch)
    desktop, desktopSaved = _answers(client)

    _fresh(monkeypatch)
    _goHeadless(autonaApp, monkeypatch)
    # The headless shell has no hotkeys object: bootstrap_gui() never ran.
    monkeypatch.setattr(HotkeyRules, "applied", False)
    try:
        headless, headlessSaved = _answers(client)
    finally:
        # The boards _goHeadless seeded (tests/test_web_operations.py's
        # `headless` fixture does the same).
        from src.core.StateManager import StateManager
        for number in (1, 2, 3):
            StateManager.Unset(f"score.{number}")
        autonaApp.settle()

    assert [status for status, _ in desktop] == [
        200, 200, 200, 200, 200, 409, 400, 200]
    assert _withoutApplied(desktop) == _withoutApplied(headless)
    assert desktopSaved == headlessSaved
    assert desktop[0][1]["data"]["hotkeys"]["applied"] is True
    assert headless[0][1]["data"]["hotkeys"]["applied"] is False
    # The hotkey save answered and saved in both (the headless one only
    # reaches settings.json).
    assert headlessSaved["hotkeys"] == {"hotkeys_enabled": False,
                                        "load_set": "Ctrl+Alt+O",
                                        "reset_scores": "F5"}


# ── the hotkeys re-registered on the GUI thread ──────────────────────────

def test_the_harness_holds_the_hotkeys_off_the_os(autonaApp):
    # A hotkey save anywhere in the suite reaches ReloadHotkeys through the
    # bridge; the session's object must not hand it to Windows
    # (tests/conftest.py).
    from src.AutonaHotkeys import AutonaHotkeys

    assert AutonaHotkeys.instance._capture_suspended is True
    assert AutonaHotkeys.instance._registered_ids == {}


def test_a_save_from_a_request_thread_registers_on_the_gui_thread(
        autonaApp, client, saves, registry):
    from src.core.HotkeyRules import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT

    answers = []
    worker = threading.Thread(
        target=lambda: answers.append(client.patch(
            HOTKEYS, json={"keys": {"load_set": "Ctrl+Alt+O"}})),
        name="flask-request")
    worker.start()
    worker.join(30)

    assert not worker.is_alive()
    _ok(answers[0])
    # Not on the request thread: queued to the hotkeys object's thread.
    assert registry == []

    autonaApp.settle()

    assert registry, "the save never re-registered the hotkeys"
    assert {thread for thread, _, _ in registry} == {threading.main_thread()}
    assert (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("O")) in [
        (modifiers, vk) for _, modifiers, vk in registry]
    assert len(registry) == len(HotkeyRules.DEFAULT_KEYS)
    assert registry.hotkeys.loaded_keys["load_set"] == "Ctrl+Alt+O"


def test_a_save_on_the_gui_thread_registers_at_once(client, saves, registry):
    _ok(client.patch(HOTKEYS, json={"keys": {"swap_teams": "Ctrl+Alt+S"}}))

    assert len(registry) == len(HotkeyRules.DEFAULT_KEYS)
    assert registry.hotkeys.loaded_keys["swap_teams"] == "Ctrl+Alt+S"


def test_turning_the_hotkeys_off_releases_every_grab(client, saves, registry):
    _ok(client.patch(HOTKEYS, json={"keys": {"swap_teams": "Ctrl+Alt+S"}}))
    registered = len(registry.hotkeys._registered_ids)
    assert registered == len(HotkeyRules.DEFAULT_KEYS)
    registry.clear()

    _ok(client.patch(HOTKEYS, json={"enabled": False}))

    assert registry == []
    assert registry.fake.UnregisterHotKey.call_count == registered
    assert registry.hotkeys._registered_ids == {}

    _ok(client.patch(HOTKEYS, json={"enabled": True}))
    assert len(registry) == len(HotkeyRules.DEFAULT_KEYS)


def test_a_save_of_nothing_registers_nothing(client, saves, registry):
    _ok(client.patch(HOTKEYS, json={"keys": {"load_set": "Ctrl+Shift+O"}}))
    _ok(client.patch(HOTKEYS, json={"enabled": True}))
    assert registry == []


def test_a_refused_save_registers_nothing(client, saves, registry):
    _assertRejected(client.patch(
        HOTKEYS, json={"keys": {"load_set": "Ctrl+Shift+S"}}), "CONFLICT", 409)
    _assertRejected(client.patch(
        HOTKEYS, json={"keys": {"load_set": "Ctrl+Esc"}}))
    assert registry == []


@pytest.mark.parametrize("stored, skipped", [
    # A hand-edited settings.json. The old merge passed null / 5 to the
    # parser, which raised inside UiMounted at start-up; a subtree that is
    # not an object raised on .items().
    ({"load_set": None, "swap_teams": 5, "reset_scores": ["Ctrl+R"]},
     {"load_set", "swap_teams", "reset_scores"}),
    (["Ctrl+Alt+O"], set()),
    ("Ctrl+Alt+O", set()),
], ids=["values", "list", "string"])
def test_a_saved_value_that_is_not_a_shortcut_does_not_stop_the_others(
        autonaApp, saves, registry, stored, skipped):
    from src.AutonaHotkeys import AutonaHotkeys
    from src.core.HotkeyRules import MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT

    SettingsManager.settings["hotkeys"] = stored

    # Start-up: built, then mounted (the order of bootstrap_gui() / Window).
    hotkeys = AutonaHotkeys()
    hotkeys.UiMounted(None)

    registered = set(hotkeys._registered_ids.values())
    assert registered == set(HotkeyRules.DEFAULT_KEYS) - skipped
    assert len(registry) == len(registered)
    assert (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("1")) in [
        (modifiers, vk) for _, modifiers, vk in registry]
    # A save reloads the same way.
    registry.clear()
    hotkeys.ReloadHotkeys()
    assert set(hotkeys._registered_ids.values()) == registered


def test_mounting_registers_what_is_saved_by_then(autonaApp, saves, registry):
    # When the launch could not read settings.json, a write between the
    # construction (bootstrap_gui()) and the window's mount -- the desktop's
    # TurnOffPolling -- may have read it: the mount registers what it holds,
    # not the defaults the construction saw.
    from src.AutonaHotkeys import AutonaHotkeys
    from src.core.HotkeyRules import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT

    SettingsManager.settings.pop("hotkeys", None)
    hotkeys = AutonaHotkeys()
    assert hotkeys.loaded_keys["load_set"] == "Ctrl+Shift+O"
    SettingsManager.settings["hotkeys"] = {"load_set": "Ctrl+Alt+O"}

    hotkeys.UiMounted(None)

    assert hotkeys.loaded_keys["load_set"] == "Ctrl+Alt+O"
    assert (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("O")) in [
        (modifiers, vk) for _, modifiers, vk in registry]


def test_the_reload_is_connected_once_whatever_applied_says(autonaApp,
                                                            monkeypatch):
    # Its own guard (like ConnectActions): `applied` is what the page is
    # told, not whether the connection was made.
    from qtpy.QtCore import SIGNAL

    from src.AutonaHotkeys import AutonaHotkeys
    from src.StateManagerBridge import StateManagerBridge

    bridge = StateManagerBridge.instance
    connected = bridge.receivers(SIGNAL("hotkeys_changed()"))
    assert AutonaHotkeys._reload_connected is True
    assert connected >= 1

    AutonaHotkeys.ConnectReload(bridge.hotkeys_changed)
    monkeypatch.setattr(HotkeyRules, "applied", False)
    AutonaHotkeys.ConnectReload(bridge.hotkeys_changed)

    assert bridge.receivers(SIGNAL("hotkeys_changed()")) == connected


# ── the pages' port: the one the server is bound with ────────────────────

def test_every_url_naming_the_server_reads_it_through_serving():
    # ADR-005 S4af: one reader of what the server is reached at,
    # WebServer.Serving() (bound with, else the settings). Only the server
    # itself reads the settings' port (its bind, and Serving's fallback),
    # and only Serving and AppSettings' injection ask Listening().
    import ast

    from tests.conftest import PROJECT_ROOT

    # The two reads of the LAN switch for "not decided yet" (None: the
    # desktop's first-launch question, the headless shell's log line), not
    # to name the server: these functions, this key, the None default --
    # and nothing else in the two files.
    undecided = {("src/Autona.py", "_StartWebServer"),
                 ("src/headless.py", "Assemble")}

    def functions(tree):
        """{call node: the innermost function it is in}."""
        found = {}

        def visit(node, function):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, child.name)
                else:
                    if isinstance(child, ast.Call):
                        found[child] = function
                    visit(child, function)

        visit(tree, None)
        return found

    src = PROJECT_ROOT / "src"
    portReaders, listeners, undecidedReads = set(), set(), set()
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        name = path.relative_to(PROJECT_ROOT).as_posix()
        for node, function in functions(tree).items():
            if not isinstance(node.func, ast.Attribute):
                continue
            if (node.func.attr == "Get" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in ("general.webserver_port",
                                               "general.webserver_listen_lan")):
                if ((name, function) in undecided
                        and node.args[0].value == "general.webserver_listen_lan"
                        and len(node.args) == 2
                        and isinstance(node.args[1], ast.Constant)
                        and node.args[1].value is None):
                    undecidedReads.add((name, function))
                else:
                    portReaders.add(name)
            if node.func.attr == "Listening":
                listeners.add(name)
    assert portReaders == {"src/AutonaWebServer.py"}
    assert undecidedReads == undecided
    assert listeners == {"src/AutonaWebServer.py", "src/bootstrap.py"}


def test_serving_says_lan_off_for_undecided(saves, monkeypatch):
    # LAN access not decided (None) is off, whether the server runs or not.
    SettingsManager.settings["general"].pop("webserver_listen_lan", None)
    assert WebServer.Serving()[1] is False
    SettingsManager.settings["general"]["webserver_listen_lan"] = None
    assert WebServer.Serving()[1] is False

    monkeypatch.setattr(WebServer, "_listening", ("127.0.0.1", 5107, None))
    assert WebServer.Serving() == (5107, False)


def test_lan_saved_before_the_restart_is_still_off(saves, monkeypatch):
    # Saved on, the running server bound to this PC only: every address is
    # still the loopback one, and the guides say a restart turns it on.
    from src.Helpers import AutonaNetworkHelper

    monkeypatch.setattr(AutonaNetworkHelper, "GetLocalIP",
                        lambda: "192.168.0.20")
    monkeypatch.setattr(WebServer, "_listening", ("127.0.0.1", 5107, False))
    SettingsManager.settings["general"]["webserver_listen_lan"] = True

    assert WebServer.Serving() == (5107, False)
    assert AutonaNetworkHelper.GetWebServerHost() == "127.0.0.1"
    assert WebServer.LanOnAtRestart() is True

    # Saved off (or undecided): nothing waits for a restart.
    SettingsManager.settings["general"]["webserver_listen_lan"] = False
    assert WebServer.LanOnAtRestart() is False
    SettingsManager.settings["general"]["webserver_listen_lan"] = None
    assert WebServer.LanOnAtRestart() is False

    # Bound to the LAN: it is on now, not at the restart.
    monkeypatch.setattr(WebServer, "_listening", ("0.0.0.0", 5107, True))
    SettingsManager.settings["general"]["webserver_listen_lan"] = True
    assert AutonaNetworkHelper.GetWebServerHost() == "192.168.0.20"
    assert WebServer.LanOnAtRestart() is False


def test_with_no_server_the_saved_lan_names_the_address(saves, monkeypatch):
    # Before start() (and in tests) the settings are what the next start
    # binds; nothing is waiting for a restart.
    from src.Helpers import AutonaNetworkHelper

    monkeypatch.setattr(AutonaNetworkHelper, "GetLocalIP",
                        lambda: "192.168.0.20")
    monkeypatch.setattr(WebServer, "_listening", None)
    SettingsManager.settings["general"]["webserver_listen_lan"] = True

    assert AutonaNetworkHelper.GetWebServerHost() == "192.168.0.20"
    assert WebServer.LanOnAtRestart() is False


# What the guides say of LAN access saved for the restart (S4af review) and
# the API guide's way to the settings are the guide pages' since ADR-005
# S-final SF-4a-2 (web/src/GuidePages/GuidePages.test.js: the connection's
# lanAtRestart, read through GET /api/v1/connection -- pinned in
# tests/test_api_v1_guides.py -- picks the "takes effect when Autona is
# restarted" note). The Qt API window's "Open settings" closed the window
# and opened the page; the page says where the settings are instead.


def test_local_pages_go_to_the_port_the_server_is_bound_with(saves,
                                                             monkeypatch):
    from src.AutonaWebPageLifecycle import LocalUrl

    SettingsManager.settings["general"]["webserver_port"] = 6200
    # No server (tests, before start()): the configured port.
    assert LocalUrl("/web/score?x=1") == "http://127.0.0.1:6200/web/score?x=1"

    # A port saved for the next start does not move a page off this run's.
    monkeypatch.setattr(WebServer, "_listening", ("127.0.0.1", 5107, False))
    assert LocalUrl("/web/score") == "http://127.0.0.1:5107/web/score"


def test_the_designer_window_loads_through_local_url():
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src"
              / "AutonaDesignerWindow.py").read_text(encoding="utf-8")
    assert "webserver_port" not in source
    calls = [node for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "LocalUrl"]
    assert len(calls) == 1
