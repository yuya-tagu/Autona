"""The state export thread must only ever walk private data.

SaveState() diffs the *live* state and then hands the resulting DeepDiff to a
background thread that writes ./out. DeepDiff does not copy: its
`values_changed` entries hold references straight into StateManager.state.
So the export thread was walking dicts the GUI thread was still writing to,
and died with "dictionary changed size during iteration" partway through the
batch.

That failure is close to invisible in production. The dying thread's
traceback does land in the log (the frozen build routes stderr to
./logs/tsh-error.log as ERROR), but nothing surfaces it to the GUI and no
caller reacts to it: what the operator sees is a handful of ./out files that
quietly never got written -- an OBS overlay stuck on the previous score. It
was found the first time the widget harness ran (see
docs/changes/test-automation-strategy.md §3).

The fix reads values out of the snapshot instead, by path. These tests pin
that: the diff may describe *which* paths changed, never *what* they hold.
"""
import os
import threading
from pathlib import Path

import pytest
from deepdiff import DeepDiff

from src.StateManager import StateManager as SM

_ORIGINAL_CWD = os.getcwd()


@pytest.fixture(scope="module")
def _workDir(tmp_path_factory):
    """A throwaway ./out root, cleaned up by pytest.

    Module-scoped via tmp_path_factory rather than tempfile.mkdtemp() at
    import time: mkdtemp leaves its directory behind on every run, and these
    tests had accumulated a pile of them in %TEMP%.
    """
    directory = tmp_path_factory.mktemp("autona_export")
    (directory / "out").mkdir()
    return directory


@pytest.fixture(autouse=True)
def _in_work_dir(_workDir):
    # ExportText writes ./out relative to the process CWD.
    os.chdir(_workDir)
    yield
    os.chdir(_ORIGINAL_CWD)


@pytest.fixture
def exported(_workDir):
    """Path to a file the export should have written."""
    def _exported(*parts):
        return Path(_workDir, "out", *parts)
    return _exported


def test_changed_values_are_read_from_the_snapshot(exported):
    """The snapshot wins over the diff, because only it is private.

    Pre-fix this wrote "live" -- the value the diff was still pointing at.
    """
    old = {"probe": {"leaf": "old"}}
    # What the GUI thread has moved on to. The export thread must not read
    # this object at all.
    live = {"probe": {"leaf": "live"}}
    diff = DeepDiff(old, live, verbose_level=2)
    # The private copy SaveState hands over, taken under the lock.
    snapshot = {"probe": {"leaf": "snapshot"}}

    SM.ExportText(old, diff, snapshot)

    assert exported("probe", "leaf.txt").read_text(encoding="utf-8") == "snapshot"


def test_added_keys_are_read_from_the_snapshot(exported):
    """Characterization, not a regression: the added-keys branch already read
    the snapshot. Pinned so the shared lookup keeps behaving that way."""
    old = {"probe": {}}
    live = {"probe": {"added": "live"}}
    diff = DeepDiff(old, live, verbose_level=2)
    snapshot = {"probe": {"added": "snapshot"}}

    SM.ExportText(old, diff, snapshot)

    assert exported("probe", "added.txt").read_text(encoding="utf-8") == "snapshot"


def test_a_subtree_mutated_during_the_walk_cannot_break_the_export(exported):
    """The original crash, made deterministic.

    `live` mutates itself the moment anything iterates it -- exactly what a
    concurrent GUI-thread write looked like to the export thread. Reading
    values from the snapshot means the export never touches it.
    """
    class _ConcurrentlyWritten(dict):
        def items(self):
            # Stand-in for another thread writing a new player key mid-walk.
            dict.__setitem__(self, f"racy{len(self)}", "x")
            return dict.items(self)

    old = {"probe": {"team": {"a": "1"}}}
    live = {"probe": {"team": _ConcurrentlyWritten({"a": "2"})}}
    diff = DeepDiff(old, live, verbose_level=2)
    snapshot = {"probe": {"team": {"a": "2"}}}

    SM.ExportText(old, diff, snapshot)

    assert exported("probe", "team", "a.txt").read_text(encoding="utf-8") == "2"


def test_a_full_save_cycle_never_kills_the_export_thread():
    """End to end: keep writing while saves run, and watch for a dead thread.

    A net rather than a proof -- whether the export thread is still running
    when the next write lands depends on timing, so this does not reproduce
    the bug on demand the way the two tests above do. It is kept because it
    is the shape the crash was first seen in, and because threading.excepthook
    is the only place that failure ever surfaced: the exception never reached
    the GUI thread, which is why it went unnoticed for so long.
    """
    failures = []
    previousHook = threading.excepthook
    threading.excepthook = lambda args: failures.append(args)

    try:
        for i in range(25):
            with SM.SaveBlock():
                for player in range(1, 6):
                    SM.Set(f"exportprobe.team.1.player.{player}",
                           {"name": f"P{player}-{i}", "score": i})
            # Interleave more writes into the same subtree while the export
            # thread started by the block above is still running.
            for player in range(1, 6):
                SM.Set(f"exportprobe.team.1.player.{player}.extra", i)

    finally:
        SM.Unset("exportprobe")
        # Join last: Unset triggers one more save cycle, and a straggler
        # thread writing after pytest has torn down its capture streams turns
        # into unrelated noise in the run.
        if SM._export_thread is not None:
            SM._export_thread.join()
        threading.excepthook = previousHook

    assert failures == [], (
        "the export thread died: "
        + "; ".join(f"{f.exc_type.__name__}: {f.exc_value}" for f in failures))
