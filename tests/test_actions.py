"""Contract tests for src/core/Actions.py (headless core Step 3).

These run with **no QApplication and no widget**, which is the whole point of
the layer: the operations a caster performs -- adjust a score, reset it, set a
team colour, toggle a field, press 反映 -- used to exist only inside a
QWidget, so they could not be tested at all without building the desktop app.
What is asserted here is therefore not just "the key changed" but the two
properties that used to be implicit in the widget:

- **which save cycle a write lands in.** Boards 1/2 are under the 反映 hold
  (R-Sync-11): a score edit must reach the live state, stay out of the delta
  stream, and leave `revision` alone until it is committed. A display option
  is *not* held and must publish at once. Getting this wrong is invisible on
  the desktop and shows up as an overlay that updates too early or never.
- **exactly one cycle per operation.** A reset writes two keys; if it does so
  in two cycles, OBS renders an intermediate 3-0 for one frame. The
  notification counts below are what pins that.

Isolation follows tests/test_state_schema.py: StateManager writes ./out
relative to the CWD, so everything runs inside a temp directory, and the
shared singletons (state, revision, settings) are handed back untouched --
this module runs in the same process, against the same StateManager, as the
widget harness in later modules.
"""
import os
import tempfile
import threading
from pathlib import Path

import pytest

from src.core import StateSchema as schema
from src.core.Actions import AutonaActions
from src.core.SettingsManager import SettingsManager
from src.Helpers.AutonaDictHelper import deep_clone
from src.StateManager import StateManager as SM

_WORK_DIR = tempfile.mkdtemp(prefix="autona_actions_test_")
_ORIGINAL_CWD = os.getcwd()

# StateManager.Initialize() is deliberately NOT called here, for the same
# reason tests/test_state_schema.py does not: it is a once-per-process no-op
# and tests/test_state_manager_held.py already spends it (at collection time,
# which happens before any test in this module runs). Only the directories the
# export thread writes into have to exist here.
os.makedirs(Path(_WORK_DIR) / "out", exist_ok=True)
os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)

BOARD = 1
PREFIX = f"score.{BOARD}"


class _Spy:
    """Counts what the state layer published, without Qt.

    Subscribes to the core channels directly -- allowed here (and only here,
    plus StateManagerBridge) because this is not a widget: nothing outlives
    the unsubscribe in the fixture.
    """

    def __init__(self):
        self.deltas = []
        self.big_changes = 0
        self.held_updates = []
        self.dirty_events = []
        self._subs = (
            (SM.observers.state_updated, self._onDelta),
            (SM.observers.state_big_change, self._onBigChange),
            (SM.observers.held_state_updated, self._onHeldUpdate),
            (SM.observers.held_dirty_changed, self._onDirty),
        )
        for channel, callback in self._subs:
            channel.subscribe(callback)

    def _onDelta(self, payload):
        self.deltas.append(payload)

    def _onBigChange(self):
        self.big_changes += 1

    def _onHeldUpdate(self, prefix):
        self.held_updates.append(prefix)

    def _onDirty(self, prefix, dirty):
        self.dirty_events.append((prefix, dirty))

    def close(self):
        for channel, callback in self._subs:
            channel.unsubscribe(callback)

    def held_updates_for(self, prefix):
        return [p for p in self.held_updates if p == prefix]


@pytest.fixture
def actions():
    return AutonaActions.instance or AutonaActions()


