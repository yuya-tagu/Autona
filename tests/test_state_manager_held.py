"""
Tests for StateManager deferred publication (HoldSubtree / CommitHeld).

Held subtree semantics (the 反映 button feature):
- Writes under a held prefix land in the live state immediately (Get()),
  but are excluded from delta broadcast / GetFullState / file export
  until CommitHeld(prefix) publishes them in one save cycle.

Importing the module is side-effect free, but StateManager.Initialize() does
file I/O relative to the CWD (./out), so it runs once inside a temp working
directory.
"""
import os
import tempfile
from pathlib import Path

import pytest

from src.core.StateManager import StateManager as SM

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

_WORK_DIR = tempfile.mkdtemp(prefix="autona_sm_test_")
_ORIGINAL_CWD = os.getcwd()


def _initialize_state_manager():
    # Initialize() creates ./out; run it under the temp dir, then restore the
    # CWD so later-collected test modules are unaffected. In a full-suite run
    # this module is the first in-process caller (pytest collects by ascending
    # filename), so Initialize() does create the directories here. The
    # makedirs below are forward defence: Initialize() is a no-op after its
    # first call anywhere in the process, so a future module sorting ahead of
    # this one would silently leave the export thread with nowhere to write.
    os.chdir(_WORK_DIR)
    os.makedirs(Path(_WORK_DIR) / "out", exist_ok=True)
    os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)
    try:
        SM.Initialize()
    finally:
        os.chdir(_ORIGINAL_CWD)


_initialize_state_manager()


@pytest.fixture(autouse=True)
def _in_work_dir():
    # SaveState's export thread writes ./out relative to the process CWD, so
    # every test (and any export thread it spawns) must run under the temp
    # dir — never the real project root.
    os.chdir(_WORK_DIR)
    yield
    _join_export()
    os.chdir(_ORIGINAL_CWD)

PREFIX = "score.1"


class _Spy:
    # Subscribes to the core channels directly (headless core Step 1): no
    # Qt involved, which is the point -- the state layer's notifications are
    # observable without a QApplication or the StateManagerBridge adapter.
    def __init__(self):
        self.deltas = []
        self.dirty_events = []
        self.held_updates = []
        SM.observers.state_updated.subscribe(
            lambda d: self.deltas.append(d))
        SM.observers.held_dirty_changed.subscribe(
            lambda p, dirty: self.dirty_events.append((p, dirty)))
        SM.observers.held_state_updated.subscribe(
            lambda p: self.held_updates.append(p))

    def delta_str(self):
        return str(self.deltas)


def _join_export():
    if SM._export_thread is not None:
        SM._export_thread.join()


def test_unheld_write_publishes_immediately():
    spy = _Spy()
    SM.Set("game.name", "TestGame")
    assert SM.Get("game.name") == "TestGame"
    assert "TestGame" in spy.delta_str()


def test_held_write_stays_out_of_broadcast_and_snapshots():
    SM.HoldSubtree(PREFIX)
    spy = _Spy()

    SM.Set(f"{PREFIX}.team.1.player.1.name", "PendingPlayer")

    # Live state sees the value; nothing was broadcast.
    assert SM.Get(f"{PREFIX}.team.1.player.1.name") == "PendingPlayer"
    assert spy.deltas == []
    # Dirty notification fired once; live viewers were notified.
    assert (PREFIX, True) in spy.dirty_events
    assert PREFIX in spy.held_updates
    assert SM.HasPendingChanges(PREFIX)

    # Published views must not contain the pending value.
    committed = SM.GetFullState()["state"]
    assert "PendingPlayer" not in str(committed)
    pending = SM.GetFullState(include_pending=True)["state"]
    assert "PendingPlayer" in str(pending)
    assert SM.GetCommitted(f"{PREFIX}.team.1.player.1.name") is None


def test_unheld_write_while_dirty_does_not_leak_pending():
    spy = _Spy()
    SM.Set("game.name", "OtherGame")
    assert len(spy.deltas) == 1
    assert "OtherGame" in spy.delta_str()
    assert "PendingPlayer" not in spy.delta_str()

    # lastSavedState (the published snapshot) must not have absorbed the
    # pending held value — otherwise the later commit diffs to nothing.
    _join_export()
    assert "PendingPlayer" not in str(SM.lastSavedState)


