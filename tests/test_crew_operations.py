"""The crew board's web operations (ADR-005 S3e-2).

RECORDING (golden.json) -- only on the pre-S3e-2 tree:

    AUTONA_RECORD_CREW=1 python -m pytest tests/test_crew_operations.py -k record
"""
import json
import os
from contextlib import contextmanager
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


def _row(board, side, member):
    return board.memberRows[side][member - 1]


def _nameMember(board, side, member, name):
    edit = _row(board, side, member)["name"]
    edit.setText(name)
    edit.editingFinished.emit()


def _typeTeamName(board, side, name):
    edit = board.teamNameEdits[side]
    edit.setText(name)
    edit.editingFinished.emit()


def _setUpDesktop(app):
    """A crew board with members, names, colours, a stock down, an active
    member and a match bar -- typed in where only the board has an input."""
    board = app.board(BOARD)
    _nameMember(board, 0, 1, "A1")
    _nameMember(board, 0, 2, "A2")
    _nameMember(board, 1, 1, "B1")
    _typeTeamName(board, 0, "Alpha")
    _typeTeamName(board, 1, "Bravo")
    WebOps.team_color(BOARD, "1", "#111111")
    WebOps.team_color(BOARD, "2", "#222222")
    _op(app, "crew_member_stock")(BOARD, 1, 1, -1)
    _op(app, "crew_set_active")(BOARD, 2, 1)
    WebOps.set_route(BOARD, bestOf=5, phase="Pools", match="WF")
    app.settle()
    return board


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
# set by set and cycle by cycle, on the desktop (the board built, the
# operation on its GUI thread) and on the headless board host (no widget:
# the same state, from core alone -- refused with 503 until S3e-2).

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
    """No board built, the headless board host and the boards the headless
    shell seeds (src/headless._SeedBoards)."""
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
    """`_setUpDesktop` without the board: what the board's inputs write,
    written through the operations they call."""
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


@pytest.mark.parametrize("label", list(SCENARIO))
def test_headless_writes_what_the_board_wrote(headless, label):
    if RECORD:
        pytest.skip("recording")
    from src.AutonaScoreboardManager import AutonaScoreboardManager
    name, args, kwargs = SCENARIO[label]
    _setUpHeadless(headless)
    assert _record(headless, name, args, kwargs) == _golden()[label]
    assert BOARD not in AutonaScoreboardManager.instance.scoreboards


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


def test_the_harness_forgets_a_swap_made_without_a_board(headless):
    """The crew board's swap flag is core's, not the state's
    (CrewBattleService._swapped): the harness drops it with the board,
    built or not (tests/conftest.py), so a test that swapped board 3
    headless does not hand the next test a swapped board."""
    from src.core.CrewBattleService import CrewBattleService
    assert WebOps.swap_teams(3) == "OK"
    assert CrewBattleService(BOARD).teamsSwapped is True
    headless.dropAllBoards()
    assert CrewBattleService(BOARD).teamsSwapped is False


# ── the board follows the state ──────────────────────────────────────────

def _shown(board):
    """The board as its widgets show it."""
    return {
        "memberNum": board.playerNumber.value(),
        "teams": [{
            "name": board.teamNameEdits[side].text(),
            "color": board.colorButtons[side].color(),
            "total": board.totalLabels[side].text(),
            "members": [(row["name"].text(), row["stock"].value(),
                         row["active"].isChecked(), not row["row"].isHidden())
                        for row in board.memberRows[side]],
        } for side in (0, 1)],
        "match": {field: combo.currentText()
                  for field, combo in board._matchCombos.items()},
    }


def _fromState(app):
    from src.core.CrewBattleService import CrewBattleService
    crew = CrewBattleService(BOARD)
    memberNum = crew.MemberNum()
    return {
        "memberNum": memberNum,
        "teams": [{
            "name": team.name,
            "color": team.color,
            "total": str(app.get(f"score.{BOARD}.team.{side + 1}.score")),
            "members": [(member.name, member.stock, member.active,
                         index < memberNum)
                        for index, member in enumerate(team.members)],
        } for side, team in enumerate(crew.ReadTeam(t) for t in (1, 2))],
        "match": {field: app.get(f"score.{BOARD}.{field}") or ""
                  for field in ("phase", "pool", "match")},
    }


