"""The stage-tracking rules, with no QApplication and no buttons.

Every assertion here used to require the desktop app: which game a +1
awards, what a swap does to a tie, why the score the rows add up to must
not be read back as an operator action. The logic lived on a QWidget and
read its own QPushButtons, so "one click on game 1 also marked game 2" --
the bug the tracker-on tests in tests/test_scoreboard_operations.py were
written for -- could only ever be caught through a real board.

Since headless core Step 4a the record is `score.{n}.stages.*` and the
buttons render it, so the rules are testable here and the widget tests are
free to be about the widget.

Isolation follows tests/test_actions.py: StateManager writes ./out relative
to the CWD, so this runs in a temp directory, and the shared singletons are
handed back untouched -- this module runs in the same process, against the
same StateManager, as the widget harness in later modules.
"""
import os
import tempfile
from pathlib import Path

import pytest

from src.core.GameTrackerService import (
    GameTrackerService, TEAM_1, TEAM_2, TIE, UNREPORTED)
from src.core.SettingsManager import SettingsManager
from src.core.DictHelper import deep_clone
from src.core.StateManager import StateManager as SM

_WORK_DIR = tempfile.mkdtemp(prefix="autona_tracker_test_")
_ORIGINAL_CWD = os.getcwd()

# StateManager.Initialize() is deliberately not called (see test_actions.py):
# it is a once-per-process no-op that tests/test_state_manager_held.py
# already spends at collection time. Only the export thread's directories
# have to exist.
os.makedirs(Path(_WORK_DIR) / "out", exist_ok=True)
os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)

BOARD = 1
PREFIX = f"score.{BOARD}"


@pytest.fixture(autouse=True)
def _isolated_state():
    os.chdir(_WORK_DIR)
    with SM.lock:
        stateSnapshot = deep_clone(SM.state)
        savedSnapshot = deep_clone(SM.lastSavedState)
        revision = SM.revision
        changedKeys = list(SM.changedKeys)
        # The hold registry too. Every board the widget harness builds calls
        # HoldSubtree("score.1"), which is the prefix this module writes
        # directly: while it is registered, the writes below land in heldKeys
        # instead of publishing and the save-cycle assertions would count
        # zero. The harness does clear the registration when it drops a board
        # (see conftest), so today this module never actually inherits one --
        # which is exactly why it is worth pinning here rather than depending
        # on another module's teardown and on collection order.
        # Restored on the way out for the same reason in reverse: a prefix
        # this module invented must not be handed to whatever runs next.
        heldPrefixes = set(SM.heldPrefixes)
        heldKeys = {prefix: list(keys) for prefix, keys in SM.heldKeys.items()}
        SM.heldPrefixes = set()
        SM.heldKeys = {}
        SM.state.pop("score", None)
        SM.lastSavedState.pop("score", None)
        SM.changedKeys = []
    settingsSnapshot = SettingsManager.settings
    # The tracker ships disabled; every test here is about what it does when
    # the operator has switched it on.
    SettingsManager.settings = {
        "general": {"disable_individual_game_tracker": False}}
    try:
        yield
    finally:
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state = stateSnapshot
            SM.lastSavedState = savedSnapshot
            SM.revision = revision
            SM.changedKeys = changedKeys
            SM.heldPrefixes = heldPrefixes
            SM.heldKeys = heldKeys
        SettingsManager.settings = settingsSnapshot
        os.chdir(_ORIGINAL_CWD)


@pytest.fixture
def tracker():
    """A tracker with five empty games, as a BO5 builds."""
    service = GameTrackerService(BOARD)
    service.SetStageCount(5)
    return service


def _winners(service):
    return [service.GameWinner(i) for i in range(service.StageCount())]


# ── the row list ─────────────────────────────────────────────────────────

def test_setting_the_stage_count_seeds_unreported_rows(tracker):
    assert tracker.StageCount() == 5
    assert _winners(tracker) == [UNREPORTED] * 5
    # Keys are 1-based strings in the state, 0-based indices in the API.
    assert set(SM.Get(f"{PREFIX}.stages").keys()) == {"1", "2", "3", "4", "5"}


