"""src/core/PlayerDirectory.py (ADR-005 S4m): the player list's rows and writes.

The player list is becoming a React page, which has no player card to save
through, so the rows it shows and the record it saves are core's. Three
properties get the attention here:

- **the record is the card's.** A save makes the record
  PlayerService.PrepareSaveToDB makes from a slot: the golden below runs the
  same picks through both, on two databases, and compares the records (their
  key order too) and the bytes of the files.
- **the database can hold anything.** A row whose mains are a JSON string, a
  broken entry, a row with no tag: the list shows what it can, and never
  falls over (the Qt list loses every row to a key that is not a string).
- **a save does not damage what was not edited.** A character the game does not
  have now goes back and forth as it is, and what is not sent of the mains and
  the location stays, whatever it is (a free-form region: start.gg locations
  nothing resolved). The golden sends all of them, since a card saves them all.

Over the player slot recording's catalogs (tests/test_player_service.py's
fixtures; `tgame`'s characters and the real countries) and a database of the
test's own. Nothing here needs a window.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from loguru import logger

from src.core import PlayerDirectory
from src.core import StateSchema as schema
from src.core.CountryCatalog import CountryCatalog
from src.core.PlayerDB import PlayerDB
from src.core.PlayerService import (
    CharacterSlot, NoteForDB, NoteFromDB, PlayerService, PreparedSave)
from src.core.StateManager import StateManager as SM
from src.core.StreamService import NO_SEED, StreamService
# Fixtures: an isolated state, and the recording's catalogs.
from tests.test_player_service import (  # noqa: F401
    EDITOR_PATH, _isolated_state, recordedCatalogs, recordingDir)
from tests.fixtures.player_slots import recording

GAME = "tgame"
OTHER_GAME = "game2"
OTHER_MAINS = [["Their Main", 2, "Their Variant"]]


@pytest.fixture
def world(recordedCatalogs, tmp_path, monkeypatch):  # noqa: F811
    """`tgame` loaded, an empty database under PlayerDB.instance, and `save` /
    `registered` / `entrants` over the recording's catalogs."""
    games, countries = recordedCatalogs
    codename, modsActive, reload = recording.GAME_SCENARIOS[GAME]
    games.SelectGame(codename, modsActive=modsActive, reload=reload)
    db = _database(tmp_path / "local_players.json")
    monkeypatch.setattr(PlayerDB, "instance", db)
    return SimpleNamespace(
        db=db, games=games, countries=countries,
        save=lambda fields: PlayerDirectory.Save(
            _naming(fields), games=games, countries=countries),
        registered=lambda: PlayerDirectory.Registered(
            games=games, countries=countries)["players"],
        entrants=lambda: PlayerDirectory.Entrants(
            games=games, countries=countries)["entrants"])


def _naming(fields):
    """Mains name the game they are of, as a page sends them: the loaded one,
    unless a test says otherwise."""
    return {"game": GAME, **fields} if "mains" in fields else fields


def _database(path):
    path = Path(path)
    return PlayerDB(path=str(path),
                    legacyCsvPath=str(path.with_suffix(".csv")))


def _entrants(monkeypatch, *entrants):
    monkeypatch.setattr(StreamService, "instance", SimpleNamespace(
        TopEntrants=lambda: [dict(entrant) for entrant in entrants]))


def _row(rows, key):
    return next(row for row in rows if row["key"] == key)


# ── the rows ─────────────────────────────────────────────────────────────

def test_the_registered_rows_are_in_key_order_with_the_loaded_games_mains(
        world):
    world.db.database.update({
        "zeta": {"gamerTag": "zeta"},
        "SP Alpha": {
            "prefix": "SP", "gamerTag": "Alpha", "name": "Real",
            "twitter": "@alpha", "country_code": "JP", "state_code": "13",
            "custom_textbox": "note",
            "mains": {GAME: [["Mario", 1, "EX"], ["Bowser", 0, ""]],
                      OTHER_GAME: OTHER_MAINS}},
        "Beta": {"gamerTag": "Beta"},
    })

    rows = world.registered()

    assert [row["key"] for row in rows] == ["Beta", "SP Alpha", "zeta"]
    assert rows[1] == {
        "key": "SP Alpha", "prefix": "SP", "gamerTag": "Alpha",
        "name": "Real", "twitter": "@alpha", "country_code": "JP",
        "state_code": "13", "custom_textbox": "note",
        "mains": [["Mario", 1, "EX"], ["Bowser", 0, ""]]}
    # A field the record lacks is empty, of the same type as any other.
    assert rows[0] == {
        "key": "Beta", "prefix": "", "gamerTag": "Beta", "name": "",
        "twitter": "", "country_code": "", "state_code": "",
        "custom_textbox": "", "mains": []}


def test_a_row_is_a_copy_of_the_stored_record(world):
    world.db.database["A"] = {
        "gamerTag": "A", "mains": {GAME: [["Mario", 1, "EX"]]}}

    world.registered()[0]["mains"][0][0] = "Changed"

    assert world.db.database["A"]["mains"][GAME][0][0] == "Mario"