def test_the_board_shows_what_core_writes_and_writes_nothing(autonaApp):
    from src.core.Actions import AutonaActions
    from src.core.CrewBattleService import CrewBattleService
    board = autonaApp.board(BOARD)
    crew = CrewBattleService(BOARD)

    crew.SetMemberName(1, 1, "Core")
    crew.AdjustMemberStock(1, 1, -2)
    crew.SetMemberName(2, 4, "Four")
    crew.SelectActiveMember(2, 4)
    crew.SetMemberCount(5)
    AutonaActions.instance.SetTeamName(BOARD, 2, "Bravo")
    AutonaActions.instance.SetTeamColor(BOARD, 1, "#123456")
    WebOps.set_route(BOARD, phase="Top 8", pool="B", match="GF")
    with WriteRecorder() as recorder:
        autonaApp.settle()

    assert recorder.sets == []
    shown = _shown(board)
    assert shown == _fromState(autonaApp)
    assert shown["teams"][0]["members"][0] == ("Core", 1, False, True)
    assert shown["teams"][1]["members"][3] == ("Four", 3, True, True)
    assert shown["teams"][0]["members"][5][3] is False   # past memberNum
    assert shown["teams"][0]["total"] == "1"

    # The whole-board operations, from the Web.
    for operation in (WebOps.swap_teams, WebOps.reset_scores,
                      WebOps.reset_players, WebOps.clear_all):
        operation(BOARD)
        with WriteRecorder() as recorder:
            autonaApp.settle()
        assert recorder.sets == []
        assert _shown(board) == _fromState(autonaApp), operation.__name__


def test_the_board_follows_a_full_reload(autonaApp):
    board = autonaApp.board(BOARD)
    with _state().lock:
        _state().state["score"][str(BOARD)]["team"]["1"]["player"]["2"][
            "name"] = "Reloaded"
    _state().observers.state_big_change.notify()
    autonaApp.settle()
    assert board.memberRows[0][1]["name"].text() == "Reloaded"


def test_a_board_edit_does_not_write_its_stale_rows_back(autonaApp):
    """The board has not shown a later change yet (its render is deferred)
    when the operator changes a stock: the edit writes that member's stock,
    and the change the board has not shown stays (the old board published
    the crew's whole column from its spinboxes)."""
    from src.core.CrewBattleService import CrewBattleService
    board = autonaApp.board(BOARD)
    crew = CrewBattleService(BOARD)
    crew.SetMemberName(1, 1, "A1")
    crew.SetMemberName(1, 2, "A2")
    autonaApp.settle()

    crew.AdjustMemberStock(1, 2, -2)                     # not shown yet
    crew.SetMemberName(1, 3, "A3")
    assert _row(board, 0, 2)["stock"].value() == 3
    _row(board, 0, 1)["stock"].setValue(1)               # the operator
    _nameMember(board, 0, 1, "Renamed")
    autonaApp.settle()

    assert autonaApp.get("score.3.team.1.player.1.stock") == 1
    assert autonaApp.get("score.3.team.1.player.2.stock") == 1
    assert autonaApp.get("score.3.team.1.player.3.name") == "A3"
    assert autonaApp.get("score.3.team.1.player.1.name") == "Renamed"
    assert autonaApp.get("score.3.team.1.score") == 1 + 1 + 3
    assert _shown(board) == _fromState(autonaApp)


def test_the_active_marker_is_one_per_crew(autonaApp):
    board = autonaApp.board(BOARD)
    _row(board, 0, 1)["active"].setChecked(True)
    autonaApp.settle()
    _row(board, 0, 2)["active"].setChecked(True)
    autonaApp.settle()
    assert autonaApp.get("score.3.team.1.activeMember") == "2"
    assert not _row(board, 0, 1)["active"].isChecked()

    # A marker the render has not caught up with does not clear the member
    # the state moved on to.
    from src.core.CrewBattleService import CrewBattleService
    CrewBattleService(BOARD).SelectActiveMember(1, 3)
    _row(board, 0, 2)["active"].setChecked(False)
    autonaApp.settle()
    assert autonaApp.get("score.3.team.1.activeMember") == "3"
    assert _row(board, 0, 3)["active"].isChecked()
    assert not _row(board, 0, 2)["active"].isChecked()

    _row(board, 0, 3)["active"].setChecked(False)
    autonaApp.settle()
    assert autonaApp.get("score.3.team.1.activeMember") == "0"


def test_the_member_count_spinbox_writes_through_core(autonaApp):
    board = autonaApp.board(BOARD)
    board.playerNumber.setValue(6)
    autonaApp.settle()
    assert autonaApp.get("score.3.team.1.memberNum") == 6
    assert autonaApp.get("score.3.team.2.memberNum") == 6
    assert not _row(board, 1, 6)["row"].isHidden()
    assert _row(board, 1, 7)["row"].isHidden()


