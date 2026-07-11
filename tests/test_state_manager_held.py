"""
Tests for StateManager deferred publication (HoldSubtree / CommitHeld).

Held subtree semantics (the 反映 button feature):
- Writes under a held prefix land in the live state immediately (Get()),
  but are excluded from delta broadcast / GetFullState / file export
  until CommitHeld(prefix) publishes them in one save cycle.

StateManager.py does file I/O relative to CWD at import time, so the module
is imported once into a temp working directory via a fake parent package
(direct_import can't be used: StateManager has relative imports).
"""
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

_WORK_DIR = tempfile.mkdtemp(prefix="autona_sm_test_")
_ORIGINAL_CWD = os.getcwd()


def _import_state_manager():
    # StateManager / SettingsManager do file I/O relative to CWD at import
    # time (./out, ./user_data). Import under the temp dir, then restore the
    # CWD so later-collected test modules are unaffected.
    os.chdir(_WORK_DIR)
    os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)
    try:
        pkg = types.ModuleType("autona_sm_test_pkg")
        pkg.__path__ = [str(SRC_ROOT)]
        sys.modules["autona_sm_test_pkg"] = pkg
        spec = importlib.util.spec_from_file_location(
            "autona_sm_test_pkg.StateManager", SRC_ROOT / "StateManager.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["autona_sm_test_pkg.StateManager"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.chdir(_ORIGINAL_CWD)


_mod = _import_state_manager()
SM = _mod.StateManager


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
    def __init__(self):
        self.deltas = []
        self.dirty_events = []
        self.held_updates = []
        SM.signals.state_updated.connect(lambda d: self.deltas.append(d))
        SM.signals.held_dirty_changed.connect(
            lambda p, dirty: self.dirty_events.append((p, dirty)))
        SM.signals.held_state_updated.connect(
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