@pytest.mark.parametrize("mains, expected", [
    # A JSON string: an old row's. Whichever game it holds.
    (json.dumps({GAME: [["Mario", 1, "EX"]]}), [["Mario", 1, "EX"]]),
    (json.dumps({OTHER_GAME: OTHER_MAINS}), []),
    ("{}", []),
    # What is not the per-game dict at all.
    ("not json", []), ("[1, 2]", []), (["Mario", 1], []), (5, []), (None, []),
    # A game whose value is not a list.
    ({GAME: "Mario"}, []), ({GAME: {"Mario": 1}}, []), ({GAME: None}, []),
    # Entries that name no character are left out, the rest kept.
    ({GAME: [[], 5, "Mario", None, [""], [None, 1], [5, 1],
             ["Mario", 1, "EX"]]}, [["Mario", 1, "EX"]]),
    # A name alone, and what is no skin or no variant.
    ({GAME: [["Mario"]]}, [["Mario", 0, ""]]),
    ({GAME: [["Mario", "x", 5]]}, [["Mario", 0, ""]]),
    ({GAME: [["Mario", None, None]]}, [["Mario", 0, ""]]),
    ({GAME: [["Mario", float("inf")]]}, [["Mario", 0, ""]]),
    ({GAME: [["Mario", {"a": 1}]]}, [["Mario", 0, ""]]),
    # The -1 of a character with no skins (what the card publishes), and of
    # one that has them now: as stored. Nothing lower, which Save does not take.
    ({GAME: [["NoArt", -1, ""]]}, [["NoArt", -1, ""]]),
    ({GAME: [["Mario", -1, ""]]}, [["Mario", -1, ""]]),
    ({GAME: [["Mario", -5, ""]]}, [["Mario", -1, ""]]),
    # A skin as a string: the number it is (PlayerService._SkinNumber).
    ({GAME: [["Mario", "2", ""]]}, [["Mario", 2, ""]]),
    # A character the game does not have now still names itself.
    ({GAME: [["Gone", 9, ""]]}, [["Gone", 9, ""]]),
])
def test_the_mains_are_read_defensively(world, mains, expected):
    world.db.database["A"] = {"gamerTag": "A", "mains": mains}

    assert world.registered()[0]["mains"] == expected


def test_no_game_loaded_gives_no_mains(world):
    world.db.database["A"] = {"gamerTag": "A",
                              "mains": {GAME: [["Mario", 1, "EX"]]}}
    world.games.ForgetSelection()

    assert world.registered()[0]["mains"] == []


def test_the_reads_name_the_game_their_mains_are_of(world, monkeypatch):
    """What PUT /players takes back, read once with the mains. The state is no
    substitute: ForgetSelection leaves the last game's codename there."""
    world.db.database["A"] = {"gamerTag": "A",
                              "mains": {GAME: [["Mario", 1, "EX"]]}}
    _entrants(monkeypatch, {"seed": 1, "gamerTag": "A"})

    def read():
        return (
            PlayerDirectory.Registered(games=world.games,
                                       countries=world.countries),
            PlayerDirectory.Entrants(games=world.games,
                                     countries=world.countries))

    registered, entrants = read()
    assert registered["game"] == entrants["game"] == GAME
    assert registered["players"][0]["mains"] == [["Mario", 1, "EX"]]
    assert entrants["entrants"][0]["mains"] == [["Mario", 1, "EX"]]

    world.games.ForgetSelection()
    registered, entrants = read()
    assert registered["game"] is None and entrants["game"] is None

    # With no tournament too.
    _entrants(monkeypatch)
    assert PlayerDirectory.Entrants(
        games=world.games, countries=world.countries) == {
            "game": None, "entrants": []}


def test_rows_that_cannot_be_shown_are_left_out_and_the_rest_shown(world):
    """A row with no tag loads under the key None (PlayerDB._Load). The Qt
    list sorts on key.lower(), so it loses every row to one such row."""
    world.db.database.update({
        None: {"mains": "{}"},
        5: {"gamerTag": 5},
        "Broken": "not a record",
        "Fine": {"gamerTag": "Fine", "name": None, "twitter": 5},
    })

    rows = world.registered()

    assert [row["key"] for row in rows] == ["Fine"]
    assert rows[0]["name"] == "" and rows[0]["twitter"] == "5"


@pytest.mark.parametrize("stored, typed", [
    ("line 1\\nline 2", "line 1\nline 2"),
    ("no breaks", "no breaks"),
])
def test_a_note_is_given_with_real_line_breaks(world, stored, typed):
    world.db.database["A"] = {"gamerTag": "A", "custom_textbox": stored}

    assert world.registered()[0]["custom_textbox"] == typed


def test_the_note_helpers_keep_the_lines_but_are_no_exact_inverses():
    assert NoteForDB("a\nb") == "a\\nb"
    assert NoteFromDB("a\\nb") == "a\nb"
    assert NoteFromDB(NoteForDB("a\n\nb")) == "a\n\nb"
    # What does not come back as it went in.
    assert NoteFromDB(NoteForDB("a\n")) == "a"
    assert NoteFromDB(NoteForDB("a\x0bb")) == "a\nb"
    assert NoteFromDB(NoteForDB("typed \\n")) == "typed \n"
    # The breaks a note box turned into "\n" on the way to the state.
    assert NoteFromDB("a\r\nb\rc d") == "a\nb\nc\nd"


# ── a row's location is what Save takes ──────────────────────────────────

CUSTOM_FLAG = "Custom Flag"


