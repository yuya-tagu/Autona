"""The player list's /api/v1 routes (ADR-005 S4m).

What the React player list (S4n) reads and writes through, added to /api/v1:

- ``GET  /api/v1/players``           every player of the local database
- ``GET  /api/v1/players/entrants``  the tournament's, with the database's record
- ``PUT  /api/v1/players``           save one (``key``, ``created``)
- ``POST /api/v1/players:delete``    ``{"key", "confirm": true}``
- ``POST /api/v1/scoreboards/{1,2}/teams/{t}/players/{p}:load``  ``{"tag"}``

Through Flask's test client against the real route table, on a database of
the test's own (the harness's file is process-global) with the player slot
recording's game and the real countries: the contract (envelopes, 400 / 404 /
409, nothing saved on a refusal) and the access rules. **The rows have real
names in them**, so the reads are PLAYERDB and not READ, and nothing here
reaches a log. What the rows and the record hold is
tests/test_player_directory.py's.
"""
import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import AutonaWebOperations as WebOps
from src.core import WebAccess
from src.core.CountryCatalog import CountryCatalog, CountrySnapshot
from src.core.DictHelper import deep_clone
from src.core.GameAssetCatalog import GameAssetCatalog
from src.core.PlayerDB import PlayerDB
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from src.core.StreamService import NO_SEED, StreamService
from tests.fixtures.player_slots import recording
from tests.test_api_v1 import _assertRejected, webApp  # noqa: F401
from tests.test_player_service import recordedCatalogs, recordingDir  # noqa: F401

GAME = "tgame"
OTHER_GAME = "game2"
OTHER_MAINS = [["Their Main", 2, ""]]
SECRET_TAG = "Secret Tag Value"
SECRET_NAME = "Secret Real Name"


@pytest.fixture
def stateKept(tmp_path):
    """A game load writes game.*: the shared state is put back afterwards.

    The export writer resolves ./out against the CWD when it writes, and is
    usually still writing when a test this short ends. recordedCatalogs' chdir
    is undone (monkeypatch) before this teardown runs, so the CWD it waits in
    has to be its own -- it used to be the project root, and every test here
    rewrote the real out/game.
    """
    work = tmp_path / "run"
    (work / "out").mkdir(parents=True)
    previousDir = os.getcwd()
    os.chdir(work)
    with SM.lock:
        state = deep_clone(SM.state)
        saved = deep_clone(SM.lastSavedState)
        revision = SM.revision
        changed = list(SM.changedKeys)
    try:
        yield
    finally:
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state = state
            SM.lastSavedState = saved
            SM.revision = revision
            SM.changedKeys = changed
        os.chdir(previousDir)


@pytest.fixture
def db(stateKept, recordedCatalogs, tmp_path, monkeypatch):  # noqa: F811
    """The recording's catalogs as the process's, `tgame` loaded, and an empty
    database under PlayerDB.instance."""
    games, countries = recordedCatalogs
    codename, modsActive, reload = recording.GAME_SCENARIOS[GAME]
    games.SelectGame(codename, modsActive=modsActive, reload=reload)
    monkeypatch.setattr(GameAssetCatalog, "instance", games)
    monkeypatch.setattr(CountryCatalog, "instance", countries)
    database = PlayerDB(path=str(tmp_path / "local_players.json"),
                        legacyCsvPath=str(tmp_path / "local_players.csv"))
    monkeypatch.setattr(PlayerDB, "instance", database)
    return database


@pytest.fixture
def client(webApp, db):  # noqa: F811
    return webApp.test_client()


