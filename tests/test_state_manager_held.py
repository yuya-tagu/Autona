"""
Tests for StateManager deferred publication (HoldSubtree / CommitHeld /
ReleaseSubtree).

Held subtree semantics (the 反映 button feature):
- Writes under a held prefix land in the live state immediately (Get()),
  but are excluded from delta broadcast / GetFullState / file export
  until CommitHeld(prefix) publishes them in one save cycle.

Importing the module is side-effect free, but StateManager.Initialize() does
file I/O relative to the CWD (./out), so it runs once inside a temp working
directory.
"""
import atexit
import os
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from src.core.StateManager import StateManager as SM

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

_WORK_DIR = tempfile.mkdtemp(prefix="autona_sm_test_")
# Usually the first Initialize() of the run, so the persisted store
# (user_data/state.json, ADR-005 S3b) lives here for the whole session.
# Removed at exit: atexit runs after the interpreter has joined the export
# and store writers (both non-daemon), so nothing writes here any more.
atexit.register(shutil.rmtree, _WORK_DIR, ignore_errors=True)
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


def _drop_held(prefix):
    # Forget a held registration and everything it left behind, then remove
    # the subtree so no pending/published value survives.
    with SM.lock:
        SM.heldPrefixes.discard(prefix)
        SM.heldKeys.pop(prefix, None)
        SM.heldExempt.pop(prefix, None)
        SM._commitPrefixes.discard(prefix)
        SM._heldTouched.discard(prefix)
        SM._heldCommitted.discard(prefix)
    SM.Unset(prefix)


@pytest.fixture(scope="module", autouse=True)
def _release_module_held_prefixes():
    # StateManager is process-global and tests in this file build on each
    # other's state, so cleanup is per module, not per test. Without it the
    # held registrations (and the unpublished edits left under them) leak
    # into later modules, e.g. a stale dirty flag on score.2.
    yield
    os.chdir(_WORK_DIR)     # Unset() exports to ./out (same reason as _in_work_dir)
    try:
        for prefix in (PREFIX, EX_PREFIX):
            _drop_held(prefix)
        _join_export()
    finally:
        os.chdir(_ORIGINAL_CWD)


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
# SwapTeams and every stage click (headless core Step 4a; the tracker went in
# ADR-005 S4ac-4).
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
    _drop_held(HELD_ONLY_PREFIX)


def test_a_held_only_save_block_never_diffs(monkeypatch, _heldOnlyBoard):
    diffs = _DiffSpy(monkeypatch)
    spy = _Spy()
    revision = SM.revision

    with SM.SaveBlock():
        SM.Set(f"{_heldOnlyBoard}.team.1.score", 1)
        SM.Set(f"{_heldOnlyBoard}.team.2.score", 2)

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


# ── held_dirty_changed follows HasPendingChanges (S4u) ───────────────────
# The notification is a cue to read HasPendingChanges, told after what it is
# about. A commit inside an open block only queues: the keys are
# pending until the SaveState that closes the block drains them, so that is
# where the False comes from. It used to be told from CommitHeld, ahead of
# the drain, and only when keys were pending by then.


class _DirtyTold:
    """(payload, HasPendingChanges at that moment) of each notification."""

    def __init__(self, prefix):
        self.prefix = prefix
        self.told = []

    def _on(self, prefix, dirty):
        if prefix == self.prefix:
            self.told.append((dirty, SM.HasPendingChanges(prefix)))

    def __enter__(self):
        SM.observers.held_dirty_changed.subscribe(self._on)
        return self

    def __exit__(self, *exc):
        SM.observers.held_dirty_changed.unsubscribe(self._on)


def test_a_commit_outside_a_block_tells_false_once_it_is_drained(_heldOnlyBoard):
    with _DirtyTold(_heldOnlyBoard) as dirty:
        SM.Set(f"{_heldOnlyBoard}.phase", "Pending")
        SM.CommitHeld(_heldOnlyBoard)
    assert dirty.told == [(True, True), (False, False)]


def test_a_commit_of_nothing_tells_nothing(_heldOnlyBoard):
    with _DirtyTold(_heldOnlyBoard) as dirty:
        SM.CommitHeld(_heldOnlyBoard)
        with SM.SaveBlock():
            SM.CommitHeld(_heldOnlyBoard)
    assert dirty.told == []


