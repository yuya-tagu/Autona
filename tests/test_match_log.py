"""The match log (ADR-006 A2): board 1's published changes as records, one
line per change, in user_data/match_log/YYYYMMDD.jsonl.

Driven through the operations the scoreboard has -- a set loaded from the
(fake) tournament, the score set, nudged and reset, the names and the
characters edited, the teams swapped -- on board 1 as the headless shell has
it (every write published) and as the desktop has it (held until 反映). The
service under test is a fresh one on a temporary directory and a clock the
test moves; the session's is stopped meanwhile.

What the rules are, and why, is the module docstring of
src/core/MatchLogService.py.
"""
import json
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest
from loguru import logger

from src.core.Actions import AutonaActions
from src.core.MatchLogService import MatchLogService
from src.core.PlayerService import PlayerService
from src.core.SetLoadService import SetLoadService
from src.core.StateManager import StateManager
from src.core.TournamentInfoService import TournamentInfoService
from tests.test_set_follow import _drain, _goHeadless, _setFollow
from tests.test_set_follow_flow import (  # noqa: F401 (fixtures)
    playerDB, provider, settings)
from tests.test_task_executor import _calls_in_order, _function

JST = timezone(timedelta(hours=9))

LINE_KEYS = ["id", "board", "started_at", "updated_at", "closed",
             "tournament", "set_id", "phase", "round", "p1", "p2", "game",
             "c1", "c2", "score", "games"]

NO_CHARACTERS = {"c1": "", "c2": ""}


class _Clock:
    """The service's clock: a local time with its offset, moved by the
    test."""

    def __init__(self):
        self.now = datetime(2026, 11, 16, 19, 30, 12, tzinfo=JST)

    def __call__(self):
        return self.now

    def tick(self, seconds=1):
        self.now += timedelta(seconds=seconds)

    def stamp(self):
        return self.now.isoformat(timespec="seconds")


class _Log:
    def __init__(self, service, directory, clock):
        self.service = service
        self.directory = directory
        self.clock = clock

    def id(self, stamp, n=1):
        return f"{stamp}-{n}-{self.service._idSuffix}"

    def files(self):
        assert self.service.Flush(5)
        return sorted(path.name for path in self.directory.glob("*.jsonl"))

    def lines(self):
        assert self.service.Flush(5)
        lines = []
        for path in sorted(self.directory.glob("*.jsonl")):
            lines += [json.loads(line) for line in
                      path.read_text(encoding="utf-8").splitlines()]
        return lines

    def records(self):
        """Each id's last line, in the order the records began."""
        last = {}
        for line in self.lines():
            last[line["id"]] = line
        return list(last.values())


@pytest.fixture
def board(autonaApp, monkeypatch):
    """Board 1 as the headless shell has it: every write published at
    once. With HARNESS_GAME's characters (codenames mario / luigi /
    peach)."""
    _goHeadless(autonaApp, monkeypatch)
    autonaApp.loadCharacters()
    autonaApp.settle()
    try:
        yield
    finally:
        autonaApp.dropAllBoards()
        autonaApp.settle()


@pytest.fixture
def desktop(autonaApp):
    """Board 1 as the desktop has it: held until 反映."""
    autonaApp.ready(1)
    autonaApp.loadCharacters()
    try:
        yield
    finally:
        autonaApp.dropAllBoards()
        autonaApp.settle()


@pytest.fixture
def log(autonaApp, tmp_path):
    """A match log of the test's own, started on the board as it is."""
    harness = MatchLogService.instance
    harness.Stop()
    clock = _Clock()
    directory = tmp_path / "match_log"
    service = MatchLogService(directory=str(directory), clock=clock)
    service.Start()
    try:
        yield _Log(service, directory, clock)
    finally:
        service.Shutdown(5)
        harness.Start()


@pytest.fixture
def logged():
    """The log's messages, INFO and up."""
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="INFO")
    try:
        yield lines
    finally:
        logger.remove(sink)


