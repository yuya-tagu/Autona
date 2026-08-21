"""Scoreboard operations against the real widgets.

These are the four things a caster does to a scoreboard during a stream --
change the score, swap sides, reset the score, clear the board -- driven
through the same entry points the +1 buttons, the hotkeys, the Web UI and
the external control API use. Every one of them had a shipped bug at some
point (see docs/changes/test-automation-strategy.md §2), and none of them
had a test.

Nothing is stubbed: `autonaApp.board(n)` is the production
AutonaScoreboardWidget / AutonaCrewBattleWidget, and `autonaApp.actions()`
is the WebServerActions object every HTTP route delegates to.

The assertions read StateManager rather than the widgets, because
StateManager is what OBS overlays and the Web UI actually consume -- a
spinbox showing the right number while the state kept the old one is the
failure these tests exist to catch.
"""
from unittest.mock import patch

import pytest

from qtpy.QtCore import QCoreApplication, QEvent
from qtpy.QtWidgets import QCheckBox, QLineEdit, QWidget
from shiboken6 import Shiboken

from src.SettingsManager import SettingsManager

# The individual game tracker ships disabled, so the stage rows it rebuilds on
# every best-of change are not built at all by default. Patching the lookup
# (rather than writing the setting) keeps SettingsManager -- which is
# process-global and shared with the rest of the session -- untouched.
_realSettingsGet = SettingsManager.Get


def _trackerEnabled(key, default=None):
    if key == "general.disable_individual_game_tracker":
        return False
    return _realSettingsGet(key, default)


def _score(app, team, board=1):
    return app.get(f"score.{board}.team.{team}.score")


def _setPlayerName(board, team, name):
    """Seed a player tag the way a loaded set does."""
    widgets = board.team1playerWidgets if team == 1 else board.team2playerWidgets
    widgets[0].SetData({"gamerTag": name}, dontLoadFromDB=True)


def _playerName(app, team, board=1):
    # The published key is the tag QLineEdit's objectName ("name"), which is
    # also what the overlays read.
    return app.get(f"score.{board}.team.{team}.player.1.name")


def _losers(board, team):
    column = board.team1column if team == 1 else board.team2column
    return column.findChild(QCheckBox, "losers")


def _teamName(board, team):
    column = board.team1column if team == 1 else board.team2column
    return column.findChild(QLineEdit, "teamName")


# ── score ────────────────────────────────────────────────────────────────

def test_score_up_and_down_reach_the_state(autonaApp):
    board = autonaApp.board(1)

    board.CommandScoreChange(0, 1)
    board.CommandScoreChange(0, 1)
    board.CommandScoreChange(1, 1)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 2
    assert _score(autonaApp, 2) == 1

    board.CommandScoreChange(0, -1)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 1


def test_score_never_goes_negative(autonaApp):
    board = autonaApp.board(1)

    board.CommandScoreChange(0, -1)
    autonaApp.settle()

    # The spinbox floor is the guard; without it the overlay would render a
    # "-1" that no operator can produce deliberately.
    assert _score(autonaApp, 1) == 0


def test_unknown_team_index_is_ignored(autonaApp):
    board = autonaApp.board(1)
    board.CommandScoreChange(0, 1)
    autonaApp.settle()

    # Reachable from the API/hotkey layer; must not raise or corrupt a board.
    board.CommandScoreChange(5, 1)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 1
    assert _score(autonaApp, 2) == 0


# ── best of ──────────────────────────────────────────────────────────────

def test_best_of_publishes_every_derived_field(autonaApp):
    board = autonaApp.board(1)

    board.bestOfSpin.setValue(5)
    autonaApp.settle()

    assert autonaApp.get("score.1.best_of") == 5
    assert autonaApp.get("score.1.best_of_short_text") == "BO5"
    assert autonaApp.get("score.1.first_to") == 3
    assert autonaApp.get("score.1.first_to_short_text") == "FT3"
    # The long forms are localized, so the wording is not a contract -- but
    # the number is. A truthy check alone would pass on an unformatted
    # "Best of {}" template, which is precisely how this breaks.
    assert "5" in autonaApp.get("score.1.best_of_text")
    assert "3" in autonaApp.get("score.1.first_to_text")