def test_a_commit_inside_a_block_tells_false_when_the_block_closes(
        _heldOnlyBoard):
    with _DirtyTold(_heldOnlyBoard) as dirty:
        with SM.SaveBlock():
            SM.Set(f"{_heldOnlyBoard}.phase", "Pending")
            SM.CommitHeld(_heldOnlyBoard)
            # Queued: still pending, and nothing said otherwise.
            assert SM.HasPendingChanges(_heldOnlyBoard)
            assert dirty.told == [(True, True)]
    assert not SM.HasPendingChanges(_heldOnlyBoard)
    assert dirty.told == [(True, True), (False, False)]
    assert SM.GetCommitted(f"{_heldOnlyBoard}.phase") == "Pending"


def test_a_write_after_the_commit_in_the_block_is_lowered_with_it(
        _heldOnlyBoard):
    """Nothing was pending when the commit was queued, so it had nothing to
    tell then; the write raises the flag and the drain takes it along. It
    used to stay raised."""
    with _DirtyTold(_heldOnlyBoard) as dirty:
        with SM.SaveBlock():
            SM.CommitHeld(_heldOnlyBoard)
            SM.Set(f"{_heldOnlyBoard}.phase", "Pending")
    assert not SM.HasPendingChanges(_heldOnlyBoard)
    assert dirty.told == [(True, True), (False, False)]
    assert SM.GetCommitted(f"{_heldOnlyBoard}.phase") == "Pending"


def test_a_commit_queued_on_another_threads_block_tells_when_that_closes(
        _heldOnlyBoard):
    """The save block is process-wide: a 反映 while another thread's block is
    open only queues too."""
    with _DirtyTold(_heldOnlyBoard) as dirty:
        SM.Set(f"{_heldOnlyBoard}.phase", "Pending")
        SM.BlockSaving()
        try:
            worker = threading.Thread(
                target=lambda: SM.CommitHeld(_heldOnlyBoard))
            worker.start()
            worker.join()
            assert dirty.told == [(True, True)]
        finally:
            SM.ReleaseSaving()
    assert dirty.told == [(True, True), (False, False)]


def test_a_write_during_the_publish_keeps_the_flag_up(_heldOnlyBoard):
    """A state_updated handler that writes under the prefix while the commit
    publishes dirties it again before the False would go out: the flag
    stays up, and the last thing told is True."""
    def write(_payload):
        SM.observers.state_updated.unsubscribe(write)
        SM.Set(f"{_heldOnlyBoard}.match", "Written while publishing")

    with _DirtyTold(_heldOnlyBoard) as dirty:
        SM.Set(f"{_heldOnlyBoard}.phase", "Pending")
        SM.observers.state_updated.subscribe(write)
        SM.CommitHeld(_heldOnlyBoard)
    assert SM.HasPendingChanges(_heldOnlyBoard)
    assert dirty.told == [(True, True), (True, True)]
    SM.CommitHeld(_heldOnlyBoard)


def test_the_peek_answers_what_the_flag_answers(_heldOnlyBoard):
    assert SM.PeekPendingChanges(_heldOnlyBoard) is False
    SM.Set(f"{_heldOnlyBoard}.phase", "Pending")
    assert SM.PeekPendingChanges(_heldOnlyBoard) is True
    SM.CommitHeld(_heldOnlyBoard)
    assert SM.PeekPendingChanges(_heldOnlyBoard) is False


# ── ReleaseSubtree: the hold undone (ADR-005 S4y review) ─────────────────
# For a board whose making or drawing failed (AutonaActions.ForgetBoard):
# nothing would ever apply its hold.


def _lastPublished(key):
    from src.core.DictHelper import deep_get
    with SM.lock:
        return deep_get(SM.lastSavedState, key)


def test_a_release_publishes_what_was_pending_and_then_every_write(
        _heldOnlyBoard):
    with _DirtyTold(_heldOnlyBoard) as dirty:
        SM.Set(f"{_heldOnlyBoard}.phase", "Pending")
        assert _lastPublished(f"{_heldOnlyBoard}.phase") is None
        SM.ReleaseSubtree(_heldOnlyBoard)
    assert dirty.told == [(True, True), (False, False)]
    assert _lastPublished(f"{_heldOnlyBoard}.phase") == "Pending"
    with SM.lock:
        assert _heldOnlyBoard not in SM.heldPrefixes

    SM.Set(f"{_heldOnlyBoard}.match", "Published as written")
    assert _lastPublished(f"{_heldOnlyBoard}.match") == "Published as written"
    assert not SM.HasPendingChanges(_heldOnlyBoard)


