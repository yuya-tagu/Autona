"""The archive clipper's results and their characters (ADR-006 A5):
core/ArchiveResults.py (the three kinds, the composition, which record is
which set, reading the match log), the start.gg fetch of an event's
completed sets (StartGGDataProvider.ParseEventResults / FetchEventResults,
from tests/fixtures/archive_results -- no network), the catalog's
GameCharacters / StartGGCharacterName, and the v1 routes the page calls
(POST /api/v1/archive/results:prepare, SHELL as the job's creation;
GET /api/v1/archive/match-logs, THIS_PC).

The game is tests/fixtures/game_assets' synthetic one (tgame, start.gg
videogame 1386; characters mario / luigi / peach / koopa / zelda, start.gg
ids 1 Mario, 2 Luigi, 3 Bowser; game2 has Ryu, start.gg 4), scanned with
the program locale ja-JP: Mario is マリオ, Luigi ルイージ, the others their
en_name.
"""
import datetime
import json
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from loguru import logger

from src.core import ArchiveJobService as archive
from src.core import ArchiveResults as results
from src.core.ArchiveJobService import ArchiveError
from src.core.GameAssetCatalog import GameAssetCatalog, GameAssetSnapshot
from src.core.MatchLogService import MatchLogService
from src.core.PlayerDB import PlayerDB
from src.core.StatusService import StatusService
from src.core.TaskExecutor import TaskExecutor
# fake, page, service, work, server, settings and shellSecret: the routes'
# fixtures (pytest finds them by name).
from tests.test_archive_jobs import (  # noqa: F401
    BASE, SOURCE, _data, _ended, _refusal, fake, page, service, work)
from tests.test_web_access import server, settings, shellSecret  # noqa: F401

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GAME_ASSETS = FIXTURES / "game_assets"
PAGES = json.loads((FIXTURES / "archive_results" / "startgg_event_pages.json")
                   .read_text(encoding="utf-8"))
WAIT = 30


# ── fixtures ─────────────────────────────────────────────────────────────

def _scanned(locale="ja-JP", remapping=None):
    def remap(language):
        # AutonaLocaleHelper.GetRemaps over `remapping`.
        for name, languages in (remapping or {}).items():
            if language.replace("-", "_") in languages:
                return name
        return None

    catalog = GameAssetCatalog(locales=lambda: (locale, locale), remap=remap)
    catalog.ScanGames()
    catalog.ReloadStartGGCharacters()
    return catalog


@pytest.fixture
def assets(tmp_path, monkeypatch):
    """cwd tmp with tests/fixtures/game_assets' games and start.gg
    characters in its user_data/."""
    monkeypatch.chdir(tmp_path)
    shutil.copytree(GAME_ASSETS / "games", tmp_path / "user_data" / "games")
    shutil.copy(GAME_ASSETS / "characters.json",
                tmp_path / "user_data" / "characters.json")
    return tmp_path


@pytest.fixture
def catalog(assets):
    """The scanned catalog, the loaded game tgame (no load: Current() is
    replaced, so no state is written)."""
    made = _scanned()
    made.Current = lambda: GameAssetSnapshot(codename="tgame")
    return made


def _database(tmp_path, players):
    database = PlayerDB(path=str(tmp_path / "players.json"))
    database.database = {player["gamerTag"]: player for player in players}
    return database


# No player: Prepare reads PlayerDB.instance when given None.
NOBODY = PlayerDB(path="nobody.json")


@pytest.fixture
def database(tmp_path):
    return _database(tmp_path, [
        {"gamerTag": "Xi", "mains": {"tgame": [["Zelda", 0, ""],
                                               ["Mario", 0, ""]]}},
        # An old row's mains: a JSON string.
        {"gamerTag": "Kappa", "mains": json.dumps(
            {"tgame": [["Peach", 1, ""]], "game2": [["Ryu", 0, ""]]})},
        {"gamerTag": "Lambda", "mains": {"game2": [["Ryu", 0, ""]]}},
        # A main the game does not have.
        {"gamerTag": "Nu", "mains": {"tgame": [["Nobody", 0, ""]]}},
    ])


@pytest.fixture
def logs(tmp_path):
    folder = tmp_path / "match_log"
    folder.mkdir()
    return folder


def _record(ident, p1, p2, score, games=(), c1="", c2="", tournament="",
            set_id=None, phase="", round_="", closed=True, game="tgame"):
    return {"id": ident, "board": 1, "started_at": "", "updated_at": "",
            "closed": closed, "tournament": tournament, "set_id": set_id,
            "phase": phase, "round": round_, "p1": p1, "p2": p2,
            "game": game, "c1": c1, "c2": c2, "score": list(score),
            "games": [{"c1": a, "c2": b, "at": ""} for a, b in games]}


def _oldRecord(*args, **kwargs):
    """A record written before records named their game: no `game`."""
    record = _record(*args, **kwargs)
    del record["game"]
    return record


def _icons(*codenames):
    """tgame's icons of `codenames` (its icon pack: chara_2_ + codename +
    _0, the lowest skin 0)."""
    return {codename: f"base_files/icon/chara_2_{codename}_00.png"
            for codename in codenames}


NO_CHARACTER_KEYS = {"c1", "c2", "games", "char_source", "autona"}


def _write(folder, date, *lines):
    with open(folder / f"{date}.jsonl", "w", encoding="utf-8") as file:
        for line in lines:
            file.write(line if isinstance(line, str)
                       else json.dumps(line, ensure_ascii=False))
            file.write("\n")


def _set(round_, p1, s1, p2, s2, ident, games=()):
    """A set as FetchEventResults answers it."""
    return {"id": ident, "round": round_, "p1": p1, "s1": s1, "p2": p2,
            "s2": s2, "games": [{"c1": a, "c2": b} for a, b in games]}


def _timestamp(year, month, day, hour=12):
    return datetime.datetime(year, month, day, hour).timestamp()


def _event(sets, tournament="Fake Cup", videogame=1386,
           start=(2026, 11, 14), end=(2026, 11, 14, 20)):
    return {"tournament": tournament, "event": "Ultimate Singles",
            "url": "https://www.start.gg/tournament/fake-cup/event/singles",
            "videogame": videogame, "startAt": _timestamp(*start),
            "endAt": _timestamp(*end) if end else None, "sets": sets}


def _startgg(event, catalog, database, logs):
    return results.Prepare(
        {"kind": "startgg", "url": event["url"]},
        fetchEvent=lambda url: event, directory=str(logs),
        catalog=catalog, database=database or NOBODY)


def _sources(summary, **counts):
    want = dict.fromkeys(results.SOURCES + (results.GIVEN, results.NONE), 0)
    want.update(counts)
    return summary["sources"] == want


