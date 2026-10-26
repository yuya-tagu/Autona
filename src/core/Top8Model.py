"""Pure data model for the Top 8 double elimination bracket.

Qt-free on purpose: this module is imported by AutonaTop8Widget (GUI) and,
unchanged, by unit tests. It owns the bracket topology (FGC standard Top 8:
4 winners-side + 4 losers-side starters) and the winner/loser propagation
logic.

State shape (StateManager keypath "top8"):
    top8.phaseLabel                  display label, default "TOP 8"
    top8.players.{1..8}              slot-fixed players (1/2=WSF1, 3/4=WSF2,
                                     5/6=LR1A, 7/8=LR1B)
    top8.matches.{MATCH_IDS}.name / slots / score / finished / active
        slots: [int, int] player slot numbers 1..8, 0 = TBD (derived here)
        active: only meaningful for gf_reset
"""

from loguru import logger

# Match processing order == topological order of the bracket graph.
MATCH_IDS = [
    "wsf1", "wsf2", "lr1a", "lr1b",
    "wf", "lqf1", "lqf2", "lsf", "lf",
    "gf", "gf_reset",
]

TBD = 0

# Player slots feeding the four starting matches.
INITIAL_SLOTS = {
    "wsf1": [1, 2],
    "wsf2": [3, 4],
    "lr1a": [5, 6],
    "lr1b": [7, 8],
}

# FGC standard cross progression: WSF1 loser -> LQF2, WSF2 loser -> LQF1
# (avoids an immediate rematch against the LR1 winner of the same half).
PROGRESSION = {
    "wsf1": {"win": ("wf", 0), "lose": ("lqf2", 1)},
    "wsf2": {"win": ("wf", 1), "lose": ("lqf1", 1)},
    "lr1a": {"win": ("lqf1", 0)},
    "lr1b": {"win": ("lqf2", 0)},
    "lqf1": {"win": ("lsf", 0)},
    "lqf2": {"win": ("lsf", 1)},
    "lsf": {"win": ("lf", 0)},
    "wf": {"win": ("gf", 0), "lose": ("lf", 1)},
    "lf": {"win": ("gf", 1)},
}

DEFAULT_ROUND_NAMES = {
    "wsf1": "Winners Semi-Final",
    "wsf2": "Winners Semi-Final",
    "wf": "Winners Final",
    "lr1a": "Losers Round 1",
    "lr1b": "Losers Round 1",
    "lqf1": "Losers Quarter-Final",
    "lqf2": "Losers Quarter-Final",
    "lsf": "Losers Semi-Final",
    "lf": "Losers Final",
    "gf": "Grand Final",
    "gf_reset": "Grand Final Reset",
}

DEFAULT_PHASE_LABEL = "TOP 8"

PLAYER_SLOTS = [str(i) for i in range(1, 9)]

# Human-readable bracket position per player slot, used by the edit panel.
SLOT_POSITIONS = {
    "1": "WSF1", "2": "WSF1",
    "3": "WSF2", "4": "WSF2",
    "5": "LR1A", "6": "LR1A",
    "7": "LR1B", "8": "LR1B",
}


def DefaultPlayer():
    return {
        "name": "",
        "sponsor": "",
        "country": {},
        "characters": {},
    }


def DefaultMatch(matchId):
    return {
        "name": DEFAULT_ROUND_NAMES.get(matchId, ""),
        "slots": [TBD, TBD],
        "score": [0, 0],
        "finished": False,
        "active": False,
    }


def DefaultState():
    return {
        "phaseLabel": DEFAULT_PHASE_LABEL,
        "players": {slot: DefaultPlayer() for slot in PLAYER_SLOTS},
        "matches": {matchId: DefaultMatch(matchId) for matchId in MATCH_IDS},
    }


def _decided(match):
    """A match produces a winner only when marked finished with a non-tie
    score."""
    score = match.get("score", [0, 0])
    return bool(match.get("finished")) and score[0] != score[1]


def _winner_index(match):
    score = match.get("score", [0, 0])
    return 0 if score[0] > score[1] else 1