@pytest.fixture(autouse=True)
def _isolated_state():
    """Temp CWD + a clean, restored StateManager / SettingsManager.

    Both directions matter, because this module shares one process (and one
    StateManager) with the widget harness and with
    tests/test_state_manager_held.py:

    - *clean going in*: another module may have left `score.*` behind and
      `score.1` registered as held **and already dirty**. held_dirty_changed
      only fires on the transition into dirty, so inheriting that state would
      silently swallow the notification these tests assert on -- a failure
      that depends on which modules ran first.
    - *restored coming out*: the snapshots below are put back untouched.
    """
    os.chdir(_WORK_DIR)
    with SM.lock:
        stateSnapshot = deep_clone(SM.state)
        savedSnapshot = deep_clone(SM.lastSavedState)
        revision = SM.revision
        heldPrefixes = set(SM.heldPrefixes)
        heldExempt = dict(SM.heldExempt)
        heldKeys = {k: list(v) for k, v in SM.heldKeys.items()}
        changedKeys = list(SM.changedKeys)
        SM.state.pop("score", None)
        SM.lastSavedState.pop("score", None)
        SM.heldPrefixes.discard(PREFIX)
        SM.heldExempt.pop(PREFIX, None)
        SM.heldKeys.pop(PREFIX, None)
        SM._commitPrefixes.discard(PREFIX)
        SM._heldTouched.discard(PREFIX)
        SM.changedKeys = []
    settingsSnapshot = SettingsManager.settings
    SettingsManager.settings = {}
    try:
        yield
    finally:
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state = stateSnapshot
            SM.lastSavedState = savedSnapshot
            SM.revision = revision
            SM.heldPrefixes = heldPrefixes
            SM.heldExempt = heldExempt
            SM.heldKeys = heldKeys
            SM.changedKeys = changedKeys
            SM._commitPrefixes.clear()
            SM._heldTouched.clear()
        SettingsManager.settings = settingsSnapshot
        os.chdir(_ORIGINAL_CWD)


@pytest.fixture
def heldBoard():
    """A board 1 registered as held, exactly as its widget registers it.

    The published baseline is forced to match the live state afterwards.
    SaveState only advances `lastSavedState` when its (path-filtered) diff
    found something, so a synthetic setup like this one can leave the two
    apart -- and the next SaveState whose changed-key list happens to be
    empty diffs the *whole* state and publishes the leftovers, which would
    read here as "the held write was broadcast".
    """
    SM.Set(PREFIX, {})
    SM.Set(f"{PREFIX}.team.1.score", 0)
    SM.Set(f"{PREFIX}.team.2.score", 0)
    SM.HoldSubtree(PREFIX, exempt=schema.STATS_KEYS)
    if SM._export_thread is not None:
        SM._export_thread.join()
    with SM.lock:
        SM.lastSavedState = deep_clone(SM.state)
        SM.changedKeys = []
    return PREFIX


@pytest.fixture
def spy():
    watcher = _Spy()
    try:
        yield watcher
    finally:
        watcher.close()


# ── which board an operation acts on ─────────────────────────────────────

def test_active_board_follows_the_saved_battle_format(actions):
    assert actions.ActiveBoard() == 1              # nothing stored yet
    for fmt, expected in (("singles", 1), ("team", 2), ("crew", 3)):
        SettingsManager.settings["scoreboard"] = {"battle_format": fmt}
        assert actions.ActiveBoard() == expected
    # An unknown format must not take the hotkeys down with it.
    SettingsManager.settings["scoreboard"] = {"battle_format": "nonsense"}
    assert actions.ActiveBoard() == 1


def test_active_board_honours_the_legacy_team_mode_flag(actions):
    """`battle_format` postdates `team_mode`, and both are in the wild.

    An operator who has run in team mode since before `battle_format` was
    introduced still has only the bool until the next time the format is set.
    The window has always read it; the hotkeys did not, so Ctrl+Shift+1 aimed
    at board 1 while the window showed board 2.
    """
    SettingsManager.settings["scoreboard"] = {"team_mode": True}
    assert actions.ResolveBattleFormat() == "team"
    assert actions.ActiveBoard() == 2

    SettingsManager.settings["scoreboard"] = {"team_mode": False}
    assert actions.ResolveBattleFormat() == "singles"
    assert actions.ActiveBoard() == 1

    # The new key wins wherever both are present -- SetBattleFormat writes
    # team_mode purely so an older build can still be rolled back to.
    SettingsManager.settings["scoreboard"] = {
        "battle_format": "crew", "team_mode": True}
    assert actions.ResolveBattleFormat() == "crew"
    assert actions.ActiveBoard() == 3