def test_a_release_inside_a_block_publishes_when_the_block_closes(
        _heldOnlyBoard):
    with _DirtyTold(_heldOnlyBoard) as dirty:
        with SM.SaveBlock():
            SM.Set(f"{_heldOnlyBoard}.phase", "Pending")
            SM.ReleaseSubtree(_heldOnlyBoard)
            assert _lastPublished(f"{_heldOnlyBoard}.phase") is None
            assert dirty.told == [(True, True)]
    assert _lastPublished(f"{_heldOnlyBoard}.phase") == "Pending"
    assert dirty.told == [(True, True), (False, False)]


def test_a_release_with_nothing_pending_tells_nothing(_heldOnlyBoard):
    with _DirtyTold(_heldOnlyBoard) as dirty:
        SM.ReleaseSubtree(_heldOnlyBoard)
        # Not held any more: a second release leaves it alone.
        SM.ReleaseSubtree(_heldOnlyBoard)
    assert dirty.told == []
    with SM.lock:
        assert _heldOnlyBoard not in SM.heldPrefixes


def test_a_failed_publish_still_lowers_the_flag(monkeypatch, _heldOnlyBoard):
    """The keys left heldKeys for changedKeys before the publish failed, so
    nothing is pending any more: the next cycle says so."""
    import src.core.StateManager as module

    def boom(*args, **kwargs):
        raise RuntimeError("the diff failed")

    with _DirtyTold(_heldOnlyBoard) as dirty:
        SM.Set(f"{_heldOnlyBoard}.phase", "Pending")
        with monkeypatch.context() as patched:
            patched.setattr(module, "DeepDiff", boom)
            with pytest.raises(RuntimeError):
                SM.CommitHeld(_heldOnlyBoard)
        assert not SM.HasPendingChanges(_heldOnlyBoard)
        SM.SaveState()
    assert dirty.told == [(True, True), (False, False)]


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


# ── WhenPublished (ADR-005 S3f-4 review) ─────────────────────────────────
# A reader of the whole state that must not get ahead of the published
# revision (the web server's game-change push) waits for the open save
# blocks: inside one, GetFullState() already has the block's writes with
# the revision before them.


def _fullState():
    full = SM.GetFullState()
    return full["revision"], full["state"].get("game", {}).get("name")


def test_when_published_runs_after_the_last_open_block_publishes():
    order = []

    def onDelta(payload):
        order.append(("delta", payload["revision"]))

    SM.observers.state_updated.subscribe(onDelta)
    try:
        with SM.SaveBlock():
            SM.Set("game.name", "WhenPublished")
            with SM.SaveBlock():
                SM.WhenPublished(lambda: order.append(("full",) + _fullState()))
            assert order == []    # the outer block is still open
    finally:
        SM.observers.state_updated.unsubscribe(onDelta)

    (delta, revision), full = order
    assert delta == "delta"
    # Its revision is the one its content was published with.
    assert full == ("full", revision, "WhenPublished")
    assert SM.saveBlocked == 0


def test_when_published_outside_a_block_runs_at_once():
    ran = []
    SM.WhenPublished(lambda: ran.append(SM.saveBlocked))
    assert ran == [0]


def test_a_failing_when_published_callback_does_not_stop_the_others():
    ran = []

    def failing():
        raise RuntimeError("a callback failed")

    with SM.SaveBlock():
        SM.Set("game.name", "Callbacks")
        SM.WhenPublished(failing)
        SM.WhenPublished(lambda: ran.append(1))
    assert ran == [1]
    assert SM._afterPublish == []


def test_a_block_opened_on_one_thread_publishes_where_it_is_closed():
    """The game load's cycle: opened on its job's thread, closed -- and
    published, its notifications run -- on the GUI thread."""
    import threading

    threads = []

    def onDelta(payload):
        threads.append(threading.current_thread().name)

    opener = threading.Thread(target=SM.BlockSaving, name="Opener")
    opener.start()
    opener.join()
    SM.observers.state_updated.subscribe(onDelta)
    try:
        SM.Set("game.name", "TwoThreads")
        assert threads == []
        SM.ReleaseSaving()
    finally:
        SM.observers.state_updated.unsubscribe(onDelta)
    assert threads == [threading.current_thread().name]
    assert SM.saveBlocked == 0


# ── A partial commit: what one operation wrote (S4ad remainder 5) ────────
# CommitHeld(prefix, written) with the HeldWrites of a RecordHeldWrites
# block: a game load's commit (AutonaActions.CommitGameLoad). It publishes
# the keys the operation wrote and nobody else touched; the operator's
# edits -- pending before it began, or written meanwhile on another thread
# -- stay behind 反映.