def Recompute(matches):
    """Derive every match's slots from finished results and return a new
    matches dict.

    Input: current top8.matches dict (score/finished are user input, slots
    may be stale). Output: same structure with slots re-derived from
    INITIAL_SLOTS + PROGRESSION. When a match's participants change, its
    score/finished are reset to avoid displaying a result between the wrong
    players.
    """
    result = {}
    new_slots = {matchId: [TBD, TBD] for matchId in MATCH_IDS}
    for matchId, slots in INITIAL_SLOTS.items():
        new_slots[matchId] = list(slots)

    for matchId in MATCH_IDS:
        old = matches.get(matchId, {}) if isinstance(matches, dict) else {}
        match = DefaultMatch(matchId)
        if old.get("name"):
            match["name"] = old.get("name")
        match["slots"] = new_slots[matchId]

        old_slots = list(old.get("slots", [TBD, TBD]))
        participants_known = TBD not in match["slots"]
        unchanged = old_slots == match["slots"]
        if participants_known and unchanged:
            match["score"] = list(old.get("score", [0, 0]))
            match["finished"] = bool(old.get("finished"))

        if matchId == "gf_reset":
            gf = result.get("gf", {})
            # The reset set exists only when the losers-side player
            # (slot index 1) takes the first grand final.
            if _decided(gf) and _winner_index(gf) == 1:
                match["active"] = True
                match["slots"] = list(gf.get("slots", [TBD, TBD]))
                old_slots = list(old.get("slots", [TBD, TBD]))
                if old_slots == match["slots"]:
                    match["score"] = list(old.get("score", [0, 0]))
                    match["finished"] = bool(old.get("finished"))
                else:
                    match["score"] = [0, 0]
                    match["finished"] = False
            else:
                match["active"] = False
                match["slots"] = [TBD, TBD]
                match["score"] = [0, 0]
                match["finished"] = False

        result[matchId] = match

        # Propagate winner/loser forward once this match is decided.
        if matchId in PROGRESSION and _decided(match):
            win = _winner_index(match)
            routes = PROGRESSION[matchId]
            if "win" in routes:
                target, position = routes["win"]
                new_slots[target][position] = match["slots"][win]
            if "lose" in routes:
                target, position = routes["lose"]
                new_slots[target][position] = match["slots"][1 - win]

    return result


