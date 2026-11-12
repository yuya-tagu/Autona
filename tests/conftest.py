"""Test infrastructure for Autona.

Since Step 3 of the startup redesign, `src/__init__.py` is empty and no src
module builds anything at import time, so tests import normally
(`from src.X import Y`). The old workaround -- loading a single .py by file
path to dodge the package chain -- is gone with it, and so is the helper
this module used to export for it.

The one thing still worth guarding is import side effects themselves; that
is tests/test_import_side_effects.py.

This module also hosts the app-level harness (`autonaApp`): a real,
bootstrapped singleton layer -- both bootstrap stages, the desktop's Qt
adapters included -- running offscreen inside a throwaway working
directory. See the section at the bottom.
"""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import pytest

# Keep test runs from writing __pycache__/*.pyc for src modules. main.py
# sets this for the app, but pytest imports src files without it and the
# leftover .pyc can mask source edits when Windows mtimes lag
# (agent-pitfalls #12).
sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

# Same binding as the app (main.py), and no window ever reaches a screen.
# Assigned rather than setdefault: PySide6 (LGPL) is a licensing requirement,
# not a preference, and a stray QT_API=pyqt6 in the environment would run the
# whole suite against the GPL binding the app must never ship with.
os.environ["QT_API"] = "pyside6"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ── The real installation is off limits ──────────────────────────────────
#
# The app's mutable paths are relative (./out, ./user_data, ./layout). The OS
# resolves them when the write happens, and the export writer runs on a thread
# of its own, so a test that is back in the project root while that writer is
# busy rewrites the developer's files -- what OBS is reading -- and no test
# fails. tests/test_api_v1_players.py did, on every run, until a snapshot in
# tests/test_harness_isolation.py happened to catch it from another process.
#
# Python's audit hooks see each such write of this process when it is attempted,
# on any thread. It is refused, so nothing is overwritten, and the test running
# then fails. Only this process's writes are seen, so an app the developer has
# open at the same time cannot trip it. Not seen: what a subprocess writes and
# what Qt's C++ writes itself (QFile, QSettings).

_REAL_TREES = tuple(os.path.normcase(str(PROJECT_ROOT / name)) + os.sep
                    for name in ("out", "user_data", "layout"))

# Audit event -> the indexes of its arguments that name a path it changes.
# Opening is told apart by its mode, in _refuseRealWrites.
_WRITE_EVENTS = {
    "_winapi.CopyFile2": (1,),   # shutil.copy2 on Windows: no open event
    "os.chmod": (0,),
    "os.link": (1,),
    "os.mkdir": (0,),
    "os.remove": (0,),
    "os.rename": (0, 1),
    "os.rmdir": (0,),
    "os.symlink": (1,),
    "os.truncate": (0,),
    "os.utime": (0,),
}
_OPEN_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

_realWrites = []


class RealInstallationWrite(OSError):
    """A write _refuseRealWrites refused. Not a PermissionError: on Windows
    tempfile.mkstemp takes that for a name clash and tries again, for good."""


def _inRealTree(path):
    """`path` made absolute against the CWD now, if it is in a real tree."""
    if isinstance(path, int):       # a file descriptor
        return None
    try:
        absolute = os.path.normcase(os.path.abspath(os.fsdecode(path)))
    except (TypeError, ValueError, OSError):
        return None
    return absolute if (absolute + os.sep).startswith(_REAL_TREES) else None


def _refuseRealWrites(event, args):
    """The audit hook: runs for every audited event of the process, so it
    looks at the event's name first and keeps to the stdlib's plain calls."""
    if event == "open":
        path, mode, flags = args
        if isinstance(mode, str):
            if not set(mode) & set("wax+"):
                return
        elif not flags & _OPEN_WRITE_FLAGS:
            return
        paths = (path,)
    elif event in _WRITE_EVENTS:
        paths = [args[index] for index in _WRITE_EVENTS[event]]
    else:
        return
    for path in paths:
        real = _inRealTree(path)
        # makedirs(exist_ok=True) asks for what is there: nothing changes.
        if real is None or (event == "os.mkdir" and os.path.isdir(real)):
            continue
        thread = threading.current_thread().name
        _realWrites.append(
            (f"{event} {real} on {thread}, CWD {os.getcwd()}",
             "".join(traceback.format_stack(limit=12))))
        raise RealInstallationWrite(f"a test may not write {real}")


sys.addaudithook(_refuseRealWrites)


def _failOnRealWrites():
    """Fail the running test if a write was refused since the last look. The
    writer may be a thread an earlier test left running: the stack tells."""
    writes = _realWrites[:]
    if not writes:
        return
    del _realWrites[:len(writes)]
    attempts = "\n".join(attempt for attempt, _stack in writes[:10])
    pytest.fail(
        f"{len(writes)} write(s) to the real installation were refused:\n"
        f"{attempts}\nThe first one came from:\n{writes[0][1]}"
        "Run the test in a throwaway directory, and wait for what writes "
        "there before leaving it (see tests/test_harness_isolation.py).",
        pytrace=False)


