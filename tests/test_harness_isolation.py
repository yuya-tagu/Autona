"""The app harness must never touch the real installation.

`autonaApp` boots the actual app: it makes real boards, writes real state
and lets StateManager's export thread write real files. All of that
resolves against the process CWD, so the harness runs inside a throwaway
directory. If that ever slipped, a test run would overwrite the developer's
`out/` (what OBS is reading) or `user_data/settings.json` (their API token,
hotkeys, ports) -- silently, and while they were streaming.

So this is not a test of a feature; it is the guard that makes every other
harness test safe to run.
"""
import os
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

import src.core.StateManager as state_manager
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
    from src.core.Actions import AutonaActions

    autonaApp.ready(1)
    AutonaActions.instance.AdjustScore(1, 1, 1)   # the board's +1 button
    autonaApp.settle()

    exported = autonaApp.dir / "out" / "program_state.json"
    assert exported.is_file(), "the harness wrote its state somewhere else"


def test_exercising_the_app_leaves_the_real_out_and_user_data_untouched(autonaApp):
    """The one that would actually have caught a mistake."""
    from src.core.Actions import AutonaActions
    from tests.conftest import load_player

    projectOut = _snapshot(PROJECT_ROOT / "out")
    projectUserData = _snapshot(PROJECT_ROOT / "user_data")
    settings = _fileSnapshot(PROJECT_ROOT / "user_data" / "settings.json")
    programState = _fileSnapshot(PROJECT_ROOT / "out" / "program_state.json")
    logo = _fileSnapshot(PROJECT_ROOT / "layout" / "logo.png")

    # Everything a board test does, and then some: make all three boards,
    # move scores, load a player, force export cycles.
    autonaApp.readyAll()
    actions = AutonaActions.instance
    actions.AdjustScore(1, 1, 2)
    actions.SetBestOf(1, 3)
    load_player(1, 1, {"gamerTag": "ISOLATION"})
    actions.SwapTeams(1)
    actions.ApplyChanges(1)
    autonaApp.settle()

    assert _snapshot(PROJECT_ROOT / "out") == projectOut
    assert _snapshot(PROJECT_ROOT / "user_data") == projectUserData
    assert _fileSnapshot(PROJECT_ROOT / "user_data" / "settings.json") == settings
    assert _fileSnapshot(PROJECT_ROOT / "out" / "program_state.json") == programState
    # Shipped trees are linked in read-only; layout/ is a real empty dir in
    # the runtime because Window.__init__ seeds layout/logo.png into it.
    assert _fileSnapshot(PROJECT_ROOT / "layout" / "logo.png") == logo


@pytest.fixture
def realTree(tmp_path, monkeypatch):
    """A directory the guard in conftest takes for a real tree, with one file
    in it. What was refused is taken back by the test: its own teardown would
    fail on it otherwise."""
    tree = tmp_path / "real"
    tree.mkdir()
    (tree / "kept.txt").write_text("before", encoding="utf-8")
    monkeypatch.setattr(conftest, "_REAL_TREES",
                        (os.path.normcase(str(tree)) + os.sep,))
    yield tree
    conftest._realWrites.clear()


@pytest.mark.parametrize("write", [
    lambda tree: open(tree / "kept.txt", "w", encoding="utf-8"),
    lambda tree: open(tree / "kept.txt", "a", encoding="utf-8"),
    lambda tree: open(tree / "kept.txt", "r+", encoding="utf-8"),
    lambda tree: open(tree / "new.txt", "xb"),
    lambda tree: os.open(tree / "new.txt", os.O_WRONLY | os.O_CREAT),
    lambda tree: tempfile.mkstemp(dir=tree),
    lambda tree: (tree / "kept.txt").write_text("after", encoding="utf-8"),
    lambda tree: os.remove(tree / "kept.txt"),
    lambda tree: os.rename(tree / "kept.txt", tree / "moved.txt"),
    lambda tree: os.replace(tree / "kept.txt", tree.parent / "out.txt"),
    lambda tree: os.mkdir(tree / "new"),
    lambda tree: os.makedirs(tree / "new" / "deep"),
    lambda tree: shutil.copyfile(tree / "kept.txt", tree / "copy.txt"),
    lambda tree: shutil.copy(tree / "kept.txt", tree / "copy.txt"),
    lambda tree: shutil.copy2(tree / "kept.txt", tree / "copy.txt"),
    lambda tree: Path(tree / "kept.txt").touch(),
    lambda tree: shutil.rmtree(tree),
], ids=["open w", "open a", "open r+", "open x", "os.open", "mkstemp",
        "write_text", "remove", "rename", "replace out", "mkdir", "makedirs",
        "copyfile", "copy", "copy2", "touch", "rmtree"])