# Real focus, on the board's window shown offscreen (the player cards'
# tests, tests/test_player_card_render.py, explain why it is not simulated).

@contextmanager
def _onScreen(board, app):
    from qtpy.QtCore import QCoreApplication, QEvent
    from qtpy.QtWidgets import QWidget
    window = board.window()
    board.setVisible(True)
    window.show()
    window.activateWindow()
    app.settle()
    try:
        yield
    finally:
        standIn = QWidget()
        standIn.show()
        standIn.activateWindow()
        app.settle()
        window.hide()
        board.setVisible(False)
        standIn.hide()
        standIn.deleteLater()
        QCoreApplication.sendPostedEvents(standIn, QEvent.Type.DeferredDelete)
        app.settle()


def _focus(widget, app):
    widget.setFocus()
    app.settle()
    assert widget.isVisible() and widget.hasFocus(), \
        "the harness could not focus the field"


def test_a_name_being_typed_is_left_alone_and_not_written_back(autonaApp):
    from src.core.CrewBattleService import CrewBattleService
    board = autonaApp.board(BOARD)
    crew = CrewBattleService(BOARD)
    crew.SetMemberName(1, 1, "BEFORE")
    autonaApp.settle()
    edit = _row(board, 0, 1)["name"]
    other = board.teamNameEdits[0]
    finished = []
    edit.editingFinished.connect(lambda: finished.append(edit.text()))

    with _onScreen(board, autonaApp):
        _focus(edit, autonaApp)
        crew.SetMemberName(1, 1, "AFTER")
        crew.AdjustMemberStock(1, 1, -1)
        autonaApp.settle()
        assert edit.text() == "BEFORE", "the field being typed in was redrawn"
        assert _row(board, 0, 1)["stock"].value() == 2   # the rest renders

        with WriteRecorder() as recorder:
            _focus(other, autonaApp)
        assert finished == ["BEFORE"], "Qt no longer finishes the field"
        assert recorder.sets == []
        assert autonaApp.get("score.3.team.1.player.1.name") == "AFTER"
        assert edit.text() == "AFTER"


def test_a_swap_takes_the_name_being_typed_with_it(autonaApp):
    board = autonaApp.board(BOARD)
    edit = _row(board, 0, 1)["name"]
    with _onScreen(board, autonaApp):
        _focus(edit, autonaApp)
        edit.setText("Typed")
        assert WebOps.swap_teams(BOARD) == "OK"
        autonaApp.settle()
    assert autonaApp.get("score.3.team.2.player.1.name") == "Typed"
    assert autonaApp.get("score.3.team.1.player.1.name") == ""
    assert _row(board, 1, 1)["name"].text() == "Typed"
    assert edit.text() == ""


@pytest.mark.parametrize("finish", ["enter", "focus-out"])
def test_a_name_typed_while_the_board_renders_is_still_written(
        autonaApp, finish):
    """The operator is typing a name when another change on the board is
    rendered: the text in the field is their edit, not one a render left
    behind, and finishing it writes it."""
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest
    board = autonaApp.board(BOARD)
    edit = _row(board, 0, 1)["name"]
    other = board.teamNameEdits[0]

    with _onScreen(board, autonaApp):
        _focus(edit, autonaApp)
        QTest.keyClicks(edit, "Bob")
        WebOps.crew_member_stock(BOARD, 2, 1, -1)    # renders the board
        autonaApp.settle()
        assert edit.text() == "Bob"
        if finish == "enter":
            QTest.keyClick(edit, Qt.Key.Key_Return)
            autonaApp.settle()
        _focus(other, autonaApp)

    assert autonaApp.get("score.3.team.1.player.1.name") == "Bob"
    assert edit.text() == "Bob"
    from src.core.CrewBattleService import CrewBattleService
    assert autonaApp.get("score.3.team.2.player.1.stock") == (
        CrewBattleService.DEFAULT_STOCK - 1)


