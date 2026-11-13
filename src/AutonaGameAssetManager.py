"""Qt adapter of the game asset catalog (ADR-005 S1a).

The data -- games, characters, skins, stages, asset paths, the start.gg id
tables -- lives in the Qt-free `src/core/GameAssetCatalog.py`. This class only:

- runs the catalog's slow operations (scan, load, start.gg download) as
  jobs of the TaskExecutor (ADR-005 S3f-4; they were QThreads) and announces
  them through `signals.onLoadAssets` / `gameLoaded` / `onLoad` (a load's
  on the GUI thread, _Publish);
- persists and restores the last selected game (SettingsManager).

Until the Qt model clean-up's PR4 it also turned the catalog's snapshot
into the QStandardItemModels the Qt combos bound to, with their icons
decoded on the TaskExecutor's fan-out lane; the combos had all gone (the
React pages read the catalog through the web operations), so it holds no
Qt model and needs no QtGui.

`games` / `selectedGame` / `has_modded_content` are read-only properties
over the current snapshot. They hand out the catalog's own dicts: treat
them as read-only and copy before writing (into the state, say).
"""
import threading
import traceback
from concurrent.futures import Future

from loguru import logger
from qtpy.QtCore import QObject, Signal

from .core.SettingsManager import SettingsManager
from .core.StateManager import StateManager
from .core.StatusService import StatusService
from .core.TaskExecutor import TaskExecutor
from .Helpers import AutonaQtHelper
from .Helpers.AutonaQtHelper import GuiMarshalAborted, in_gui_thread


# How a load request's attempt went: the result of the Future
# LoadGameByCodename returns.
LOADED = "loaded"
SKIPPED = "skipped"         # the catalog did not select (see SelectGame)
SUPERSEDED = "superseded"   # a newer load was asked for before it ran
STOPPED = "stopped"         # the app is shutting down
FAILED = "failed"           # logged


class _LoadRequest:
    """One LoadGameByCodename call: what it asked for, its done(), its
    generation (each request takes the next) and the Future it returned,
    which gets the attempt's outcome."""

    def __init__(self, codename, modsActive, reload, done):
        self.codename = codename
        self.modsActive = modsActive
        self.reload = reload
        self.done = done
        self.generation = None
        self.future = Future()
        # Running before anyone holds it, so whoever does cannot cancel it
        # (cancel() is False): a cancelled one made _Settle's set_result
        # raise, before the generation was finished -- the waiters of a
        # synchronous request waited for ever (ADR-005 S3f-4 review).
        self.future.set_running_or_notify_cancel()


