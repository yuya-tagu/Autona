"""Per-board statistics: when they are asked for, what is fetched, and what
lands in the state (ADR-005 S3e-5).

The stats are the textbook case of write category (b) in
docs/changes/headless-core-redesign.md §2 -- "data-load results poured into
the state". Nothing here is an operator edit: a fetch is triggered, a
Start.gg worker answers some time later, and the answer is written under
``score.{n}.*`` for the overlays and the Web UI to render.

Headless core Step 4a moved the decisions and the writes here. ADR-005
S3e-5 moved the rest of it: the triggers and the fetch.

- **Triggers.** Core's own operations ask: a slot's player or seed changed
  (``PlayerService`` -- a load, a clear, a renamed player; ``PlayerChanged``),
  other players on a team after a swap / move / clear (``AutonaActions``;
  ``TeamsChanged``), and anyone who wants a statistic again (the web
  routes, the STATS panel; ``Refresh`` / ``RefreshAll``). They used to be the
  player cards' Qt signals relayed by the board, so a board that was not
  built -- and the headless shell, which builds none -- never fetched.
- **The fetch** is the shell's, injected as ``fetcher`` (bootstrap_core():
  ``AutonaStatsUtil``, which hands the request to the tournament provider's
  thread pool and the answer back to the main thread). Core imports no
  ``src`` module; without a fetcher nothing is fetched, as with no
  tournament loaded.
- **The roster size** is the state's: the number of player slots team 1 of
  the board has (``score.{n}.team.1.player``), ``DefaultPlayersPerTeam()``
  when it has none (S3e decision 1). It was the board's card count; on a
  built board the two are the same (the roster is trimmed to the cards).

The fetcher's interface: ``IsStartGG()``; ``RecentSets(p1, p2, videogame,
onResult)``, ``LastSets(playerId, team, onResult)``, ``HistorySets(playerId,
team, videogame, onResult)`` start a fetch and call ``onResult(data)`` on
the main thread when it answers (never, when no tournament is loaded);
``Later(fn)`` runs ``fn()`` on the main thread once control is back in its
event loop.

Late answers. A fetch is numbered when it is asked for, per board, statistic
and team (`_generations`; the numbers come from one counter, so no two
requests ever share one); its answer is written only if no later request
for the same statistic was made since, *and* the slot still holds the
player it was asked for. Anything else is dropped: a slow answer for the
previous player never lands on the next one, whichever board or thread
asked. The request and its "loading" / cleared write are made under
``StateManager.lock``, so two threads asking at once cannot leave the
statistic waiting for an answer that will be dropped. The fetch itself is
started outside the lock.

Only a newer request supersedes an answer: one that lands after the board
became a team board, or after another provider was loaded, with nothing
asked for since, is written as it always was (the head-to-head would be
left "loading" otherwise). Asking again settles it.

Failures. A statistic that cannot be asked for (the fetch raised) is logged
and the others are still asked for -- by RefreshAll, PlayerChanged and the
queued team refreshes alike -- and a route that asked still answers "OK":
the old triggers were Qt signals, whose slots' errors never reached the
operation or the route that emitted them.

``score.{n}.{last_sets,history_sets,recent_sets,upset_factor}`` are the
paths StateSchema.STATS_KEYS exempts from the 反映 hold: fetched data
publishes as soon as it lands, because no operator would think to press
反映 for numbers they did not type. A refresh opens no save block of its
own: what it writes joins the caller's cycle, or is a cycle of its own, as
the Qt triggers' writes were.

Import side effects: none (agent-pitfalls #1). Instances are cheap and
stateless (``StatsService(n)``); the request numbers and the queued team
refreshes are the class's.
"""
import itertools
import math
import threading
from datetime import datetime

import orjson
from loguru import logger

from .PlayerService import DefaultPlayersPerTeam
from .SettingsManager import SettingsManager
from .StateManager import StateManager