@pytest.mark.parametrize("operation", ["reset_players", "clear_all"])
def test_a_clear_from_the_web_takes_the_name_being_typed_with_it(
        autonaApp, operation):
    """The operator is typing into an empty row when the Web clears the
    crew: the field is finished first, as on boards 1/2, and the clear then
    takes the typed text with it -- it is not left in the field to be
    written once the operator leaves it."""
    from qtpy.QtTest import QTest
    board = autonaApp.board(BOARD)
    edit = _row(board, 0, 1)["name"]
    other = board.teamNameEdits[0]

    with _onScreen(board, autonaApp):
        _focus(edit, autonaApp)
        QTest.keyClicks(edit, "Typed")
        assert getattr(WebOps, operation)(BOARD) == "OK"
        autonaApp.settle()
        _focus(other, autonaApp)

    assert autonaApp.get("score.3.team.1.player.1.name") == ""
    assert edit.text() == ""
    assert _shown(board) == _fromState(autonaApp)


def test_a_row_hidden_while_its_name_is_typed_writes_nothing(autonaApp):
    """The member count drops (from the Web) while the operator is typing
    in a row it hides. Hiding the field finishes it -- inside the render,
    which writes nothing: the typed text is not written, and the row shows
    the state when it is shown again."""
    from qtpy.QtTest import QTest
    from src.core.CrewBattleService import CrewBattleService
    board = autonaApp.board(BOARD)
    crew = CrewBattleService(BOARD)
    crew.SetMemberCount(5)
    autonaApp.settle()
    edit = _row(board, 0, 5)["name"]

    with _onScreen(board, autonaApp):
        _focus(edit, autonaApp)
        QTest.keyClicks(edit, "Typed")
        WebOps.set_route(BOARD, players=3)
        with WriteRecorder() as recorder:
            autonaApp.settle()
        assert recorder.sets == []
        assert _row(board, 0, 5)["row"].isHidden()
        assert not edit.hasFocus()

    assert autonaApp.get("score.3.team.1.player.5.name") == ""
    crew.SetMemberCount(5)
    autonaApp.settle()
    assert edit.text() == ""
    assert _shown(board) == _fromState(autonaApp)


@pytest.mark.parametrize("finish", ["enter", "focus-out"])
def test_a_name_written_elsewhere_while_it_is_typed_wins(autonaApp, finish):
    """HeldBackText's rule (b) on the crew board: the member's name itself
    is written elsewhere while the operator types it. That write wins --
    finishing the typing writes nothing, and the new name is shown once the
    field is left. What they type after that render is theirs again."""
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest
    from src.core.CrewBattleService import CrewBattleService
    board = autonaApp.board(BOARD)
    crew = CrewBattleService(BOARD)
    edit = _row(board, 0, 1)["name"]
    other = board.teamNameEdits[0]
    key = "score.3.team.1.player.1.name"

    with _onScreen(board, autonaApp):
        _focus(edit, autonaApp)
        QTest.keyClicks(edit, "TYPED")
        crew.SetMemberName(1, 1, "ELSEWHERE")
        autonaApp.settle()
        # A later pass (a stock from the Web) changes nothing about that.
        WebOps.crew_member_stock(BOARD, 2, 1, -1)
        autonaApp.settle()
        assert edit.text() == "TYPED"

        with WriteRecorder() as recorder:
            if finish == "enter":
                QTest.keyClick(edit, Qt.Key.Key_Return)
                autonaApp.settle()
            _focus(other, autonaApp)
        assert recorder.sets == []
        assert autonaApp.get(key) == "ELSEWHERE"
        assert edit.text() == "ELSEWHERE"

        _focus(edit, autonaApp)
        QTest.keyClicks(edit, "!")
        _focus(other, autonaApp)
    assert autonaApp.get(key) == "ELSEWHERE!"
    assert _shown(board) == _fromState(autonaApp)


def test_a_match_field_the_operator_sits_in_is_held_back(autonaApp):
    """The match bar's combos follow the same rule: the phase is written
    from the Web while the operator is in its field without typing. Leaving
    the field does not put the old phase back, and the new one is shown."""
    board = autonaApp.board(BOARD)
    combo = board.phaseCombo
    other = board.teamNameEdits[0]
    WebOps.set_route(BOARD, phase="Pools")
    autonaApp.settle()
    assert combo.currentText() == "Pools"

    with _onScreen(board, autonaApp):
        _focus(combo.lineEdit(), autonaApp)
        WebOps.set_route(BOARD, phase="Top 8")
        autonaApp.settle()
        assert combo.currentText() == "Pools", "the field was redrawn"
        with WriteRecorder() as recorder:
            _focus(other, autonaApp)
        assert recorder.sets == []

    assert autonaApp.get("score.3.phase") == "Top 8"
    assert combo.currentText() == "Top 8"
