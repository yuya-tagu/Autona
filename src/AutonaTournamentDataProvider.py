"""The tournament provider's Qt adapter: the loaded provider, its signals,
and the fetches the app asks it for.

Every fetch is a job of the core task executor (`TaskExecutor.instance`,
ADR-005 S3f-2; until then a Worker on a QThreadPool of this class). A
fetch's answer is handed to the GUI thread through the long-lived GUI
executor (`AutonaQtHelper.gui_executor`) from the job's `then`, and the
signal is emitted there -- the signals, their arguments, how often and on
which thread they fire are what they were. Nothing per call has to live for
the answer to arrive: a Worker's signals object did, and the garbage
collector could take it before the queued answer was delivered (the answer
was then silently lost).

The provider methods are called as the Worker called them: with a
`progress_callback` and a `cancel_event`. The requests a provider method
makes in parallel go to the executor's fan-out lane (S3f-3). A job never
waits on the GUI thread.
"""
import threading
import time
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from .core.AnalysisService import AnalysisService
from .core.SetSelectionService import SetSelectionService
from .core.StateManager import StateManager
from .core.StreamService import StreamService
from .core.TaskExecutor import TaskExecutor
from .core import TournamentService as tournament_service
from .core.TournamentService import TournamentService
from .AutonaPlayerDB import AutonaPlayerDB
from .Helpers import AutonaQtHelper
from .TournamentDataProvider.TournamentDataProvider import TournamentDataProvider
from .TournamentDataProvider.StartGGDataProvider import StartGGDataProvider
from .TournamentDataProvider.StartGGDataProvider import (
    ResolveEventUrl as ResolveStartGGEventUrl)
from .Helpers.AutonaVersionHelper import get_supported_providers
from loguru import logger


def _NoProgress(*args):
    pass


def _OnGuiThread(fn, *args):
    """`fn(*args)` on the GUI thread, not waited for (a job's answer)."""
    AutonaQtHelper.gui_executor(lambda: fn(*args))


def _Stopping(executor):
    """Whether `executor` is stopping: the app is quitting, and a request
    the shutdown cut short answered {} or an empty list. That is no answer
    -- written, it would empty the tournament info or a statistic during
    the shutdown's wait -- so a job's answer found on its own thread after
    the stop is dropped (ADR-005 S3f-2 review). An answer made before the
    stop is a real one and is delivered."""
    if executor.stopping.is_set():
        logger.info("A tournament fetch answered while the app is quitting: "
                    "dropped")
        return True
    return False


class _UnlessStopping:
    """The `callback` (anything with an emit) a provider method answers on
    itself, as a job hands it over: its answer is dropped once the executor
    is stopping (see _Stopping)."""

    def __init__(self, callback, executor):
        self._callback = callback
        self._executor = executor

    def emit(self, *args):
        if not _Stopping(self._executor):
            self._callback.emit(*args)


class AutonaTournamentDataProviderSignals(QObject):
    tournament_changed = Signal()
    tournament_data_updated = Signal(dict)
    twitch_username_updated = Signal()
    get_sets_finished = Signal(list)
    get_stations_finished = Signal(list)
    tournament_phases_updated = Signal(list)
    game_changed = Signal(int)
    stream_queue_loaded = Signal(dict)
    sets_data_updated = Signal(dict)
    tournament_url_update = Signal(str)


