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
from qtpy.QtWidgets import (
    QCheckBox, QComboBox, QLineEdit, QPushButton, QWidget)
from shiboken6 import Shiboken

from src.core.SettingsManager import SettingsManager

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
    from src.core.StateManager import StateManager

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


# The set length is the state's since ADR-005 S2e: AutonaActions.SetBestOf
# writes it and resizes a built board's rows, from whichever entry point,
# and the spinbox and the rows render it from the board's held cycle.

def test_a_best_of_set_in_core_reaches_the_spinbox_and_the_rows(autonaApp):
    """The way the Web, /api/v1 and a set load set it: not through the
    spinbox. One save cycle -- the render writes nothing."""
    from src.core.Actions import AutonaActions

    with patch.object(SettingsManager, "Get", side_effect=_trackerEnabled):
        board = autonaApp.board(1)
        tracker = board.individualGameTracker
        seen, unsubscribe = _heldUpdateSpy("score.1")
        try:
            AutonaActions.instance.SetBestOf(1, 7)
            autonaApp.settle()
        finally:
            unsubscribe()

    assert seen == ["score.1"]
    assert board.bestOfSpin.value() == 7
    assert len(tracker.stage_widget_list) == 7
    assert len(autonaApp.get("score.1.stages")) == 7
    assert not tracker.isHidden()


def test_a_best_of_set_from_another_thread_reaches_the_board(autonaApp):
    """AutonaActions may be called from any thread; the board renders on
    its own, from the held cycle Qt queues across."""
    import threading

    from src.core.Actions import AutonaActions

    with patch.object(SettingsManager, "Get", side_effect=_trackerEnabled):
        board = autonaApp.board(1)
        worker = threading.Thread(
            target=lambda: AutonaActions.instance.SetBestOf(1, 5))
        worker.start()
        worker.join()
        autonaApp.settle()

    assert autonaApp.get("score.1.best_of") == 5
    assert board.bestOfSpin.value() == 5
    assert len(board.individualGameTracker.stage_widget_list) == 5


def test_a_game_load_starts_the_rows_over_at_the_same_length(autonaApp):
    """The rows recorded games of the previous game's stages: the buttons
    are built again, unchecked, though their number does not change."""
    with patch.object(SettingsManager, "Get", side_effect=_trackerEnabled):
        board = autonaApp.board(1)
        tracker = board.individualGameTracker
        board.bestOfSpin.setValue(3)
        autonaApp.settle()
        _stageButton(board, game=1, team=1).click()
        autonaApp.settle()
        rows = tracker.stage_order_list_widget

        board._onGameAssetsLoaded()
        autonaApp.settle()

    assert tracker.stage_order_list_widget is not rows
    assert len(tracker.stage_widget_list) == 3
    assert _wonGames(autonaApp, 1) == []
    assert not _stageButton(board, game=1, team=1).isChecked()


def test_a_dropped_board_unregisters_its_game_rows(autonaApp):
    """The registration ends with the board: a set length set afterwards is
    the six keys alone, as on any board that is not built."""
    from src.core.Actions import AutonaActions

    actions = AutonaActions.instance
    board = autonaApp.board(1)
    assert actions._gameTrackers.get(1) is board.individualGameTracker.service
    del board

    autonaApp._dropBoard(1)

    assert 1 not in actions._gameTrackers
    try:
        with patch.object(SettingsManager, "Get",
                          side_effect=_trackerEnabled):
            actions.SetBestOf(1, 5)
        autonaApp.settle()
        assert autonaApp.get("score.1.best_of") == 5
        assert autonaApp.get("score.1.stages") is None
    finally:
        autonaApp.state.Unset("score.1")
        autonaApp.settle()


def test_a_click_on_a_game_the_set_no_longer_has_is_refused(autonaApp):
    """A set length set from another thread resizes the rows at once; the
    buttons are rebuilt when the held cycle reaches the GUI thread. A click
    in between on a row that is gone records nothing -- no stray
    `stages.5` -- and the score is not summed from it."""
    import threading

    from src.core.Actions import AutonaActions

    with patch.object(SettingsManager, "Get", side_effect=_trackerEnabled):
        board = autonaApp.board(1)
        tracker = board.individualGameTracker
        board.bestOfSpin.setValue(5)
        autonaApp.settle()
        stale = _stageButton(board, game=5, team=1)

        worker = threading.Thread(
            target=lambda: AutonaActions.instance.SetBestOf(1, 3))
        worker.start()
        worker.join()
        assert len(tracker.stage_widget_list) == 5, (
            "precondition: the buttons are not rebuilt yet")

        stale.click()
        assert sorted(autonaApp.get("score.1.stages")) == ["1", "2", "3"]
        assert _score(autonaApp, 1) == 0

        autonaApp.settle()

    assert len(tracker.stage_widget_list) == 3
    assert sorted(autonaApp.get("score.1.stages")) == ["1", "2", "3"]
    assert _score(autonaApp, 1) == 0 and _wonGames(autonaApp, 1) == []


def test_a_failing_match_text_does_not_stop_what_sets_the_best_of(
        autonaApp, monkeypatch):
    """An empty matchNames (the locale tables not loaded) makes the Qt
    formatter raise. The set length is published with the English texts,
    and the callers that reach it directly since ADR-005 S2e go on: a set
    load applies its losers and players, the Web answers, a game load
    commits."""
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    from src.core.StateManager import StateManager

    with patch.object(SettingsManager, "Get", side_effect=_trackerEnabled):
        board = autonaApp.board(1)
        monkeypatch.setattr(AutonaLocaleHelper, "matchNames", {})

        board.ChangeSetData({
            "bestOf": 5, "team1losers": True, "overwrite": True,
            "entrants": [[{"gamerTag": "AAA"}], [{"gamerTag": "BBB"}]]})
        autonaApp.settle()

        assert autonaApp.get("score.1.best_of") == 5
        assert autonaApp.get("score.1.best_of_text") == "Best of 5"
        assert len(board.individualGameTracker.stage_widget_list) == 5
        assert autonaApp.get("score.1.team.1.losers") is True
        assert _playerName(autonaApp, 1) == "AAA"
        assert _playerName(autonaApp, 2) == "BBB"

        assert autonaApp.actions().set_route(1, bestOf=7) == "OK"
        autonaApp.settle()
        assert autonaApp.get("score.1.first_to_text") == "First to 4"

        board._onGameAssetsLoaded()
        autonaApp.settle()

    assert StateManager.HasPendingChanges("score.1") is False
    assert StateManager.GetCommitted("score.1.best_of_text") == "Best of 7"
    assert autonaApp.state.saveBlocked == 0


def test_a_set_with_a_best_of_that_is_not_a_number_still_loads(autonaApp):
    """The one key is refused (a warning) and the rest of the set applies;
    the spinbox's TypeError used to abandon the losers and the players."""
    board = autonaApp.board(1)
    board.bestOfSpin.setValue(3)
    autonaApp.settle()

    board.ChangeSetData({
        "bestOf": "five", "team1losers": True, "overwrite": True,
        "entrants": [[{"gamerTag": "AAA"}], [{"gamerTag": "BBB"}]]})
    autonaApp.settle()

    assert autonaApp.get("score.1.best_of") == 3
    assert board.bestOfSpin.value() == 3
    assert autonaApp.get("score.1.team.1.losers") is True
    assert _playerName(autonaApp, 1) == "AAA"
    assert _playerName(autonaApp, 2) == "BBB"


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