@pytest.mark.parametrize("country, state, expected", [
    ("JP", "13", ("JP", "13")),
    # A free-form location, which the database holds (StartGGDataProvider:
    # `resolved or state_raw`): resolved as the editor resolves it.
    ("JP", "Shibuya-ku, Tokyo", ("JP", "13")),
    ("JP", "Nowhere at all", ("JP", "")),
    ("JP", "", ("JP", "")),
    # A region of no country, and a country the catalog does not know.
    ("", "13", ("", "")),
    ("ZZ", "13", ("", "")),
    # A custom flag has no regions.
    (CUSTOM_FLAG, "", (CUSTOM_FLAG, "")),
    (CUSTOM_FLAG, "13", (CUSTOM_FLAG, "")),
    # The file can hold anything.
    (5, "13", ("", "")),
    ("JP", 13, ("JP", "")),
    (None, None, ("", "")),
])
def test_a_rows_location_is_one_save_takes(world, country, state, expected):
    world.db.database["A"] = {"gamerTag": "A", "country_code": country,
                              "state_code": state}

    row = world.registered()[0]

    assert (row["country_code"], row["state_code"]) == expected
    # Sent back as it is: saved, never refused.
    fields = {key: value for key, value in row.items() if key != "key"}
    assert world.save(fields) == {"key": "A", "created": False}


def test_until_the_countries_are_loaded_a_row_gives_what_is_stored(world):
    """Nothing can be told apart yet, and a blank saved back would lose the
    player's country (PUT /players answers NOT_READY for these then)."""
    world.db.database["A"] = {"gamerTag": "A", "country_code": "JP",
                              "state_code": "Tokyo"}
    rows = PlayerDirectory.Registered(
        games=world.games, countries=CountryCatalog())["players"]

    assert rows[0]["country_code"] == "JP"
    assert rows[0]["state_code"] == "Tokyo"


def test_a_real_catalog_row_round_trips_through_the_record(world):
    assert world.save({
        "prefix": "SP", "gamerTag": "Tag", "name": "Real", "twitter": "@t",
        "custom_textbox": "a\nb", "country_code": "JP", "state_code": "13",
        "mains": [["Mario", 1, "EX"], ["Zelda", 0, ""]],
    }) == {"key": "SP Tag", "created": True}

    row = world.registered()[0]

    assert row.pop("key") == "SP Tag"
    assert world.save(row) == {"key": "SP Tag", "created": False}
    assert world.registered()[0] == {**row, "key": "SP Tag"}


# ── what a save was not asked to change, it does not change ──────────────

_KEPT_MAINS = [
    # A character the game does not have now (mods off), with a variant it
    # does not have either; one with no skins; one with a skin row it does not
    # have now, and the -1 of a card that had none.
    ["Gone", 9, "Gone Variant"], ["NoArt", -1, ""], ["Mario", 7, ""],
    ["Mario", -1, ""]]


def test_mains_the_player_has_go_back_unchecked(world):
    world.db.database["Tag"] = {"gamerTag": "Tag", "mains": {
        GAME: _KEPT_MAINS, OTHER_GAME: OTHER_MAINS}}
    row = world.registered()[0]
    assert row["mains"] == _KEPT_MAINS

    assert world.save({"gamerTag": "Tag", "mains": row["mains"]}) == {
        "key": "Tag", "created": False}

    assert world.db.database["Tag"]["mains"] == {
        GAME: _KEPT_MAINS, OTHER_GAME: OTHER_MAINS}


def test_only_what_is_new_is_checked(world):
    world.db.database["Tag"] = {"gamerTag": "Tag", "mains": {
        GAME: _KEPT_MAINS}}

    assert world.save({"gamerTag": "Tag", "mains": [
        ["Gone", 9, "Gone Variant"], ["Bowser", 1, "EX"]]})["created"] is False

    assert world.db.database["Tag"]["mains"] == {
        GAME: [["Gone", 9, "Gone Variant"], ["Bowser", 1, "EX"]]}


@pytest.mark.parametrize("pick, refused", [
    # Close to what the player has is not what the player has.
    (["Gone", 8, "Gone Variant"], PlayerDirectory.UNKNOWN_CHARACTER),
    (["Gone", 9, ""], PlayerDirectory.UNKNOWN_CHARACTER),
    (["Mario", 6, ""], PlayerDirectory.UNKNOWN_SKIN),
    (["Mario", -1, "EX"], PlayerDirectory.UNKNOWN_SKIN),
    (["Mario", 7, "EX"], PlayerDirectory.UNKNOWN_SKIN),
])
def test_a_main_that_differs_is_a_new_one(world, pick, refused):
    stored = {"gamerTag": "Tag", "mains": {GAME: _KEPT_MAINS}}
    world.db.database["Tag"] = json.loads(json.dumps(stored))

    assert world.save({"gamerTag": "Tag", "mains": [pick]}) == refused

    assert world.db.database["Tag"] == stored


def test_what_another_game_or_player_has_is_no_excuse(world):
    world.db.database.update({
        "Tag": {"gamerTag": "Tag", "mains": {OTHER_GAME: [["Gone", 9, ""]]}},
        "Other": {"gamerTag": "Other", "mains": {GAME: [["Gone", 9, ""]]}},
    })

    assert world.save({"gamerTag": "Tag", "mains": [["Gone", 9, ""]]}) == \
        PlayerDirectory.UNKNOWN_CHARACTER
    assert world.save({"gamerTag": "New", "mains": [["Gone", 9, ""]]}) == \
        PlayerDirectory.UNKNOWN_CHARACTER


def test_a_main_kept_in_a_json_string_or_an_older_shape_goes_back_too(world):
    """As a row gives it: the record ends up with the shape a card's has."""
    world.db.database.update({
        "Text": {"gamerTag": "Text",
                 "mains": json.dumps({GAME: [["Gone", 9, ""]]})},
        "Short": {"gamerTag": "Short", "mains": {GAME: [["Gone", "2"]]}},
    })
    rows = {row["key"]: row for row in world.registered()}

    for key, expected in (("Text", ["Gone", 9, ""]), ("Short", ["Gone", 2, ""])):
        assert rows[key]["mains"] == [expected]
        world.save({"gamerTag": key, "mains": rows[key]["mains"]})
        assert world.db.database[key]["mains"] == {GAME: [expected]}