@pytest.fixture(scope="session", autouse=True)
def _noRealWritesInTheSession():
    """What comes after the last test's teardown: the session's own teardown
    and an export writer still running. Fails that test's teardown."""
    yield
    module = sys.modules.get("src.core.StateManager")
    if module is not None:
        module.StateManager.JoinExports(30)
    _failOnRealWrites()


@pytest.fixture(autouse=True)
def _noRealWrites():
    yield
    _failOnRealWrites()


@pytest.fixture(scope="session")
def qapp():
    """The one Qt application object for the whole test session.

    Qt allows exactly one per process, and a QWidget needs it to be a
    QApplication -- a bare QCoreApplication built by whichever Qt test ran
    first would take every later widget test down with it. So all of them
    share this fixture.
    """
    from qtpy.QtWidgets import QApplication  # local: non-Qt tests skip the cost
    return QApplication.instance() or QApplication([])


class BoardLockRecord:
    """Whether the calling thread is inside a board operation -- the `op`
    a BoardHost runs under its lock -- as the `boardLock` fixture records
    it."""

    def __init__(self):
        self._local = threading.local()

    def held(self):
        return getattr(self._local, "depth", 0) > 0


@pytest.fixture
def boardLock(monkeypatch):
    """Every BoardHost's Run, recording which thread is inside its `op`
    (ADR-005 S4ad review): `boardLock.held()` says whether the caller is
    under a board host's lock. Recorded by wrapping Run, not by asking the
    RLock (`_is_owned()` is private), so a write that skips the host reads
    False. Patched on the class, so it records for the host a test swaps in
    too (the headless fixtures' own BoardHost()) -- and on the shared host
    when an earlier test's monkeypatch of its Run left one there (undoing a
    patch of an instance's method sets the bound method back on the
    instance, where it hides the class's)."""
    from src import AutonaWebOperations
    from src.AutonaWebOperations import BoardHost

    record = BoardLockRecord()

    def recording(callRun):
        def run(op, *args, **kwargs):
            def inside():
                local = record._local
                local.depth = getattr(local, "depth", 0) + 1
                try:
                    return op()
                finally:
                    local.depth -= 1
            return callRun(inside, *args, **kwargs)
        return run

    realRun = BoardHost.Run
    host = AutonaWebOperations.boardHost
    if "Run" in vars(host):
        monkeypatch.setattr(host, "Run", recording(vars(host)["Run"]))
    monkeypatch.setattr(
        BoardHost, "Run",
        lambda self, op, *args, **kwargs: recording(
            lambda *a, **k: realRun(self, *a, **k))(op, *args, **kwargs))
    return record


TREE_REMOVAL_TIMEOUT_SECS = 10


def remove_tree(path, timeout=TREE_REMOVAL_TIMEOUT_SECS):
    """Remove the directory tree `path` and return once it is gone.

    For what the tests install into the session's shared runtime
    (user_data/games/<game>): the next test that installs the same tree
    copies it to the same place, so a removal that leaves anything behind
    fails every later setup, far from the test that left it. On Windows a
    file another handle has open -- an anti-virus or indexer scan of the
    files the test has just copied in -- cannot go at once (a sharing
    violation, or only marked for deletion while its directories stay),
    which `rmtree(ignore_errors=True)` used to leave behind in silence.
    Tried again until the tree is gone; after `timeout` seconds this
    raises, naming what is left, in the teardown of the test that
    installed it.
    """
    import time

    path = Path(path)
    deadline = time.monotonic() + timeout
    while True:
        error = None
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass
        except OSError as caught:
            error = caught
        if not os.path.lexists(path):
            return
        if time.monotonic() >= deadline:
            left = sorted(str(p.relative_to(path)) for p in path.rglob("*"))
            raise OSError(
                f"{path} is still there after {timeout} s "
                f"(last error: {error!r}); left: {left[:10]}") from error
        time.sleep(0.05)


def copy_trees(pairs):
    """Copy each (source, destination) directory tree; the destinations.

    All or nothing: when one copy fails (its destination is already
    there, say), the trees copied so far -- and the one that failed part
    way -- are removed before the error goes on, so a fixture whose setup
    fails does not leave them for the next test's setup to trip over.
    """
    copied = []
    try:
        for source, destination in pairs:
            existed = os.path.lexists(destination)
            try:
                shutil.copytree(source, destination)
            except BaseException:
                if not existed:
                    copied.append(destination)
                raise
            copied.append(destination)
    except BaseException:
        for destination in reversed(copied):
            remove_tree(destination)
        raise
    return [destination for _source, destination in pairs]


TASKS_TIMEOUT_SECS = 30


