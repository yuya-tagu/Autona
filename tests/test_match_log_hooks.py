"""match_decided's judgement in the match log (ADR-009 appendix D.1): the
candidates and withdrawals MatchLogService tells the hooks' dispatcher it
is given.

Driven like tests/test_match_log.py (board 1 as the headless shell has it,
every write published) with a fake dispatcher that records what it is told.
The wait before a candidate fires and the "fired" mark are the dispatcher's
(lane L9), so they are not here: only what the service tells, and when.
"""
import pytest

from src.core.Actions import AutonaActions
from src.core.MatchLogService import MatchLogService
from src.core.PlayerService import PlayerService
from src.core.PluginTypes import HookEvent
from src.core.StateManager import StateManager
from tests.test_match_log import (  # noqa: F401 (fixtures)
    _Clock, _game, _Log, _match, _name, _reset, board)


class _Hooks:
    """The hooks' dispatcher as the match log sees it (PluginHooks' Wanted /
    MatchCandidate / MatchWithdrawn): records each call, checking it comes
    holding StateManager.lock (the observer's)."""

    def __init__(self, wanted=True, failing=False):
        self.wanted = wanted
        self.failing = failing
        self.calls = []
        self.asked = 0

    def Wanted(self, event):
        assert event == HookEvent.MATCH_DECIDED
        assert StateManager.lock._is_owned()
        self.asked += 1
        return self.wanted

    def MatchCandidate(self, recordId, line):
        assert StateManager.lock._is_owned()
        if self.failing:
            raise RuntimeError("the dispatcher is broken")
        self.calls.append(("candidate", recordId, dict(line)))

    def MatchWithdrawn(self, recordId):
        assert StateManager.lock._is_owned()
        self.calls.append(("withdrawn", recordId))

    def take(self):
        calls, self.calls = self.calls, []
        return calls


def _hooked(autonaApp, tmp_path, hooks):
    harness = MatchLogService.instance
    harness.Stop()
    clock = _Clock()
    directory = tmp_path / "match_log"
    service = MatchLogService(directory=str(directory), clock=clock,
                              hooks=hooks)
    service.Start()
    return harness, _Log(service, directory, clock)


@pytest.fixture
def hooks():
    return _Hooks()


@pytest.fixture
def log(autonaApp, tmp_path, hooks):
    """A match log of the test's own, telling `hooks`; best_of 0 (not set)
    to begin with."""
    assert AutonaActions.instance.SetBestOf(1, 0) or \
        StateManager.Get("score.1.best_of") == 0
    harness, made = _hooked(autonaApp, tmp_path, hooks)
    try:
        yield made
    finally:
        made.service.Shutdown(5)
        harness.Start()


def _bestOf(n, board=1):
    AutonaActions.instance.SetBestOf(board, n)
    assert StateManager.GetCommitted(f"score.{board}.best_of") == n


def _candidate(log, line=None):
    """The candidate the open record's last line makes."""
    line = line or log.records()[-1]
    return ("candidate", line["id"], dict(line, closed=False))


def _summary(calls):
    return [(call[0], call[2]["score"]) if call[0] == "candidate"
            else (call[0],) for call in calls]


# ── when a record is decided ─────────────────────────────────────────────

def test_a_majority_of_counted_games_is_a_candidate(board, log, hooks):
    _bestOf(3)
    _match()
    _game(1)
    assert hooks.take() == []
    _game(2)
    assert hooks.take() == []
    _game(1)
    [line] = log.records()
    assert line["score"] == [2, 1]
    # The line the log has, as it is now (closed false).
    assert hooks.take() == [_candidate(log)]


def test_a_loaded_set_s_baseline_alone_is_no_candidate(board, log, hooks):
    _bestOf(3)
    # A set loaded with its reported score: a baseline, no game counted.
    with StateManager.SaveBlock():
        StateManager.Set("score.1.set_id", 3003)
        _match()
        AutonaActions.instance.SetScore(1, 1, 2)
    assert log.lines() == []
    assert hooks.take() == []

    # Its best_of entered after: still no game.
    _bestOf(5)
    _bestOf(3)
    assert hooks.take() == []
    assert hooks.asked == 0

    # A game counted on top of the baseline decides it.
    _game(2)
    assert _summary(hooks.take()) == [("candidate", [2, 1])]


def test_a_best_of_entered_after_the_games_decides_then(board, log, hooks):
    _match()
    _game(1)
    _game(1)
    # No best_of: nothing decided.
    assert hooks.take() == []
    lines = log.lines()

    _bestOf(3)
    assert hooks.take() == [_candidate(log)]
    # best_of is no change to the record: no line written.
    assert log.lines() == lines


def test_names_and_a_score_typed_together_decide(board, log, hooks):
    _bestOf(3)
    with StateManager.SaveBlock():
        _match()
        AutonaActions.instance.SetScore(1, 1, 2)
    [line] = log.records()
    assert len(line["games"]) == 2
    assert hooks.take() == [_candidate(log)]


def test_an_even_best_of_decides_at_a_majority(board, log, hooks):
    _bestOf(2)
    _match()
    _game(1)
    assert hooks.take() == []
    _game(1)
    assert _summary(hooks.take()) == [("candidate", [2, 0])]


