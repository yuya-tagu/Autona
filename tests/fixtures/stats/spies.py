"""Test doubles for the statistics (ADR-005 S3e-5): counting what a step
asks for, per board (StatsTriggers), and a fetcher that answers when told
(FakeFetcher).

The statistics used to be asked for through each board's AutonaStatsUtil
signals (``RecentSetsSignal``, ``LastSetsP1Signal``, ...): the player cards'
triggers, relayed by the board, and the board's own after a swap / move /
clear. Since S3e-5 everything that asks goes through core's
``StatsService.Refresh`` -- the operations that change a player, the routes,
the STATS panel -- and the old signals are gone. This counts one board's
Refresh calls under the old signals' names, so recordings and assertions
written against the signals read the same: one count is one fetch (or, for
the upset factor, one recalculation through the trigger -- the operations
that recalculate it inside their own save cycle call UpdateUpsetFactor
directly, as they did before, and are not counted).
"""
from unittest.mock import patch

from src.core.StatsService import StatsService

NAMES = ("RecentSetsSignal", "LastSetsP1Signal", "LastSetsP2Signal",
         "PlayerHistoryStandingsP1Signal", "PlayerHistoryStandingsP2Signal",
         "UpsetFactorCalculation")


def SignalName(kind, team=None):
    """The old trigger's name for StatsService.Refresh(kind, team)."""
    if kind == StatsService.RECENT_SETS:
        return "RecentSetsSignal"
    if kind == StatsService.LAST_SETS:
        return f"LastSetsP{int(team)}Signal"
    if kind == StatsService.HISTORY_SETS:
        return f"PlayerHistoryStandingsP{int(team)}Signal"
    if kind == StatsService.UPSET_FACTOR:
        return "UpsetFactorCalculation"
    raise ValueError(kind)


class StatsTriggers:
    """How often each statistic of `board` was asked for inside the block
    (`counts`: {old signal name: count}). `board` is a board number, a
    board widget, or None (nothing is counted)."""

    NAMES = NAMES

    def __init__(self, board):
        if board is not None and not isinstance(board, int):
            board = board.scoreboardNumber
        self.board = board
        self.counts = dict.fromkeys(NAMES, 0)
        self._patch = None

    def __enter__(self):
        if self.board is None:
            return self
        original = StatsService.Refresh

        def counting(service, kind, team=None):
            if service.scoreboardNumber == self.board:
                self.counts[SignalName(kind, team)] += 1
            return original(service, kind, team)

        self._patch = patch.object(StatsService, "Refresh", counting)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        if self._patch is not None:
            self._patch.stop()
            self._patch = None
        return False


class FakeFetcher:
    """StatsService.fetcher for core: records each request as (statistic,
    arguments, onResult) and answers only when a test calls its onResult;
    `Later` queues until `runLater()`."""

    def __init__(self, startGG=True):
        self.startGG = startGG
        self.requests = []
        self.later = []

    def IsStartGG(self):
        return self.startGG

    def RecentSets(self, p1id, p2id, videogame, onResult):
        self.requests.append(
            (StatsService.RECENT_SETS, (p1id, p2id, videogame), onResult))

    def LastSets(self, playerId, team, onResult):
        self.requests.append(
            (StatsService.LAST_SETS, (playerId, team), onResult))

    def HistorySets(self, playerId, team, videogame, onResult):
        self.requests.append(
            (StatsService.HISTORY_SETS, (playerId, team, videogame),
             onResult))

    def Later(self, fn):
        self.later.append(fn)

    def runLater(self):
        queued, self.later = self.later, []
        for fn in queued:
            fn()

    def asked(self):
        """The requests made so far, without their callbacks."""
        return [(kind, args) for kind, args, _ in self.requests]

    def answer(self, index, data):
        self.requests[index][2](data)