# ── names ────────────────────────────────────────────────────────────────

def test_names_are_compared_without_sponsor_case_width_or_spaces():
    same = results.NormalizeName
    assert same("TEAM | Ｋａｐｐａ ") == same("kappa") == "kappa"
    assert same("A | B | Ka ppa") == "kappa"
    # A full-width bar is one after NFKC.
    assert same("TEAM ｜ Kappa") == "kappa"
    assert same("Ｍｒ．　Ｇ") == same("mr.g")
    # Accents and kana are names of their own.
    assert same("ガ") != same("カ")
    assert same("Émile") != same("Emile")
    assert same(None) == same(3) == ""


# ── reading the match log ────────────────────────────────────────────────

def test_the_last_line_of_a_record_is_the_record_and_bad_lines_are_skipped(
        logs):
    first = _record("A", "Kappa", "Lambda", (1, 0), [("mario", "luigi")])
    later = _record("A", "Kappa", "Lambda", (2, 0),
                    [("mario", "luigi"), ("mario", "peach")])
    other = _record("B", "Mu", "Nu", (0, 1), [("zelda", "koopa")])
    _write(logs, "20261114", first, "not json", "[1, 2]", '{"id": 5}',
           '{"id": ""}', other, later, '{"id": "C", "p1": "Cut')

    records = results.ReadDay("20261114", str(logs))

    # A keeps its first line's place, with its last line's content.
    assert [r["id"] for r in records] == ["A", "B"]
    assert records[0]["score"] == (2, 0)
    assert records[0]["games"] == [("mario", "luigi"), ("mario", "peach")]
    assert results.ReadDay("20261115", str(logs)) == []


def test_an_open_record_is_used_and_an_empty_or_unscored_one_is_not(logs):
    _write(logs, "20261114",
           _record("open", "Kappa", "Lambda", (1, 1), closed=False),
           _record("noname", "Kappa", "", (1, 0)),
           _record("blank", "  ", "Lambda", (1, 0)),
           _record("reset", "Kappa", "Lambda", (0, 0)),
           dict(_record("bad", "Kappa", "Lambda", (1, 0)), score=[1, "2"]),
           dict(_record("short", "Kappa", "Lambda", (1, 0)), score=[1]),
           dict(_record("neg", "Kappa", "Lambda", (1, 0)), score=[1, -1]),
           dict(_record("bool", "Kappa", "Lambda", (1, 0)), score=[True, 0]))

    assert [r["id"] for r in results.ReadDay("20261114", str(logs))] == [
        "open"]


def test_the_days_list_the_tournaments_newest_first(logs):
    _write(logs, "20261113", _record("a", "A", "B", (2, 0),
                                     tournament="Fake Cup"))
    _write(logs, "20261114",
           _record("b", "A", "B", (2, 1), tournament="Fake Cup"),
           _record("c", "C", "D", (0, 2)),
           _record("d", "E", "F", (2, 0), tournament="Fake Cup"),
           _record("e", "G", "H", (1, 0), tournament="Other Cup"))
    # Nothing used that day; files that are no day.
    _write(logs, "20261115", _record("f", "A", "", (1, 0)))
    _write(logs, "notes", _record("g", "A", "B", (1, 0)))
    _write(logs, "2026111", _record("h", "A", "B", (1, 0)))

    assert results.Days(str(logs)) == [
        {"date": "20261114", "records": 4, "tournaments": [
            {"name": "Fake Cup", "records": 2}, {"name": "", "records": 1},
            {"name": "Other Cup", "records": 1}]},
        {"date": "20261113", "records": 1, "tournaments": [
            {"name": "Fake Cup", "records": 1}]},
    ]
    assert results.Days(str(logs / "none")) == []


def test_a_day_is_read_again_only_when_its_file_changes(logs, monkeypatch):
    _write(logs, "20261114", _record("a", "A", "B", (2, 0),
                                     tournament="Fake Cup"))
    _write(logs, "20261113", _record("b", "A", "B", (2, 0)))
    read = []
    real = results.ReadDay

    def counted(date, directory=None):
        read.append(date)
        return real(date, directory)

    monkeypatch.setattr(results, "ReadDay", counted)
    first = results.Days(str(logs))
    assert sorted(read) == ["20261113", "20261114"]

    read.clear()
    assert results.Days(str(logs)) == first
    assert read == []

    # A record appended: that day is read again.
    with open(logs / "20261114.jsonl", "a", encoding="utf-8") as file:
        file.write(json.dumps(_record("c", "C", "D", (1, 0))) + "\n")
    days = results.Days(str(logs))
    assert read == ["20261114"]
    assert days[0]["records"] == 2
    assert days[0]["tournaments"] == [{"name": "Fake Cup", "records": 1},
                                      {"name": "", "records": 1}]

    # A day removed is forgotten.
    (logs / "20261113.jsonl").unlink()
    assert [day["date"] for day in results.Days(str(logs))] == ["20261114"]
    assert [key for key in results._dayCache
            if key[0] == str(logs)] == [(str(logs), "20261114")]


def test_a_folder_that_cannot_be_listed_has_no_day(logs, monkeypatch):
    _write(logs, "20261114", _record("a", "A", "B", (2, 0)))

    def refused(_path):
        raise PermissionError("denied")

    messages = []
    sink = logger.add(messages.append, level="WARNING")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(results.os, "listdir", refused)
            assert results.Days(str(logs)) == []
    finally:
        logger.remove(sink)
    assert any("could not be listed" in message for message in messages)


def test_the_match_log_folder_is_the_services(logs, monkeypatch):
    _write(logs, "20261114", _record("a", "A", "B", (2, 0)))
    monkeypatch.setattr(MatchLogService, "instance",
                        SimpleNamespace(directory=str(logs)))

    assert [day["date"] for day in results.Days()] == ["20261114"]


# ── the match log's kind ─────────────────────────────────────────────────