def test_a_save_without_mains_changes_no_games_mains(world):
    mains = {GAME: _KEPT_MAINS, OTHER_GAME: OTHER_MAINS}
    world.db.database["Tag"] = {"gamerTag": "Tag",
                                "mains": json.loads(json.dumps(mains))}

    world.save({"gamerTag": "Tag", "name": "New"})

    assert world.db.database["Tag"]["name"] == "New"
    assert world.db.database["Tag"]["mains"] == mains


def test_empty_mains_empty_the_loaded_games(world):
    world.db.database["Tag"] = {"gamerTag": "Tag", "mains": {
        GAME: _KEPT_MAINS, OTHER_GAME: OTHER_MAINS}}

    world.save({"gamerTag": "Tag", "mains": []})

    assert world.db.database["Tag"]["mains"] == {
        GAME: [], OTHER_GAME: OTHER_MAINS}


@pytest.mark.parametrize("country, state", [
    ("JP", "13"),
    # What no catalog vouches for: a free-form region, a country whose flag is
    # gone, what the file holds.
    ("JP", "Shibuya-ku, Tokyo"),
    ("Gone Flag", "Somewhere"),
    ("ZZ", ""),
    (5, 13),
])
def test_a_location_that_is_not_sent_stays_as_stored(world, country, state):
    world.db.database["Tag"] = {"gamerTag": "Tag", "country_code": country,
                                "state_code": state}

    assert world.save({"gamerTag": "Tag", "name": "New"})["created"] is False

    stored = world.db.database["Tag"]
    assert stored["name"] == "New"
    assert stored.get("country_code", "") == country
    assert stored.get("state_code", "") == state


def test_a_location_that_is_not_sent_needs_no_country_data(world):
    world.db.database["Tag"] = {"gamerTag": "Tag", "country_code": "JP",
                                "state_code": "Tokyo"}

    assert PlayerDirectory.Save({"gamerTag": "Tag"}, games=world.games,
                                countries=CountryCatalog()) == {
        "key": "Tag", "created": False}

    assert world.db.database["Tag"]["country_code"] == "JP"
    assert world.db.database["Tag"]["state_code"] == "Tokyo"


@pytest.mark.parametrize("blank", ["", None])
def test_a_blank_location_clears_it(world, blank):
    world.db.database["Tag"] = {"gamerTag": "Tag", "country_code": "JP",
                                "state_code": "13"}

    world.save({"gamerTag": "Tag", "state_code": blank})
    assert world.db.database["Tag"]["country_code"] == "JP"
    assert "state_code" not in world.db.database["Tag"]

    world.save({"gamerTag": "Tag", "country_code": blank})
    assert "country_code" not in world.db.database["Tag"]


@pytest.mark.parametrize("sent, country, state", [
    # The same country: the region is not touched. A free-form one included.
    ({"country_code": "JP"}, "JP", "Shibuya-ku, Tokyo"),
    # Another one, or none: a stored region is another country's now.
    ({"country_code": "US"}, "US", None),
    ({"country_code": ""}, None, None),
    # A new region for it.
    ({"country_code": "US", "state_code": "CA"}, "US", "CA"),
    ({"country_code": "US", "state_code": ""}, "US", None),
    # A region alone: the stored country's.
    ({"state_code": "13"}, "JP", "13"),
])
def test_a_location_sent_is_read_with_the_stored_one(world, sent, country, state):
    world.db.database["Tag"] = {"gamerTag": "Tag", "country_code": "JP",
                                "state_code": "Shibuya-ku, Tokyo"}

    assert world.save({"gamerTag": "Tag", **sent})["created"] is False

    stored = world.db.database["Tag"]
    assert stored.get("country_code") == country
    assert stored.get("state_code") == state


def test_a_stored_region_with_no_country_stays_when_no_country_is_sent(world):
    world.db.database["Tag"] = {"gamerTag": "Tag", "state_code": "Somewhere"}

    world.save({"gamerTag": "Tag", "country_code": ""})

    assert world.db.database["Tag"]["state_code"] == "Somewhere"


@pytest.mark.parametrize("country", ["US", "ZZ", 5, None])
def test_a_region_sent_alone_is_checked_against_the_stored_country(
        world, country):
    world.db.database["Tag"] = {"gamerTag": "Tag", "country_code": country}

    assert world.save({"gamerTag": "Tag", "state_code": "13"}) == \
        PlayerDirectory.UNKNOWN_STATE

    assert "state_code" not in world.db.database["Tag"]


def test_a_changed_tag_inherits_nothing(world):
    """Another record, as it always was: the page sends what it wants on it."""
    world.db.database["Old"] = {"gamerTag": "Old", "country_code": "JP",
                                "state_code": "13",
                                "mains": {GAME: [["Mario", 1, ""]]}}

    world.save({"gamerTag": "New"})

    assert world.db.database["New"] == {
        "prefix": "", "gamerTag": "New", "name": "", "twitter": "",
        "custom_textbox": "", "mains": {}}
    assert world.db.database["Old"]["country_code"] == "JP"


# ── the entrants ─────────────────────────────────────────────────────────

