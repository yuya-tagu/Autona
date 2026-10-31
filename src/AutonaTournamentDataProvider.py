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
import re
import threading
import time
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from .core.SettingsManager import SettingsManager
from .core.StateManager import StateManager
from .core.StreamService import StreamService
from .core.TaskExecutor import TaskExecutor
from .AutonaPlayerDB import AutonaPlayerDB
from .Helpers import AutonaQtHelper
from .TournamentDataProvider.TournamentDataProvider import TournamentDataProvider
from .TournamentDataProvider.StartGGDataProvider import StartGGDataProvider
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
    entrants_updated = Signal()
    tournament_data_updated = Signal(dict)
    twitch_username_updated = Signal()
    get_sets_finished = Signal(list)
    get_stations_finished = Signal(list)
    tournament_phases_updated = Signal(list)
    game_changed = Signal(int)
    stream_queue_loaded = Signal(dict)
    sets_data_updated = Signal(dict)
    tournament_url_update = Signal(str)
    media_updated = Signal()


class AutonaTournamentDataProvider(QObject):
    instance: "AutonaTournamentDataProvider" = None

    def __init__(self) -> None:
        super().__init__(None)
        self.provider: TournamentDataProvider = None
        self.signals: AutonaTournamentDataProviderSignals = AutonaTournamentDataProviderSignals()

        self.signals.game_changed.connect(self.GameChanged)
        self.signals.tournament_url_update.connect(self.SetTournamentSignal)

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
        # one again) is loaded. GUI thread only (SetTournament*).
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

    @Slot(str, bool)
    @Slot(str)
    def SetTournament(self, url, initialLoading=False, force=False):
        # force=True re-fetches even when the same URL is already loaded
        # (used by the "Set tournament" button to refresh on demand).
        if not force and self.provider and self.provider.url == url:
            return
        self._CancelEntrants()

        if url is not None and "start.gg" in url:
            AutonaTournamentDataProvider.instance.provider = StartGGDataProvider(
                url, self)
            url = AutonaTournamentDataProvider.instance.provider.GetRealEventURL(url)
        else:
            logger.error("Unsupported provider...")
            AutonaTournamentDataProvider.instance.provider = None

        SettingsManager.Set("TOURNAMENT_URL", url)

        if self.provider is not None:
            self.GetTournamentData(initialLoading=initialLoading)
            self.GetTournamentPhases()

            self._StartEntrants()
            AutonaTournamentDataProvider.instance.signals.tournament_changed.emit()

            # AutonaTournamentDataProvider.instance.SetGameFromProvider()
        else:
            # No tournament loaded -> name completers offer the whole DB.
            AutonaPlayerDB.ClearTournamentPlayers()
            AutonaTournamentDataProvider.instance.signals.tournament_data_updated.emit({
            })
            AutonaTournamentDataProvider.instance.signals.tournament_phases_updated.emit([
            ])
            AutonaTournamentDataProvider.instance.signals.tournament_changed.emit()
            # (No game change: a cleared URL keeps whatever game is loaded.
            # The old LoadGameAssets(0) here had been a no-op since the
            # PdM decision of 2026-10-23, see GameAssetCatalog.SelectGame.)

    def SetTournamentSignal(self, url, initialLoading=False):
        if self.provider and self.provider.url == url:
            return
        self._CancelEntrants()

        if url is not None and "start.gg" in url:
            AutonaTournamentDataProvider.instance.provider = StartGGDataProvider(
                url, self)
        else:
            logger.error("Unsupported provider...")
            AutonaTournamentDataProvider.instance.provider = None

        SettingsManager.Set("TOURNAMENT_URL", url)

        if self.provider is not None:
            self.GetTournamentData(initialLoading=initialLoading)
            self.GetTournamentPhases()

            self._StartEntrants()
            AutonaTournamentDataProvider.instance.signals.tournament_changed.emit()
        else:
            # No tournament loaded -> name completers offer the whole DB.
            AutonaPlayerDB.ClearTournamentPlayers()
            AutonaTournamentDataProvider.instance.signals.tournament_data_updated.emit({
            })
            AutonaTournamentDataProvider.instance.signals.tournament_phases_updated.emit([
            ])
            AutonaTournamentDataProvider.instance.signals.tournament_changed.emit()

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

    def GetBannerURL(self):
        """The loaded tournament's banner URL; None with no tournament or
        none found. Blocking (a start.gg request): the stream thumbnail
        job's banner lookup (ADR-005 S4d), never for the GUI thread."""
        provider = self.provider
        if provider is None:
            return None
        return provider.GetBannerURL()

    def SetStartggEventSlug(self, mainWindow):
        inp = QDialog(mainWindow)

        layout = QVBoxLayout()
        inp.setLayout(layout)

        inp.layout().addWidget(QLabel(
            QApplication.translate("app", "Paste the tournament URL.")+ "\n" + QApplication.translate("app", "For StartGG, the link must contain the /event/ part") + "\n" + QApplication.translate("app", "Supported providers:") + " " + ", ".join(get_supported_providers())

        ))

        lineEdit = QLineEdit()
        okButton = QPushButton("OK")
        validators = [
            QRegularExpression("start.gg/tournament/[^/]+/event[s]?/[^/]+"),
            QRegularExpression("start.gg/admin/tournament/[^/]+/brackets/[^/]+")
        ]

        def validateText():
            okButton.setDisabled(True)

            for validator in validators:
                match = validator.match(lineEdit.text()).capturedTexts()
                if len(match) > 0:
                    okButton.setDisabled(False)

        lineEdit.textEdited.connect(validateText)

        # Prefill with the currently selected tournament URL (if any) so the
        # operator can just press OK to re-fetch, or edit to switch events.
        lineEdit.setText(SettingsManager.Get("TOURNAMENT_URL", ""))
        validateText()

        inp.layout().addWidget(lineEdit)

        okButton.clicked.connect(inp.accept)
        okButton.setDisabled(True)
        inp.layout().addWidget(okButton)

        inp.setWindowTitle(QApplication.translate("app", "Set tournament URL"))
        inp.resize(600, 10)

        accepted = inp.exec() == QDialog.Accepted
        if accepted:
            url = lineEdit.text()

            if "start.gg" in url:
                matches = re.match(
                    "(.*start.gg/tournament/[^/]*/event[s]?/[^/]*)", url)
                if matches:
                    url = matches.group(0)

                    # Some URLs in startgg have eventS but the API doesn't work with that format
                    url = url.replace("/events/", "/event/")

            SettingsManager.Set("TOURNAMENT_URL", url)
            # force=True so re-selecting the same tournament still re-fetches.
            AutonaTournamentDataProvider.instance.SetTournament(
                SettingsManager.Get("TOURNAMENT_URL"), force=True)

        inp.deleteLater()
        return accepted

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
        if self.provider is None:
            logger.warning("GetTournamentData called with no provider set")
            return
        self._Fetch(
            "tournament data", self.provider.GetTournamentData,
            lambda tournamentData: self._onTournamentDataResult(
                tournamentData, initialLoading))

    def _onTournamentDataResult(self, tournamentData, initialLoading):
        """Handle tournament data on the main thread."""
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

    def GetAnalysisPhaseGroup(self, id, onResult):
        """Analysis-dock-only fetch. Uses the lightweight query so Start.gg
        responses stay well under the timeout budget, and routes the result
        to a caller-supplied callback -- nothing else sees it, and the
        state (top8.*, which only the core Top 8 import writes) is
        untouched by cross-phase-group probes."""
        if self.provider is None:
            onResult(None)
            return
        if not hasattr(self.provider, "GetAnalysisPhaseGroup"):
            logger.warning(
                "Provider has no GetAnalysisPhaseGroup — skipping analysis fetch")
            onResult(None)
            return
        self._Fetch("analysis phase group", self.provider.GetAnalysisPhaseGroup,
                    onResult, id=id)

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

    def GetStandings(self, playerNumber, callback):
        if self.provider is None:
            return
        self._Fetch("standings", self.provider.GetStandings, callback.emit,
                    playerNumber=playerNumber)

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
        if SettingsManager.Get("TOURNAMENT_URL"):
            AutonaTournamentDataProvider.instance.SetTournament(
                SettingsManager.Get("TOURNAMENT_URL"), initialLoading=True)
            AutonaTournamentDataProvider.instance.signals.twitch_username_updated.emit()

    def GetProvider(self):
        return self.provider