def _ok(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    return body


def _saved(db):
    if not Path(db.path).exists():
        return None
    return json.loads(Path(db.path).read_text(encoding="utf-8"))


# ── GET /players ─────────────────────────────────────────────────────────

def test_the_read_gives_the_rows(client, db):
    db.database["SP Alpha"] = {
        "prefix": "SP", "gamerTag": "Alpha", "name": "Real",
        "country_code": "JP", "state_code": "13",
        "custom_textbox": "a\\nb",
        "mains": {GAME: [["Mario", 1, "EX"]], OTHER_GAME: OTHER_MAINS}}

    body = _ok(client.get("/api/v1/players"))

    assert "revision" not in body             # the database, not state
    assert body["data"] == {"game": GAME, "players": [{
        "key": "SP Alpha", "prefix": "SP", "gamerTag": "Alpha",
        "name": "Real", "twitter": "", "country_code": "JP",
        "state_code": "13", "custom_textbox": "a\nb",
        "mains": [["Mario", 1, "EX"]]}]}


def test_the_reads_name_null_with_no_game(client, db, monkeypatch):
    GameAssetCatalog.instance.ForgetSelection()
    monkeypatch.setattr(StreamService, "instance", None)

    assert _ok(client.get("/api/v1/players"))["data"] == {
        "game": None, "players": []}
    assert _ok(client.get("/api/v1/players/entrants"))["data"] == {
        "game": None, "entrants": []}


def test_broken_rows_do_not_break_the_read(client, db):
    db.database.update({
        None: {"mains": "{}"},
        "Text": {"gamerTag": "Text",
                 "mains": json.dumps({GAME: [["Mario", 2, "EX"]]})},
        "Broken": {"gamerTag": "Broken", "mains": "{not json"},
        "Entries": {"gamerTag": "Entries", "mains": {GAME: [[], 5, ["Zelda"]]}},
    })

    rows = _ok(client.get("/api/v1/players"))["data"]["players"]

    assert {row["key"]: row["mains"] for row in rows} == {
        "Broken": [], "Entries": [["Zelda", 0, ""]],
        "Text": [["Mario", 2, "EX"]]}


# ── GET /players/entrants ────────────────────────────────────────────────

def test_the_entrants_read_gives_the_seeds_and_the_records(
        client, db, monkeypatch):
    db.database["SP Alpha"] = {"prefix": "SP", "gamerTag": "Alpha",
                               "mains": {GAME: [["Mario", 1, "EX"]]}}
    monkeypatch.setattr(StreamService, "instance", SimpleNamespace(
        TopEntrants=lambda: [{"seed": 1, "gamerTag": "ALPHA"},
                             {"seed": NO_SEED, "gamerTag": "Newcomer"}]))

    body = _ok(client.get("/api/v1/players/entrants"))

    assert "revision" not in body
    assert body["data"]["game"] == GAME
    rows = body["data"]["entrants"]
    assert [(row["seed"], row["key"], row["gamerTag"]) for row in rows] == [
        (1, "SP Alpha", "Alpha"), (None, None, "Newcomer")]
    assert rows[0]["mains"] == [["Mario", 1, "EX"]]
    assert rows[1]["mains"] == []


def test_no_tournament_gives_an_empty_list(client, monkeypatch):
    monkeypatch.setattr(StreamService, "instance", None)

    assert _ok(client.get("/api/v1/players/entrants"))["data"] == {
        "game": GAME, "entrants": []}


def test_an_entrant_row_goes_back_without_its_key_and_seed(
        client, db, monkeypatch):
    """Both are the read's: `key` is what a delete takes, `seed` the
    tournament's. The rest is what a save takes, and the tag alone of one the
    database does not have makes its record."""
    db.database["SP Alpha"] = {
        "prefix": "SP", "gamerTag": "Alpha", "name": "Real",
        "country_code": "JP", "state_code": "Shibuya-ku, Tokyo",
        "mains": {GAME: [["Gone", 9, ""]], OTHER_GAME: OTHER_MAINS}}
    monkeypatch.setattr(StreamService, "instance", SimpleNamespace(
        TopEntrants=lambda: [{"seed": 1, "gamerTag": "ALPHA"},
                             {"seed": 2, "gamerTag": "Newcomer"}]))
    data = _ok(client.get("/api/v1/players/entrants"))["data"]

    for row in data["entrants"]:
        _assertRejected(client.put("/api/v1/players",
                                   json={"game": data["game"], **row}))
        del row["key"], row["seed"]
        # The location only when it changes: a row's can hide a stored one.
        unsent = {name: value for name, value in row.items()
                  if name not in ("country_code", "state_code")}
        _ok(client.put("/api/v1/players",
                       json={"game": data["game"], **unsent}))

    assert db.database["SP Alpha"]["state_code"] == "Shibuya-ku, Tokyo"
    assert db.database["SP Alpha"]["mains"] == {
        GAME: [["Gone", 9, ""]], OTHER_GAME: OTHER_MAINS}
    assert db.database["Newcomer"] == {
        "prefix": "", "gamerTag": "Newcomer", "name": "", "twitter": "",
        "custom_textbox": "", "mains": {GAME: []}}

    # With the location too it is not refused either.
    for row in data["entrants"]:
        _ok(client.put("/api/v1/players", json={"game": data["game"], **row}))

    assert db.database["SP Alpha"]["state_code"] == "13"


# ── PUT /players ─────────────────────────────────────────────────────────

def test_a_save_answers_the_key_and_whether_it_is_new(client, db):
    body = {"prefix": "SP", "gamerTag": "Tag", "name": "Real",
            "twitter": "@t", "custom_textbox": "a\nb",
            "country_code": "JP", "state_code": "13",
            "mains": [["Mario", 1, "EX"]], "game": GAME}

    created = _ok(client.put("/api/v1/players", json=body))

    assert created["data"] == {"key": "SP Tag", "created": True}
    assert "revision" not in created
    assert db.database["SP Tag"] == {
        "prefix": "SP", "gamerTag": "Tag", "name": "Real", "twitter": "@t",
        "custom_textbox": "a\\nb", "mains": {GAME: [["Mario", 1, "EX"]]},
        "country_code": "JP", "state_code": "13"}

    assert _ok(client.put("/api/v1/players", json=body))["data"] == {
        "key": "SP Tag", "created": False}
    assert [row["gamerTag"] for row in _saved(db)] == ["Tag"]


def test_a_save_does_not_touch_the_state(client):
    revision = SM.revision

    _ok(client.put("/api/v1/players", json={"gamerTag": "Tag"}))

    assert SM.revision == revision


def test_a_row_can_be_sent_back_as_it_is(client, db):
    """Nothing a row holds is refused: `key` apart, whatever the database
    stored -- here a free-form region, a JSON-string mains. Its location is
    the one it shows, which a page sends only when it changed (the next test)."""
    db.database["SP Tag"] = {
        "prefix": "SP", "gamerTag": "Tag", "country_code": "JP",
        "state_code": "Shibuya-ku, Tokyo", "custom_textbox": "a\\nb",
        "mains": json.dumps({GAME: [["Mario", 1, "EX"], ["NoArt", -1, ""]],
                             OTHER_GAME: OTHER_MAINS})}
    data = _ok(client.get("/api/v1/players"))["data"]
    row = data["players"][0]
    key = row.pop("key")

    _ok(client.put("/api/v1/players", json={"game": data["game"], **row}))

    assert db.database[key]["state_code"] == "13"
    assert db.database[key]["custom_textbox"] == "a\\nb"
    assert db.database[key]["mains"] == {
        GAME: [["Mario", 1, "EX"], ["NoArt", -1, ""]],
        OTHER_GAME: OTHER_MAINS}


def test_an_edit_of_a_name_damages_nothing_else(client, db):
    """What a page sends when the operator typed a name: the texts it shows,
    and the mains with the game the read named. A character the game does not
    have now, a -1, a region nothing resolves, a flag that is gone: all as
    they were."""
    mains = [["Gone", 9, "Gone Variant"], ["NoArt", -1, ""], ["Mario", -1, ""]]
    db.database["Tag"] = {
        "gamerTag": "Tag", "name": "Old", "country_code": "Gone Flag",
        "state_code": "Nowhere at all",
        "mains": {GAME: mains, OTHER_GAME: OTHER_MAINS}}
    data = _ok(client.get("/api/v1/players"))["data"]
    row = data["players"][0]
    assert row["mains"] == mains
    assert (row["country_code"], row["state_code"]) == ("", "")

    _ok(client.put("/api/v1/players", json={
        "gamerTag": row["gamerTag"], "prefix": row["prefix"], "name": "New",
        "twitter": row["twitter"], "custom_textbox": row["custom_textbox"],
        "mains": row["mains"], "game": data["game"]}))

    assert db.database["Tag"] == {
        "prefix": "", "gamerTag": "Tag", "name": "New", "twitter": "",
        "custom_textbox": "", "mains": {GAME: mains, OTHER_GAME: OTHER_MAINS},
        "country_code": "Gone Flag", "state_code": "Nowhere at all"}


def test_the_other_games_mains_stay(client, db):
    db.database["Tag"] = {"gamerTag": "Tag", "mains": {
        OTHER_GAME: OTHER_MAINS, GAME: [["Peach", 1, ""]]}}

    _ok(client.put("/api/v1/players", json={
        "gamerTag": "Tag", "mains": [["Mario", 0]], "game": GAME}))

    assert db.database["Tag"]["mains"] == {
        OTHER_GAME: OTHER_MAINS, GAME: [["Mario", 0, ""]]}


def test_a_save_without_mains_changes_none(client, db):
    db.database["Tag"] = {"gamerTag": "Tag", "mains": {
        OTHER_GAME: OTHER_MAINS, GAME: [["Peach", 1, ""]]}}

    _ok(client.put("/api/v1/players", json={"gamerTag": "Tag", "name": "New"}))

    assert db.database["Tag"]["mains"] == {
        OTHER_GAME: OTHER_MAINS, GAME: [["Peach", 1, ""]]}


def _mains(*entries):
    return {"gamerTag": "T", "game": GAME, "mains": list(entries)}


@pytest.mark.parametrize("body", [
    {},
    {"gamerTag": ""},
    {"gamerTag": "   "},
    {"gamerTag": 5},
    {"gamerTag": None},
    {"prefix": "SP"},
    {"gamerTag": "T", "prefix": 5},
    {"gamerTag": "T", "name": ["x"]},
    {"gamerTag": "T", "game": 5},
    {"gamerTag": "T", "seed": 3},                 # not a field of the record
    {"gamerTag": "T", "key": "T"},
    {"gamerTag": "T", "country_code": "ZZ"},
    {"gamerTag": "T", "country_code": 5},
    {"gamerTag": "T", "state_code": "13"},        # a region of no country
    {"gamerTag": "T", "country_code": "JP", "state_code": "Tokyo"},
    # Mains name the game they are of.
    {"gamerTag": "T", "mains": [["Mario", 0]]},
    {"gamerTag": "T", "mains": []},
    {"gamerTag": "T", "game": GAME, "mains": None},
    {"gamerTag": "T", "game": GAME, "mains": "Mario"},
    {"gamerTag": "T", "game": GAME,               # PATCH players' shape
     "mains": {GAME: [["Mario", 0]]}},
    _mains("Mario"),
    _mains(["Mario"]),
    _mains(["Mario", "1"]),
    _mains(["Mario", True]),
    _mains(["Mario", -2]),
    _mains(["Mario", 0, 5]),
    _mains(["Mario", 0, "", "x"]),
    _mains([5, 0]),
    _mains(*[["Mario", 0]] * 101),
    _mains(["Gone", 0]),
    _mains(["Mario", 3]),
    _mains(["Mario", -1]),                        # the player has no such main
    _mains(["Mario", 0, "Gone"]),
    # The one-line texts.
    {"gamerTag": "a\nb"},
    {"gamerTag": "T", "prefix": "a\rb"},
    {"gamerTag": "T", "name": "a\r\nb"},
    {"gamerTag": "T", "twitter": "a b"},
    {"gamerTag": "T", "country_code": "J\nP"},
    {"gamerTag": "T", "country_code": "JP", "state_code": "1\n3"},
    {"gamerTag": "x" * 257},
    {"gamerTag": "T", "prefix": "x" * 257},
    {"gamerTag": "T", "name": "x" * 257},
    {"gamerTag": "T", "twitter": "x" * 257},
    {"gamerTag": "T", "custom_textbox": "x" * 4097},
])
def test_a_bad_save_is_rejected_and_saves_nothing(client, db, body):
    _assertRejected(client.put("/api/v1/players", json=body))

    assert db.database == {}
    assert _saved(db) is None


def test_a_body_that_is_no_object_is_rejected(client, db):
    _assertRejected(client.put("/api/v1/players", json=["gamerTag"]))
    assert db.database == {}


def test_the_texts_are_trimmed_and_the_note_keeps_its_lines(client, db):
    body = _ok(client.put("/api/v1/players", json={
        "prefix": " SP ", "gamerTag": "\tTag\n", "name": " Real Name ",
        "twitter": " @t ", "country_code": " JP ", "state_code": " 13 ",
        "custom_textbox": "  a\nb  "}))

    assert body["data"] == {"key": "SP Tag", "created": True}
    assert db.database["SP Tag"] == {
        "prefix": "SP", "gamerTag": "Tag", "name": "Real Name", "twitter": "@t",
        "custom_textbox": "  a\\nb  ", "mains": {},
        "country_code": "JP", "state_code": "13"}


def test_the_limits_are_of_what_is_saved(client, db):
    _ok(client.put("/api/v1/players", json={
        "gamerTag": " " + "x" * 256 + " ", "custom_textbox": "x" * 4096}))

    assert list(db.database) == ["x" * 256]


def test_no_value_is_in_a_text_refusal(client, db):
    for body in ({"gamerTag": f"{SECRET_TAG}\nx"},
                 {"gamerTag": SECRET_TAG * 20},
                 {"gamerTag": "T", "custom_textbox": SECRET_TAG * 300}):
        refused = client.put("/api/v1/players", json=body)
        assert refused.status_code == 400
        assert SECRET_TAG not in refused.get_data(as_text=True)


@pytest.mark.parametrize("body", [
    {"gamerTag": "T", "mains": [["Mario", 0]], "game": OTHER_GAME},
    {"gamerTag": "T", "mains": [], "game": OTHER_GAME},
    {"gamerTag": "T", "mains": [["Mario", 0]], "game": None},
    {"gamerTag": "T", "mains": [], "game": None},
])
def test_mains_of_another_game_conflict(client, db, body):
    _assertRejected(client.put("/api/v1/players", json=body), "CONFLICT", 409)

    assert db.database == {}


def test_with_no_game_loaded_the_read_s_game_is_what_goes_back(client, db):
    """ForgetSelection leaves the last game's codename in the state, and the
    last game's tables in the catalog: only the read's `game` is the answer."""
    GameAssetCatalog.instance.ForgetSelection()
    game = _ok(client.get("/api/v1/players"))["data"]["game"]
    assert game is None

    _assertRejected(client.put("/api/v1/players", json={
        "gamerTag": "T", "mains": [], "game": GAME}), "CONFLICT", 409)
    # Nothing to store them under.
    _assertRejected(client.put("/api/v1/players", json={
        "gamerTag": "T", "mains": [["Mario", 0]], "game": game}),
        "CONFLICT", 409)
    assert db.database == {}

    _ok(client.put("/api/v1/players", json={
        "gamerTag": "T", "mains": [], "game": game}))
    assert db.database["T"]["mains"] == {}


def test_a_game_without_mains_is_not_looked_at(client, db):
    _ok(client.put("/api/v1/players",
                   json={"gamerTag": "T", "game": OTHER_GAME}))

    assert list(db.database) == ["T"]


def test_a_save_that_lost_to_a_newer_one_conflicts(client, db, monkeypatch):
    from src.core.PlayerService import PlayerService

    db.database[SECRET_TAG] = {"gamerTag": SECRET_TAG, "name": "Stored"}
    monkeypatch.setitem(PlayerService._savedSequence, SECRET_TAG,
                        PlayerService.NextSaveSequence() + 10)

    refused = client.put("/api/v1/players",
                         json={"gamerTag": SECRET_TAG, "name": "Late"})

    _assertRejected(refused, "CONFLICT", 409)
    assert SECRET_TAG not in refused.get_data(as_text=True)
    assert db.database[SECRET_TAG]["name"] == "Stored"
    assert _saved(db) is None


@pytest.mark.parametrize("body", [
    {"gamerTag": "T", "country_code": "JP"},
    {"gamerTag": "T", "state_code": "13"},
])
def test_a_location_waits_for_the_country_data(client, db, monkeypatch, body):
    monkeypatch.setattr(CountryCatalog.instance, "_current", CountrySnapshot())

    _assertRejected(client.put("/api/v1/players", json=body), "NOT_READY", 409)

    assert db.database == {}


@pytest.mark.parametrize("body", [
    {"gamerTag": "T"},
    {"gamerTag": "T", "country_code": ""},
    {"gamerTag": "T", "country_code": " ", "state_code": None},
])
def test_without_a_location_the_country_data_is_not_waited_for(
        client, db, monkeypatch, body):
    monkeypatch.setattr(CountryCatalog.instance, "_current", CountrySnapshot())

    _ok(client.put("/api/v1/players", json=body))

    assert list(db.database) == ["T"]


def test_a_location_that_is_not_sent_stays_with_no_country_data(
        client, db, monkeypatch):
    """A row gives the stored values then, and they are not sent back."""
    monkeypatch.setattr(CountryCatalog.instance, "_current", CountrySnapshot())
    db.database["T"] = {"gamerTag": "T", "country_code": "JP",
                        "state_code": "Tokyo"}

    _ok(client.put("/api/v1/players", json={"gamerTag": "T", "name": "New"}))

    assert db.database["T"]["country_code"] == "JP"
    assert db.database["T"]["state_code"] == "Tokyo"


@pytest.mark.parametrize("sent, country, state", [
    ({"country_code": ""}, None, None),
    ({"state_code": ""}, "JP", None),
    ({"country_code": "JP", "state_code": ""}, "JP", None),
    ({"country_code": "US"}, "US", None),
    ({"state_code": "13"}, "JP", "13"),
])
def test_a_location_that_is_sent_changes(client, db, sent, country, state):
    db.database["T"] = {"gamerTag": "T", "country_code": "JP",
                        "state_code": "Tokyo"}

    _ok(client.put("/api/v1/players", json={"gamerTag": "T", **sent}))

    assert db.database["T"].get("country_code") == country
    assert db.database["T"].get("state_code") == state


# ── POST /players:delete ─────────────────────────────────────────────────

def test_delete_needs_the_confirmation(client, db):
    db.database["Tag"] = {"gamerTag": "Tag"}

    for body in ({"key": "Tag"}, {"key": "Tag", "confirm": False}):
        _assertRejected(client.post("/api/v1/players:delete", json=body),
                        "CONFIRMATION_REQUIRED", 409)

    assert list(db.database) == ["Tag"]
    assert _saved(db) is None


def test_delete_removes_the_key_and_saves(client, db):
    db.database.update({"SP Tag": {"prefix": "SP", "gamerTag": "Tag"},
                        "Tag": {"gamerTag": "Tag"}})

    body = _ok(client.post("/api/v1/players:delete",
                           json={"key": "SP Tag", "confirm": True}))

    assert "revision" not in body
    assert list(db.database) == ["Tag"]
    assert [row["gamerTag"] for row in _saved(db)] == ["Tag"]


def test_a_key_the_database_does_not_have_is_404(client, db):
    db.database["SP Tag"] = {"prefix": "SP", "gamerTag": "Tag"}

    # The tag alone is not the key.
    _assertRejected(
        client.post("/api/v1/players:delete",
                    json={"key": "Tag", "confirm": True}), "NOT_FOUND", 404)

    assert list(db.database) == ["SP Tag"]
    assert _saved(db) is None


@pytest.mark.parametrize("key", [None, "", 5, ["Tag"]])
def test_a_key_that_is_no_string_is_rejected(client, db, key):
    db.database["Tag"] = {"gamerTag": "Tag"}

    _assertRejected(client.post("/api/v1/players:delete",
                                json={"key": key, "confirm": True}))

    assert list(db.database) == ["Tag"]


# ── POST .../players/{p}:load ────────────────────────────────────────────

def _swap():
    # What board 1's SWAP runs (its SwapTeams).
    from src.core.Actions import AutonaActions
    AutonaActions.instance.SwapTeams(1)


@pytest.fixture
def board(autonaApp, webApp, tmp_path, monkeypatch):  # noqa: F811
    """Board 1 made afresh on the harness (its game and countries), with no
    widget (ADR-005 S4ac-2), and a database of the test's own with one
    player."""
    autonaApp.ready(1)
    database = PlayerDB(path=str(tmp_path / "local_players.json"),
                        legacyCsvPath=str(tmp_path / "local_players.csv"))
    database.database["SP Alpha"] = {
        "prefix": "SP", "gamerTag": "Alpha", "name": "Real", "mains": "{}"}
    monkeypatch.setattr(PlayerDB, "instance", database)
    return SimpleNamespace(app=autonaApp, client=webApp.test_client())


def test_a_load_writes_the_slot(board):
    body = _ok(board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/1:load", json={"tag": "alpha"}))
    board.app.settle()

    assert body["revision"] == SM.revision
    assert board.app.get("score.1.team.1.player.1.name") == "Alpha"
    assert board.app.get("score.1.team.1.player.1.team") == "SP"
    assert board.app.get("score.1.team.2.player.1.name") in ("", None)


def test_a_load_names_the_sets_team_so_a_swap_flips_it(board):
    """The legacy route's rule, and the Qt list's "set to P1": after a swap
    the set's team 1 is on state team 2. PATCH players names the state team;
    this is the one v1 team parameter that does not (spec §6.27)."""
    _swap()
    board.app.settle()

    _ok(board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/1:load", json={"tag": "alpha"}))
    board.app.settle()

    assert board.app.get("score.1.team.2.player.1.name") == "Alpha"
    assert board.app.get("score.1.team.1.player.1.name") in ("", None)


def test_an_unknown_tag_is_404_and_writes_nothing(board):
    before = json.dumps(board.app.get("score.1"), sort_keys=True, default=str)

    _assertRejected(board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/1:load",
        json={"tag": "Nobody"}), "NOT_FOUND", 404)
    board.app.settle()

    assert json.dumps(board.app.get("score.1"), sort_keys=True,
                      default=str) == before


def test_a_slot_the_team_does_not_have_is_404(board):
    _assertRejected(board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/9:load",
        json={"tag": "alpha"}), "NOT_FOUND", 404)


def test_the_legacy_route_still_answers_500_for_that_slot(board):
    """NoSuchSlot is an IndexError: the 404 is v1's alone."""
    response = board.client.get(
        "/scoreboard1-load-player-from-tag-1-9?tag=alpha")

    assert response.status_code == 500


# ── ... and by the record's key (ADR-005 S4z) ────────────────────────────

def _twoAlphas(board):
    """Two records with the gamer tag "Alpha": a tag finds the first, a key
    the one it names."""
    database = PlayerDB.instance
    database.database["Alpha"] = {"prefix": "", "gamerTag": "Alpha",
                                  "name": "Untagged", "mains": "{}"}
    database.database["Other Bravo"] = {"prefix": "Other",
                                        "gamerTag": "Bravo",
                                        "name": "Real Bravo", "mains": "{}"}
    return database


def test_a_key_loads_the_record_it_names(board):
    _twoAlphas(board)

    body = _ok(board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/1:load",
        json={"key": "Alpha"}))
    board.app.settle()

    assert body["revision"] == SM.revision
    assert board.app.get("score.1.team.1.player.1.name") == "Alpha"
    assert board.app.get("score.1.team.1.player.1.team") in ("", None)
    assert board.app.get("score.1.team.1.player.1.real_name") == "Untagged"

    # The tag finds the first record with that tag, as it always did.
    _ok(board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/1:load",
        json={"tag": "alpha"}))
    board.app.settle()
    assert board.app.get("score.1.team.1.player.1.team") == "SP"


