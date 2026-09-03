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

_EXTRA_ROOTS = (
    schema.STREAM_ROOT,
    schema.LAYOUT_OPTIONS_ROOT,
    schema.COMMENTARY_ROOT,
    schema.RESULT_PLAYERS_ROOT,
)


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
        # The roots the Step 5-5 operations below write. Popped for the same
        # reason `score` is: this module shares one StateManager with every
        # other, and a revision count is only meaningful from a known start.
        for root in _EXTRA_ROOTS:
            SM.state.pop(root, None)
            SM.lastSavedState.pop(root, None)
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


# ── the match bar (headless core Step 5) ─────────────────────────────────
#
# `phase` / `pool` / `match` were written by a lambda that used the combo's
# own objectName as the key segment, so "which fields exist" was a property of
# a .ui file and "an unknown field" was not a case that could occur. Both are
# decidable here now.

def test_set_match_field_writes_each_of_the_three_keys(actions, heldBoard):
    for field, value in (("phase", "Bracket"),
                         ("pool", "Pool A"),
                         ("match", "Winners Final")):
        assert actions.SetMatchField(BOARD, field, value) is True
        assert SM.Get(f"{PREFIX}.{field}") == value


def test_set_match_field_accepts_exactly_the_schema_field_list(actions,
                                                               heldBoard):
    for field in schema.SCORE_MATCH_FIELDS:
        assert actions.SetMatchField(BOARD, field, "x") is True
    # Real board keys, but not fields of the match bar: writing them from
    # here would bypass the operations that own them.
    for field in ("best_of", "teamsSwapped", "score", "", None):
        assert actions.SetMatchField(BOARD, field, "x") is False
    assert SM.Get(f"{PREFIX}.best_of") is None


def test_set_match_field_writes_a_cleared_field_as_an_empty_string(
        actions, heldBoard):
    # The overlays render the key; unsetting it would leave them showing the
    # previous round for a set that has none.
    actions.SetMatchField(BOARD, "match", "Winners Final")
    assert actions.SetMatchField(BOARD, "match", None) is True
    assert SM.Get(f"{PREFIX}.match") == ""


def test_rewriting_an_unchanged_field_does_not_light_the_apply_button(
        actions, heldBoard, spy):
    """The behaviour the Qt widgets used to provide for free.

    setCurrentText() / setChecked() emit nothing when the value has not
    moved, so re-asserting a field (ClearScore on an already-clear board, a
    re-loaded set, a focus-out with nothing typed) never marked the board
    dirty. The write does not go through the widget any more, so the guard
    has to be in the operation -- same rule as SetLoadService._SetIfChanged.
    """
    actions.SetMatchField(BOARD, "match", "Winners Final")
    actions.ApplyChanges(BOARD)
    spy.held_updates.clear()
    assert SM.HasPendingChanges(PREFIX) is False

    assert actions.SetMatchField(BOARD, "match", "Winners Final") is False

    assert SM.HasPendingChanges(PREFIX) is False
    assert spy.held_updates_for(PREFIX) == []


# ── team card fields ─────────────────────────────────────────────────────

def test_set_team_name_writes_the_display_team(actions, heldBoard):
    assert actions.SetTeamName(BOARD, 2, "Alpha") is True
    assert SM.Get(f"{PREFIX}.team.2.teamName") == "Alpha"


def test_clearing_a_team_name_writes_an_empty_string(actions, heldBoard):
    actions.SetTeamName(BOARD, 1, "Alpha")
    assert actions.SetTeamName(BOARD, 1, None) is True
    assert SM.Get(f"{PREFIX}.team.1.teamName") == ""


def test_set_team_name_ignores_an_unknown_team(actions, heldBoard):
    assert actions.SetTeamName(BOARD, 3, "Alpha") is False
    assert SM.Get(f"{PREFIX}.team.3") is None