def _name(team, tag):
    assert PlayerService(f"score.1.team.{team}.player.1").Edit(
        {"name": tag}) is None


def _character(team, name):
    assert PlayerService(f"score.1.team.{team}.player.1").EditCharacter(
        1, name) is None


def _game(team, delta=1):
    assert AutonaActions.instance.AdjustScore(1, team, delta) is not None


def _reset():
    assert AutonaActions.instance.ResetScore(1)


def _apply(autonaApp):
    AutonaActions.instance.ApplyChanges(1)
    autonaApp.settle()


def _match(p1="ALPHA", p2="BRAVO", c1="Mario", c2="Luigi"):
    _name(1, p1)
    _name(2, p2)
    _character(1, c1)
    _character(2, c2)


def _summary(records):
    return [(r["p1"], r["p2"], r["score"], len(r["games"]), r["closed"])
            for r in records]


# ── a match typed by hand ────────────────────────────────────────────────

def test_a_match_typed_by_hand_is_one_record(board, log):
    _match()
    # Both names and no game yet: nothing written.
    assert log.lines() == []

    log.clock.tick()
    first = log.clock.stamp()
    _game(1)
    log.clock.tick()
    # A character changed after the first game: the next game's.
    _character(2, "Peach")
    log.clock.tick()
    second = log.clock.stamp()
    _game(2)
    log.clock.tick()
    third = log.clock.stamp()
    _game(1)
    log.clock.tick()
    closed = log.clock.stamp()
    _reset()

    lines = log.lines()
    assert [list(line) for line in lines] == [LINE_KEYS] * len(lines)
    assert {line["id"] for line in lines} == {log.id("20261116-193013")}
    assert log.files() == ["20261116.jsonl"]
    # One line per change: three games, the reset closing the record. The
    # character waited for the second game.
    assert [(line["score"], line["c2"], line["closed"]) for line in lines] \
        == [([1, 0], "luigi", False), ([1, 1], "peach", False),
            ([2, 1], "peach", False), ([2, 1], "peach", True)]
    assert [line["updated_at"] for line in lines][-1] == closed
    record = lines[-1]
    assert record["board"] == 1
    assert record["started_at"] == first
    assert record["set_id"] is None
    assert (record["p1"], record["p2"]) == ("ALPHA", "BRAVO")
    assert record["games"] == [
        {"c1": "mario", "c2": "luigi", "at": first},
        {"c1": "mario", "c2": "peach", "at": second},
        {"c1": "mario", "c2": "peach", "at": third}]


def test_the_next_match_typed_one_name_at_a_time(board, log):
    _match()
    _game(1)
    _game(1)
    _game(2)

    # One name of the next match: the record is played, so it keeps its
    # name -- a correction or the next match, not known yet.
    _name(1, "CHARLIE")
    assert log.records()[-1]["p1"] == "ALPHA"
    assert len(log.lines()) == 3

    # The other one too: the next match. Its score is the last one's, a
    # baseline: no game, nothing written.
    _name(2, "DELTA")
    assert _summary(log.records()) == [("ALPHA", "BRAVO", [2, 1], 3, True)]

    # The reset and its first game: a record of its own.
    _reset()
    _character(1, "Peach")
    _game(2)
    records = log.records()
    assert [r["id"] for r in records] == [
        log.id("20261116-193012", 1), log.id("20261116-193012", 2)]
    assert _summary(records) == [
        ("ALPHA", "BRAVO", [2, 1], 3, True),
        ("CHARLIE", "DELTA", [0, 1], 1, False)]
    assert records[1]["games"][0]["c1"] == "peach"


def test_the_next_match_typed_after_a_reset(board, log):
    _match()
    _game(1)
    _game(1)
    _reset()
    _name(1, "CHARLIE")
    _name(2, "DELTA")
    _game(1)

    assert _summary(log.records()) == [
        ("ALPHA", "BRAVO", [2, 0], 2, True),
        ("CHARLIE", "DELTA", [1, 0], 1, False)]