def test_a_key_wins_over_a_tag(board):
    _twoAlphas(board)

    _ok(board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/1:load",
        json={"key": "Other Bravo", "tag": "alpha"}))
    board.app.settle()

    assert board.app.get("score.1.team.1.player.1.name") == "Bravo"
    assert board.app.get("score.1.team.1.player.1.team") == "Other"


@pytest.mark.parametrize("key", ["sp alpha", "Alpha ", "SP", "Nobody"])
def test_a_key_is_matched_exactly_and_an_unknown_one_is_404(board, key):
    before = json.dumps(board.app.get("score.1"), sort_keys=True, default=str)

    response = board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/1:load", json={"key": key})
    _assertRejected(response, "NOT_FOUND", 404)
    assert response.get_json()["error"]["message"] == \
        "no player with that key"
    board.app.settle()

    assert json.dumps(board.app.get("score.1"), sort_keys=True,
                      default=str) == before


def test_a_key_that_names_no_record_is_404(board):
    PlayerDB.instance.database["Broken"] = ["not", "a", "record"]

    _assertRejected(board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/1:load",
        json={"key": "Broken"}), "NOT_FOUND", 404)


@pytest.mark.parametrize("key", [None, "", 5, ["SP Alpha"], {"k": 1}])
def test_a_key_that_is_no_string_is_400_even_with_a_tag(board, key):
    _assertRejected(board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/1:load",
        json={"key": key, "tag": "alpha"}))
    board.app.settle()
    assert board.app.get("score.1.team.1.player.1.name") in ("", None)


