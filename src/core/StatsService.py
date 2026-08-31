"""Per-board statistics: what is asked for, and what lands in the state.

The stats are the textbook case of write category (b) in
docs/changes/headless-core-redesign.md §2 -- "data-load results poured into
the state". Nothing here is an operator edit: a fetch is triggered, a
Start.gg worker answers some time later, and the answer is written under
``score.{n}.*`` for the overlays and the Web UI to render. None of that
needs a widget, and until Step 4a all of it lived in ``AutonaStatsUtil``
purely because that class happened to own the Qt signals used as the
trigger and the result carrier.

What stayed on the Qt side (``src/AutonaStatsUtil.py``) is exactly the
transport:

- the six trigger signals, which are how a Flask thread or a player-widget
  edit asks for a recalculation on the GUI thread;
- the three result signals, which are how a Start.gg worker thread hands
  its payload back;
- ``AutonaTournamentDataProvider``, whose Worker/QThreadPool machinery is
  explicitly out of scope for this step.

What moved here is every decision and every write: whether a board is
eligible for a stat at all, which ids the fetch needs, the upset-factor
math, and the shape of each subtree that is published. That is what makes
the calculations testable without a QApplication -- and it is why this
class writes to ``StateManager`` directly rather than handing values back
to the widget to write.

``score.{n}.{last_sets,history_sets,recent_sets,upset_factor}`` are the
paths StateSchema.STATS_KEYS exempts from the 反映 hold: fetched data
publishes as soon as it lands, because no operator would think to press
反映 for numbers they did not type.

Import side effects: none (agent-pitfalls #1). One instance per board, made
by that board's AutonaStatsUtil.
"""
import math
from datetime import datetime

import orjson
from loguru import logger

from .SettingsManager import SettingsManager
from .StateManager import StateManager


class StatsService:
    """The stats of one scoreboard. Bound to ``score.{scoreboardNumber}``.

    ``playersPerTeam`` is a callable returning how many players each team
    of that board currently has. Every statistic here is defined for 1v1
    sets only -- a "recent sets between these two" means nothing for a
    5-man crew -- and that count is still owned by the Qt board (its
    ``team1playerWidgets`` list), so it is injected rather than read. Note
    what is *not* done: reading ``len(score.{n}.team.1.player)`` from the
    state instead. It would be one key lookup and it would be wrong today,
    because the player subtree is written by the player widgets as they are
    built and torn down; making the state the authority on the roster size
    belongs with the player-subtree migration (Step 5).
    """

    def __init__(self, scoreboardNumber, playersPerTeam):
        self.scoreboardNumber = scoreboardNumber
        self._playersPerTeam = playersPerTeam

    # ── helpers ──────────────────────────────────────────────────────────

    @property
    def prefix(self) -> str:
        return f"score.{self.scoreboardNumber}"

    def IsSingles(self) -> bool:
        """True when each side is one player, the only case stats cover."""
        return self._playersPerTeam() == 1

    def PlayerId(self, team):
        """The provider id of team `team`'s first player (1-based team)."""
        return StateManager.Get(f"{self.prefix}.team.{team}.player.1.id")

    @staticmethod
    def VideogameId():
        """The loaded tournament's game, as the provider queries want it."""
        return StateManager.Get("provider_videogame.id")

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
        # Ids are provider payloads (lists as often as scalars), so they are
        # compared by their serialised form rather than with ==.
        if orjson.dumps(p1id) == orjson.dumps(p2id):
            return None
        return p1id, p2id, videogame

    def MarkRecentSetsLoading(self):
        StateManager.Set(f"{self.prefix}.recent_sets",
                         {"state": "loading", "sets": []})

    def ClearRecentSets(self):
        StateManager.Set(f"{self.prefix}.recent_sets",
                         {"state": "done", "sets": []})

    def UpdateRecentSets(self, data):
        """Publish a head-to-head result, unless a newer one already landed.

        Two fetches can be in flight at once (both player ids changing in
        one set load), and the thread pool gives no ordering guarantee, so
        the request timestamp decides which answer wins.
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
    # it arrives on the same provider signal every board listens to, so it
    # stays here rather than growing a second owner.

    def UpdateStreamQueue(self, data):
        with StateManager.SaveBlock():
            StateManager.Set("streamQueue", data)
            StateManager.Set("currentStream",
                             SettingsManager.Get("twitch_username"))
