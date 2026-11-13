"""The SCORE page's field writes (ADR-005 S4z).

What the React SCORE page will edit a board 1 / 2 slot and the match bar
with, added to /api/v1 with the Qt panel still there:

- ``PATCH .../teams/{t}/players/{p}/fields``           the card's inputs
- ``PATCH .../teams/{t}/players/{p}/characters/{row}``  one character row
- ``PATCH /api/v1/scoreboards/{n}/match``               the match bar

Each writes what the desktop's own input wrote (the Qt card's line edits and
combos, the match bar's combos and BEST OF -- checked against them until
they went with the Qt SCORE in ADR-005 S4ac-3; the card's against a
recording of it since), in one save cycle, held on the desktop until 反映
and published at once headless. A refused field writes nothing.
"""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import PlayerService as players
from src.core import StateSchema as schema
from src.core.Actions import AutonaActions
from src.core.CountryCatalog import CountryCatalog
from src.core.GameAssetCatalog import GameAssetCatalog
from src.core.MatchNameCandidates import MatchNameCandidates
from src.core.PlayerService import PlayerService
from src.core.StateManager import StateManager
from tests.fixtures.write_recording import WriteRecorder, encode
from tests.test_api_v1 import _assertRejected, client, webApp  # noqa: F401
from tests.test_api_v1_score import (  # noqa: F401
    _apply, _cycles, _ok, _oneCycle, _pending, _published, desktopBoards,
    shell)
from tests.test_game_asset_manager_load import (  # noqa: F401
    _load, goldenGames)
from tests.test_web_operations import _dropHeadlessBoards, _goHeadless

SLOT = "score.1.team.1.player.1"
FIELDS = "/api/v1/scoreboards/{n}/teams/{t}/players/{p}/fields"
CHARACTER = "/api/v1/scoreboards/{n}/teams/{t}/players/{p}/characters/{row}"
MATCH = "/api/v1/scoreboards/{n}/match"

# The player the slot holds before each run: an id, a city, a seed and an
# online avatar from a provider, which a name edit treats each its own way.
START = {"gamerTag": "ALPHA", "prefix": "SP", "name": "Real Alpha",
         "twitter": "alpha_tw", "custom_textbox": "first", "id": 1234,
         "seed": 3, "city": "Shibuya", "avatar": "avatars/alpha.png"}


def _fields(client, body, n=1, t=1, p=1):
    return client.patch(FIELDS.format(n=n, t=t, p=p), json=body)


def _character(client, body, row=1, n=1, t=1, p=1):
    return client.patch(CHARACTER.format(n=n, t=t, p=p, row=row), json=body)


def _match(client, body, n=1):
    return client.patch(MATCH.format(n=n), json=body)


def _slot(path=SLOT):
    with StateManager.lock:
        return copy.deepcopy(StateManager.Get(path))


# What the Qt card wrote, step by step (FIELD_STEPS / CHARACTER_STEPS done on
# its line edits, note box and combos), recorded on the last tree that had it
# (f71a3b90, just before ADR-005 S4ac-3), where these routes' runs equalled
# it: every write in order with its value's bytes (so its key order), the
# deltas published at 反映, and the slot afterwards.
CARD_RECORDING = (Path(__file__).parent / "fixtures" / "score_fields"
                  / "golden.json")


def _onTheCard(name):
    return json.loads(CARD_RECORDING.read_text(encoding="utf-8"))[name]


def _asRecorded(played):
    """A run as the recording holds it (its tuples as JSON lists)."""
    return json.loads(json.dumps(played))


# The locale the fields' recording ran under: the recording machine's first
# UI language (a Japanese Windows), which LoadLocale() falls back to when the
# settings name none. The character rows need none: goldenGames pins the
# game recording's locale.
RECORDED_LOCALE = "ja-Jpan-JP"
LOCALE_SETTINGS = ("program_language", "game_asset_language",
                   "fg_term_language")


