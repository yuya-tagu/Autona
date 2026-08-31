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
import contextlib
from unittest.mock import patch

import pytest

from qtpy.QtCore import QCoreApplication, QEvent
from qtpy.QtWidgets import QCheckBox, QLineEdit, QPushButton, QWidget
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


def _stageButton(board, game, team):
    """The T1 / T2 result button on one stage row. `game` is 1-based."""
    index = game - 1
    row = board.individualGameTracker.stage_widget_list[index]
    button = row.findChild(QPushButton, f"stageTeam{team}Check_{index}")
    assert button is not None, f"no stage button for game {game}, team {team}"
    return button


def _wonGames(app, team, board=1):
    """Which games the state records as won by `team` (1-based game numbers)."""
    stages = app.get(f"score.{board}.stages") or {}
    key = f"t{team}_win"
    return sorted(int(game) for game, result in stages.items()
                  if isinstance(result, dict) and result.get(key))


@contextlib.contextmanager
def _restoredDisplayOption(key):
    """Put one display option back the way the test found it -- both halves.

    A display option deliberately lives in two places (SettingsManager is the
    persisted default a board re-reads when built, StateManager is what the
    overlays render), and this suite shares one process-global copy of each
    with every other test module. Restoring only the settings half left the
    *state* half flipped for whatever ran next.

    "Absent" is restored as absent, in both halves. A display option is
    normally missing from SettingsManager entirely -- boards read it with a
    default and never write it back -- so putting a literal True there is not
    the same state the test found, and a later board would publish that
    invented value.
    """
    from src.StateManager import StateManager

    path = f"display_options.{key}"
    missing = object()
    storedSetting = SettingsManager.Get(path, missing)
    storedState = StateManager.Get(path, missing)
    try:
        yield
    finally:
        if storedSetting is missing:
            SettingsManager.Unset(path)
        else:
            SettingsManager.Set(path, storedSetting)
        if storedState is missing:
            StateManager.Unset(path)
        else:
            StateManager.Set(path, storedState)


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


def test_team_color_action_reaches_the_state(autonaApp):
    autonaApp.board(1)

    autonaApp.actions().team_color(1, 2, "#123456")
    autonaApp.settle()

    assert autonaApp.get("score.1.team.2.color") == "#123456"


# ── the inverted write path (headless core Step 3) ───────────────────────
#
# The score is written by AutonaActions and the board follows the state, so
# the two things that can go wrong are new: the display can drift away from
# what was published, and the board's reaction to a change can write it back
# (the echo loop the design forbids -- Design.md §3, docs/changes/
# headless-core-redesign.md §3).

def _heldUpdateSpy(prefix):
    """Collect held_state_updated notifications for one prefix.

    Returns (list, unsubscribe). Subscribing to the core channel directly is
    what test code and core code may do; a *widget* may not (it would never
    be unsubscribed -- tests/test_signal_lifetime.py enforces that).
    """
    from src.StateManager import StateManager

    seen = []

    def onHeld(notified):
        if notified == prefix:
            seen.append(notified)

    StateManager.observers.held_state_updated.subscribe(onHeld)
    return seen, lambda: StateManager.observers.held_state_updated.unsubscribe(
        onHeld)


def test_the_board_display_follows_the_published_score(autonaApp):
    board = autonaApp.board(1)

    # Straight to the state, as a hotkey or an API call now does -- no
    # spinbox involved.
    autonaApp.actions().team_scoreup(1, 1)
    autonaApp.settle()

    assert board.scoreDisplay1.value() == 1, (
        "the score spinbox no longer follows the state it is supposed to "
        "render; an operator would see a different score from the overlay")


def test_a_score_change_is_exactly_one_save_cycle(autonaApp):
    """Echo-loop check: the board's reaction must not write the score back."""
    board = autonaApp.board(1)
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        board.CommandScoreChange(0, 1)
        autonaApp.settle()
    finally:
        unsubscribe()

    assert _score(autonaApp, 1) == 1
    assert seen == ["score.1"], (
        f"a +1 produced {len(seen)} save cycles instead of 1: the board "
        "reacted to the state change by writing it again")


def test_reset_score_is_exactly_one_save_cycle(autonaApp):
    board = autonaApp.board(1)
    board.CommandScoreChange(0, 2)
    board.CommandScoreChange(1, 1)
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        board.ResetScore()
        autonaApp.settle()
    finally:
        unsubscribe()

    assert _score(autonaApp, 1) == 0 and _score(autonaApp, 2) == 0
    assert board.scoreDisplay1.value() == 0
    assert board.scoreDisplay2.value() == 0
    # Two keys, one cycle -- an overlay must never render a half-reset board.
    assert seen == ["score.1"]