def test_a_match_log_day_becomes_results_with_its_characters(
        logs, catalog, database):
    _write(logs, "20261114",
           _record("r1", "Kappa", "Lambda", (2, 1),
                   [("mario", "luigi"), ("koopa", "luigi"),
                    ("mario", "luigi")],
                   c1="mario", c2="luigi", tournament="Fake Cup",
                   phase="Top 8", round_="Winners Final"),
           # No game names a character: the board's last ones.
           _record("r2", "Mu", "Nu", (0, 2), [("", ""), ("", "")],
                   c1="zelda", c2="peach", tournament="Fake Cup",
                   phase="Top 8"),
           # Characters the game does not know: the player database's.
           _record("r3", "Xi", "Omicron", (1, 0), [("ryu", "ghostly")],
                   c1="ryu", c2="ghostly", tournament="Fake Cup"),
           # Three characters: the first two played.
           _record("r4", "Pi", "Rho", (2, 1),
                   [("zelda", "mario"), ("peach", "mario"),
                    ("mario", "mario")], tournament="Fake Cup"),
           # Nothing known at all.
           _record("r5", "Sigma", "Tau", (1, 0), tournament="Fake Cup"))

    prepared = results.Prepare({"kind": "matchlog", "date": "20261114"},
                               directory=str(logs), catalog=catalog,
                               database=database)

    sets = prepared["results"]["sets"]
    assert prepared["results"]["event"] == "Fake Cup"
    assert prepared["results"]["date"] == "2026-11-14"
    assert sets[0] == {
        "round": "Winners Final", "p1": "Kappa", "s1": 2, "p2": "Lambda",
        "s2": 1, "id": "r1", "c1": "マリオ/Bowser", "c2": "ルイージ",
        "games": [{"c1": "マリオ", "c2": "ルイージ"},
                  {"c1": "Bowser", "c2": "ルイージ"},
                  {"c1": "マリオ", "c2": "ルイージ"}],
        "char_source": "scoreboard",
        "autona": {"game": "tgame", "c1": ["mario", "koopa"],
                   "c2": ["luigi"], "icons": _icons("mario", "koopa", "luigi")}}
    # The phase when the round is empty; no game names one, so no games.
    assert sets[1] == {
        "round": "Top 8", "p1": "Mu", "s1": 0, "p2": "Nu", "s2": 2,
        "id": "r2", "c1": "Zelda", "c2": "Peach",
        "char_source": "scoreboard",
        "autona": {"game": "tgame", "c1": ["zelda"], "c2": ["peach"],
                   "icons": _icons("zelda", "peach")}}
    # Xi's first main only (one guess); Omicron has none.
    assert sets[2] == {
        "round": "", "p1": "Xi", "s1": 1, "p2": "Omicron", "s2": 0,
        "id": "r3", "c1": "Zelda", "char_source": "mains",
        "autona": {"game": "tgame", "c1": ["zelda"], "c2": [],
                   "icons": _icons("zelda")}}
    assert sets[3]["c1"] == "Zelda/Peach" and sets[3]["c2"] == "マリオ"
    assert sets[3]["autona"]["c1"] == ["zelda", "peach"]
    assert sets[4] == {"round": "", "p1": "Sigma", "s1": 1, "p2": "Tau",
                       "s2": 0, "id": "r5"}
    summary = prepared["summary"]
    assert (summary["sets"], summary["game"], summary["records"]) == (
        5, "tgame", 5)
    assert _sources(summary, scoreboard=3, mains=1, none=1)


def test_a_records_characters_are_of_the_game_it_names(logs, catalog,
                                                       database):
    _write(logs, "20261114",
           # game2's, though tgame is loaded.
           _record("r1", "Kappa", "Lambda", (2, 0), [("ryu", "ryu")] * 2,
                   tournament="Game2 Cup", game="game2"),
           # Written before records named their game: no character -- its
           # own (tgame's codenames) nor Xi's main.
           _oldRecord("r2", "Xi", "Omicron", (1, 0), [("mario", "luigi")]),
           # No game was loaded.
           _record("r3", "Xi", "Nu", (1, 0), [("mario", "luigi")],
                   game=None))

    def prepare(tournament=None):
        return results.Prepare(
            {"kind": "matchlog", "date": "20261114",
             "tournament": tournament},
            directory=str(logs), catalog=catalog, database=database)

    prepared = prepare()
    sets = prepared["results"]["sets"]
    assert (sets[0]["c1"], sets[0]["c2"]) == ("Ryu", "Ryu")
    # game2's icon pack names its files another way (no prefix, "_").
    assert sets[0]["autona"] == {"game": "game2", "c1": ["ryu"],
                                 "c2": ["ryu"],
                                 "icons": {"ryu": "base_files/icon/ryu_0.png"}}
    assert not NO_CHARACTER_KEYS & set(sets[1])
    assert not NO_CHARACTER_KEYS & set(sets[2])
    # More than one game (and none): no game.
    assert prepared["summary"]["game"] is None
    assert _sources(prepared["summary"], scoreboard=1, none=2)
    assert prepare("Game2 Cup")["summary"]["game"] == "game2"


def test_a_match_log_tournament_is_its_name_as_written(logs, catalog):
    _write(logs, "20261114",
           _record("a", "A", "B", (2, 0), tournament="Fake Cup"),
           _record("b", "C", "D", (2, 0), tournament="fake cup"),
           _record("c", "E", "F", (2, 0)))

    def prepare(tournament):
        return results.Prepare(
            {"kind": "matchlog", "date": "20261114",
             "tournament": tournament},
            directory=str(logs), catalog=catalog, database=NOBODY)["results"]

    named = prepare(" Fake Cup ")
    assert [s["id"] for s in named["sets"]] == ["a"]
    assert named["event"] == "Fake Cup"
    assert [s["id"] for s in prepare("")["sets"]] == ["c"]
    # The whole day: more than one name, so no event name.
    whole = prepare(None)
    assert [s["id"] for s in whole["sets"]] == ["a", "b", "c"]
    assert whole["event"] == ""


@pytest.mark.parametrize("body, code, message", [
    ({"kind": "matchlog"}, archive.INVALID, results.BAD_DATE),
    ({"kind": "matchlog", "date": "2026-11-14"}, archive.INVALID,
     results.BAD_DATE),
    ({"kind": "matchlog", "date": "20261340"}, archive.INVALID,
     results.BAD_DATE),
    ({"kind": "matchlog", "date": "20261114", "tournament": 3},
     archive.INVALID, results.BAD_TOURNAMENT),
    ({"kind": "matchlog", "date": "20261115"}, archive.NOT_FOUND,
     results.NO_RECORDS),
    ({"kind": "matchlog", "date": "20261114", "tournament": "Other"},
     archive.NOT_FOUND, results.NO_TOURNAMENT_RECORDS),
    ({"kind": "csv"}, archive.INVALID, results.BAD_KIND),
    ({}, archive.INVALID, results.BAD_KIND),
    (["matchlog"], archive.INVALID, results.BAD_KIND),
])
def test_a_match_log_request_that_names_no_records_is_refused(
        logs, catalog, body, code, message):
    _write(logs, "20261114", _record("a", "A", "B", (2, 0),
                                     tournament="Fake Cup"))

    with pytest.raises(ArchiveError) as refused:
        results.Prepare(body, directory=str(logs), catalog=catalog,
                        database=NOBODY)
    assert (refused.value.code, refused.value.message) == (code, message)