def test_a_best_of_one_reset_then_renamed_one_at_a_time(board, log):
    # Review (a): a best-of-1, the reset, the next players typed one by
    # one. The reset from one game looks like a game taken back, so it is
    # set aside -- and put back once the next match shows: the first name.
    _match()
    _game(1)
    _reset()
    assert _summary(log.records()) == [("ALPHA", "BRAVO", [0, 0], 0, False)]

    _name(1, "CHARLIE")
    _name(2, "DELTA")
    _game(2)

    records = log.records()
    assert _summary(records) == [
        ("ALPHA", "BRAVO", [1, 0], 1, True),
        ("CHARLIE", "DELTA", [0, 1], 1, False)]
    assert records[0]["games"][0]["c1"] == "mario"


def test_the_winner_staying_on_with_the_reset_in_the_same_apply(
        autonaApp, desktop, log):
    # Review (b): the next player and the reset in one 反映, the winner
    # staying: the best-of-1 stays the last match's.
    _match()
    _game(1)
    _apply(autonaApp)
    _name(2, "CHARLIE")
    _reset()
    _apply(autonaApp)
    _game(1)
    _apply(autonaApp)

    assert _summary(log.records()) == [
        ("ALPHA", "BRAVO", [1, 0], 1, True),
        ("ALPHA", "CHARLIE", [1, 0], 1, False)]


@pytest.mark.parametrize("order", ["name, then reset", "reset, then name"])
def test_the_winner_staying_on_headless(board, log, order):
    _match()
    _game(1)
    if order == "name, then reset":
        _name(2, "CHARLIE")
        _reset()
    else:
        _reset()
        _name(2, "CHARLIE")
    _game(2)

    assert _summary(log.records()) == [
        ("ALPHA", "BRAVO", [1, 0], 1, True),
        ("ALPHA", "CHARLIE", [0, 1], 1, False)]


def test_a_game_set_aside_is_put_back_when_another_set_is_loaded(board, log):
    _match()
    _game(1)
    _reset()
    StateManager.Set("score.1.set_id", 2002)

    records = log.records()
    assert _summary(records) == [("ALPHA", "BRAVO", [1, 0], 1, True)]
    assert records[0]["set_id"] is None


def test_a_name_corrected_mid_set_is_taken_in_with_the_next_game(board, log):
    # A typo fixed before the first game is simply the name.
    _match(p1="ALHPA")
    _name(1, "ALPH")
    _game(1)
    assert log.records()[-1]["p1"] == "ALPH"

    # Fixed after it: the record waits for a game under the new name.
    _name(1, "ALPHA")
    assert log.records()[-1]["p1"] == "ALPH"
    _game(2)

    [record] = log.records()
    assert (record["p1"], record["score"], len(record["games"])) == (
        "ALPHA", [1, 1], 2)


def test_the_round_waits_for_the_next_game_and_is_left_to_the_next_match(
        board, log):
    AutonaActions.instance.SetMatchField(1, "match", "Winners Round 1")
    _match()
    _game(1)
    # Changed mid-set: taken in with the next game.
    AutonaActions.instance.SetMatchField(1, "match", "Winners Round 2")
    assert log.records()[-1]["round"] == "Winners Round 1"
    _game(2)
    assert log.records()[-1]["round"] == "Winners Round 2"

    # The next match's round and characters, typed before its players: the
    # closed record does not say them.
    AutonaActions.instance.SetMatchField(1, "match", "Winners Semi-Final")
    _character(1, "Peach")
    _name(1, "CHARLIE")
    _name(2, "DELTA")

    records = log.records()
    assert [(r["round"], r["c1"], r["closed"]) for r in records] == [
        ("Winners Round 2", "mario", True)]
    _reset()
    _game(1)
    assert [(r["round"], r["c1"]) for r in log.records()] == [
        ("Winners Round 2", "mario"), ("Winners Semi-Final", "peach")]