def test_a_key_names_the_sets_team_too(board):
    _swap()
    board.app.settle()

    _ok(board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/1:load",
        json={"key": "SP Alpha"}))
    board.app.settle()

    assert board.app.get("score.1.team.2.player.1.name") == "Alpha"
    assert board.app.get("score.1.team.1.player.1.name") in ("", None)


def test_a_key_for_a_slot_the_team_does_not_have_is_404(board):
    _assertRejected(board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/9:load",
        json={"key": "SP Alpha"}), "NOT_FOUND", 404)


def test_a_key_answers_the_same_headless(board, monkeypatch):
    from tests.test_web_operations import _dropHeadlessBoards, _goHeadless

    _goHeadless(board.app, monkeypatch)
    try:
        _ok(board.client.post(
            "/api/v1/scoreboards/1/teams/1/players/1:load",
            json={"key": "SP Alpha"}))
        assert board.app.get("score.1.team.1.player.1.name") == "Alpha"
        assert board.app.get("score.1.team.1.player.1.team") == "SP"
        _assertRejected(board.client.post(
            "/api/v1/scoreboards/1/teams/1/players/1:load",
            json={"key": "sp alpha"}), "NOT_FOUND", 404)
        _assertRejected(board.client.post(
            "/api/v1/scoreboards/3/teams/1/players/1:load",
            json={"key": "SP Alpha"}))
    finally:
        _dropHeadlessBoards(board.app)


