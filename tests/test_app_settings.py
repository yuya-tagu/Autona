"""The settings window's settings in core (src/core/AppSettings.py, ADR-005
S4ae).

Pinned:

- the read is an allow-list: exactly the general switches and colours, the
  port and LAN access, the three languages -- never the access token, the
  Designer pairing code or any other key settings.json holds;
- the table's defaults are the readers' (an AST scan of every
  SettingsManager.Get of those keys in src, the settings window excepted:
  it keeps its own until S4af);
- an update validates everything first and writes once, nothing when
  nothing changes; when the launch could not read settings.json, against
  the file read again (or after moving a corrupt one aside), and nothing at
  all while it still cannot be opened;
- the port is an int 1-65535 (a bool is not), and one saved out of range is
  shown as it is; an undecided LAN access (None) is kept by any other save;
- restartPending: what is saved against what is in effect.

Settings are a private dict here, never saved to disk.
"""
import ast
import json
from pathlib import Path

import pytest

from src.core import AppSettings as module
from src.core import SettingsManager as settingsModule
from src.core.AppSettings import AppSettings
from src.core.SettingsManager import DEFAULT_WEBSERVER_PORT, SettingsManager

PROJECT_ROOT = Path(__file__).resolve().parents[1]

LANGUAGES = {"en": ["English", "English"], "ja": ["日本語", "Japanese"]}
SECRET_TOKEN = "s3cret-token-0123456789abcdef"
SECRET_PAIRING = "PAIR-9876"

GENERAL_NAMES = ["profanity_filter", "disable_autoupdate",
                 "disable_scoreupdate", "disable_export", "disable_overwrite",
                 "disable_individual_game_tracker", "team_1_default_color",
                 "team_2_default_color"]


@pytest.fixture
def saves(monkeypatch):
    """A private settings dict, never saved; each save recorded."""
    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    recorded = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: recorded.append(json.loads(json.dumps(
                            SettingsManager.settings))))
    return recorded


class _Listening:
    """What the web server is bound with: None while none runs."""

    def __init__(self, value=None):
        self.value = value

    def __call__(self):
        return self.value


def _make(listening=None, languages=LANGUAGES):
    return AppSettings(languages=lambda: languages,
                       listening=listening or _Listening())


# ── the read ─────────────────────────────────────────────────────────────

def test_the_read_is_the_allow_list_and_nothing_else(saves):
    SettingsManager.settings.update({
        "general": {"webserver_token": SECRET_TOKEN,
                    "webserver_port": 6000,
                    "qt_scoreboard_panel": True,
                    "theme_color": "#123456",
                    "profanity_filter": False},
        "designer": {"figma_pairing_code": SECRET_PAIRING},
        "hotkeys": {"load_set": "Ctrl+Alt+O"},
        "SELECTED_GAME_CODENAME": "ssbu",
        "program_language": "ja",
    })

    described = _make().Describe()

    assert set(described) == {"general", "network", "language", "loadFailed",
                              "movedAside"}
    assert described["loadFailed"] is False
    assert described["movedAside"] is None
    assert list(described["general"]) == GENERAL_NAMES
    assert set(described["network"]) == {
        "port", "listenLan", "listening", "restartPending"}
    assert set(described["language"]) == {
        "program", "gameAsset", "fgTerm", "choices", "restartPending"}
    text = json.dumps(described, ensure_ascii=False)
    assert SECRET_TOKEN not in text
    assert SECRET_PAIRING not in text
    for other in ("webserver_token", "qt_scoreboard_panel", "theme_color",
                  "#123456", "Ctrl+Alt+O", "ssbu", "figma"):
        assert other not in text
    assert described["general"]["profanity_filter"] is False
    assert described["network"]["port"] == 6000
    assert described["language"]["program"] == "ja"


def test_nothing_saved_reads_as_the_defaults(saves):
    described = _make().Describe()

    assert described["general"] == {
        "profanity_filter": True, "disable_autoupdate": True,
        "disable_scoreupdate": False, "disable_export": False,
        "disable_overwrite": False, "disable_individual_game_tracker": True,
        "team_1_default_color": "#fe3636", "team_2_default_color": "#2e89ff"}
    assert described["network"] == {
        "port": DEFAULT_WEBSERVER_PORT, "listenLan": None, "listening": None,
        "restartPending": False}
    assert described["language"] == {
        "program": "default", "gameAsset": "default", "fgTerm": "default",
        "choices": [
            {"code": "en", "name": "English", "englishName": "English"},
            {"code": "ja", "name": "日本語", "englishName": "Japanese"}],
        "restartPending": False}
    assert saves == []