# ── withdrawn, and the line replaced ─────────────────────────────────────

def test_a_game_taken_back_withdraws_the_candidate(board, log, hooks):
    _bestOf(3)
    _match()
    _game(1)
    _game(1)
    [first] = hooks.take()
    recordId = first[1]

    _game(1, -1)
    assert hooks.take() == [("withdrawn", recordId)]
    # Withdrawn once: another change while not decided tells nothing.
    _game(2)
    assert hooks.take() == []

    # Decided again, the same record.
    _game(1)
    [again] = hooks.take()
    assert again == _candidate(log)
    assert again[1] == recordId


def test_a_larger_best_of_withdraws_and_a_zero_one_too(board, log, hooks):
    _bestOf(3)
    _match()
    _game(1)
    _game(1)
    [(_kind, recordId, _line)] = hooks.take()

    _bestOf(5)
    assert hooks.take() == [("withdrawn", recordId)]
    _bestOf(3)
    assert hooks.take() == [_candidate(log)]
    _bestOf(0)
    assert hooks.take() == [("withdrawn", recordId)]


def test_a_changed_line_while_decided_is_told_again(board, log, hooks):
    _bestOf(5)
    _match()
    for _ in range(3):
        _game(1)
    [first] = hooks.take()
    assert first[2]["score"] == [3, 0]

    # Another game while decided: the same record, its new line.
    _game(2)
    [second] = hooks.take()
    assert second == _candidate(log)
    assert second[1] == first[1]
    assert second[2]["score"] == [3, 1]

    # The swap changes the line (the sides), not the decision.
    assert AutonaActions.instance.SwapTeams(1)
    [third] = hooks.take()
    assert third == _candidate(log)
    assert (third[2]["p1"], third[2]["score"]) == ("BRAVO", [1, 3])

    # A change the record does not take in (a played record's round
    # waits): its line is the same, nothing told.
    StateManager.Set("score.1.match", "Grand Final")
    assert hooks.take() == []


# ── a record closing ────────────────────────────────────────────────────

def test_the_next_match_withdraws_nothing(board, log, hooks):
    _bestOf(3)
    _match()
    _game(1)
    _game(1)
    [first] = hooks.take()

    # The next match, and its own best_of in the same publish: the closed
    # record is judged with the best_of it was played with.
    with StateManager.SaveBlock():
        StateManager.Set("score.1.set_id", 2002)
        AutonaActions.instance.SetBestOf(1, 5)
    assert hooks.take() == []
    assert log.records()[0]["closed"] is True

    # The next record is its own.
    _reset()
    _name(1, "CHARLIE")
    _name(2, "DELTA")
    for _ in range(3):
        _game(2)
    [second] = hooks.take()
    assert second[1] != first[1]
    assert (second[2]["p1"], second[2]["score"]) == ("CHARLIE", [0, 3])


def test_a_best_of_one_put_back_on_close_is_told_then(board, log, hooks):
    _bestOf(1)
    _match()
    _game(1)
    [first] = hooks.take()
    # The reset after a best-of-1 looks like a game taken back.
    _reset()
    assert hooks.take() == [("withdrawn", first[1])]

    # The next match shows: the game is put back as the record closes.
    _name(1, "CHARLIE")
    [closed] = hooks.take()
    assert closed[1] == first[1]
    assert closed[2]["closed"] is False
    assert (closed[2]["p1"], closed[2]["score"]) == ("ALPHA", [1, 0])
    assert log.records()[0]["closed"] is True


def test_the_close_tells_nothing(board, log, hooks):
    _bestOf(3)
    _match()
    _game(1)
    hooks.take()
    log.service.Shutdown(5)
    assert hooks.take() == []


# ── what is not told ────────────────────────────────────────────────────

def test_no_wanted_hook_queues_no_candidate(board, log, hooks):
    hooks.wanted = False
    _bestOf(3)
    _match()
    _game(1)
    _game(1)
    _game(1, -1)
    _game(1)
    assert hooks.take() == []
    assert hooks.asked == 2
    # The log is the same.
    assert log.records()[-1]["score"] == [2, 0]


def test_no_dispatcher_records_as_before(autonaApp, tmp_path, board):
    _bestOf(3)
    harness, made = _hooked(autonaApp, tmp_path, None)
    try:
        _match()
        _game(1)
        _game(1)
        assert [line["score"] for line in made.lines()] == [[1, 0], [2, 0]]
    finally:
        made.service.Shutdown(5)
        harness.Start()


def test_a_failing_dispatcher_changes_nothing_in_the_log(board, log, hooks):
    hooks.failing = True
    _bestOf(3)
    _match()
    _game(1)
    _game(1)
    _game(2)
    assert [line["score"] for line in log.lines()] == [
        [1, 0], [2, 0], [2, 1]]


def test_board_2_is_not_judged(board, log, hooks):
    _bestOf(3, board=2)
    for team, tag in ((1, "ALPHA"), (2, "BRAVO")):
        assert PlayerService(f"score.2.team.{team}.player.1").Edit(
            {"name": tag}) is None
    for _ in range(2):
        assert AutonaActions.instance.AdjustScore(2, 1, 1) is not None
    assert StateManager.GetCommitted("score.2.team.1.score") == 2
    assert hooks.take() == []
    assert log.lines() == []