@pytest.fixture
def _recordingBoard(_heldOnlyBoard):
    yield _heldOnlyBoard
    with SM.lock:
        assert SM._heldRecords == [], "a recording outlived its commits"
        assert SM._commitWritten == {}
        assert SM._takenLive == {}, "a partial commit was not published"


def _onAnotherThread(write):
    worker = threading.Thread(target=write)
    worker.start()
    worker.join()


class _Deltas:
    """The delta rows published inside the block."""

    def __init__(self):
        self.payloads = []

    def _on(self, payload):
        self.payloads.append(payload)

    def __enter__(self):
        SM.observers.state_updated.subscribe(self._on)
        return self

    def __exit__(self, *exc):
        SM.observers.state_updated.unsubscribe(self._on)

    def text(self):
        return str([payload["delta"] for payload in self.payloads])


def test_a_partial_commit_publishes_only_what_the_operation_wrote(
        _recordingBoard):
    board = _recordingBoard
    SM.Set(f"{board}.phase", "Before")          # pending before it began
    with _Deltas() as deltas, _DirtyTold(board) as dirty:
        with SM.RecordHeldWrites() as written:
            with SM.SaveBlock():
                SM.Set(f"{board}.match", "Loaded")
                _onAnotherThread(lambda: SM.Set(f"{board}.pool", "Typed"))
                SM.CommitHeld(board, written)
                # Decided when it is drained: a write after the commit in
                # the same cycle goes with it.
                SM.Set(f"{board}.round", "After the commit")
                assert _lastPublished(f"{board}.match") == "Baseline"

    assert _lastPublished(f"{board}.match") == "Loaded"
    assert _lastPublished(f"{board}.round") == "After the commit"
    assert _lastPublished(f"{board}.phase") is None
    assert _lastPublished(f"{board}.pool") is None
    assert "Before" not in deltas.text() and "Typed" not in deltas.text()
    assert "Loaded" in deltas.text()
    assert SM.GetFullState()["state"]["score"]["9"].get("pool") is None
    # Still pending: no False, and the live state has everything.
    assert SM.HasPendingChanges(board)
    assert dirty.told == []
    assert SM.Get(f"{board}.pool") == "Typed"

    with _DirtyTold(board) as dirty:
        SM.CommitHeld(board)
    assert dirty.told == [(False, False)]
    assert _lastPublished(f"{board}.phase") == "Before"
    assert _lastPublished(f"{board}.pool") == "Typed"


def test_a_partial_commit_of_everything_pending_lowers_the_flag(
        _recordingBoard):
    board = _recordingBoard
    with _DirtyTold(board) as dirty:
        with SM.RecordHeldWrites() as written:
            with SM.SaveBlock():
                SM.Set(f"{board}.match", "Loaded")
                SM.CommitHeld(board, written)
    assert dirty.told == [(True, True), (False, False)]
    assert _lastPublished(f"{board}.match") == "Loaded"


def test_a_nested_recording_is_the_outer_one(_recordingBoard):
    board = _recordingBoard
    with SM.RecordHeldWrites() as outer:
        with SM.SaveBlock():
            with SM.RecordHeldWrites() as inner:
                SM.Set(f"{board}.match", "Inner")
            assert inner is outer
            SM.Set(f"{board}.phase", "Outer")
            SM.CommitHeld(board, outer)
    assert _lastPublished(f"{board}.match") == "Inner"
    assert _lastPublished(f"{board}.phase") == "Outer"
    assert not SM.HasPendingChanges(board)


@pytest.mark.parametrize("theirs, mine", [
    ("team.1.score", "team.1.score"),     # the same key
    ("team.1", "team.1.score"),           # above it
    ("team.1.score", "team.1"),           # below it
], ids=["same", "above", "below"])
def test_a_key_someone_else_wrote_too_stays_pending(
        _recordingBoard, theirs, mine):
    """Published, it would carry their edit, or a value made from it (the
    BEST OF text re-derived over the operator's set length)."""
    board = _recordingBoard
    SM.Set(f"{board}.team", {"1": {"score": 0}})
    SM.CommitHeld(board)
    SM.Set(f"{board}.{theirs}",
           {"score": 3} if theirs == "team.1" else 3)

    with SM.RecordHeldWrites() as written:
        with SM.SaveBlock():
            SM.Set(f"{board}.{mine}",
                   {"score": 5} if mine == "team.1" else 5)
            SM.Set(f"{board}.phase", "Loaded")
            SM.CommitHeld(board, written)

    assert _lastPublished(f"{board}.team") == {"1": {"score": 0}}
    assert _lastPublished(f"{board}.phase") == "Loaded"
    assert SM.HasPendingChanges(board)
    SM.CommitHeld(board)
    assert _lastPublished(f"{board}.team") == {"1": {"score": 5}}