@pytest.fixture
def recordedLocale(autonaApp):
    """The countries loaded under the recording's locale, whatever the
    machine's, as tests/test_set_follow_flow.py's recordedLocale.

    A country's value carries its names in the program and export locales
    ("日本" / "Japan"), taken when the countries are loaded; an English
    runner wrote every country step with the other names. Pinned the way
    the harness loads it at start-up (LoadLocale, LoadRoundNames), with the
    countries reloaded under it; afterwards all three are loaded again
    under the harness's own settings.
    """
    from src.core.SettingsManager import SettingsManager
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper

    def load():
        AutonaLocaleHelper.LoadLocale()
        AutonaLocaleHelper.LoadRoundNames()
        CountryCatalog.instance.Load()
        autonaApp.settle()

    realGet = SettingsManager.Get
    with patch.object(SettingsManager, "Get", lambda key, default=None: (
            RECORDED_LOCALE if key in LOCALE_SETTINGS
            else realGet(key, default))):
        load()
    assert CountryCatalog.instance.IsLoaded()
    try:
        yield
    finally:
        load()


@pytest.fixture
def countries(autonaApp):
    """The country data, loaded the way the first card built loads it (the
    harness builds no card until a test asks for one). Kept loaded: a card
    built by any later test would load it anyway."""
    if not CountryCatalog.instance.IsLoaded():
        CountryCatalog.instance.Load()
        autonaApp.settle()
    assert CountryCatalog.instance.IsLoaded()


def _load_start(app, n=1):
    """START in slot 1 of team 1, published."""
    PlayerService(f"score.{n}.team.1.player.1").ApplyData(
        dict(START), loadFromDB=False)
    AutonaActions.instance.ApplyChanges(n)
    app.settle()


# ── the card's inputs, as the operator used them ─────────────────────────

# (what it is, what the route is sent): what the operator did on the Qt card
# (its line edits, the note, the country and region combos) step by step,
# which these requests wrote the same as until ADR-005 S4ac-3.
FIELD_STEPS = [
    ("the tag", {"gamerTag": "ALPHA2"}),
    ("the sponsor", {"prefix": "NEW"}),
    ("the sponsor emptied", {"prefix": ""}),
    ("the real name", {"name": "Someone"}),
    ("the twitter", {"twitter": "tw2"}),
    ("the note", {"custom_textbox": "one\ntwo"}),
    ("a country", {"country_code": "JP"}),
    ("a region", {"state_code": "13"}),
    ("another country", {"country_code": "US"}),
    ("no country", {"country_code": ""}),
    ("the same tag again", {"gamerTag": "ALPHA2"}),
]


def _play(app, client):
    """Each step through the route, then 反映: what it wrote, what was
    published and the slot afterwards."""
    _load_start(app)
    played = []
    for label, body in FIELD_STEPS:
        with WriteRecorder() as recorder:
            _ok(_fields(client, body))
            AutonaActions.instance.ApplyChanges(1)
            app.settle()
        played.append((label, recorder.Result(), encode(_slot())))
    return played


def test_the_fields_write_what_the_qt_card_wrote(
        autonaApp, client, recordedLocale, monkeypatch):
    # Against the Qt card itself until ADR-005 S4ac-3, against its
    # recording since (under its locale: recordedLocale).
    onCard = _onTheCard("fields")
    assert [label for label, _recorded, _slot_ in onCard] \
        == [label for label, _body in FIELD_STEPS]
    # Every step but the last wrote something; the last nothing.
    assert [label for label, recorded, _slot_ in onCard
            if not recorded["sets"]] == ["the same tag again"]

    autonaApp.ready(1)
    assert _asRecorded(_play(autonaApp, client)) == onCard

    with monkeypatch.context() as headless:
        _goHeadless(autonaApp, headless)
        try:
            assert _asRecorded(_play(autonaApp, client)) == onCard
        finally:
            _dropHeadlessBoards(autonaApp)


def test_a_new_name_keeps_the_avatar_city_and_seed_and_drops_the_id(
        autonaApp, client, shell):
    _load_start(autonaApp)
    before = _slot()
    assert (before["id"], before["city"], before["seed"],
            before["online_avatar"]) == (1234, "Shibuya", 3,
                                         "avatars/alpha.png")

    _ok(_fields(client, {"gamerTag": "ALPHA2"}))
    _apply(client)
    autonaApp.settle()

    after = _slot()
    assert after["name"] == "ALPHA2"
    assert (after["mergedName"], after["mergedOnlyName"]) == (
        "SP | ALPHA2", "ALPHA2")
    assert after["id"] is None
    assert (after["city"], after["seed"], after["online_avatar"]) == (
        "Shibuya", 3, "avatars/alpha.png")