def test_a_write_to_the_real_installation_is_refused(realTree, write):
    with pytest.raises(conftest.RealInstallationWrite):
        write(realTree)

    assert sorted(p.name for p in realTree.iterdir()) == ["kept.txt"]
    assert (realTree / "kept.txt").read_text(encoding="utf-8") == "before"
    assert conftest._realWrites, "the refused write was not recorded"


def test_a_relative_write_is_judged_by_the_cwd_it_resolves_against(
        realTree, monkeypatch):
    monkeypatch.chdir(realTree.parent)
    with pytest.raises(conftest.RealInstallationWrite):
        open("real/kept.txt", "w", encoding="utf-8")
    assert conftest._realWrites

    (realTree.parent / "elsewhere").mkdir()
    monkeypatch.chdir(realTree.parent / "elsewhere")
    conftest._realWrites.clear()
    Path("real").mkdir()
    Path("real/kept.txt").write_text("a directory of the same name",
                                     encoding="utf-8")
    assert conftest._realWrites == []


def test_a_write_on_another_thread_is_refused_and_fails_the_running_test(
        realTree):
    """The export writer's case: nothing raises in the test itself."""
    caught = []

    def writer():
        try:
            open(realTree / "kept.txt", "w", encoding="utf-8")
        except OSError as error:
            caught.append(error)

    thread = threading.Thread(target=writer, name="SomeWriter")
    thread.start()
    thread.join()

    assert isinstance(caught[0], conftest.RealInstallationWrite)
    with pytest.raises(pytest.fail.Exception, match="SomeWriter"):
        conftest._failOnRealWrites()
    conftest._failOnRealWrites()      # once: the next test does not fail too


def test_reading_and_what_changes_nothing_pass_the_guard(realTree):
    assert (realTree / "kept.txt").read_text(encoding="utf-8") == "before"
    assert list(realTree.iterdir())
    os.makedirs(realTree, exist_ok=True)      # what the app does at startup
    os.close(os.open(realTree / "kept.txt", os.O_RDONLY))

    assert conftest._realWrites == []


def test_the_guard_watches_the_trees_the_app_writes():
    trees = {os.path.normcase(str(PROJECT_ROOT / name)) + os.sep
             for name in ("out", "user_data", "layout")}
    assert set(conftest._REAL_TREES) == trees


def test_the_persisted_state_store_is_not_the_real_one(autonaApp):
    """user_data/state.json is the store of record (ADR-005 S3b).

    Its path is fixed by the first StateManager.Initialize() of the process
    -- the harness's, or an earlier test module's, always in a throwaway
    directory -- and every later save cycle in the session writes there,
    whatever the CWD is by then. If that first call ever ran in the project
    root, the whole suite would be overwriting the developer's saved state.
    """
    from src.core.StatePersistence import StatePersistence

    autonaApp.state.Set("harness_store_probe.value", "probe")
    autonaApp.settle()
    try:
        store = StatePersistence.Path()
        assert store is not None, "the harness never opened the store"
        store = Path(store).resolve()
        assert store.is_file(), "settle() did not get the store written"
        # Compared with the real store itself, not "anywhere under the
        # project": a --basetemp inside the repo is a throwaway directory too.
        real = (PROJECT_ROOT / "user_data" / "state.json").resolve()
        assert store != real, f"the suite writes the real store: {store}"
        assert store.parent.parent != PROJECT_ROOT.resolve(), (
            f"the store's runtime directory is the project root: {store}")
    finally:
        autonaApp.state.Unset("harness_store_probe")
        autonaApp.settle()


def test_the_working_directory_is_restored_afterwards():
    # No autonaApp fixture here: by the time this runs, any earlier harness
    # test must have put the CWD back.
    #
    # Compared against the directory pytest was launched from, not the
    # project root: those are usually the same, but the harness restores the
    # former, and hard-coding the latter would fail a run started from
    # anywhere else for a reason that has nothing to do with isolation.
    assert Path(os.getcwd()).resolve() == Path(conftest._ORIGINAL_CWD).resolve()


