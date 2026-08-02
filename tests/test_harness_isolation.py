"""The widget harness must never touch the real installation.

`autonaApp` boots the actual app: it builds real scoreboards, writes real
state and lets StateManager's export thread write real files. All of that
resolves against the process CWD, so the harness runs inside a throwaway
directory. If that ever slipped, a test run would overwrite the developer's
`out/` (what OBS is reading) or `user_data/settings.json` (their API token,
hotkeys, ports) -- silently, and while they were streaming.

So this is not a test of a feature; it is the guard that makes every other
widget test safe to run.
"""
import os
from pathlib import Path

from tests import conftest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Walked but not fingerprinted: the bundled game data is hundreds of MB of
# files nothing in the harness writes, and stat()ing all of them on every
# assertion would dominate the suite's runtime.
_SKIPPED_SUBTREES = {"games"}


def _snapshot(directory: Path):
    """Every file under `directory` as (relative path) -> (size, mtime).

    Recursive on purpose. A one-level listing plus directory mtimes looks
    equivalent but is not on Windows, where a directory's mtime only moves
    when an entry is added or removed directly inside it -- so a rewrite of
    out/score/1/team/1/name.txt (exactly what a leaked board would do) left
    every watched mtime untouched and the guard saw nothing.
    """
    if not directory.exists():
        return None
    snapshot = {}
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in _SKIPPED_SUBTREES]
        for name in files:
            path = Path(root, name)
            try:
                stat = path.stat()
            except OSError:      # vanished mid-walk; nothing this suite wrote
                continue
            snapshot[str(path.relative_to(directory))] = (
                stat.st_size, stat.st_mtime_ns)
    return snapshot


def _fileSnapshot(path: Path):
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def test_the_harness_runs_outside_the_project(autonaApp):
    cwd = Path(os.getcwd()).resolve()

    assert cwd == autonaApp.dir.resolve()
    assert cwd != PROJECT_ROOT
    assert PROJECT_ROOT not in cwd.parents


def test_state_writes_land_in_the_runtime_directory(autonaApp):
    board = autonaApp.board(1)
    board.CommandScoreChange(0, 1)
    autonaApp.settle()

    exported = autonaApp.dir / "out" / "program_state.json"
    assert exported.is_file(), "the harness wrote its state somewhere else"


def test_exercising_the_app_leaves_the_real_out_and_user_data_untouched(autonaApp):
    """The one that would actually have caught a mistake."""
    projectOut = _snapshot(PROJECT_ROOT / "out")
    projectUserData = _snapshot(PROJECT_ROOT / "user_data")
    settings = _fileSnapshot(PROJECT_ROOT / "user_data" / "settings.json")
    programState = _fileSnapshot(PROJECT_ROOT / "out" / "program_state.json")
    logo = _fileSnapshot(PROJECT_ROOT / "layout" / "logo.png")

    # Everything a widget test does, and then some: build all three boards,
    # move scores, edit a player, force export cycles.
    for number in (1, 2, 3):
        autonaApp.board(number)
    board = autonaApp.board(1)
    board.CommandScoreChange(0, 2)
    board.bestOfSpin.setValue(3)
    board.team1playerWidgets[0].SetData(
        {"gamerTag": "ISOLATION"}, dontLoadFromDB=True)
    board.SwapTeams()
    autonaApp.settle()

    assert _snapshot(PROJECT_ROOT / "out") == projectOut
    assert _snapshot(PROJECT_ROOT / "user_data") == projectUserData
    assert _fileSnapshot(PROJECT_ROOT / "user_data" / "settings.json") == settings
    assert _fileSnapshot(PROJECT_ROOT / "out" / "program_state.json") == programState
    # Shipped trees are linked in read-only; layout/ is a real empty dir in
    # the runtime because Window.__init__ seeds layout/logo.png into it.
    assert _fileSnapshot(PROJECT_ROOT / "layout" / "logo.png") == logo


def test_the_working_directory_is_restored_afterwards():
    # No autonaApp fixture here: by the time this runs, any earlier widget
    # test must have put the CWD back.
    #
    # Compared against the directory pytest was launched from, not the
    # project root: those are usually the same, but the harness restores the
    # former, and hard-coding the latter would fail a run started from
    # anywhere else for a reason that has nothing to do with isolation.
    assert Path(os.getcwd()).resolve() == Path(conftest._ORIGINAL_CWD).resolve()


def test_dropped_boards_stop_reacting_to_global_signals(autonaApp):
    """A dropped board must be dead in C++, not merely unreferenced.

    Every board wires lambdas into the process-wide singletons
    (AutonaGameAssetManager.signals.onLoad and friends). A lambda holds no
    weak reference to its receiver, so PySide6 cannot auto-disconnect it --
    only destroying the C++ object does. Without that, a board dropped at the
    end of one test would still answer the next test's emit and rewrite
    score.{n}: invisible state in tests that never built a widget, and writes
    into the developer's real ./out once the CWD is restored.

    A RuntimeError from a lambda whose receiver is already gone is fine and
    expected here; the state staying clean is the contract.
    """
    from src.AutonaGameAssetManager import AutonaGameAssetManager

    autonaApp.board(1)
    autonaApp.board(2)
    autonaApp.board(3)
    autonaApp.dropAllBoards()

    AutonaGameAssetManager.instance.signals.onLoad.emit()
    autonaApp.settle()

    for number in (1, 2, 3):
        assert autonaApp.get(f"score.{number}") is None, (
            f"a dropped board {number} is still writing state")


def test_dropping_a_board_removes_its_exported_files(autonaApp):
    """The drop has to reach ./out, not vanish into the held buffer.

    _dropBoard() clears the held registration *before* it unsets the subtree.
    In the other order the Unset's changed keys are buffered into
    heldKeys["score.1"] and then discarded along with it, so the removal
    reaches neither the delta nor the export: out/score/1/**.txt keeps the
    last values and lastSavedState keeps a score.1 no widget backs.
    """
    board = autonaApp.board(1)
    board.CommandScoreChange(0, 1)
    autonaApp.settle()

    exported = autonaApp.dir / "out" / "score" / "1" / "team" / "1" / "score.txt"
    assert exported.is_file(), "precondition: the score should have exported"

    autonaApp.dropAllBoards()
    autonaApp.settle()

    assert not exported.exists(), "the dropped board's files were left behind"
    assert autonaApp.state.lastSavedState.get("score", {}).get("1") is None


def test_dropping_a_board_leaves_no_held_registration(autonaApp):
    """The harness's held cleanup must keep up with StateManager.

    AutonaScoreboardWidget.__init__ calls HoldSubtree("score.{n}") and there
    is no inverse API, so conftest._dropBoard() clears the registry fields by
    hand. That list is a copy of StateManager's internals: if a new held-
    related field appears (or one is renamed), the cleanup silently stops
    being complete and tests/test_state_manager_held.py starts depending on
    execution order. This asserts the visible half of that contract.
    """
    autonaApp.board(1)
    autonaApp.dropAllBoards()

    state = autonaApp.state
    held = [p for p in state.heldPrefixes if p.startswith("score.")]
    assert held == [], f"held prefixes survived the drop: {held}"
    assert [p for p in state.heldKeys if p.startswith("score.")] == []
    assert [p for p in state.heldExempt if p.startswith("score.")] == []
    assert [p for p in state._commitPrefixes if p.startswith("score.")] == []
    assert [p for p in state._heldTouched if p.startswith("score.")] == []