def test_commit_publishes_pending_in_one_delta():
    spy = _Spy()
    SM.Set(f"{PREFIX}.best_of", 5)

    SM.CommitHeld(PREFIX)

    assert not SM.HasPendingChanges(PREFIX)
    assert (PREFIX, False) in spy.dirty_events
    assert len(spy.deltas) == 1
    assert "PendingPlayer" in spy.delta_str()
    assert "best_of" in spy.delta_str()

    committed = SM.GetFullState()["state"]
    assert "PendingPlayer" in str(committed)
    assert SM.GetCommitted(f"{PREFIX}.best_of") == 5


def test_save_block_coalesces_and_keeps_held_separate():
    spy = _Spy()
    with SM.SaveBlock():
        SM.Set(f"{PREFIX}.team.1.player.1.name", "BlockedPlayer")
        SM.Set("game.name", "BlockGame")
    # One delta for the unheld key only.
    assert len(spy.deltas) == 1
    assert "BlockGame" in spy.delta_str()
    assert "BlockedPlayer" not in spy.delta_str()
    # Held write inside the block was notified (coalesced) on release.
    assert spy.held_updates == [PREFIX]

    spy2 = _Spy()
    SM.CommitHeld(PREFIX)
    assert "BlockedPlayer" in spy2.delta_str()


def test_unset_under_held_prefix_is_deferred():
    spy = _Spy()
    SM.Unset(f"{PREFIX}.team.1.player.1.name")
    assert SM.Get(f"{PREFIX}.team.1.player.1.name") is None
    assert spy.deltas == []
    # Committed view still holds the last published value.
    assert SM.GetCommitted(f"{PREFIX}.team.1.player.1.name") == "BlockedPlayer"

    SM.CommitHeld(PREFIX)
    assert SM.GetCommitted(f"{PREFIX}.team.1.player.1.name") is None


def test_commit_while_save_blocked_flushes_on_release():
    spy = _Spy()
    SM.BlockSaving()
    try:
        SM.Set(f"{PREFIX}.match", "Winners Final")
        SM.CommitHeld(PREFIX)
        assert spy.deltas == []
    finally:
        SM.ReleaseSaving()
    assert "Winners Final" in spy.delta_str()


def test_exported_file_contains_committed_view_only():
    SM.Set(f"{PREFIX}.phase", "PendingPhase")
    SM.Set("game.name", "FileGame")
    _join_export()
    exported = (Path(_WORK_DIR) / "out" / "program_state.json").read_text(
        encoding="utf-8")
    assert "FileGame" in exported
    assert "PendingPhase" not in exported

    SM.CommitHeld(PREFIX)
    _join_export()
    exported = (Path(_WORK_DIR) / "out" / "program_state.json").read_text(
        encoding="utf-8")
    assert "PendingPhase" in exported


# --- Exempt sub-paths -------------------------------------------------
# HoldSubtree(prefix, exempt=[...]) opts fetched derived data (the stats
# keys AutonaStatsUtil writes) out of the hold: those keys publish as soon
# as they are written, even while operator edits under the same prefix are
# still pending. A separate prefix keeps this independent of the tests
# above, which hold "score.1" without exempt paths.
EX_PREFIX = "score.2"
EX_KEYS = ["last_sets", "upset_factor"]


def test_exempt_registration_publishes_baseline():
    SM.Set(f"{EX_PREFIX}.best_of", 3)
    SM.Set(f"{EX_PREFIX}.last_sets.1", {})
    SM.Set(f"{EX_PREFIX}.upset_factor", 0)
    SM.HoldSubtree(EX_PREFIX, exempt=EX_KEYS)
    assert len(SM.heldExempt[EX_PREFIX]) == len(EX_KEYS)