def test_a_score_hotkey_works_with_no_board_built(autonaApp):
    """The structural fix: the hotkey no longer needs a panel to exist.

    Connected per board, Ctrl+Shift+1 did nothing at all when the operator's
    saved battle format was crew (board 3 never wired the score hotkeys), and
    scored *both* boards after a singles/team switch.
    """
    from src.AutonaHotkeys import AutonaHotkeys
    from src.core.Actions import AutonaActions

    autonaApp.dropAllBoards()
    autonaApp.settle()
    assert AutonaActions.instance.ActiveBoard() == 1
    assert autonaApp.get("score.1") is None

    AutonaHotkeys.signals.team1_score_up.emit()
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.score") == 1


def test_the_score_hotkey_is_routed_exactly_once(autonaApp):
    """ConnectActions() has to stay idempotent.

    `signals` is class-level and outlives every instance, so a second connect
    would leave each score hotkey applying its delta twice -- invisible until
    an operator's +1 came out as +2. bootstrap_core() can run more than once
    in a process (this harness bootstraps its own runtime), which is what the
    guard inside ConnectActions is there for.
    """
    from src.AutonaHotkeys import AutonaHotkeys
    from src.StateManager import StateManager

    autonaApp.dropAllBoards()
    # dropAllBoards() only clears score.{n} for boards that were actually
    # built, so with none built it leaves whatever the previous test wrote.
    # The count below is the whole assertion, so start from nothing.
    StateManager.Unset("score.1")
    autonaApp.settle()
    assert autonaApp.get("score.1.team.1.score") is None

    # Back to un-routed, then route twice: the second call must do nothing.
    # DisconnectActions exists for exactly this -- there is no object whose
    # destruction would release a class-level connection.
    AutonaHotkeys.DisconnectActions()
    AutonaHotkeys.ConnectActions()
    AutonaHotkeys.ConnectActions()

    AutonaHotkeys.signals.team1_score_up.emit()
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.score") == 1, (
        "one hotkey press did not produce exactly one +1")


def test_the_sponsor_toggle_publishes_once_and_does_not_echo(autonaApp):
    """The desktop display-option control goes through Actions now.

    display_options.* is not held, so this is the operation where a delta is
    directly observable: exactly one revision per toggle, and the board's own
    mirror (the QAction, the checkbox, the inline sponsor field) must not
    produce a second one.
    """
    from src.StateManager import StateManager

    board = autonaApp.board(1)
    autonaApp.settle()
    with _restoredDisplayOption("show_sponsor"):
        revision = StateManager.revision
        board.hideSponsorCheck.setChecked(True)
        autonaApp.settle()

        assert autonaApp.get("display_options.show_sponsor") is False
        assert StateManager.revision == revision + 1, (
            "the sponsor toggle published more than once: the board wrote "
            "the state change back after receiving it")
        # The invisible carrier the overlays' Qt-side equivalent reads.
        assert board._eyeActions["show_sponsor"].isChecked() is False


def test_a_remote_display_option_reaches_a_built_board(autonaApp):
    """POST /display-option no longer pokes each board's QAction.

    It writes the option once; boards mirror it from the state notification.
    Before Step 3 the route walked boards 1 and 2 -- and *built* both of them
    as a side effect of a Web UI eye-menu click.
    """
    board = autonaApp.board(1)
    autonaApp.settle()
    with _restoredDisplayOption("show_seed"):
        autonaApp.actions().set_display_option(
            {"option": "show_seed", "value": False})
        autonaApp.settle()

        assert autonaApp.get("display_options.show_seed") is False
        assert board._eyeActions["show_seed"].isChecked() is False


def test_an_unknown_display_option_is_still_rejected(autonaApp):
    autonaApp.board(1)
    result = autonaApp.actions().set_display_option(
        {"option": "show_nothing", "value": True})

    assert result.startswith("ERROR")
    assert autonaApp.get("display_options.show_nothing") is None


# ── the individual game tracker, switched ON ─────────────────────────────
#
# The tracker ships disabled, so every test above runs with no stage rows at
# all -- which is exactly the configuration in which the score's two writers
# (AutonaActions and the tracker's own stage tally) cannot collide. With it on
# they both write score.{n}.team.{t}.score, and the notification from one is
# indistinguishable from an operator action to the other unless the tracker
# says which writes are its own (AutonaIndividualGameTracker.writingScore).
#
# Note on save cycles: an operation here produces TWO held_state_updated
# notifications, not the one the tracker-off tests assert. The first is the
# score write; the second is the stage row the tracker sets in response, which
# is a second (held) save cycle. That is the accepted cost of the tracker
# being on -- nothing is published either way until 反映 -- and it is asserted
# below so a change in the count is noticed rather than discovered.


@pytest.fixture
def trackedBoard(autonaApp):
    """Board 1 with the game tracker enabled and five stage rows built.

    The setting is patched rather than written: SettingsManager is
    process-global and shared with the rest of the session.
    """
    with patch.object(SettingsManager, "Get", side_effect=_trackerEnabled):
        board = autonaApp.board(1)
        board.bestOfSpin.setValue(5)
        autonaApp.settle()
        assert len(board.individualGameTracker.stage_widget_list) == 5
        yield board


