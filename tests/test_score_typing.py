"""A score typed into a 1v1 / team board's display (ADR-005 S4x).

The two score spinboxes wrote through the game tracker's own UpdateScore
(GameTrackerService), a second writer of score.{n}.team.{t}.score beside
core's. They call the operation v1's ``score:set`` calls now
(AutonaActions.SetScore): one save cycle with the game rows following, and
nothing written for the score the state already has. So the display typed
in, the route on the desktop and the route headless write the same.
"""
import pytest

from src.core import GameTrackerService as tracker
from src.core.Actions import AutonaActions
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager
from tests.fixtures.write_recording import WriteRecorder, encode
from tests.test_api_v1 import client, webApp  # noqa: F401
from tests.test_api_v1_score import _ok
from tests.test_web_operations import _dropHeadlessBoards, _goHeadless


@pytest.fixture
def trackerOn(monkeypatch):
    # The tracker ships disabled. The lookup, not the process-global setting
    # (as tests/test_api_v1_score.py does).
    realGet = SettingsManager.Get
    monkeypatch.setattr(SettingsManager, "Get", staticmethod(
        lambda key, default=None:
            False if key == "general.disable_individual_game_tracker"
            else realGet(key, default)))


def _display(board, team):
    return board.scoreDisplay1 if team == 1 else board.scoreDisplay2


# (what it is, the team typed in, the number typed, both teams' scores --
# what score:set is sent)
STEPS = [
    ("the left to 2", 1, 2, (2, 0)),
    ("the right to 1", 2, 1, (2, 1)),
    ("the left down to 1", 1, 1, (1, 1)),
    ("the right to 3", 2, 3, (1, 3)),
    ("the left to 99", 1, 99, (99, 3)),
    ("the left to 0", 1, 0, (0, 3)),
]


def _setUp(n):
    # A set of five games, so the rows have somewhere to follow to.
    AutonaActions.instance.SetBestOf(n, 5)
    AutonaActions.instance.ApplyChanges(n)


def _play(app, n, act):
    _setUp(n)
    app.settle()
    played = []
    for label, team, value, scores in STEPS:
        with WriteRecorder() as recorder:
            act(team, value, scores)
            # 反映, so the desktop's held write reaches the delta as the
            # headless one does at once.
            AutonaActions.instance.ApplyChanges(n)
            app.settle()
        played.append((label, recorder.Result(),
                       encode(app.get(f"score.{n}"))))
    return played


@pytest.mark.parametrize("n", [1, 2])
def test_typing_writes_what_score_set_writes(
        autonaApp, client, monkeypatch, trackerOn, n):
    board = autonaApp.board(n)
    typed = _play(autonaApp, n,
                  lambda team, value, _scores:
                      _display(board, team).setValue(value))
    # Every step wrote the score, and the rows followed it.
    assert [label for label, recorded, _state in typed
            if not recorded["sets"]] == []
    assert any(key.startswith(f"score.{n}.stages.")
               for _label, recorded, _state in typed
               for key, _value in recorded["sets"])

    def throughTheRoute(_team, _value, scores):
        _ok(client.post(f"/api/v1/scoreboards/{n}/score:set",
                        json={"team1": scores[0], "team2": scores[1]}))

    autonaApp.board(n)
    assert _play(autonaApp, n, throughTheRoute) == typed

    with monkeypatch.context() as headless:
        _goHeadless(autonaApp, headless)
        try:
            assert _play(autonaApp, n, throughTheRoute) == typed
        finally:
            _dropHeadlessBoards(autonaApp)


@pytest.mark.parametrize("n", [1, 2])
def test_typing_is_one_save_cycle_with_the_rows(autonaApp, trackerOn, n):
    board = autonaApp.board(n)
    _setUp(n)
    autonaApp.settle()
    cycles = []
    StateManager.observers.held_state_updated.subscribe(cycles.append)
    try:
        board.scoreDisplay1.setValue(3)
        autonaApp.settle()
    finally:
        StateManager.observers.held_state_updated.unsubscribe(cycles.append)
    assert cycles == [f"score.{n}"]
    assert autonaApp.get(f"score.{n}.team.1.score") == 3
    rows = AutonaActions.instance.GameTracker(n)
    assert [rows.GameWinner(game) for game in range(4)]         == [tracker.TEAM_1] * 3 + [tracker.UNREPORTED]


@pytest.mark.parametrize("n", [1, 2])
def test_the_score_the_state_has_writes_nothing(autonaApp, n):
    """The display's valueChanged fires for a value the state already has
    when its render races the operator's typing; re-asserting it would light
    反映 for an edit nobody made."""
    autonaApp.ready(n)
    AutonaActions.instance.SetScore(n, 1, 2)
    AutonaActions.instance.ApplyChanges(n)
    autonaApp.settle()
    with WriteRecorder() as recorder:
        assert AutonaActions.instance.SetScore(n, 1, 2) == 2
        autonaApp.settle()
    assert recorder.sets == [] and recorder.deltas == []
    assert StateManager.HasPendingChanges(f"score.{n}") is False


def test_the_tracker_has_no_score_writer_of_its_own():
    # The writers of a team's score inside core are AutonaActions, the
    # tracker's PublishStageResults (rows -> score) and the set load's
    # ApplyScores (.claude/rules/state-and-webserver.md).
    from src.core.GameTrackerService import GameTrackerService
    assert not hasattr(GameTrackerService, "UpdateScore")