# ── the start.gg kind: which characters ──────────────────────────────────

def test_characters_come_from_the_record_then_the_report_then_the_mains(
        logs, catalog, database):
    _write(logs, "20261114",
           _record("r1", "Kappa", "Lambda", (3, 1),
                   [("peach", "zelda")] * 4, tournament="Fake Cup"),
           # Side 2's character is unknown to the game: start.gg's.
           _record("r4", "Pi", "Rho", (2, 0), [("zelda", "nobody")] * 2,
                   tournament="Fake Cup"))
    event = _event([
        # A record and a report: the record's.
        _set("Winners Final", "Kappa", 3, "Lambda", 1, 1,
             [(1, 2), (1, 2), (3, 2), (1, 2)]),
        # The report only (a game with no selection is none).
        _set("Losers Final", "Mu", 3, "Nu", 2, 2,
             [(1, 3), (None, None), (2, 3)]),
        # Neither: Xi's main; Omicron has none.
        _set("Losers Round 1", "Xi", 2, "Omicron", 0, 3),
        # One side each.
        _set("Winners Semi-Final", "Pi", 2, "Rho", 0, 4, [(1, 2), (1, 2)]),
        # Nothing known: Ryu (4) is game2's, 5 no game's.
        _set("Grand Final", "Sigma", 3, "Tau", 0, 5, [(4, 5)]),
    ])

    prepared = _startgg(event, catalog, database, logs)

    sets = prepared["results"]["sets"]
    assert sets[0]["c1"] == "Peach" and sets[0]["c2"] == "Zelda"
    assert sets[0]["char_source"] == "scoreboard"
    assert sets[0]["games"] == [{"c1": "Peach", "c2": "Zelda"}] * 4
    assert sets[1]["c1"] == "マリオ/ルイージ" and sets[1]["c2"] == "Bowser"
    assert sets[1]["char_source"] == "report"
    assert sets[1]["games"] == [{"c1": "マリオ", "c2": "Bowser"},
                                {"c1": None, "c2": None},
                                {"c1": "ルイージ", "c2": "Bowser"}]
    assert sets[1]["autona"] == {"game": "tgame", "c1": ["mario", "luigi"],
                                 "c2": ["koopa"],
                                 "icons": _icons("mario", "luigi", "koopa")}
    assert (sets[2]["c1"], sets[2].get("c2"), sets[2]["char_source"]) == (
        "Zelda", None, "mains")
    assert "games" not in sets[2]
    # The weakest side: the record gave side 1, the report side 2. The
    # record's games name a character, so they are the games.
    assert (sets[3]["c1"], sets[3]["c2"], sets[3]["char_source"]) == (
        "Zelda", "ルイージ", "report")
    assert sets[3]["games"] == [{"c1": "Zelda", "c2": None}] * 2
    assert not NO_CHARACTER_KEYS & set(sets[4])
    assert _sources(prepared["summary"], scoreboard=1, report=2, mains=1,
                    none=1)
    assert prepared["summary"]["records"] == 2


def test_a_record_of_another_game_gives_a_start_gg_set_nothing(
        logs, catalog, database):
    _write(logs, "20261114",
           # Codenames tgame has, but the record is game2's.
           _record("other", "Kappa", "Lambda", (3, 1),
                   [("peach", "zelda")] * 4, tournament="Fake Cup",
                   game="game2"),
           _oldRecord("old", "Xi", "Omicron", (2, 0), [("peach", "peach")],
                      tournament="Fake Cup"))
    event = _event([
        _set("Winners Final", "Kappa", 3, "Lambda", 1, 1, [(1, 2)] * 4),
        _set("Losers Round 1", "Xi", 2, "Omicron", 0, 2),
    ])

    prepared = _startgg(event, catalog, database, logs)

    sets = prepared["results"]["sets"]
    # The report's, and Xi's main: the records give nothing.
    assert (sets[0]["c1"], sets[0]["char_source"]) == ("マリオ", "report")
    assert (sets[1]["c1"], sets[1]["char_source"]) == ("Zelda", "mains")
    assert prepared["summary"]["records"] == 2


def test_a_record_is_first_the_set_whose_id_it_has(logs, catalog):
    _write(logs, "20261114",
           # Set 2's by its id, though a name was typed another way and the
           # stream ended at 1-1. Its sides by the one name the same:
           # Lambda is its p2, the set's p1.
           _record("gf", "Kap", "Lambda", (1, 1), [("zelda", "peach")],
                   tournament="Fake Cup", set_id=2),
           # Then the winners final is the pair's one record left, at any
           # score.
           _record("wf", "Kappa", "Lambda", (2, 1), [("mario", "luigi")],
                   tournament="Fake Cup"),
           # Set 3's id twice, neither at its score: none's -- not set 4's
           # either, though x2 has set 4's names and score.
           _record("x1", "Mu", "Nu", (1, 0), [("koopa", "koopa")],
                   tournament="Fake Cup", set_id="3"),
           _record("x2", "Mu", "Nu", (2, 0), [("koopa", "koopa")],
                   tournament="Fake Cup", set_id="3"))
    event = _event([
        _set("Winners Final", "Kappa", 3, "Lambda", 1, 1),
        _set("Grand Final", "Lambda", 3, "Kappa", 2, 2),
        _set("Losers Final", "Mu", 3, "Nu", 2, 3),
        _set("Losers Semi-Final", "Mu", 2, "Nu", 0, 4),
    ])

    sets = _startgg(event, catalog, None, logs)["results"]["sets"]

    assert (sets[0]["c1"], sets[0]["c2"]) == ("マリオ", "ルイージ")
    assert (sets[1]["c1"], sets[1]["c2"]) == ("Peach", "Zelda")
    assert not NO_CHARACTER_KEYS & set(sets[2])
    assert not NO_CHARACTER_KEYS & set(sets[3])


def test_a_record_of_a_set_id_at_its_score_is_that_sets(logs, catalog):
    _write(logs, "20261114",
           _record("a", "Kappa", "Lambda", (2, 3), [("peach", "zelda")],
                   tournament="Fake Cup", set_id="1"),
           # Swapped sides, at the set's score.
           _record("b", "Lambda", "Kappa", (1, 3), [("koopa", "mario")],
                   tournament="Fake Cup", set_id="1"))

    sets = _startgg(_event([_set("Winners Final", "Kappa", 3, "Lambda", 1,
                                 1)]), catalog, None, logs)["results"]["sets"]

    assert (sets[0]["c1"], sets[0]["c2"]) == ("マリオ", "Bowser")


