"""The game loader both shells share (ADR-005 S-final SF-6b; on its own
thread from end to end since SF-6c).

Until SF-6b the desktop's asset adapter (src/AutonaGameAssetManager.py) and
the headless loader (src/headless.HeadlessGames) each held a copy of the
same design (S3f-4, S3f-6), and they had drifted apart. SF-6b made the
desktop's the one loader of both; SF-6c removed the adapters with what they
still did (their Qt signals and the marshal onto the main thread): the
loader is bootstrap_core()'s (`GameLoader.instance`), and it tells what
happened through core's channels (`loaded`, `scanned`), which the Qt bridge
turns into signals (StateManagerBridge.game_loaded / games_scanned).

What it does:

- the startup (Start): the start.gg character table refreshed, the first
  scan -- the games an asset job moved aside put back first -- and, once,
  the last pick restored after it (SELECTED_GAME_CODENAME);
- the scan (Scan; AssetPackService's after an install or import): the
  catalog's ScanGames on the calling job, `scanned` told (the web server's
  games_changed), then on the main thread the provider's game re-applied
  (`afterScan`) and the startup restore;
- the loads (LoadGameByCodename), on one TaskExecutor job at most -- the
  loader -- which takes the latest request until none waits: a request
  still waiting when a newer one comes is not loaded (SUPERSEDED);
- one load, on the loader's thread: the selection in one save cycle
  (_SaveCycle), then what follows it -- the roster, the set length and
  their commit, the pick persisted -- as one board operation, recorded
  (StateManager.RecordHeldWrites), the cycle closed at its end, and once
  that operation is over `loaded` told (the player DB's refresh, the web
  server's push) unless the app is going away (_Publish);
- the modded content turned off when a tournament is loaded (TurnModsOff):
  an intent the loader holds and applies to the loads it takes next (below).

Nothing here waits for the main thread (SF-6c): what follows a selection
ran there, handed over and waited for (run_sync), until then, and a load
the shutdown caught in that wait was settled by a copy of it
(_SettleStopped). The one way onto the main thread is `later`, not waited
for: what follows a scan.

Injected (core imports no Qt and no web layer): `runOnBoard(op,
atShutdown=False)` (the board host's Run), `later(fn)` (run on the main
thread once its event loop is back, not waited for), `unavailable()` (why a
call posted to the main thread now would not run, or None: the app is going
away), `isMainThread()`, and optionally `afterScan()`.
bootstrap.make_game_loader builds one with the shells' common hooks.
"""
import threading
import traceback
from concurrent.futures import Future

from loguru import logger

from .Observable import ObserverChannel
from .SettingsManager import SettingsManager
from .StateManager import StateManager
from .StatusService import StatusService
from .TaskExecutor import TaskExecutor


# How a load request's attempt went: the result of the Future
# LoadGameByCodename returns.
LOADED = "loaded"
SKIPPED = "skipped"         # the catalog did not select (see SelectGame)
SUPERSEDED = "superseded"   # a newer load was asked for before it ran
STOPPED = "stopped"         # the app is shutting down
FAILED = "failed"           # logged

# Where the last pick is kept between launches.
SELECTED_GAME_SETTING = "SELECTED_GAME_CODENAME"


