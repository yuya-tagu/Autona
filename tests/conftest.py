"""Test infrastructure for Autona.

Since Step 3 of the startup redesign, `src/__init__.py` is empty and no src
module builds anything at import time, so tests import normally
(`from src.X import Y`). The old workaround -- loading a single .py by file
path to dodge the package chain -- is gone with it, and so is the helper
this module used to export for it.

The one thing still worth guarding is import side effects themselves; that
is tests/test_import_side_effects.py.

This module also hosts the widget-level harness (`autonaApp`): a real,
bootstrapped singleton layer plus real scoreboard widgets, running offscreen
inside a throwaway working directory. See the section at the bottom.
"""
import contextlib
import json
import os
import shutil
import subprocess
import sys
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


# ── Widget-level harness (L2) ────────────────────────────────────────────
#
# Everything below builds a real, bootstrapped Autona -- the same singletons
# main.py gets, and the same scoreboard widgets -- offscreen, so the core UI
# can be driven from tests instead of by hand. See
# docs/changes/test-automation-strategy.md §4 for why this layer exists: the
# widgets are where most historical bugs were found and were the only part of
# the app with no automated coverage at all.
#
# Two rules keep it honest:
#
# 1. It never touches the real installation. The app resolves its mutable
#    paths (./out, ./user_data, tmp/) against the process CWD, so the harness
#    runs inside a throwaway directory and links in only the read-only data
#    the widgets load (the .ui files, assets/, the i18n tables, the GraphQL
#    query texts). tests/test_harness_isolation.py asserts that.
# 2. Nothing here is a stub. GetScoreboard() returns the production widget;
#    a test that passes because the harness faked something is worthless.

_ORIGINAL_CWD = os.getcwd()