def test_exempt_write_publishes_immediately_when_clean():
    spy = _Spy()
    SM.Set(f"{EX_PREFIX}.upset_factor", 7)
    # Never queued behind the 反映 button.
    assert not SM.HasPendingChanges(EX_PREFIX)
    assert "upset_factor" in spy.delta_str()
    assert SM.GetFullState()["state"]["score"]["2"]["upset_factor"] == 7


def test_exempt_write_publishes_while_held_edit_pending():
    SM.Set(f"{EX_PREFIX}.phase", "PendingExemptPhase")
    assert SM.HasPendingChanges(EX_PREFIX)

    spy = _Spy()
    SM.Set(f"{EX_PREFIX}.last_sets.1", {"round_name": "ExemptRound"})

    # Stats go out now; the pending operator edit stays masked.
    assert "ExemptRound" in spy.delta_str()
    assert "PendingExemptPhase" not in spy.delta_str()

    published = SM.GetFullState()["state"]["score"]["2"]
    assert published["last_sets"]["1"]["round_name"] == "ExemptRound"
    assert "phase" not in published

    _join_export()
    exported = (Path(_WORK_DIR) / "out" / "program_state.json").read_text(
        encoding="utf-8")
    assert "ExemptRound" in exported
    assert "PendingExemptPhase" not in exported


def test_get_committed_serves_live_value_for_exempt_paths():
    assert SM.GetCommitted(f"{EX_PREFIX}.phase") is None
    assert SM.GetCommitted(f"{EX_PREFIX}.last_sets.1") == {
        "round_name": "ExemptRound"}
    # Whole-subtree read (the /get_scoreboard path): committed elsewhere,
    # live for the exempt keys.
    subtree = SM.GetCommitted(EX_PREFIX)
    assert subtree["last_sets"]["1"]["round_name"] == "ExemptRound"
    assert subtree["best_of"] == 3
    assert "phase" not in subtree


def test_get_committed_overlay_does_not_mutate_last_saved_state():
    with SM.SaveBlock():
        SM.Set(f"{EX_PREFIX}.last_sets.1", {"round_name": "BlockedRound"})
        # Not saved yet, but the exempt key is never masked.
        subtree = SM.GetCommitted(EX_PREFIX)
        assert subtree["last_sets"]["1"]["round_name"] == "BlockedRound"
        # The overlay clones; lastSavedState keeps its own storage.
        assert SM.lastSavedState["score"]["2"]["last_sets"]["1"][
            "round_name"] == "ExemptRound"


def test_commit_publishes_held_edit_without_dropping_stats():
    spy = _Spy()
    SM.CommitHeld(EX_PREFIX)
    assert not SM.HasPendingChanges(EX_PREFIX)
    assert "PendingExemptPhase" in spy.delta_str()

    published = SM.GetFullState()["state"]["score"]["2"]
    assert published["phase"] == "PendingExemptPhase"
    assert published["last_sets"]["1"]["round_name"] == "BlockedRound"


def test_prefix_wide_held_write_keeps_live_exempt_values():
    # Clearing the whole subtree is itself a held write; the exempt keys
    # written afterwards must still reach the published view.
    SM.Set(EX_PREFIX, {})
    assert SM.HasPendingChanges(EX_PREFIX)

    SM.Set(f"{EX_PREFIX}.upset_factor", 3)
    published = SM.GetFullState()["state"]["score"]["2"]
    assert published["upset_factor"] == 3
    # The rest of the subtree is still the committed view.
    assert published["phase"] == "PendingExemptPhase"


# --- A save cycle with nothing to publish -----------------------------
# A write under a held prefix is buffered into heldKeys and never reaches
# changedKeys, so a SaveBlock that only wrote held keys releases into
# SaveState with an empty changed-key list. That list is handed to DeepDiff
# as `include_paths`, and DeepDiff reads an empty one as "no filter" -- the
# most expensive diff there is, on every +1 with the game tracker on, every
# SwapTeams and every stage click (headless core Step 4a).
#
# It was not even a *harmless* full diff: lastSavedState is only advanced at
# the paths each cycle diffs, so it does not have to equal the live state
# anywhere else. When this was found, the export thread also stamped
# `timestamp` and `revision` into lastSavedState (it stamps a copy since the
# 2026-10 export writer), and the unfiltered comparison always found those
# two "removed" -- publishing a delta, bumping the revision and rewriting
# program_state.json for a cycle in which nothing was published at all.


