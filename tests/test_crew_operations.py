"""The crew board's web operations (ADR-005 S3e-2).

RECORDING (golden.json) -- only on the pre-S3e-2 tree:

    AUTONA_RECORD_CREW=1 python -m pytest tests/test_crew_operations.py -k record
"""
import json
import os
from pathlib import Path

import pytest

from src import AutonaWebOperations as WebOps
from tests.fixtures.write_recording import WriteRecorder, encode

RECORD = os.environ.get("AUTONA_RECORD_CREW") == "1"
GOLDEN_PATH = (Path(__file__).resolve().parent / "fixtures"
               / "crew_operations" / "golden.json")

BOARD = 3

# (label, operation, args, kwargs)
SCENARIOS = [
    ("stock down", "crew_member_stock", (3, 1, 1, -1), {}),
    ("stock up, route strings", "crew_member_stock", ("3", "2", "1", "1"), {}),
    ("stock clamps at 99", "crew_member_stock", (3, 1, 2, 200), {}),
    ("stock clamps at 0", "crew_member_stock", (3, 1, 2, -5), {}),
    ("stock unchanged", "crew_member_stock", (3, 2, 3, 0), {}),
    ("stock of a hidden member", "crew_member_stock", (3, 1, 7, -1), {}),
    ("stock team out of range", "crew_member_stock", (3, 3, 1, -1), {}),
    ("stock member out of range", "crew_member_stock", (3, 1, 10, -1), {}),
    ("stock member zero", "crew_member_stock", (3, 1, 0, -1), {}),
    ("active", "crew_set_active", (3, 1, 2), {}),
    ("active again", "crew_set_active", (3, 2, 1), {}),
    ("active nobody", "crew_set_active", (3, 2, 0), {}),
    ("active nobody, already", "crew_set_active", (3, 1, 0), {}),
    ("active negative", "crew_set_active", (3, 2, -1), {}),
    ("active out of range", "crew_set_active", (3, 1, 10), {}),
    ("active team out of range", "crew_set_active", (3, 3, 1), {}),
    ("active, route strings", "crew_set_active", ("3", "1", "3"), {}),
    ("swap", "swap_teams", (3,), {}),
    ("reset scores", "reset_scores", (3,), {}),
    ("reset match", "reset_match", (3,), {}),
    ("reset players", "reset_players", (3,), {}),
    ("clear all", "clear_all", (3,), {}),
    ("get swap", "get_swap", (3,), {}),
    ("set fields", "set_route", (3,),
     {"bestOf": 3, "phase": "Top 8", "pool": "", "match": "GF"}),
    ("set best of clamps", "set_route", (3,), {"bestOf": 150}),
    ("set the same best of", "set_route", (3,), {"bestOf": 5}),
    ("set players", "set_route", (3,), {"players": 5}),
    ("set players clamps", "set_route", (3,), {"players": 20}),
    ("set the same players", "set_route", (3,), {"players": 3}),
    ("set characters", "set_route", (3,), {"characters": 2}),
    ("set losers", "set_route", (3,), {"losers": "true", "team": "1"}),
    ("set team name", "set_route", (3,), {"team": "1", "teamName": "X"}),
    ("set everything", "set_route", (3,),
     {"bestOf": 1, "phase": "P", "pool": "A", "match": "M", "players": 2,
      "characters": 1, "losers": "false", "team": "2", "teamName": "Y"}),
]


def _state():
    from src.core.StateManager import StateManager
    return StateManager


def _op(app, name):
    """The web operation: the shared one, or -- on the pre-S3e-2 tree,
    which has none for the crew's members -- the desktop's
    WebServerActions."""
    return getattr(WebOps, name, None) or getattr(app.actions(), name)


def _writeSetUp(app):
    """A crew board with members, names, colours, a stock down, an active
    member and a match bar. The recording typed the names into the board's
    inputs, which wrote them through these operations (CrewBattleService /
    AutonaActions); the inputs went with the Qt SCORE in ADR-005 S4ac-3."""
    from src.core.Actions import AutonaActions
    from src.core.CrewBattleService import CrewBattleService
    crew = CrewBattleService(BOARD)
    crew.SetMemberName(1, 1, "A1")
    crew.SetMemberName(1, 2, "A2")
    crew.SetMemberName(2, 1, "B1")
    AutonaActions.instance.SetTeamName(BOARD, 1, "Alpha")
    AutonaActions.instance.SetTeamName(BOARD, 2, "Bravo")
    WebOps.team_color(BOARD, "1", "#111111")
    WebOps.team_color(BOARD, "2", "#222222")
    WebOps.crew_member_stock(BOARD, 1, 1, -1)
    WebOps.crew_set_active(BOARD, 2, 1)
    WebOps.set_route(BOARD, bestOf=5, phase="Pools", match="WF")
    app.settle()