def test_a_record_on_swapped_sides_gives_its_characters_swapped(
        logs, catalog):
    _write(logs, "20261114", _record(
        "r", "lambda", "ＫＡＰＰＡ", (1, 3),
        [("koopa", "mario"), ("koopa", "mario"), ("zelda", "mario"),
         ("koopa", "mario")], tournament="Fake Cup"))

    prepared = _startgg(_event([_set("Winners Final", "TEAM | Kappa", 3,
                                     "Lambda", 1, 1)]),
                        catalog, None, logs)

    played = prepared["results"]["sets"][0]
    assert (played["c1"], played["c2"]) == ("マリオ", "Bowser/Zelda")
    assert played["games"][2] == {"c1": "マリオ", "c2": "Zelda"}
    assert played["autona"]["c2"] == ["koopa", "zelda"]


def test_one_set_and_one_record_of_a_pair_are_one_match_whatever_the_score(
        logs, catalog):
    # A record left at 2-1 (the stream ended) is still that set.
    _write(logs, "20261114", _record("r", "Kappa", "Lambda", (2, 1),
                                     [("mario", "luigi")] * 3,
                                     tournament="Fake Cup"))

    prepared = _startgg(_event([_set("Winners Final", "Kappa", 3,
                                     "Lambda", 1, 1)]),
                        catalog, None, logs)

    assert prepared["results"]["sets"][0]["char_source"] == "scoreboard"


def test_sets_of_one_pair_are_told_apart_by_their_score(logs, catalog):
    _write(logs, "20261114",
           _record("wf", "Kappa", "Lambda", (3, 1), [("mario", "luigi")],
                   tournament="Fake Cup"),
           # The grand final, Kappa still on the left.
           _record("gf", "Kappa", "Lambda", (2, 3), [("peach", "zelda")],
                   tournament="Fake Cup"))
    event = _event([
        _set("Winners Final", "Kappa", 3, "Lambda", 1, 1),
        # start.gg has Lambda as p1 in the grand final.
        _set("Grand Final", "Lambda", 3, "Kappa", 2, 2),
    ])

    sets = _startgg(event, catalog, None, logs)["results"]["sets"]

    assert (sets[0]["c1"], sets[0]["c2"]) == ("マリオ", "ルイージ")
    assert (sets[1]["c1"], sets[1]["c2"]) == ("Zelda", "Peach")


def test_sets_a_score_cannot_tell_apart_take_no_record(logs, catalog):
    _write(logs, "20261114",
           _record("a", "Kappa", "Lambda", (3, 1), [("mario", "luigi")],
                   tournament="Fake Cup"),
           # A record of the pair with a score no set has: no set's.
           _record("b", "Kappa", "Lambda", (1, 1), [("peach", "zelda")],
                   tournament="Fake Cup"))
    event = _event([
        _set("Winners Final", "Kappa", 3, "Lambda", 1, 1, [(3, 3)]),
        _set("Grand Final", "Kappa", 3, "Lambda", 1, 2, [(3, 3)]),
    ])

    prepared = _startgg(event, catalog, None, logs)

    assert [s["char_source"] for s in prepared["results"]["sets"]] == [
        "report", "report"]


def test_an_events_records_are_its_days_by_name_or_set(logs, catalog):
    record = _record
    games = [("mario", "luigi")]
    _write(logs, "20261113", record("before", "A", "B", (2, 0), games,
                                    tournament="Fake Cup"))
    _write(logs, "20261114",
           # Another display name, but start.gg's set.
           record("byset", "A", "B", (2, 0), games, tournament="配信名",
                  set_id="1"),
           record("othercup", "C", "D", (2, 0), games,
                  tournament="Other Cup"),
           record("noname", "C", "D", (2, 0), games))
    # The day after the end (the stream ran past midnight), the name
    # written another way.
    _write(logs, "20261115", record("after", "C", "D", (0, 2), games,
                                    tournament="ＦＡＫＥ ＣＵＰ"))
    _write(logs, "20261116", record("late", "E", "F", (2, 0), games,
                                    tournament="Fake Cup"))
    event = _event([_set("R1", "A", 2, "B", 0, 1), _set("R2", "C", 0, "D", 2, 2),
                    _set("R3", "E", 2, "F", 0, 3)])

    prepared = _startgg(event, catalog, None, logs)

    assert prepared["summary"]["records"] == 2
    assert [s.get("char_source") for s in prepared["results"]["sets"]] == [
        "scoreboard", "scoreboard", None]


def test_an_events_days_are_at_most_a_week_and_a_day(logs, catalog):
    for day in (14, 21, 22):
        _write(logs, f"202611{day}", _record(
            f"d{day}", "A", "B", (2, 0), [("mario", "luigi")],
            tournament="Fake Cup"))

    prepared = _startgg(_event([_set("R1", "A", 2, "B", 0, 1)],
                               end=(2026, 12, 30)), catalog, None, logs)

    # 14 .. 21: 8 days.
    assert prepared["summary"]["records"] == 2


def test_an_event_without_an_end_is_its_start_day_and_the_next(logs,
                                                                catalog):
    for day in (14, 15, 16):
        _write(logs, f"202611{day}", _record(
            f"d{day}", "A", "B", (2, 0), [("mario", "luigi")],
            tournament="Fake Cup"))

    prepared = _startgg(_event([_set("R1", "A", 2, "B", 0, 1)], end=None),
                        catalog, None, logs)

    assert prepared["summary"]["records"] == 2


def test_an_event_without_a_start_reads_no_record(logs, catalog):
    _write(logs, "20261114", _record("a", "A", "B", (2, 0),
                                     [("mario", "luigi")],
                                     tournament="Fake Cup"))
    event = dict(_event([_set("R1", "A", 2, "B", 0, 1)]), startAt=None)

    prepared = _startgg(event, catalog, None, logs)

    assert prepared["summary"]["records"] == 0
    assert "date" not in prepared["results"]


def test_the_results_name_the_event_and_its_bracket(logs, catalog):
    prepared = _startgg(_event([_set("R1", "A", 2, "B", 0, 1)]), catalog,
                        None, logs)

    assert prepared["results"] == {
        "event": "Fake Cup",
        "sets": [{"round": "R1", "p1": "A", "s1": 2, "p2": "B", "s2": 0,
                  "id": 1}],
        "bracket_url":
            "https://www.start.gg/tournament/fake-cup/event/singles",
        "date": "2026-11-14"}
    assert prepared["summary"]["game"] == "tgame"