def test_a_stage_click_awards_exactly_the_game_that_was_clicked(
        autonaApp, trackedBoard):
    """Regression: one click used to win the next game as well.

    StageResultsChanged writes the score it summed from the rows, and that
    write notifies synchronously -- reaching _SyncScoreDisplays while the
    spinbox still holds the old value. Read as an ordinary 0 -> 1 transition
    it called SyncStagesToScore, which awarded the first *empty* stage: game
    2, on top of the game 1 the operator actually clicked.
    """
    _stageButton(trackedBoard, game=1, team=1).click()
    autonaApp.settle()

    assert _score(autonaApp, 1) == 1
    assert _score(autonaApp, 2) == 0
    assert _wonGames(autonaApp, 1) == [1], (
        "a single stage click marked more than the game it was on: the "
        "board reacted to the tracker's own score write by asking the "
        "tracker to award another stage for it")


def test_a_stage_click_is_exactly_two_save_cycles(autonaApp, trackedBoard):
    """One click = the row it wrote, then the score that follows from it.

    History, because the number has been wrong twice. Before headless core
    Step 4a a click ran six held cycles: three bare Sets for the result
    flags, the summed score, and two more from StageResultsToScore re-Setting
    what the tracker had just written. Step 4a folded the flags into one
    SaveBlock (six -> four) and the review of it removed the redundant pair
    (four -> two). Two is the floor without merging the row write and the
    score write into a single cycle, which would need one writer for both.

    Nothing is published while the board is held either way, so this is not
    a correctness bound -- it is the tripwire for a third writer creeping
    back in, which is exactly how the extra pair survived Step 4a.
    """
    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        _stageButton(trackedBoard, game=1, team=1).click()
        autonaApp.settle()
    finally:
        unsubscribe()

    assert seen == ["score.1", "score.1"], (
        f"a stage click produced {len(seen)} save cycles instead of 2")


def test_stage_clicks_keep_tracking_each_game_independently(
        autonaApp, trackedBoard):
    """The other half of the fix: suppression must not disable the tracker."""
    _stageButton(trackedBoard, game=1, team=1).click()
    _stageButton(trackedBoard, game=2, team=2).click()
    _stageButton(trackedBoard, game=3, team=1).click()
    autonaApp.settle()

    assert _score(autonaApp, 1) == 2
    assert _score(autonaApp, 2) == 1
    assert _wonGames(autonaApp, 1) == [1, 3]
    assert _wonGames(autonaApp, 2) == [2]


def test_unclicking_a_stage_takes_the_score_back_down(
        autonaApp, trackedBoard):
    """The mirror image: clicking a checked button clears that game.

    Without the guard this ran DecrementScore on top of the operator's own
    un-check and cleared a second game.
    """
    _stageButton(trackedBoard, game=1, team=1).click()
    _stageButton(trackedBoard, game=2, team=1).click()
    autonaApp.settle()
    assert _wonGames(autonaApp, 1) == [1, 2]

    _stageButton(trackedBoard, game=2, team=1).click()
    autonaApp.settle()

    assert _score(autonaApp, 1) == 1
    assert _wonGames(autonaApp, 1) == [1]


def test_a_score_button_with_the_tracker_on_marks_one_stage(
        autonaApp, trackedBoard):
    """The normal case, which the guard must leave working.

    A +1 that did NOT come from the tracker is exactly the transition the
    stage rows are supposed to follow: the score goes up by one and the first
    empty game is marked for that team.
    """
    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        trackedBoard.CommandScoreChange(0, 1)
        autonaApp.settle()
    finally:
        unsubscribe()

    assert _score(autonaApp, 1) == 1
    assert _wonGames(autonaApp, 1) == [1]
    # Score write, then the stage row written in response -- see the section
    # note above. The tracker-off path is one cycle
    # (test_a_score_change_is_exactly_one_save_cycle).
    assert seen == ["score.1", "score.1"], (
        f"a +1 with the tracker on produced {len(seen)} save cycles "
        "instead of 2")


def test_a_score_reset_with_the_tracker_on_clears_every_stage(
        autonaApp, trackedBoard):
    trackedBoard.CommandScoreChange(0, 2)
    trackedBoard.CommandScoreChange(1, 1)
    autonaApp.settle()
    assert _wonGames(autonaApp, 1) == [1, 2]
    assert _wonGames(autonaApp, 2) == [3]

    trackedBoard.ResetScore()
    autonaApp.settle()

    assert _score(autonaApp, 1) == 0 and _score(autonaApp, 2) == 0
    assert _wonGames(autonaApp, 1) == []
    assert _wonGames(autonaApp, 2) == []


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