def test_a_field_that_names_no_one_else_keeps_the_id(
        autonaApp, client, shell, countries):
    _load_start(autonaApp)

    _ok(_fields(client, {"twitter": "other", "name": "", "country_code": "JP",
                         "custom_textbox": ""}))
    _apply(client)
    autonaApp.settle()

    slot = _slot()
    assert slot["id"] == 1234
    assert (slot["twitter"], slot["real_name"], slot["custom_textbox"]) == (
        "other", "", "")
    assert slot["country"]["code"] == "JP"
    assert slot["mergedName"] == "SP | ALPHA"


def test_the_fields_are_one_save_cycle(autonaApp, client, shell, countries):
    _load_start(autonaApp)

    with _cycles() as told:
        _ok(_fields(client, {"prefix": "TM", "gamerTag": "BRAVO",
                             "twitter": "b", "country_code": "JP",
                             "state_code": "13", "name": "Real B"}))
        autonaApp.settle()

    _oneCycle(told)
    _apply(client)
    autonaApp.settle()
    slot = _slot()
    assert (slot["team"], slot["name"], slot["mergedName"], slot["id"]) == (
        "TM", "BRAVO", "TM | BRAVO", None)
    assert (slot["country"]["code"], slot["state"]["code"]) == ("JP", "13")


def test_a_region_alone_is_of_the_country_shown(
        autonaApp, client, shell, countries):
    _load_start(autonaApp)
    _ok(_fields(client, {"country_code": "JP"}))
    _ok(_fields(client, {"state_code": "13"}))
    _apply(client)
    autonaApp.settle()
    assert _slot()["state"]["code"] == "13"

    # Another country without a region empties it, as a pick does; with one
    # of its regions, that region.
    _ok(_fields(client, {"country_code": "US", "state_code": "CA"}))
    _apply(client)
    autonaApp.settle()
    assert (_slot()["country"]["code"], _slot()["state"]["code"]) == (
        "US", "CA")
    _ok(_fields(client, {"country_code": "JP"}))
    _apply(client)
    autonaApp.settle()
    assert (_slot()["country"]["code"], _slot()["state"]) == ("JP", {})

    _ok(_fields(client, {"state_code": "13"}))
    _ok(_fields(client, {"state_code": ""}))
    _apply(client)
    autonaApp.settle()
    assert (_slot()["country"]["code"], _slot()["state"]) == ("JP", {})


def test_the_fields_are_held_until_apply(
        autonaApp, client, desktopBoards, countries):
    _load_start(autonaApp)
    published = copy.deepcopy(_published(client))

    _ok(_fields(client, {"gamerTag": "HELD", "country_code": "JP"}))
    autonaApp.settle()

    assert _published(client) == published
    pending = _pending(client)
    assert pending["dirty"] is True
    player = pending["board"]["team"]["1"]["player"]["1"]
    assert (player["name"], player["country"]["code"]) == ("HELD", "JP")

    _apply(client)
    autonaApp.settle()
    assert _published(client)["team"]["1"]["player"]["1"]["name"] == "HELD"


def _nothingWritten(app, request):
    before = json.dumps(_slot(), sort_keys=True, default=str)
    with WriteRecorder() as recorder:
        response = request()
        app.settle()
    assert recorder.sets == []
    assert json.dumps(_slot(), sort_keys=True, default=str) == before
    return response


def test_a_field_already_shown_writes_nothing(
        autonaApp, client, shell, countries):
    """Not even the same value again: on the desktop it would light 反映
    for nothing (the card's line edit writes only what differs)."""
    _load_start(autonaApp)
    _ok(_fields(client, {"country_code": "JP", "state_code": "13"}))
    autonaApp.settle()
    for body in ({"gamerTag": "ALPHA"}, {"prefix": "SP", "twitter": "alpha_tw"},
                 {"country_code": "JP"}, {"state_code": "13"},
                 {"country_code": "JP", "state_code": "13",
                  "custom_textbox": "first", "name": "Real Alpha"}):
        _ok(_nothingWritten(autonaApp, lambda: _fields(client, body)))