def test_setting_the_stage_count_again_clears_the_previous_set(tracker):
    tracker.SetStageResult(0, TEAM_1)
    tracker.SetStageCount(3)

    assert tracker.StageCount() == 3
    assert _winners(tracker) == [UNREPORTED] * 3


def test_rebuilding_the_rows_is_one_save_cycle():
    """Five rows are 16 keys; OBS must not see them arrive one at a time."""
    service = GameTrackerService(BOARD)
    seen = []

    def record(payload):
        seen.append(payload)

    SM.observers.state_updated.subscribe(record)
    try:
        service.SetStageCount(5)
    finally:
        SM.observers.state_updated.unsubscribe(record)

    assert len(seen) == 1


@pytest.mark.parametrize("requested, disabled, expected", [
    (5, False, 5),
    (0, False, 0),      # best-of not set
    (5, True, 0),       # feature switched off: no rows, widget hidden
])
def test_resolve_stage_count(requested, disabled, expected):
    SettingsManager.Set("general.disable_individual_game_tracker", disabled)
    assert GameTrackerService(BOARD).ResolveStageCount(requested) == expected


# ── one row's result ─────────────────────────────────────────────────────

@pytest.mark.parametrize("winner, flags", [
    (TEAM_1, {"t1_win": True, "t2_win": False, "tie": False}),
    (TEAM_2, {"t1_win": False, "t2_win": True, "tie": False}),
    (TIE, {"t1_win": False, "t2_win": False, "tie": True}),
    (UNREPORTED, {"t1_win": False, "t2_win": False, "tie": False}),
])
def test_a_result_is_written_exclusively(tracker, winner, flags):
    """All three flags are always written, so no row can carry two."""
    tracker.SetStageResult(0, TEAM_2)      # something to overwrite
    tracker.SetStageResult(0, winner)

    row = SM.Get(f"{PREFIX}.stages.1")
    assert {key: row[key] for key in flags} == flags
    assert tracker.GameWinner(0) == winner


def test_a_row_that_is_not_a_dict_reads_as_unreported(tracker):
    """The stage combo can leave one behind (see SetStageAsset)."""
    SM.Set(f"{PREFIX}.stages.2", None)
    assert tracker.GameWinner(1) == UNREPORTED


def test_selecting_a_stage_keeps_the_recorded_result(tracker):
    """The combo overwrites the whole row with the stage's asset payload."""
    tracker.SetStageResult(1, TEAM_2)

    tracker.SetStageAsset(1, {"name": "Final Destination", "codename": "fd"})

    assert SM.Get(f"{PREFIX}.stages.2.name") == "Final Destination"
    assert tracker.GameWinner(1) == TEAM_2


# ── finding rows ─────────────────────────────────────────────────────────

def test_the_first_empty_stage_skips_reported_games(tracker):
    tracker.SetStageResult(0, TEAM_1)
    tracker.SetStageResult(1, TIE)

    assert tracker.FirstEmptyStage() == 2
    assert tracker.FirstEmptyStage(3) == 3


def test_a_completed_set_has_no_empty_stage():
    service = GameTrackerService(BOARD)
    service.SetStageCount(1)
    service.SetStageResult(0, TEAM_1)

    assert service.FirstEmptyStage() == -1


def test_won_stages_are_listed_per_team(tracker):
    tracker.SetStageResult(0, TEAM_1)
    tracker.SetStageResult(1, TEAM_2)
    tracker.SetStageResult(2, TEAM_1)
    tracker.SetStageResult(3, TIE)

    assert tracker.WonStages(0) == [0, 2]     # team is 0-based here
    assert tracker.WonStages(1) == [1]


# ── score <-> stages ─────────────────────────────────────────────────────