def drain_tasks(timeout=TASKS_TIMEOUT_SECS):
    """Run the TaskExecutor's work, and what it hands to the GUI thread, to
    the end -- in place of the QThreadPool.waitForDone() + processEvents()
    the asset tests used until ADR-005 S3f-4.

    Pumps this (the GUI) thread while it waits: a game load job marshals its
    models onto it (run_sync), so a plain drain() would wait for the job
    that waits for us. Done once the executor is idle after the GUI thread
    has run what the tasks queued there (a decoded icon, the scan's
    onLoadAssets) -- which can submit more (the restore's load), so it
    looks again. Test harness only: src never pumps the event loop
    (agent-pitfalls #9).

    Everything means everything: a stepped executor's queued jobs run too,
    still one at a time -- a step that is waiting for this thread (its
    run_next() timed out after the short slice below) is waited for, with
    the events processed in between, before the next one starts
    (TaskExecutor.run_next). A test stepping its jobs by hand waits for icon
    decodes with deliver_decodes() instead.
    """
    import time

    from qtpy.QtWidgets import QApplication
    from src.core.TaskExecutor import TaskExecutor

    executor = TaskExecutor.instance
    deadline = time.monotonic() + timeout
    while True:
        QApplication.processEvents()
        if executor.drain(0.005):
            QApplication.processEvents()
            if executor.drain(0):
                return
        assert time.monotonic() < deadline, \
            "the background tasks did not finish"


def deliver_decodes(decodes, timeout=TASKS_TIMEOUT_SECS):
    """Wait for icon decodes -- the Futures a model rebuild of the asset
    adapter returned -- then run what they handed to the GUI thread (the
    icons set on their items). Nothing else the executor runs is touched,
    so it suits a test whose jobs are stepped by hand."""
    from concurrent.futures import CancelledError

    from qtpy.QtWidgets import QApplication

    for future in decodes:
        # TimeoutError fails the test; a decode's own error was logged.
        with contextlib.suppress(CancelledError):
            future.exception(timeout)
    QApplication.processEvents()


@pytest.fixture(autouse=True)
def _freshStatistics():
    """Every test starts with the statistics' class state empty: the request
    numbers and the queued team refreshes (StatsService, ADR-005 S3e-5) are
    process-wide, and a refresh left queued by one test -- its Later never
    delivered -- would swallow the next one's on that board. The fetcher
    bootstrap registered stays. The numbers come from one counter, so an
    answer still in flight from an earlier test never passes for a new
    request's. A run that never imported the service (the Web UI job's
    contract tests, without the app's requirements) has nothing to reset."""
    module = sys.modules.get("src.core.StatsService")
    if module is None:
        yield
        return
    StatsService = module.StatsService

    def reset():
        with StatsService._queueLock:
            StatsService._queuedTeams.clear()
        StatsService._generations.clear()

    reset()
    yield
    reset()


@pytest.fixture(autouse=True)
def _freshSettingsRecord():
    """Every test starts with nothing SettingsManager keeps about a
    settings.json it could not read: the changes made in memory only while
    it could not be opened (put back over the file when a later write reads
    it) and where a corrupt one was moved aside are process-wide. A test
    that left a change recorded -- a Set while `loadFailed`, which it
    monkeypatched -- would have it put back over the next test's file. A
    run that never imported the module has nothing to forget."""
    module = sys.modules.get("src.core.SettingsManager")
    if module is None:
        yield
        return
    SettingsManager = module.SettingsManager

    def reset():
        SettingsManager._unsaved = []
        SettingsManager.movedAside = None

    reset()
    yield
    reset()


@pytest.fixture(autouse=True)
def _freshSetFollow():
    """Every test starts with no set loaded, followed or polled on any
    board: what the set follow keeps (SetFollowService, ADR-005 S3e-7) and
    the set each board loaded last (SetLoadService) are process-wide, and
    were a built board's own until then. A poll timer left running by one
    test is stopped. The fetcher bootstrap registered stays. A run that never
    imported the service has nothing to forget."""
    module = sys.modules.get("src.core.SetFollowService")
    if module is None:
        yield
        return
    SetFollowService = module.SetFollowService

    SetFollowService.ForgetBoard()
    yield
    SetFollowService.ForgetBoard()


@contextlib.contextmanager
def tournamentLoaded():
    """A tournament loaded that answers nothing: a set load goes through --
    the board's clears, the set recorded as followed, the poll -- and its
    set's fetch is in flight for good (the other fetches start nothing). A
    set loads only with a tournament (ADR-005 S3e-7 review: with none,
    nothing could be fetched and nothing is cleared), and a set whose fetch
    could not start is not recorded as loaded (S3e-7 re-review); the board
    tests that load a set without the fake provider
    (tests/test_set_follow_flow.py) load it under this."""
    from unittest.mock import patch
    from src.core.SetFollowService import SetFollowService

    fetcher = SetFollowService.fetcher
    with patch.object(fetcher, "Available", return_value=True), \
            patch.object(fetcher, "Match", return_value=True):
        yield