@pytest.mark.parametrize("body", [
    {}, {"gamerTag": 1}, {"gamerTag": None}, {"prefix": ["SP"]},
    {"twitter": {"x": 1}}, {"country_code": None}, {"state_code": 13},
    {"seed": 3}, {"id": 1}, {"real_name": "x"}, {"team": "x"},
    {"gamerTag": "OK", "city": "x"}])
def test_a_bad_body_is_400_and_writes_nothing(
        autonaApp, client, shell, countries, body):
    _load_start(autonaApp)
    _assertRejected(_nothingWritten(autonaApp, lambda: _fields(client, body)))


# PUT /players' rule for a player's texts (S4z review): no line break outside
# the note, and a length. Not trimmed first: a trailing break is one too.
@pytest.mark.parametrize("body", [
    {"gamerTag": "a\nb"}, {"gamerTag": "ALPHA\n"}, {"prefix": "a\rb"},
    {"name": "a\r\nb"}, {"twitter": "a b"},
    {"gamerTag": "OK", "twitter": "\n"},
    {"gamerTag": "x" * 257}, {"prefix": "x" * 257}, {"name": "x" * 257},
    {"twitter": "x" * 257}, {"custom_textbox": "x" * 4097}])
def test_a_text_that_breaks_the_player_text_rule_is_400_and_writes_nothing(
        autonaApp, client, shell, body):
    _load_start(autonaApp)
    response = _nothingWritten(autonaApp, lambda: _fields(client, body))
    _assertRejected(response)
    [key] = [key for key, value in body.items() if value != "OK"]
    assert response.get_json()["error"]["message"] in (
        f"{key} must be one line", f"{key} must be at most 256 characters",
        f"{key} must be at most 4096 characters")


def test_the_note_keeps_its_lines_and_no_text_is_trimmed(
        autonaApp, client, shell):
    _load_start(autonaApp)
    _ok(_fields(client, {"custom_textbox": "  a\nb  " + "x" * 4089,
                         "twitter": " @t ", "name": "x" * 256}))
    _apply(client)
    autonaApp.settle()
    slot = _slot()
    assert slot["custom_textbox"] == "  a\nb  " + "x" * 4089
    assert (slot["twitter"], slot["real_name"]) == (" @t ", "x" * 256)


def test_no_value_is_in_a_text_refusal(autonaApp, client, shell):
    secret = "Secret Person"
    for body in ({"gamerTag": f"{secret}\nx"}, {"prefix": secret * 20},
                 {"custom_textbox": secret * 400}):
        refused = _fields(client, body)
        assert refused.status_code == 400
        assert secret not in refused.get_data(as_text=True)


# No value in the message (the route logs keys only: player data, §2.7).
@pytest.mark.parametrize("body, message", [
    ({"gamerTag": "OK", "country_code": "ZZ"}, "unknown country_code"),
    ({"country_code": "JP", "state_code": "CA"},
     "state_code is not a region of the country"),
    # A region with no country shown.
    ({"state_code": "13"}, "state_code is not a region of the country"),
    ({"country_code": "", "state_code": "13"},
     "state_code is not a region of the country"),
])
def test_a_location_the_data_does_not_have_is_400_and_writes_nothing(
        autonaApp, client, shell, countries, body, message):
    _load_start(autonaApp)
    response = _nothingWritten(autonaApp, lambda: _fields(client, body))
    _assertRejected(response)
    assert response.get_json()["error"]["message"] == message


def test_a_location_waits_for_the_country_data(
        autonaApp, client, shell, countries, monkeypatch):
    _load_start(autonaApp)
    monkeypatch.setattr(CountryCatalog.instance, "IsLoaded", lambda: False)

    for body in ({"country_code": "JP"}, {"state_code": "13"},
                 {"gamerTag": "X", "country_code": "JP"}):
        _assertRejected(
            _nothingWritten(autonaApp, lambda: _fields(client, body)),
            "NOT_READY", 409)
    # Emptying one, or no location at all, needs no country data.
    _ok(_fields(client, {"country_code": "", "state_code": ""}))
    _ok(_fields(client, {"twitter": "x"}))