def test_the_game_is_the_events_else_the_loaded_one(logs, assets,
                                                    database):
    catalog = _scanned()
    catalog.Current = lambda: GameAssetSnapshot(codename="game2")
    played = _set("R1", "A", 2, "B", 0, 1, [(1, 4)])
    # Kappa and Lambda have game2's Ryu as their main.
    guessed = _set("R2", "Kappa", 2, "Lambda", 0, 2)

    # The event's videogame: tgame, though game2 is loaded.
    first = _startgg(_event([dict(played)]), catalog, None, logs)
    assert first["summary"]["game"] == "tgame"
    assert first["results"]["sets"][0]["c1"] == "マリオ"
    assert "c2" not in first["results"]["sets"][0]
    # A videogame no scanned game is: no character -- not the loaded
    # game's report nor mains.
    second = _startgg(_event([dict(played), dict(guessed)], videogame=4242),
                      catalog, database, logs)
    assert second["summary"]["game"] is None
    assert _sources(second["summary"], none=2)
    # No videogame: the loaded one.
    third = _startgg(_event([dict(played)], videogame=None), catalog, None,
                     logs)
    assert third["summary"]["game"] == "game2"
    assert third["results"]["sets"][0]["c2"] == "Ryu"
    assert third["results"]["sets"][0]["autona"]["icons"] == {
        "ryu": "base_files/icon/ryu_0.png"}
    # Neither: no character.
    catalog.Current = lambda: GameAssetSnapshot()
    fourth = _startgg(_event([dict(played)], videogame=None), catalog, None,
                      logs)
    assert fourth["summary"]["game"] is None
    assert _sources(fourth["summary"], none=1)


@pytest.mark.parametrize("url, raised, code, message", [
    (None, None, archive.INVALID, results.BAD_URL),
    ("https://example.com/event", None, archive.INVALID, results.BAD_URL),
    ("https://www.start.gg/x", ValueError("no slug"), archive.INVALID,
     results.BAD_URL),
    ("https://www.start.gg/x", LookupError("none"), archive.NOT_FOUND,
     results.NO_EVENT),
    ("https://www.start.gg/x", RuntimeError("page 2"),
     archive.FETCH_FAILED, results.STARTGG_FAILED),
    ("https://www.start.gg/x", TimeoutError("deadline"),
     archive.FETCH_FAILED, results.STARTGG_FAILED),
])
def test_an_event_start_gg_does_not_give_is_refused(
        logs, catalog, url, raised, code, message):
    def fetch(_url):
        raise raised

    with pytest.raises(ArchiveError) as refused:
        results.Prepare({"kind": "startgg", "url": url}, fetchEvent=fetch,
                        directory=str(logs), catalog=catalog, database=NOBODY)
    assert (refused.value.code, refused.value.message) == (code, message)


def test_an_event_with_no_completed_set_is_refused(logs, catalog):
    with pytest.raises(ArchiveError) as refused:
        _startgg(_event([]), catalog, None, logs)
    assert (refused.value.code, refused.value.message) == (
        archive.NOT_FOUND, results.NO_STARTGG_SETS)


# ── the JSON kind ────────────────────────────────────────────────────────

def test_json_results_keep_their_own_characters(logs, catalog, database):
    # A record that would match: JSON results take none.
    _write(logs, "20261114", _record("a", "Kappa", "Lambda", (3, 1),
                                     [("mario", "luigi")]))
    given = {"event": "Fake Cup", "bracket_url": "https://x", "sets": [
        {"round": "Winners Final", "p1": "Kappa", "s1": 3, "p2": "Lambda",
         "s2": 1, "c1": "ホムラ/ヒカリ"},
        {"round": None, "p1": "Xi", "s1": 2, "p2": "Lambda", "s2": 0,
         "id": "x1", "extra": [1]},
    ]}

    prepared = results.Prepare({"kind": "json", "results": given},
                               directory=str(logs), catalog=catalog,
                               database=database)

    assert prepared["results"]["event"] == "Fake Cup"
    assert prepared["results"]["bracket_url"] == "https://x"
    assert prepared["results"]["sets"][0] == given["sets"][0]
    assert prepared["results"]["sets"][1] == dict(
        given["sets"][1], c1="Zelda", char_source="mains",
        autona={"game": "tgame", "c1": ["zelda"], "c2": [],
                "icons": _icons("zelda")})
    assert prepared["summary"]["records"] == 0
    assert _sources(prepared["summary"], given=1, mains=1)
    # The page's object is not changed.
    assert "c1" not in given["sets"][1]


def test_a_json_sets_own_autona_key_is_not_taken(logs, catalog,
                                                  database):
    forged = {"game": "../../etc", "c1": ["evil"], "icons": {"evil": "x"}}
    given = {"sets": [
        # Its own characters: kept, the key dropped.
        {"p1": "Kappa", "s1": 2, "p2": "Lambda", "s2": 0, "c1": "Peach",
         "autona": forged},
        # None of its own: composed, the key Prepare's.
        {"p1": "Xi", "s1": 2, "p2": "Nobody", "s2": 0, "autona": forged},
        # Only the key: no character.
        {"p1": "Pi", "s1": 2, "p2": "Rho", "s2": 0, "autona": forged},
    ]}

    sets = results.Prepare({"kind": "json", "results": given},
                           directory=str(logs), catalog=catalog,
                           database=database)["results"]["sets"]

    assert sets[0] == {"p1": "Kappa", "s1": 2, "p2": "Lambda", "s2": 0,
                       "c1": "Peach"}
    assert sets[1]["autona"] == {"game": "tgame", "c1": ["zelda"], "c2": [],
                                 "icons": _icons("zelda")}
    assert sets[2] == {"p1": "Pi", "s1": 2, "p2": "Rho", "s2": 0}


@pytest.mark.parametrize("given", [None, {"sets": []}, {"sets": [{"p1": 1}]},
                                   "sets"])
def test_json_results_vodsplit_would_refuse_are_refused(catalog, given):
    with pytest.raises(ArchiveError) as refused:
        results.Prepare({"kind": "json", "results": given}, catalog=catalog,
                        database=NOBODY)
    assert refused.value.code == archive.INVALID


# ── the catalog ──────────────────────────────────────────────────────────

def test_the_characters_display_names_are_a_loads(assets):
    # The load's names (the S1a golden, recorded with the program locale
    # zh-TW): GameCharacters names a character as a load would.
    recorded = json.loads((GAME_ASSETS / "golden.json").read_text(
        encoding="utf-8"))
    golden = recorded["scenarios"]["tgame"]["characters"]
    table = _scanned(recorded["locale"]["program"],
                     recorded["locale"]["remapping"]).GameCharacters("tgame")

    assert {name: table[name]["display_name"] for name in golden} == {
        name: character["display_name"]
        for name, character in golden.items()}
    assert table["Bowser"]["codename"] == "koopa"
    assert table["Bowser"]["smashgg_name"] == "Bowser"
    assert _scanned().GameCharacters("nogame") == {}