def test_a_game_takes_the_characters_from_before_its_publish(board, log):
    _match()
    _game(1)
    # The score and the next game's character in one publish.
    with StateManager.SaveBlock():
        _character(2, "Peach")
        _game(1)

    [record] = log.records()
    assert [(g["c1"], g["c2"]) for g in record["games"]] == [
        ("mario", "luigi"), ("mario", "luigi")]
    assert record["c2"] == "peach"


def test_a_score_taken_back_takes_its_game_back(board, log):
    _match()
    _game(1)
    _game(2)
    _game(2, -1)
    assert log.records()[-1]["games"] == [
        {"c1": "mario", "c2": "luigi", "at": log.clock.stamp()}]

    # From one game to 0-0 is a correction too, not a new match.
    _game(1, -1)
    [record] = log.records()
    assert (record["score"], record["games"], record["closed"]) == (
        [0, 0], [], False)

    # A game next: the one taken back was a mis-click, not put back later.
    _game(2)
    _name(1, "CHARLIE")
    _name(2, "DELTA")
    assert _summary(log.records()) == [("ALPHA", "BRAVO", [0, 1], 1, True)]


def test_names_and_a_score_typed_together_are_that_many_games(board, log):
    with StateManager.SaveBlock():
        _match()
        AutonaActions.instance.SetScore(1, 1, 2)
    at = log.clock.stamp()

    # By hand: the score is games, the characters known for the last one.
    [record] = log.records()
    assert record["score"] == [2, 0]
    assert record["games"] == [
        dict(NO_CHARACTERS, at=at),
        {"c1": "mario", "c2": "luigi", "at": at}]

    # Two games in one publish are two games.
    with StateManager.SaveBlock():
        AutonaActions.instance.SetScore(1, 1, 3)
        AutonaActions.instance.SetScore(1, 2, 1)
    [record] = log.records()
    assert record["score"] == [3, 1]
    assert record["games"][2:] == [
        {"c1": "mario", "c2": "luigi", "at": at}] * 2


def test_the_swap_only_swaps_the_sides(board, log):
    _match()
    _game(1)
    assert AutonaActions.instance.SwapTeams(1)
    assert StateManager.Get("score.1.team.1.player.1.name") == "BRAVO"
    _game(1)

    [record] = log.records()
    assert (record["p1"], record["p2"], record["c1"], record["c2"]) == (
        "BRAVO", "ALPHA", "luigi", "mario")
    assert record["score"] == [1, 1]
    assert [(g["c1"], g["c2"]) for g in record["games"]] == [
        ("luigi", "mario"), ("luigi", "mario")]


def test_a_cleared_board_changes_nothing(board, log):
    _match()
    _game(1)
    _game(2)
    assert AutonaActions.instance.ClearPlayers(1)
    [record] = log.records()
    assert (record["p1"], record["p2"], record["c1"]) == (
        "ALPHA", "BRAVO", "mario")
    assert record["closed"] is False


def test_a_character_the_catalog_does_not_know_is_its_name(board, log):
    _match()
    StateManager.Set("score.1.team.2.player.1.character",
                     {1: {"name": "Guest Fighter"}})
    _game(1)

    [record] = log.records()
    assert (record["c2"], record["games"][0]["c2"]) == (
        "Guest Fighter", "Guest Fighter")


def _switchGame(codename):
    # A game load: the board's characters go with it.
    with StateManager.SaveBlock():
        StateManager.Set("game.codename", codename)
        for team in (1, 2):
            StateManager.Set(f"score.1.team.{team}.player.1.character", {})


def test_another_game_clears_the_characters(board, log):
    codename = StateManager.Get("game.codename")
    _match()
    try:
        # A game switch clears the board's characters: the record's are
        # not this game's either. Before its first game, the record is the
        # new game's.
        _switchGame("anothergame")
        _game(1)
        # Read before the game is put back (another switch).
        [record] = log.records()
    finally:
        StateManager.Set("game.codename", codename)

    assert record["game"] == "anothergame"
    assert (record["c1"], record["c2"]) == ("", "")
    assert record["games"] == [dict(NO_CHARACTERS, at=log.clock.stamp())]