def test_the_entrants_are_the_services_order_with_the_databases_record(
        world, monkeypatch):
    world.db.database.update({
        "SP Alpha": {"prefix": "SP", "gamerTag": "Alpha", "name": "Real",
                     "country_code": "JP",
                     "mains": {GAME: [["Mario", 1, "EX"]]}},
    })
    _entrants(monkeypatch,
              {"seed": 1, "gamerTag": "Newcomer"},
              {"seed": 2, "gamerTag": "alpha"},
              {"seed": NO_SEED, "gamerTag": "Unseeded"},
              {"seed": NO_SEED, "gamerTag": ""})

    rows = world.entrants()

    assert [row["seed"] for row in rows] == [1, 2, None, None]
    assert rows[0] == {
        "seed": 1, "key": None, "prefix": "", "gamerTag": "Newcomer",
        "name": "", "twitter": "", "country_code": "", "state_code": "",
        "custom_textbox": "", "mains": []}
    # Matched as GetPlayerFromTag matches: the record, sponsor and all.
    assert rows[1] == {
        "seed": 2, "key": "SP Alpha", "prefix": "SP", "gamerTag": "Alpha",
        "name": "Real", "twitter": "", "country_code": "JP", "state_code": "",
        "custom_textbox": "", "mains": [["Mario", 1, "EX"]]}
    assert rows[2]["key"] is None and rows[2]["gamerTag"] == "Unseeded"
    assert rows[3]["key"] is None and rows[3]["gamerTag"] == ""


def test_an_entrant_is_matched_as_get_player_from_tag_matches(
        world, monkeypatch):
    """One index for the read rather than GetPlayerFromTag per entrant (a
    full scan of the database, under its lock): the same answers."""
    world.db.database.update({
        "NoTag": {"gamerTag": None},
        "A First": {"prefix": "A", "gamerTag": "Same"},
        "B Second": {"prefix": "B", "gamerTag": "same"},
        "Other": {"gamerTag": "Other"},
        "Ünï": {"gamerTag": "Ünï"},
    })
    tags = ["Same", "SAME", "same", "other", "ÜNÏ", "Nobody"]
    _entrants(monkeypatch, *[{"seed": index + 1, "gamerTag": tag}
                             for index, tag in enumerate(tags)])

    rows = world.entrants()

    for tag, row in zip(tags, rows):
        found = world.db.GetPlayerFromTag(tag)
        if found is None:
            assert row["key"] is None, tag
        else:
            assert world.db.database[row["key"]] is found, tag
    assert rows[1]["key"] == "A First"


def test_no_tournament_gives_no_entrants(world, monkeypatch):
    _entrants(monkeypatch)
    assert world.entrants() == []

    monkeypatch.setattr(StreamService, "instance", None)
    assert world.entrants() == []


def test_a_row_with_no_tag_does_not_stop_the_lookup(world):
    """GetPlayerFromTag used to raise on a row whose gamerTag is None, which
    the file can hold -- for every tag after that row, :load included."""
    world.db.database.update({
        None: {"mains": "{}"},
        "Number": {"gamerTag": 5},
        "Broken": "not a record",
        "Nothing": None,
        "Fine": {"gamerTag": "Fine"},
    })

    assert world.db.GetPlayerFromTag("fine")["gamerTag"] == "Fine"
    assert world.db.GetPlayerFromTag("nobody") is None


# ── the record is the card's (golden) ────────────────────────────────────
#
# One input through both: a card's writes and PrepareSaveToDB, and Save.

_GOLDEN = {
    "everything": {
        "prefix": "SP", "gamerTag": "Tag", "name": "Real Name",
        "twitter": "@tag", "custom_textbox": "line 1\nline 2",
        "country_code": "JP", "state_code": "13",
        "mains": [["Mario", 1, "EX"], ["Bowser", 0, ""], ["Zelda", 0, "Alt"]]},
    "a tag alone": {"gamerTag": "Tag"},
    "no sponsor, one main": {
        "gamerTag": "Tag", "name": "Real", "mains": [["Luigi", 2, ""]]},
    "a country with no region": {"gamerTag": "Tag", "country_code": "US"},
    "a custom flag": {"gamerTag": "Tag", "country_code": CUSTOM_FLAG},
    "a character with no skins": {"gamerTag": "Tag", "mains": [["NoArt", 0, ""]]},
    "a note of breaks and blanks": {
        "gamerTag": "Tag", "custom_textbox": "a\n\n  b  \n"},
    "characters past the sponsor": {
        "prefix": "長い スポンサー", "gamerTag": "タグ", "twitter": " spaced ",
        "mains": [["Peach", 2, "Alt"], ["Random", 0, "None"]]},
}


def _throughTheCard(games, countries, fields):
    """The record a card's edits make: each pick written as the card's
    handlers write it, then the slot read back. What SavePlayerToDB does."""
    service = PlayerService(EDITOR_PATH, games=games, countries=countries)
    service.SeedEmpty(3)
    with SM.SaveBlock():
        for field, key in ((schema.PLAYER_SPONSOR_KEY, "prefix"),
                           (schema.PLAYER_TAG_KEY, "gamerTag"),
                           (schema.PLAYER_REAL_NAME_KEY, "name"),
                           (schema.PLAYER_TWITTER_KEY, "twitter"),
                           (schema.PLAYER_NOTE_KEY, "custom_textbox")):
            service.SetText(field, fields.get(key, ""))
        country = fields.get("country_code")
        service.SetCountry(country)
        service.SetRegion(country, fields.get("state_code"))
        snap = games.Current()
        slots = []
        for name, skin, variant in fields.get("mains", []):
            # A card's empty skin combo has no row: -1.
            row = skin if snap.skinRows.get(name) else -1
            slots.append(CharacterSlot(name, row, variant or None, ""))
        slots += [CharacterSlot()] * (3 - len(slots))
        service.PublishCharacterSlots(slots)
    return service


