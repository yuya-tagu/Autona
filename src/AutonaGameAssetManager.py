"""Qt adapter of the game asset catalog (ADR-005 S1a).

The data -- games, characters, skins, stages, asset paths, the start.gg id
tables -- lives in the Qt-free `src/core/GameAssetCatalog.py`. This class only:

- turns the catalog's current snapshot into the QStandardItemModels the
  combos bind to, plus their icons (decoded on the TaskExecutor's fan-out
  lane, applied on the GUI thread);
- runs the catalog's slow operations (scan, load, start.gg download) as
  jobs of the TaskExecutor (ADR-005 S3f-4; they were QThreads) and announces
  them through `signals.onLoadAssets` / `onLoad`;
- persists and restores the last selected game (SettingsManager).

The old attributes (`games`, `selectedGame`, `characters`, ...) are
read-only properties over the current snapshot. They hand out the catalog's
own dicts: treat them as read-only and copy before writing (into the state,
say).
"""
import os
import threading
import traceback
from concurrent.futures import CancelledError, Future

from loguru import logger
from PIL import Image
from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *

from .core.GameAssetCatalog import CANCEL_ICON_PATH
from .core.SettingsManager import SettingsManager
from .core.StateManager import StateManager
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

        # Memoised 32px stock-icon QIcons, keyed by (en_name, skin), for the
        # snapshot generation in _iconCacheGeneration. QIcon must be built
        # on the GUI thread; all callers run there.
        self._iconCache = {}
        self._iconCacheGeneration = None

        self.characterModel = QStandardItemModel()
        self.skinModels = {}
        self.variantModel = QStandardItemModel()
        self.colorModel = QStandardItemModel()
        self.stageModel = QStandardItemModel()
        self.stageModelWithBlank = QStandardItemModel()
        # The previous model generation, kept alive until the next rebuild
        # (agent-pitfalls #13). See _RebuildModels.
        self._retiredModels = None
        # The snapshot generation the models above were built from.
        self._modelGeneration = None

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
        # Generation of the stage / skin models the icon decodes are for.
        # Bumped whenever UpdateStageModel / UpdateSkinModel replace those
        # models; a decode result carrying an older value belongs to a retired
        # generation and is dropped instead of being applied to its items.
        self._stageIconGeneration = 0
        self._skinIconGeneration = 0

        # Startup restore of the last-used game (see _RestoreSelectedGame).
        # Armed by UiMounted() so a later LoadGames() rescan — "Add game
        # assets", asset guide — never overrides the user's current pick.
        self._pendingSelectedGameRestore = False
        # onLoad is emitted on the GUI thread (_Publish), where this runs.
        # (A load the shutdown stopped after its selection calls it on the
        # loader's thread, _SettleUnannounced: SettingsManager takes its own
        # lock.)
        self.signals.onLoad.connect(self._PersistSelectedGame)

        self._RebuildModels()

    # ── read-only views of the current snapshot ─────────────────────────

    @property
    def games(self):
        return self.catalog.GamesView()

    @property
    def selectedGame(self):
        return self.catalog.Current().game

    @property
    def characters(self):
        return self.catalog.Current().characters

    @property
    def skins(self):
        return self.catalog.Current().skins

    @property
    def variants(self):
        return self.catalog.Current().variants

    @property
    def colors(self):
        return self.catalog.Current().colors

    @property
    def stages(self):
        return self.catalog.Current().stages

    @property
    def biggestCompletePack(self):
        return self.catalog.Current().biggestCompletePack

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

    def SetGameFromStartGGId(self, gameid):
        codename = self.catalog.GameFromStartGGId(gameid)
        if codename:
            self.LoadGameByCodename(codename)

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
        the models onto the GUI thread; on the GUI thread it raises
        RuntimeError.

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
                "marshals the models onto the GUI thread: it cannot be made "
                "from the GUI thread")

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
        # select a game -- once its icon decodes are, and the requests that
        # wait take no job lane (each used to be a job of its own blocked on
        # a lock: rapid switching could take all four lanes from the
        # provider's fetches; ADR-005 S3f-4 review).
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
        # One request's attempt; its outcome. The models are rebuilt on the
        # GUI thread, marshalled by run_sync (QPixmap / QIcon are GUI-thread
        # only; built here they crashed at boot), which gives up if the app
        # shuts down first (so the shutdown and the interpreter's exit do
        # not wait on the loader for ever).
        from .Helpers.AutonaStatusManager import AutonaStatusManager
        status = {}
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
                self._StatusProgress(request.codename, status))
        except Exception:
            logger.error(traceback.format_exc())
            return FAILED
        finally:
            AutonaStatusManager.pop(status.get("token"))

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
        # cycle stays open while the GUI thread rebuilds the models and runs
        # the slots: other threads' writes meanwhile are published with it.
        # The catalog's writer lock is released by the time the GUI thread
        # is waited on (SelectGame returned).
        self.catalog.ReloadStartGGCharacters()
        cycle = _SaveCycle()
        # The icon decodes the rebuild starts, collected as they start: they
        # are waited for however far the rebuild got.
        decodes = []
        cycle.Open()
        try:
            snapshot = self.catalog.SelectGame(
                codename, modsActive=mods_active, reload=mods_reload_mode,
                progress=progress)
            if snapshot is None:
                return SKIPPED
            try:
                AutonaQtHelper.gui_executor.run_sync(
                    lambda: self._Publish(snapshot, cycle, decodes))
            except GuiMarshalAborted as aborted:
                logger.info(f"Game load of {codename} stopped: {aborted}")
                self._SettleUnannounced(snapshot)
                return STOPPED
            return LOADED
        finally:
            try:
                cycle.Release()
            finally:
                self._WaitForDecodes(decodes)

    def _SettleUnannounced(self, snapshot):
        # The selection is in the state and goes out with the load's cycle,
        # but the load will not be announced (gameLoaded, onLoad): the
        # shutdown came first (STOPPED; on the loader's thread) or the model
        # rebuild failed (FAILED; on the GUI thread, in _Publish). What
        # their slots would have written after it is written here, in that
        # cycle, so the state -- and state.json -- holds one game (ADR-005
        # S3f-4 reviews). The roster the load asks for (gameLoaded ->
        # bootstrap.wire_game_roster: the previous game's picks emptied),
        # committed as each board's onLoad handler commits it, then the
        # pick persisted (onLoad's _PersistSelectedGame). Core's operations,
        # callable from any thread -- not through the board host, which
        # hands its work to the GUI thread (the loader's thread would wait
        # for it). Left as they are: the stage rows, and the models a failed
        # rebuild left part-way -- the next load redoes both
        # (RepublishBestOf, _RebuildModels).
        from .core.Actions import AutonaActions
        actions = AutonaActions.instance
        for board in actions.ApplyGameRoster(
                getattr(snapshot, "replaced", None)):
            actions.ApplyChanges(board)
        self._PersistSelectedGame()

    def _Publish(self, snapshot, cycle, decodes):
        # GUI thread: the models rebuilt, then the load announced --
        # gameLoaded (the roster) ahead of onLoad (everything that rebinds to
        # the new models), each slot run here, in connection order -- and
        # last the load's save cycle closed, here too. So a load that
        # returns has had all of them run: what set_game's "OK" has always
        # meant. (Until ADR-005 S3f-4 the loader thread emitted them and the
        # slots ran queued after it, each in a save cycle of its own.) The
        # icon decodes the rebuild starts go into `decodes`.
        try:
            try:
                self._RebuildModels(decodes)
            except Exception:
                # Not announced, then (FAILED): what the slots would have
                # written goes with the selection all the same, before the
                # cycle closes (ADR-005 S3f-4 review). The slots' exceptions
                # do not reach here: emit reports them and goes on.
                self._SettleUnannounced(snapshot)
                raise
            self.signals.gameLoaded.emit(snapshot)
            self.signals.onLoad.emit()
        finally:
            cycle.Release()

    @staticmethod
    def _WaitForDecodes(decodes):
        # Each decode's own Future: result() sees a cancellation at once
        # (concurrent.futures.wait would not see the shutdown's; see
        # core/TaskExecutor). A failed decode was logged by its lane.
        for future in decodes:
            try:
                future.result()
            except CancelledError:
                pass
            except Exception:
                pass

    @staticmethod
    def _StatusProgress(codename, status):
        # Footer progress for the threaded load. Called by the catalog under
        # its writer lock: AutonaStatusManager only emits a (queued) signal,
        # so this never blocks. The status is pushed on the first call, so
        # a load the catalog skips shows nothing.
        from .Helpers.AutonaStatusManager import AutonaStatusManager
        message = f"ゲームアセットを読み込み中... ({codename})"

        def progress(done, total):
            if status.get("token") is None:
                status["token"] = AutonaStatusManager.push(message)
            # Throttled to every 5 characters to avoid signal spam.
            if done > 0 and (done % 5 == 0 or done >= total):
                AutonaStatusManager.update(
                    status["token"], message, progress=done, total=total)
        return progress

    # ── models ──────────────────────────────────────────────────────────

    @staticmethod
    def _HasGame(snapshot):
        # False only for the snapshot before any load (and after
        # ResetGameState): its models are empty, without the blank first
        # rows, exactly as before the first load.
        return bool(snapshot.codename or snapshot.characters or snapshot.stages
                    or snapshot.variants or snapshot.colors)

    def _RebuildModels(self, decodes=None):
        """Rebuild every model from the catalog's current snapshot.

        Reads Current() rather than taking the snapshot of the load that
        asked for it, so overlapping loads converge on the latest one, and
        skips when the models already belong to that generation. GUI thread
        only (QPixmap / QIcon). Returns the Futures of the icon decodes it
        started (UpdateSkinModel / UpdateStageModel), [] when it skipped:
        `decodes` if given, appended to as each decode starts -- so a caller
        has the ones started before a failure part-way (the loader waits
        for them).
        """
        decodes = [] if decodes is None else decodes
        snapshot = self.catalog.Current()
        if snapshot.generation == self._modelGeneration:
            return decodes
        # Retire the previous model generation instead of letting it be
        # garbage-collected mid-swap: player-widget combos and proxies stay
        # attached to the old models until their onLoad handlers
        # (ReloadCharacters etc.) rebind AFTER this batch, and deleting a C++
        # model under a live view/proxy is an access-violation crash (same
        # class as the AutonaPlayerDB.SetupModel boot crash, 2026-07-11).
        self._retiredModels = [
            self.characterModel, self.skinModels, self.variantModel,
            self.colorModel, self.stageModel, self.stageModelWithBlank]
        self._modelGeneration = snapshot.generation
        self.UpdateCharacterModel(snapshot)
        self.UpdateSkinModel(snapshot, decodes)
        self.UpdateVariantModel(snapshot)
        self.UpdateColorModel(snapshot)
        self.UpdateStageModel(snapshot, decodes)
        return decodes

    def _DecodeIcons(self, decode, complete, *args, task_name):
        """Run `decode(*args)` on the TaskExecutor's fan-out lane and hand its
        result to `complete` on the GUI thread; the decode's Future.

        The fan-out lane, not the job lane: the loader job waits for these
        (_WaitForDecodes), and a job waits only on fan-out tasks (core
        /TaskExecutor). The decode stops at QImage; `complete` builds the
        QPixmap / QIcon and sets it on the GUI thread, through the long-lived
        GUI executor (the Worker's own signals object these went through
        until ADR-005 S3f-4 could be collected before its answer arrived).
        """
        def run():
            result = decode(*args)
            AutonaQtHelper.gui_executor(lambda: complete(result))
        return TaskExecutor.instance.submit_fanout(run, task_name=task_name)

    def UpdateStageModel(self, snapshot=None, decodes=None):
        """Rebuild the two stage models; the Futures of their icon decodes
        (appended to `decodes` if given, and that list returned)."""
        snapshot = snapshot or self.catalog.Current()
        decodes = [] if decodes is None else decodes
        try:
            self._stageIconGeneration += 1
            generation = self._stageIconGeneration
            self.stageModel = QStandardItemModel()
            self.stageModelWithBlank = QStandardItemModel()
            if not self._HasGame(snapshot):
                return decodes
            # Add blank
            item = QStandardItem("")
            item.setData({}, Qt.ItemDataRole.UserRole)
            self.stageModelWithBlank.appendRow(item)

            # The catalog orders both lists: config order for the stage
            # combo, sorted for the with-blank one.
            for model, rows in ((self.stageModel, snapshot.stageRows),
                                (self.stageModelWithBlank,
                                 snapshot.stageRowsSorted)):
                for text, stage in rows:
                    item = QStandardItem(text)
                    item.setData(stage, Qt.ItemDataRole.UserRole)
                    model.appendRow(item)

                    decodes.append(self._DecodeIcons(
                        self.LoadStageImage, self.LoadStageImageComplete,
                        stage, item, generation,
                        task_name=f"stage icon ({text})"))
        except Exception:
            logger.error(traceback.format_exc())
        return decodes

    def LoadStageImage(self, stage, item, generation):
        # Runs on a fan-out thread. QPixmap / QIcon are GUI-thread only, so
        # this stops at a QImage; LoadStageImageComplete converts it on the GUI
        # thread. A None image asks for the fallback (cancel) icon.
        try:
            if stage.get("path") and os.path.exists(stage.get("path")):
                img = Image.open(stage.get("path"))

                resizeMultiplier = 1

                if img.width > img.height:
                    resizeMultiplier = 64/img.width
                else:
                    resizeMultiplier = 64/img.height

                img = img.resize((int(img.width*resizeMultiplier),
                                 int(img.height*resizeMultiplier)), Image.BILINEAR)

                img = img.convert("RGBA")
                data = img.tobytes("raw", "RGBA")
                qimg = QImage(
                    data, img.size[0], img.size[1], QImage.Format.Format_RGBA8888)

                # copy(): the QImage above only wraps `data`, which does not
                # outlive this call; the GUI thread gets one owning its pixels.
                return (generation, item, qimg.copy())
        except Exception:
            logger.error(traceback.format_exc())
            return (generation, item, None)

    def LoadStageImageComplete(self, result):
        # GUI thread (handed over by the LoadStageImage decode).
        try:
            if result is None:
                return
            generation, item, image = result
            if generation != self._stageIconGeneration:
                # The stage models were rebuilt while this decode ran: its
                # item belongs to a retired generation.
                return
            if image is None:
                icon = QIcon(QPixmap(CANCEL_ICON_PATH).scaled(32, 32))
            else:
                icon = QIcon(QPixmap.fromImage(image))
            if not icon.isNull():
                item.setIcon(icon)
        except Exception:
            logger.error(traceback.format_exc())
            return (None)

    def GetStockIcon(self, en_name, skin=0):
        # Shared, memoised 32px stock-icon QIcon for a character/skin, from
        # the current snapshot. QIcon / QPixmap must be built on the GUI
        # thread; all callers (AutonaPlayerDB.SetupModel, the player browser)
        # run there. Returns an empty QIcon when no path exists.
        return self._StockIcon(self.catalog.Current(), en_name, skin)

    def _StockIcon(self, snapshot, en_name, skin):
        # The icon comes from `snapshot`, so a model built from one snapshot
        # never mixes in another game's icon when a load lands meanwhile.
        # The cache follows the snapshot generation.
        if snapshot.generation != self._iconCacheGeneration:
            self._iconCache = {}
            self._iconCacheGeneration = snapshot.generation
        cache_key = (en_name, skin)
        icon = self._iconCache.get(cache_key)
        if icon is None:
            path = snapshot.stockIconPaths.get(en_name, {}).get(skin)
            if path is None:
                return QIcon()
            icon = QIcon(QPixmap.fromImage(QImage(path).scaledToWidth(
                32, Qt.TransformationMode.SmoothTransformation)))
            self._iconCache[cache_key] = icon
        return icon

    def UpdateCharacterModel(self, snapshot=None):
        snapshot = snapshot or self.catalog.Current()
        try:
            self.characterModel = QStandardItemModel()
            if not self._HasGame(snapshot):
                return

            # Add one empty
            item = QStandardItem("")
            self.characterModel.appendRow(item)

            # Rows come in display order, modded ones already left out while
            # mods are off (GameAssetCatalog).
            for text, data in snapshot.characterRows:
                item = QStandardItem()
                item.setData(text, Qt.ItemDataRole.EditRole)
                item.setIcon(self._StockIcon(snapshot, data.get("en_name"), 0))
                item.setData(data, Qt.ItemDataRole.UserRole)
                self.characterModel.appendRow(item)
        except Exception:
            logger.error(traceback.format_exc())

    def UpdateColorModel(self, snapshot=None):
        snapshot = snapshot or self.catalog.Current()
        try:
            self.colorModel = QStandardItemModel()
            if not self._HasGame(snapshot):
                return

            # Add one empty
            item = QStandardItem("")
            self.colorModel.appendRow(item)

            for text, data in snapshot.colorRows:
                item = QStandardItem()
                item.setData(text, Qt.ItemDataRole.EditRole)

                try:
                    icon = QPixmap(100, 100)
                    icon.fill(QColor("#" + data.get("value")))
                    item.setIcon(QIcon(icon))
                except Exception:
                    # A colour without a value: the row stays, iconless.
                    logger.error(traceback.format_exc())

                item.setData(data, Qt.ItemDataRole.UserRole)
                self.colorModel.appendRow(item)
        except Exception:
            logger.error(traceback.format_exc())

    def UpdateVariantModel(self, snapshot=None):
        snapshot = snapshot or self.catalog.Current()
        try:
            self.variantModel = QStandardItemModel()
            if not self._HasGame(snapshot):
                return

            # Add one empty
            item = QStandardItem("")
            self.variantModel.appendRow(item)

            for text, data in snapshot.variantRows:
                item = QStandardItem()
                item.setData(text, Qt.ItemDataRole.EditRole)

                if data.get("icon_path"):
                    item.setIcon(QIcon(QPixmap.fromImage(QImage(data["icon_path"]))))
                else:
                    item.setIcon(QIcon(QPixmap.fromImage(QImage(CANCEL_ICON_PATH))))

                item.setData(data, Qt.ItemDataRole.UserRole)
                self.variantModel.appendRow(item)
        except Exception:
            logger.error(traceback.format_exc())

    def UpdateSkinModel(self, snapshot=None, decodes=None):
        """Rebuild the skin models, one per character; the Futures of their
        icon decodes, one per character (appended to `decodes` as each
        starts, if given, and that list returned)."""
        snapshot = snapshot or self.catalog.Current()
        self.skinModels = {}
        self._skinIconGeneration += 1
        generation = self._skinIconGeneration

        decodes = [] if decodes is None else decodes

        for key, rows in snapshot.skinRows.items():
            skinModel = QStandardItemModel()

            allAssetData = []
            allItem = []

            for _skin, text, assetData in rows:
                item = QStandardItem()
                skinModel.appendRow(item)
                item.setData(text, Qt.ItemDataRole.EditRole)
                item.setData(assetData, Qt.ItemDataRole.UserRole)

                allItem.append(item)
                allAssetData.append(assetData)

            self.skinModels[key] = skinModel

            # The pack comes from the snapshot the rows were built from, not
            # from Current() inside the decode (a later load may have landed).
            decodes.append(self._DecodeIcons(
                self.LoadSkinImages, self.LoadSkinImagesComplete,
                allAssetData, allItem, skinModel, generation,
                snapshot.biggestCompletePack,
                task_name=f"skin icons ({key})"))

        return decodes

    def LoadSkinImages(self, allAssetData, allItem, skinModel, generation, biggestCompletePack):
        # Runs on a fan-out thread: stops at QImage (see LoadStageImage);
        # LoadSkinImagesComplete builds the icons on the GUI thread.
        # skinModel is unused: passed only to keep the model alive while the decode runs.
        # A None image (no usable asset, or one that failed to decode) asks
        # for the fallback (cancel) icon, per skin. The old code
        # opened cancel.svg with PIL, which cannot read SVG, so one
        # asset-less skin dropped the icons of the whole character.
        images = []

        for i in range(len(allAssetData)):
            assetData = allAssetData[i]
            try:
                # Set to use first asset as a fallback
                key = biggestCompletePack
                asset = None

                if assetData["assets"].get(key):
                    asset = assetData["assets"][key]
                elif assetData["assets"].get("full"):
                    asset = assetData["assets"]["full"]
                elif assetData["assets"].get("base_files/icon"):
                    asset = assetData["assets"]["base_files/icon"]

                if not asset or not asset.get("asset"):
                    images.append(None)
                    continue

                img = Image.open(asset["asset"])

                targetW = 128
                targetH = 96

                originalW = img.width
                originalH = img.height

                proportional_zoom = 1

                if asset.get("average_size"):
                    proportional_zoom = 0
                    proportional_zoom = max(
                        proportional_zoom,
                        (targetW / asset.get("average_size", {}).get("x", 0)) * 1.2
                    )
                    proportional_zoom = max(
                        proportional_zoom,
                        (targetH / asset.get("average_size", {}).get("y", 0)) * 1.2
                    )

                # For cropped assets, zoom to fill
                # Calculate max zoom
                zoom_x = targetW / originalW
                zoom_y = targetH / originalH

                minZoom = 1
                rescalingFactor = 1
                customZoom = 1

                if asset.get("rescaling_factor"):
                    rescalingFactor = asset.get("rescaling_factor")

                uncropped_edge = asset.get("uncropped_edge", [])

                if not uncropped_edge or len(uncropped_edge) == 0:
                    if zoom_x > zoom_y:
                        minZoom = zoom_x
                    else:
                        minZoom = zoom_y
                else:
                    if (
                        "u" in uncropped_edge and
                        "d" in uncropped_edge and
                        "l" in uncropped_edge and
                        "r" in uncropped_edge
                    ):
                        if "icon" in asset.get("type", []):
                            customZoom = 0.5 # Add zoom for icons to fit
                        else:
                            customZoom = 1.2  # Add zoom in for uncropped assets
                        minZoom = customZoom * proportional_zoom * rescalingFactor
                    elif (
                        not "l" in uncropped_edge and
                        not "r" in uncropped_edge
                    ):
                        minZoom = zoom_x
                    elif (
                        not "u" in uncropped_edge and
                        not "d" in uncropped_edge
                    ):
                        minZoom = zoom_y
                    else:
                        minZoom = customZoom * proportional_zoom * rescalingFactor

                zoom = max(minZoom, customZoom * minZoom)

                # Centering
                xx = 0
                yy = 0

                eyesight = asset.get("eyesight")

                if not eyesight:
                    eyesight = {
                        "x": originalW / 2,
                        "y": originalH / 2
                    }

                xx = -eyesight["x"] * zoom + targetW / 2

                maxMoveX = targetW - originalW * zoom

                if not uncropped_edge or not "l" in uncropped_edge:
                    if (xx > 0):
                        xx = 0

                if not uncropped_edge or not "r" in uncropped_edge:
                    if (xx < maxMoveX):
                        xx = maxMoveX

                yy = -eyesight["y"] * zoom + targetH / 2

                maxMoveY = targetH - originalH * zoom

                if not uncropped_edge or not "u" in uncropped_edge:
                    if (yy > 0):
                        yy = 0

                if not uncropped_edge or not "d" in uncropped_edge:
                    if (yy < maxMoveY):
                        yy = maxMoveY

                img = img.resize(
                    (int(originalW*zoom), int(originalH*zoom)), Image.BILINEAR)
                img = img.crop((
                    int(-xx),
                    int(-yy),
                    int(-xx+128),
                    int(-yy+96)
                ))

                img = img.convert("RGBA")
                data = img.tobytes("raw", "RGBA")
                qimg = QImage(
                    data, img.size[0], img.size[1], QImage.Format.Format_RGBA8888)

                # copy(): own the pixels, `data` does not outlive this call.
                images.append(qimg.copy())
            except Exception:
                logger.error(traceback.format_exc())
                images.append(None)

        return (generation, [(allItem[i], images[i]) for i in range(len(allItem))])

    def LoadSkinImagesComplete(self, result):
        # GUI thread (handed over by the LoadSkinImages decode).
        try:
            if result is None:
                return
            generation, pairs = result
            if generation != self._skinIconGeneration:
                # The skin models were rebuilt while this decode ran: its
                # items belong to a retired generation.
                return
            fallback = None
            for item, image in pairs:
                if image is None:
                    if fallback is None:
                        fallback = QIcon(
                            QPixmap(CANCEL_ICON_PATH).scaled(32, 32))
                    icon = fallback
                else:
                    icon = QIcon(QPixmap.fromImage(image))
                if not icon.isNull():
                    item.setIcon(icon)
        except Exception:
            logger.error(traceback.format_exc())
            return (None)

    # ── lookups (delegated to the catalog) ──────────────────────────────

    def GetCharacterAssets(self, characterCodename: str, skin: int, assetpack: str = None):
        return self.catalog.CharacterAssets(characterCodename, skin, assetpack)