def test_set_team_losers_writes_a_bool(actions, heldBoard):
    # The Qt caller hands over a checkbox state; everything else can hand
    # over whatever a query string parsed to.
    assert actions.SetTeamLosers(BOARD, 1, "yes") is True
    assert SM.Get(f"{PREFIX}.team.1.losers") is True
    assert actions.SetTeamLosers(BOARD, 1, 0) is True
    assert SM.Get(f"{PREFIX}.team.1.losers") is False


def test_set_team_losers_ignores_an_unknown_team(actions, heldBoard):
    assert actions.SetTeamLosers(BOARD, 0, True) is False
    assert SM.Get(f"{PREFIX}.team.0") is None


def test_set_teams_swapped_publishes_the_mirror(actions, heldBoard):
    actions.SetTeamsSwapped(BOARD, True)
    assert SM.Get(f"{PREFIX}.teamsSwapped") is True
    actions.SetTeamsSwapped(BOARD, False)
    assert SM.Get(f"{PREFIX}.teamsSwapped") is False


# ── best of ──────────────────────────────────────────────────────────────

def test_set_best_of_publishes_every_derived_key_in_one_cycle(
        actions, heldBoard, spy):
    actions.SetBestOf(BOARD, 5)

    assert SM.Get(f"{PREFIX}.best_of") == 5
    assert SM.Get(f"{PREFIX}.best_of_short_text") == "BO5"
    assert SM.Get(f"{PREFIX}.first_to") == 3
    assert SM.Get(f"{PREFIX}.first_to_short_text") == "FT3"
    # The long forms are localized, so the wording is not a contract -- the
    # number is. A truthy check would pass on an unformatted template.
    assert "5" in SM.Get(f"{PREFIX}.best_of_text")
    assert "3" in SM.Get(f"{PREFIX}.first_to_text")
    # Six keys, one fact, one save cycle: an overlay must never render BO5
    # beside FT2.
    assert spy.held_updates_for(PREFIX) == [PREFIX]


def test_best_of_zero_blanks_the_long_texts_only(actions, heldBoard):
    actions.SetBestOf(BOARD, 3)
    actions.SetBestOf(BOARD, 0)

    # 0 means "not set" -- the overlay shows nothing rather than "Best of 0".
    assert SM.Get(f"{PREFIX}.best_of_text") == ""
    assert SM.Get(f"{PREFIX}.first_to_text") == ""
    # The short forms have always been unconditional.
    assert SM.Get(f"{PREFIX}.best_of_short_text") == "BO0"
    assert SM.Get(f"{PREFIX}.first_to_short_text") == "FT0"


def test_best_of_rounds_the_first_to_up(actions, heldBoard):
    for bestOf, firstTo in ((1, 1), (3, 2), (5, 3), (7, 4)):
        actions.SetBestOf(BOARD, bestOf)
        assert SM.Get(f"{PREFIX}.first_to") == firstTo


def test_best_of_uses_the_injected_text_formatter(heldBoard):
    """The one Qt-shaped part of a set length, handed in from outside.

    The localized "Best of 5" comes from AutonaLocaleHelper.matchNames, whose
    module imports Qt, so bootstrap_core() injects the lookup instead
    (AutonaLocaleHelper.FormatMatchText) -- the same arrangement as
    TournamentInfoService's date format.
    """
    calls = []

    def formatter(key, number):
        calls.append((key, number))
        return f"<{key}:{number}>"

    AutonaActions(formatMatchText=formatter).SetBestOf(BOARD, 5)

    assert calls == [("best_of", 5), ("first_to", 3)]
    assert SM.Get(f"{PREFIX}.best_of_text") == "<best_of:5>"
    assert SM.Get(f"{PREFIX}.first_to_text") == "<first_to:3>"