def test_a_record_with_a_game_keeps_its_game(board, log):
    codename = StateManager.Get("game.codename")
    _match()
    _game(1)
    at = log.clock.stamp()
    try:
        # The set is over; the next set's game is loaded before it is.
        _switchGame("anothergame")
        [record] = log.records()
    finally:
        StateManager.Set("game.codename", codename)

    assert codename and record["game"] == codename
    assert (record["c1"], record["c2"]) == ("", "")
    assert record["games"] == [{"c1": "mario", "c2": "luigi", "at": at}]


def test_a_record_with_no_game_loaded_names_none(board, log):
    codename = StateManager.Get("game.codename")
    StateManager.Set("game.codename", "")
    try:
        _match()
        _game(1)
        [record] = log.records()
    finally:
        StateManager.Set("game.codename", codename)

    assert record["game"] is None


# ── a set loaded from the tournament ─────────────────────────────────────

def test_a_set_load_begins_a_record_with_the_set(
        autonaApp, provider, settings, playerDB, board, log):
    _setFollow().LoadSet({"id": 1001, "auto_update": "set"})
    _drain(autonaApp)
    assert StateManager.Get("score.1.team.1.player.1.name") == "ALPHA"
    # Loaded at 0-0: nothing written until a game.
    assert log.lines() == []

    _game(1)
    round1001 = StateManager.Get("score.1.match")
    phase = StateManager.Get("score.1.phase")
    tournament = StateManager.Get("tournamentInfo.displayTournamentName") \
        or StateManager.Get("tournamentInfo.tournamentName") or ""

    _setFollow().LoadSet({"id": 1002, "auto_update": "set"})
    _drain(autonaApp)
    assert StateManager.Get("score.1.team.1.player.1.name") == "CHARLIE"
    _game(2)

    records = log.records()
    assert [(r["set_id"], r["p1"], r["p2"], r["c1"], r["c2"], r["score"],
             r["closed"]) for r in records] == [
        ("1001", "ALPHA", "BRAVO", "mario", "luigi", [1, 0], True),
        ("1002", "CHARLIE", "DELTA", "peach", "mario", [0, 1], False)]
    assert records[0]["round"] == round1001 != ""
    assert records[0]["phase"] == phase
    assert records[0]["tournament"] == tournament
    assert records[1]["round"] == StateManager.Get("score.1.match")


def test_the_same_set_loaded_again_keeps_its_id(
        autonaApp, provider, settings, playerDB, board, log):
    # Review 4: the players cleared (the set forgotten, as the board's
    # clear does) and the set loaded again. Its load clears the score (a
    # reset, a new record) on the id the board still shows, then fills the
    # board; the fake tournament reports 1-0 this time: with the names, a
    # baseline, no game.
    _setFollow().LoadSet({"id": 1001, "auto_update": "set"})
    _drain(autonaApp)
    _game(1)
    _game(1)
    assert AutonaActions.instance.ClearPlayers(1)
    SetLoadService(1).ClearSetTracking()
    _setFollow().LoadSet({"id": 1001, "auto_update": "set"})
    _drain(autonaApp)
    assert StateManager.Get("score.1.team.1.score") == 1
    _game(2)

    assert [(r["set_id"], r["p1"], r["score"], len(r["games"]), r["closed"])
            for r in log.records()] == [
        ("1001", "ALPHA", [2, 0], 2, True),
        ("1001", "ALPHA", [1, 1], 1, False)]


def test_a_match_typed_over_the_set_the_board_still_shows_has_no_set(
        autonaApp, provider, settings, playerDB, board, log):
    # The reset on a loaded set keeps the id on the board, like the set
    # loaded again; the next players typed one at a time are another pair
    # than the set's: no set.
    _setFollow().LoadSet({"id": 1001, "auto_update": "set"})
    _drain(autonaApp)
    _game(1)
    _game(1)
    _reset()
    _name(1, "CHARLIE")
    _name(2, "DELTA")
    _game(1)
    assert StateManager.Get("score.1.set_id") == 1001

    assert [(r["set_id"], r["p1"], r["closed"]) for r in log.records()] == [
        ("1001", "ALPHA", True), (None, "CHARLIE", False)]