def test_a_score_increase_awards_the_next_empty_game(tracker):
    tracker.SyncStagesToScore(0, 0, 2)

    assert _winners(tracker)[:2] == [TEAM_1, TEAM_1]
    assert _winners(tracker)[2:] == [UNREPORTED] * 3


def test_a_score_decrease_clears_the_last_game_that_team_won(tracker):
    tracker.SyncStagesToScore(0, 0, 1)
    tracker.SyncStagesToScore(1, 0, 1)
    assert _winners(tracker)[:2] == [TEAM_1, TEAM_2]

    tracker.SyncStagesToScore(0, 1, 0)

    assert _winners(tracker)[:2] == [UNREPORTED, TEAM_2], (
        "clearing team 1's game must not touch the game team 2 won")


def test_a_reported_tie_disables_the_tracking(tracker):
    """A tie breaks the "one game = one point" assumption entirely."""
    tracker.SetStageResult(2, TIE)

    tracker.SyncStagesToScore(0, 0, 1)

    assert tracker.WonStages(0) == [], (
        "the score moved but no game may be awarded once a tie is reported")


def test_the_score_the_rows_add_up_to_is_published(tracker):
    tracker.SetStageResult(0, TEAM_1)
    tracker.SetStageResult(1, TEAM_2)
    tracker.SetStageResult(2, TEAM_1)
    tracker.SetStageResult(3, TIE)      # counts for neither side

    assert tracker.PublishStageResults() == (2, 1)
    assert SM.Get(f"{PREFIX}.team.1.score") == 2
    assert SM.Get(f"{PREFIX}.team.2.score") == 1


def test_publishing_the_stage_score_is_one_cycle_and_flags_itself(tracker):
    """Both properties the board depends on, in one place.

    The board reconciles its spinboxes from the same notification this
    write produces, and would read it as an operator +1 -- awarding another
    stage for it -- unless `writingScore` says whose write it is.
    """
    tracker.SetStageResult(0, TEAM_1)
    seen = []

    def record(payload):
        seen.append(tracker.writingScore)

    SM.observers.state_updated.subscribe(record)
    try:
        tracker.PublishStageResults()
    finally:
        SM.observers.state_updated.unsubscribe(record)

    assert seen == [True], (
        "the two team scores must publish in one cycle, with the tracker "
        "identifying itself as the author for the whole of it")
    assert tracker.writingScore is False, "the flag was left set"


def test_update_score_publishes_and_then_moves_the_rows(tracker):
    """The spinbox-driven path: set loading, ClearScore, SwapTeams."""
    tracker.UpdateScore(0, 2)

    assert SM.Get(f"{PREFIX}.team.1.score") == 2
    assert tracker.WonStages(0) == [0, 1]

    tracker.UpdateScore(0, 1)

    assert SM.Get(f"{PREFIX}.team.1.score") == 1
    assert tracker.WonStages(0) == [0]


# ── swap ─────────────────────────────────────────────────────────────────

def test_swapping_mirrors_the_winners_and_leaves_ties_alone(tracker):
    tracker.SetStageResult(0, TEAM_1)
    tracker.SetStageResult(1, TEAM_2)
    tracker.SetStageResult(2, TIE)

    tracker.SwapStageResults()

    assert _winners(tracker) == [
        TEAM_2, TEAM_1, TIE, UNREPORTED, UNREPORTED]


def test_swapping_twice_is_the_identity(tracker):
    tracker.SetStageResult(0, TEAM_1)
    tracker.SetStageResult(2, TEAM_2)
    before = _winners(tracker)

    tracker.SwapStageResults()
    tracker.SwapStageResults()

    assert _winners(tracker) == before


def test_a_swap_is_one_save_cycle(tracker):
    tracker.SetStageResult(0, TEAM_1)
    seen = []

    def record(payload):
        seen.append(payload)

    SM.observers.state_updated.subscribe(record)
    try:
        tracker.SwapStageResults()
    finally:
        SM.observers.state_updated.unsubscribe(record)

    assert len(seen) == 1, (
        "a half-applied swap must never be observable by an overlay")