def test_best_of_falls_back_to_the_shipped_english_text(heldBoard):
    # No formatter = a headless run. Publishing the English default beats
    # publishing a blank key, and it is the same string the shipped
    # src/i18n/tournament_term/en.json carries.
    AutonaActions().SetBestOf(BOARD, 5)

    assert SM.Get(f"{PREFIX}.best_of_text") == "Best of 5"
    assert SM.Get(f"{PREFIX}.first_to_text") == "First to 3"


# ── the team roster (headless core Step 5, player instalment) ────────────
#
# The slots themselves belong to PlayerService (one per player widget); the
# *count* is a board operation, and both of these used to be bare
# StateManager.Unset calls inside AutonaScoreboardWidget.

def _seedRoster(team, slots):
    for slot in slots:
        SM.Set(f"{PREFIX}.team.{team}.player.{slot}.name", f"P{slot}")


def test_clearing_a_roster_drops_every_slot(actions):
    _seedRoster(1, ("1", "2", "3"))

    assert actions.ClearTeamRoster(BOARD, 1) is True

    assert SM.Get(f"{PREFIX}.team.1.player") is None


def test_clearing_one_roster_leaves_the_other_alone(actions):
    _seedRoster(1, ("1",))
    _seedRoster(2, ("1",))

    actions.ClearTeamRoster(BOARD, 1)

    assert SM.Get(f"{PREFIX}.team.2.player.1.name") == "P1"


def test_trimming_a_roster_removes_only_the_surplus(actions):
    _seedRoster(1, ("1", "2", "3", "4"))

    assert sorted(actions.TrimTeamRoster(BOARD, 1, 2)) == ["3", "4"]

    assert sorted(SM.Get(f"{PREFIX}.team.1.player")) == ["1", "2"]


def test_trimming_a_roster_that_is_already_short_enough(actions):
    _seedRoster(1, ("1", "2"))

    assert actions.TrimTeamRoster(BOARD, 1, 5) == []
    assert sorted(SM.Get(f"{PREFIX}.team.1.player")) == ["1", "2"]


def test_trimming_a_team_with_no_roster_is_a_no_op(actions):
    """Null-safe where the loop it replaced was not.

    That one read `team.{t}.player` as a dict whenever `team.{t}` was truthy
    at all, so a team carrying only a name would have raised
    AttributeError -- inside the save block that wraps a players-per-team
    change.
    """
    SM.Set(f"{PREFIX}.team.1.teamName", "Alpha")

    assert actions.TrimTeamRoster(BOARD, 1, 1) == []
    assert SM.Get(f"{PREFIX}.team.1.teamName") == "Alpha"


def test_a_non_numeric_roster_slot_is_left_where_it_is(actions):
    """`int(k)` used to raise on it; skipping is the smaller failure."""
    SM.Set(f"{PREFIX}.team.1.player.junk", {"name": "?"})
    _seedRoster(1, ("1", "2"))

    assert actions.TrimTeamRoster(BOARD, 1, 1) == ["2"]
    assert SM.Get(f"{PREFIX}.team.1.player.junk") == {"name": "?"}


@pytest.mark.parametrize("team", [0, 3, None, "x"])
def test_the_roster_operations_ignore_an_impossible_team(actions, team):
    _seedRoster(1, ("1",))

    assert actions.ClearTeamRoster(BOARD, team) is False
    assert actions.TrimTeamRoster(BOARD, team, 0) == []
    assert SM.Get(f"{PREFIX}.team.1.player.1.name") == "P1"


def test_trimming_a_roster_is_one_save_cycle(actions, heldBoard, spy):
    """Every removed slot in one cycle: an overlay must not render a board
    that is losing its players one at a time."""
    _seedRoster(1, ("1", "2", "3"))
    SM.CommitHeld(PREFIX)
    if SM._export_thread is not None:
        SM._export_thread.join()
    spy.held_updates.clear()

    with SM.SaveBlock():
        actions.TrimTeamRoster(BOARD, 1, 1)

    assert spy.held_updates_for(PREFIX) == [PREFIX]


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