@pytest.mark.parametrize("p1, p2, setId", [
    ("CHARLIE", "DELTA", None),
    # The winner staying on: one of the set's two is not the pair.
    ("ALPHA", "CHARLIE", None),
    # The set's two, sides swapped by typing: the same pair.
    ("BRAVO", "ALPHA", "1001"),
])
def test_after_a_reset_on_a_loaded_set_the_set_goes_with_its_pair(
        autonaApp, provider, settings, playerDB, board, log, p1, p2, setId):
    _setFollow().LoadSet({"id": 1001, "auto_update": "set"})
    _drain(autonaApp)
    _game(1)
    _game(1)
    _reset()
    with StateManager.SaveBlock():
        _name(1, p1)
        _name(2, p2)
    _game(1)

    assert [(r["set_id"], r["p1"], r["p2"], r["closed"])
            for r in log.records()] == [
        ("1001", "ALPHA", "BRAVO", True), (setId, p1, p2, False)]


# ── the desktop: what 反映 publishes ──────────────────────────────────────

def test_the_desktop_records_what_is_applied(autonaApp, desktop, log):
    _match()
    _game(1)
    _game(2)
    # Pending: nothing published, nothing recorded.
    assert StateManager.GetCommitted("score.1.team.1.score") == 0
    assert log.lines() == []

    # The names and the score in one 反映, typed by hand: two games, the
    # characters known for the last.
    _apply(autonaApp)
    [record] = log.records()
    assert record["score"] == [1, 1]
    assert [(g["c1"], g["c2"]) for g in record["games"]] == [
        ("", ""), ("mario", "luigi")]

    _game(1)
    _game(1)
    _apply(autonaApp)
    [record] = log.records()
    assert (record["score"], len(record["games"])) == ([3, 1], 4)

    # The next match typed and the score reset, then one 反映.
    _name(1, "CHARLIE")
    _name(2, "DELTA")
    _reset()
    _apply(autonaApp)
    assert [(r["p1"], r["closed"]) for r in log.records()] == [
        ("ALPHA", True)]


def test_an_edit_not_applied_is_not_taken_from_another_publish(
        autonaApp, desktop, log):
    _match()
    _game(1)
    _apply(autonaApp)
    lines = log.lines()

    # Pending on board 1, and a publish of something else.
    _name(2, "ZULU")
    _game(1)
    assert TournamentInfoService.instance.SetDisplayName("CUP #1")
    autonaApp.settle()
    assert (log.service._seen.p2, log.service._seen.score,
            log.service._seen.tournament) == ("BRAVO", (1, 0), "CUP #1")
    assert log.lines() == lines

    _apply(autonaApp)
    [record] = log.records()
    assert (record["p2"], record["score"], record["tournament"]) == (
        "ZULU", [2, 0], "CUP #1")


# ── how it follows: inside the publish, on both channels ─────────────────

def test_a_publish_does_not_wait_for_the_writer(board, log):
    # The writer held inside its write: the publishes still end (the
    # observer queues and goes; nothing it takes is held while writing).
    write = log.service._Write
    release = threading.Event()
    entered = threading.Event()

    def _held(batch):
        entered.set()
        assert release.wait(10)
        return write(batch)

    log.service._Write = _held
    worker = threading.Thread(target=lambda: (
        _match(), _game(1), _game(2), _game(1)))
    try:
        _match()
        _game(1)
        assert entered.wait(5)
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
    finally:
        release.set()
        worker.join(5)
    [record] = log.records()
    assert record["score"] == [3, 1]