def test_the_legacy_route_answers_as_it_did_for_the_rest(board):
    found = board.client.get("/scoreboard1-load-player-from-tag-1-1?tag=alpha")
    assert (found.status_code, found.get_data(as_text=True)) == (200, "OK")
    board.app.settle()
    assert board.app.get("score.1.team.1.player.1.name") == "Alpha"

    missing = board.client.get(
        "/scoreboard1-load-player-from-tag-1-1?tag=Nobody")
    assert (missing.status_code, missing.get_data(as_text=True)) == (
        200, "ERROR")


def test_a_load_copies_the_record_under_the_database_s_lock(board, monkeypatch):
    """Get's record is the live dict the entrants job merges into: the copy
    the load works on is taken where that cannot happen."""
    database = PlayerDB.instance
    owned = []

    class Watched(dict):
        def __deepcopy__(self, memo):
            owned.append(database.lock._is_owned())
            return dict(self)

    database.database["SP Alpha"] = Watched(database.database["SP Alpha"])
    loaded = []
    monkeypatch.setattr(
        WebOps, "_load_player",
        lambda board_, team, player, data, *args: loaded.append(data))

    _ok(board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/1:load", json={"tag": "alpha"}))

    assert owned == [True]
    assert loaded == [database.database["SP Alpha"]]
    assert loaded[0] is not database.database["SP Alpha"]


def test_an_index_error_from_the_load_itself_is_no_404(board, monkeypatch):
    """A broken record is not "no such slot": a 500 with the traceback in
    the log, not a 404 that looks like a wrong number."""
    def broken(*args, **kwargs):
        raise IndexError("a broken record")

    monkeypatch.setattr(WebOps, "_load_player", broken)

    response = board.client.post(
        "/api/v1/scoreboards/1/teams/1/players/1:load", json={"tag": "alpha"})

    assert response.status_code == 500
    assert response.get_json()["error"]["code"] == "INTERNAL"


@pytest.mark.parametrize("path, body", [
    ("/api/v1/scoreboards/3/teams/1/players/1:load", {"tag": "alpha"}),
    ("/api/v1/scoreboards/4/teams/1/players/1:load", {"tag": "alpha"}),
    ("/api/v1/scoreboards/1/teams/3/players/1:load", {"tag": "alpha"}),
    ("/api/v1/scoreboards/1/teams/1/players/0:load", {"tag": "alpha"}),
    ("/api/v1/scoreboards/1/teams/1/players/1:load", {}),
    ("/api/v1/scoreboards/1/teams/1/players/1:load", {"tag": ""}),
    ("/api/v1/scoreboards/1/teams/1/players/1:load", {"tag": 5}),
])
def test_a_bad_load_is_rejected(board, path, body):
    _assertRejected(board.client.post(path, json=body))


# ── access: personal data ────────────────────────────────────────────────

_READS = ["/api/v1/players", "/api/v1/players/entrants"]
_WRITES = [
    ("put", "/api/v1/players", {"gamerTag": "T"}, "save_player",
     {"key": "T", "created": True}),
    ("post", "/api/v1/players:delete", {"key": "T", "confirm": True},
     "delete_player", True),
    ("post", "/api/v1/scoreboards/1/teams/1/players/1:load", {"tag": "T"},
     "load_player_from_tag", "OK"),
]


def test_the_reads_are_the_player_db_and_the_writes_writes():
    for endpoint in ("api_v1.get_players", "api_v1.get_player_entrants"):
        assert WebAccess.ROUTE_ACCESS[endpoint] == WebAccess.PLAYERDB
    for endpoint in ("api_v1.player_save", "api_v1.player_delete",
                     "api_v1.player_load"):
        assert WebAccess.ROUTE_ACCESS[endpoint] == WebAccess.WRITE


@pytest.fixture
def lan(monkeypatch, webApp):  # noqa: F811
    """The routes' operations recorded, the token private."""
    from src.AutonaWebServer import WebServer
    from tests.test_web_access import LAN_HOST, LAN_HOST_2

    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", (LAN_HOST, LAN_HOST_2))
    calls = []
    for _method, _path, _body, name, answer in _WRITES:
        monkeypatch.setattr(
            WebOps, name,
            lambda *args, _name=name, _answer=answer:
            calls.append(_name) or _answer)
    monkeypatch.setattr(
        WebOps, "get_players",
        lambda: calls.append("get_players") or {"game": None, "players": []})
    monkeypatch.setattr(
        WebOps, "get_player_entrants",
        lambda: calls.append("get_player_entrants") or {"game": None,
                                                        "entrants": []})
    return calls


def _peer(webApp):  # noqa: F811
    from tests.test_web_access import LAN_PEER

    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER
    return peer


@pytest.mark.parametrize("path", _READS)
def test_the_reads_need_the_token_from_another_computer(
        webApp, lan, path):  # noqa: F811
    peer = _peer(webApp)

    refused = peer.get(path)
    assert refused.status_code == 401
    assert refused.get_json()["error"]["code"] == "UNAUTHENTICATED"
    assert lan == []

    token = WebAccess.RegenerateToken()
    assert peer.get(path, headers={"X-Autona-Token": token}).status_code == 200
    assert len(lan) == 1

    # This PC (the desktop's embedded view): no token.
    assert webApp.test_client().get(path).status_code == 200
    assert len(lan) == 2


@pytest.mark.parametrize("path", _READS)
@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
])
def test_the_reads_refuse_another_site_on_this_computer(
        webApp, lan, path, headers):  # noqa: F811
    """A page of another site in the operator's browser, which reaches this
    PC as the operator: the real names are not for it. READ routes answer it."""
    refused = webApp.test_client().get(path, headers=headers)

    assert refused.status_code == 403
    assert refused.get_json()["error"]["code"] == "FORBIDDEN"
    assert lan == []