def test_a_write_after_the_recording_ended_is_someone_else_s(
        _recordingBoard):
    """Until its commit is drained: here the cycle it was queued in is
    still open when the recording ends."""
    board = _recordingBoard
    with SM.SaveBlock():
        with SM.RecordHeldWrites() as written:
            SM.Set(f"{board}.match", "Loaded")
            SM.CommitHeld(board, written)
        SM.Set(f"{board}.match", "Typed after it")
    assert _lastPublished(f"{board}.match") == "Baseline"
    assert SM.Get(f"{board}.match") == "Typed after it"
    assert SM.HasPendingChanges(board)


def test_a_subtree_new_to_the_published_view_goes_whole_in_live_order(
        _recordingBoard):
    board = _recordingBoard
    SM.Set(f"{board}.phase", "Before")          # keeps the board pending
    with SM.RecordHeldWrites() as written:
        with SM.SaveBlock():
            SM.Set(f"{board}.team.2.name", "A")
            SM.Set(f"{board}.team.2.score", 1)
            SM.CommitHeld(board, written)
    published = _lastPublished(f"{board}.team.2")
    assert published == {"name": "A", "score": 1}
    assert list(published) == list(SM.Get(f"{board}.team.2"))
    assert _lastPublished(f"{board}.phase") is None


def test_a_new_subtree_someone_else_wrote_in_is_not_published_in_part(
        _recordingBoard):
    """A key at a time would make it in another key order than the live
    state's, and could publish a part of what someone else is making."""
    board = _recordingBoard
    SM.Set(f"{board}.team.2.color", "#fff")     # theirs, pending
    with SM.RecordHeldWrites() as written:
        with SM.SaveBlock():
            SM.Set(f"{board}.team.2.name", "A")
            SM.Set(f"{board}.match", "Loaded")
            SM.CommitHeld(board, written)
    assert _lastPublished(f"{board}.team") is None
    assert _lastPublished(f"{board}.match") == "Loaded"
    SM.CommitHeld(board)
    assert list(_lastPublished(f"{board}.team.2")) == ["color", "name"]


def test_a_commit_after_the_recording_ended_publishes_nothing(
        _recordingBoard):
    from loguru import logger

    board = _recordingBoard
    with SM.RecordHeldWrites() as written:
        SM.Set(f"{board}.match", "Loaded")
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        SM.CommitHeld(board, written)
    finally:
        logger.remove(sink)
    assert any("the recording is over" in m for m in messages)
    assert _lastPublished(f"{board}.match") == "Baseline"
    assert SM.HasPendingChanges(board)


def test_a_release_drops_the_partial_commit(_recordingBoard):
    board = _recordingBoard
    with SM.RecordHeldWrites() as written:
        with SM.SaveBlock():
            SM.Set(f"{board}.match", "Loaded")
            SM.CommitHeld(board, written)
            SM.ReleaseSubtree(board)
            with SM.lock:
                assert board not in SM._commitWritten
    assert _lastPublished(f"{board}.match") == "Loaded"
    assert not SM.HasPendingChanges(board)


def test_a_release_drops_an_ended_recording_at_once(_recordingBoard):
    """Its only commit dropped, nothing will ask it anything: not kept for
    the next save cycle."""
    board = _recordingBoard
    with SM.SaveBlock():
        with SM.RecordHeldWrites() as written:
            SM.Set(f"{board}.match", "Loaded")
            SM.CommitHeld(board, written)
        SM.ReleaseSubtree(board)
        with SM.lock:
            assert SM._heldRecords == []


def test_a_drain_that_raises_still_drops_the_ended_recordings(
        monkeypatch, _recordingBoard):
    """Ended before its commit is drained (kept for it), then the drain
    fails: dropped all the same."""
    board = _recordingBoard

    def boom(*args):
        raise RuntimeError("the drain failed")

    with monkeypatch.context() as patched:
        patched.setattr(SM, "_publishedUnits", staticmethod(boom))
        with pytest.raises(RuntimeError):
            with SM.SaveBlock():
                with SM.RecordHeldWrites() as written:
                    SM.Set(f"{board}.match", "Loaded")
                    SM.CommitHeld(board, written)
                with SM.lock:
                    assert len(SM._heldRecords) == 1
    with SM.lock:
        assert SM._heldRecords == []
    # Not published, and still pending: 反映 has it.
    assert _lastPublished(f"{board}.match") == "Baseline"
    SM.CommitHeld(board)
    assert _lastPublished(f"{board}.match") == "Loaded"