class _SaveCycle:
    """The one save cycle of a game load, opened on the loader's thread and
    closed on the GUI thread (AutonaGameAssetManager._Load): one
    BlockSaving, then one ReleaseSaving, whichever thread gets to it first
    (agent-pitfalls #6). The close publishes -- unless another thread's
    SaveBlock is open then: the last block to close publishes, on its
    thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._open = False

    def Open(self):
        StateManager.BlockSaving()
        self._open = True

    def Release(self):
        """Close the cycle (it publishes when it is the last open block);
        a no-op once it was closed."""
        with self._lock:
            if not self._open:
                return
            self._open = False
        StateManager.ReleaseSaving()


class AutonaGameAssetManagerSignals(QObject):
    onLoad = Signal()
    onLoadAssets = Signal()
    # The GameAssetSnapshot a load published, emitted just before onLoad
    # (bootstrap.wire_game_roster), on the GUI thread. The load's own
    # snapshot, which a slot keeps reading after a later load has moved the
    # catalog's Current() on.
    gameLoaded = Signal(object)


class AutonaGameAssetManager(QObject):
    instance: "AutonaGameAssetManager" = None

    def __init__(self, catalog, parent=None) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.signals = AutonaGameAssetManagerSignals()

        # The game loads (ADR-005 S3f-4 review): one loader job at most,
        # taking the latest request until none is waiting (_LoaderJob).
        # Under _requestLock -- a Condition, which a synchronous request
        # waits on: the generation of the latest request (each takes the
        # next), the request waiting for the loader (None: none; a newer one
        # replaces it), whether a loader job is submitted and not over, that
        # job's Future (None before the first), and the generation of the
        # latest request the loader is over with.
        self._requestLock = threading.Condition()
        self._loadGeneration = 0
        self._pendingLoad = None
        self._loaderRunning = False
        self._loadFuture = None
        self._finishedGeneration = 0

        # Startup restore of the last-used game (see _RestoreSelectedGame).
        # Armed by UiMounted() so a later LoadGames() rescan — "Add game
        # assets", asset guide — never overrides the user's current pick.
        self._pendingSelectedGameRestore = False
        # onLoad is emitted on the GUI thread (_Publish), where this runs.
        # (A load the shutdown stopped after its selection calls it on the
        # loader's thread, _SettleUnannounced: SettingsManager takes its own
        # lock.)
        self.signals.onLoad.connect(self._PersistSelectedGame)

    # ── read-only views of the current snapshot ─────────────────────────
    # (`characters` / `skins` / `variants` / `colors` / `biggestCompletePack`
    # went with the Qt models in the Qt model clean-up's PR4: no reader was
    # left. Read the catalog's Current() instead.)

    @property
    def games(self):
        return self.catalog.GamesView()

    @property
    def selectedGame(self):
        return self.catalog.Current().game

    @property
    def has_modded_content(self):
        return self.catalog.Current().hasModdedContent

    # ── startup / scan ──────────────────────────────────────────────────

    def UiMounted(self):
        self.DownloadStartGGCharacters()
        self._pendingSelectedGameRestore = True
        self.signals.onLoadAssets.connect(self._RestoreSelectedGame)
        self.LoadGames()

    def _PersistSelectedGame(self):
        # The selected game used to live only in memory (reset every launch)
        # and was re-derived solely from the Start.gg videogame id of the
        # restored tournament. That made the sidebar game pulldown depend on
        # a live network round trip: blank for the first seconds of every
        # launch, and blank for the whole session when offline / when the
        # fetch failed / with no tournament. Persist the pick locally
        # instead. StateManager is not usable as the restore source —
        # bootstrap clears `game`, and out/program_state.json is a
        # regenerable export, not user settings.
        SettingsManager.Set(
            "SELECTED_GAME_CODENAME",
            StateManager.Get("game.codename", "") or "")

    def _RestoreSelectedGame(self):
        # Runs on the GUI thread (queued from the scan job's emit), by which
        # point the catalog holds the local user_data/games scan.
        if not self._pendingSelectedGameRestore:
            return
        self._pendingSelectedGameRestore = False

        codename = SettingsManager.Get("SELECTED_GAME_CODENAME", "") or ""
        if not codename:
            return

        if codename not in self.catalog.GameCodenames():
            logger.info(
                f"Last used game '{codename}' is not installed anymore - "
                "skipping restore")
            return

        # Mods stay off on restore, unchanged from the previous startup
        # behaviour: the sidebar checkbox re-syncs itself from
        # game.mods_active on onLoad.
        self.LoadGameByCodename(codename)

    def DownloadStartGGCharacters(self):
        # The start.gg character table refresh: a job (network; it writes the
        # file only, the next load reads it).
        TaskExecutor.instance.submit(
            self.catalog.DownloadStartGGCharacters,
            task_name="start.gg characters download")

    def LoadGames(self):
        # The scan, as a job; onLoadAssets is emitted from it, so its slots
        # (the provider's game first, the startup restore last: bootstrap
        # _gui, agent-pitfalls #2) run queued on the GUI thread, in the
        # order they were connected. A failed scan is logged by the executor
        # and emits nothing.
        TaskExecutor.instance.submit(self._Scan, task_name="game scan")

    def _Scan(self):
        self.catalog.ScanGames()
        self.signals.onLoadAssets.emit()

    # ── game load ───────────────────────────────────────────────────────

    def SetGameFromStartGGId(self, gameid, done=None):
        """`done()` as LoadGameByCodename has it: at once for a game that is
        not one of ours, which asks the loader nothing."""
        codename = self.catalog.GameFromStartGGId(gameid)
        if codename or done is not None:
            self.LoadGameByCodename(codename or "", done=done)

    def LoadGameAssets(self, game: int = 0, async_mode=True, mods_active=False, mods_reload_mode=False):
        """Load the game at 1-based position `game` in the scan.

        0 / None / out of range means "no game" (the tournament URL was
        cleared), which keeps whatever is loaded (see SelectGame)."""
        codenames = self.catalog.GameCodenames()
        codename = ""
        if isinstance(game, int) and 1 <= game <= len(codenames):
            codename = codenames[game - 1]
        self.LoadGameByCodename(codename, async_mode=async_mode,
                                mods_active=mods_active,
                                mods_reload_mode=mods_reload_mode)

    def LoadGameByCodename(self, codename, async_mode=True, mods_active=False, mods_reload_mode=False, done=None):
        """Load `codename` (ADR-005 S3f-4): on the loader, a job of the
        TaskExecutor -- one at most, however many requests come.

        `async_mode` (the default): returns at once a Future, whose result
        says how the attempt went (LOADED / SKIPPED / SUPERSEDED / STOPPED /
        FAILED); it cannot be cancelled (cancel() is False: the attempt is
        the loader's). An empty codename ("no game": the tournament URL
        cleared, which keeps what is loaded) is SKIPPED at once, done() and
        all, on the calling thread. Off: returns once the attempt is over
        (below) -- from a thread other than the GUI thread only (a web
        request: AutonaWebOperations.set_game), since the loader marshals
        the load's announcement onto the GUI thread (_Publish); on the GUI
        thread it raises RuntimeError.

        The latest request wins: a request still waiting for the loader when
        a newer one comes is not loaded (SUPERSEDED, nothing selected), so
        rapid requests A, B, C end on C. A load already running when C comes
        finishes first. A synchronous request is over once the loader is over
        with it, or with a request made after it -- whatever that attempt's
        outcome: set_game's "OK" means the attempt is over, as it did for a
        skipped or failed load (logged) when set_game loaded on the GUI
        thread (ADR-005 S3f-4 review).

        `done()`, if given, is called once the attempt is over however it
        went -- loaded, skipped by the catalog (an unknown codename, e.g. one
        a rescan dropped meanwhile; the same game without reload), failed,
        not run because the app is shutting down, or superseded. onLoad
        fires only for a load that happened, so whoever holds something
        until "the load is over" (the /api/v1 in-progress flag) releases it
        here. A request the loader cannot be started for (the executor
        refuses the job: NestedSubmitError on one of its lanes) raises, with
        nothing recorded and no done().

        done() runs on whichever thread ends the attempt: the loader's (a
        job-lane thread), the thread of the request that superseded it (the
        GUI thread, say), the thread that ended the loader (the shutdown's
        for a loader cancelled while queued; the lane's for one that died),
        or the calling thread (an empty codename; a loader cancelled as it
        was submitted). So it must be quick, safe on any of them, and must
        not ask for another load: on a job-lane thread with no loader
        running an asynchronous request cannot start one (NestedSubmitError,
        logged, and that request is lost), and a synchronous one would wait
        for the loader it runs on (ADR-005 S3f-4 review).
        """
        if not codename:
            request = _LoadRequest(codename, mods_active, mods_reload_mode,
                                   done)
            self._Settle(request, SKIPPED)
            return request.future
        if not async_mode and in_gui_thread():
            raise RuntimeError(
                "a synchronous game load waits for the loader, which "
                "marshals the load's announcement onto the GUI thread: it "
                "cannot be made from the GUI thread")

        request = _LoadRequest(codename, mods_active, mods_reload_mode, done)
        orphan = None
        with self._requestLock:
            if not self._loaderRunning:
                # First, so that a refused job leaves everything as it was
                # (ADR-005 S3f-4 review: the generation used to move first,
                # and a waiter then waited for a load nobody would run).
                self._StartLoader()
            self._loadGeneration += 1
            request.generation = self._loadGeneration
            superseded, self._pendingLoad = self._pendingLoad, request
            if not self._loaderRunning:
                # Cancelled as it was submitted (the executor has shut
                # down): _LoaderEnded ran already, and nothing will take it.
                orphan, self._pendingLoad = request, None
        if superseded is not None:
            logger.info(f"Game load of {superseded.codename} skipped: "
                        f"{codename} was asked for after it")
            self._Settle(superseded, SUPERSEDED)
        if orphan is not None:
            self._SettleAndFinish(orphan, STOPPED)
        if not async_mode:
            self._WaitForLoad(request.generation)
        return request.future

    def _StartLoader(self):
        # Under _requestLock, with no loader running. Raises what the
        # executor's submit raises.
        job = TaskExecutor.instance.submit(self._LoaderJob,
                                           task_name="game load")
        self._loadFuture = job
        self._loaderRunning = True
        # Right here when the job is cancelled already (re-entering the
        # Condition's RLock).
        job.add_done_callback(self._LoaderEnded)

    def _LoaderEnded(self, job):
        # The loader job's Future is done. Normally the job ended itself
        # (nothing left; _loaderRunning is already off). Otherwise it was
        # cancelled while queued -- the shutdown -- or failed outside a load
        # (logged by its lane): the request it would have taken is over.
        with self._requestLock:
            if self._loadFuture is not job or not self._loaderRunning:
                return
            self._loaderRunning = False
            orphan, self._pendingLoad = self._pendingLoad, None
        if orphan is not None:
            self._SettleAndFinish(
                orphan, STOPPED if job.cancelled() else FAILED)

    def _SettleAndFinish(self, request, outcome):
        # A request nothing else will take, over: settled first, then its
        # generation finished -- as the loader does it, so a synchronous
        # waiter returns with its done() run and its Future answered -- and
        # finished whatever the settle did.
        try:
            self._Settle(request, outcome)
        finally:
            with self._requestLock:
                self._Finished(request.generation)

    def _Finished(self, generation):
        # Under _requestLock: the loader is over with `generation` (and so
        # with every request before it).
        if generation > self._finishedGeneration:
            self._finishedGeneration = generation
        self._requestLock.notify_all()

    def _WaitForLoad(self, generation):
        # A synchronous request's wait: until the loader is over with it or
        # with a later request. Blocks on the Condition, which every way a
        # request ends notifies (the loader, _LoaderEnded, a cancelled
        # submit).
        with self._requestLock:
            self._requestLock.wait_for(
                lambda: self._finishedGeneration >= generation)

    @staticmethod
    def _Settle(request, outcome):
        # The request is over: its done(), then its Future.
        try:
            if request.done is not None:
                request.done()
        except Exception:
            logger.error(traceback.format_exc())
        finally:
            request.future.set_result(outcome)

    def _LoaderJob(self):
        # The loader, on a job-lane thread: the latest request, until none
        # is waiting. One at a time by construction (LoadGameByCodename
        # starts it only when none runs), so a load is over -- the next may
        # select a game -- once what follows its selection on the GUI thread
        # is (its icon decodes too, until the Qt model clean-up's PR4), and
        # the requests that wait take no job lane (each used to be a job of
        # its own blocked on a lock: rapid switching could take all four
        # lanes from the provider's fetches; ADR-005 S3f-4 review).
        while True:
            with self._requestLock:
                request, self._pendingLoad = self._pendingLoad, None
                if request is None:
                    self._loaderRunning = False
                    return
            outcome = FAILED
            try:
                outcome = self._RunLoad(request)
            finally:
                # Finished whatever the settle did: a synchronous request
                # waiting for this generation (or an older one) must not
                # wait for ever (ADR-005 S3f-4 review: it did when a caller
                # had cancelled the Future, which made the settle raise).
                self._SettleAndFinish(request, outcome)

    def _RunLoad(self, request):
        # One request's attempt; its outcome. What follows the selection --
        # the announcement, whose onLoad slots touch the window's widgets
        # (its game button, the mods checkbox) -- runs on the GUI thread,
        # marshalled by run_sync (_Publish), which gives up if the app shuts
        # down first (so the shutdown and the interpreter's exit do not wait
        # on the loader for ever). Until the Qt model clean-up's PR4 the
        # marshal rebuilt the Qt models first (QPixmap / QIcon are
        # GUI-thread only; built here they crashed at boot). Whether the
        # marshal is still needed is the run_sync review's (S-final).
        #
        # The footer's progress (core's StatusService, ADR-005 S-final
        # SF-2): called by the catalog under its writer lock, it only
        # records and tells a channel whose one subscriber queues a Qt
        # signal, so it never blocks. Pushed on the first report, so a load
        # the catalog skips shows nothing; updated every 5 characters.
        progress = StatusService.Progress(
            "assets_load", {"codename": request.codename}, every=5)
        try:
            # Checked before anything is written. Once the app is shutting
            # down, a load that waited for the loader would publish its
            # selection to state.json without what goes with it -- the pick
            # (SELECTED_GAME_CODENAME), the roster -- which the GUI thread
            # would no longer run: the next launch would restore the
            # previous game over picks this one forgot.
            reason = AutonaQtHelper.gui_unavailable_reason()
            if reason is not None:
                logger.info(
                    f"Game load of {request.codename} not run: {reason}")
                return STOPPED
            return self._Load(
                request.codename, request.modsActive, request.reload,
                progress)
        except Exception:
            logger.error(traceback.format_exc())
            return FAILED
        finally:
            progress.Close()

    def _Load(self, codename, mods_active, mods_reload_mode, progress):
        # The loader's load: the selection here, what follows it on the GUI
        # thread (_Publish). Returns the outcome.
        #
        # One save cycle for the whole load -- the selection's writes and
        # those of the slots that follow it (the roster, onLoad's) -- opened
        # here and closed on the GUI thread at the end of _Publish, so the
        # cycle normally publishes there and its notifications
        # (state_updated, the held ones) run there, direct, inside
        # StateManager.lock as every GUI-thread cycle's do (agent-pitfalls
        # #17). Not when another thread's SaveBlock is open at that moment:
        # the last block to close publishes, on its own thread. One
        # BlockSaving, one ReleaseSaving on every path (agent-pitfalls #6):
        # _SaveCycle releases once, from _Publish or, when the GUI thread did
        # not run it (skipped, aborted, failed first), from here. The
        # cycle stays open while the GUI thread runs the slots (it rebuilt
        # the Qt models first until the Qt model clean-up's PR4): other
        # threads' board operations wait, as _Publish holds
        # the board host's lock (ADR-005 S4ad review; they waited behind it
        # on the GUI thread until S4ad), and their core-direct writes
        # meanwhile (score:adjust, a colour) land in the cycle. Neither
        # those nor what is written while this thread selects -- before
        # _Publish, a board operation's included -- go out with the load:
        # its commit publishes what the load wrote (_Publish records it;
        # until the fix of 2026-11-12 they did, held edits and all). The
        # catalog's writer lock is released by the time the GUI thread is
        # waited on (SelectGame returned).
        self.catalog.ReloadStartGGCharacters()
        cycle = _SaveCycle()
        cycle.Open()
        try:
            snapshot = self.catalog.SelectGame(
                codename, modsActive=mods_active, reload=mods_reload_mode,
                progress=progress)
            if snapshot is None:
                return SKIPPED
            try:
                AutonaQtHelper.gui_executor.run_sync(
                    lambda: self._Publish(snapshot, cycle))
            except GuiMarshalAborted as aborted:
                logger.info(f"Game load of {codename} stopped: {aborted}")
                self._SettleUnannounced(snapshot)
                return STOPPED
            return LOADED
        finally:
            # (It waited for the icon decodes the model rebuild had started
            # too, until the Qt model clean-up's PR4.)
            cycle.Release()

    def _SettleUnannounced(self, snapshot):
        # The selection is in the state and goes out with the load's cycle,
        # but the load will not be announced (gameLoaded, onLoad): the
        # shutdown came first (STOPPED; on the loader's thread). What
        # their slots would have written after it is written here, in that
        # cycle, so the state -- and state.json -- holds one game (ADR-005
        # S3f-4 reviews). The roster the load asks for (gameLoaded ->
        # bootstrap.wire_game_roster: the previous game's picks emptied),
        # committed as an announced load commits it (what the roster wrote,
        # recorded), then the pick persisted (onLoad's _PersistSelectedGame).
        # Core's operations, through the board host as the announced load's
        # (its lock, on this thread; the headless shell's _SettleStopped
        # does the same). `atShutdown`: the host refuses a board operation
        # off the main thread once the app is shutting down (the 503 a late
        # request gets), and this one is that shutdown's own write. Until
        # ADR-005 S4ad the desktop's host handed its work to the GUI thread,
        # which the loader's thread would have waited for, so this called
        # core directly. (_Publish called it too, on the GUI thread inside
        # its own operation and recording, when the Qt model rebuild failed
        # -- FAILED -- until the Qt model clean-up's PR4 removed the
        # rebuild.)
        from . import AutonaWebOperations
        from .core.Actions import AutonaActions
        actions = AutonaActions.instance

        def settle():
            # Until the fix of 2026-11-12 each board the roster applied to
            # was committed whole (ApplyChanges), held edits and all.
            with StateManager.RecordHeldWrites() as written:
                actions.ApplyGameRoster(getattr(snapshot, "replaced", None))
                actions.CommitGameLoad(written)

        AutonaWebOperations.boardHost.Run(settle, atShutdown=True)
        self._PersistSelectedGame()

    def _Publish(self, snapshot, cycle):
        # GUI thread: the load announced -- gameLoaded (the roster, the set
        # length and the commit: bootstrap.wire_game_roster) ahead of onLoad
        # (the window's game button and mods checkbox, the persisted pick,
        # the player DB's refresh), each slot run here, in connection order
        # -- and last the load's save cycle closed, here too, which
        # publishes what the commit queued. So a load that returns has had
        # all of them run: what set_game's "OK" has always meant. (Until
        # ADR-005 S3f-4 the loader thread emitted them and the slots ran
        # queued after it, each in a save cycle of its own. Until the Qt
        # model clean-up's PR4 the Qt models were rebuilt here first, and a
        # rebuild that raised settled the load unannounced -- FAILED -- in
        # this same operation and recording.)
        #
        # All of it one board operation (AutonaWebOperations.boardHost,
        # under its lock; ADR-005 S4ad review): no board operation of
        # another thread lands between the roster, the slots and the close.
        # Until S4ad those ran on this (the GUI) thread, queued behind this
        # call. Nothing in here waits for another thread (onLoad's slots
        # take the settings' / player DB's locks, which come after this one,
        # and queue the rest), so a request thread holding the lock only
        # makes this wait for its operation to end. What another thread
        # writes meanwhile all the same (a core-direct write: score:adjust),
        # or wrote while the loader selected (a board operation too), is not
        # the load's: the commit publishes what the load wrote -- recorded
        # here from the announcement to the close
        # (StateManager.RecordHeldWrites; the wiring's joins it) -- and
        # leaves the rest behind 反映. Until the fix of 2026-11-12 the close
        # published all of it. The load's writes are told by thread: a
        # gameLoaded / onLoad slot that writes held keys from another thread
        # (a job it hands off and waits for, a queued call) is someone else
        # to the commit, and what it writes waits for 反映.
        # Imported here: the web layer is not this module's to pull in when
        # it is imported.
        from . import AutonaWebOperations

        def publish():
            with StateManager.RecordHeldWrites():
                try:
                    # The slots' exceptions do not reach here: emit reports
                    # them and goes on.
                    self.signals.gameLoaded.emit(snapshot)
                    self.signals.onLoad.emit()
                    # The commit (AutonaActions.CommitGameLoad) is the
                    # wiring's, on gameLoaded, with the roster since ADR-005
                    # S4ad. It was called here, after onLoad, while a Qt
                    # player card wrote its rows on onLoad (until S4ac-3).
                    # Queued inside this cycle and this recording, it still
                    # takes what onLoad's slots write with it, when the
                    # cycle closes below.
                finally:
                    cycle.Release()

        AutonaWebOperations.boardHost.Run(publish)