@pytest.mark.parametrize("n, t, p, code, status", [
    (3, 1, 1, "INVALID_ARGUMENT", 400),
    (4, 1, 1, "INVALID_ARGUMENT", 400),
    (1, 3, 1, "INVALID_ARGUMENT", 400),
    (1, 1, 0, "INVALID_ARGUMENT", 400),
    (1, 1, 9, "NOT_FOUND", 404),
    (2, 2, 2, "NOT_FOUND", 404),
])
def test_a_slot_that_is_not_there_is_refused(
        autonaApp, client, shell, n, t, p, code, status):
    response = _fields(client, {"twitter": "x"}, n=n, t=t, p=p)
    _assertRejected(response, code, status)


# ── a character row ──────────────────────────────────────────────────────

# (what it is, (the row, what the route is sent)): the picks the operator
# made on the Qt card's character, skin and variant combos, which these
# requests wrote the same as until ADR-005 S4ac-3.
CHARACTER_STEPS = [
    ("Mario on row 1", (1, {"en_name": "Mario"})),
    ("its third skin, another character", (1, {"skin": 2})),
    ("a variant", (1, {"variant": "EX"})),
    ("Luigi: the first skin, the variant kept", (1, {"en_name": "Luigi"})),
    ("no variant", (1, {"variant": ""})),
    ("Peach on row 2", (2, {"en_name": "Peach"})),
    ("its second skin", (2, {"skin": 1})),
    ("a character without skins", (3, {"en_name": "NoArt"})),
    ("row 1 emptied", (1, {"en_name": ""})),
    ("the same character again", (2, {"en_name": "Peach"})),
]


def _playCharacters(app, client):
    _load_start(app)
    played = []
    for label, (row, body) in CHARACTER_STEPS:
        with WriteRecorder() as recorder:
            _ok(_character(client, body, row=row))
            AutonaActions.instance.ApplyChanges(1)
            app.settle()
        # Not the writes: a pick on the Qt card wrote the rows twice (its
        # skin combo's new rows, then the pick), the same value both times.
        played.append((label, recorder.deltas, recorder.bigChanges,
                       encode(_slot()["character"])))
    return played


def test_a_character_row_writes_what_the_qt_card_s_combos_wrote(
        autonaApp, client, goldenGames, monkeypatch):
    # Against the Qt card's combos until ADR-005 S4ac-3, against their
    # recording since.
    onCard = _onTheCard("characters")
    assert [label for label, _deltas, _big, _rows in onCard] \
        == [label for label, _step in CHARACTER_STEPS]
    assert [label for label, deltas, _big, _rows in onCard if not deltas] \
        == ["the same character again"]

    _load(goldenGames, "tgame")
    autonaApp.settle()
    snap = GameAssetCatalog.instance.Current()
    assert len(snap.skinRows.get("NoArt", ())) == 0
    assert len(snap.skinRows.get("Mario", ())) == 3
    autonaApp.ready(1)
    assert _asRecorded(_playCharacters(autonaApp, client)) == onCard

    with monkeypatch.context() as headless:
        _goHeadless(autonaApp, headless)
        try:
            assert _asRecorded(_playCharacters(autonaApp, client)) == onCard
        finally:
            _dropHeadlessBoards(autonaApp)


@pytest.fixture
def game(goldenGames, shell, autonaApp):
    """The fixture game on the shell's boards, START in slot 1."""
    _load(goldenGames, "tgame")
    autonaApp.settle()
    _load_start(autonaApp)
    return shell


def _rowValue(row):
    return _slot()["character"][row]


def test_a_character_pick_is_one_save_cycle_and_the_other_rows_stay(
        autonaApp, client, game):
    _ok(_character(client, {"en_name": "Peach", "skin": 2}, row=2))
    _apply(client)
    autonaApp.settle()
    others = (copy.deepcopy(_rowValue(1)), copy.deepcopy(_rowValue(3)))

    with _cycles() as told:
        _ok(_character(client, {"en_name": "Mario", "skin": 1,
                                "variant": "Alt", "game": "tgame"}))
        autonaApp.settle()
    _oneCycle(told)
    _apply(client)
    autonaApp.settle()

    value = _rowValue(1)
    assert (value["en_name"], value["skin"], value["variant"]["en_name"]) \
        == ("Mario", 1, "Alt")
    assert (_rowValue(2)["en_name"], _rowValue(2)["skin"]) == ("Peach", 2)
    assert _rowValue(3) == others[1]