def _setUpDesktop(app):
    """The crew board the desktop makes at launch (ADR-005 S4ac-1), set up."""
    app.ready(BOARD)
    _writeSetUp(app)


def _record(app, name, args, kwargs):
    with WriteRecorder() as recorder:
        try:
            result, raises = _op(app, name)(*args, **kwargs), None
        except Exception as error:
            result, raises = None, type(error).__name__
        app.settle()
    recorded = recorder.Result()
    recorded.update({
        "result": result,
        "raises": raises,
        "board": encode(_state().Get(f"score.{BOARD}")),
        "swapped": WebOps.get_swap(BOARD),
    })
    return recorded


@pytest.mark.skipif(not RECORD, reason="recording only")
def test_record(autonaApp):
    golden = {}
    for label, name, args, kwargs in SCENARIOS:
        _setUpDesktop(autonaApp)
        golden[label] = _record(autonaApp, name, args, kwargs)
    GOLDEN_PATH.write_text(
        json.dumps(golden, ensure_ascii=False, indent=1), encoding="utf-8")


# ── replay: what the operations write now ────────────────────────────────
#
# The golden was recorded through the board widget, before the move. The
# operations are core's now (CrewBattleService) and have to write the same,
# set by set and cycle by cycle, on the desktop (the board made at launch;
# the widget went in ADR-005 S4ac-3, and the operation ran on its GUI thread
# until S4ad) and on the headless shell's boards (the same state, from core
# alone -- refused with 503 until S3e-2). Both through the one BoardHost
# since S4ad.

SCENARIO = {label: (name, args, kwargs)
            for label, name, args, kwargs in SCENARIOS}


def _golden():
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("label", list(SCENARIO))
def test_the_desktop_writes_what_the_board_wrote(autonaApp, label):
    if RECORD:
        pytest.skip("recording")
    name, args, kwargs = SCENARIO[label]
    _setUpDesktop(autonaApp)
    assert _record(autonaApp, name, args, kwargs) == _golden()[label]


@pytest.fixture
def headless(autonaApp, monkeypatch):
    """No board built, a fresh board host (the one class of both shells
    since ADR-005 S4ad) and the boards the headless shell seeds
    (src/headless._SeedBoards)."""
    from src import headless as shell
    autonaApp.dropAllBoards()
    with _state().SaveBlock():
        shell._SeedBoards()
    autonaApp.settle()
    monkeypatch.setattr(WebOps, "boardHost", WebOps.BoardHost())
    try:
        yield autonaApp
    finally:
        _state().Unset("score")
        autonaApp.settle()


def _setUpHeadless(app):
    """`_setUpDesktop` on the boards the headless shell seeds."""
    _writeSetUp(app)


@pytest.mark.parametrize("label", list(SCENARIO))
def test_headless_writes_what_the_board_wrote(headless, label):
    if RECORD:
        pytest.skip("recording")
    name, args, kwargs = SCENARIO[label]
    _setUpHeadless(headless)
    assert _record(headless, name, args, kwargs) == _golden()[label]


def test_the_crew_routes_answer_headless(headless):
    # What answered 503 until S3e-2, through the routes' own operations.
    assert WebOps.crew_member_stock("3", "1", "1", "-1") == "OK"
    assert WebOps.crew_set_active("3", "2", "2") == "OK"
    assert WebOps.swap_teams(3) == "OK"
    assert WebOps.get_swap(3) == "True"
    headless.settle()
    assert headless.get("score.3.team.2.player.1.stock") == 2
    assert headless.get("score.3.team.1.activeMember") == "2"
    assert WebOps.clear_all(3) == "OK"
    headless.settle()
    assert headless.get("score.3.team.1.memberNum") == 1
    assert WebOps.get_swap(3) == "True"   # a clear does not unswap


def test_a_fresh_board_forgets_a_swap(headless):
    """The swap flag is the state's (score.3.teamsSwapped, ADR-005 S4w), so
    what takes the board's state takes it: nothing is left to forget on the
    side, and the seed does not write it (absent is not swapped, as on boards
    1/2)."""
    from src.core.Actions import AutonaActions
    from src.core.CrewBattleService import CrewBattleService
    assert WebOps.swap_teams(3) == "OK"
    assert CrewBattleService(BOARD).teamsSwapped is True
    assert headless.get("score.3.teamsSwapped") is True
    AutonaActions.instance.SeedFreshBoard(BOARD)
    assert CrewBattleService(BOARD).teamsSwapped is False
    assert headless.get("score.3.teamsSwapped") is None