def test_a_swap_is_one_save_cycle_and_the_cards_show_it(autonaApp):
    board = autonaApp.board(1)
    _setPlayerName(board, 1, "LEFT")
    _setPlayerName(board, 2, "RIGHT")
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        board.SwapTeams()
        autonaApp.settle()
    finally:
        unsubscribe()

    assert seen == ["score.1"]
    tag = board.team1playerWidgets[0].findChild(QLineEdit, "name")
    assert tag.text() == "RIGHT"
    tag = board.team2playerWidgets[0].findChild(QLineEdit, "name")
    assert tag.text() == "LEFT"


# The swap is a core operation since ADR-005 S2c (AutonaActions.SwapTeams).
# The hotkey calls it for the active board; the Web UI builds the board
# first, as it always did (an unbuilt board is not held).


_STATS_SIGNALS = ("RecentSetsSignal", "LastSetsP1Signal", "LastSetsP2Signal",
                  "PlayerHistoryStandingsP1Signal",
                  "PlayerHistoryStandingsP2Signal", "UpsetFactorCalculation")


@contextlib.contextmanager
def _statsFetches(board):
    """How often each of the board's statistics triggers fired."""
    counts = dict.fromkeys(_STATS_SIGNALS, 0)
    slots = []
    for name in _STATS_SIGNALS:
        def slot(name=name):
            counts[name] += 1
        getattr(board.stats.signals, name).connect(slot)
        slots.append((name, slot))
    try:
        yield counts
    finally:
        for name, slot in slots:
            getattr(board.stats.signals, name).disconnect(slot)


@pytest.mark.parametrize("ids, fetches", [
    (([101, 1101], [303, 3303]), 1),    # other players on each side: once
    ((None, None), 0),                  # no ids, nothing to fetch for
])
def test_a_1v1_swap_refetches_the_stats_once(autonaApp, ids, fetches):
    """The swap exchanges the ids in the state with no card involved, so
    the board asks for the statistics itself (_NoticeSwap): once per swap,
    and only when it put other players on the sides. The upset factor is
    recalculated by the operation, not through the trigger."""
    board = autonaApp.board(1)
    for card, value in zip((board.team1playerWidgets[0],
                            board.team2playerWidgets[0]), ids):
        card.player.SetPlayerId(value)
    autonaApp.settle()

    with _statsFetches(board) as counts:
        board.SwapTeams()
        autonaApp.settle()

    assert counts == dict(dict.fromkeys(_STATS_SIGNALS, fetches),
                          UpsetFactorCalculation=0)

def test_the_swap_hotkey_swaps_the_active_board_only(autonaApp):
    """It used to be connected to every built board: after a singles/team
    switch one key press swapped the hidden board as well."""
    from src.AutonaHotkeys import AutonaHotkeys
    from src.core.Actions import AutonaActions

    one = autonaApp.board(1)
    autonaApp.board(2)
    _setPlayerName(one, 1, "LEFT")
    _setPlayerName(one, 2, "RIGHT")
    autonaApp.settle()
    assert AutonaActions.instance.ActiveBoard() == 1

    AutonaHotkeys.signals.swap_teams.emit()
    autonaApp.settle()

    assert autonaApp.get("score.1.teamsSwapped") is True
    assert _playerName(autonaApp, 1) == "RIGHT"
    assert autonaApp.get("score.2.teamsSwapped") is None


def test_the_swap_hotkey_does_nothing_with_no_board_built(autonaApp):
    """As before the swap moved to core (each built board connected the
    hotkey to itself). Unlike the score hotkeys, which do work without a
    panel: an unbuilt board is not held, so a swap there would be published
    at once and then wiped by the seed that builds the board."""
    from src.AutonaHotkeys import AutonaHotkeys
    from src.core.Actions import AutonaActions
    from src.core.StateManager import StateManager

    autonaApp.dropAllBoards()
    StateManager.Unset("score.1")
    autonaApp.settle()
    assert AutonaActions.instance.ActiveBoard() == 1
    try:
        with patch.object(AutonaActions.instance, "SwapTeams") as swap:
            AutonaHotkeys.signals.swap_teams.emit()
            autonaApp.settle()

        swap.assert_not_called()
        assert autonaApp.manager.scoreboards.get(1) is None
        assert autonaApp.get("score.1") is None
    finally:
        StateManager.Unset("score.1")
        autonaApp.settle()


def test_a_web_swap_is_done_when_it_answers(autonaApp):
    """The legacy route used to queue a signal into the board and answer
    "OK" before anything had moved; get_swap read the board's attribute."""
    board = autonaApp.board(1)
    _setPlayerName(board, 1, "LEFT")
    _setPlayerName(board, 2, "RIGHT")
    autonaApp.settle()
    actions = autonaApp.actions()

    assert actions.swap_teams("1") == "OK"
    assert actions.get_swap("1") == "True"
    assert _playerName(autonaApp, 1) == "RIGHT"

    assert actions.swap_teams(1) == "OK"
    assert actions.get_swap(1) == "False"


def test_a_web_swap_builds_the_board_first(autonaApp):
    """An unbuilt board is not held, and building it seeds its subtree
    afresh: a swap made on it before the build would go out at once -- to
    a board no 反映 button shows -- and then be wiped. So the route builds
    the board first, as it always did, and the swap waits for 反映 there
    and survives every later route to the board."""
    from src.core.StateManager import StateManager

    autonaApp.dropAllBoards()
    StateManager.Unset("score.2")
    autonaApp.settle()
    actions = autonaApp.actions()
    try:
        assert actions.swap_teams(2) == "OK"
        autonaApp.settle()

        board = autonaApp.manager.scoreboards.get(2)
        assert board is not None
        assert autonaApp.get("score.2.teamsSwapped") is True
        assert StateManager.HasPendingChanges("score.2") is True
        assert StateManager.GetCommitted("score.2.teamsSwapped") is None

        assert autonaApp.manager.GetScoreboard(2) is board
        assert actions.get_swap(2) == "True"
        actions.get_scoreboard(2)
        autonaApp.settle()
        assert autonaApp.get("score.2.teamsSwapped") is True
    finally:
        autonaApp.dropAllBoards()
        StateManager.Unset("score.2")
        autonaApp.settle()


def test_reading_the_swap_flag_builds_the_board(autonaApp):
    """As before S2c: get_swap reached the flag through the board."""
    from src.core.StateManager import StateManager

    autonaApp.dropAllBoards()
    StateManager.Unset("score.2")
    autonaApp.settle()
    try:
        assert autonaApp.actions().get_swap(2) == "False"
        assert autonaApp.manager.scoreboards.get(2) is not None
    finally:
        autonaApp.dropAllBoards()
        StateManager.Unset("score.2")
        autonaApp.settle()


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
    from src.core.StateManager import StateManager

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
    from src.core.StateManager import StateManager

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
    from src.core.StateManager import StateManager

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


# ── the panel's own fields (headless core Step 5) ────────────────────────
#
# The team card's two inputs and the match bar's three combos used to be
# bound by findChildren loops that wrote score.{N}.team.{T}.{objectName} /
# score.{N}.{objectName}. They call AutonaActions now, against key names that
# live in StateSchema. What that can break is what these check: the operator's
# edit still has to reach the state, the widgets' *other* handlers have to
# survive (the team crest, the " [L]" suffix), and neither may cost an extra
# save cycle.