def FromBracket(bracket, entrants):
    """Extract the final 8 of a double-elimination ``core.Bracket.Bracket``
    into the fixed Top 8 topology.

    Returns ``(players, matches)`` or ``None`` when the phase group's shape
    cannot be mapped (no grand final, wrong set counts, ...).

    * ``players``: slot(str 1..8) -> {name, sponsor, country_code, mains}.
      Country/character resolution is left to the caller
      (Top8Service.ResolveImportedPlayer, against the core catalogs).
    * ``matches``: same shape as ``DefaultState()["matches"]``, with scores
      oriented to our slot layout and slots re-derived via Recompute.
    """
    rounds = getattr(bracket, "rounds", None) or {}

    def _sets(r):
        return rounds.get(str(r)) or []

    positive = sorted(int(k) for k in rounds if int(k) > 0)
    negative = sorted((int(k) for k in rounds if int(k) < 0), key=abs)
    if len(positive) < 3 or len(negative) < 4:
        logger.warning(
            f"Top8: phase group too small (winners rounds={len(positive)}, "
            f"losers rounds={len(negative)})")
        return None

    # WSF is the highest winners round with exactly 2 sets. The 1-set
    # rounds above it disambiguate WF / GF / optional Reset — checking set
    # counts from the top alone can't tell "WF, GF" from "GF, Reset".
    two_set_rounds = [r for r in positive if len(_sets(r)) == 2]
    if not two_set_rounds:
        logger.warning(
            "Top8: no winners semi-final round (progression pool?)")
        return None
    wsf_r = two_set_rounds[-1]
    above = [r for r in positive if r > wsf_r]
    if len(above) == 2:
        (wf_r, gf_r), reset_r = above, None
    elif len(above) == 3:
        wf_r, gf_r, reset_r = above
    else:
        logger.warning(
            f"Top8: unexpected winners structure above WSF ({above})")
        return None

    lf_r, lsf_r, lqf_r, lr1_r = (
        negative[-1], negative[-2], negative[-3], negative[-4])

    expected = [(wsf_r, 2), (wf_r, 1), (gf_r, 1),
                (lr1_r, 2), (lqf_r, 2), (lsf_r, 1), (lf_r, 1)]
    if reset_r is not None:
        expected.append((reset_r, 1))
    for r, count in expected:
        if len(_sets(r)) != count:
            logger.warning(
                f"Top8: round {r} has {len(_sets(r))} sets, expected {count}")
            return None

    wsf_sets = _sets(wsf_r)
    lqf_sets = _sets(lqf_r)
    lr1_sets = _sets(lr1_r)

    sgg = {
        "wsf1": wsf_sets[0], "wsf2": wsf_sets[1],
        "wf": _sets(wf_r)[0],
        "lsf": _sets(lsf_r)[0], "lf": _sets(lf_r)[0],
        "gf": _sets(gf_r)[0],
    }
    if reset_r is not None:
        sgg["gf_reset"] = _sets(reset_r)[0]

    # Align Start.gg's actual loser routing with our fixed topology
    # (lqf2 receives wsf1's loser, lqf1 receives wsf2's loser). When the
    # wiring is missing, fall back to positional order.
    lqf_from_wsf1 = getattr(wsf_sets[0], "loseNext", None)
    lqf_from_wsf2 = getattr(wsf_sets[1], "loseNext", None)
    if (lqf_from_wsf1 in lqf_sets and lqf_from_wsf2 in lqf_sets
            and lqf_from_wsf1 is not lqf_from_wsf2):
        sgg["lqf2"], sgg["lqf1"] = lqf_from_wsf1, lqf_from_wsf2
    else:
        logger.warning("Top8: LQF wiring unresolved, using positional order")
        sgg["lqf1"], sgg["lqf2"] = lqf_sets[0], lqf_sets[1]

    lr1_to_lqf1 = [s for s in lr1_sets
                   if getattr(s, "winNext", None) is sgg["lqf1"]]
    lr1_to_lqf2 = [s for s in lr1_sets
                   if getattr(s, "winNext", None) is sgg["lqf2"]]
    if lr1_to_lqf1 and lr1_to_lqf2:
        sgg["lr1a"], sgg["lr1b"] = lr1_to_lqf1[0], lr1_to_lqf2[0]
    else:
        logger.warning("Top8: LR1 wiring unresolved, using positional order")
        sgg["lr1a"], sgg["lr1b"] = lr1_sets[0], lr1_sets[1]

    # Players: the 8 starting positions, mapped seed -> our slot number.
    players = {}
    seed_to_slot = {}
    for match_id, slot_a, slot_b in (
            ("wsf1", 1, 2), ("wsf2", 3, 4),
            ("lr1a", 5, 6), ("lr1b", 7, 8)):
        bs = sgg[match_id]
        for pos, slot in ((0, slot_a), (1, slot_b)):
            seed = bs.playerIds[pos]
            players[str(slot)] = _entrant_to_player(entrants, seed)
            if isinstance(seed, int) and seed > 0:
                seed_to_slot[seed] = slot

    # Matches: apply each finished set's score in topological order,
    # re-deriving slots after every application so orientation checks
    # always compare against resolved participants.
    matches = Recompute({mid: DefaultMatch(mid) for mid in MATCH_IDS})
    for match_id in MATCH_IDS:
        bs = sgg.get(match_id)
        if bs is None:
            continue
        score = list(getattr(bs, "score", [0, 0]) or [0, 0])
        finished = bool(getattr(bs, "finished", False))
        if not finished or score[0] == score[1]:
            continue

        ours = matches[match_id]["slots"]
        theirs = [seed_to_slot.get(p, TBD) for p in bs.playerIds]
        if TBD in ours or TBD in theirs:
            logger.warning(
                f"Top8: participants unresolved for {match_id} "
                f"(ours={ours}, startgg={theirs}), skipping its score")
            continue
        if theirs == ours:
            pass
        elif theirs == ours[::-1]:
            score = score[::-1]
        else:
            logger.warning(
                f"Top8: participant mismatch for {match_id} "
                f"(ours={ours}, startgg={theirs}), skipping its score")
            continue

        matches[match_id]["score"] = score
        matches[match_id]["finished"] = True
        matches = Recompute(matches)

    return players, matches


def _entrant_to_player(entrants, seed):
    """entrants[seed-1] -> plain player dict (Qt-free fields only)."""
    empty = {"name": "", "sponsor": "", "country_code": None, "mains": {}}
    if not isinstance(seed, int) or seed <= 0:
        return dict(empty)
    if seed > len(entrants or []):
        return dict(empty)
    team = entrants[seed - 1] or {}
    members = team.get("players") or []
    if not members:
        return dict(empty)
    data = members[0] or {}
    return {
        "name": data.get("gamerTag") or "",
        "sponsor": data.get("prefix") or "",
        "country_code": data.get("country_code"),
        "mains": data.get("mains") or {},
    }
