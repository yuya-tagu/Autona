"""A score typed for a 1v1 / team board (ADR-005 S4x).

The Qt board's two score spinboxes wrote through the game tracker's own
UpdateScore (GameTrackerService), a second writer of
score.{n}.team.{t}.score beside core's. They called the operation v1's
``score:set`` calls from S4x (AutonaActions.SetScore): one save cycle, and
nothing written for the score the state already has. The spinboxes went
with the Qt board in S4ac-3 and the tracker in S4ac-4; what they called,
the route on the desktop and the route headless still write the same.
"""
import pytest

from src.core.Actions import AutonaActions
from src.core.StateManager import StateManager
from tests.fixtures.write_recording import WriteRecorder, encode
from tests.test_api_v1 import client, webApp  # noqa: F401
from tests.test_api_v1_score import _ok
from tests.test_web_operations import _dropHeadlessBoards, _goHeadless


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
    # A set of five games, as a board in play has.
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
def test_set_score_writes_what_score_set_writes(
        autonaApp, client, monkeypatch, n):
    # What the Qt board's score display called on a number typed (S4x).
    autonaApp.ready(n)
    typed = _play(autonaApp, n,
                  lambda team, value, _scores:
                      AutonaActions.instance.SetScore(n, team, value))
    # Every step wrote the score.
    assert [label for label, recorded, _state in typed
            if not recorded["sets"]] == []

    def throughTheRoute(_team, _value, scores):
        _ok(client.post(f"/api/v1/scoreboards/{n}/score:set",
                        json={"team1": scores[0], "team2": scores[1]}))

    autonaApp.ready(n)
    assert _play(autonaApp, n, throughTheRoute) == typed

    with monkeypatch.context() as headless:
        _goHeadless(autonaApp, headless)
        try:
            assert _play(autonaApp, n, throughTheRoute) == typed
        finally:
            _dropHeadlessBoards(autonaApp)


@pytest.mark.parametrize("n", [1, 2])
def test_set_score_is_one_save_cycle(autonaApp, n):
    autonaApp.ready(n)
    _setUp(n)
    autonaApp.settle()
    cycles = []
    StateManager.observers.held_state_updated.subscribe(cycles.append)
    try:
        AutonaActions.instance.SetScore(n, 1, 3)
        autonaApp.settle()
    finally:
        StateManager.observers.held_state_updated.unsubscribe(cycles.append)
    assert cycles == [f"score.{n}"]
    assert autonaApp.get(f"score.{n}.team.1.score") == 3


@pytest.mark.parametrize("n", [1, 2])
def test_the_score_the_state_has_writes_nothing(autonaApp, n):
    """A score the state already has (the Qt display's valueChanged fired
    for one when its render raced the operator's typing; a page can send
    one too): re-asserting it would light 反映 for an edit nobody made."""
    autonaApp.ready(n)
    AutonaActions.instance.SetScore(n, 1, 2)
    AutonaActions.instance.ApplyChanges(n)
    autonaApp.settle()
    with WriteRecorder() as recorder:
        assert AutonaActions.instance.SetScore(n, 1, 2) == 2
        autonaApp.settle()
    assert recorder.sets == [] and recorder.deltas == []
    assert StateManager.HasPendingChanges(f"score.{n}") is False