def test_typing_a_team_name_reaches_the_state(autonaApp):
    board = autonaApp.board(2)  # the team board, where the names are visible
    edit = _teamName(board, 1)

    edit.setText("Alpha")
    edit.editingFinished.emit()
    autonaApp.settle()

    assert autonaApp.get("score.2.team.1.teamName") == "Alpha"


def test_a_team_name_edit_is_exactly_one_save_cycle(autonaApp):
    """Echo-loop check, and the crest handler has to stay free.

    editingFinished runs two handlers: the name write and the team-crest
    lookup. The crest is a no-op when no ./user_data/team_logo file matches
    (SetLoadService._SetIfChanged), so the pair is one cycle -- a second one
    would mean either handler wrote something it did not have to.
    """
    board = autonaApp.board(2)
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.2")
    try:
        edit = _teamName(board, 1)
        edit.setText("Alpha")
        edit.editingFinished.emit()
        autonaApp.settle()
    finally:
        unsubscribe()

    assert autonaApp.get("score.2.team.1.teamName") == "Alpha"
    assert seen == ["score.2"], (
        f"a team name edit produced {len(seen)} save cycles instead of 1")


def test_leaving_a_team_name_unchanged_does_not_light_the_apply_button(
        autonaApp):
    """Behaviour change, pinned deliberately (headless core Step 5).

    editingFinished fires on every focus-out, typed into or not, and the old
    handler wrote unconditionally -- so tabbing through the field marked the
    board dirty. The write is guarded now, the same way the set-load path has
    been since Step 4b.
    """
    from src.core.StateManager import StateManager

    board = autonaApp.board(2)
    edit = _teamName(board, 1)
    edit.setText("Alpha")
    edit.editingFinished.emit()
    StateManager.CommitHeld("score.2")
    autonaApp.settle()
    assert StateManager.HasPendingChanges("score.2") is False

    edit.editingFinished.emit()
    autonaApp.settle()

    assert StateManager.HasPendingChanges("score.2") is False


def test_the_losers_checkbox_writes_the_flag_and_the_player_names(autonaApp):
    """The flag and the " [L]" the thumbnail generator renders, together.

    Both are AutonaActions.SetTeamLosers since ADR-005 S2b, in one save
    cycle. The suffix used to be the checkbox's second handler -- every
    player card's SetLosers, connected per card -- so the set-load path
    needed its own copy (_ApplyLosersToPlayers) because it does not have
    this signal; now the two paths share the core half.
    """
    from src.core.StateManager import StateManager

    board = autonaApp.board(1)
    _setPlayerName(board, 1, "AAA")
    autonaApp.settle()

    cycles = []
    StateManager.observers.held_state_updated.subscribe(cycles.append)
    try:
        _losers(board, 1).setChecked(True)
        autonaApp.settle()
    finally:
        StateManager.observers.held_state_updated.unsubscribe(cycles.append)

    assert autonaApp.get("score.1.team.1.losers") is True
    assert autonaApp.get("score.1.team.1.player.1.mergedName") == "AAA [L]"
    assert autonaApp.get("score.1.team.1.player.1.mergedOnlyName") == "AAA [L]"
    assert cycles.count("score.1") == 1

    _losers(board, 1).setChecked(False)
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.losers") is False
    assert autonaApp.get("score.1.team.1.player.1.mergedName") == "AAA"


def test_a_losers_flip_from_another_thread_mid_name_edit_keeps_the_new_name(
        autonaApp):
    """AutonaActions.SetTeamLosers is core and may run on any thread.

    It rebuilds the team's display names from the slots' state. A name edit
    is two handlers of one editingFinished: the tag reaching the state
    (_OnTextChanged) and the display name being published (NameChanged).
    With the publish first, a flip landing right after it rebuilt the name
    from the previous tag -- "OLD [L]" next to name "NEW" -- and nothing
    published it again. The tag goes first since ADR-005 S2b; the flip is
    run here, on a real second thread, at exactly that point.
    """
    import threading

    from src.core import StateSchema as schema
    from src.core.Actions import AutonaActions

    board = autonaApp.board(1)
    _setPlayerName(board, 1, "OLD")
    autonaApp.settle()
    card = board.team1playerWidgets[0]
    service = card.player
    publish = service.PublishMergedName
    flips = []

    def publishThenFlip(sponsor, tag):
        merged = publish(sponsor, tag)
        if not flips:
            worker = threading.Thread(
                target=lambda: flips.append(
                    AutonaActions.instance.SetTeamLosers(1, 1, True)))
            worker.start()
            worker.join(timeout=10)
            assert not worker.is_alive()
        return merged

    service.PublishMergedName = publishThenFlip
    try:
        edit = card._w(schema.PLAYER_TAG_KEY, QLineEdit)
        edit.setText("NEW")
        edit.editingFinished.emit()
        autonaApp.settle()
    finally:
        del service.PublishMergedName

    assert flips == [True]
    assert autonaApp.get("score.1.team.1.player.1.name") == "NEW"
    assert autonaApp.get("score.1.team.1.player.1.mergedName") == "NEW [L]"
    assert autonaApp.get(
        "score.1.team.1.player.1.mergedOnlyName") == "NEW [L]"


def test_typing_in_the_match_bar_reaches_the_state(autonaApp):
    board = autonaApp.board(1)

    for combo, value in ((board.phaseCombo, "Bracket"),
                         (board.poolCombo, "Pool A"),
                         (board.matchCombo, "Winners Final")):
        combo.setCurrentText(value)
        combo.lineEdit().editingFinished.emit()
    autonaApp.settle()

    assert autonaApp.get("score.1.phase") == "Bracket"
    assert autonaApp.get("score.1.pool") == "Pool A"
    assert autonaApp.get("score.1.match") == "Winners Final"


def test_a_match_bar_edit_is_exactly_one_save_cycle(autonaApp):
    """Two signals, one write.

    Both currentIndexChanged and the line edit's editingFinished are wired to
    the same operation (either can be what changed the text), so picking a
    listed entry used to write the same value twice.
    """
    board = autonaApp.board(1)
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        board.matchCombo.setCurrentText("Winners Final")
        board.matchCombo.lineEdit().editingFinished.emit()
        autonaApp.settle()
    finally:
        unsubscribe()

    assert autonaApp.get("score.1.match") == "Winners Final"
    assert seen == ["score.1"], (
        f"one match-bar edit produced {len(seen)} save cycles instead of 1")


# ── the match bar: an operator's reset vs. a set load's pre-pass (S-17) ──
#
# Two clears that used to be one function. The reset entry points -- the
# すべての入力値をリセット button, and WebServerActions' reset_match /
# clear_all, which every legacy route, Socket.IO event and /api/v1
# :reset-match / :clear delegates to -- blank round name / phase / pool.
# The clear NewSetSelected runs before a new set arrives must not:
# SetLoadService.ApplyMatchInfo keeps the round for a payload that carries
# none, so a reset-style clear there would blank the round mid-stream.

def _confirmed(method):
    """Run a method that asks "are you sure?" first, answering Yes."""
    from qtpy.QtWidgets import QMessageBox

    with patch.object(QMessageBox, "question",
                      return_value=QMessageBox.StandardButton.Yes):
        method()


_RESETS = {
    "ResetAllInputs": lambda app, board, n: _confirmed(board.ResetAllInputs),
    "reset_match": lambda app, board, n: app.actions().reset_match(n),
    "clear_all": lambda app, board, n: app.actions().clear_all(n),
}


