from qtpy.QtCore import QObject, QThreadPool


class TournamentDataProvider(QObject):
    def __init__(self, url, threadpool, tshTdp) -> None:
        super().__init__(None)

        self.name = ""
        self.url = url
        self.entrants = []
        self.tournamentData = {}
        # Where the provider's own nested Workers run (GetMatch's two APIs,
        # GetRecentSets' pages) until ADR-005 S3f-3 moves them to the task
        # executor's fan-out lane and removes this argument. The adapter
        # passes None since S3f-2, when its jobs moved to the executor's job
        # lane: Qt's global pool, apart from the lane whose jobs wait on it.
        self.threadpool = (threadpool if threadpool is not None
                           else QThreadPool.globalInstance())
        self.videogame = None
        self.tshTdp = tshTdp

    def GetIconURL(self):
        pass

    def GetBannerURL(self):
        pass

    def GetEntrants(self, cancel_event=None):
        pass

    def GetTournamentData(self, progress_callback=None, cancel_event=None):
        pass

    def GetMatch(self, setId, progress_callback=None, cancel_event=None):
        pass

    def GetMatches(self, getFinished=False, phaseGroupIds=None, progress_callback=None, cancel_event=None):
        pass

    def GetStations(self, progress_callback=None, cancel_event=None):
        pass

    def GetStreamQueue(self, streamName=None, progress_callback=None, cancel_event=None):
        pass

    def GetStreamMatchId(self, streamName):
        pass

    def GetStationMatchsId(self, stationId):
        pass

    def GetRecentSets(self, id1, id2, videogame, callback):
        pass

    def GetLastSets(self, playerId, playerNumber):
        pass

    def GetPlayerHistoryStandings(self, playerId, playerNumber, gameType):
        pass

    def GetTournamentPhases(self, progress_callback=None, cancel_event=None):
        pass

    def GetTournamentPhaseGroup(self, id, progress_callback=None, cancel_event=None):
        pass

    def GetStandings(self, playerNumber):
        pass

    def GetFutureMatch(self, progrss_callback=None):
        pass

    # give me a list of objects that contain a "id" property
    def GetFutureMatchesList(self, sets: object, progress_callback=None, cancel_event=None):
        pass
