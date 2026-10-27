"""How core's statistics reach Start.gg: the Qt side of StatsService.

Everything the statistics decide and write is `src/core/StatsService.py`,
and since ADR-005 S3e-5 so is *when* they are asked for (core's operations
ask; this file used to be one object per board, owning the trigger signals
the player cards drove). What is left is the transport, registered once by
bootstrap_core() as `StatsService.fetcher`:

- a request goes to the tournament provider (`AutonaTournamentDataProvider`;
  the fetch runs as a job of the core task executor since ADR-005 S3f-2);
- its answer comes back on the main thread. The provider's job calls
  `callback.emit(data)` from its own thread; the callback handed to it here
  re-emits that on `answered`, a queued signal to a QObject living on the
  main thread. The payload crosses as the `dict` it always crossed as --
  the same conversion the per-board result signals made, so what is
  published (the recent sets' key order included) is unchanged;
- `Later(fn)` runs `fn` on the main thread once its event loop is back in
  control (StatsService.TeamsChanged);
- the provider's stream queue, relayed to `StatsService.UpdateStreamQueue`
  (every built board used to relay it, each writing the same two keys).

QtCore only: the headless shell has it too, so headless statistics fetch.
No class-level `instance` -- the one registration is `StatsService.fetcher`.
"""
from qtpy.QtCore import QObject, Qt, Signal, Slot
from loguru import logger

from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .core.StatsService import StatsService


class _StatsSignals(QObject):
    # (onResult, data): a fetch answered.
    answered = Signal(object, dict)
    # fn: run on the main thread, from its event loop.
    later = Signal(object)

    def __init__(self):
        super().__init__()
        # Queued even when emitted on the main thread: Later() is "once the
        # event loop is back", never "now".
        self.answered.connect(self._OnAnswered, Qt.ConnectionType.QueuedConnection)
        self.later.connect(self._OnLater, Qt.ConnectionType.QueuedConnection)

    @Slot(object, dict)
    def _OnAnswered(self, onResult, data):
        try:
            onResult(data)
        except Exception:
            logger.exception("Stats: writing a fetched statistic failed")

    @Slot(object)
    def _OnLater(self, fn):
        try:
            fn()
        except Exception:
            logger.exception("Stats: refreshing the statistics failed")

    @Slot(dict)
    def OnStreamQueue(self, data):
        try:
            StatsService.UpdateStreamQueue(data)
        except Exception:
            logger.exception("Stats: writing the stream queue failed")


class _Reply:
    """The `callback` a provider worker emits its answer on."""

    def __init__(self, signals, onResult):
        self._signals = signals
        self._onResult = onResult

    def emit(self, data):
        self._signals.answered.emit(self._onResult, data)


class AutonaStatsUtil:
    """StatsService's fetcher (see the module docstring). Built on the main
    thread by bootstrap_core(), after the tournament provider."""

    def __init__(self):
        self.signals = _StatsSignals()
        AutonaTournamentDataProvider.instance.signals.stream_queue_loaded.connect(
            self.signals.OnStreamQueue)

    @staticmethod
    def _Provider():
        return AutonaTournamentDataProvider.instance

    def IsStartGG(self):
        """True only when a Start.gg tournament is currently loaded.

        `provider` stays None until a tournament URL is set, so the name has
        to be reached through a guard -- the statistics are reachable from
        the web API and the stats panel at any time, including before any
        tournament has been loaded.
        """
        provider = self._Provider()
        return bool(provider and provider.provider
                    and provider.provider.name == "StartGG")

    def _Callback(self, onResult):
        return _Reply(self.signals, onResult)

    def RecentSets(self, p1id, p2id, videogame, onResult):
        self._Provider().GetRecentSets(
            self._Callback(onResult), p1id, p2id, videogame)

    def LastSets(self, playerId, team, onResult):
        self._Provider().GetLastSets(
            self._Callback(onResult), playerId, str(team))

    def HistorySets(self, playerId, team, videogame, onResult):
        self._Provider().GetPlayerHistoryStandings(
            self._Callback(onResult), playerId, str(team), videogame)

    def Later(self, fn):
        self.signals.later.emit(fn)