@pytest.mark.parametrize("reset", sorted(_RESETS))
@pytest.mark.parametrize("boardNumber", [1, 2])
def test_a_reset_clears_the_match_bar(autonaApp, boardNumber, reset):
    board = autonaApp.board(boardNumber)
    board.ChangeSetData(dict(_SET))
    autonaApp.settle()
    assert _matchFields(board) == ("Winners Final", "Bracket", "Pool A")

    _RESETS[reset](autonaApp, board, boardNumber)
    autonaApp.settle()

    prefix = f"score.{boardNumber}"
    assert autonaApp.get(f"{prefix}.match") == ""
    assert autonaApp.get(f"{prefix}.phase") == ""
    assert autonaApp.get(f"{prefix}.pool") == ""
    # The English form of the same round name (the YouTube title reads it).
    assert autonaApp.get(f"{prefix}.match_en") == ""
    # The combos follow the state, exactly as they do for a loaded set.
    assert _matchFields(board) == ("", "", "")
    # ...and the half of the reset that already worked still does.
    assert (_score(autonaApp, 1, boardNumber),
            _score(autonaApp, 2, boardNumber)) == (0, 0)
    assert autonaApp.get(f"{prefix}.team.2.losers") is False


def test_a_set_without_a_round_name_keeps_the_previous_one(autonaApp):
    """The set-load pre-pass (NewSetSelected -> ClearScore) is not a reset.

    A payload carrying no round_name / tournament_phase / tournament_pool
    leaves the match bar as the previous set had it.
    """
    board = autonaApp.board(1)
    board.ChangeSetData(dict(_SET))
    autonaApp.settle()

    board.NewSetSelected({"id": "1001", "auto_update": "set"})
    autonaApp.settle()
    # The pre-pass alone must not have touched it either.
    assert autonaApp.get("score.1.match") == "Winners Final"

    board.UpdateSetData({"id": "1001", "team1score": 1, "team2score": 0})
    autonaApp.settle()

    assert autonaApp.get("score.1.match") == "Winners Final"
    assert autonaApp.get("score.1.phase") == "Bracket"
    assert autonaApp.get("score.1.pool") == "Pool A"
    assert _matchFields(board) == ("Winners Final", "Bracket", "Pool A")
    assert (_score(autonaApp, 1), _score(autonaApp, 2)) == (1, 0)


def test_a_set_with_a_round_name_overwrites_the_previous_one(autonaApp):
    board = autonaApp.board(1)
    board.ChangeSetData(dict(_SET))
    autonaApp.settle()

    board.NewSetSelected({"id": "1002", "auto_update": "set"})
    autonaApp.settle()
    board.UpdateSetData({
        "id": "1002",
        "round_name": "Losers Final",
        "round_name_en": "Losers Final",
        "tournament_phase": "Top 8",
        "tournament_pool": "Pool B",
    })
    autonaApp.settle()

    assert autonaApp.get("score.1.match") == "Losers Final"
    assert autonaApp.get("score.1.match_en") == "Losers Final"
    assert autonaApp.get("score.1.phase") == "Top 8"
    assert autonaApp.get("score.1.pool") == "Pool B"
    assert _matchFields(board) == ("Losers Final", "Top 8", "Pool B")


def test_clearing_the_score_still_zeroes_the_score_and_the_losers_flags(
        autonaApp):
    # The half of ClearScore that does work, driven through the rewired
    # widgets: the spinboxes still reach GameTrackerService.UpdateScore and
    # the checkboxes still reach AutonaActions.SetTeamLosers.
    board = autonaApp.board(1)
    board.ChangeSetData(dict(_SET))
    autonaApp.settle()
    assert (_score(autonaApp, 1), _score(autonaApp, 2)) == (2, 1)
    assert autonaApp.get("score.1.team.2.losers") is True

    board.ClearScore()
    autonaApp.settle()

    assert (_score(autonaApp, 1), _score(autonaApp, 2)) == (0, 0)
    assert autonaApp.get("score.1.team.2.losers") is False


def test_a_best_of_change_is_exactly_one_save_cycle(autonaApp):
    """The six derived keys and the stage rows are one operation.

    AutonaActions.SetBestOf writes the six keys and, through the board's
    registered tracker, GameTrackerService.SetStageCount in one block, so
    the whole change is a single export (ADR-005 S2e; the spinbox's handler
    wrapped the two before). Tracker off here, which is the shipped default;
    with it on the rows join the same cycle.
    """
    board = autonaApp.board(1)
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        board.bestOfSpin.setValue(5)
        autonaApp.settle()
    finally:
        unsubscribe()

    assert autonaApp.get("score.1.best_of") == 5
    assert autonaApp.get("score.1.first_to_short_text") == "FT3"
    assert seen == ["score.1"], (
        f"a best-of change produced {len(seen)} save cycles instead of 1")


def test_the_display_tournament_name_field_publishes_once(autonaApp):
    """Not held (it is tournamentInfo.*), so the delta is directly visible."""
    from src.core.StateManager import StateManager

    board = autonaApp.board(1)
    autonaApp.settle()
    stored = StateManager.Get("tournamentInfo.displayTournamentName")
    try:
        revision = StateManager.revision
        board.displayTournamentNameEdit.setText("SPECIAL EDITION")
        board.displayTournamentNameEdit.editingFinished.emit()
        autonaApp.settle()

        assert autonaApp.get(
            "tournamentInfo.displayTournamentName") == "SPECIAL EDITION"
        assert StateManager.revision == revision + 1, (
            "the display-name field published more than once: the board wrote "
            "the state change back after receiving it")
    finally:
        StateManager.Set(
            "tournamentInfo.displayTournamentName", stored or "")


def test_building_a_board_republishes_every_display_option(autonaApp):
    """The loop the board used to carry its own copy of.

    An operator's toggles persist in SettingsManager, so building a board is
    what brings a program_state.json restored from an older session back in
    line with them. The definition is StateSchema's now.
    """
    from src.core import StateSchema as schema
    from src.core.StateManager import StateManager

    stored = StateManager.Get("display_options")
    try:
        StateManager.Unset("display_options")
        autonaApp.board(1)
        autonaApp.settle()

        assert set(StateManager.Get("display_options") or {}) == set(
            schema.DISPLAY_OPTION_KEYS)
    finally:
        if stored is not None:
            StateManager.Set("display_options", stored)


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


def test_a_score_moving_several_games_is_followed_in_one_cycle(
        autonaApp, trackedBoard):
    """Three recorded games cleared by one reset: the score, then every row
    of both teams in one more cycle -- not one cycle a row (ADR-005 S2e).
    The same follow serves a +2, a loaded set's score and the clears."""
    trackedBoard.CommandScoreChange(0, 2)
    trackedBoard.CommandScoreChange(1, 1)
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        trackedBoard.ResetScore()
        autonaApp.settle()
    finally:
        unsubscribe()

    assert _wonGames(autonaApp, 1) == [] and _wonGames(autonaApp, 2) == []
    assert seen == ["score.1", "score.1"], (
        f"a reset of three recorded games produced {len(seen)} save cycles "
        "instead of 2")

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        trackedBoard.CommandScoreChange(0, 2)
        autonaApp.settle()
    finally:
        unsubscribe()

    assert _wonGames(autonaApp, 1) == [1, 2]
    assert seen == ["score.1", "score.1"]


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