# ── HeldWriteGroup: what goes together, what the operation decided ───────
# Review of the partial commit (2026-11-12): a game load that switched games
# published a slot's blank mains while its characters waited with a pick
# not applied -- OBS showed the previous game's characters beside blank
# mains -- and a reload of the same game published mains it had made from
# such a pick.


@pytest.mark.parametrize("theirs, mine, superseded", [
    ("team.1.score", "team.1.score", True),     # at it, written before it
    ("team.1.score", "team.1", True),           # below it
    ("team.1", "team.1.score", False),          # above it
], ids=["at", "below", "above"])
def test_an_authoritative_write_supersedes_an_edit_it_overwrote(
        _recordingBoard, theirs, mine, superseded):
    """The live value is the operation's: the edit is gone from it. So the
    key goes out, and the edit is no longer pending -- not one above it,
    which still holds what the operation did not write."""
    board = _recordingBoard
    SM.Set(f"{board}.team", {"1": {"score": 0}})
    SM.CommitHeld(board)
    SM.Set(f"{board}.{theirs}", {"score": 3} if theirs == "team.1" else 3)
    SM.Set(f"{board}.phase", "Theirs")          # keeps the board pending

    with SM.RecordHeldWrites() as written:
        with SM.SaveBlock():
            with SM.HeldWriteGroup(authoritative=True):
                SM.Set(f"{board}.{mine}",
                       {"score": 5} if mine == "team.1" else 5)
            SM.CommitHeld(board, written)

    assert _lastPublished(f"{board}.phase") is None
    assert SM.HasPendingChanges(board)
    with SM.lock:
        teamPending = [key for key in SM.heldKeys[board] if "team" in key]
    if superseded:
        assert _lastPublished(f"{board}.team") == {"1": {"score": 5}}
        assert teamPending == []
    else:
        assert _lastPublished(f"{board}.team") == {"1": {"score": 0}}
        assert teamPending


def test_an_edit_after_the_authoritative_write_is_not_superseded(
        _recordingBoard):
    board = _recordingBoard
    with SM.RecordHeldWrites() as written:
        with SM.SaveBlock():
            with SM.HeldWriteGroup(authoritative=True):
                SM.Set(f"{board}.match", "Loaded")
            _onAnotherThread(lambda: SM.Set(f"{board}.match", "Typed"))
            SM.CommitHeld(board, written)
    assert _lastPublished(f"{board}.match") == "Baseline"
    assert SM.HasPendingChanges(board)


def test_an_authoritative_write_back_to_the_published_value_lowers_the_flag(
        _recordingBoard):
    """An edit the operation overwrote with what is published already: the
    flag would stay up with nothing to apply."""
    board = _recordingBoard
    SM.Set(f"{board}.match", "Typed")
    with _DirtyTold(board) as dirty:
        with SM.RecordHeldWrites() as written:
            with SM.SaveBlock():
                with SM.HeldWriteGroup(authoritative=True):
                    SM.Set(f"{board}.match", "Baseline")
                SM.CommitHeld(board, written)
    assert not SM.HasPendingChanges(board)
    assert dirty.told == [(False, False)]


@pytest.mark.parametrize("authoritative", [False, True],
                         ids=["made-from-it", "its-own"])
def test_a_group_goes_out_together_or_waits_together(
        _recordingBoard, authoritative):
    """A player's characters and mains, made from one read: one held back
    by someone's pending key holds the other back, unless the operation's
    values are its own (then both go, over the edit)."""
    board = _recordingBoard
    SM.Set(f"{board}.player", {"character": "A", "mains": "A"})
    SM.CommitHeld(board)
    SM.Set(f"{board}.player.character", "Picked")   # not applied
    SM.Set(f"{board}.phase", "Theirs")

    with SM.RecordHeldWrites() as written:
        with SM.SaveBlock():
            with SM.HeldWriteGroup(authoritative=authoritative):
                # A nested group is the outer one.
                with SM.HeldWriteGroup():
                    SM.Set(f"{board}.player.character", "New")
                    SM.Set(f"{board}.player.mains", "New")
            SM.Set(f"{board}.match", "Loaded")      # in no group
            SM.CommitHeld(board, written)

    assert _lastPublished(f"{board}.match") == "Loaded"
    assert _lastPublished(f"{board}.player") == (
        {"character": "New", "mains": "New"} if authoritative
        else {"character": "A", "mains": "A"})