def test_a_skin_of_a_character_without_skins_is_none(autonaApp, client, game):
    _ok(_character(client, {"en_name": "NoArt", "skin": 5}))
    _apply(client)
    autonaApp.settle()
    assert (_rowValue(1)["en_name"], _rowValue(1)["skin"]) == ("NoArt", -1)


def test_a_pick_already_shown_writes_nothing(autonaApp, client, game):
    _ok(_character(client, {"en_name": "Peach", "skin": 1, "variant": "EX"}))
    autonaApp.settle()
    for body in ({"en_name": "Peach"}, {"skin": 1}, {"variant": "EX"},
                 {"en_name": "Peach", "skin": 1, "variant": "EX",
                  "game": "tgame"}):
        _ok(_nothingWritten(autonaApp, lambda: _character(client, body)))


def test_the_character_row_is_held_until_apply(
        autonaApp, client, goldenGames, desktopBoards):
    _load(goldenGames, "tgame")
    autonaApp.settle()
    _load_start(autonaApp)
    published = copy.deepcopy(_published(client))

    _ok(_character(client, {"en_name": "Zelda"}))
    autonaApp.settle()
    assert _published(client) == published
    pending = _pending(client)
    assert pending["board"]["team"]["1"]["player"]["1"]["character"]["1"][
        "en_name"] == "Zelda"

    _apply(client)
    autonaApp.settle()
    assert _published(client)["team"]["1"]["player"]["1"]["character"]["1"][
        "en_name"] == "Zelda"


@pytest.mark.parametrize("body", [
    {}, {"game": "tgame"}, {"en_name": None}, {"en_name": 1},
    {"skin": -1}, {"skin": True}, {"skin": "1"}, {"skin": 1.0},
    {"variant": 1}, {"variant": ["EX"]}, {"en_name": "Mario", "game": 1},
    {"en_name": "Mario", "color": 1}, {"name": "Mario"}])
def test_a_bad_character_body_is_400_and_writes_nothing(
        autonaApp, client, game, body):
    _assertRejected(
        _nothingWritten(autonaApp, lambda: _character(client, body)))


@pytest.mark.parametrize("body, row, code, status", [
    ({"en_name": "Nobody"}, 1, "INVALID_ARGUMENT", 400),
    ({"en_name": "Mario", "skin": 3}, 1, "INVALID_ARGUMENT", 400),
    ({"variant": "Nope"}, 1, "INVALID_ARGUMENT", 400),
    ({"en_name": "Mario"}, 0, "INVALID_ARGUMENT", 400),
    ({"en_name": "Mario"}, 4, "NOT_FOUND", 404),
    ({"en_name": "Mario", "game": "game2"}, 1, "CONFLICT", 409),
    ({"en_name": "", "game": ""}, 1, "CONFLICT", 409),
])
def test_a_pick_the_game_does_not_have_is_refused_and_writes_nothing(
        autonaApp, client, game, body, row, code, status):
    _assertRejected(
        _nothingWritten(autonaApp, lambda: _character(client, body, row=row)),
        code, status)


def test_with_no_game_a_character_is_400(autonaApp, client, shell):
    _load_start(autonaApp)
    _assertRejected(_nothingWritten(
        autonaApp, lambda: _character(client, {"en_name": "Mario"})))
    # The blank row needs no game.
    _ok(_character(client, {"en_name": ""}))


@pytest.mark.parametrize("n, t, p, code, status", [
    (3, 1, 1, "INVALID_ARGUMENT", 400),
    (1, 0, 1, "INVALID_ARGUMENT", 400),
    (1, 1, 0, "INVALID_ARGUMENT", 400),
    (1, 2, 9, "NOT_FOUND", 404),
])
def test_a_character_slot_that_is_not_there_is_refused(
        autonaApp, client, shell, n, t, p, code, status):
    _assertRejected(_character(client, {"en_name": ""}, n=n, t=t, p=p),
                    code, status)


