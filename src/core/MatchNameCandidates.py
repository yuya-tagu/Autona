"""The names the match bar's editable combos offer (ADR-005 S4x).

The phase / pool / round combos of a board offer the localized terms
(AutonaLocaleHelper's candidate lists: the phase terms, the "group" template
for four pools, the ladder of round names). On boards 1/2 the loaded
tournament adds its own: the real pool names of its phases with several
groups, and "Winners TOP{n}" / "Losers TOP{n}" for its entrant count (both
sides of a double elimination open with the whole field). The crew board's
combos offer the terms alone, as they always did.

The tournament's part was each 1v1 / team board's widget until ADR-005 S4x
(`_UpdatePoolCandidates` / `_UpdateEntrantsMatchItem`, on the provider's
signals), so a board built after the tournament loaded never had it, and
nothing outside the desktop could ask. It is kept here now, fed by the
provider's two payloads (bootstrap_core() connects them, ahead of every
reader), and read by the boards' combos and GET /api/v1/match-names/candidates
alike (`Candidates(board)`), so both offer the same lists in the same order.

Not the state: what a combo offers is not what the overlays show. The
localized lists and the round templates are AutonaLocaleHelper's, read when
asked (`generic` / `matchNames`, injected: core may not import it).
"""
import threading

from loguru import logger

PHASE = "phase"
POOL = "pool"
MATCH = "match"
KINDS = (PHASE, POOL, MATCH)

# The boards whose combos offer the tournament's names too (1v1 / team).
TOURNAMENT_BOARDS = (1, 2)

# The entrant-count round names: their term keys and the shipped English
# template used when the terms lack one (the widget's fallbacks).
ENTRANT_ROUNDS = (("winners_top_n", "Winners TOP{0}"),
                  ("losers_top_n", "Losers TOP{0}"))


class MatchNameCandidates:
    # Created once by bootstrap_core().
    instance: "MatchNameCandidates" = None

    def __init__(self, generic=None, matchNames=None):
        # `generic() -> {"phase": [...], "pool": [...], "match": [...]}`: the
        # localized lists (AutonaLocaleHelper's Get*NameCandidates).
        # `matchNames() -> dict`: the round terms, for the entrant rounds.
        self._generic = generic
        self._matchNames = matchNames
        # What the loaded tournament adds, from the provider's last payloads.
        # Guarded by _lock: fed on the main thread, read by the web server's.
        self._lock = threading.Lock()
        self._entrants = 0
        self._pools = ()

    # ── what the provider says (bootstrap_core() connects these) ─────────

    def FollowTournamentData(self, data):
        """tournament_data_updated: the entrant count of the payload. A
        payload without one -- the empty one of an unloaded tournament
        included -- takes the entrant rounds away, as the board did."""
        if not isinstance(data, dict):
            return
        try:
            entrants = int(data.get("numEntrants") or 0)
        except (TypeError, ValueError):
            entrants = 0
        with self._lock:
            self._entrants = max(0, entrants)

    def FollowPhases(self, phases):
        """tournament_phases_updated: the names of the groups of every phase
        that has several (a single group's name says nothing), each once, in
        the order the phases list them. An empty list (an unloaded
        tournament) takes them away."""
        names = []
        for phase in (phases if isinstance(phases, (list, tuple)) else ()):
            groups = phase.get("groups") if isinstance(phase, dict) else None
            if not isinstance(groups, (list, tuple)) or len(groups) < 2:
                continue
            for group in groups:
                name = group.get("name") if isinstance(group, dict) else None
                if isinstance(name, str) and name and name not in names:
                    names.append(name)
        with self._lock:
            self._pools = tuple(names)

    # ── what a combo offers ──────────────────────────────────────────────

    def Candidates(self, board=None) -> dict:
        """{"phase", "pool", "match"}: the lists board `board`'s combos offer,
        in their order, without the blank first row. Boards 1/2 put the
        tournament's names ahead of the terms -- its pools ahead of the
        generic ones, its entrant rounds ahead of the ladder -- each only
        where the terms do not offer it already (there it stays in its
        place, as the board left it). Any other board (the crew board, none
        given): the terms."""
        lists = self._GenericLists()
        if board not in TOURNAMENT_BOARDS:
            return lists
        with self._lock:
            entrants, pools = self._entrants, list(self._pools)
        return {
            PHASE: lists[PHASE],
            POOL: _Ahead(pools, lists[POOL]),
            MATCH: _Ahead(self._EntrantRounds(entrants), lists[MATCH]),
        }

    def _GenericLists(self) -> dict:
        try:
            lists = self._generic() if self._generic is not None else {}
        except Exception:
            # A term file .format() rejects is logged where the lists are
            # built; a combo with no terms is still a combo.
            logger.exception("The match name candidates could not be built")
            lists = {}
        if not isinstance(lists, dict):
            lists = {}
        return {kind: [name for name in (lists.get(kind) or ())
                       if isinstance(name, str)]
                for kind in KINDS}

    def _EntrantRounds(self, entrants) -> list:
        if entrants <= 0:
            return []
        try:
            terms = self._matchNames() if self._matchNames is not None else {}
        except Exception:
            terms = {}
        if not isinstance(terms, dict):
            terms = {}
        rounds = []
        for key, fallback in ENTRANT_ROUNDS:
            template = terms.get(key, fallback)
            # A term that is not text is no name (the user's terms file can
            # hold anything): left out, as one .format() rejects.
            if not isinstance(template, str):
                logger.warning(f"The round term {key} is not text: "
                               f"{template!r}")
                continue
            try:
                rounds.append(template.format(entrants))
            except (IndexError, KeyError, ValueError, AttributeError,
                    TypeError):
                logger.warning(f"The round term {key} cannot be formatted: "
                               f"{template!r}")
        return rounds


def _Ahead(first, rest) -> list:
    """`first`'s names the `rest` does not offer, each once, then `rest`."""
    ahead = []
    for name in first:
        if name and name not in rest and name not in ahead:
            ahead.append(name)
    return ahead + list(rest)