def apply_set(board, data):
    """Apply set payload `data` to board number `board` as the app applies
    one: boards 1/2 through core's
    SetFollowService.ApplySetPayload, one save cycle, the record a
    one-player payload's `savePlayerToDb` asks for saved once the cycle is
    over (SaveRecord); the crew board through CrewBattleService.ApplySetData.
    What the boards' own ChangeSetData did, which no caller in the app had
    (the fetched sets, POST /score and the Web's set fields go to core) and
    ADR-005 S4x removed."""
    from src.core.Actions import AutonaActions
    from src.core.CrewBattleService import CrewBattleService
    from src.core.SetFollowService import SaveRecord, SetFollowService

    if board == AutonaActions.CREW_BOARD:
        CrewBattleService(board).ApplySetData(data)
    else:
        SaveRecord(SetFollowService(board).ApplySetPayload(data))


def load_player(board, team, data, slot=1):
    """Load player `data` into slot `slot` of state team `team` on board
    number `board` (1/2), without the player DB: core's
    PlayerService.ApplyData, what the Qt card's SetData(data,
    dontLoadFromDB=True) ran before it rendered (ADR-005 S3e-3; the cards
    went with the Qt SCORE in S4ac-3)."""
    from src.core.PlayerService import PlayerService

    path = f"score.{board}.team.{team}.player.{slot}"
    return PlayerService(path).ApplyData(data, loadFromDB=False)


def edit_tag(path, text):
    """The tag of the player at `path` edited and finished, as the Qt card's
    tag field ran it on editingFinished until ADR-005 S4ac-3, in its save
    cycles: the text (_OnTextChanged), then the display name, the images and
    the id emptied, which asks for the statistics (NameChanged). v1's player
    edit (PlayerService.Edit) makes the same writes and asks for the same
    statistics in one save cycle, so the recordings made through the card
    (tests/test_stats_flow.py, tests/test_board_operations.py) replay this."""
    from src.core.PlayerService import PlayerService
    from src.core.StateManager import StateManager

    player = PlayerService(path)
    if StateManager.Get(player.Key("name")) != text:
        player.SetText("name", text)
    sponsor = StateManager.Get(player.Key("team")) or ""
    player.PublishMergedName(sponsor, text)
    player.PublishImages(sponsor, text)
    player.SetPlayerId(None, announce=True)


def clear_players(board, no_mains=False):
    """Empty every player of board number `board` (1/2) as the app does:
    the board host's ClearPlayers -- core's ClearPlayers, the set loaded
    last forgotten. The board's own CommandClearAll until ADR-005 S4x moved
    it to the host (the Web's reset_players / clear_all were its only
    callers)."""
    from src import AutonaWebOperations as WebOps

    WebOps.boardHost.ClearPlayers(board, no_mains)


# ── App-level harness (L2) ───────────────────────────────────────────────
#
# Everything below builds a real, bootstrapped Autona -- the same singletons
# main.py gets, both bootstrap stages -- offscreen, so the boards, the routes
# and core can be driven from tests instead of by hand. See
# docs/changes/test-automation-strategy.md §4 for why this layer exists. It
# built the Qt SCORE's board widgets too until ADR-005 S4ac-3 removed them.
#
# Two rules keep it honest:
#
# 1. It never touches the real installation. The app resolves its mutable
#    paths (./out, ./user_data, tmp/) against the process CWD, so the harness
#    runs inside a throwaway directory and links in only the read-only data
#    the app loads (assets/, the i18n tables, the GraphQL query texts).
#    tests/test_harness_isolation.py asserts that.
# 2. Nothing here is a stub: the singletons are the production ones; a test
#    that passes because the harness faked something is worthless.

_ORIGINAL_CWD = os.getcwd()

# Read-only trees the app loads, linked rather than copied: assets/ alone
# is ~110 MB (the country database and the bundled fonts). Nothing in src/
# writes to any of them -- the one runtime write into a shipped tree,
# layout/logo.png, comes from Autona.Window.__init__, which this layer does
# not build. layout/ therefore gets a real (empty) directory below.
_LINKED_TREES = [
    "assets",
    "src/i18n",                       # mapping.json for AutonaLocaleHelper
    "src/TournamentDataProvider",     # StartGG*Query.txt
]

# Created empty in the runtime directory. `out` and `user_data` are the app's
# own writable roots; `tmp` is where AutonaResolve() puts render scratch.
_RUNTIME_DIRS = ["out", "user_data", "user_data/games", "layout", "tmp"]

# The synthetic game loadCharacters() installs into the runtime
# user_data/games (and dropAllBoards() removes again).
HARNESS_GAME = "harness_game"
HARNESS_GAME_NAME = "Harness Game"