def test_best_of_zero_blanks_the_long_texts(autonaApp):
    board = autonaApp.board(1)
    board.bestOfSpin.setValue(3)
    autonaApp.settle()

    board.bestOfSpin.setValue(0)
    autonaApp.settle()

    # 0 means "not set" — the overlay shows nothing rather than "Best of 0".
    assert autonaApp.get("score.1.best_of_text") == ""
    assert autonaApp.get("score.1.first_to_text") == ""


def test_changing_best_of_does_not_pile_up_stage_rows(autonaApp):
    """Each best-of change rebuilds the game tracker's stage list.

    It used to keep every list it had ever built: removeWidget() takes the old
    container out of the layout but leaves it parented to the tracker, so the
    dead rows stayed alive -- along with the currentIndexChanged lambdas that
    write score.1.stages.*, which then answered a stage-model reload from
    widgets no longer on screen.

    Run with the tracker switched on, so the rows themselves are built and
    counted; the default (off) build would only ever leak the empty container.
    """
    with patch.object(SettingsManager, "Get", side_effect=_trackerEnabled):
        board = autonaApp.board(1)
        tracker = board.individualGameTracker

        # Warm up through one full rebuild, so the baseline is a settled
        # tracker rather than the one __init__ happened to leave behind.
        board.bestOfSpin.setValue(5)
        board.bestOfSpin.setValue(3)
        autonaApp.settle()
        before = len(tracker.findChildren(QWidget))

        for value in (5, 3, 5, 3, 5, 3):
            board.bestOfSpin.setValue(value)
        autonaApp.settle()

        assert len(tracker.findChildren(QWidget)) == before, (
            "a best-of change left its old stage list parented to the tracker. "
            "removeWidget() only detaches it from the layout -- the container "
            "has to be destroyed, or every rebuild stays alive for the session")


def test_the_replaced_stage_list_is_destroyed(autonaApp):
    """Un-parenting the old list is not enough: it has to be deleted.

    The count above only sees the tracker's children, so setParent(None) alone
    would satisfy it while the container -- and its stage rows, and their
    connections -- lived on as an orphaned top-level widget. This is the half
    that pins the deleteLater().

    The harness never reaps deferred deletions on its own (loop level 0, no
    exec(); see tests/conftest.py), so the DeferredDelete is delivered here
    explicitly, aimed at the container so nothing else is torn down with it.
    """
    with patch.object(SettingsManager, "Get", side_effect=_trackerEnabled):
        board = autonaApp.board(1)
        tracker = board.individualGameTracker

        board.bestOfSpin.setValue(5)
        autonaApp.settle()
        replaced = tracker.stage_order_list_widget
        assert Shiboken.isValid(replaced)

        board.bestOfSpin.setValue(3)
        autonaApp.settle()

        assert replaced is not tracker.stage_order_list_widget
        assert replaced.parent() is None, (
            "the replaced stage list is still parented to the tracker")

        QCoreApplication.sendPostedEvents(replaced, QEvent.Type.DeferredDelete)
        assert not Shiboken.isValid(replaced), (
            "the replaced stage list was never handed to deleteLater(), so "
            "nothing will ever destroy it: it stays alive as an orphan, with "
            "its stage combos still connected to the shared stage model and "
            "still writing score.1.stages.*")


# ── swap ─────────────────────────────────────────────────────────────────

def test_swap_exchanges_scores_players_and_flags(autonaApp):
    board = autonaApp.board(1)

    board.CommandScoreChange(0, 2)
    board.CommandScoreChange(1, 1)
    _setPlayerName(board, 1, "LEFT")
    _setPlayerName(board, 2, "RIGHT")
    _losers(board, 2).setChecked(True)
    autonaApp.settle()

    board.SwapTeams()
    autonaApp.settle()

    assert _score(autonaApp, 1) == 1
    assert _score(autonaApp, 2) == 2
    assert _playerName(autonaApp, 1) == "RIGHT"
    assert _playerName(autonaApp, 2) == "LEFT"
    assert _losers(board, 1).isChecked() is True
    assert _losers(board, 2).isChecked() is False


def test_swap_exchanges_team_names(autonaApp):
    board = autonaApp.board(2)  # team board: the names are visible there

    _teamName(board, 1).setText("Alpha")
    _teamName(board, 1).editingFinished.emit()
    _teamName(board, 2).setText("Bravo")
    _teamName(board, 2).editingFinished.emit()
    autonaApp.settle()

    board.SwapTeams()
    autonaApp.settle()

    assert autonaApp.get("score.2.team.1.teamName") == "Bravo"
    assert autonaApp.get("score.2.team.2.teamName") == "Alpha"