# ── set loading (headless core Step 4b) ──────────────────────────────────
#
# The board no longer *is* the set data: core/SetLoadService writes it and
# these widgets render it (_SyncSetFields / _SyncScoreDisplays). So the two
# things worth checking through a real board are the ones the service tests
# cannot see -- that the widgets actually follow, and that following does
# not write the value straight back.

def _matchFields(board):
    return (board.matchCombo.currentText(),
            board.phaseCombo.currentText(),
            board.poolCombo.currentText())


_SET = {
    "round_name": "Winners Final",
    "round_name_en": "Winners Final",
    "tournament_phase": "Bracket",
    "tournament_pool": "Pool A",
    "team1score": 2,
    "team2score": 1,
    "team1losers": False,
    "team2losers": True,
}


def test_a_loaded_set_reaches_the_state(autonaApp):
    board = autonaApp.board(1)
    board.ChangeSetData(dict(_SET))
    autonaApp.settle()

    assert autonaApp.get("score.1.match") == "Winners Final"
    assert autonaApp.get("score.1.match_en") == "Winners Final"
    assert autonaApp.get("score.1.phase") == "Bracket"
    assert autonaApp.get("score.1.pool") == "Pool A"
    assert (_score(autonaApp, 1), _score(autonaApp, 2)) == (2, 1)
    assert autonaApp.get("score.1.team.1.losers") is False
    assert autonaApp.get("score.1.team.2.losers") is True


def test_the_board_renders_a_loaded_set(autonaApp):
    """The widgets follow the state now instead of being poked into it."""
    board = autonaApp.board(1)
    board.ChangeSetData(dict(_SET))
    autonaApp.settle()

    assert _matchFields(board) == ("Winners Final", "Bracket", "Pool A")
    assert board.scoreDisplay1.value() == 2
    assert board.scoreDisplay2.value() == 1
    assert _losers(board, 1).isChecked() is False
    assert _losers(board, 2).isChecked() is True


def test_a_set_load_is_exactly_one_save_cycle(autonaApp):
    """Echo-loop check: rendering the set must not write any of it back.

    Tracker OFF, which is the shipped default and what `autonaApp.board(1)`
    gives. With it on, a loaded score also drives the stage rows
    (_SyncScoreDisplays -> SyncStagesToScore), and those writes are held too
    -- so more than one cycle is correct there, exactly as it is for a +1.
    """
    board = autonaApp.board(1)
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        board.ChangeSetData(dict(_SET))
        autonaApp.settle()
    finally:
        unsubscribe()

    assert seen == ["score.1"], (
        f"loading a set produced {len(seen)} save cycles instead of 1: the "
        "board reacted to the state change by writing it again")


def test_reloading_the_same_set_publishes_nothing(autonaApp):
    """The second load is all no-ops, so it must not mark the board dirty."""
    from src.core.StateManager import StateManager

    board = autonaApp.board(1)
    board.ChangeSetData(dict(_SET))
    StateManager.CommitHeld("score.1")
    autonaApp.settle()
    assert StateManager.HasPendingChanges("score.1") is False

    board.ChangeSetData(dict(_SET))
    autonaApp.settle()

    assert StateManager.HasPendingChanges("score.1") is False, (
        "re-applying an identical set lit the 反映 button for edits nobody "
        "made")


def test_a_set_loaded_onto_a_swapped_board_lands_on_the_right_side(autonaApp):
    board = autonaApp.board(2)
    board.SwapTeams()
    autonaApp.settle()
    assert autonaApp.get("score.2.teamsSwapped") is True

    # Two players a side: the team-name half of a set only applies to a
    # multi-player entrant, as it always has.
    board.ChangeSetData(dict(
        _SET,
        entrants=[[{"gamerTag": "A"}, {"gamerTag": "B"}],
                  [{"gamerTag": "C"}, {"gamerTag": "D"}]],
        p1_name="Alpha", p2_name="Bravo", overwrite=True))
    autonaApp.settle()

    # The set's first entrant is shown on the right after a swap, so its
    # score, losers flag, team name and players all belong to state team 2.
    assert (_score(autonaApp, 1, board=2), _score(autonaApp, 2, board=2)) == (1, 2)
    assert autonaApp.get("score.2.team.2.losers") is False
    assert autonaApp.get("score.2.team.1.losers") is True
    assert autonaApp.get("score.2.team.2.teamName") == "Alpha"
    assert autonaApp.get("score.2.team.1.teamName") == "Bravo"
    assert _playerName(autonaApp, 2, board=2) == "A"
    assert _playerName(autonaApp, 1, board=2) == "C"


def test_a_loaded_team_name_reaches_the_line_edit(autonaApp):
    board = autonaApp.board(2)
    board.ChangeSetData({
        "entrants": [[{"gamerTag": "A"}, {"gamerTag": "B"}],
                     [{"gamerTag": "C"}, {"gamerTag": "D"}]],
        "p1_name": "Alpha", "p2_name": "Bravo", "overwrite": True,
    })
    autonaApp.settle()

    assert autonaApp.get("score.2.team.1.teamName") == "Alpha"
    assert _teamName(board, 1).text() == "Alpha"
    assert _teamName(board, 2).text() == "Bravo"


def test_following_a_set_records_which_set_it_is(autonaApp):
    board = autonaApp.board(1)
    board.NewSetSelected({"id": "999", "auto_update": "set"})
    autonaApp.settle()

    assert autonaApp.get("score.1.set_id") == "999"
    assert autonaApp.get("score.1.auto_update") == "set"
    assert board.setLoad.lastSetSelected == "999"


def test_cancelling_the_follow_forgets_the_set(autonaApp):
    board = autonaApp.board(1)
    board.NewSetSelected({"id": "999", "auto_update": "set"})
    autonaApp.settle()

    board.StopAutonaUpdate(clear_variables=True)
    autonaApp.settle()

    assert autonaApp.get("score.1.set_id") is None
    assert autonaApp.get("score.1.auto_update") is None
    assert board.setLoad.lastSetSelected is None


def test_a_stale_async_refresh_is_dropped(autonaApp):
    """An in-flight fetch answering after the operator moved on."""
    board = autonaApp.board(1)
    board.setLoad.lastSetSelected = "999"
    board.ChangeSetData({"round_name": "Winners Final"})
    autonaApp.settle()

    board.UpdateSetData({"id": "111", "round_name": "Losers Round 1",
                         "entrants": []})
    autonaApp.settle()

    assert autonaApp.get("score.1.match") == "Winners Final"


def test_a_loaded_score_reaches_the_stage_rows(autonaApp, trackedBoard):
    """A set's score awards games the same way a +1 does.

    SetLoadService writes team.{t}.score and *nothing under stages*; the
    rows follow through _SyncScoreDisplays -> SyncStagesToScore. The row
    count must be untouched by a set load: only
    GameTrackerService.SetStageCount() keeps it equal to the widget's
    stage_widget_list, so a writer that grew `stages` on its own would leave
    rows nothing renders (see StageCount()).
    """
    with patch.object(SettingsManager, "Get", side_effect=_trackerEnabled):
        trackedBoard.ChangeSetData({"team1score": 2, "team2score": 1})
        autonaApp.settle()

    assert _wonGames(autonaApp, 1) == [1, 2]
    assert _wonGames(autonaApp, 2) == [3]
    assert len(autonaApp.get("score.1.stages") or {}) == 5
    assert len(trackedBoard.individualGameTracker.stage_widget_list) == 5