def test_a_characters_icon_is_its_lowest_skins_file(assets):
    table = _scanned().GameCharacters("tgame")

    # chara_2_ + codename + _0, then the skin: the lowest (00 of 00..02).
    assert table["Luigi"]["icon"] == "base_files/icon/chara_2_luigi_00.png"
    assert table["Bowser"]["icon"] == "base_files/icon/chara_2_koopa_00.png"
    # No file of its own: none (not another character's that starts the
    # same).
    assert table["NoArt"]["icon"] is None
    assert _scanned().GameCharacters("game2")["Ryu"]["icon"] == \
        "base_files/icon/ryu_0.png"


def test_a_game_with_no_icon_pack_names_no_icon(assets):
    shutil.rmtree(assets / "user_data" / "games" / "game2" / "base_files"
                  / "icon")

    assert _scanned().GameCharacters("game2")["Ryu"]["icon"] is None


def test_start_gg_names_a_character_id(assets):
    catalog = _scanned()

    assert catalog.StartGGCharacterName(1) == "Mario"
    assert catalog.StartGGCharacterName("3") == "Bowser"
    assert catalog.StartGGCharacterName(77) is None


# ── start.gg: an event's completed sets ──────────────────────────────────

def _provider():
    from src.TournamentDataProvider import StartGGDataProvider as provider
    return provider


EXPECTED_SETS = [
    {"id": 101, "round": "Winners Final", "p1": "Kappa", "s1": 3,
     "p2": "Lambda", "s2": 1,
     "games": [{"c1": 1, "c2": 3}, {"c1": 1, "c2": 2},
               {"c1": 3, "c2": None}, {"c1": 4, "c2": 2}]},
    {"id": 106, "round": "Losers Final", "p1": "Mu", "s1": 3, "p2": "Nu",
     "s2": 2, "games": []},
    {"id": 107, "round": "Grand Final", "p1": "Kappa", "s1": 3, "p2": "Mu",
     "s2": 0, "games": [{"c1": None, "c2": None}]},
]


def test_start_gg_answers_become_the_events_completed_sets():
    # Set 101's first game has a selection of another type and one of no
    # type before the characters: only CHARACTER selections are taken.
    parsed = _provider().ParseEventResults(PAGES, "https://www.start.gg/e")

    assert parsed == {
        "tournament": "Fake Cup", "event": "Ultimate Singles",
        "url": "https://www.start.gg/e", "videogame": 1386,
        "startAt": 1794884400, "endAt": 1794913200, "sets": EXPECTED_SETS}


@pytest.fixture
def fanout(monkeypatch):
    executor = TaskExecutor()
    monkeypatch.setattr(TaskExecutor, "instance", executor)
    try:
        yield executor
    finally:
        executor.shutdown(WAIT)


BRACKETS_ID = "88"
SLUG_ANSWER = {"data": {"event": {
    "slug": "tournament/fake-cup/event/ultimate-singles"}}}


class _Answers:
    """_QueryRequests: the fixture's pages by page number, `fail` maps a
    page to how many times it answers {} first; `slug` the answer to an
    admin URL's brackets id."""

    def __init__(self, pages, fail=None, slug=None):
        self.pages = pages
        self.fail = dict(fail or {})
        self.slug = SLUG_ANSWER if slug is None else slug
        self.asked = []
        self.lock = threading.Lock()

    def __call__(self, url, type_, headers, jsonParams, params, timeout,
                 cancel_event=None):
        variables = jsonParams["variables"]
        if jsonParams["operationName"] == "TournamentSlugQuery":
            with self.lock:
                self.asked.append(("brackets", variables["id"]))
            return self.slug
        page = variables["page"]
        with self.lock:
            self.asked.append((variables["eventSlug"], page,
                               variables["perPage"]))
            if self.fail.get(page):
                self.fail[page] -= 1
                return {}
        return self.pages[page - 1] if page <= len(self.pages) else {}


URL = "https://www.start.gg/tournament/fake-cup/event/ultimate-singles"
SLUG = "tournament/fake-cup/event/ultimate-singles"


def test_every_page_of_an_event_is_fetched(fanout, monkeypatch):
    provider = _provider()
    answers = _Answers(PAGES, fail={2: 1})
    monkeypatch.setattr(provider, "_QueryRequests", answers)

    parsed = provider.FetchEventResults(URL + "/overview")

    assert parsed["sets"] == EXPECTED_SETS
    assert parsed["url"] == "https://www.start.gg/" + SLUG
    # Page 2 failed once and was asked again.
    assert sorted(answers.asked) == [(SLUG, 1, 32), (SLUG, 2, 32),
                                     (SLUG, 2, 32)]
    assert StatusService.Snapshot()["jobs"] == []


def test_a_page_that_never_answers_fails_the_fetch(fanout, monkeypatch):
    provider = _provider()
    monkeypatch.setattr(provider, "_QueryRequests",
                        _Answers(PAGES, fail={2: 2}))

    with pytest.raises(RuntimeError):
        provider.FetchEventResults(URL)
    assert StatusService.Snapshot()["jobs"] == []


def test_a_first_page_that_does_not_answer_fails_the_fetch(fanout,
                                                           monkeypatch):
    provider = _provider()
    answers = _Answers(PAGES, fail={1: 1})
    monkeypatch.setattr(provider, "_QueryRequests", answers)

    with pytest.raises(RuntimeError):
        provider.FetchEventResults(URL)
    # Not asked again: QueryRequests retried it already.
    assert answers.asked == [(SLUG, 1, 32)]


@pytest.mark.parametrize("answer", [
    {"data": {"event": None}},
    # With errors too: still no such event.
    {"errors": [{"message": "not found"}], "data": {"event": None}},
])
def test_an_event_start_gg_does_not_have_is_looked_up_in_vain(
        fanout, monkeypatch, answer):
    provider = _provider()
    monkeypatch.setattr(provider, "_QueryRequests", _Answers([answer]))

    with pytest.raises(LookupError):
        provider.FetchEventResults(URL)
    # The footer's fetch is gone.
    assert StatusService.Snapshot()["jobs"] == []


ADMIN_URL = ("https://www.start.gg/admin/tournament/fake-cup/brackets/"
             + BRACKETS_ID + "/99")


def test_an_admin_url_is_resolved_before_the_pages(fanout, monkeypatch):
    provider = _provider()
    answers = _Answers(PAGES)
    monkeypatch.setattr(provider, "_QueryRequests", answers)

    parsed = provider.FetchEventResults(ADMIN_URL)

    assert parsed["url"] == "https://www.start.gg/" + SLUG
    assert answers.asked[0] == ("brackets", BRACKETS_ID)
    assert sorted(answers.asked[1:]) == [(SLUG, 1, 32), (SLUG, 2, 32)]