def test_a_saved_value_of_another_type_reads_as_the_default(saves):
    SettingsManager.settings["general"] = {
        "profanity_filter": "no", "disable_export": 1,
        "team_1_default_color": 5, "webserver_port": True,
        "webserver_listen_lan": 1}
    SettingsManager.settings["fg_term_language"] = ""
    SettingsManager.settings["game_asset_language"] = None

    described = _make().Describe()

    assert described["general"]["profanity_filter"] is True
    assert described["general"]["disable_export"] is False
    assert described["general"]["team_1_default_color"] == "#fe3636"
    assert described["network"]["port"] == DEFAULT_WEBSERVER_PORT
    # The server takes it as on (a truthy value).
    assert described["network"]["listenLan"] is True
    assert described["language"]["fgTerm"] == "default"
    assert described["language"]["gameAsset"] == "default"


def test_a_port_saved_out_of_range_is_shown_as_it_is(saves):
    # The window allowed up to 99999; the server failed to listen on it. The
    # page shows it, and a save then has to be a real port.
    SettingsManager.settings["general"] = {"webserver_port": 99999}
    assert _make().Describe()["network"]["port"] == 99999


def test_an_undecided_lan_access_reads_as_null(saves):
    SettingsManager.settings["general"] = {"webserver_listen_lan": None}
    assert _make().Describe()["network"]["listenLan"] is None


def test_the_language_list_failing_leaves_the_choices_empty(saves):
    def broken():
        raise RuntimeError("locale not loaded")

    settings = AppSettings(languages=broken, listening=_Listening())

    assert settings.Describe()["language"]["choices"] == []
    # Only "default" can be saved then.
    refusal, _ = settings.UpdateLanguage({"program": "ja"})
    assert refusal.code == module.INVALID
    assert settings.UpdateLanguage({"program": "default"}) == (None, False)


# ── the defaults are the readers' ────────────────────────────────────────

def _readers():
    """Every SettingsManager.Get("<key>", default) in src with a literal key,
    the settings window and AppSettings itself left out: {key: [(path,
    default)]}. A default that is a name is resolved for the one name the
    readers use (DEFAULT_WEBSERVER_PORT)."""
    names = {"DEFAULT_WEBSERVER_PORT": DEFAULT_WEBSERVER_PORT,
             "None": None, "True": True, "False": False}
    found = {}
    skipped = {PROJECT_ROOT / "src" / "core" / "AppSettings.py"}
    for path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
        if path in skipped or "Settings" in path.relative_to(
                PROJECT_ROOT / "src").parts[:1]:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "Get"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "SettingsManager"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                continue
            if len(node.args) > 1:
                default = node.args[1]
                if isinstance(default, ast.Constant):
                    value = default.value
                elif isinstance(default, ast.Name) and default.id in names:
                    value = names[default.id]
                else:
                    value = ("<unresolved>", ast.unparse(default))
            else:
                value = None
            found.setdefault(node.args[0].value, []).append(
                (path.relative_to(PROJECT_ROOT).as_posix(), value))
    return found


READERS = _readers()


@pytest.mark.parametrize("setting", [
    s for s in module.SETTINGS if s.group == module.GENERAL
    and s.kind == module.BOOL] + [module.Group(module.NETWORK)[0]],
    ids=lambda s: s.name)
def test_each_reader_takes_the_tables_default(setting):
    readers = READERS.get(setting.key, [])
    # Not vacuous: each of these is read somewhere outside the window.
    assert readers, f"no reader of {setting.key}"
    assert all(default == setting.default and type(default) is type(
        setting.default) for _, default in readers), readers


def test_disable_autoupdate_is_on_when_nothing_is_saved():
    # The reader (SetFollowService.Polling) has always taken it as on; the
    # window showed it off. The table follows what the app does.
    assert READERS["general.disable_autoupdate"] == [
        ("src/core/SetFollowService.py", True)]
    setting, = [s for s in module.SETTINGS if s.name == "disable_autoupdate"]
    assert setting.default is True