def test_image_downloads_finish_before_the_directory_is_restored(autonaApp,
                                                                  monkeypatch):
    """The other thread that writes ./out, and the one settle() used to miss.

    A state value that is an https .png/.jpg URL of an image host (avatars) makes
    StateManager.CreateFilesDict spawn a daemon thread that downloads it to
    "./out/<path>.png". That path is relative, so the OS resolves it against
    whatever the CWD is *when the write happens* -- not when the thread
    started. A download still in flight when the harness chdir'd back would
    therefore land its image in the developer's real ./out.

    The URL points at a closed port on loopback, made an image host for the
    test: nothing leaves the machine, the request fails immediately, and what
    is under test is that settle() waited for the thread at all -- so the
    assertion checks a download was really started before checking it finished.
    """
    monkeypatch.setattr(state_manager, "EXPORT_IMAGE_HOSTS", {"127.0.0.1:1"})
    projectOut = _snapshot(PROJECT_ROOT / "out")

    autonaApp.state.Set("harness_download_probe.image",
                        "https://127.0.0.1:1/avatar.png")
    autonaApp.settle()

    threads = list(autonaApp.state._download_threads)
    assert threads, "precondition: the export should have started a download"
    assert not any(t.is_alive() for t in threads), (
        "settle() returned with an image download still running")

    autonaApp.state.Unset("harness_download_probe")
    autonaApp.settle()

    assert _snapshot(PROJECT_ROOT / "out") == projectOut


def test_finished_image_downloads_are_not_kept_forever(autonaApp, monkeypatch):
    """The tracking list must not grow for the length of a stream.

    Every avatar and every character-art URL of every set loaded in a session
    goes through it, so a list that only ever appends is a slow leak in the
    app itself, not just in the tests.
    """
    monkeypatch.setattr(state_manager, "EXPORT_IMAGE_HOSTS", {"127.0.0.1:1"})
    for i in range(4):
        autonaApp.state.Set(f"harness_download_probe.image_{i}",
                            f"https://127.0.0.1:1/{i}.png")
        autonaApp.settle()

    assert len(autonaApp.state._download_threads) <= 1

    autonaApp.state.Unset("harness_download_probe")
    autonaApp.settle()


def test_dropped_boards_stop_reacting_to_global_signals(autonaApp):
    """A dropped board must not be written by what follows a game load.

    The game load's wiring (AutonaActions.ApplyGameRoster on the asset
    manager's onLoad, bootstrap.wire_game_roster) writes the boards core has
    made; a board dropped at the end of one test must not answer the next
    test's emit and rewrite score.{n}: invisible state in tests that never
    made a board, and writes into the developer's real ./out once the CWD is
    restored. (The Qt boards wired lambdas of their own into the same
    signal until ADR-005 S4ac-3, which only destroying the C++ object
    disconnected.)
    """
    from src.AutonaGameAssetManager import AutonaGameAssetManager

    autonaApp.readyAll()
    autonaApp.dropAllBoards()

    AutonaGameAssetManager.instance.signals.onLoad.emit()
    autonaApp.settle()

    for number in (1, 2, 3):
        assert autonaApp.get(f"score.{number}") is None, (
            f"a dropped board {number} is still writing state")


def test_dropping_a_board_removes_its_exported_files(autonaApp):
    """The drop has to reach ./out, not vanish into the held buffer.

    _dropBoard() clears the held registration *before* it unsets the
    subtree. In the other order the Unset's changed keys are buffered into
    heldKeys["score.1"] and then discarded along with it, so the removal
    reaches neither the delta nor the export: out/score/1/**.txt keeps the
    last values and lastSavedState keeps a score.1 no board backs.
    """
    from src.core.Actions import AutonaActions

    autonaApp.ready(1)
    AutonaActions.instance.AdjustScore(1, 1, 1)   # the +1
    autonaApp.settle()

    exported = autonaApp.dir / "out" / "score" / "1" / "team" / "1" / "score.txt"
    assert exported.is_file(), "precondition: the score should have exported"

    autonaApp.dropAllBoards()
    autonaApp.settle()

    assert not exported.exists(), "the dropped board's files were left behind"
    assert autonaApp.state.lastSavedState.get("score", {}).get("1") is None


def test_dropping_a_board_leaves_no_held_registration(autonaApp):
    """The harness's held cleanup must keep up with StateManager.

    Making a 1v1 / team board with `panel` registers score.{n} as held
    (AutonaActions.EnsureBoard -> StateSchema.HoldBoard; the widget's
    __init__ until ADR-005 S4y), and its inverse would publish what is
    pending, so conftest._dropBoard() clears the registry fields by hand.
    That list is a copy of StateManager's internals: if a new held-related
    field appears (or one is renamed), the cleanup silently stops being
    complete and tests/test_state_manager_held.py starts depending on
    execution order. This asserts the visible half of that contract.
    """
    autonaApp.ready(1)
    autonaApp.dropAllBoards()

    state = autonaApp.state
    held = [p for p in state.heldPrefixes if p.startswith("score.")]
    assert held == [], f"held prefixes survived the drop: {held}"
    assert [p for p in state.heldKeys if p.startswith("score.")] == []
    assert [p for p in state.heldExempt if p.startswith("score.")] == []
    assert [p for p in state._commitPrefixes if p.startswith("score.")] == []
    assert [p for p in state._heldTouched if p.startswith("score.")] == []
    assert [p for p in state._commitWritten if p.startswith("score.")] == []
    assert [p for p in state._takenLive if p.startswith("score.")] == []