@pytest.mark.parametrize("method, path, body, operation, _answer", _WRITES)
def test_the_writes_need_the_token_from_another_computer(
        webApp, lan, method, path, body, operation, _answer):  # noqa: F811
    peer = _peer(webApp)

    refused = getattr(peer, method)(path, json=body)
    assert refused.status_code == 401
    assert lan == []

    token = WebAccess.RegenerateToken()
    assert getattr(peer, method)(
        path, json=body, headers={"X-Autona-Token": token}).status_code == 200
    assert lan == [operation]

    assert getattr(webApp.test_client(), method)(
        path, json=body).status_code == 200
    assert lan == [operation, operation]


# ── the logs ─────────────────────────────────────────────────────────────

@pytest.fixture
def logged():
    from loguru import logger

    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="DEBUG")
    try:
        yield lines
    finally:
        logger.remove(sink)


def test_no_value_of_a_save_or_a_delete_reaches_the_log(client, db, logged):
    _ok(client.put("/api/v1/players", json={
        "prefix": "Secret Sponsor", "gamerTag": SECRET_TAG,
        "name": SECRET_NAME, "twitter": "@secret_handle"}))
    _ok(client.get("/api/v1/players"))
    _ok(client.post("/api/v1/players:delete",
                    json={"key": f"Secret Sponsor {SECRET_TAG}",
                          "confirm": True}))
    # Refused too: a refusal's answer and log line have no value either.
    refused = client.put("/api/v1/players",
                         json={"gamerTag": SECRET_TAG, "country_code": "ZZ"})
    assert SECRET_TAG not in refused.get_data(as_text=True)
    missing = client.post("/api/v1/players:delete",
                          json={"key": SECRET_TAG, "confirm": True})
    assert SECRET_TAG not in missing.get_data(as_text=True)

    text = "\n".join(logged)
    for value in (SECRET_TAG, SECRET_NAME, "Secret Sponsor", "secret_handle"):
        assert value not in text
    # The op log is there, with the keys.
    assert "op=player.save" in text and "gamerTag" in text
    assert "op=player.delete" in text