# ── layout options / stream / whole-subtree publishes (Step 5-5) ─────────
#
# The four panels these belong to are lazily built and three of them own a
# QWebEngineView, so none of them is in the widget harness. Before this
# instalment their writes therefore had no coverage of any kind; the
# operations are here now, and this is it.

def test_set_layout_option_writes_both_halves_of_the_mirror(actions):
    from src.core.LayoutOptionsManager import LayoutOptionsManager

    stored = LayoutOptionsManager.settings
    LayoutOptionsManager.settings = {}
    try:
        actions.SetLayoutOption("scoreboard", "show_flags", True)

        # LayoutOptionsManager is what the LAYOUT panel reads back when it
        # rebuilds its controls; StateManager is what the overlay renders.
        assert LayoutOptionsManager.Get("scoreboard.show_flags") is True
        assert SM.Get("layout_options.scoreboard.show_flags") is True
    finally:
        LayoutOptionsManager.settings = stored


def test_set_layout_option_is_not_guarded(actions):
    """Rewriting the same value has to reach both sides.

    Building the LAYOUT panel assigns every control its stored value and
    those assignments emit, which is how a layout's options reach the state
    at all on a fresh install. A _SetIfChanged here would leave the settings
    file and the state disagreeing after either one was edited by hand.
    """
    from src.core.LayoutOptionsManager import LayoutOptionsManager

    stored = LayoutOptionsManager.settings
    LayoutOptionsManager.settings = {}
    try:
        actions.SetLayoutOption("scoreboard", "font", "Inter")
        SM.Unset("layout_options.scoreboard.font")
        actions.SetLayoutOption("scoreboard", "font", "Inter")
        assert SM.Get("layout_options.scoreboard.font") == "Inter"
    finally:
        LayoutOptionsManager.settings = stored


def test_the_stream_setters_publish_once_and_skip_a_no_op(actions, spy):
    revision = SM.revision

    assert actions.SetStreamUrl("https://twitch.tv/example") is True
    assert SM.Get("stream.url") == "https://twitch.tv/example"
    assert SM.revision == revision + 1

    # The auto-refresh recomputes the same title on every tournament /
    # entrant / asset change, and the operator's focus-out fires
    # unconditionally: neither may cost a save cycle.
    assert actions.SetStreamUrl("https://twitch.tv/example") is False
    assert SM.revision == revision + 1

    assert actions.SetStreamTitle("Autona Cup") is True
    assert actions.SetStreamTitle("Autona Cup") is False
    assert SM.Get("stream.title") == "Autona Cup"
    assert SM.revision == revision + 2
    # stream.* is not held, so both writes were broadcast, not staged.
    assert spy.held_updates == []
    assert len(spy.deltas) == 2


def test_the_stream_setters_normalise_none_to_an_empty_string(actions):
    """An absent key and an empty one are different to the guard.

    "not set yet" -> "" is a real change and is published once; the second
    call is the no-op. The combo yields "" for its blank row anyway, so this
    only matters to a headless caller.
    """
    assert actions.SetStreamUrl(None) is True
    assert SM.Get("stream.url") == ""
    assert actions.SetStreamUrl(None) is False

    assert actions.SetStreamTitle(None) is True
    assert SM.Get("stream.title") == ""
    assert actions.SetStreamTitle("") is False


def test_publishing_the_commentary_lineup_writes_the_whole_root(actions):
    slots = {"1": {"name": "Ada", "display": 1}, "2": {"name": "", "display": 0}}
    revision = SM.revision

    actions.PublishCommentary(slots)

    assert SM.Get("commentary.1.name") == "Ada"
    assert SM.Get("commentary") == slots
    assert SM.revision == revision + 1


def test_publishing_the_result_placements_writes_the_whole_root(actions):
    players = {"1": {"rank": 1, "name": "Ada", "characters": []}}
    revision = SM.revision

    actions.PublishResultPlayers(players)

    assert SM.Get("result_players") == players
    assert SM.revision == revision + 1