def test_a_big_change_is_followed(board, log, monkeypatch):
    channel = StateManager.observers.state_big_change
    notify = channel.notify
    told = []
    monkeypatch.setattr(channel, "notify",
                        lambda: (told.append(True), notify()))
    StateManager.Set("matchLogProbe", {f"k{i}": 0 for i in range(120)})
    try:
        _match()
        # More than 100 rows (120 values changed): no delta, the big change.
        with StateManager.SaveBlock():
            for i in range(120):
                StateManager.Set(f"matchLogProbe.k{i}", 1)
            _game(1)
    finally:
        StateManager.Unset("matchLogProbe")

    assert told
    [record] = log.records()
    assert (record["score"], len(record["games"])) == ([1, 0], 1)


# ── the file, the ids, the close ─────────────────────────────────────────

def test_a_record_stays_in_the_file_of_the_day_it_began(board, log):
    log.clock.now = datetime(2026, 11, 16, 23, 59, 58, tzinfo=JST)
    _match()
    _game(1)
    log.clock.tick(10)
    _game(2)
    _reset()
    _game(1)

    assert log.files() == ["20261116.jsonl", "20261117.jsonl"]
    assert [r["id"] for r in log.records()] == [
        log.id("20261116-235958"), log.id("20261117-000008")]


def test_an_id_carries_the_process_s_draw(board, log):
    _match()
    _game(1)
    [record] = log.records()
    assert re.fullmatch(r"20261116-193012-1-[0-9a-f]{4}", record["id"])
    assert record["id"].endswith("-" + log.service._idSuffix)


def test_a_line_cut_short_is_not_joined_to_the_next(board, log):
    log.directory.mkdir()
    day = log.directory / "20261116.jsonl"
    day.write_text('{"id": "20261116-1900', encoding="utf-8")
    _match()
    _game(1)
    _game(2)

    assert log.service.Flush(5)
    text = day.read_text(encoding="utf-8").split("\n")
    assert text[0] == '{"id": "20261116-1900'
    assert [json.loads(line)["score"] for line in text[1:-1]] == [
        [1, 0], [1, 1]]
    assert text[-1] == ""


def test_the_close_ends_the_open_record_and_the_following(board, log):
    _match()
    _game(1)
    log.clock.tick()
    assert log.service.Shutdown(5)

    lines = log.lines()
    assert [line["closed"] for line in lines] == [False, True]
    assert lines[-1]["updated_at"] == log.clock.stamp()
    assert log.service._OnStatePublished not in \
        StateManager.observers.state_updated._callbacks

    _game(1)
    assert len(log.lines()) == 2


def test_the_close_puts_back_a_game_set_aside(board, log):
    # A best-of-1, the reset, then the close: the close ends the match like
    # the next one would, so the reset was not a game taken back.
    _match()
    _game(1)
    _reset()
    assert log.service.Shutdown(5)

    lines = log.lines()
    assert [(line["score"], len(line["games"]), line["closed"])
            for line in lines] == [
        ([1, 0], 1, False), ([0, 0], 0, False), ([1, 0], 1, True)]
    assert lines[-1]["games"][0]["c1"] == "mario"


def test_a_record_never_written_is_dropped_on_close(board, log):
    _match()
    assert log.service.Shutdown(5)
    assert log.lines() == []


def test_the_headless_close_writes_the_closed_record(board, log,
                                                     monkeypatch):
    from src import headless
    from src.core.ArchiveJobService import ArchiveJobService
    from src.core.StatePersistence import StatePersistence
    from src.core.TaskExecutor import TaskExecutor
    from tests.test_headless import _FakeExecutor
    _match()
    _game(1)
    monkeypatch.setattr(MatchLogService, "instance", log.service)
    monkeypatch.setattr(ArchiveJobService, "instance", None)
    monkeypatch.setattr(TaskExecutor, "instance", _FakeExecutor([]))
    monkeypatch.setattr(StateManager, "JoinExports",
                        staticmethod(lambda timeout=None: True))
    monkeypatch.setattr(StatePersistence, "Flush",
                        staticmethod(lambda timeout=None: True))

    headless.HeadlessShell(app=None).Shutdown()

    assert [(r["p1"], r["score"], r["closed"]) for r in log.lines()] == [
        ("ALPHA", [1, 0], False), ("ALPHA", [1, 0], True)]