def test_a_group_outside_a_recording_does_nothing(_recordingBoard):
    board = _recordingBoard
    with SM.HeldWriteGroup(authoritative=True):
        SM.Set(f"{board}.match", "Typed")
    with SM.lock:
        assert getattr(SM._recording, "group", None) is None
    assert SM.HasPendingChanges(board)


@pytest.mark.parametrize("meanwhile", [False, True],
                         ids=["retried", "written-into-meanwhile"])
def test_a_partial_commit_whose_publish_failed_goes_with_the_next_one(
        monkeypatch, _recordingBoard, meanwhile):
    """As a full commit's keys do: drained, and left in changedKeys for the
    next save cycle. Not with a write into it since: that one waits for
    反映, and the path with it."""
    import src.core.StateManager as module

    board = _recordingBoard
    SM.Set(f"{board}.phase", "Theirs")          # stays pending

    def boom(*args, **kwargs):
        raise RuntimeError("the diff failed")

    with monkeypatch.context() as patched:
        patched.setattr(module, "DeepDiff", boom)
        with pytest.raises(RuntimeError):
            with SM.RecordHeldWrites() as written:
                with SM.SaveBlock():
                    SM.Set(f"{board}.match", "Loaded")
                    SM.CommitHeld(board, written)
    assert _lastPublished(f"{board}.match") == "Baseline"
    if meanwhile:
        SM.Set(f"{board}.match", "Typed")

    SM.SaveState()

    assert _lastPublished(f"{board}.match") == (
        "Baseline" if meanwhile else "Loaded")
    assert _lastPublished(f"{board}.phase") is None
    assert SM.HasPendingChanges(board)
    SM.CommitHeld(board)
    assert _lastPublished(f"{board}.match") == (
        "Typed" if meanwhile else "Loaded")


def test_two_recordings_at_once_each_publish_their_own(_recordingBoard):
    """Each is the other's someone else: a key both wrote waits."""
    board = _recordingBoard
    started, wrote = threading.Event(), threading.Event()

    def theirs():
        with SM.RecordHeldWrites() as written:
            started.set()
            SM.Set(f"{board}.pool", "Theirs")
            SM.Set(f"{board}.round", "Theirs")
            SM.CommitHeld(board, written)
            wrote.wait(10)

    with SM.SaveBlock():
        worker = threading.Thread(target=theirs)
        with SM.RecordHeldWrites() as written:
            worker.start()
            assert started.wait(10)
            SM.Set(f"{board}.match", "Mine")
            SM.Set(f"{board}.round", "Mine")
            SM.CommitHeld(board, written)
            wrote.set()
            worker.join(10)
            assert not worker.is_alive()

    assert _lastPublished(f"{board}.match") == "Mine"
    assert _lastPublished(f"{board}.pool") == "Theirs"
    assert _lastPublished(f"{board}.round") is None
    with SM.lock:
        assert SM.heldKeys[board] == ["root['score']['9']['round']"] * 2


def test_a_recording_that_ended_is_drained_by_another_thread(
        _recordingBoard):
    """Its commit queued in a cycle another thread closes (the loader opens
    the load's cycle; the GUI thread closes it): drained there, and dropped
    then."""
    board = _recordingBoard
    SM.Set(f"{board}.phase", "Theirs")
    SM.BlockSaving()
    try:
        def load():
            with SM.RecordHeldWrites() as written:
                SM.Set(f"{board}.match", "Loaded")
                SM.CommitHeld(board, written)

        _onAnotherThread(load)
        with SM.lock:
            assert len(SM._heldRecords) == 1
    finally:
        SM.ReleaseSaving()
    assert _lastPublished(f"{board}.match") == "Loaded"
    assert _lastPublished(f"{board}.phase") is None


def test_what_a_partial_commit_publishes_takes_its_live_place(
        _recordingBoard):
    """In its dict, where the live state has it: removed and written again,
    a key goes last there, not where it was."""
    board = _recordingBoard
    SM.Set(f"{board}.team", {"1": {"name": "A", "score": 0}})
    SM.CommitHeld(board)
    SM.Set(f"{board}.phase", "Theirs")
    with SM.RecordHeldWrites() as written:
        with SM.SaveBlock():
            SM.Unset(f"{board}.team.1.name")
            SM.Set(f"{board}.team.1.color", "#fff")
            SM.Set(f"{board}.team.1.name", "B")
            SM.CommitHeld(board, written)
    published = _lastPublished(f"{board}.team.1")
    assert published == {"score": 0, "color": "#fff", "name": "B"}
    assert list(published) == list(SM.Get(f"{board}.team.1"))