class AutonaTournamentDataProvider(QObject):
    instance: "AutonaTournamentDataProvider" = None

    def __init__(self) -> None:
        super().__init__(None)
        self.provider: TournamentDataProvider = None
        self.signals: AutonaTournamentDataProviderSignals = AutonaTournamentDataProviderSignals()

        self.signals.game_changed.connect(self.GameChanged)

        # How a start.gg videogame id becomes the loaded game. Registered by
        # the shell (bootstrap_gui() points it at the game asset adapter,
        # which also re-applies the provider's game after every scan); the
        # provider no longer knows the adapter, so it can be built without
        # one (ADR-005 S3c).
        self.selectGameFromStartGGId = None

        # The set list load (LoadSets): only the newest one answers. Its
        # number, and the event that tells the one before it to stop. GUI
        # thread only (the window that asks for the list and the delivery
        # of the answers).
        self._setLoadGeneration = 0
        self._setLoadCancel = None
        # The entrants fetch of the tournament loaded now: the event that
        # tells it to stop once another tournament (or none, or the same
        # one again) is loaded. GUI thread only (Load / UnloadTournament).
        self._entrantsCancel = None

    def GameChanged(self, videogame):
        StateManager.Set(f"provider_videogame", {
            "id": videogame
        })
        self.SetGameFromProvider()

    def SetGameFromProvider(self):

        if not self.provider or not self.provider.videogame:
            return

        if "start.gg" in self.provider.url:
            if self.selectGameFromStartGGId is None:
                # A shell that loads tournaments must register one; silence
                # here would mean "the tournament's game never loads" with
                # nothing in the log.
                logger.warning(
                    "no game selector registered; provider game "
                    f"{self.provider.videogame} not applied")
                return
            self.selectGameFromStartGGId(self.provider.videogame)
        else:
            logger.error("Unsupported provider...")

    # ── the tournament's set / clear (core/TournamentService.py, S4f) ────
    #
    # Every entrance -- the sidebar's URL dialog and confirmation below,
    # /api/v1 (the Event page's, ADR-005 S4g), the legacy route, Socket.IO
    # and the launch's restore -- goes through TournamentService, which
    # saves the URL, resolves an admin URL in a job and forgets the selected
    # game on every clear. These are what it asks this adapter for, on the
    # main thread. tournament_url_update is the announcement it makes (no
    # longer a command): the sidebar's buttons follow it.

    def LoadTournament(self, url, initialLoading=False, force=False):
        """Load the start.gg event at `url` (already resolved and saved by
        TournamentService): its data, phases and entrants are fetched, and
        tournament_changed is emitted. The loaded URL again does nothing
        unless `force` (a re-fetch on demand). True when it (re)loaded.
        Main thread."""
        if not force and self.provider and self.provider.url == url:
            return False
        self._CancelEntrants()
        self.provider = StartGGDataProvider(url, self)
        # The match list is the tournament's (ADR-005 S4j): dropped once
        # the new provider is in place -- a Refresh made before this (on
        # any thread, from the provider replaced) is dropped with it, and
        # one made after fetches from the new one (S4j review M2). The
        # panel used to drop it on tournament_changed.
        self._ForgetAnalysis()
        self._ForgetSetSelection()

        self.GetTournamentData(initialLoading=initialLoading)
        self.GetTournamentPhases()

        self._StartEntrants()
        self.signals.tournament_changed.emit()
        return True

    def UnloadTournament(self):
        """No tournament loaded: the tournament info is emptied (an empty
        tournament_data_updated), so are the phases, and the name completers
        offer the whole player DB again. The game stays loaded; forgetting
        the selection is TournamentService's. Main thread."""
        self._CancelEntrants()
        self.provider = None
        # After the provider is gone, as LoadTournament does.
        self._ForgetAnalysis()
        self._ForgetSetSelection()
        AutonaPlayerDB.ClearTournamentPlayers()
        self.signals.tournament_data_updated.emit({})
        self.signals.tournament_phases_updated.emit([])
        self.signals.tournament_changed.emit()

    def AnnounceUrl(self, url):
        """Tell the shell the tournament URL was set ("" : cleared) --
        tournament_url_update, after the load / the clear. Main thread."""
        self.signals.tournament_url_update.emit(url or "")

    @staticmethod
    def ResolveEventUrl(url):
        """The event URL a start.gg admin/brackets URL stands for (a start.gg
        request, blocking: TournamentService's job, never the GUI thread);
        `url` itself when it could not be resolved. No provider is built
        for it (a QObject, on the job's thread)."""
        return ResolveStartGGEventUrl(url)

    def _StartEntrants(self):
        """Fetch the loaded tournament's entrants (the provider's own job),
        with a cancel event of its own. GUI thread."""
        cancel = self._entrantsCancel = threading.Event()
        self.provider.GetEntrants(cancel_event=cancel)

    def _CancelEntrants(self):
        """Tell the entrants fetch of the tournament loaded until now to
        stop: it stops between pages and leaves the player DB's tournament
        filter alone -- tags it already handed over included, which the GUI
        thread drops (AutonaPlayerDB.SetTournamentPlayers). Otherwise a
        replaced tournament's fetch kept paging and finally installed its
        players over the new tournament's (or over the cleared filter).
        GUI thread.

        Called whenever the loaded tournament is about to be replaced (or
        unset), so the stream title's top entrants go with it (ADR-005 S4d;
        the panel used to read them off the new provider, empty until its
        fetch was done). Set before the emptying, the cancel event also
        stops a fetch that is just handing its entrants over
        (StreamService.SetTopEntrants)."""
        if self._entrantsCancel is not None:
            self._entrantsCancel.set()
            self._entrantsCancel = None
        if StreamService.instance is not None:
            StreamService.instance.SetTopEntrants([])

    @staticmethod
    def _ForgetAnalysis():
        """Drop the match list (AnalysisService.Forget): its fetch in
        progress and its rows are the tournament loaded until now. Called
        directly, not through a state observer, right after the provider
        is replaced. GUI thread."""
        if AnalysisService.instance is not None:
            AnalysisService.instance.Forget()

    @staticmethod
    def _ForgetSetSelection():
        """The set selection's lists the same way (SetSelectionService.Forget,
        ADR-005 S4v). GUI thread."""
        if SetSelectionService.instance is not None:
            SetSelectionService.instance.Forget()

    def GetBannerURL(self):
        """The loaded tournament's banner URL; None with no tournament or
        none found. Blocking (a start.gg request): the stream thumbnail
        job's banner lookup (ADR-005 S4d), never for the GUI thread."""
        provider = self.provider
        if provider is None:
            return None
        return provider.GetBannerURL()

    def SetStartggEventSlug(self, mainWindow):
        """The tournament URL dialog (the sidebar's "Set tournament"),
        prefilled with the URL set last. Accepted, it hands the URL to
        TournamentService -- re-fetched even when it is the loaded one. True
        when accepted."""
        inp = QDialog(mainWindow)

        layout = QVBoxLayout()
        inp.setLayout(layout)

        inp.layout().addWidget(QLabel(
            QApplication.translate("app", "Paste the tournament URL.")+ "\n" + QApplication.translate("app", "For StartGG, the link must contain the /event/ part") + "\n" + QApplication.translate("app", "Supported providers:") + " " + ", ".join(get_supported_providers())

        ))

        lineEdit = QLineEdit()
        okButton = QPushButton("OK")

        def validateText():
            # The forms core takes (TournamentService.UrlKind): the event
            # URL, or the admin/brackets one, resolved by core.
            okButton.setDisabled(
                tournament_service.UrlKind(lineEdit.text()) is None)

        lineEdit.textEdited.connect(validateText)

        # Prefill with the currently selected tournament URL (if any) so the
        # operator can just press OK to re-fetch, or edit to switch events.
        lineEdit.setText(TournamentService.TournamentUrl())

        inp.layout().addWidget(lineEdit)

        okButton.clicked.connect(inp.accept)
        validateText()
        inp.layout().addWidget(okButton)

        inp.setWindowTitle(QApplication.translate("app", "Set tournament URL"))
        inp.resize(600, 10)

        accepted = inp.exec() == QDialog.Accepted
        url = lineEdit.text()
        inp.deleteLater()
        if not accepted:
            return False
        # force=True so re-selecting the same tournament still re-fetches.
        return TournamentService.instance.SetTournament(
            url, force=True) == (
            tournament_service.STARTED)

    @staticmethod
    def ConfirmClearTournament(parent):
        """Ask before unsetting the tournament (the sidebar's "Unset
        tournament": owner's decision 2, S4f -- the Event page asks its own),
        then hand it to TournamentService, which also forgets the selected
        game. Call from a click / a menu action, never from a state or a
        value signal (the modal rule). True when cleared."""
        answer = QMessageBox.question(
            parent,
            QApplication.translate("app", "Confirm"),
            QApplication.translate(
                "app", "Start.gg との接続を解除します。よろしいですか？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        TournamentService.instance.ClearTournament()
        return True

    @staticmethod
    def _Fetch(taskName, fetch, then=None, **kwargs):
        """Run the provider method `fetch(**kwargs)` as a job of the task
        executor, with the progress callback and cancel event a provider
        method takes (unless given), and hand its result to `then` on the
        GUI thread (ADR-005 S3f-2). A fetch that raises is logged once by
        the executor, and `then` is not called (as a Worker's `result` was
        not emitted); nor is it once the executor is stopping (_Stopping).
        A provider's own `callback` is handed over the same way. The job's
        Future."""
        executor = TaskExecutor.instance
        kwargs.setdefault("progress_callback", _NoProgress)
        kwargs.setdefault("cancel_event", threading.Event())
        if kwargs.get("callback") is not None:
            kwargs["callback"] = _UnlessStopping(kwargs["callback"], executor)
        deliver = None
        if then is not None:
            def deliver(result):
                if not _Stopping(executor):
                    _OnGuiThread(then, result)
        return executor.submit(
            fetch, task_name=taskName, then=deliver, **kwargs)

    def GetTournamentData(self, initialLoading=False):
        provider = self.provider
        if provider is None:
            logger.warning("GetTournamentData called with no provider set")
            return
        self._Fetch(
            "tournament data", provider.GetTournamentData,
            lambda tournamentData: self._onTournamentDataResult(
                tournamentData, initialLoading, provider))

    def RefreshTournamentData(self, done):
        """The tournament's data asked for again and applied, as
        GetTournamentData does, and then the game it names seen loaded (a load
        is the loader's job, and ends later): `done()`, on the GUI thread, once
        that is over however it went -- the data could not be fetched, the
        tournament was replaced meanwhile, the game is not one of ours -- so
        that what comes next reads the game of this tournament
        (ResultService.LoadStandings, ADR-005 S4q review). Once the app is
        quitting it may not come.

        True if it was started; False, calling nothing, when no tournament is
        loaded or the executor has shut down."""
        provider = self.provider
        if provider is None:
            return False

        def fetch(**kwargs):
            # Not left to the executor: `then` is not called after a raise.
            try:
                return provider.GetTournamentData(**kwargs)
            except Exception:
                logger.opt(exception=True).warning(
                    "The tournament's data could not be fetched again")
                return None

        def gameLoaded():
            # On whichever thread ended the attempt (LoadGameByCodename).
            _OnGuiThread(done)

        def applied(tournamentData):
            try:
                if tournamentData:
                    self._onTournamentDataResult(tournamentData, False,
                                                 provider)
                if provider is self.provider and provider.videogame \
                        and self.selectGameFromStartGGId is not None:
                    self.selectGameFromStartGGId(provider.videogame,
                                                 done=gameLoaded)
                    return
            except Exception:
                logger.exception("The tournament's data could not be applied")
            done()

        future = self._Fetch("tournament data", fetch, applied)
        return not future.cancelled()

    def _onTournamentDataResult(self, tournamentData, initialLoading,
                                provider):
        """The tournament data `provider` answered, on the main thread.

        Dropped unless `provider` is still the one loaded (ADR-005 S4g
        review M2): a tournament replaced (or unloaded, or loaded again by
        force: a new provider) while its data was on the way would otherwise
        write its tournamentInfo.* over the new one's -- and settle the new
        one's status (TournamentService.PayloadApplied) with its own."""
        if provider is not self.provider:
            logger.info("Tournament data of a tournament no longer loaded: "
                        "dropped")
            return
        # Emit game_changed here (main thread) instead of from worker thread
        videogame = tournamentData.pop("_videogame", None)
        if videogame:
            self.signals.game_changed.emit(videogame)
        tournamentData["initial_load"] = initialLoading
        self.signals.tournament_data_updated.emit(tournamentData)

    def GetTournamentPhases(self):
        if self.provider is None:
            return
        self._Fetch(
            "tournament phases", self.provider.GetTournamentPhases,
            AutonaTournamentDataProvider.instance.signals
            .tournament_phases_updated.emit)

    def RunWithProvider(self, job, status=None):
        """Run `job(provider)` as a job of the task executor: the core's way
        to the tournament (Top8Service.fetcher, ADR-005 S3e-8; the set
        loads and follows of AutonaSetFollowUtil, S3e-7). On the provider's
        thread pool until ADR-005 S3f-2.

        The provider is the one loaded now, read once here. `status`, when
        given, is shown in the footer while the job runs. True if the job
        was started; False, running nothing, when no tournament is loaded
        or the executor has shut down (the app is quitting). A started job
        is always called, exactly once -- unless the shutdown cancels it
        while it is still queued: the status is only a display, and a
        failure to show it must not skip the job (the Top 8 import releases
        its in-progress flag there, and nothing else would). The job runs
        off the GUI thread and touches no widget; its errors are logged
        here.
        """
        provider = self.provider
        if provider is None:
            return False

        def run():
            statusManager = token = None
            try:
                # Imported here, not at module level: the status manager
                # builds its signal QObject on import (agent-pitfalls #1).
                from .Helpers.AutonaStatusManager import AutonaStatusManager
                statusManager = AutonaStatusManager
                if status:
                    token = statusManager.push(status)
            except Exception:
                logger.exception("could not show a provider job's status")
            try:
                job(provider)
            except Exception:
                logger.exception("a tournament provider job failed")
            finally:
                if token is not None:
                    try:
                        statusManager.pop(token)
                    except Exception:
                        logger.exception(
                            "could not clear a provider job's status")

        future = TaskExecutor.instance.submit(
            run, task_name="tournament provider job")
        return not future.cancelled()

    def LoadSets(self, showFinished, phaseGroupIds=None):
        """Load the set list (the set selection window). Only the newest
        load answers: asking again tells the one before it to stop (its
        cancel event, which GetMatches checks between pages) and drops
        whatever it still delivers -- its progress and its list -- on the
        GUI thread, by the load's number. Until ADR-005 S3f-2 the previous
        Worker's signals were blocked instead, which let an answer already
        queued through. GUI thread."""
        if self.provider is None:
            return
        if self._setLoadCancel is not None:
            self._setLoadCancel.set()
        self._setLoadGeneration += 1
        generation = self._setLoadGeneration
        cancel = self._setLoadCancel = threading.Event()

        def progress(n, t):
            # From the job's thread, page by page: delivered in order, ahead
            # of the list (one queue, the GUI executor's).
            _OnGuiThread(self._OnSetsProgress, generation, n, t)

        self._Fetch("set list", self.provider.GetMatches,
                    lambda data: self._OnSetsLoaded(generation, data),
                    getFinished=showFinished, phaseGroupIds=phaseGroupIds,
                    progress_callback=progress, cancel_event=cancel)

    def CancelSetLoad(self):
        """Stop the set list load still running, if any, and drop whatever
        it still delivers (the set selection window, once a set is picked).
        GUI thread."""
        if self._setLoadCancel is not None:
            self._setLoadCancel.set()
            self._setLoadCancel = None
        self._setLoadGeneration += 1

    def _OnSetsProgress(self, generation, n, t):
        if generation != self._setLoadGeneration:
            return
        logger.info(f"SetDataUpdated: {n}/{t}")
        self.signals.sets_data_updated.emit({
            "progress": n,
            "totalPages": t,
            "sets": []
        })

    def _OnSetsLoaded(self, generation, data):
        if generation != self._setLoadGeneration:
            logger.info("Sets loaded by a superseded load: dropped")
            return
        logger.info(f"Sets loaded: {len(data) if data is not None and hasattr(data, '__len__') else 0} items")
        self.signals.get_sets_finished.emit(data)
        self.signals.sets_data_updated.emit({
            "progress": 0,
            "totalPages": 0,
            "sets": data
        })

    def LoadStations(self):
        if self.provider is None:
            return
        self._Fetch("stations", self.provider.GetStations,
                    self._OnStationsLoaded)

    def _OnStationsLoaded(self, data):
        logger.info(f"Stations loaded: {len(data) if data is not None and hasattr(data, '__len__') else 0} items")
        self.signals.get_stations_finished.emit(data)

    def GetRecentSets(self, callback, id1, id2, videogame):
        # The provider answers through `callback` itself (the statistics'
        # relay, which hands it to the GUI thread).
        if self.provider is None:
            return
        self._Fetch("recent sets", self.provider.GetRecentSets,
                    id1=id1, id2=id2, callback=callback,
                    requestTime=time.time_ns(), videogame=videogame)

    @staticmethod
    def _ProviderPlayerId(playerId):
        """The provider's player id out of a slot's `id`: a Start.gg entrant
        is [player id, user id]; anything else (a scalar from the local
        database or the Web, say) is the id itself. Indexing a scalar
        raised TypeError here, on the caller's thread -- so a statistic
        request stopped the ones after it (ADR-005 S3e-5 review)."""
        if isinstance(playerId, (list, tuple)):
            return playerId[0] if playerId else None
        return playerId

    def GetLastSets(self, callback, playerId, playerNumber):
        if self.provider is None:
            return
        self._Fetch("last sets", self.provider.GetLastSets,
                    playerID=self._ProviderPlayerId(playerId),
                    playerNumber=playerNumber, callback=callback)

    def GetPlayerHistoryStandings(self, callback, playerId, playerNumber, gameType):
        if self.provider is None:
            return
        self._Fetch("player history", self.provider.GetPlayerHistoryStandings,
                    playerID=self._ProviderPlayerId(playerId),
                    playerNumber=playerNumber, gameType=gameType,
                    callback=callback)

    def UiMounted(self):
        """The launch's restore of the last session's tournament (both
        shells), through TournamentService like every other load."""
        if TournamentService.TournamentUrl():
            TournamentService.instance.Restore()
            self.signals.twitch_username_updated.emit()

    def GetProvider(self):
        return self.provider