def _link_tree(link: Path, target: Path):
    """Point `link` at `target` without copying or needing admin rights.

    A directory junction is used on Windows: os.symlink() there requires
    either Developer Mode or elevation, which a test run cannot assume.
    """
    if sys.platform == "win32":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"could not link {link} -> {target}: {result.stdout}{result.stderr}")
    else:
        os.symlink(target, link, target_is_directory=True)


class _AutonaHarness:
    """Handle on the bootstrapped app, handed to tests as `autonaApp`."""

    def __init__(self, directory: Path):
        self.dir = directory
        # True while HARNESS_GAME is installed and loaded (loadCharacters);
        # dropAllBoards() uninstalls it.
        self._gameInstalled = False

    # -- state ------------------------------------------------------------

    @property
    def state(self):
        from src.core.StateManager import StateManager
        return StateManager

    def get(self, keypath, default=None):
        from src.core.StateManager import StateManager
        return StateManager.Get(keypath, default)

    def boardsNow(self):
        """The boards' life as core and the state have it now, under one
        hold of the state lock: which boards are made (BoardEnsured), the
        held `score.*` prefixes, which of the three have edits pending, and
        the `score` subtree live and as published. Copies."""
        import copy
        from src.core.Actions import AutonaActions
        from src.core.StateManager import StateManager
        with StateManager.lock:
            return {
                "made": [AutonaActions.instance.BoardEnsured(number)
                         for number in (1, 2, 3)],
                "held": sorted(prefix for prefix in StateManager.heldPrefixes
                               if prefix.startswith("score.")),
                "pending": [StateManager.HasPendingChanges(f"score.{number}")
                            for number in (1, 2, 3)],
                "heldKeys": {prefix: list(keys) for prefix, keys
                             in StateManager.heldKeys.items() if keys},
                "live": copy.deepcopy(StateManager.state.get("score")),
                "published": copy.deepcopy(
                    StateManager.lastSavedState.get("score")),
            }

    # -- boards (core) ----------------------------------------------------

    def ready(self, number=1):
        """Board `number` (1 = 1v1, 2 = team, 3 = crew) made afresh in core,
        as the desktop makes it at launch: its launch write, and for boards
        1 / 2 the display options, the polling off and the 反映 hold
        (AutonaActions.EnsureBoard with `panel`; the crew board is never
        held). The number.

        Made afresh rather than reset, so no test can be handed a board
        carrying another test's inputs -- and so a reset path being broken
        (which is exactly what several tests check) cannot silently become
        the thing that isolates them. Only this board, while the desktop has
        all three from launch (readyAll()).
        """
        from src.core.Actions import AutonaActions

        self._loadCountries()
        self._dropBoard(number)
        assert AutonaActions.instance.EnsureBoard(number, panel=True), \
            f"board {number} was not made"
        self.settle()
        return number

    def readyAll(self):
        """Every board made afresh in core as the desktop's launch makes them
        (AutonaActions.MakeBoards with `panel`, bootstrap_gui(); ADR-005
        S4ac-1): one save cycle, the `score` subtree dropped first, boards 1
        / 2 held, the crew board not."""
        from src.core.Actions import AutonaActions

        self._loadCountries()
        self._dropBoards()
        assert AutonaActions.instance.MakeBoards(panel=True) is True
        self.settle()

    def _loadCountries(self):
        """The country data, which the window loads at launch before any
        board is used (Window.__init__: AutonaCountryHelper.init) and the
        harness, which builds no window, loads with the first board readied
        here, so a player load resolves its country and region as in the
        app. Kept loaded once loaded."""
        from src.Helpers.AutonaCountryHelper import AutonaCountryHelper
        from src.core.CountryCatalog import CountryCatalog
        if not CountryCatalog.instance.IsLoaded():
            AutonaCountryHelper.LoadCountries()

    def _dropBoard(self, number):
        """Drop board `number`: what core keeps of it forgotten, its 反映
        hold cleared, and its `score.{n}` subtree unset when it was made or
        held. Its widgets went first until ADR-005 S4ac-3 removed them."""
        from src.core.Actions import AutonaActions
        from src.core.SetFollowService import SetFollowService

        # The set the board loaded and what it follows are core's since
        # ADR-005 S3e-7 (they were the board's, and went with it).
        SetFollowService.ForgetBoard(number)
        # So are the characters per player a board with no character slot
        # gives its next players (ADR-005 S4x: the board's hidden spinbox and
        # the headless host's memory until then, both of which a new board or
        # host started from the default).
        AutonaActions.instance._emptyCharacters.pop(number, None)
        # And whether the board is made (ADR-005 S4y: AutonaActions.
        # EnsureBoard, which ready() runs and which seeds the board only the
        # first time). Forgotten, so the next one seeds it
        # afresh. Not ForgetBoard: its release would publish what is pending
        # (below).
        with self.state.lock:
            made = number in AutonaActions.instance._ensuredBoards
            AutonaActions.instance._ensuredBoards.discard(number)

        # Making a 1v1 / team board with `panel` registers its subtree as
        # held (the 反映 button; AutonaActions.EnsureBoard since ADR-005 S4y,
        # the widget's __init__ until then). Its inverse, StateManager.
        # ReleaseSubtree, would publish what is pending; a test's leftovers
        # are thrown away instead, so the registration is cleared here
        # directly -- leaving it behind would make every later write under
        # score.{n} invisible to a test that never made a board, and would
        # hand tests/test_state_manager_held.py a pre-registered prefix.
        #
        # This has to happen BEFORE the Unset below. While the prefix is still
        # held, the Unset's changed keys are buffered into heldKeys[prefix] and
        # then thrown away with it, so the board's removal would reach neither
        # the delta nor ./out -- leaving stale score/{n}/*.txt behind and a
        # stale score.{n} in lastSavedState. Held state is read under the lock
        # by SaveState, so take it here too.
        #
        # A prefix can be held with no board to drop: a module that drives
        # StateManager on its own (tests/test_state_manager_held.py) leaves its
        # registration and its published score.{n} behind. Clear those as well.
        # A board made on them would have its seed held back, not published,
        # since AutonaActions.SeedFreshBoard relies on running before the hold.
        # A board that was not made or held keeps what a test wrote there (a
        # score hotkey with no board writes score.1 on its own).
        prefix = f"score.{number}"
        with self.state.lock:
            leftover = prefix in self.state.heldPrefixes
            self.state.heldPrefixes.discard(prefix)
            self.state.heldExempt.pop(prefix, None)
            self.state.heldKeys.pop(prefix, None)
            self.state._commitPrefixes.discard(prefix)
            self.state._heldTouched.discard(prefix)
            self.state._heldCommitted.discard(prefix)
        if not (made or leftover):
            return

        self.state.Unset(prefix)

        self.settle()

    def _dropBoards(self):
        from src.core.Actions import AutonaActions
        for number in AutonaActions.BOARDS:
            self._dropBoard(number)

    # -- game -------------------------------------------------------------

    def loadCharacters(self, names=("Mario", "Luigi", "Peach"), skins=3):
        """Install and load a small synthetic game, HARNESS_GAME.

        The harness has no game installed (its user_data/games is empty), so
        AutonaGameAssetManager.characterModel has zero rows: every character
        combo can only ever sit at index -1, and any assertion about *which*
        character a slot ended up on is vacuous. This writes a real game pack
        -- one stock-icon PNG per character and skin -- into the runtime
        user_data/games and loads it through the production path: the
        catalog's scan and load, then the Qt adapter's model rebuild.

        What it leaves out is the onLoad broadcast that ends a real load
        (publishGameLoad() is that end), so nothing connected to it reacts.

        The icon decodes the rebuild starts are waited out here: they must
        not outlive the CWD.
        """
        from PIL import Image

        from src.AutonaGameAssetManager import AutonaGameAssetManager
        from src.core.GameAssetCatalog import GameAssetCatalog

        game = self.dir / "user_data" / "games" / HARNESS_GAME
        remove_tree(game)
        icons = game / "base_files" / "icon"
        icons.mkdir(parents=True)
        (game / "base_files" / "config.json").write_text(json.dumps({
            "name": HARNESS_GAME_NAME,
            "character_to_codename": {
                name: {"codename": name.lower()} for name in names},
        }), encoding="utf-8")
        (icons / "config.json").write_text(json.dumps(
            {"type": ["icon"], "prefix": "", "postfix": "_"}),
            encoding="utf-8")
        for name in names:
            for skin in range(skins):
                Image.new("RGBA", (8, 8), (200, 30, 30, 255)).save(
                    icons / f"{name.lower()}_{skin}.png")

        catalog = GameAssetCatalog.instance
        catalog.ScanGames()
        catalog.SelectGame(HARNESS_GAME, reload=True)
        manager = AutonaGameAssetManager.instance
        deliver_decodes(manager._RebuildModels())
        self._gameInstalled = True
        return manager

    def publishGameLoad(self):
        """The GUI thread's end of a game load, on the game loaded now:
        AutonaGameAssetManager._Publish, with no loader thread -- the load's
        save cycle opened here, as the loader opens it. Under the board
        host's lock, the models rebuilt, gameLoaded (the shared wiring's
        roster, set length and commit, queued in that cycle), onLoad (every
        slot connected to it, in its real place) and the cycle closed, as in
        the app.
        """
        from src.AutonaGameAssetManager import (AutonaGameAssetManager,
                                                _SaveCycle)
        from src.core.GameAssetCatalog import GameAssetCatalog

        cycle = _SaveCycle()
        cycle.Open()
        decodes = []
        AutonaGameAssetManager.instance._Publish(
            GameAssetCatalog.instance.Current(), cycle, decodes)
        deliver_decodes(decodes)
        self.settle()

    def _restoreAssets(self):
        """Uninstall HARNESS_GAME and go back to the no-game models.

        The icon decodes of loadCharacters' rebuild were waited for there,
        and a game load a test ran in between is waited for by the test
        (drain_tasks): the decodes run on the task executor, which may be a
        test's stepped one here, whose queued jobs must stay queued."""
        if not self._gameInstalled:
            return
        from qtpy.QtWidgets import QApplication

        from src.AutonaGameAssetManager import AutonaGameAssetManager
        from src.core.GameAssetCatalog import GameAssetCatalog

        manager = AutonaGameAssetManager.instance
        QApplication.processEvents()
        remove_tree(self.dir / "user_data" / "games" / HARNESS_GAME)
        catalog = GameAssetCatalog.instance
        catalog.ScanGames()
        # The empty snapshot rebuilds the empty models of a fresh start; the
        # synthetic generation is retired, not dropped (agent-pitfalls #13).
        catalog.ResetGameState()
        manager._RebuildModels()
        self._gameInstalled = False

    def dropAllBoards(self):
        """Every board dropped (what core keeps of them), and HARNESS_GAME
        uninstalled."""
        self._dropBoards()
        self._restoreAssets()

    # -- web layer --------------------------------------------------------

    def web(self):
        """The web operations every route reaches (src/AutonaWebOperations
        .py, ADR-005 S3c; all of them since S3e-9). Here they run through
        what bootstrap_gui() registered -- the shell operations; the board
        operations through the web layer's one board host (ADR-005 S4ad) --
        as in the app. A module: web().swap_teams(1).
        """
        from src import AutonaWebOperations
        return AutonaWebOperations

    # -- scheduling -------------------------------------------------------

    def settle(self):
        """Let queued Qt work, the export thread and image downloads finish.

        StateManager hands file export to a background thread; without this a
        test could read ./out before it was written, or leave the thread
        running past the CWD being restored.

        Joining once is not enough: draining the Qt queue can itself run a
        slot that calls SaveState, which starts a *new* export thread after
        the join. So this loops until a pass leaves the same thread (or none)
        behind.

        The persisted store (user_data/state.json, ADR-005 S3b) has a writer
        of its own that waits for the state to be quiet for a second. It is
        flushed too, so no write is left in flight when a test ends and a
        second process started in the same directory
        (tests/live/restart_probe.py) reads a current store. Its path is
        fixed when StateManager.Initialize() runs, so unlike ./out it cannot
        follow a CWD change.

        The image downloads come last because the export thread is what
        spawns them (CreateFilesDict, for any state value that is an http
        .png/.jpg URL -- avatars and character art). They write
        "./out/<path>.png", resolved against the CWD *at write time*, so a
        thread still running when the harness restores the directory would
        drop its image into the developer's real ./out.
        """
        from qtpy.QtWidgets import QApplication
        from src.core.StateManager import StateManager
        from src.core.StatePersistence import StatePersistence

        previous = object()
        while True:
            QApplication.processEvents()

            thread = StateManager._export_thread
            if thread is None or thread is previous:
                break
            thread.join()
            previous = thread

        assert StatePersistence.Flush(30), "state.json writer did not finish"

        StateManager.JoinDownloads()