def test_no_tag_of_a_load_reaches_the_log(board, logged):
    PlayerDB.instance.database[SECRET_TAG] = {
        "gamerTag": SECRET_TAG, "name": SECRET_NAME, "mains": "{}"}

    _ok(board.client.post("/api/v1/scoreboards/1/teams/1/players/1:load",
                          json={"tag": SECRET_TAG}))
    board.app.settle()
    missing = board.client.post("/api/v1/scoreboards/1/teams/1/players/1:load",
                                json={"tag": "Secret Nobody"})
    assert "Secret Nobody" not in missing.get_data(as_text=True)

    text = "\n".join(logged)
    assert "op=player.load" in text
    for value in (SECRET_TAG, SECRET_NAME, "Secret Nobody"):
        assert value not in text


def _logSinks(path):
    """What each log sink of a source file is configured with: the arguments
    of logger.add, and of the handlers handed to logger.configure."""
    sinks = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Call) \
                and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "logger":
            sinks.append({keyword.arg: keyword.value
                          for keyword in node.keywords})
        elif isinstance(node, ast.Dict) and any(
                isinstance(key, ast.Constant) and key.value == "sink"
                for key in node.keys):
            sinks.append({key.value: value
                          for key, value in zip(node.keys, node.values)})
    return sinks