# ── what the writer cannot write ─────────────────────────────────────────

def test_a_line_that_cannot_be_written_is_tried_again(board, log, logged):
    # The directory is a file at first, gone after the first try.
    log.service.retryDelays = (0.05, 0.05)
    log.directory.write_text("not a directory", encoding="utf-8")
    write = log.service._Write
    tries = []

    def _counted(batch):
        failed = write(batch)
        tries.append(len(failed))
        if len(tries) == 1:
            log.directory.unlink()
        return failed

    log.service._Write = _counted
    _match()
    _game(1)

    [record] = log.records()
    assert record["score"] == [1, 0]
    assert tries[:2] == [1, 0]
    mine = [line for line in logged if "match log" in line]
    assert any("to be tried again" in line for line in mine)
    assert not any("ALPHA" in line or "BRAVO" in line for line in mine)


def test_a_line_never_written_is_dropped_after_its_tries(board, log, logged):
    log.service.retryDelays = (0.01, 0.01)
    log.directory.write_text("not a directory", encoding="utf-8")
    _match()
    _game(1)
    _game(1)

    assert log.service.Flush(5)
    assert log.service._writer.is_alive()
    assert log.directory.read_text(encoding="utf-8") == "not a directory"
    mine = [line for line in logged if "match log" in line]
    assert any("line(s) dropped" in line for line in mine)
    assert not any("ALPHA" in line or "BRAVO" in line for line in mine)


def test_the_close_tries_again_within_its_time(board, log, logged):
    log.service.retryDelays = (60.0,)
    log.directory.write_text("not a directory", encoding="utf-8")
    _match()
    _game(1)

    # The close does not wait the minute: it gives up in its own time.
    assert log.service.Shutdown(1.0)
    assert not log.service._writer.is_alive()
    assert any("line(s) dropped" in line for line in logged)


def test_text_that_cannot_be_encoded_is_written_replaced(log, tmp_path):
    path = str(tmp_path / "match_log" / "20261116.jsonl")
    assert log.service._Write([(path, '{"p1": "A\ud800B"}', 0)]) == []
    assert (tmp_path / "match_log" / "20261116.jsonl").read_text(
        encoding="utf-8") == '{"p1": "A?B"}\n'


def test_another_error_drops_that_file_s_lines_and_goes_on(
        log, tmp_path, logged, monkeypatch):
    broken = str(tmp_path / "match_log" / "20261116.jsonl")
    fine = str(tmp_path / "match_log" / "20261117.jsonl")

    def _separator(path):
        if path == broken:
            raise ValueError("probe")
        return ""

    monkeypatch.setattr(log.service, "_Separator", _separator)
    assert log.service._Write(
        [(broken, "{}", 0), (fine, '{"ok": 1}', 0)]) == []
    assert (tmp_path / "match_log" / "20261117.jsonl").read_text(
        encoding="utf-8") == '{"ok": 1}\n'
    assert not (tmp_path / "match_log" / "20261116.jsonl").exists()
    assert any("dropped" in line and "ValueError" in line
               for line in logged)


# ── the shells' close ────────────────────────────────────────────────────

@pytest.mark.parametrize("path, function", [
    ("src/Autona.py", "closeEvent"),
    ("src/headless.py", "Shutdown"),
])
def test_both_shells_close_the_match_log_after_the_tasks(path, function):
    # After the web server and the tasks, which can still publish a change
    # it records; the headless shell before its flush.
    calls = _calls_in_order(_function(path, function))
    closes = [i for i, call in enumerate(calls)
              if call == "MatchLogService.instance.Shutdown"]

    assert len(closes) == 1, calls
    assert calls.index("self.webserver.stop") < closes[0]
    assert calls.index("TaskExecutor.instance.shutdown") < closes[0]
    if "StateManager.JoinExports" in calls:
        assert closes[0] < calls.index("StateManager.JoinExports")