@pytest.fixture(scope="session")
def _autonaRuntime(qapp, tmp_path_factory):
    """Bootstrap the singleton layer once, in a throwaway directory."""
    from src.Helpers import AutonaDirHelper

    directory = tmp_path_factory.mktemp("autona_runtime")

    for relative in _LINKED_TREES:
        link = directory / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        _link_tree(link, PROJECT_ROOT / relative)
    for relative in _RUNTIME_DIRS:
        (directory / relative).mkdir(parents=True, exist_ok=True)

    previousDir = AutonaDirHelper.current_dir
    os.chdir(directory)
    # AutonaResolve() resolves against the CWD captured when its module was
    # first imported -- which, in a pytest run, is the project root. Repoint
    # it explicitly so the shipped data is read through the links above and
    # scratch output (AutonaResolve("tmp")) stays inside the runtime dir,
    # whatever import order the session happened to have.
    AutonaDirHelper.current_dir = directory

    try:
        from src.Helpers import AutonaQtHelper
        from src.bootstrap import bootstrap_core, bootstrap_gui

        # Both stages, as src/Autona.py runs them: the desktop's Qt adapters
        # (the shell operations, the hotkeys, the models) are what several
        # tests drive.
        bootstrap_core()
        bootstrap_gui()
        harness = _AutonaHarness(directory)
        # The boards bootstrap_gui() made (AutonaActions.MakeBoards, ADR-005
        # S4ac-1: all three, boards 1 / 2 held), as it left them, for
        # tests/test_headless_seed.py. They are dropped below.
        harness.launch = harness.boardsNow()
        # Normally done by src/Autona.py at import time. The @gui_thread_sync
        # operations (the shell operations) and the game load job's model
        # marshal raise without it, and that module is
        # the one thing tests must not import (agent-pitfalls #1).
        if AutonaQtHelper.gui_executor is None:
            AutonaQtHelper.init_gui_executor()

        # bootstrap_core() deliberately stops short of the per-run locale
        # (that is the shell's: Window.__init__ or src/headless.py), so the
        # harness does these two as well: without them
        # AutonaLocaleHelper.matchNames is empty, and the best_of handler --
        # which formats "Best of {}" out of it -- raises halfway through,
        # leaving first_to and friends unwritten. That is a harness artifact,
        # not app behaviour, and it must not look like one.
        from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
        AutonaLocaleHelper.LoadLocale()
        AutonaLocaleHelper.LoadRoundNames()

        # NOTE ON HOTKEYS: Win32 RegisterHotKey grabs a combination
        # system-wide, so nothing here may register one on the developer's
        # machine. AutonaHotkeys.instance exists after bootstrap; UiMounted()
        # (which only Window.__init__ calls, and this layer never builds a
        # Window) does not run, but since ADR-005 S4ae a save through the
        # settings page (core's HotkeyRules.Update -- a v1 PATCH in a test)
        # reaches ReloadHotkeys through StateManagerBridge.hotkeys_changed
        # and would register. So the session's object is held in the
        # settings window's own guard: while a capture is suspended,
        # SetupHotkeys() loads the keys and registers nothing (and installs
        # no native filter). A test of the registration builds its own
        # AutonaHotkeys over a stubbed user32 (tests/test_hotkeys_*.py), or
        # lifts the guard with monkeypatch over one
        # (tests/test_api_v1_settings.py). It is deliberately NOT forced off
        # through SettingsManager -- SettingsManager is process-global, and
        # pinning hotkeys.* here would silently change what
        # tests/test_hotkeys_*.py observe.
        from src.AutonaHotkeys import AutonaHotkeys
        AutonaHotkeys.instance.SuspendCapture()

        # Every test starts with no board made, none held and no `score`
        # subtree -- what each test's teardown leaves (dropAllBoards), and
        # what the bootstrap left before S4ac-1. Left in place, the launch's
        # boards would meet only whichever test a session happens to run
        # first. A test makes the boards it needs: ready(n), or all three as
        # the launch does, readyAll() (ADR-005 S4ac-2).
        harness.dropAllBoards()
        from src.core.StateManager import StateManager
        StateManager.Unset("score")
        harness.settle()

        yield harness
    finally:
        # Belt and braces: every autonaApp teardown already settles, but the
        # CWD must not be restored while an image download is still resolving
        # its relative output path.
        from src.core.StateManager import StateManager
        StateManager.JoinDownloads()

        AutonaDirHelper.current_dir = previousDir
        os.chdir(_ORIGINAL_CWD)
        # Break the junctions ourselves rather than leaving them for
        # tmp_path_factory's 3-generation cleanup: shutil.rmtree only learned
        # to stop at a junction in Python 3.12 (os.path.isjunction), so on
        # 3.11 and below it would descend through these links and delete the
        # contents of the real assets/ and src/ trees. os.rmdir() removes the
        # link itself and never follows it, on every version.
        for relative in reversed(_LINKED_TREES):
            link = directory / relative
            try:
                # POSIX symlinks are unlinked; a Windows junction is a real
                # directory entry and needs rmdir.
                if link.is_symlink():
                    os.unlink(link)
                else:
                    os.rmdir(link)
            except OSError:
                pass