def test_lan_access_readers_take_nothing_saved_as_off():
    # The table keeps None (undecided: the desktop asks); every reader takes
    # it, or nothing saved, as this PC only.
    readers = READERS["general.webserver_listen_lan"]
    assert readers
    assert all(default is None or default is False
               for _, default in readers), readers
    assert module.Group(module.NETWORK)[1].default is None


def test_language_readers_take_nothing_saved_as_the_default():
    for setting in module.Group(module.LANGUAGE):
        readers = READERS[setting.key]
        assert readers
        # LoadLocale treats None (and "") as "default".
        assert all(default in (None, "default") for _, default in readers), \
            readers
        assert setting.default == "default"


def test_the_default_colours_are_the_boards():
    from src.core.Actions import AutonaActions

    colours = {s.name: s.default for s in module.SETTINGS
               if s.kind == module.COLOR}
    assert colours == {
        "team_1_default_color": AutonaActions.DEFAULT_TEAM_COLORS[1],
        "team_2_default_color": AutonaActions.DEFAULT_TEAM_COLORS[2]}


def test_the_hotkey_switch_default_is_the_desktops():
    from src.core import HotkeyRules

    readers = READERS[HotkeyRules.ENABLED_KEY]
    assert readers
    assert all(default is HotkeyRules.DEFAULT_ENABLED
               for _, default in readers), readers


def test_the_qt_scoreboard_switch_is_not_one_of_them():
    # general.qt_scoreboard_panel goes with the Qt boards (S4ac).
    assert "general.qt_scoreboard_panel" not in {
        s.key for s in module.SETTINGS}


# ── the writes ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("update, fields, name", [
    ("UpdateGeneral", {"profanity_filter": False, "disable_export": "yes"},
     "disable_export"),
    ("UpdateGeneral", {"profanity_filter": None}, "profanity_filter"),
    ("UpdateGeneral", {"profanity_filter": 0}, "profanity_filter"),
    ("UpdateGeneral", {"qt_scoreboard_panel": True}, "qt_scoreboard_panel"),
    ("UpdateGeneral", {"webserver_token": "x"}, "webserver_token"),
    ("UpdateGeneral", {"port": 6000}, "port"),
    ("UpdateGeneral", {"team_1_default_color": "red"}, "team_1_default_color"),
    ("UpdateGeneral", {"team_1_default_color": "#12345"},
     "team_1_default_color"),
    ("UpdateGeneral", {"team_1_default_color": "#1234567"},
     "team_1_default_color"),
    ("UpdateGeneral", {"team_2_default_color": 0xfe3636},
     "team_2_default_color"),
    ("UpdateNetwork", {"port": 0}, "port"),
    ("UpdateNetwork", {"port": 65536}, "port"),
    ("UpdateNetwork", {"port": True}, "port"),
    ("UpdateNetwork", {"port": "5107"}, "port"),
    ("UpdateNetwork", {"port": 5107.0}, "port"),
    ("UpdateNetwork", {"port": 6000, "listenLan": None}, "listenLan"),
    ("UpdateNetwork", {"listenLan": "true"}, "listenLan"),
    ("UpdateNetwork", {"webserver_port": 6000}, "webserver_port"),
    ("UpdateLanguage", {"program": "xx"}, "program"),
    ("UpdateLanguage", {"gameAsset": None}, "gameAsset"),
    ("UpdateLanguage", {"fgTerm": ""}, "fgTerm"),
    ("UpdateLanguage", {"program": "ja", "fgTerm": 5}, "fgTerm"),
    ("UpdateLanguage", {"program_language": "ja"}, "program_language"),
])
def test_one_bad_field_writes_nothing(saves, update, fields, name):
    settings = _make()

    refusal, written = getattr(settings, update)(fields)

    assert refusal.code == module.INVALID
    assert refusal.name == name
    assert written is False
    assert SettingsManager.settings == {}
    assert saves == []


@pytest.mark.parametrize("update", ["UpdateGeneral", "UpdateNetwork",
                                    "UpdateLanguage"])
@pytest.mark.parametrize("fields", [{}, None, [], "port"])
def test_an_empty_update_is_refused(saves, update, fields):
    refusal, written = getattr(_make(), update)(fields)
    assert refusal.code == module.INVALID and refusal.name is None
    assert written is False and saves == []