def test_the_refusals_are_core_s(autonaApp, game):
    service = PlayerService(SLOT)
    assert service.EditCharacter(1, character="Nobody") \
        == players.UNKNOWN_CHARACTER
    assert service.EditCharacter(9) == players.NO_SUCH_ROW
    assert service.EditCharacter(1, game="other") == players.OTHER_GAME
    assert service.Edit({}, country="ZZ") == players.UNKNOWN_COUNTRY
    assert service.Edit({}, region="13") == players.UNKNOWN_REGION
    with pytest.raises(ValueError):
        service.Edit({"seed": "3"})


def test_a_slot_with_no_character_rows_has_no_row_to_pick_on(
        autonaApp, client, game):
    # S4z review: a roster of 0 characters is a slot without rows, not the
    # default 3 a load makes up for a slot that has none.
    assert AutonaActions.instance.SetCharactersPerPlayer(1, 0)
    AutonaActions.instance.ApplyChanges(1)
    autonaApp.settle()
    assert _slot()["character"] == {}

    for row in (1, 2, 3):
        _assertRejected(_nothingWritten(
            autonaApp,
            lambda: _character(client, {"en_name": "Mario"}, row=row)),
            "NOT_FOUND", 404)
    assert _slot()["character"] == {}
    assert PlayerService(SLOT).EditCharacter(1, character="Mario") \
        == players.NO_SUCH_ROW


@pytest.mark.parametrize("raising", ["PublishMergedName", "PublishImages",
                                     "SetCountry"])
def test_an_edit_that_raises_half_way_leaves_no_previous_id(
        autonaApp, shell, countries, monkeypatch, raising):
    # S4z review: ApplyData's guarantee. The tag is written, so whatever
    # raises after it, the id is emptied and the statistics are asked for.
    _load_start(autonaApp)
    asked = []

    def broken(*_args, **_kwargs):
        raise RuntimeError("broken")

    # Its own context: `shell` has patched with the same monkeypatch.
    with monkeypatch.context() as patched:
        patched.setattr(PlayerService, "Announce",
                        lambda self, change: asked.append(change))
        patched.setattr(PlayerService, raising, broken)
        with pytest.raises(RuntimeError, match="broken"):
            PlayerService(SLOT).Edit({schema.PLAYER_TAG_KEY: "OTHER"},
                                     country="JP")
    AutonaActions.instance.ApplyChanges(1)
    autonaApp.settle()

    slot = _slot()
    assert slot["name"] == "OTHER"
    assert slot["id"] is None
    assert [change.idChanged for change in asked] == [True]
    if raising != "PublishMergedName":
        # The display name is of what was written.
        assert slot["mergedName"] == "SP | OTHER"


# ── the match bar ────────────────────────────────────────────────────────

def _matchSteps(listed):
    """What the route is sent: what the operator did on the Qt bar (its
    combos' editingFinished and currentIndexChanged, its BEST OF spinbox),
    which these requests wrote the same as until ADR-005 S4ac-3. `listed`
    is a term of the match combo's list."""
    return [
        {"phase": "Top 8", "pool": "A1", "match": listed, "bestOf": 5},
        {"phase": ""},
        {"match": "", "pool": ""},
        {"bestOf": 0},
        {"bestOf": 3, "match": "Grand Final"},
        {"bestOf": 3, "match": "Grand Final"},
    ]


def _playMatch(app, client, n, steps):
    played = []
    AutonaActions.instance.SetMatchField(n, "phase", "")
    AutonaActions.instance.SetMatchField(n, "pool", "")
    AutonaActions.instance.SetMatchField(n, "match", "")
    AutonaActions.instance.SetBestOf(n, 0)
    StateManager.Set(f"score.{n}.match_en", "Kept English")
    AutonaActions.instance.ApplyChanges(n)
    app.settle()
    for body in steps:
        with WriteRecorder() as recorder:
            _ok(_match(client, body, n))
            AutonaActions.instance.ApplyChanges(n)
            app.settle()
        board = _slot(f"score.{n}")
        played.append((recorder.Result(), encode(
            {key: board.get(key) for key in (
                "phase", "pool", "match", "match_en", "best_of")})))
    return played