# Read-only trees the widgets load, linked rather than copied: assets/ alone
# is ~110 MB (the country database and the bundled fonts). Nothing in src/
# writes to any of them -- the one runtime write into a shipped tree,
# layout/logo.png, comes from Autona.Window.__init__, which this layer does
# not build. layout/ therefore gets a real (empty) directory below.
_LINKED_TREES = [
    "assets",
    "src/layout",                     # the .ui files loaded by uic.loadUi
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

    # -- widgets ----------------------------------------------------------

    @property
    def manager(self):
        from src.AutonaScoreboardManager import AutonaScoreboardManager
        return AutonaScoreboardManager.instance

    def board(self, number=1):
        """A freshly built scoreboard: 1 = 1v1, 2 = team, 3 = crew.

        Rebuilt per call rather than reset, so no test can be handed a board
        carrying another test's inputs -- and so a reset path being broken
        (which is exactly what several of these tests check) cannot silently
        become the thing that isolates them.
        """
        self._dropBoard(number)
        board = self.manager.EnsureScoreboard(number)
        assert board is not None, f"scoreboard {number} failed to build"
        self.settle()
        return board

    def _dropBoard(self, number):
        from qtpy.QtCore import QCoreApplication, QEvent

        from src.core.CrewBattleService import CrewBattleService

        # The crew board's swap flag is core's, kept outside the state (see
        # CrewBattleService._swapped), so the Unset below does not take it.
        # In the app only the board's launch write clears it (SeedBoardState),
        # and a test that swaps board 3 without building it (the headless
        # board host) builds nothing that would: drop it here, built board or
        # not, so the next test does not start swapped.
        CrewBattleService._swapped.pop(number, None)
        # The set the board loaded and what it follows are core's since
        # ADR-005 S3e-7 (they were the board's, and went with it).
        from src.core.SetFollowService import SetFollowService
        SetFollowService.ForgetBoard(number)

        manager = self.manager
        board = manager.scoreboards.pop(number, None)
        if board is None:
            return
        for attribute in ("scoreboard", "teamScoreboard", "crewScoreboard"):
            if getattr(manager, attribute, None) is board:
                setattr(manager, attribute, None)
        board.setParent(None)
        board.deleteLater()
        # ...and actually destroy it. QApplication.processEvents() does NOT
        # run deferred deletions: Qt only reaps a DeferredDelete event whose
        # loop level is below the one being drained, and pytest never calls
        # exec(), so the level stays 0 and every deleteLater() here would sit
        # in the queue for the whole session. The C++ widget would stay alive
        # with its lambda connections to the process-wide singletons
        # (AutonaGameAssetManager.signals.onLoad, StateManagerBridge, ...)
        # intact -- a lambda holds no weak reference to its receiver, so
        # PySide6 cannot auto-disconnect it. Every dropped board would then
        # keep answering signals and rewriting score.{n}, including after the
        # CWD was restored, i.e. into the developer's real ./out.
        # sendPostedEvents() with an explicit DeferredDelete type is the one
        # call that reaps it at loop level 0; it is aimed at this board so
        # nothing else in the session gets torn down as a side effect.
        QCoreApplication.sendPostedEvents(board, QEvent.Type.DeferredDelete)

        # AutonaScoreboardWidget.__init__ registers its subtree as held (the
        # 反映 button). StateManager has no inverse, so the registration is
        # cleared here directly -- leaving it behind would make every later
        # write under score.{n} invisible to a test that never built a board,
        # and would hand tests/test_state_manager_held.py a pre-registered
        # prefix.
        #
        # This has to happen BEFORE the Unset below. While the prefix is still
        # held, the Unset's changed keys are buffered into heldKeys[prefix] and
        # then thrown away with it, so the board's removal would reach neither
        # the delta nor ./out -- leaving stale score/{n}/*.txt behind and a
        # stale score.{n} in lastSavedState. Held state is read under the lock
        # by SaveState, so take it here too.
        prefix = f"score.{number}"
        with self.state.lock:
            self.state.heldPrefixes.discard(prefix)
            self.state.heldExempt.pop(prefix, None)
            self.state.heldKeys.pop(prefix, None)
            self.state._commitPrefixes.discard(prefix)
            self.state._heldTouched.discard(prefix)

        self.state.Unset(prefix)

        self.settle()

    def loadCharacters(self, names=("Mario", "Luigi", "Peach"), skins=3):
        """Install and load a small synthetic game, HARNESS_GAME.

        The harness has no game installed (its user_data/games is empty), so
        AutonaGameAssetManager.characterModel has zero rows: every character
        combo can only ever sit at index -1, and any assertion about *which*
        character a slot ended up on is vacuous. This writes a real game pack
        -- one stock-icon PNG per character and skin -- into the runtime
        user_data/games and loads it through the production path: the
        catalog's scan and load, then the Qt adapter's model rebuild.

        What it leaves out is the onLoad broadcast that ends a real load, so
        widgets other tests left alive do not react to it. Existing combos
        only rebind on onLoad: call this before building the widgets that
        should see the roster.

        The icon workers the rebuild starts are waited out here: they must
        not outlive the CWD.
        """
        from PIL import Image
        from qtpy.QtWidgets import QApplication

        from src.AutonaGameAssetManager import AutonaGameAssetManager
        from src.core.GameAssetCatalog import GameAssetCatalog

        game = self.dir / "user_data" / "games" / HARNESS_GAME
        if game.exists():
            shutil.rmtree(game)
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
        manager._RebuildModels()
        manager.threadpool.waitForDone()
        QApplication.processEvents()
        self._gameInstalled = True
        return manager

    def _restoreAssets(self):
        """Uninstall HARNESS_GAME and go back to the no-game models."""
        if not self._gameInstalled:
            return
        from qtpy.QtWidgets import QApplication

        from src.AutonaGameAssetManager import AutonaGameAssetManager
        from src.core.GameAssetCatalog import GameAssetCatalog

        manager = AutonaGameAssetManager.instance
        manager.threadpool.waitForDone()
        QApplication.processEvents()
        shutil.rmtree(self.dir / "user_data" / "games" / HARNESS_GAME,
                      ignore_errors=True)
        catalog = GameAssetCatalog.instance
        catalog.ScanGames()
        # The empty snapshot rebuilds the empty models of a fresh start; the
        # synthetic generation is retired, not dropped (agent-pitfalls #13).
        catalog.ResetGameState()
        manager._RebuildModels()
        self._gameInstalled = False

    def dropAllBoards(self):
        for number in self.manager.BOARD_NUMBERS:
            self._dropBoard(number)
        self._restoreAssets()

    # -- web layer --------------------------------------------------------

    def actions(self):
        """A WebServerActions bound to the real manager (never started).

        The board-dependent operations (set loading, the thumbnail, ...;
        the statistics are web() since ADR-005 S3e-5): what the HTTP routes
        and Socket.IO events that need a widget
        delegate to, so driving it exercises the same code path as a request
        without binding a port.
        """
        from src.AutonaWebServerActions import WebServerActions
        return WebServerActions(parent=None, scoreboard=self.manager)

    def web(self):
        """The web operations every route reaches without a widget of its
        own (src/AutonaWebOperations.py, ADR-005 S3c): the core-direct ones
        and the S2 operations. Here they run through the desktop's board
        host (bootstrap_gui() registered it on the real manager), as in the
        app. A module, called like the actions object: web().swap_teams(1).
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

        # Both stages, as src/Autona.py runs them: the harness builds desktop
        # widgets (boards, panels), which need the Qt adapters.
        bootstrap_core()
        bootstrap_gui()
        # Normally done by src/Autona.py at import time. WebServerActions'
        # @gui_thread_sync methods raise without it, and that module is the
        # one thing tests must not import (agent-pitfalls #1).
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
        # machine. Nothing does: AutonaHotkeys.instance exists after
        # bootstrap, but registration happens in UiMounted(), which only
        # Window.__init__ calls and this layer never builds a Window. It is
        # deliberately NOT forced off through SettingsManager either --
        # SettingsManager is process-global, and pinning hotkeys.* here would
        # silently change what tests/test_hotkeys_*.py observe.

        yield _AutonaHarness(directory)
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
    - the boards, because a live scoreboard stays connected to
      StateManagerBridge and would keep reacting to writes made by test
      modules that never asked for a widget.
    """
    os.chdir(_autonaRuntime.dir)
    try:
        yield _autonaRuntime
    finally:
        _autonaRuntime.dropAllBoards()
        _autonaRuntime.settle()
        os.chdir(_ORIGINAL_CWD)