@pytest.mark.parametrize("label", list(_GOLDEN))
@pytest.mark.parametrize("existing", [
    None,
    {"prefix": "SP", "gamerTag": "Tag", "name": "Old", "country_code": "FR",
     "state_code": "75", "custom_textbox": "old",
     "mains": {OTHER_GAME: OTHER_MAINS, GAME: [["Peach", 1, ""]]}},
    {"prefix": "SP", "gamerTag": "Tag", "name": "Old",
     "mains": json.dumps({OTHER_GAME: OTHER_MAINS, GAME: [["Peach", 1, ""]]})},
], ids=["a new record", "over a stored record", "over a JSON string's mains"])
def test_save_makes_the_record_the_card_makes(
        world, tmp_path, monkeypatch, label, existing):
    """A card saves everything it shows, so here everything is sent: what a
    save leaves out is the section above's."""
    fields = _GOLDEN[label]

    def stored(db):
        if existing is not None:
            existing_ = json.loads(json.dumps(existing))
            existing_["gamerTag"] = fields["gamerTag"]
            existing_["prefix"] = fields.get("prefix", "")
            db.database[PlayerDB.PlayerTag(existing_)] = existing_
        return db

    card = stored(_database(tmp_path / "card.json"))
    monkeypatch.setattr(PlayerDB, "instance", card)
    _throughTheCard(world.games, world.countries, fields).SavePlayerToDB()

    direct = stored(_database(tmp_path / "direct.json"))
    monkeypatch.setattr(PlayerDB, "instance", direct)
    answer = world.save(
        {"country_code": "", "state_code": "", "mains": [], **fields})

    assert answer["created"] == (existing is None)
    assert list(card.database) == list(direct.database) == [answer["key"]]
    # json.dumps keeps the order the keys were made in.
    assert json.dumps(direct.database, ensure_ascii=False) == \
        json.dumps(card.database, ensure_ascii=False)
    assert Path(direct.path).read_bytes() == Path(card.path).read_bytes()


def test_the_golden_runs_are_real_records(world, tmp_path, monkeypatch):
    """The comparison above means something: what the card's path saved."""
    db = _database(tmp_path / "card.json")
    monkeypatch.setattr(PlayerDB, "instance", db)
    _throughTheCard(world.games, world.countries,
                    _GOLDEN["everything"]).SavePlayerToDB()

    assert db.database["SP Tag"] == {
        "prefix": "SP", "gamerTag": "Tag", "name": "Real Name",
        "twitter": "@tag", "custom_textbox": "line 1\\nline 2",
        "mains": {GAME: [["Mario", 1, "EX"], ["Bowser", 0, ""],
                         ["Zelda", 0, "Alt"]]},
        "country_code": "JP", "state_code": "13"}


def test_a_character_with_no_skins_stores_what_its_card_publishes(world):
    world.save({"gamerTag": "Tag", "mains": [["NoArt", 7]]})

    assert world.db.database["Tag"]["mains"] == {GAME: [["NoArt", -1, ""]]}


# ── what Save keeps, and what it writes ──────────────────────────────────

def test_the_other_games_mains_stay(world):
    world.db.database["Tag"] = {
        "gamerTag": "Tag",
        "mains": {OTHER_GAME: OTHER_MAINS, GAME: [["Peach", 1, ""]]}}

    assert world.save({"gamerTag": "Tag", "mains": [["Mario", 0, ""]]}) == {
        "key": "Tag", "created": False}

    assert world.db.database["Tag"]["mains"] == {
        OTHER_GAME: OTHER_MAINS, GAME: [["Mario", 0, ""]]}


@pytest.mark.parametrize("through", ["a save here", "a card's save"])
def test_the_other_games_mains_stay_in_a_stored_json_string(world, through):
    """AddPlayers' merge cannot update a string (it logs and replaces them;
    pinned as it is), so SaveRecordToDB, which both go through, parses them
    first. A card's save used to lose them."""
    world.db.database["Tag"] = {
        "gamerTag": "Tag", "mains": json.dumps({OTHER_GAME: OTHER_MAINS})}
    fields = {"gamerTag": "Tag", "mains": [["Mario", 0, ""]]}
    errors = []
    sink = logger.add(lambda message: errors.append(str(message)),
                      level="ERROR")
    try:
        if through == "a save here":
            world.save(fields)
        else:
            _throughTheCard(world.games, world.countries,
                            fields).SavePlayerToDB()
    finally:
        logger.remove(sink)

    assert errors == []
    assert world.db.database["Tag"]["mains"] == {
        OTHER_GAME: OTHER_MAINS, GAME: [["Mario", 0, ""]]}


@pytest.mark.parametrize("mains", ["not json", "[1]", 5, ["Mario", 1], None])
def test_stored_mains_that_are_no_dict_do_not_stop_a_save(world, mains):
    world.db.database["Tag"] = {"gamerTag": "Tag", "mains": mains}

    world.save({"gamerTag": "Tag", "mains": [["Mario", 0, ""]]})

    assert world.db.database["Tag"]["mains"] == {GAME: [["Mario", 0, ""]]}


def test_a_row_loaded_without_mains_saves_cleanly(world, tmp_path):
    """A load gives a row without `mains` the string "{}": the usual case of
    the string above, from a file an older build wrote."""
    db = world.db
    Path(db.path).write_text(
        json.dumps([{"gamerTag": "Old", "name": "Real"}]), encoding="utf-8")
    db.LoadDB()
    assert db.database["Old"]["mains"] == "{}"

    assert world.save({"gamerTag": "Old", "mains": [["Mario", 0, ""]]}) == {
        "key": "Old", "created": False}

    assert db.database["Old"]["mains"] == {GAME: [["Mario", 0, ""]]}