def test_a_save_writes_every_field_at_once(saves):
    settings = _make()

    refusal, written = settings.UpdateGeneral({
        "profanity_filter": False, "disable_export": True,
        "team_1_default_color": "#ABC"})

    assert (refusal, written) == (None, True)
    # One write of settings.json with all three.
    assert saves == [{"general": {"profanity_filter": False,
                                  "disable_export": True,
                                  "team_1_default_color": "#aabbcc"}}]


def test_the_value_in_effect_is_not_written_again(saves):
    settings = _make()

    # Nothing saved: these are the defaults.
    assert settings.UpdateGeneral({"profanity_filter": True,
                                   "disable_autoupdate": True,
                                   "team_1_default_color": "#FE3636"}) == (
        None, False)
    assert settings.UpdateNetwork({"port": DEFAULT_WEBSERVER_PORT}) == (
        None, False)
    assert settings.UpdateLanguage({"program": "default"}) == (None, False)
    assert saves == [] and SettingsManager.settings == {}

    # Only what differs is written.
    SettingsManager.settings["general"] = {"disable_export": True}
    assert settings.UpdateGeneral({"disable_export": True,
                                   "disable_overwrite": True}) == (None, True)
    assert saves == [{"general": {"disable_export": True,
                                  "disable_overwrite": True}}]


def test_a_value_saved_as_another_type_is_replaced(saves):
    SettingsManager.settings["general"] = {"profanity_filter": 1,
                                           "webserver_port": "5107"}
    settings = _make()

    assert settings.UpdateGeneral({"profanity_filter": True}) == (None, True)
    assert settings.UpdateNetwork({"port": 5107}) == (None, True)
    assert SettingsManager.settings["general"] == {
        "profanity_filter": True, "webserver_port": 5107}


@pytest.mark.parametrize("port", [1, 80, 65535])
def test_any_real_port_is_saved(saves, port):
    assert _make().UpdateNetwork({"port": port}) == (None, True)
    assert SettingsManager.settings["general"]["webserver_port"] == port


def test_an_undecided_lan_access_is_kept_by_a_port_save(saves):
    SettingsManager.settings["general"] = {"webserver_listen_lan": None}
    settings = _make()

    assert settings.UpdateNetwork({"port": 6000}) == (None, True)
    assert SettingsManager.settings["general"] == {
        "webserver_listen_lan": None, "webserver_port": 6000}
    assert settings.Describe()["network"]["listenLan"] is None


def test_nothing_saved_for_lan_access_stays_unsaved_by_a_port_save(saves):
    assert _make().UpdateNetwork({"port": 6000}) == (None, True)
    assert SettingsManager.settings == {"general": {"webserver_port": 6000}}


def test_lan_access_set_off_decides_it(saves):
    # Off is what the server already does for None, but the operator chose:
    # the desktop stops asking.
    SettingsManager.settings["general"] = {"webserver_listen_lan": None}
    assert _make().UpdateNetwork({"listenLan": False}) == (None, True)
    assert SettingsManager.settings["general"]["webserver_listen_lan"] is False


def test_a_language_saved_as_none_is_the_default_already(saves):
    SettingsManager.settings["program_language"] = None
    settings = _make()

    assert settings.UpdateLanguage({"program": "default"}) == (None, False)
    assert settings.UpdateLanguage({"program": "ja", "fgTerm": "en"}) == (
        None, True)
    assert saves == [{"program_language": "ja", "fg_term_language": "en"}]


@pytest.mark.parametrize("update, fields", [
    ("UpdateGeneral", {"profanity_filter": False}),
    ("UpdateNetwork", {"port": 6000}),
    ("UpdateNetwork", {"listenLan": True}),
    ("UpdateLanguage", {"program": "ja"}),
])
def test_nothing_is_written_while_settings_json_cannot_be_opened(
        saves, monkeypatch, tmp_path, update, fields):
    # Writing would replace the unread file with this one key (and lose the
    # access token with the rest). It is read again first, and still cannot
    # be opened (a directory: an OSError other than "not found").
    settings = _make()
    unopenable = tmp_path / "settings.json"
    unopenable.mkdir()
    monkeypatch.setattr(settingsModule, "SETTINGS_PATH", str(unopenable))
    monkeypatch.setattr(SettingsManager, "loadFailed", True)

    refusal, written = getattr(settings, update)(fields)

    assert refusal.code == module.NOT_READY
    assert written is False
    assert saves == [] and SettingsManager.settings == {}
    # The read still answers, and says why nothing can be saved.
    assert settings.Describe()["general"]["profanity_filter"] is True
    assert settings.Describe()["loadFailed"] is True