@pytest.mark.parametrize("slug, raised", [
    # start.gg did not answer (the network): not a bad URL.
    ({}, RuntimeError),
    ({"data": {"event": None}}, LookupError),
    ({"errors": [{"message": "x"}], "data": {"event": None}}, LookupError),
])
def test_an_admin_url_start_gg_does_not_resolve_is_no_fetch(
        fanout, monkeypatch, slug, raised):
    provider = _provider()
    answers = _Answers(PAGES, slug=slug)
    monkeypatch.setattr(provider, "_QueryRequests", answers)

    with pytest.raises(raised):
        provider.FetchEventResults(ADMIN_URL)
    assert answers.asked == [("brackets", BRACKETS_ID)]
    assert StatusService.Snapshot()["jobs"] == []


def test_a_fetch_past_its_deadline_gives_up(fanout, monkeypatch):
    provider = _provider()
    monkeypatch.setattr(provider, "ARCHIVE_FETCH_DEADLINE_SECS", 0.3)
    released = []

    def answer(url, type_, headers, jsonParams, params, timeout,
               cancel_event=None):
        if jsonParams["variables"]["page"] == 1:
            return PAGES[0]
        # Page 2 answers nothing until the fetch gives up on it.
        released.append(cancel_event.wait(WAIT))
        return {}

    monkeypatch.setattr(provider, "_QueryRequests", answer)
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        provider.FetchEventResults(URL)
    assert time.monotonic() - started < WAIT
    assert StatusService.Snapshot()["jobs"] == []
    # The request still out was told to stop.
    for _ in range(200):
        if released:
            break
        time.sleep(0.05)
    assert released == [True]


def test_a_cancelled_request_is_not_asked_again(monkeypatch):
    provider = _provider()
    cancel = threading.Event()
    asked = []

    def failing(url, **_kwargs):
        asked.append(url)
        cancel.set()
        raise ConnectionError("down")

    started = time.monotonic()
    assert provider._QueryRequests("https://x", failing, {}, None, None, 1,
                                   cancel_event=cancel) == {}
    assert asked == ["https://x"]
    # The 1 s backoff was cut short.
    assert time.monotonic() - started < 1


def test_a_url_naming_no_event_is_no_fetch(fanout, monkeypatch):
    provider = _provider()
    answers = _Answers(PAGES)
    monkeypatch.setattr(provider, "_QueryRequests", answers)

    with pytest.raises(ValueError):
        provider.FetchEventResults("https://www.start.gg/tournament/fake-cup")
    assert answers.asked == []


# ── the v1 routes ────────────────────────────────────────────────────────

@pytest.fixture
def prepared_app(catalog, database, logs, monkeypatch):
    """The routes' core: this test's catalog, player database and match
    log folder."""
    monkeypatch.setattr(GameAssetCatalog, "instance", catalog)
    monkeypatch.setattr(PlayerDB, "instance", database)
    monkeypatch.setattr(MatchLogService, "instance",
                        SimpleNamespace(directory=str(logs)))
    return logs


def test_the_page_prepares_the_results_through_the_routes(
        page, prepared_app, monkeypatch):
    from src import AutonaWebOperations

    logs = prepared_app
    _write(logs, "20261114",
           _record("r1", "Kappa", "Lambda", (3, 1), [("mario", "luigi")],
                   tournament="Fake Cup"))

    days = _data(page.get(f"{BASE}/match-logs"))
    assert days == {"days": [{"date": "20261114", "records": 1,
                              "tournaments": [{"name": "Fake Cup",
                                               "records": 1}]}]}

    prepared = _data(page.post(f"{BASE}/results:prepare", json={
        "kind": "matchlog", "date": "20261114", "tournament": "Fake Cup"}))
    assert prepared["results"]["sets"][0]["c1"] == "マリオ"
    assert prepared["summary"]["sources"]["scoreboard"] == 1

    asked = []

    def fetch(url):
        asked.append(url)
        return _event([_set("Winners Final", "Kappa", 3, "Lambda", 1, 1)])

    monkeypatch.setattr(AutonaWebOperations, "FetchEventResults", fetch)
    fetched = _data(page.post(f"{BASE}/results:prepare", json={
        "kind": "startgg", "url": " " + URL + " "}))
    assert asked == [URL]
    assert fetched["results"]["sets"][0]["char_source"] == "scoreboard"

    given = _data(page.post(f"{BASE}/results:prepare", json={
        "kind": "json", "results": {"sets": [
            {"p1": "Xi", "s1": 2, "p2": "Nu", "s2": 0}]}}))
    assert given["summary"]["sources"]["mains"] == 1


def test_the_routes_refuse_what_core_refuses(page, prepared_app,
                                             monkeypatch):
    from src import AutonaWebOperations

    url = f"{BASE}/results:prepare"
    _refusal(page.post(url, json={"kind": "csv"}), 400, "INVALID_ARGUMENT",
             archive.INVALID)
    message = _refusal(page.post(url, json={"kind": "matchlog",
                                            "date": "20261114"}),
                       404, "NOT_FOUND", archive.NOT_FOUND)
    assert message == results.NO_RECORDS

    def unanswered(_url):
        raise RuntimeError("start.gg did not answer")

    monkeypatch.setattr(AutonaWebOperations, "FetchEventResults", unanswered)
    message = _refusal(page.post(url, json={"kind": "startgg", "url": URL}),
                       503, "UNAVAILABLE", archive.FETCH_FAILED)
    assert message == results.STARTGG_FAILED


def test_prepared_results_go_through_the_job_into_its_sets(
        page, service, work, prepared_app):
    prepared = _data(page.post(f"{BASE}/results:prepare", json={
        "kind": "json", "results": {"event": "Fake Cup", "sets": [
            {"round": "Grand Final", "p1": "Xi", "s1": 3, "p2": "Kappa",
             "s2": 1}]}}))
    autona = {"game": "tgame", "c1": ["zelda"], "c2": ["peach"],
              "icons": _icons("zelda", "peach")}
    assert prepared["results"]["sets"][0]["autona"] == autona

    created = _data(page.post(f"{BASE}/jobs", json={
        "source": SOURCE, "results": prepared["results"], "event": None}),
        201)
    jobId = created["job"]["id"]
    done = _ended(service, jobId)

    assert done["state"] == "done"
    stored = json.loads((work / jobId / "results.json").read_text(
        encoding="utf-8"))
    assert stored == prepared["results"]
    _job, sets = service.Job(jobId)
    assert sets["sets"][0]["set"]["autona"] == autona
    assert sets["sets"][0]["set"]["char_source"] == "mains"