def test_with_no_game_loaded_every_games_mains_stay(world):
    """The slot's path has no `mains` here, and AddPlayers takes that as
    replacing them all: an edit of a name would lose them."""
    world.db.database["Tag"] = {
        "gamerTag": "Tag", "name": "Old",
        "mains": {GAME: [["Mario", 1, ""]], OTHER_GAME: OTHER_MAINS}}
    world.games.ForgetSelection()

    world.save({"gamerTag": "Tag", "name": "New"})
    world.save({"gamerTag": "Tag", "twitter": "@new", "name": "New",
                "mains": [], "game": None})
    world.save({"gamerTag": "Fresh"})

    assert world.db.database["Tag"]["name"] == "New"
    assert world.db.database["Tag"]["mains"] == {
        GAME: [["Mario", 1, ""]], OTHER_GAME: OTHER_MAINS}
    assert world.db.database["Fresh"]["mains"] == {}


def test_a_blank_text_is_blank_afterwards(world):
    """The texts are the record's: what is not sent is not kept, and the
    memory-only keys of a fetch go with it, as they do from the card."""
    world.save({"prefix": "", "gamerTag": "Tag", "name": "Real",
                "twitter": "@t", "custom_textbox": "note"})
    world.db.database["Tag"]["seed"] = 5

    world.save({"gamerTag": "Tag"})

    assert world.db.database["Tag"] == {
        "prefix": "", "gamerTag": "Tag", "name": "", "twitter": "",
        "custom_textbox": "", "mains": {}}


def test_a_changed_tag_is_another_player(world):
    world.save({"gamerTag": "Old"})

    assert world.save({"gamerTag": "New"}) == {"key": "New", "created": True}
    assert world.save({"prefix": "SP", "gamerTag": "New"}) == {
        "key": "SP New", "created": True}

    assert sorted(world.db.database) == ["New", "Old", "SP New"]