class _LoadRequest:
    """One LoadGameByCodename call (or a load the loader asks for itself:
    TurnModsOff's): what it asked for, its done(), its generation (each
    request takes the next) and the Future it returned, which gets the
    attempt's outcome."""

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
    closed there too: at the end of _Publish, or by the load when _Publish
    did not get to it (skipped, failed first). One BlockSaving, then one
    ReleaseSaving (agent-pitfalls #6), whichever caller gets to it first --
    under a lock, so two closes never both release (one thread closes it
    since ADR-005 S-final SF-6c, when the main thread stopped closing it in
    the marshalled _Publish; the lock costs nothing and keeps the close
    safe for a caller on another thread: SF-6c review). The close
    publishes -- unless another thread's SaveBlock is open then: the last
    block to close publishes, on its thread."""

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


class GameLoader:
    # bootstrap_core()'s, for both shells (ADR-005 S-final SF-6c; each
    # shell's adapter held its own until then).
    instance = None

    # Core's notifications (ADR-005 S-final SF-6c; the shells' Qt signals
    # until then), class channels as AutonaActions.battleFormatChanges: the
    # Qt bridge subscribes when it is built, before the loader is, and turns
    # them into StateManagerBridge.game_loaded / games_scanned.
    #
    # `loaded(snapshot)`: a load is published -- the selection, the roster
    # and its commit, the pick persisted, the save cycle closed -- on the
    # loader's thread, outside the board host's lock and the load's
    # recording (what a subscriber writes is not the load's); not told for a
    # load the shutdown came to first (STOPPED). The snapshot is the load's own
    # GameAssetSnapshot (the catalog's Current() can be a later load's by
    # the time a subscriber reads it).
    loaded = ObserverChannel("game_loaded")
    # `scanned()`: the games were scanned, on the scan's job, ahead of the
    # provider's game and the startup restore (the web server's
    # games_changed: ADR-005 S-final SF-3a).
    scanned = ObserverChannel("games_scanned")

    def __init__(self, catalog, *, runOnBoard, later, unavailable,
                 isMainThread, afterScan=None):
        self.catalog = catalog
        self._runOnBoard = runOnBoard
        self._later = later
        self._unavailable = unavailable
        self._isMainThread = isMainThread
        self._afterScan = afterScan

        # The loads (ADR-005 S3f-4 review): one loader job at most, taking
        # the latest request until none is waiting (_LoaderJob). Under
        # _requestLock -- a Condition, which a synchronous request waits on:
        # the generation of the latest request (each takes the next), the
        # request waiting for the loader (None: none; a newer one replaces
        # it), whether a loader job is submitted and not over, that job's
        # Future (None before the first), the generation of the latest
        # request the loader is over with, and the modded content's intent
        # (TurnModsOff: the generation of the latest request when the mods
        # were turned off while the loader ran; None: none).
        self._requestLock = threading.Condition()
        self._loadGeneration = 0
        self._pendingLoad = None
        self._loaderRunning = False
        self._loadFuture = None
        self._finishedGeneration = 0
        self._modsOffGeneration = None

        # The startup restore of the last pick (_RestoreSelectedGame), armed
        # by Start() so that a later rescan (an asset pack's install or
        # import) never overrides the operator's pick.
        self._pendingRestore = False

    # ── startup / scan ──────────────────────────────────────────────────

    def Start(self):
        """The startup's, once (the window's UiMounted, the headless
        Assemble): the start.gg character table refreshed, the first scan,
        and the last pick restored after it. Not from a job lane (it
        submits the scan)."""
        self.DownloadStartGGCharacters()
        self._pendingRestore = True
        TaskExecutor.instance.submit(self._FirstScan, task_name="game scan")

    def DownloadStartGGCharacters(self):
        # The start.gg character table refresh: a job (network; it writes
        # the file only, the next load reads it).
        TaskExecutor.instance.submit(
            self.catalog.DownloadStartGGCharacters,
            task_name="start.gg characters download")

    def Scan(self):
        """The scan, on the calling thread -- a job: the startup's, or an
        asset job's (AssetPackService.scan; a job may not submit another,
        so it scans on its own). `scanned` is told from here (the web
        server's games_changed; the shells' onLoadAssets / gamesScanned
        until ADR-005 S-final SF-6c), then the main thread re-applies the
        provider's game (`afterScan`) and, after the first scan, the last
        pick -- in that order, so the restore wins. A scan that raises is
        logged by its job and asks for nothing."""
        self.catalog.ScanGames()
        self.scanned.notify()
        self._later(self._OnScanned)

    def _FirstScan(self):
        # A process that died between an asset job's two moves left an
        # installed game moved aside (user_data/games/.autona-replaced-*);
        # it is put back before the first scan lists the games
        # (AssetPackService.RestoreReplaced, ADR-005 S-final SF-3b; it never
        # waits for a running job, which does it itself, and logs its own
        # failures). The upload an earlier run left (user_data/
        # .autona-uploads; SF-4b-1) is removed beside it (RemoveStaleUploads,
        # which logs its own failures too). The service is bootstrap_core()'s.
        from .AssetPackService import AssetPackService
        if AssetPackService.instance is not None:
            AssetPackService.instance.RestoreReplaced()
            AssetPackService.instance.RemoveStaleUploads()
        self.Scan()

    def _OnScanned(self):
        # On the main thread after a scan: the provider's game first, then
        # the startup restore (the desktop's onLoadAssets slots ran in that
        # order until SF-6b: bootstrap_gui connected the provider first).
        # The restore runs whatever the provider's step does, as it did when
        # each was a slot of its own (SF-6b review).
        if self._afterScan is not None:
            try:
                self._afterScan()
            except Exception:
                logger.exception("The provider's game could not be applied "
                                 "after the scan")
        self._RestoreSelectedGame()

    def _RestoreSelectedGame(self):
        # The pick is persisted locally (PersistSelectedGame) rather than
        # re-derived from the restored tournament's start.gg videogame id,
        # which made the game depend on a network round trip: none for the
        # first seconds of every launch, none at all offline. Not the state:
        # bootstrap clears `game`, and out/program_state.json is an export.
        if not self._pendingRestore:
            return
        self._pendingRestore = False

        codename = SettingsManager.Get(SELECTED_GAME_SETTING, "") or ""
        if not codename:
            return
        if codename not in self.catalog.GameCodenames():
            logger.info(
                f"Last used game '{codename}' is not installed anymore - "
                "skipping restore")
            return
        # Mods stay off on restore: the AppShell's switch shows
        # game.mods_active.
        self.LoadGameByCodename(codename)

    @staticmethod
    def PersistSelectedGame():
        """The loaded game as the next launch's pick: written by every
        published load, inside its board operation after the roster's
        commit, on the loader's thread (_Publish; the first slot of the
        shells' announcement until ADR-005 S-final SF-6c). SettingsManager
        takes its own lock, after the board host's."""
        SettingsManager.Set(
            SELECTED_GAME_SETTING,
            StateManager.Get("game.codename", "") or "")

    # ── the loads ───────────────────────────────────────────────────────

    def SelectGameFromStartGGId(self, gameid, done=None):
        """The tournament provider's selector: a start.gg videogame id ->
        the game. `done()` as LoadGameByCodename has it: at once for a game
        that is not one of ours, which asks the loader nothing."""
        codename = self.catalog.GameFromStartGGId(gameid)
        if codename or done is not None:
            self.LoadGameByCodename(codename or "", done=done)

    def LoadGameByCodename(self, codename, async_mode=True, mods_active=False,
                           mods_reload_mode=False, done=None):
        """Load `codename` (ADR-005 S3f-4): on the loader, a job of the
        TaskExecutor -- one at most, however many requests come. Returns a
        Future, whose result says how the attempt went (LOADED / SKIPPED /
        SUPERSEDED / STOPPED / FAILED); it cannot be cancelled (cancel() is
        False: the attempt is the loader's).

        `async_mode` (the default): returns at once. An empty codename ("no
        game": the tournament URL cleared, which keeps what is loaded) is
        SKIPPED at once, done() and all, on the calling thread. Off: returns
        once the attempt is over (below) -- from a thread other than the
        main one only (a web request: AutonaWebOperations.set_game); on the
        main thread it raises RuntimeError: its event loop would stop for
        the whole load, and a caller inside a board operation would wait
        for a loader waiting for its lock. Never make one from inside a
        board operation (the board host's Run) on any thread, nor holding
        StateManager.lock: it waits for the loader, whose _Publish waits
        for those locks -- a deadlock the guard does not catch, as it looks
        at the main thread only (ADR-005 S-final SF-6c review). Over means
        published: the game, its roster and the pick are in by then; what
        `loaded` sets off on the main thread (the web push, the player DB's
        refresh) is queued there and may still be to come (ADR-005 S-final
        SF-6c; it had run too until then, when the main thread ran what
        follows a load).

        The latest request wins: a request still waiting for the loader when
        a newer one comes is not loaded (SUPERSEDED, nothing selected), so
        rapid requests A, B, C end on C. A load already running when C comes
        finishes first. A newer request for the same game keeps the reload
        the one it supersedes asked for, with the newer one's mods (ADR-005
        S-final SF-5b-1). A synchronous request is over once the loader is
        over with it, or with a request made after it, whatever that
        attempt's outcome: set_game's "OK" means the attempt is over.

        `done()`, if given, is called once the attempt is over however it
        went -- loaded, skipped by the catalog (an unknown codename; the
        same game without reload), failed, not run because the app is
        shutting down, or superseded (/api/v1 releases its in-progress flag
        there). It runs on whichever thread ends the attempt: the loader's,
        the superseding request's, the shutdown's for a loader cancelled
        while queued, the lane's for one that died, or the calling one. So
        it must be quick and must not ask for another load (from a job lane
        with no loader running, a request cannot start one:
        NestedSubmitError; a synchronous one would wait for the loader it
        runs on). A request the loader cannot be started for (the executor
        refuses the job) raises, with nothing recorded and no done().
        """
        if not codename:
            request = _LoadRequest(codename, mods_active, mods_reload_mode,
                                   done)
            self._Settle(request, SKIPPED)
            return request.future
        if not async_mode and self._isMainThread():
            raise RuntimeError(
                "a synchronous game load waits for the loader: it cannot be "
                "made from the main thread, whose event loop would stop for "
                "the whole load")

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
            if superseded is not None and superseded.codename == codename:
                request.reload = request.reload or superseded.reload
            if not self._loaderRunning:
                # Cancelled as it was submitted (the executor has shut
                # down): _LoaderEnded ran already, and nothing will take it.
                orphan, self._pendingLoad = request, None
        if superseded is not None:
            logger.info(f"Game load of {superseded.codename} skipped: "
                        f"{codename} was asked for after it")
            self._Settle(superseded, SUPERSEDED)
        if orphan is not None:
            logger.info(f"Game load of {codename} not run: the app is "
                        "shutting down")
            self._SettleAndFinish(orphan, STOPPED)
        if not async_mode:
            self._WaitForLoad(request.generation)
        return request.future

    def TurnModsOff(self):
        """Have the game the loader ends on loaded without its modded
        content (ADR-005 S-final SF-5b-1: a tournament newly loaded --
        TournamentService, through AutonaWebOperations.turnModsOff).
        Returns at once. Not from a job lane (it may start the loader).

        With no loader running, the selected game is asked for again
        without them when they are on. With one running, the intent is
        held (SF-6b) and applied by the loader to what it takes next: every
        request made before this one loads without the mods (a reload, too,
        when it would be skipped as the selected game while that has them
        on), and so does a later one that does not ask for them; a later
        one that asks for them wins, and drops the intent. Once nothing is
        waiting, the game the loader ends on is asked for again without
        them if it still has them on -- the last load failed, say, or ran
        with them -- and the intent is dropped. So no request is dropped
        or put in front of one made before it, and no game is loaded again
        after another one. (SF-5b-1 decided it from the waiting and the
        running request instead -- GameAssetCatalog.PlanModsOff -- and a
        game a later request skipped kept its mods, as did a failed load's
        game: the ledger's risks (a) and (b).)"""
        with self._requestLock:
            if self._loaderRunning:
                self._modsOffGeneration = self._loadGeneration
                logger.info("The modded content goes off with the game the "
                            "loader ends on")
                return
            current = self.catalog.Current()
            if not current.codename or not current.modsActive:
                return
            logger.info(f"{current.codename} asked for again without its "
                        "modded content")
            # Asked under the lock (re-entered), so no request comes before
            # it; nothing waits, so it supersedes nothing.
            self.LoadGameByCodename(current.codename, mods_active=False,
                                    mods_reload_mode=True)

    def _ApplyModsOff(self, request):
        # Under _requestLock, as the loader takes `request`: the modded
        # content's intent (TurnModsOff), applied to it.
        intent = self._modsOffGeneration
        if intent is None:
            return
        if request.modsActive and request.generation > intent:
            # Asked for with the mods after they were turned off: the later
            # word wins.
            self._modsOffGeneration = None
            return
        current = self.catalog.Current()
        changed = request.modsActive
        request.modsActive = False
        if (not request.reload and request.codename == current.codename
                and current.modsActive):
            # The selected game again: SelectGame would skip it, mods on.
            request.reload = True
            changed = True
        if changed:
            logger.info(f"Game load of {request.codename} asked for without "
                        "its modded content")

    def _ModsOffRequest(self):
        # Under _requestLock, nothing waiting: the intent's last word -- the
        # game the loader ends on, again without the mods if it has them
        # (the last load failed, or ran with them). Dropped either way, so a
        # reload that fails is not asked for again. None: nothing to load.
        intent, self._modsOffGeneration = self._modsOffGeneration, None
        if intent is None:
            return None
        current = self.catalog.Current()
        if not current.codename or not current.modsActive:
            return None
        logger.info(f"{current.codename} asked for again without its "
                    "modded content")
        request = _LoadRequest(current.codename, False, True, None)
        self._loadGeneration += 1
        request.generation = self._loadGeneration
        return request

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
        # (logged by its lane): the request it would have taken is over, and
        # so is the modded content's intent.
        with self._requestLock:
            if self._loadFuture is not job or not self._loaderRunning:
                return
            self._loaderRunning = False
            self._modsOffGeneration = None
            orphan, self._pendingLoad = self._pendingLoad, None
        if orphan is not None:
            cancelled = job.cancelled()
            logger.info(f"Game load of {orphan.codename} not run: the "
                        "loader " + ("was cancelled" if cancelled
                                     else "failed"))
            self._SettleAndFinish(orphan, STOPPED if cancelled else FAILED)

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
        # is waiting (and the modded content's intent has had its last
        # word). One at a time by construction (LoadGameByCodename starts it
        # only when none runs), so a load is over -- the next may select a
        # game -- once what follows its selection is, and the requests that
        # wait take no job lane (each used to be a job of its own blocked on
        # a lock: rapid switching could take all four lanes from the
        # provider's fetches; ADR-005 S3f-4 review).
        while True:
            applied = True
            with self._requestLock:
                request, self._pendingLoad = self._pendingLoad, None
                if request is not None:
                    try:
                        self._ApplyModsOff(request)
                    except Exception:
                        # Taken, so nothing else would settle it: FAILED,
                        # not run, like a load that raises (SF-6b review).
                        logger.error(traceback.format_exc())
                        applied = False
                else:
                    request = self._ModsOffRequest()
                    if request is None:
                        self._loaderRunning = False
                        return
            outcome = FAILED
            try:
                if applied:
                    outcome = self._RunLoad(request)
            finally:
                # Finished whatever the settle did: a synchronous request
                # waiting for this generation (or an older one) must not
                # wait for ever (ADR-005 S3f-4 review: it did when a caller
                # had cancelled the Future, which made the settle raise).
                self._SettleAndFinish(request, outcome)

    def _RunLoad(self, request):
        # One request's attempt; its outcome.
        #
        # The footer's progress (StatusService, ADR-005 S-final SF-2):
        # called by the catalog under its writer lock, it only records and
        # tells a channel whose one subscriber queues a Qt signal, so it
        # never blocks. Pushed on the first report, so a load the catalog
        # skips shows nothing; updated every 5 characters. (The headless
        # loader showed none until SF-6b.)
        progress = StatusService.Progress(
            "assets_load", {"codename": request.codename}, every=5)
        try:
            # Checked before anything is written: once the app is shutting
            # down, a load that waited for the loader is not started (the
            # shutdown is waiting for the loader to end; the load would only
            # hold it up). One the shutdown comes to after its selection is
            # finished and published all the same, unannounced (_Publish).
            reason = self._unavailable()
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
        # The loader's load, all of it on the loader's thread (ADR-005
        # S-final SF-6c; what follows the selection was marshalled onto the
        # main thread until then): the selection, then _Publish. Returns the
        # outcome.
        #
        # One save cycle for the whole load -- the selection's writes and
        # those of what follows it (the roster and its commit) -- opened
        # here and closed at the end of _Publish, so the cycle normally
        # publishes there, on this thread, and its notifications
        # (state_updated, the held ones) run there inside StateManager.lock
        # (agent-pitfalls #17); the bridge queues them to the main thread's
        # receivers. Not when another thread's SaveBlock is open at that
        # moment: the last block to close publishes, on its own thread. One
        # BlockSaving, one ReleaseSaving on every path (agent-pitfalls #6):
        # _SaveCycle releases once, from _Publish or, when it did not get to
        # it (skipped, failed first), from here. Other threads' board
        # operations wait while _Publish runs (it holds the board host's
        # lock), and their core-direct writes meanwhile (score:adjust) land
        # in the cycle; neither those nor what is written while this thread
        # selects go out with the load: its commit publishes what the load
        # wrote (_Publish records it). The catalog's writer lock is released
        # by the time the board host's lock is taken (SelectGame returned):
        # no board operation is run under it.
        self.catalog.ReloadStartGGCharacters()
        cycle = _SaveCycle()
        cycle.Open()
        try:
            snapshot = self.catalog.SelectGame(
                codename, modsActive=mods_active, reload=mods_reload_mode,
                progress=progress)
            if snapshot is None:
                return SKIPPED
            stopped = self._Publish(snapshot, cycle)
            if stopped is not None:
                logger.info(f"Game load of {codename} stopped: {stopped}")
                return STOPPED
            return LOADED
        finally:
            cycle.Release()

    def _Publish(self, snapshot, cycle):
        # What follows the selection, on the loader's thread, as one board
        # operation (the board host's Run, under its lock; ADR-005 S4ad
        # review), so no board operation of another thread lands between
        # the roster, the commit and the close -- then, that operation over,
        # the load told:
        #
        # - the roster (AutonaActions.ApplyGameRoster: the boards to the
        #   game's players per team, the previous game's picks emptied on a
        #   switch -- `replaced`, the load's own: a later load's snapshot
        #   could say "no switch") and the set length (RepublishBestOfs),
        #   then their commit (CommitGameLoad), even when they raise -- what
        #   bootstrap.wire_game_roster did on the shells' gameLoaded until
        #   ADR-005 S-final SF-6c;
        # - the pick persisted (the first slot of their onLoad /
        #   gameChanged until then);
        # - the save cycle closed, which publishes what the commit queued;
        # - `loaded` told -- after the close, so the bridge queues the web
        #   server's full-state push behind the load's delta (a push queued
        #   first would reach the clients ahead of that delta), and outside
        #   the board operation and its recording (a subscriber neither holds
        #   the board host's lock nor writes as the load) -- unless the app
        #   is going away (`unavailable()`): returned then, and the load is
        #   STOPPED. A load the shutdown comes to after its selection is
        #   published all the same, so the state -- and state.json -- holds
        #   one game: the selection with its roster and pick (ADR-005 S3f-4
        #   reviews; until SF-6c a copy of this wrote it when the marshal
        #   gave the main thread's call up: _SettleStopped). `atShutdown`:
        #   the board host refuses a board operation off the main thread
        #   once the app is shutting down (the 503 a late request gets), and
        #   this one is a load's that is under way.
        #
        # Returns the reason the load was not told (`unavailable()`'s), or
        # None. The roster's steps and the pick are each isolated as the
        # shells' slots were (emit reported a slot's exception and went
        # on): logged, and the load goes on -- LOADED. What else raises in
        # here fails the load (FAILED); the cycle is closed all the same.
        #
        # Nothing in here waits for another thread (the roster takes the
        # state's lock, the pick the settings'; both come after the board
        # host's). What another thread writes meanwhile (a core-direct
        # write: score:adjust), or wrote while the loader selected, is not
        # the load's: the commit publishes what the load wrote -- recorded
        # here from the roster to the close (StateManager.RecordHeldWrites)
        # -- and leaves the rest behind 反映. The load's writes are told by
        # thread: a write from another thread is someone else's.
        from .Actions import AutonaActions
        actions = AutonaActions.instance
        replaced = getattr(snapshot, "replaced", None)
        stopped = []

        def publish():
            with StateManager.RecordHeldWrites() as written:
                try:
                    try:
                        try:
                            actions.ApplyGameRoster(replaced)
                            actions.RepublishBestOfs()
                        finally:
                            # Committed whatever the roster did: a load's
                            # writes are not left waiting behind 反映.
                            actions.CommitGameLoad(written)
                    except Exception:
                        logger.exception(
                            "The loaded game's roster could not be written")
                    try:
                        self.PersistSelectedGame()
                    except Exception:
                        logger.exception(
                            "The loaded game could not be kept for the "
                            "next launch")
                finally:
                    cycle.Release()

        self._runOnBoard(publish, atShutdown=True)
        reason = self._unavailable()
        if reason is not None:
            return reason
        self.loaded.notify(snapshot)
        return None