def test_the_format_board_table_cannot_be_edited_through_an_alias(actions):
    # AutonaScoreboardManager.FORMAT_BOARDS is this same object, so a plain
    # dict here would be a shared mutable with two owners.
    with pytest.raises(TypeError):
        actions.FORMAT_BOARDS["singles"] = 99


# ── score ────────────────────────────────────────────────────────────────

def test_adjust_score_writes_the_board_key(actions, heldBoard):
    assert actions.AdjustScore(BOARD, 1, 1) == 1
    assert actions.AdjustScore(BOARD, 1, 1) == 2
    assert actions.AdjustScore(BOARD, 2, 3) == 3

    assert SM.Get(f"{PREFIX}.team.1.score") == 2
    assert SM.Get(f"{PREFIX}.team.2.score") == 3


def test_adjust_score_clamps_to_the_spinbox_range(actions, heldBoard):
    # The QSpinBox range used to be the only guard; the operation carries it
    # now, and AutonaScoreboardWidget applies the same bounds to the spinbox.
    assert actions.AdjustScore(BOARD, 1, -1) == 0
    assert SM.Get(f"{PREFIX}.team.1.score") == 0

    assert actions.AdjustScore(BOARD, 1, 500) == actions.SCORE_MAX
    assert actions.AdjustScore(BOARD, 1, 1) == actions.SCORE_MAX


def test_adjust_score_ignores_an_unknown_team(actions, heldBoard):
    assert actions.AdjustScore(BOARD, 5, 1) is None
    assert SM.Get(f"{PREFIX}.team.5") is None


def test_adjust_score_is_a_no_op_on_the_crew_board(actions):
    # Board 3's team score is the sum of its members' remaining stocks, so a
    # team-level nudge has no target -- the same no-op the crew widget did.
    assert actions.AdjustScore(3, 1, 1) is None
    assert SM.Get("score.3") is None


def test_a_score_edit_stays_behind_the_apply_button(actions, heldBoard, spy):
    revision = SM.revision

    actions.AdjustScore(BOARD, 1, 1)

    # Live state has it, the broadcast does not, and the published revision
    # has not moved (R-Sync-11).
    assert SM.Get(f"{PREFIX}.team.1.score") == 1
    assert spy.deltas == []
    assert spy.big_changes == 0
    assert SM.revision == revision
    assert SM.HasPendingChanges(PREFIX)
    # One save cycle, so exactly one "live value changed" notification --
    # this is the echo-loop check: a second one would mean the write came
    # back round and was applied again.
    assert spy.held_updates_for(PREFIX) == [PREFIX]
    assert (PREFIX, True) in spy.dirty_events


def test_reset_score_zeroes_both_teams_in_one_cycle(actions, heldBoard, spy):
    actions.AdjustScore(BOARD, 1, 3)
    actions.AdjustScore(BOARD, 2, 2)
    spy.held_updates.clear()
    revision = SM.revision

    assert actions.ResetScore(BOARD) is True

    assert SM.Get(f"{PREFIX}.team.1.score") == 0
    assert SM.Get(f"{PREFIX}.team.2.score") == 0
    # Two keys, one save cycle: OBS must never see a half-reset board.
    assert spy.held_updates_for(PREFIX) == [PREFIX]
    assert SM.revision == revision


def test_reset_score_declines_the_crew_board(actions):
    # Crew "reset" means restoring member stocks, which Step 3 left with the
    # widget; the False return is what routes the caller there.
    assert actions.ResetScore(3) is False
    assert SM.Get("score.3") is None


# ── team colour ──────────────────────────────────────────────────────────

def test_set_team_color_writes_the_hex_string(actions, heldBoard):
    assert actions.SetTeamColor(BOARD, 2, "#123456") is True
    assert SM.Get(f"{PREFIX}.team.2.color") == "#123456"


def test_set_team_color_ignores_an_empty_color(actions, heldBoard):
    actions.SetTeamColor(BOARD, 1, "#abcdef")
    for empty in (None, "", 0):
        assert actions.SetTeamColor(BOARD, 1, empty) is False
    # Clearing the colour would leave overlays with a transparent team strip.
    assert SM.Get(f"{PREFIX}.team.1.color") == "#abcdef"