@pytest.mark.parametrize("n", [1, 2])
def test_the_match_bar_writes_the_same_on_both_shells(
        autonaApp, client, monkeypatch, n):
    # Against the real Qt bar until ADR-005 S4ac-3 (S4z review; the
    # desktop's run equalled it, step by step).
    autonaApp.ready(n)
    terms = MatchNameCandidates.instance.Candidates(n)["match"]
    assert terms, "the match combo offers no term"
    steps = _matchSteps(terms[0])

    desktop = _playMatch(autonaApp, client, n, steps)
    # match_en is never touched; "" empties; 0 is a set length.
    assert json.loads(desktop[0][1])["match"] == terms[0]
    assert json.loads(desktop[1][1])["phase"] == ""
    assert json.loads(desktop[2][1])["match"] == ""
    assert json.loads(desktop[3][1])["best_of"] == 0
    assert {json.loads(state)["match_en"] for _rec, state in desktop} \
        == {"Kept English"}
    assert all(recorded["sets"] for recorded, _state in desktop[:-1])
    assert desktop[-1][0]["sets"] == []

    with monkeypatch.context() as headless:
        _goHeadless(autonaApp, headless)
        try:
            assert _playMatch(autonaApp, client, n, steps) == desktop
        finally:
            _dropHeadlessBoards(autonaApp)


def test_patch_set_reads_the_same_body_otherwise(autonaApp, client, shell):
    """What the docs say PATCH set does differently, so a client knows which
    to call: a blank phase or match is not carried, 0 is not a set length,
    and match_en is emptied."""
    _ok(_match(client, {"phase": "P", "match": "M", "bestOf": 3}))
    StateManager.Set("score.1.match_en", "Kept English")
    _apply(client)
    autonaApp.settle()

    _ok(client.patch("/api/v1/scoreboards/1/set",
                     json={"phase": "", "match": "", "bestOf": 0}))
    _apply(client)
    autonaApp.settle()
    board = _slot("score.1")
    assert (board["phase"], board["match"], board["best_of"],
            board["match_en"]) == ("P", "M", 3, "")


def test_the_match_bar_is_one_save_cycle(autonaApp, client, shell):
    with _cycles() as told:
        _ok(_match(client, {"phase": "Top 8", "pool": "B", "match": "M",
                            "bestOf": 7}))
        autonaApp.settle()
    _oneCycle(told)


def test_the_match_bar_is_held_until_apply(autonaApp, client, desktopBoards):
    published = copy.deepcopy(_published(client, 2))

    _ok(_match(client, {"match": "HELD", "bestOf": 9}, n=2))
    autonaApp.settle()
    assert _published(client, 2) == published
    assert (_pending(client, 2)["board"]["match"],
            _pending(client, 2)["board"]["best_of"]) == ("HELD", 9)

    _apply(client, 2)
    autonaApp.settle()
    assert (_published(client, 2)["match"],
            _published(client, 2)["best_of"]) == ("HELD", 9)


@pytest.mark.parametrize("body", [
    {}, {"phase": 1}, {"pool": None}, {"match": ["x"]}, {"bestOf": -1},
    {"bestOf": 100}, {"bestOf": "3"}, {"bestOf": True}, {"bestOf": 2.0},
    {"match_en": "x"}, {"players": 2}, {"characters": 1}, {"losers": True},
    {"teamName": {"team": 1, "value": "x"}}, {"match": "OK", "pool": 3}])
def test_a_bad_match_body_is_400_and_writes_nothing(
        autonaApp, client, shell, body):
    before = json.dumps(_slot("score.1"), sort_keys=True, default=str)
    with WriteRecorder() as recorder:
        _assertRejected(_match(client, body))
        autonaApp.settle()
    assert recorder.sets == []
    assert json.dumps(_slot("score.1"), sort_keys=True, default=str) == before


@pytest.mark.parametrize("n", [3, 4, 0])
def test_the_match_bar_is_board_1_and_2_s(autonaApp, client, shell, n):
    _assertRejected(_match(client, {"match": "x"}, n))
