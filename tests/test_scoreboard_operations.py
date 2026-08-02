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
import pytest

from qtpy.QtWidgets import QCheckBox, QLineEdit


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
    """Setup guard for the xfail below, kept outside it deliberately.

    If ChangeSetData ever stops writing score.3.best_of, the xfail test's own
    precondition would fail and it would keep passing as an xfail -- so the
    BUG-4 fix would look untracked forever. Pinned separately instead.
    """
    board = autonaApp.board(3)

    board.ChangeSetData({"bestOf": 5})
    autonaApp.settle()

    assert autonaApp.get("score.3.best_of") == 5


@pytest.mark.xfail(strict=True, reason=(
    "Known gap: reset_match sets the crew board's compat best_of spinbox, "
    "but nothing publishes that to state, so score.3.best_of keeps the value "
    "ChangeSetData wrote and overlays keep rendering the old BO after a "
    "match reset. Boards 1/2 clear it. Remove this marker with the fix."))
def test_reset_match_clears_best_of_on_the_crew_board(autonaApp):
    # The precondition (ChangeSetData writes score.3.best_of) is pinned by
    # test_change_set_data_publishes_best_of_on_the_crew_board above; only the
    # missing clear is expected to fail here.
    board = autonaApp.board(3)

    board.ChangeSetData({"bestOf": 5})
    autonaApp.settle()

    autonaApp.actions().reset_match(3)
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