@pytest.fixture
def autonaApp(_autonaRuntime):
    """Run one test inside the bootstrapped app's working directory.

    Two things are undone afterwards, both because this suite shares one
    process with tests that assume plain module state:

    - the CWD, because the app writes ./out and ./user_data relative to it,
      and neither the rest of the suite nor the developer's real
      installation may be touched;
    - the boards: core's memories of each board (_dropBoard: whether it is
      made, its hold, its subtree, the set it loaded, the characters per
      player of an empty roster) and the tournament's names the match bar
      offers (MatchNameCandidates, ADR-005 S4x: they were the boards' until
      then, and went with them), so no test inherits another's. Until
      ADR-005 S4ac-3 the board widgets went too, which stayed connected to
      StateManagerBridge.
    """
    os.chdir(_autonaRuntime.dir)
    try:
        yield _autonaRuntime
    finally:
        _autonaRuntime.dropAllBoards()
        from src.core.MatchNameCandidates import MatchNameCandidates
        if MatchNameCandidates.instance is not None:
            # What the provider's unload hands it (UnloadTournament).
            MatchNameCandidates.instance.FollowTournamentData({})
            MatchNameCandidates.instance.FollowPhases([])
        _autonaRuntime.settle()
        os.chdir(_ORIGINAL_CWD)