def test_no_log_sink_writes_the_values_of_local_variables():
    """loguru's default (diagnose=True) writes every local variable's value
    into a traceback: an exception anywhere near a player would put their name
    and handle in logs/, which can go along with a bug report. Read from the
    source: importing src/Autona.py is the start-up itself."""
    root = Path(__file__).resolve().parents[1]
    sinks = {}
    for path in [root / "main.py", *sorted((root / "src").rglob("*.py"))]:
        found = _logSinks(path)
        if found:
            sinks[path.relative_to(root).as_posix()] = found

    assert sorted(sinks) == ["src/Autona.py", "src/headless.py"]
    # The console, autona.log, tsh-error.log.
    assert [len(found) for found in sinks.values()] == [3, 3]
    for file, found in sinks.items():
        for sink in found:
            value = sink.get("diagnose")
            assert isinstance(value, ast.Constant) and value.value is False, \
                file


def test_what_a_sink_without_diagnose_keeps_out():
    """What the setting does, so that the test above means something."""
    from loguru import logger

    def traceback(**options):
        lines = []
        sink = logger.add(lambda message: lines.append(str(message)),
                          level="ERROR", **options)
        try:
            player = SECRET_NAME
            try:
                player.no_such_attribute
            except AttributeError:
                logger.exception("failed")
        finally:
            logger.remove(sink)
        return "\n".join(lines)

    assert SECRET_NAME in traceback(diagnose=True)
    assert SECRET_NAME not in traceback(diagnose=False)
    assert "AttributeError" in traceback(diagnose=False)