def test_set_team_color_ignores_an_unknown_team(actions, heldBoard):
    assert actions.SetTeamColor(BOARD, 0, "#123456") is False


def test_team_color_is_held_like_the_rest_of_the_board(actions, heldBoard, spy):
    revision = SM.revision
    actions.SetTeamColor(BOARD, 1, "#123456")
    assert spy.deltas == []
    assert SM.revision == revision


# ── display options ──────────────────────────────────────────────────────

def test_set_display_option_writes_both_halves_of_the_mirror(actions):
    assert actions.SetDisplayOption("show_real_name", True) is True

    # SettingsManager is the persisted local default a board re-reads when it
    # is built; StateManager is what the Web UI and the overlays render from.
    assert SettingsManager.Get("display_options.show_real_name") is True
    assert SM.Get("display_options.show_real_name") is True


def test_set_display_option_rejects_an_unknown_key(actions):
    assert actions.SetDisplayOption("show_nothing", True) is False
    assert SM.Get("display_options.show_nothing") is None


def test_set_display_option_publishes_immediately_and_once(actions, spy):
    revision = SM.revision

    actions.SetDisplayOption("show_sponsor", False)

    # Not a held subtree: the toggle is not an operator edit waiting on 反映,
    # so it reaches the overlays straight away -- in exactly one delta.
    assert len(spy.deltas) == 1
    assert SM.revision == revision + 1
    assert "show_sponsor" in str(spy.deltas[0])


def test_every_schema_display_option_is_accepted(actions):
    for key in schema.DISPLAY_OPTION_KEYS:
        assert actions.SetDisplayOption(key, True) is True
        assert SM.Get(f"display_options.{key}") is True


# ── 反映 ──────────────────────────────────────────────────────────────────

def test_apply_changes_publishes_the_pending_edits_in_one_delta(
        actions, heldBoard, spy):
    actions.AdjustScore(BOARD, 1, 2)
    actions.SetTeamColor(BOARD, 1, "#123456")
    revision = SM.revision

    actions.ApplyChanges(BOARD)

    assert not SM.HasPendingChanges(PREFIX)
    assert len(spy.deltas) == 1
    assert SM.revision == revision + 1
    assert (PREFIX, False) in spy.dirty_events


def test_apply_changes_on_a_board_that_was_never_built_is_harmless(actions):
    # Board 2's subtree is not held (no widget registered it), and board 3 is
    # never held at all. Committing either must not raise.
    actions.ApplyChanges(2)
    actions.ApplyChanges(3)


# ── threading ────────────────────────────────────────────────────────────

def test_concurrent_adjustments_do_not_lose_an_increment(actions, heldBoard):
    """Score adjustments arrive off the GUI thread now.

    They used to be funnelled through a queued Qt signal, i.e. serialised on
    the GUI thread by construction. Two Web UI clients pressing +1 at the
    same moment now both run AdjustScore concurrently, so the
    read-modify-write has to be taken under StateManager's lock -- without
    it, one of the two reads the same value and one increment disappears.
    """
    threadCount, perThread = 4, 10
    errors = []

    def worker():
        try:
            for _ in range(perThread):
                actions.AdjustScore(BOARD, 1, 1)
        except Exception as error:      # pragma: no cover - failure detail
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(threadCount)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert SM.Get(f"{PREFIX}.team.1.score") == threadCount * perThread


def test_the_other_operations_are_callable_off_the_main_thread(
        actions, heldBoard):
    # Smoke: nothing here may touch a Qt object, so none of it may care which
    # thread it is on. (An assert_gui_thread anywhere below would raise.)
    errors = []

    def worker():
        try:
            actions.ResetScore(BOARD)
            actions.SetTeamColor(BOARD, 1, "#00ff00")
            actions.SetDisplayOption("show_seed", False)
            actions.ApplyChanges(BOARD)
        except Exception as error:      # pragma: no cover - failure detail
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert errors == []
    assert SM.Get(f"{PREFIX}.team.1.color") == "#00ff00"
    assert SM.Get("display_options.show_seed") is False