@pytest.mark.parametrize("games, swapped", [
    # 2-1: the scores cross over with the rows.
    (((1, 2), (2, 1), (3, 1)), {1: [1], 2: [2, 3]}),
    # 1-1: the score does not move, the rows still mirror.
    (((1, 1), (2, 2)), {1: [2], 2: [1]}),
])
def test_a_swap_mirrors_the_games_on_the_rows_and_the_buttons(
        autonaApp, trackedBoard, games, swapped):
    """The swap mirrors the rows in the same save cycle as it exchanges the
    scores (ADR-005 S2c). It used to swap the scores through the spinboxes,
    whose transition the tracker then followed on top of the mirror: T2 T1
    T1 came out as T1 T2 T1, 1-2 on the score and 2-1 on the rows."""
    with patch.object(SettingsManager, "Get", side_effect=_trackerEnabled):
        for game, team in games:
            _stageButton(trackedBoard, game=game, team=team).click()
        autonaApp.settle()

        trackedBoard.SwapTeams()
        autonaApp.settle()

    for team in (1, 2):
        assert _wonGames(autonaApp, team) == swapped[team]
        assert _score(autonaApp, team) == len(swapped[team])
        for game in range(1, 6):
            assert _stageButton(trackedBoard, game, team).isChecked() \
                == (game in swapped[team]), (team, game)


def test_a_reset_set_load_does_not_reshuffle_recorded_games(
        autonaApp, trackedBoard):
    """Behaviour change, pinned deliberately (headless core Step 4b).

    /post_data always sends reset_score, and the old path acted on it
    literally: the spinboxes went to 0 (clearing every row) and then back up
    (re-awarding from the top), so a set whose wins alternated came back
    reordered -- T2, T1, T1 turned into T1, T1, T2. The zero and the real
    value are one save cycle now, so the rows only move by however much the
    score actually moved.
    """
    with patch.object(SettingsManager, "Get", side_effect=_trackerEnabled):
        _stageButton(trackedBoard, game=1, team=2).click()
        _stageButton(trackedBoard, game=2, team=1).click()
        _stageButton(trackedBoard, game=3, team=1).click()
        autonaApp.settle()
        assert _wonGames(autonaApp, 1) == [2, 3]
        assert _wonGames(autonaApp, 2) == [1]

        trackedBoard.ChangeSetData(
            {"reset_score": True, "team1score": 2, "team2score": 1})
        autonaApp.settle()

    assert _wonGames(autonaApp, 1) == [2, 3]
    assert _wonGames(autonaApp, 2) == [1]


def test_a_loaded_losers_flag_reaches_the_player_names(autonaApp):
    """The " [L]" suffix the thumbnail generator renders.

    The checkbox is only rendered by a set load (_SyncSetFields sets it with
    its signals blocked, because it is the render side and may not write),
    so the names cannot come from its `toggled`: SetLoadService.ApplyLosers
    writes the flag and its team's names together (ADR-005 S2b). Before
    that the board carried the flag into each card by hand
    (_ApplyLosersToPlayers), and without it the state and the checkbox said
    "losers" while the exported name did not.
    """
    board = autonaApp.board(1)
    board.ChangeSetData({
        "entrants": [[{"gamerTag": "AAA"}], [{"gamerTag": "BBB"}]],
        "team1losers": False, "team2losers": True, "overwrite": True,
    })
    autonaApp.settle()

    assert autonaApp.get("score.1.team.2.mergedName") is None  # team-level
    assert autonaApp.get("score.1.team.2.player.1.mergedName") == "BBB [L]"
    assert autonaApp.get("score.1.team.1.player.1.mergedName") == "AAA"
    # The carriers of the same fact must agree.
    assert autonaApp.get("score.1.team.2.losers") is True
    assert _losers(board, 2).isChecked() is True
    assert autonaApp.get("score.1.team.1.losers") is False
    assert _losers(board, 1).isChecked() is False


def test_a_loaded_losers_flag_follows_the_swap(autonaApp):
    board = autonaApp.board(1)
    board.SwapTeams()
    autonaApp.settle()

    board.ChangeSetData({
        "entrants": [[{"gamerTag": "AAA"}], [{"gamerTag": "BBB"}]],
        "team1losers": True, "team2losers": False, "overwrite": True,
    })
    autonaApp.settle()

    # The set's first entrant is on the right after a swap, so its flag and
    # its player both belong to state team 2.
    assert autonaApp.get("score.1.team.2.player.1.mergedName") == "AAA [L]"
    assert autonaApp.get("score.1.team.2.losers") is True
    assert autonaApp.get("score.1.team.1.player.1.mergedName") == "BBB"
    assert autonaApp.get("score.1.team.1.losers") is False


def test_clearing_the_score_still_clears_the_losers_names(autonaApp):
    """ClearScore drives the checkbox directly (no blocker), so the names
    lose their suffix through its `toggled` -- AutonaActions.SetTeamLosers,
    the same core half a set load uses (SetLoadService.ApplyLosers)."""
    board = autonaApp.board(1)
    board.ChangeSetData({
        "entrants": [[{"gamerTag": "AAA"}], [{"gamerTag": "BBB"}]],
        "team2losers": True, "overwrite": True,
    })
    autonaApp.settle()
    assert autonaApp.get("score.1.team.2.player.1.mergedName") == "BBB [L]"

    board.ClearScore()
    autonaApp.settle()

    assert autonaApp.get("score.1.team.2.losers") is False
    assert autonaApp.get("score.1.team.2.player.1.mergedName") == "BBB"
    assert _losers(board, 2).isChecked() is False


def test_swapping_teams_carries_the_losers_names_across(autonaApp):
    """The flags and the names cross over together in the state
    (AutonaActions.SwapTeams): a name's " [L]" came from its team's flag,
    and the flag goes with it, so nothing is re-derived. The checkboxes
    render the result."""
    board = autonaApp.board(1)
    board.ChangeSetData({
        "entrants": [[{"gamerTag": "AAA"}], [{"gamerTag": "BBB"}]],
        "team1losers": False, "team2losers": True, "overwrite": True,
    })
    autonaApp.settle()

    board.SwapTeams()
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.losers") is True
    assert autonaApp.get("score.1.team.2.losers") is False
    assert autonaApp.get("score.1.team.1.player.1.mergedName") == "BBB [L]"
    assert autonaApp.get("score.1.team.2.player.1.mergedName") == "AAA"
    assert _losers(board, 1).isChecked() is True
    assert _losers(board, 2).isChecked() is False


# ── clearing (ADR-005 S2d) ───────────────────────────────────────────────
#
# The clears are core operations (AutonaActions.ClearScore / ResetMatch /
# ClearPlayers / ClearBoard): one save cycle each, where the board used to
# drive its widgets back to empty -- a cycle per player card, per score
# spinbox and per losers box. What a board still adds is checked here:
# the cards follow, the set loaded last is forgotten, the statistics are
# fetched once, and the Web builds the board first.

_CLEARS = {
    "CommandClearAll": lambda app, board, n: board.CommandClearAll(),
    "ResetAllInputs": lambda app, board, n: _confirmed(board.ResetAllInputs),
    "reset_players": lambda app, board, n: app.actions().reset_players(n),
    "clear_all": lambda app, board, n: app.actions().clear_all(n),
}