def test_a_partial_commit_of_a_change_to_none_alone_stays_published(
        monkeypatch, _recordingBoard):
    """What a partial commit publishes is a change to None, and nothing
    else, and DeepDiff leaves it out (exclude_types), so the cycle has no
    delta. It is published all the same -- the published view has it, and
    the next cycle does not rewind it. The re-review of 2026-11-12: with no
    delta, the paths taken live were dropped while lastSavedState kept the
    old value, so the value came back.

    DeepDiff 8.1.1 does report such a change once include_paths is given
    (its include filter is an if ahead of the exclude_types elif), and
    SaveState always gives it: the DeepDiff here is one that leaves the
    change out, as exclude_types says -- what the published view keeps must
    not hang on that."""
    import src.core.StateManager as module

    real = module.DeepDiff

    def noneBlind(t1, t2, **kwargs):
        diff = real(t1, t2, **kwargs)
        rows = diff.get("type_changes", {})
        if rows and set(diff) == {"type_changes"} and all(
                row["new_value"] is None for row in rows.values()):
            return real(t1, t1, **kwargs)
        return diff

    board = _recordingBoard
    SM.Set(f"{board}.team", {"name": "A", "logo": "crest.png"})
    SM.CommitHeld(board)
    SM.Set(f"{board}.phase", "Theirs")          # stays pending

    monkeypatch.setattr(module, "DeepDiff", noneBlind)
    with _Deltas() as deltas:
        with SM.RecordHeldWrites() as written:
            with SM.SaveBlock():
                SM.Set(f"{board}.team.logo", None)
                SM.CommitHeld(board, written)
    assert deltas.payloads == []

    assert SM.HasPendingChanges(board)
    published = {"name": "A", "logo": None}
    assert SM.GetCommitted(f"{board}.team") == published
    assert _lastPublished(f"{board}.team") == published
    # The next publish (a write outside the board) keeps it.
    SM.Set("held_none_probe", 1)
    try:
        assert _lastPublished(f"{board}.team") == published
        assert _lastPublished(f"{board}.phase") is None
    finally:
        SM.Unset("held_none_probe")


def test_groups_and_units_held_back_are_closed_transitively(_recordingBoard):
    """A key held back holds back its group, whatever shares a unit with
    it, the groups of those, and so on: here someone's pending key holds
    back group A; A's other key shares a unit (a subtree new to the
    published view) with group B's key; B's other key waits too. Under the
    re-review's finding the closure stopped after one step, and B's other
    key went out without the half of B that waited."""
    board = _recordingBoard
    SM.Set(f"{board}.player", {"character": "A", "mains": "A"})
    SM.CommitHeld(board)
    SM.Set(f"{board}.player.character", "Picked")   # not applied

    with SM.RecordHeldWrites() as written:
        with SM.SaveBlock():
            with SM.HeldWriteGroup():
                SM.Set(f"{board}.player.character", "A2")
                SM.Set(f"{board}.extra.one", "A2")
            with SM.HeldWriteGroup():
                SM.Set(f"{board}.extra.two", "B")
                SM.Set(f"{board}.match", "B")
            SM.CommitHeld(board, written)

    assert _lastPublished(f"{board}.player") == {
        "character": "A", "mains": "A"}
    assert _lastPublished(f"{board}.extra") is None
    assert _lastPublished(f"{board}.match") == "Baseline"


def test_a_unit_held_back_holds_back_the_units_above_it(_recordingBoard):
    """A key in a group held back, below a key the operation wrote whole
    (no group): published, the one above would carry the held-back value
    in its live subtree, so it waits too."""
    board = _recordingBoard
    SM.Set(f"{board}.team", {"name": "A", "score": 0})
    SM.CommitHeld(board)
    SM.Set(f"{board}.phase", "Theirs")          # not applied

    with SM.RecordHeldWrites() as written:
        with SM.SaveBlock():
            SM.Set(f"{board}.team", {"name": "A", "score": 1})
            with SM.HeldWriteGroup():
                SM.Set(f"{board}.team.name", "B")
                SM.Set(f"{board}.phase", "Made from it")
            SM.Set(f"{board}.match", "Loaded")
            SM.CommitHeld(board, written)

    assert _lastPublished(f"{board}.team") == {"name": "A", "score": 0}
    assert _lastPublished(f"{board}.match") == "Loaded"