class StatsService:
    """The stats of one scoreboard. Bound to ``score.{scoreboardNumber}``."""

    # What Refresh() is asked for.
    RECENT_SETS = "recent_sets"
    LAST_SETS = "last_sets"
    HISTORY_SETS = "history_sets"
    UPSET_FACTOR = "upset_factor"

    # How a statistic is fetched (see the module docstring). Registered by
    # bootstrap_core(); None: nothing is fetched.
    fetcher = None

    # (board, statistic, team) -> the number of the latest request. Read and
    # written under StateManager.lock. The numbers are _counter's, unique
    # across every key: an answer still in flight is never taken for a
    # later request's, even once this table was emptied (the tests do).
    _generations = {}
    _counter = itertools.count(1)

    # board -> the teams whose statistics are queued for the main thread
    # (TeamsChanged). Under its own lock: it is taken from any thread, and
    # never with anything else held.
    _queuedTeams = {}
    _queueLock = threading.Lock()

    def __init__(self, scoreboardNumber):
        self.scoreboardNumber = int(scoreboardNumber)

    # ── helpers ──────────────────────────────────────────────────────────

    @property
    def prefix(self) -> str:
        return f"score.{self.scoreboardNumber}"

    def PlayersPerTeam(self) -> int:
        """How many players each team of the board has: team 1's player
        slots in the state, the default roster size when it has none."""
        roster = StateManager.Get(f"{self.prefix}.team.1.player")
        count = len(roster) if isinstance(roster, dict) else 0
        return count if count > 0 else DefaultPlayersPerTeam()

    def IsSingles(self) -> bool:
        """True when each side is one player, the only case stats cover --
        a "recent sets between these two" means nothing for a 5-man crew."""
        return self.PlayersPerTeam() == 1

    def PlayerId(self, team):
        """The provider id of team `team`'s first player (1-based team)."""
        return StateManager.Get(f"{self.prefix}.team.{team}.player.1.id")

    @staticmethod
    def VideogameId():
        """The loaded tournament's game, as the provider queries want it."""
        return StateManager.Get("provider_videogame.id")

    @staticmethod
    def _SameId(a, b) -> bool:
        # Ids are provider payloads (lists as often as scalars), so they are
        # compared by their serialised form rather than with ==.
        return orjson.dumps(a) == orjson.dumps(b)

    @classmethod
    def _IsStartGG(cls) -> bool:
        fetcher = cls.fetcher
        return bool(fetcher is not None and fetcher.IsStartGG())

    # ── what asks for the statistics ─────────────────────────────────────

    def Refresh(self, kind, team=None):
        """Fetch / recalculate one statistic of this board now (`team`: 1 or
        2, for the per-player ones)."""
        if kind == self.RECENT_SETS:
            self._RefreshRecentSets()
        elif kind == self.LAST_SETS:
            self._RefreshLastSets(int(team))
        elif kind == self.HISTORY_SETS:
            self._RefreshHistorySets(int(team))
        elif kind == self.UPSET_FACTOR:
            self.UpdateUpsetFactor()
        else:
            raise ValueError(f"unknown statistic {kind!r}")

    def RefreshQuietly(self, kind, team=None):
        """Refresh, with a failure logged rather than raised (see the module
        docstring): what asks for several statistics goes on to the next,
        and a route answers "OK"."""
        try:
            self.Refresh(kind, team)
        except Exception:
            logger.exception(
                f"Stats: {self.prefix} {kind}"
                f"{'' if team is None else f' (team {team})'}: "
                "asking for the statistic failed")

    def RefreshAll(self):
        """Every statistic of the board, in the order the STATS panel's
        button and the web's refresh have always asked for them."""
        self.RefreshQuietly(self.RECENT_SETS)
        self.RefreshQuietly(self.LAST_SETS, 1)
        self.RefreshQuietly(self.LAST_SETS, 2)
        self.RefreshQuietly(self.HISTORY_SETS, 1)
        self.RefreshQuietly(self.HISTORY_SETS, 2)
        self.RefreshQuietly(self.UPSET_FACTOR)

    def PlayerChanged(self, team, change):
        """A write changed slot 1..N of `team` (`change`: a PlayerService
        SlotChange): another player -- the head-to-head and the team's last
        sets / standings -- and / or another seed -- the upset factor. Run
        now, into the caller's save cycle."""
        if change.idChanged:
            self.RefreshQuietly(self.RECENT_SETS)
            self.RefreshQuietly(self.LAST_SETS, team)
            self.RefreshQuietly(self.HISTORY_SETS, team)
        if change.seedChanged:
            self.RefreshQuietly(self.UPSET_FACTOR)

    def TeamsChanged(self, teams):
        """An operation put other players on `teams` without loading anyone
        (a swap, a move, a clear): fetch their statistics again -- the
        head-to-head once, then each team's last sets / standings.

        Not now: once the main thread is back in its event loop, in save
        cycles of their own after the operation's, and once however many
        operations asked in between, as the board's deferred request did
        (it was emitted from the event loop, never from inside the state
        notification that noticed the change; agent-pitfalls #9). The ids
        are read then. Without a fetcher, now.

        A Later that raises takes the board's queue back (logged, not
        raised: the operation is done): a queue left behind would swallow
        every later TeamsChanged of the board."""
        board = self.scoreboardNumber
        with StatsService._queueLock:
            queued = StatsService._queuedTeams.get(board)
            first = queued is None
            StatsService._queuedTeams[board] = (queued or set()) | {
                int(team) for team in teams}
        if not first:
            return
        fetcher = StatsService.fetcher
        if fetcher is None:
            StatsService._RunQueuedTeams(board)
            return
        try:
            fetcher.Later(lambda: StatsService._RunQueuedTeams(board))
        except Exception:
            with StatsService._queueLock:
                StatsService._queuedTeams.pop(board, None)
            logger.exception(
                f"Stats: score.{board}: the statistics of the teams were "
                "not asked for again")

    @staticmethod
    def _RunQueuedTeams(board):
        with StatsService._queueLock:
            teams = StatsService._queuedTeams.pop(board, set())
        if not teams:
            return
        stats = StatsService(board)
        stats.RefreshQuietly(StatsService.RECENT_SETS)
        for team in (1, 2):
            if team in teams:
                stats.RefreshQuietly(StatsService.LAST_SETS, team)
                stats.RefreshQuietly(StatsService.HISTORY_SETS, team)

    # ── request numbers ──────────────────────────────────────────────────

    def _Ask(self, kind, team):
        """Number a new request for `kind` (/ `team`). Caller holds
        StateManager.lock."""
        key = (self.scoreboardNumber, kind, team)
        generation = next(StatsService._counter)
        StatsService._generations[key] = generation
        return generation

    def _Answer(self, kind, team, generation, ids, update):
        """The callback a fetch answers into: `update(data)`, if the request
        is still the latest one and the slots still hold `ids` (the team's
        player, or both sides' for the head-to-head)."""
        key = (self.scoreboardNumber, kind, team)

        def onResult(data):
            with StateManager.lock:
                latest = StatsService._generations.get(key) == generation
                holds = [self.PlayerId(team)] if team is not None \
                    else [self.PlayerId(1), self.PlayerId(2)]
                if not (latest and all(self._SameId(held, asked)
                                       for held, asked in zip(holds, ids))):
                    logger.debug(
                        f"Stats: {self.prefix} {kind}"
                        f"{'' if team is None else f' (team {team})'}: "
                        "dropped an answer for a superseded request")
                    return
                update(data)
        return onResult

    # ── recent sets (head to head) ───────────────────────────────────────

    def RecentSetsRequest(self):
        """(p1id, p2id, videogame) for a head-to-head fetch, or None.

        None means "there is nothing to ask for", which the caller answers
        by publishing an empty, finished result rather than leaving the
        previous pairing's sets on screen.
        """
        if not self.IsSingles():
            return None
        p1id = self.PlayerId(1)
        p2id = self.PlayerId(2)
        videogame = self.VideogameId()
        if not (p1id and p2id and videogame):
            return None
        if self._SameId(p1id, p2id):
            return None
        return p1id, p2id, videogame

    def _RefreshRecentSets(self):
        startGG = self._IsStartGG()
        with StateManager.lock:
            request = self.RecentSetsRequest() if startGG else None
            generation = self._Ask(self.RECENT_SETS, None)
            if request is None:
                self.ClearRecentSets()
                return
            self.MarkRecentSetsLoading()
        StatsService.fetcher.RecentSets(
            *request, self._Answer(self.RECENT_SETS, None, generation,
                                   request[:2], self.UpdateRecentSets))

    def MarkRecentSetsLoading(self):
        StateManager.Set(f"{self.prefix}.recent_sets",
                         {"state": "loading", "sets": []})

    def ClearRecentSets(self):
        StateManager.Set(f"{self.prefix}.recent_sets",
                         {"state": "done", "sets": []})

    def UpdateRecentSets(self, data):
        """Publish a head-to-head result, unless a newer one already landed.

        The request timestamp is the older guard (from before the requests
        were numbered) and stays: it is what the provider's answer carries.
        """
        lastUpdateTime = StateManager.Get(
            f"{self.prefix}.recent_sets.request_time", 0)
        if data.get("request_time", 0) > lastUpdateTime:
            StateManager.Set(f"{self.prefix}.recent_sets", {
                "state": "done",
                "sets": data.get("sets"),
                "request_time": data.get("request_time"),
            })

    # ── last sets (per player) ───────────────────────────────────────────

    def _RefreshLastSets(self, team):
        # Unlike the Start.gg-only statistics below, any loaded provider is
        # asked (the provider answers nothing when none is loaded).
        with StateManager.lock:
            # Numbered even when nothing is fetched: an answer still in
            # flight for an earlier request is superseded all the same.
            generation = self._Ask(self.LAST_SETS, team)
            if not self.IsSingles():
                return
            playerId = self.PlayerId(team)
            if not playerId:
                self.ClearLastSets(team)
        logger.info(f"Player {team} ID: {playerId}")
        fetcher = StatsService.fetcher
        if playerId and fetcher is not None:
            fetcher.LastSets(playerId, team, self._Answer(
                self.LAST_SETS, team, generation, [playerId],
                self.UpdateLastSets))

    def ClearLastSets(self, playerNumber):
        StateManager.Set(f"{self.prefix}.last_sets.{playerNumber}", {})

    def UpdateLastSets(self, data):
        playerNumber = data.get("playerNumber")
        with StateManager.SaveBlock():
            # Reset first so sets from the previous player don't linger when
            # the new player has fewer sets (keys are sequential 1..N).
            StateManager.Set(f"{self.prefix}.last_sets.{playerNumber}", {})
            for index, set_ in enumerate(data.get("last_sets", []), start=1):
                StateManager.Set(
                    f"{self.prefix}.last_sets.{playerNumber}.{index}", {
                        "phase_id": set_.get("phase_id"),
                        "phase_name": set_.get("phase_name"),
                        "round_name": set_.get("round_name"),
                        "player_score": set_.get("player1_score"),
                        "player_seed": set_.get("player1_seed"),
                        "player_team": set_.get("player1_team"),
                        "player_name": set_.get("player1_name"),
                        "oponent_score": set_.get("player2_score"),
                        "oponent_seed": set_.get("player2_seed"),
                        "oponent_team": set_.get("player2_team"),
                        "oponent_name": set_.get("player2_name"),
                    })

    # ── placement history (per player) ───────────────────────────────────

    def _RefreshHistorySets(self, team):
        startGG = self._IsStartGG()
        with StateManager.lock:
            # Numbered even when nothing is fetched (see _RefreshLastSets).
            generation = self._Ask(self.HISTORY_SETS, team)
            if not (startGG and self.IsSingles()):
                return
            playerId = self.PlayerId(team)
            if not playerId:
                self.ClearHistorySets(team)
                return
        StatsService.fetcher.HistorySets(
            playerId, team, self.VideogameId(), self._Answer(
                self.HISTORY_SETS, team, generation, [playerId],
                self.UpdateHistorySets))

    def ClearHistorySets(self, playerNumber):
        StateManager.Set(f"{self.prefix}.history_sets.{playerNumber}", {})

    def UpdateHistorySets(self, data):
        playerNumber = data.get("playerNumber")
        with StateManager.SaveBlock():
            # Same stale-key reset as UpdateLastSets.
            StateManager.Set(f"{self.prefix}.history_sets.{playerNumber}", {})
            for index, set_ in enumerate(
                    data.get("history_sets", []), start=1):
                eventDate = datetime.fromtimestamp(set_.get("event_date"))
                StateManager.Set(
                    f"{self.prefix}.history_sets.{playerNumber}.{index}", {
                        "placement": set_.get("placement"),
                        "event_name": set_.get("event_name"),
                        "tournament_name": set_.get("tournament_name"),
                        "entrants": set_.get("entrants"),
                        "event_date_month": eventDate.strftime("%B"),
                        "event_date_day": eventDate.strftime("%d"),
                        "event_date_year": eventDate.strftime("%Y"),
                    })

    # ── upset factor ─────────────────────────────────────────────────────

    def UpdateUpsetFactor(self):
        """Publish |seed distance| for the current pairing, or 0.

        Fully local: the seeds are already in the state, so this is the one
        statistic with no fetch behind it.
        """
        key = f"{self.prefix}.upset_factor"
        if not self.IsSingles():
            StateManager.Set(key, 0)
            return

        bracket_type = StateManager.Get(
            "score.bracket_type", "DOUBLE_ELIMINATION")
        p1 = StateManager.Get(f"{self.prefix}.team.1.player.1.seed")
        logger.info(
            f"Scoreboard {self.scoreboardNumber} - P1 Seed: " + str(p1))
        p2 = StateManager.Get(f"{self.prefix}.team.2.player.1.seed")
        logger.info(
            f"Scoreboard {self.scoreboardNumber} - P2 Seed: " + str(p2))
        if p1 and p2:
            StateManager.Set(key, abs(
                self.CalculatePlacementMath(bracket_type, int(p1))
                - self.CalculatePlacementMath(bracket_type, int(p2))))
        else:
            # One (or both) seeds missing: reset instead of leaving the
            # previous pairing's upset factor in state.
            StateManager.Set(key, 0)

    # Calculation of Seeding/Placement to determine
    # Upset Factor or Seeding Performance Rating
    #
    # bracket_type [STRING] = The bracket type returned from Start.GG
    #
    # x [INTEGER] = The seeding/placement value being used to determine
    # the end product (Ex. Seeding for UF or Seeding/Placement for SPR)
    @staticmethod
    def CalculatePlacementMath(bracket_type, x):
        # Due to how the logs works, if the player is first seed,
        # the value will always be 0 and no math needs to be done.
        if x <= 1:
            return 0

        single_elim_calc = math.floor(math.log2(x - 1))
        double_elim_calc = math.ceil(math.log2((2 * x) / 3))

        # Double Elimination Sum of Values
        if bracket_type == "DOUBLE_ELIMINATION":
            return single_elim_calc + double_elim_calc
        # Single Elimination Sum of Values
        elif bracket_type == "SINGLE_ELIMINATION":
            return single_elim_calc
        else:
            return 0

    # ── stream queue ─────────────────────────────────────────────────────
    #
    # Board-independent (it writes the two roots below, not score.{n}), but
    # it arrives from the same provider as the statistics, and the fetcher
    # relays it (AutonaStatsUtil).

    @staticmethod
    def UpdateStreamQueue(data):
        with StateManager.SaveBlock():
            StateManager.Set("streamQueue", data)
            StateManager.Set("currentStream",
                             SettingsManager.Get("twitch_username"))