def test_a_launch_that_could_not_read_compares_with_the_file_read_again(
        saves, monkeypatch, tmp_path):
    # The launch's failure was passing (the file held by another program):
    # the save reads settings.json again, and decides what changes against
    # it. Against the defaults that stood in, 5107 would be "the same" and
    # the user's 6000 would stay.
    stored = {"general": {"webserver_port": 6000,
                          "webserver_token": SECRET_TOKEN},
              "designer": {"figma_pairing_code": SECRET_PAIRING}}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(stored), encoding="utf-8")
    monkeypatch.setattr(settingsModule, "SETTINGS_PATH", str(path))
    monkeypatch.setattr(SettingsManager, "loadFailed", True)
    settings = _make()
    assert settings.Describe()["network"]["port"] == DEFAULT_WEBSERVER_PORT

    assert settings.UpdateNetwork({"port": DEFAULT_WEBSERVER_PORT}) == (
        None, True)

    stored["general"]["webserver_port"] = DEFAULT_WEBSERVER_PORT
    assert saves == [stored]
    described = settings.Describe()
    assert described["loadFailed"] is False
    assert described["network"]["port"] == DEFAULT_WEBSERVER_PORT


def test_a_corrupt_file_is_moved_aside_and_the_save_goes_through(
        saves, monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    path.write_bytes(b'{"general": {"webserver_po')
    monkeypatch.setattr(settingsModule, "SETTINGS_PATH", str(path))
    monkeypatch.setattr(SettingsManager, "loadFailed", True)
    settings = _make()

    assert settings.UpdateGeneral({"profanity_filter": False}) == (None, True)

    backups = list(tmp_path.glob("settings.json.unreadable-*.bak"))
    assert [backup.read_bytes() for backup in backups] == [
        b'{"general": {"webserver_po']
    assert saves[-1] == {"general": {"profanity_filter": False}}
    described = settings.Describe()
    assert described["loadFailed"] is False
    # The file name only: the read is READ, and the path would show where
    # Autona is installed.
    assert described["movedAside"] == backups[0].name


def _failingSave(monkeypatch):
    def fail():
        raise OSError("disk full")

    monkeypatch.setattr(SettingsManager, "SaveSettings", fail)


@pytest.mark.parametrize("update, fields", [
    ("UpdateGeneral", {"profanity_filter": False, "disable_export": True}),
    ("UpdateNetwork", {"port": 6000, "listenLan": True}),
    ("UpdateLanguage", {"program": "ja", "fgTerm": "en"}),
])
def test_a_save_that_fails_leaves_the_settings_as_they_were(
        saves, monkeypatch, update, fields):
    # Memory must not keep what settings.json does not: the page would show
    # it, and the next save of anything would write it. Keys that were not
    # there are not left behind, empty parents neither.
    SettingsManager.settings.update({"general": {"profanity_filter": True},
                                     "designer": {"figma_pairing_code": "x"}})
    before = json.loads(json.dumps(SettingsManager.settings))
    settings = _make()
    _failingSave(monkeypatch)

    with pytest.raises(OSError):
        getattr(settings, update)(fields)

    assert SettingsManager.settings == before
    assert settings.Describe()["general"]["profanity_filter"] is True


def test_a_failed_set_many_puts_back_exactly_what_was_there(saves,
                                                           monkeypatch):
    SettingsManager.settings.update({
        "a": {"kept": 1, "replaced": [1, 2], "none": None},
        "flat": "x"})
    before = json.loads(json.dumps(SettingsManager.settings))
    replaced = SettingsManager.settings["a"]["replaced"]
    _failingSave(monkeypatch)

    with pytest.raises(OSError):
        SettingsManager.SetMany([
            ("a.replaced", "new"), ("a.none", 5), ("a.added", True),
            ("b.c.d", 1),
            # A later pair replacing an earlier one's parent.
            ("a", {"other": 1}), ("a.replaced", "again"),
            ("flat", None), ("fresh", 0)])

    assert SettingsManager.settings == before
    # The same objects, not copies: a reader holding one sees no change.
    assert SettingsManager.settings["a"]["replaced"] is replaced


def test_a_set_many_that_cannot_set_a_key_sets_none(saves):
    # "flat" is not an object: the third key cannot go under it. The two
    # before it are put back, and nothing is saved.
    SettingsManager.settings.update({"flat": "x", "a": {"kept": 1}})
    before = json.loads(json.dumps(SettingsManager.settings))

    with pytest.raises(TypeError):
        SettingsManager.SetMany([("a.kept", 2), ("n.new", 1),
                                 ("flat.below", 1)])

    assert SettingsManager.settings == before
    assert saves == []


def test_a_save_is_logged_with_its_keys_and_values(saves):
    from loguru import logger

    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        _make().UpdateNetwork({"port": 6000})
    finally:
        logger.remove(sink)

    assert any("general.webserver_port=6000" in m for m in messages)


# ── restartPending ───────────────────────────────────────────────────────

def test_network_pending_while_no_server_runs_compares_with_the_start(saves):
    SettingsManager.settings["general"] = {"webserver_port": 5107,
                                           "webserver_listen_lan": False}
    settings = _make()
    assert settings.Describe()["network"]["restartPending"] is False

    settings.UpdateNetwork({"port": 6000})
    assert settings.Describe()["network"]["restartPending"] is True
    settings.UpdateNetwork({"port": 5107})
    assert settings.Describe()["network"]["restartPending"] is False
    settings.UpdateNetwork({"listenLan": True})
    assert settings.Describe()["network"]["restartPending"] is True


def test_network_pending_compares_with_what_the_server_is_bound_with(saves):
    SettingsManager.settings["general"] = {"webserver_port": 5107,
                                           "webserver_listen_lan": None}
    listening = _Listening((5107, False))
    settings = _make(listening)

    described = settings.Describe()["network"]
    assert described["listening"] == {"port": 5107, "lan": False}
    # None is off to the server: nothing to restart for.
    assert described["restartPending"] is False

    settings.UpdateNetwork({"port": 6000})
    assert settings.Describe()["network"]["restartPending"] is True
    # The server restarted on it.
    listening.value = (6000, False)
    assert settings.Describe()["network"] == {
        "port": 6000, "listenLan": None,
        "listening": {"port": 6000, "lan": False}, "restartPending": False}

    # The desktop's first-run question saved LAN access before the server
    # started with it: bound with it, nothing pending.
    SettingsManager.settings["general"]["webserver_listen_lan"] = True
    listening.value = (6000, True)
    assert settings.Describe()["network"]["restartPending"] is False
    listening.value = (6000, False)
    assert settings.Describe()["network"]["restartPending"] is True


def test_a_failing_listening_reads_as_no_server(saves):
    def broken():
        raise RuntimeError("no server module")

    settings = AppSettings(languages=lambda: LANGUAGES, listening=broken)
    assert settings.Describe()["network"]["listening"] is None


def test_language_pending_compares_with_the_start(saves):
    SettingsManager.settings["program_language"] = None
    settings = _make()
    assert settings.Describe()["language"]["restartPending"] is False

    settings.UpdateLanguage({"gameAsset": "ja"})
    assert settings.Describe()["language"]["restartPending"] is True
    settings.UpdateLanguage({"gameAsset": "default"})
    # "default" saved where nothing was: the same language.
    assert settings.Describe()["language"]["restartPending"] is False


def test_the_start_is_what_was_saved_when_it_was_built(saves):
    # bootstrap_core() builds it before either shell reads these.
    SettingsManager.settings["general"] = {"webserver_port": 6000}
    SettingsManager.settings["program_language"] = "ja"
    settings = _make()

    SettingsManager.settings["general"]["webserver_port"] = 5107
    SettingsManager.settings["program_language"] = "en"
    described = settings.Describe()
    assert described["network"]["restartPending"] is True
    assert described["language"]["restartPending"] is True


def test_import_builds_nothing():
    # agent-pitfalls #1 (the subprocess check is tests/test_import_side_effects.py).
    source = (PROJECT_ROOT / "src" / "core" / "AppSettings.py").read_text(
        encoding="utf-8")
    assert "instance = AppSettings(" not in source