def test_swap_is_its_own_inverse(autonaApp):
    board = autonaApp.board(1)

    board.CommandScoreChange(0, 3)
    _setPlayerName(board, 1, "LEFT")
    _setPlayerName(board, 2, "RIGHT")
    autonaApp.settle()

    board.SwapTeams()
    board.SwapTeams()
    autonaApp.settle()

    assert _score(autonaApp, 1) == 3
    assert _score(autonaApp, 2) == 0
    assert _playerName(autonaApp, 1) == "LEFT"
    assert _playerName(autonaApp, 2) == "RIGHT"


def test_swap_publishes_the_swapped_flag(autonaApp):
    board = autonaApp.board(1)

    board.SwapTeams()
    autonaApp.settle()
    assert autonaApp.get("score.1.teamsSwapped") is True

    board.SwapTeams()
    autonaApp.settle()
    assert autonaApp.get("score.1.teamsSwapped") is False


# ── reset score ──────────────────────────────────────────────────────────

def test_reset_score_zeroes_scores_and_keeps_players(autonaApp):
    board = autonaApp.board(1)

    board.CommandScoreChange(0, 2)
    board.CommandScoreChange(1, 3)
    _setPlayerName(board, 1, "KEEP ME")
    autonaApp.settle()

    board.ResetScore()
    autonaApp.settle()

    assert _score(autonaApp, 1) == 0
    assert _score(autonaApp, 2) == 0
    # Reset Score is the between-games button: the players stay on the board.
    assert _playerName(autonaApp, 1) == "KEEP ME"


# ── the WebServerActions layer ───────────────────────────────────────────
#
# clear_all / reset_match reach for the best_of spinbox, which the 1v1 and
# team boards reparent out of `scoreColumn` while they are built. Looking it
# up with scoreColumn.findChild() therefore returned None and raised
# AttributeError -- an HTTP 500 on /scoreboard<N>-clear-all and on the API's
# :clear / :reset-match (fixed in 019d85f9). These run through the real
# actions object so that regression cannot come back unnoticed.

@pytest.mark.parametrize("boardNumber", [1, 2])
def test_reset_match_clears_score_and_best_of(autonaApp, boardNumber):
    board = autonaApp.board(boardNumber)
    actions = autonaApp.actions()

    board.CommandScoreChange(0, 2)
    board.bestOfSpin.setValue(5)
    autonaApp.settle()

    actions.reset_match(boardNumber)
    autonaApp.settle()

    assert _score(autonaApp, 1, boardNumber) == 0
    assert _score(autonaApp, 2, boardNumber) == 0
    assert autonaApp.get(f"score.{boardNumber}.best_of") == 0


def test_reset_match_restores_full_stocks_on_the_crew_board(autonaApp):
    # Board 3 has no best-of UI; it carries a hidden spinbox purely so the
    # generic actions can poke it without an AttributeError. Reaching it at
    # all used to be the whole point of this test, but "did not raise" would
    # also pass on a reset_match that did nothing: for crew, ClearScore ->
    # ResetScore means every member back to full stocks and nobody active.
    from src.AutonaCrewBattleWidget import DEFAULT_STOCK

    board = autonaApp.board(3)

    board.CommandMemberStock(0, 1, -2)
    board.CommandMemberStock(1, 2, -1)
    board.SetActiveMember(0, 1)
    autonaApp.settle()
    assert autonaApp.get("score.3.team.1.player.1.stock") == DEFAULT_STOCK - 2
    assert autonaApp.get("score.3.team.2.player.2.stock") == DEFAULT_STOCK - 1
    assert autonaApp.get("score.3.team.1.activeMember") == "1"

    autonaApp.actions().reset_match(3)
    autonaApp.settle()

    for team in (1, 2):
        for member in range(1, len(board.memberRows[team - 1]) + 1):
            assert autonaApp.get(
                f"score.3.team.{team}.player.{member}.stock") == DEFAULT_STOCK
        assert autonaApp.get(f"score.3.team.{team}.activeMember") == "0"


def test_change_set_data_publishes_best_of_on_the_crew_board(autonaApp):
    """Setup guard for the reset test below, kept separate deliberately.

    If ChangeSetData ever stops writing score.3.best_of, the reset test's own
    precondition would silently disappear and it would keep passing on a
    never-written key. Pinned separately instead.
    """
    board = autonaApp.board(3)

    board.ChangeSetData({"bestOf": 5})
    autonaApp.settle()

    assert autonaApp.get("score.3.best_of") == 5
    # The hidden compat spinbox is the source of truth, like boards 1/2.
    assert board._bestOfSpin.value() == 5


