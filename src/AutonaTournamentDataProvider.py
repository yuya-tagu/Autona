import re
import time
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from .core.SettingsManager import SettingsManager
from .core.StateManager import StateManager
from .AutonaPlayerDB import AutonaPlayerDB
from .TournamentDataProvider.TournamentDataProvider import TournamentDataProvider
from .TournamentDataProvider.StartGGDataProvider import StartGGDataProvider
from .Helpers.AutonaVersionHelper import get_supported_providers
from loguru import logger

from .Workers import Worker


class AutonaTournamentDataProviderSignals(QObject):
    tournament_changed = Signal()
    entrants_updated = Signal()
    tournament_data_updated = Signal(dict)
    twitch_username_updated = Signal()
    get_sets_finished = Signal(list)
    get_stations_finished = Signal(list)
    tournament_phases_updated = Signal(list)
    tournament_phasegroup_updated = Signal(dict)
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
        self.threadPool = QThreadPool()

        self.signals.game_changed.connect(self.GameChanged)
        self.signals.tournament_url_update.connect(self.SetTournamentSignal)

        # How a start.gg videogame id becomes the loaded game. Registered by
        # the shell (bootstrap_gui() points it at the game asset adapter,
        # which also re-applies the provider's game after every scan); the
        # provider no longer knows the adapter, so it can be built without
        # one (ADR-005 S3c).
        self.selectGameFromStartGGId = None

        self.setLoadingWorker = None

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

        if url is not None and "start.gg" in url:
            AutonaTournamentDataProvider.instance.provider = StartGGDataProvider(
                url, self.threadPool, self)
            url = AutonaTournamentDataProvider.instance.provider.GetRealEventURL(url)
        else:
            logger.error("Unsupported provider...")
            AutonaTournamentDataProvider.instance.provider = None

        SettingsManager.Set("TOURNAMENT_URL", url)

        if self.provider is not None:
            self.GetTournamentData(initialLoading=initialLoading)
            self.GetTournamentPhases()

            AutonaTournamentDataProvider.instance.provider.GetEntrants()
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

        if url is not None and "start.gg" in url:
            AutonaTournamentDataProvider.instance.provider = StartGGDataProvider(
                url, self.threadPool, self)
        else:
            logger.error("Unsupported provider...")
            AutonaTournamentDataProvider.instance.provider = None

        SettingsManager.Set("TOURNAMENT_URL", url)

        if self.provider is not None:
            self.GetTournamentData(initialLoading=initialLoading)
            self.GetTournamentPhases()

            AutonaTournamentDataProvider.instance.provider.GetEntrants()
            AutonaTournamentDataProvider.instance.signals.tournament_changed.emit()
        else:
            # No tournament loaded -> name completers offer the whole DB.
            AutonaPlayerDB.ClearTournamentPlayers()
            AutonaTournamentDataProvider.instance.signals.tournament_data_updated.emit({
            })
            AutonaTournamentDataProvider.instance.signals.tournament_phases_updated.emit([
            ])
            AutonaTournamentDataProvider.instance.signals.tournament_changed.emit()

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

    def GetTournamentData(self, initialLoading=False):
        if self.provider is None:
            logger.warning("GetTournamentData called with no provider set")
            return
        worker = Worker(self.provider.GetTournamentData)
        worker.signals.result.connect(lambda tournamentData: self._onTournamentDataResult(tournamentData, initialLoading))
        self.threadPool.start(worker)

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
        worker = Worker(self.provider.GetTournamentPhases)
        worker.signals.result.connect(lambda tournamentPhases: [
            AutonaTournamentDataProvider.instance.signals.tournament_phases_updated.emit(
                tournamentPhases)
        ])
        self.threadPool.start(worker)

    def GetTournamentPhaseGroup(self, id):
        if self.provider is None:
            return
        worker = Worker(self.provider.GetTournamentPhaseGroup, **{"id": id})
        worker.signals.result.connect(lambda phaseGroupData: [
            AutonaTournamentDataProvider.instance.signals.tournament_phasegroup_updated.emit(
                phaseGroupData)
        ])
        self.threadPool.start(worker)

    def GetAnalysisPhaseGroup(self, id, onResult):
        """Analysis-dock-only fetch. Uses the lightweight query so Start.gg
        responses stay well under the timeout budget, and routes the result
        to a caller-supplied callback instead of the shared
        tournament_phasegroup_updated signal — this keeps the Bracket dock
        and StateManager untouched by cross-phase-group probes."""
        if self.provider is None:
            onResult(None)
            return
        if not hasattr(self.provider, "GetAnalysisPhaseGroup"):
            logger.warning(
                "Provider has no GetAnalysisPhaseGroup — skipping analysis fetch")
            onResult(None)
            return
        worker = Worker(self.provider.GetAnalysisPhaseGroup, **{"id": id})
        worker.signals.result.connect(onResult)
        self.threadPool.start(worker)

    def LoadSets(self, showFinished, phaseGroupIds=None):
        if self.provider is None:
            return
        if self.setLoadingWorker:
            # If there was a previous set loading worker,
            # block its signals
            self.setLoadingWorker.cancel()
            self.setLoadingWorker.signals.blockSignals(True)

        worker = Worker(self.provider.GetMatches, **
                        {"getFinished": showFinished,
                         "phaseGroupIds": phaseGroupIds})
        worker.signals.result.connect(lambda data: [
            logger.info(f"Sets loaded: {len(data) if data is not None and hasattr(data, '__len__') else 0} items"),
            self.signals.get_sets_finished.emit(data),
            self.signals.sets_data_updated.emit({
                "progress": 0,
                "totalPages": 0,
                "sets": data
            })
        ])
        worker.signals.progress.connect(lambda n, t: [
            logger.info(f"SetDataUpdated: {n}/{t}"),
            self.signals.sets_data_updated.emit({
                "progress": n,
                "totalPages":t,
                "sets": []
            })
        ])
        self.setLoadingWorker = worker
        self.threadPool.start(worker)

    def LoadStations(self):
        if self.provider is None:
            return
        worker = Worker(self.provider.GetStations)
        worker.signals.result.connect(lambda data: [
            logger.info(f"Stations loaded: {len(data) if data is not None and hasattr(data, '__len__') else 0} items"),
            self.signals.get_stations_finished.emit(data)
        ])
        self.threadPool.start(worker)

    def LoadStationSets(self, mainWindow):
        if mainWindow.lastStationSelected:
            worker = Worker(
                AutonaTournamentDataProvider.instance.LoadStationSetsDo,
                **{"mainWindow": mainWindow}
            )
            self.threadPool.start(worker)

    def LoadStationSetsDo(self, mainWindow, progress_callback=None, cancel_event=None):
        if AutonaTournamentDataProvider.instance.provider is None:
            return
        stationSet = None

        if mainWindow.lastStationSelected.get("type") == "stream":
            stationSet = AutonaTournamentDataProvider.instance.provider.GetStreamMatchId(
                mainWindow.lastStationSelected.get("identifier"))
        else:
            stationSets = AutonaTournamentDataProvider.instance.provider.GetStationMatchsId(
                mainWindow.lastStationSelected.get("id")
            )

            if stationSets is not None and len(stationSets) > 0:
                stationSet = stationSets[0]

                queueCache = mainWindow.stationQueueCache
                logger.info(
                    f"Station follow: queue={len(getattr(queueCache, 'queue', None) or [])} "
                    f"stationSets={len(stationSets)}")
                if queueCache and not queueCache.CheckQueue(stationSets):
                    queueCache.UpdateQueue(stationSets)

                    AutonaTournamentDataProvider.instance.GetStationMatches(
                        stationSets, mainWindow)

        if not stationSet:
            stationSet = {}

        stationSet["auto_update"] = mainWindow.lastStationSelected.get(
            "type")

        mainWindow.signals.NewSetSelected.emit(stationSet)

    # omits the first one (loaded through NewSetSelected)
    def GetStationMatches(self, matchesId, mainWindow):
        if self.provider is None:
            return
        matchesId = matchesId[1:]

        worker = Worker(self.provider.GetFutureMatchesList, **{
            "setsId": matchesId
        })
        worker.signals.result.connect(
            lambda sets: mainWindow.signals.StationSetsLoaded.emit(sets)
        )
        self.threadPool.start(worker)

    def GetMatch(self, mainWindow, setId, overwrite=True, no_mains=False):
        if self.provider is None:
            return
        worker = Worker(self.provider.GetMatch, **
                        {"setId": setId})
        worker.signals.result.connect(lambda data: [
            data.update({"overwrite": overwrite, "no_mains": no_mains}),
            mainWindow.signals.UpdateSetData.emit(data)
        ])
        self.threadPool.start(worker)

    def GetRecentSets(self, callback, id1, id2, videogame):
        if self.provider is None:
            return
        worker = Worker(self.provider.GetRecentSets, **{
            "id1": id1, "id2": id2, "callback": callback, "requestTime": time.time_ns(), "videogame": videogame
        })
        self.threadPool.start(worker)

    def GetStandings(self, playerNumber, callback):
        if self.provider is None:
            return
        worker = Worker(self.provider.GetStandings, **{
            "playerNumber": playerNumber
        })
        worker.signals.result.connect(lambda data: [
            callback.emit(data)
        ])
        self.threadPool.start(worker)

    def GetLastSets(self, callback, playerId, playerNumber):
        if self.provider is None:
            return
        worker = Worker(self.provider.GetLastSets, **{
            "playerID": playerId[0],
            "playerNumber": playerNumber,
            "callback": callback
        })
        self.threadPool.start(worker)

    def GetPlayerHistoryStandings(self, callback, playerId, playerNumber, gameType):
        if self.provider is None:
            return
        worker = Worker(self.provider.GetPlayerHistoryStandings, **{
            "playerID": playerId[0],
            "playerNumber": playerNumber,
            "gameType": gameType,
            "callback": callback
        })
        self.threadPool.start(worker)

    def GetStreamQueue(self):
        if self.provider is None:
            return
        worker = Worker(self.provider.GetStreamQueue)
        worker.signals.result.connect(lambda streamQueue: [
            AutonaTournamentDataProvider.instance.signals.stream_queue_loaded.emit(
                streamQueue)
        ])
        self.threadPool.start(worker)

    def UiMounted(self):
        if SettingsManager.Get("TOURNAMENT_URL"):
            AutonaTournamentDataProvider.instance.SetTournament(
                SettingsManager.Get("TOURNAMENT_URL"), initialLoading=True)
            AutonaTournamentDataProvider.instance.signals.twitch_username_updated.emit()

    def GetProvider(self):
        return self.provider