def test_a_save_writes_no_state(world, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("Save wrote the state")

    monkeypatch.setattr(SM, "Set", refuse)
    monkeypatch.setattr(SM, "Unset", refuse)

    assert world.save({"gamerTag": "Tag", "country_code": "JP",
                       "mains": [["Mario", 1, "EX"]]})["created"] is True


def test_a_save_is_written_and_announced_once(world):
    announced = []
    world.db.changed.subscribe(lambda: announced.append(True))

    world.save({"gamerTag": "Tag"})

    assert announced == [True]
    assert [row["gamerTag"] for row in
            json.loads(Path(world.db.path).read_text(encoding="utf-8"))] == [
        "Tag"]


@pytest.mark.parametrize("fields, refused", [
    ({}, PlayerDirectory.NO_TAG),
    ({"gamerTag": ""}, PlayerDirectory.NO_TAG),
    ({"gamerTag": "   "}, PlayerDirectory.NO_TAG),
    ({"gamerTag": None, "prefix": "SP"}, PlayerDirectory.NO_TAG),
    ({"gamerTag": "T", "country_code": "ZZ"}, PlayerDirectory.UNKNOWN_COUNTRY),
    ({"gamerTag": "T", "country_code": "JP", "state_code": "Tokyo"},
     PlayerDirectory.UNKNOWN_STATE),
    ({"gamerTag": "T", "state_code": "13"}, PlayerDirectory.UNKNOWN_STATE),
    ({"gamerTag": "T", "country_code": CUSTOM_FLAG, "state_code": "13"},
     PlayerDirectory.UNKNOWN_STATE),
    ({"gamerTag": "T", "mains": [["Gone", 0]]},
     PlayerDirectory.UNKNOWN_CHARACTER),
    ({"gamerTag": "T", "mains": [["Mario", 3]]},
     PlayerDirectory.UNKNOWN_SKIN),
    ({"gamerTag": "T", "mains": [["Mario", -1]]},
     PlayerDirectory.UNKNOWN_SKIN),
    ({"gamerTag": "T", "mains": [["Mario", 0, "Gone"]]},
     PlayerDirectory.UNKNOWN_VARIANT),
    # One bad entry refuses the whole save.
    ({"gamerTag": "T", "mains": [["Mario", 0], ["Gone", 0]]},
     PlayerDirectory.UNKNOWN_CHARACTER),
    ({"gamerTag": "T", "mains": [["Mario", 0]], "game": OTHER_GAME},
     PlayerDirectory.OTHER_GAME),
    # Empty mains are the loaded game's too: they empty them.
    ({"gamerTag": "T", "mains": [], "game": OTHER_GAME},
     PlayerDirectory.OTHER_GAME),
    ({"gamerTag": "T", "mains": [], "game": None},
     PlayerDirectory.OTHER_GAME),
])
def test_what_save_refuses_saves_nothing(world, fields, refused):
    announced = []
    world.db.changed.subscribe(lambda: announced.append(True))

    assert world.save(fields) == refused

    assert world.db.database == {}
    assert announced == []
    assert not Path(world.db.path).exists()


def test_the_loaded_game_named_as_the_mains_game_is_fine(world):
    assert world.save({"gamerTag": "T", "mains": [["Mario", 0]],
                       "game": GAME})["created"] is True


def test_mains_that_name_no_game_are_another_games(world):
    assert PlayerDirectory.Save(
        {"gamerTag": "T", "mains": []}, games=world.games,
        countries=world.countries) == PlayerDirectory.OTHER_GAME
    assert world.db.database == {}


def test_a_game_without_mains_means_nothing(world):
    """It names what the mains are of, and none are sent."""
    assert world.save({"gamerTag": "T", "game": OTHER_GAME})["created"] is True


def test_mains_with_no_game_loaded_are_refused(world):
    """Nothing to store them under, and the tables of the last game stay
    after ForgetSelection: quietly dropping the picks would pass as saved."""
    world.games.ForgetSelection()

    assert world.save({"gamerTag": "T", "mains": [["Mario", 0]],
                       "game": None}) == PlayerDirectory.OTHER_GAME
    # The codename the state still has is not the loaded game's.
    assert world.save({"gamerTag": "T", "mains": [["Mario", 0]],
                       "game": GAME}) == PlayerDirectory.OTHER_GAME
    assert world.save({"gamerTag": "T", "mains": [], "game": GAME}) == \
        PlayerDirectory.OTHER_GAME
    assert world.db.database == {}

    # No mains, as the read gives them then: nothing to store, and fine.
    assert world.save({"gamerTag": "T", "mains": [],
                       "game": None})["created"] is True


# ── the order of the saves is one with the card's ────────────────────────

def test_a_card_save_read_before_a_save_here_is_dropped(world):
    """Two writers, one order (PlayerService.SaveRecordToDB): the card's
    record was read first, the request's saved first."""
    service = _throughTheCard(world.games, world.countries,
                              {"gamerTag": "Tag", "name": "From the card"})
    prepared = service.PrepareSaveToDB()

    world.save({"gamerTag": "Tag", "name": "From the request"})

    assert PlayerService.SaveRecordToDB(prepared) is False
    assert world.db.database["Tag"]["name"] == "From the request"


def test_a_save_here_read_before_a_card_save_loses_to_it(world):
    service = _throughTheCard(world.games, world.countries,
                              {"gamerTag": "Tag", "name": "From the card"})
    older = PreparedSave(
        {"gamerTag": "Tag", "name": "Older", "mains": {}},
        PlayerService.NextSaveSequence())
    service.SavePlayerToDB()

    assert PlayerService.SaveRecordToDB(older) is False
    assert world.db.database["Tag"]["name"] == "From the card"


def test_the_numbers_of_both_writers_come_from_one_counter(world):
    service = _throughTheCard(world.games, world.countries, {"gamerTag": "T"})

    first = PlayerService.NextSaveSequence()
    second = service.PrepareSaveToDB().sequence
    third = PlayerService.NextSaveSequence()

    assert first < second < third


def test_a_save_that_lost_to_a_newer_one_says_so(world, monkeypatch):
    """The newer one has a number taken after this one's. Saying nothing would
    tell the page its edit is in the database."""
    world.db.database["Tag"] = {"gamerTag": "Tag", "name": "Stored"}
    monkeypatch.setitem(PlayerService._savedSequence, "Tag",
                        PlayerService.NextSaveSequence() + 10)
    announced = []
    world.db.changed.subscribe(lambda: announced.append(True))

    assert world.save({"gamerTag": "Tag", "name": "Late"}) == \
        PlayerDirectory.SUPERSEDED

    assert world.db.database["Tag"]["name"] == "Stored"
    assert announced == []


def test_a_save_is_numbered_before_what_it_keeps_is_read(world, monkeypatch):
    """A card's save landing in between has the later number: this one, read
    before it, loses to it rather than write what it read over it."""
    world.db.database["Tag"] = {"gamerTag": "Tag", "country_code": "JP"}
    order = []
    number = PlayerService.NextSaveSequence
    get = world.db.Get
    monkeypatch.setattr(PlayerService, "NextSaveSequence", staticmethod(
        lambda: order.append("number") or number()))
    monkeypatch.setattr(world.db, "Get", lambda *args, **kwargs:
                        order.append("read") or get(*args, **kwargs))

    world.save({"gamerTag": "Tag"})

    assert order[:2] == ["number", "read"]


def test_a_dropped_save_logs_no_name(world):
    world.save({"gamerTag": "Secret Tag"})
    older = PreparedSave({"gamerTag": "Secret Tag"}, 0)
    logged = []
    sink = logger.add(lambda message: logged.append(str(message)),
                      level="DEBUG")
    try:
        assert PlayerService.SaveRecordToDB(older) is False
    finally:
        logger.remove(sink)

    assert any("dropped" in line for line in logged)
    assert not any("Secret Tag" in line for line in logged)


# ── Delete ───────────────────────────────────────────────────────────────

def test_delete_takes_the_key_and_saves(world):
    world.save({"prefix": "SP", "gamerTag": "Tag"})
    world.save({"gamerTag": "Tag"})
    announced = []
    world.db.changed.subscribe(lambda: announced.append(True))

    assert PlayerDirectory.Delete("SP Tag") is True

    assert list(world.db.database) == ["Tag"]
    assert announced == [True]
    assert [row["gamerTag"] for row in
            json.loads(Path(world.db.path).read_text(encoding="utf-8"))] == [
        "Tag"]


def test_delete_of_a_key_the_database_does_not_have_does_nothing(world):
    world.save({"prefix": "SP", "gamerTag": "Tag"})
    announced = []
    world.db.changed.subscribe(lambda: announced.append(True))
    written = Path(world.db.path).stat().st_mtime_ns

    # The tag alone is not the key.
    assert PlayerDirectory.Delete("Tag") is False

    assert list(world.db.database) == ["SP Tag"]
    assert announced == []
    assert Path(world.db.path).stat().st_mtime_ns == written