@pytest.mark.parametrize("clear", ["CommandClearAll", "ResetAllInputs"])
def test_a_clear_is_one_save_cycle_and_the_cards_show_it(autonaApp, clear):
    board = autonaApp.board(2)
    _setPlayerName(board, 1, "LEFT")
    _setPlayerName(board, 2, "RIGHT")
    board.CommandScoreChange(0, 2)
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.2")
    try:
        _CLEARS[clear](autonaApp, board, 2)
        autonaApp.settle()
    finally:
        unsubscribe()

    assert seen == ["score.2"]
    for card in board.playerWidgets:
        assert card.findChild(QLineEdit, "name").text() == ""
    assert not _playerName(autonaApp, 1, board=2)
    expectedScore = 0 if clear == "ResetAllInputs" else 2
    assert _score(autonaApp, 1, board=2) == expectedScore
    assert board.scoreDisplay1.value() == expectedScore


def test_a_board_reset_takes_the_team_names_and_keeps_the_counts(autonaApp):
    """すべての入力値をリセット: values only. The Web's clear_all keeps the
    names instead (unless it shrinks the roster to one player) and resets
    the counts -- the two differ, as they always have (ADR-005 S2
    decision 3)."""
    board = autonaApp.board(2)
    board.playerNumber.setValue(2)
    autonaApp.settle()
    for team in (1, 2):
        edit = _teamName(board, team)
        edit.setText(f"Team {team}")
        edit.editingFinished.emit()
    autonaApp.settle()

    _confirmed(board.ResetAllInputs)
    autonaApp.settle()

    assert autonaApp.get("score.2.team.1.teamName") == ""
    assert autonaApp.get("score.2.team.2.teamName") == ""
    assert _teamName(board, 1).text() == ""
    assert board.playerNumber.value() == 2
    assert len(autonaApp.get("score.2.team.1.player")) == 2


@pytest.mark.parametrize("clear", sorted(_CLEARS))
@pytest.mark.parametrize("ids, fetches", [
    (([101, 1101], [303, 3303]), 1),    # players gone from both sides: once
    ((None, None), 0),                  # no ids went, nothing to fetch for
])
def test_a_clear_refetches_the_stats_once(autonaApp, clear, ids, fetches):
    """A card used to ask for the statistics when its own Clear() dropped
    its id (one fetch per card); the board asks once per clear now, and
    the upset factor is recalculated by the operation."""
    board = autonaApp.board(1)
    for card, value in zip((board.team1playerWidgets[0],
                            board.team2playerWidgets[0]), ids):
        card.player.SetPlayerId(value)
        card.player.SetText("name", "SOMEONE")
    autonaApp.settle()

    with _statsFetches(board) as counts:
        _CLEARS[clear](autonaApp, board, 1)
        autonaApp.settle()

    assert autonaApp.get("score.1.team.1.player.1.id") is None
    assert counts == dict(dict.fromkeys(_STATS_SIGNALS, fetches),
                          UpsetFactorCalculation=0)