def test_crew_board_seeds_best_of_at_construction(autonaApp):
    # Overlays read score.3.best_of unconditionally, so the key must exist
    # from the start rather than only after the first ChangeSetData.
    autonaApp.board(3)
    autonaApp.settle()

    assert autonaApp.get("score.3.best_of") == 0


def test_reset_match_clears_best_of_on_the_crew_board(autonaApp):
    # BUG-4 regression: reset_match used to leave score.3.best_of at the value
    # ChangeSetData wrote, so overlays kept rendering the old BO.
    board = autonaApp.board(3)

    board.ChangeSetData({"bestOf": 5})
    autonaApp.settle()

    autonaApp.actions().reset_match(3)
    autonaApp.settle()

    assert autonaApp.get("score.3.best_of") == 0
    assert board._bestOfSpin.value() == 0


def test_clear_all_clears_best_of_on_the_crew_board(autonaApp):
    board = autonaApp.board(3)

    board.ChangeSetData({"bestOf": 5})
    autonaApp.settle()

    autonaApp.actions().clear_all(3)
    autonaApp.settle()

    assert autonaApp.get("score.3.best_of") == 0


@pytest.mark.parametrize("boardNumber", [1, 2])
def test_clear_all_resets_scores_best_of_and_players(autonaApp, boardNumber):
    board = autonaApp.board(boardNumber)
    actions = autonaApp.actions()

    board.CommandScoreChange(0, 2)
    board.bestOfSpin.setValue(5)
    _setPlayerName(board, 1, "GONE")
    autonaApp.settle()

    actions.clear_all(boardNumber)
    autonaApp.settle()

    assert _score(autonaApp, 1, boardNumber) == 0
    assert _score(autonaApp, 2, boardNumber) == 0
    assert autonaApp.get(f"score.{boardNumber}.best_of") == 0
    assert not _playerName(autonaApp, 1, boardNumber)


def test_reset_scores_action_leaves_players_alone(autonaApp):
    board = autonaApp.board(1)
    actions = autonaApp.actions()

    board.CommandScoreChange(0, 2)
    _setPlayerName(board, 1, "KEEP ME")
    autonaApp.settle()

    actions.reset_scores(1)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 0
    assert _playerName(autonaApp, 1) == "KEEP ME"


def test_score_up_action_matches_the_widget(autonaApp):
    autonaApp.board(1)
    actions = autonaApp.actions()

    actions.team_scoreup(1, 1)
    actions.team_scoreup(1, 2)
    actions.team_scoreup(1, 2)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 1
    assert _score(autonaApp, 2) == 2

    actions.team_scoredown(1, 2)
    autonaApp.settle()

    assert _score(autonaApp, 2) == 1


# ── lifecycle ────────────────────────────────────────────────────────────

def test_close_without_a_loaded_set_does_not_raise(autonaApp):
    """BUG-5: autoUpdateTimer / timeLeftTimer stay None until NewSetSelected()
    creates them, but closeEvent used to call .stop() on both unconditionally.
    Closing a board that never had a set loaded raised AttributeError -- Qt
    swallows exceptions from event handlers, so it was silent in production.
    Calling closeEvent() directly (rather than .close()) makes a regression
    fail loudly instead of vanishing into Qt's exception hook again.
    """
    from qtpy.QtGui import QCloseEvent

    board = autonaApp.board(1)
    assert board.autoUpdateTimer is None
    assert board.timeLeftTimer is None

    board.closeEvent(QCloseEvent())  # must not raise


def test_close_with_a_loaded_set_still_stops_the_timers(autonaApp):
    """The None guard must not break the ordinary path where NewSetSelected
    already built real timers."""
    from qtpy.QtCore import QTimer
    from qtpy.QtGui import QCloseEvent

    board = autonaApp.board(1)
    board.autoUpdateTimer = QTimer()
    board.autoUpdateTimer.start(5000)
    board.timeLeftTimer = QTimer()
    board.timeLeftTimer.start(100)

    board.closeEvent(QCloseEvent())

    assert not board.autoUpdateTimer.isActive()
    assert not board.timeLeftTimer.isActive()