class _DiffSpy:
    """Counts DeepDiff calls made by SaveState and records their filter."""

    def __init__(self, monkeypatch):
        import src.core.StateManager as module

        self.calls = []
        real = module.DeepDiff

        def spy(*args, **kwargs):
            self.calls.append(kwargs.get("include_paths"))
            return real(*args, **kwargs)

        monkeypatch.setattr(module, "DeepDiff", spy)


HELD_ONLY_PREFIX = "score.9"


@pytest.fixture
def _heldOnlyBoard():
    SM.Set(f"{HELD_ONLY_PREFIX}.match", "Baseline")
    SM.HoldSubtree(HELD_ONLY_PREFIX)
    _join_export()
    yield HELD_ONLY_PREFIX
    with SM.lock:
        SM.heldPrefixes.discard(HELD_ONLY_PREFIX)
        SM.heldKeys.pop(HELD_ONLY_PREFIX, None)
        SM._commitPrefixes.discard(HELD_ONLY_PREFIX)
        SM._heldTouched.discard(HELD_ONLY_PREFIX)
    SM.Unset(HELD_ONLY_PREFIX)


def test_a_held_only_save_block_never_diffs(monkeypatch, _heldOnlyBoard):
    diffs = _DiffSpy(monkeypatch)
    spy = _Spy()
    revision = SM.revision

    with SM.SaveBlock():
        SM.Set(f"{_heldOnlyBoard}.team.1.score", 1)
        SM.Set(f"{_heldOnlyBoard}.stages.1.t1_win", True)

    assert diffs.calls == [], (
        "a save cycle with nothing to publish still ran DeepDiff; with an "
        "empty include_paths that is a full-state comparison")
    assert spy.deltas == []
    assert SM.revision == revision, (
        "a held-only cycle bumped the revision, so every client saw a "
        "discontinuity for a change that was never published")
    # What the cycle exists for still happens: the live-value notification.
    assert spy.held_updates == [_heldOnlyBoard]
    assert SM.Get(f"{_heldOnlyBoard}.team.1.score") == 1


def test_committing_a_clean_prefix_diffs_nothing(monkeypatch, _heldOnlyBoard):
    assert not SM.HasPendingChanges(_heldOnlyBoard)

    diffs = _DiffSpy(monkeypatch)
    spy = _Spy()
    SM.CommitHeld(_heldOnlyBoard)

    assert diffs.calls == []
    assert spy.deltas == []


def test_a_block_that_wrote_an_unheld_key_still_diffs_filtered(
        monkeypatch, _heldOnlyBoard):
    """The skip must be "nothing queued", not "held prefixes exist"."""
    diffs = _DiffSpy(monkeypatch)
    spy = _Spy()

    with SM.SaveBlock():
        SM.Set(f"{_heldOnlyBoard}.phase", "Pending")   # held
        SM.Set("game.name", "MixedGame")               # published

    assert len(diffs.calls) == 1
    # A set, because SaveState builds include_paths with set(changedKeys) and
    # that order is not the write order -- asserting the list would make a
    # second changed key here a flaky test rather than a failing one.
    assert set(diffs.calls[0]) == {"root['game']['name']"}, (
        "the diff was not filtered to the keys that actually changed")
    assert "MixedGame" in spy.delta_str()
    assert "Pending" not in spy.delta_str()


def test_committing_pending_edits_still_diffs_them(monkeypatch, _heldOnlyBoard):
    SM.Set(f"{_heldOnlyBoard}.match", "Grand Final")
    assert SM.HasPendingChanges(_heldOnlyBoard)

    diffs = _DiffSpy(monkeypatch)
    spy = _Spy()
    SM.CommitHeld(_heldOnlyBoard)

    assert len(diffs.calls) == 1
    assert set(diffs.calls[0]) == {"root['score']['9']['match']"}
    assert "Grand Final" in spy.delta_str()