def test_dropping_a_board_leaves_no_partial_commit_behind(autonaApp):
    """The partial commit's ledgers (StateManager.RecordHeldWrites /
    CommitHeld with `written`) are held state too: a board dropped with a
    commit queued, paths taken live or a recording open on it leaves none
    of them for the next test -- a queued commit would keep an ended
    recording, and its next drain would ask about keys of a board that is
    gone."""
    autonaApp.ready(1)
    state = autonaApp.state
    with state.RecordHeldWrites() as written:
        state.Set("score.1.phase", "Loaded")
        with state.lock:
            # As a commit inside an open block leaves them, and a failed
            # publish its paths.
            state._commitWritten.setdefault("score.1", []).append(written)
            state._takenLive["score.1"] = ["score.1.phase"]
            assert "score.1" in written.mine
        autonaApp.dropAllBoards()
        with state.lock:
            assert [p for p in state._commitWritten
                    if p.startswith("score.")] == []
            assert [p for p in state._takenLive
                    if p.startswith("score.")] == []
            assert [p for writes in state._heldRecords
                    for p in (*writes.mine, *writes.others)
                    if p.startswith("score.")] == []
    with state.lock:
        assert state._heldRecords == []


def test_a_readied_board_is_made_as_at_launch(autonaApp):
    """ready(n) / readyAll() make the boards in core as the desktop's launch
    does (AutonaActions.EnsureBoard / MakeBoards with `panel`): made, boards
    1 / 2 held, the crew board not, nothing pending (ADR-005 S4ac-2)."""
    for number in (1, 2, 3):
        autonaApp.ready(number)
    now = autonaApp.boardsNow()
    assert now["made"] == [True, True, True]
    assert now["held"] == ["score.1", "score.2"]
    assert now["pending"] == [False, False, False]

    autonaApp.readyAll()
    assert autonaApp.boardsNow()["made"] == [True, True, True]
    assert autonaApp.boardsNow()["held"] == ["score.1", "score.2"]


def test_a_tree_held_open_for_a_moment_is_still_removed(tmp_path):
    """remove_tree() waits out a handle someone else has open.

    The game trees the fixtures install share one runtime directory, so a
    tree a teardown leaves behind fails the next install of it -- and every
    install after that, far from the test that left it (153 setup errors in
    one full run). On Windows a file another handle has open (an
    anti-virus or indexer scanning what was just copied in) cannot be
    deleted at once; `rmtree(ignore_errors=True)` gave up on it in silence.
    """
    import threading

    tree = tmp_path / "games" / "tgame"
    (tree / "base_files").mkdir(parents=True)
    held = tree / "base_files" / "config.json"
    held.write_text("{}", encoding="utf-8")
    handle = open(held, encoding="utf-8")  # no FILE_SHARE_DELETE on Windows
    threading.Timer(0.3, handle.close).start()
    try:
        conftest.remove_tree(tree)
    finally:
        handle.close()

    assert not tree.exists()


def test_a_tree_that_cannot_be_removed_fails_loudly(tmp_path):
    """Past the timeout remove_tree() raises, naming what is left, in the
    teardown of the test that installed the tree -- not in a later setup."""
    tree = tmp_path / "games" / "tgame"
    tree.mkdir(parents=True)
    held = tree / "config.json"
    held.write_text("{}", encoding="utf-8")
    if os.name != "nt":
        pytest.skip("an open file only blocks its removal on Windows")
    with open(held, encoding="utf-8"):
        with pytest.raises(OSError, match="config.json"):
            conftest.remove_tree(tree, timeout=0.2)
    conftest.remove_tree(tree)


def test_a_copy_that_fails_part_way_leaves_nothing_behind(tmp_path):
    """copy_trees() is all or nothing: a setup that fails does not leave the
    trees it had copied for the next test's setup to trip over (each later
    install of them would fail with FileExistsError)."""
    source = tmp_path / "fixture"
    for name in ("game2", "tgame"):
        (source / name).mkdir(parents=True)
        (source / name / "config.json").write_text("{}", encoding="utf-8")
    games = tmp_path / "games"
    (games / "tgame").mkdir(parents=True)  # what a failed teardown left
    (games / "tgame" / "left.txt").write_text("", encoding="utf-8")

    with pytest.raises(FileExistsError):
        conftest.copy_trees([(source / n, games / n) for n in ("game2", "tgame")])

    assert not (games / "game2").exists(), "the tree copied before the failure was kept"
    assert (games / "tgame" / "left.txt").exists(), "a tree it did not copy was removed"