@pytest.mark.parametrize("clear", sorted(_CLEARS))
def test_the_same_set_loads_again_after_a_clear(autonaApp, clear):
    """NewSetSelected skips the set the board already follows; a clear
    forgets it (it lives in SetLoadService now, not in the state)."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider

    board = autonaApp.board(1)
    actions = autonaApp.actions()
    provider = AutonaTournamentDataProvider.instance
    with patch.object(provider, "GetMatch") as fetched:
        try:
            board.NewSetSelected({"id": "999", "auto_update": "set"})
            board.NewSetSelected({"id": "999", "auto_update": "set"})
            autonaApp.settle()
            assert fetched.call_count == 1
            assert actions.get_set(1) == "999"

            _CLEARS[clear](autonaApp, board, 1)
            autonaApp.settle()
            assert actions.get_set(1) == "0"

            board.NewSetSelected({"id": "999", "auto_update": "set"})
            autonaApp.settle()
            assert fetched.call_count == 2
        finally:
            board.StopAutonaUpdate(clear_variables=True)
            autonaApp.settle()


_WEB_CLEARS = {
    "reset_match": lambda actions: actions.reset_match(2),
    "reset_players": lambda actions: actions.reset_players(2),
    "clear_all": lambda actions: actions.clear_all(2),
    "get_set": lambda actions: actions.get_set(2),
    "set_route": lambda actions: actions.set_route(2, match="Grand Final"),
}


@pytest.mark.parametrize("route", sorted(_WEB_CLEARS))
def test_a_web_clear_builds_the_board_first(autonaApp, route):
    """As GetScoreboard() did before S2d: an unbuilt board is not held,
    and building it seeds its subtree afresh, so the route builds it and
    the write waits for 反映 there."""
    from src.core.StateManager import StateManager

    autonaApp.dropAllBoards()
    StateManager.Unset("score.2")
    autonaApp.settle()
    actions = autonaApp.actions()
    try:
        answer = _WEB_CLEARS[route](actions)
        autonaApp.settle()

        board = autonaApp.manager.scoreboards.get(2)
        assert board is not None
        assert autonaApp.manager.GetScoreboard(2) is board
        state = autonaApp.get("score.2")
        if route in ("reset_match", "clear_all"):
            for team in ("1", "2"):
                assert state["team"][team]["score"] == 0
                assert state["team"][team]["losers"] is False
            assert (state["match"], state["phase"], state["pool"])                 == ("", "", "")
            assert state["best_of"] == 0
        if route in ("reset_players", "clear_all"):
            for team in ("1", "2"):
                for slot in state["team"][team]["player"].values():
                    # A fresh slot has no text keys; the clear adds none.
                    assert not slot.get("name") and not slot.get("team")
                    assert slot["seed"] == 0
                    assert slot["online_avatar"] is None
            assert all(not card.findChild(QLineEdit, "name").text()
                       for card in board.playerWidgets)
        if route == "clear_all":
            assert len(state["team"]["1"]["player"]) == 1
            assert board.playerNumber.value() == 1
        if route == "get_set":
            assert answer == "0"
        if route == "set_route":
            assert autonaApp.get("score.2.match") == "Grand Final"
            assert StateManager.HasPendingChanges("score.2") is True
            assert StateManager.GetCommitted("score.2.match") \
                != "Grand Final"
            assert board.matchCombo.currentText() == "Grand Final"
    finally:
        autonaApp.dropAllBoards()
        StateManager.Unset("score.2")
        autonaApp.settle()


def test_a_web_team_name_is_one_save_cycle_with_its_crest(autonaApp):
    """set_route's teamName= writes the literal column, as the operator
    typing into it: the name, and the crest that follows it."""
    # A crest for "Alpha" only (SetLoadService.PublishTeamLogo looks it up
    # by the lower-cased name).
    crest = autonaApp.dir / "user_data" / "team_logo" / "alpha.png"
    crest.parent.mkdir(parents=True, exist_ok=True)
    crest.write_bytes(b"")
    try:
        board = autonaApp.board(2)
        autonaApp.settle()

        seen, unsubscribe = _heldUpdateSpy("score.2")
        try:
            autonaApp.actions().set_route(2, team="1", teamName="Alpha")
            autonaApp.settle()
            # The same name again writes nothing.
            autonaApp.actions().set_route(2, team="1", teamName="Alpha")
            autonaApp.settle()
        finally:
            unsubscribe()

        assert seen == ["score.2"]
        assert autonaApp.get("score.2.team.1.teamName") == "Alpha"
        assert autonaApp.get("score.2.team.1.logo")             == "./user_data/team_logo/alpha.png"
        assert _teamName(board, 1).text() == "Alpha"

        # A name without a crest takes the crest away, in the same cycle.
        seen, unsubscribe = _heldUpdateSpy("score.2")
        try:
            autonaApp.actions().set_route(2, team="1", teamName="Bravo")
            autonaApp.settle()
        finally:
            unsubscribe()
        assert seen == ["score.2"]
        assert autonaApp.get("score.2.team.1.teamName") == "Bravo"
        assert autonaApp.get("score.2.team.1.logo") is None
    finally:
        crest.unlink(missing_ok=True)


def test_a_clear_keeps_the_city(autonaApp):
    """Known gap, recorded rather than fixed (ADR-005 S2 decision 5): no
    player card's Clear() ever emptied the city, so the clear does not
    either. A player loaded from start.gg leaves theirs behind."""
    board = autonaApp.board(1)
    card = board.team1playerWidgets[0]
    card.player.SetCity("Shibuya")
    card.player.SetText("name", "SOMEONE")
    autonaApp.settle()

    board.CommandClearAll()
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.player.1.name") == ""
    assert autonaApp.get("score.1.team.1.player.1.city") == "Shibuya"


# ── the player card's write path (headless core Step 5, instalment 2) ────
#
# The card's inputs are wired to core/PlayerService by name now
# (StateSchema.PLAYER_INPUT_KEYS) instead of through a findChildren loop that
# used each widget's objectName as the last key segment. The service is
# tested without Qt in tests/test_player_service.py; what needs a real widget
# is the wiring itself -- that each input still reaches its key, that the
# board does not write anything back when it renders the result, and that the
# roster's own size changes still take the state with them.


def _tagEdit(board, team, index=0):
    from src.core import StateSchema as schema

    widgets = board.team1playerWidgets if team == 1 else board.team2playerWidgets
    return widgets[index].findChild(QLineEdit, schema.PLAYER_TAG_KEY)


def test_typing_a_player_tag_reaches_its_key(autonaApp):
    board = autonaApp.board(1)
    autonaApp.settle()

    edit = _tagEdit(board, 1)
    edit.setText("TAG")
    edit.editingFinished.emit()
    autonaApp.settle()

    assert _playerName(autonaApp, 1) == "TAG"
    assert autonaApp.get("score.1.team.1.player.1.mergedName") == "TAG"


def test_a_player_tag_edit_is_exactly_three_save_cycles(autonaApp):
    """Echo-loop check, with the number stated so a fourth writer is noticed.

    One editingFinished runs three operations, and each is a cycle of its
    own: the display names (`PlayerService.PublishMergedName`, one block for
    the two spellings), the name-derived images (`PublishImages`, one block
    for the avatar and the sponsor crests), and the tag itself. The player id
    is a fourth handler but writes nothing when the slot has not changed
    hands.

    It was four before the player instalment -- the two name spellings were
    bare Sets. Nothing is published while the board is held either way, so
    this is a tripwire rather than a correctness bound.
    """
    board = autonaApp.board(1)
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        edit = _tagEdit(board, 1)
        edit.setText("TAG")
        edit.editingFinished.emit()
        autonaApp.settle()
    finally:
        unsubscribe()

    assert _playerName(autonaApp, 1) == "TAG"
    assert seen == ["score.1"] * 3, (
        f"a player tag edit produced {len(seen)} save cycles instead of 3")


def test_finishing_an_edit_in_the_country_combo_writes_no_stray_key(autonaApp):
    """A pre-existing leak the static wiring closes.

    The country and region combos are editable, so each owns an internal
    QLineEdit whose objectName is "". `findChildren(QLineEdit)` reached them,
    so finishing an edit there published `score.1.team.1.player.1.` -- a key
    with an empty last segment, which nothing reads and every export carried.
    """
    from src.core import StateSchema as schema

    board = autonaApp.board(1)
    autonaApp.settle()
    player = board.team1playerWidgets[0]

    combo = player._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
    combo.lineEdit().setText("Japan (JP)")
    combo.lineEdit().editingFinished.emit()
    autonaApp.settle()

    assert "" not in (autonaApp.get(player.path) or {}), (
        "an edit in the country combo wrote a nameless key under the player")


def test_shrinking_the_roster_takes_the_player_slots_with_it(autonaApp):
    board = autonaApp.board(2)
    board.playerNumber.setValue(3)
    autonaApp.settle()
    for team in (1, 2):
        widgets = (board.team1playerWidgets if team == 1
                   else board.team2playerWidgets)
        for index, widget in enumerate(widgets):
            widget.SetData({"gamerTag": f"T{team}P{index + 1}"},
                           dontLoadFromDB=True)
    autonaApp.settle()
    assert autonaApp.get("score.2.team.1.player.3.name") == "T1P3"

    board.playerNumber.setValue(1)
    autonaApp.settle()

    for team in (1, 2):
        remaining = autonaApp.get(f"score.2.team.{team}.player") or {}
        assert sorted(remaining) == ["1"], (
            f"team {team} kept {sorted(remaining)} after shrinking to one "
            "player; the surplus stays on the overlay")


def test_a_roster_that_grows_with_a_loaded_losers_flag_is_flagged(autonaApp):
    """The gap the player instalment closed, closed for good by S2b.

    A set that arrives with the flag set *and* more entrants than the board
    currently shows used to leave the new cards at losers=False -- they were
    connected to the LOSERS checkbox only from the moment they were created
    -- so their mergedName lost the " [L]" the thumbnail generator renders.
    The player instalment had the new cards read the flag as they were built
    (_AdoptTeamLosers); since ADR-005 S2b there is no per-card flag at all:
    a card publishes its name with the flag it finds in the state, which
    SetLoadService.ApplyLosers has written before the entrants are poured in.
    """
    board = autonaApp.board(2)
    autonaApp.settle()
    assert len(board.team1playerWidgets) == 1

    board.ChangeSetData({
        "entrants": [[{"gamerTag": "A1"}, {"gamerTag": "A2"}],
                     [{"gamerTag": "B1"}, {"gamerTag": "B2"}]],
        "team1losers": True, "team2losers": False, "overwrite": True,
    })
    autonaApp.settle()

    assert len(board.team1playerWidgets) == 2
    assert [autonaApp.get(f"score.2.team.1.player.{m}.mergedName")
            for m in (1, 2)] == ["A1 [L]", "A2 [L]"]
    # ...and the team that did not come out of losers stays unflagged.
    assert [autonaApp.get(f"score.2.team.2.player.{m}.mergedName")
            for m in (1, 2)] == ["B1", "B2"]


def test_the_details_dialog_apply_button_commits_the_board(autonaApp):
    """反映 from inside a player card is the board-level publish.

    The button used to call StateManager.CommitHeld with a prefix it sliced
    out of self.path; it resolves the board through PlayerService and calls
    the same operation the board's own button does.
    """
    from src.core.StateManager import StateManager

    board = autonaApp.board(1)
    autonaApp.settle()
    player = board.team1playerWidgets[0]

    _setPlayerName(board, 1, "PENDING")
    autonaApp.settle()
    assert StateManager.HasPendingChanges("score.1") is True

    player.btApplyDetails.click()
    autonaApp.settle()

    assert StateManager.HasPendingChanges("score.1") is False
