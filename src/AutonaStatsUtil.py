"""Qt transport for one board's statistics.

Everything this class used to compute and write now lives in
`src/core/StatsService.py` (headless core Step 4a). What is left is the
part that is genuinely Qt: the signals.

They come in two kinds, and the distinction is the whole reason this file
still exists:

- **triggers (in)** -- `RecentSetsSignal`, `LastSetsP1/P2Signal`,
  `PlayerHistoryStandingsP1/P2Signal`, `UpsetFactorCalculation`. Emitted by
  the player widgets (an id or a seed changed), the STATS panel's refresh
  button, and the web layer, which reaches them from the Flask thread. The
  signal is what carries the call to the GUI thread; that is why the
  WebServerActions stats routes can stay `@gui_thread_sync`-free.
- **results (out)** -- `recent_sets_updated`, `last_sets_updated`,
  `history_sets_updated`. Handed to `AutonaTournamentDataProvider` as the
  callback a Start.gg worker emits when its request comes back, which is
  the worker-thread -> GUI-thread hop.

`AutonaTournamentDataProvider` (Worker / QThreadPool) is deliberately not
migrated: Step 4a moves the decisions and the writes, not the fetching.
"""
from qtpy.QtCore import *

from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .core.StatsService import StatsService
from loguru import logger


class AutonaStatsSignals(QObject):
    RecentSetsSignal = Signal()
    LastSetsP1Signal = Signal()
    LastSetsP2Signal = Signal()
    PlayerHistoryStandingsP1Signal = Signal()
    PlayerHistoryStandingsP2Signal = Signal()
    UpsetFactorCalculation = Signal()

    recent_sets_updated = Signal(dict)
    last_sets_updated = Signal(dict)
    history_sets_updated = Signal(dict)

class AutonaStatsUtil:
    """Per-scoreboard stats fetcher.

    Not a singleton: every instance is bound to one board (it writes under
    `score.{scoreboardNumber}.*`) and is owned by that board as
    `AutonaScoreboardWidget.stats`. Reach it through
    `AutonaScoreboardManager.instance.GetScoreboard(n).stats`. Upstream TSH
    had a class-level `instance` built at import time; the per-board split
    made it meaningless, so it is gone -- do not reintroduce it.

    The calculations and the state writes are `self.service`
    (`src/core/StatsService.py`), one per board as well.
    """

    def __init__(self, scoreboardNumber, scoreboard):
        self.signals: AutonaStatsSignals = AutonaStatsSignals()

        self.scoreboardNumber = scoreboardNumber
        self.scoreboard = scoreboard
        # Lazily read, not captured: this runs from the board's __init__,
        # where team1playerWidgets does not exist yet.
        self.service = StatsService(
            scoreboardNumber,
            playersPerTeam=lambda: len(self.scoreboard.team1playerWidgets))

        self.signals.PlayerHistoryStandingsP1Signal.connect(
            self.GetPlayerHistoryStandingsP1)
        self.signals.PlayerHistoryStandingsP2Signal.connect(
            self.GetPlayerHistoryStandingsP2)
        self.signals.LastSetsP1Signal.connect(
            self.GetLastSetsP1)
        self.signals.LastSetsP2Signal.connect(
            self.GetLastSetsP2)
        self.signals.RecentSetsSignal.connect(
            self.GetRecentSets)
        self.signals.UpsetFactorCalculation.connect(
            self.GetSetUpsetFactor)

        self.signals.history_sets_updated.connect(
            self.UpdateHistorySets)
        self.signals.last_sets_updated.connect(
            self.UpdateLastSets)
        self.signals.recent_sets_updated.connect(
            self.UpdateRecentSets)
        AutonaTournamentDataProvider.instance.signals.stream_queue_loaded.connect(
            self.UpdateStreamQueue)

    def _IsStartGG(self):
        """True only when a Start.gg tournament is currently loaded.

        `provider` stays None until a tournament URL is set, so the name has
        to be reached through a guard -- these calculations are reachable
        from the web API and the stats panel at any time, including before
        any tournament has been loaded.
        """
        provider = AutonaTournamentDataProvider.instance
        return bool(provider and provider.provider
                    and provider.provider.name == "StartGG")

    def GetRecentSets(self):
        request = self.service.RecentSetsRequest() if self._IsStartGG() else None

        if request is None:
            self.service.ClearRecentSets()
            return

        self.service.MarkRecentSetsLoading()
        AutonaTournamentDataProvider.instance.GetRecentSets(
            self.signals.recent_sets_updated, *request)

    def UpdateRecentSets(self, data):
        self.service.UpdateRecentSets(data)

    def GetLastSetsP1(self):
        self._GetLastSets(1)

    def GetLastSetsP2(self):
        self._GetLastSets(2)

    def _GetLastSets(self, team):
        # Note the guard: any loaded provider will do here, unlike the
        # Start.gg-only statistics below.
        if not (self.service.IsSingles()
                and AutonaTournamentDataProvider.instance):
            return
        playerId = self.service.PlayerId(team)
        logger.info(f"Player {team} ID: {playerId}")
        if playerId:
            AutonaTournamentDataProvider.instance.GetLastSets(
                self.signals.last_sets_updated, playerId, str(team))
        else:
            self.service.ClearLastSets(team)

    def UpdateLastSets(self, data):
        self.service.UpdateLastSets(data)

    def GetPlayerHistoryStandingsP1(self):
        self._GetPlayerHistoryStandings(1)

    def GetPlayerHistoryStandingsP2(self):
        self._GetPlayerHistoryStandings(2)

    def _GetPlayerHistoryStandings(self, team):
        if not (self.service.IsSingles() and self._IsStartGG()):
            return
        playerId = self.service.PlayerId(team)
        if playerId:
            AutonaTournamentDataProvider.instance.GetPlayerHistoryStandings(
                self.signals.history_sets_updated, playerId, str(team),
                self.service.VideogameId())
        else:
            self.service.ClearHistorySets(team)

    def UpdateHistorySets(self, data):
        self.service.UpdateHistorySets(data)

    def GetSetUpsetFactor(self):
        self.service.UpdateUpsetFactor()

    def UpdateStreamQueue(self, data):
        self.service.UpdateStreamQueue(data)
