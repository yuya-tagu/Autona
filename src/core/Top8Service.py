"""The write path for the TOP 8 bracket (``top8.*``).

The topology -- who plays whom, where a winner goes, what an undecided match
looks like -- has always been Qt-free, in ``src/core/Top8Model.py``. What was
not was the deciding *when* to write it: the panel merged staged spin values
into the published matches, re-ran ``Recompute``, and wrote the two subtrees
itself, from six ``StateManager.Set`` calls that spelled ``top8.players`` /
``top8.matches`` out by hand. This module is that half (headless core Step 5,
instalment 5).

The split it draws is the same one ``PlayerService`` draws: the panel still
*assembles* a player entry, because a player's country and character come out
of Qt models (``AutonaCountryHelper`` rows, ``AutonaGameAssetManager`` skins)
and resolving those is display work. Everything downstream of "here are the
eight entries" is here.

Module-level functions rather than a class with an ``instance``: there is
nothing per-board and nothing to remember between calls (contrast
``SetLoadService``, one per board, or ``TournamentInfoService``, which keeps
the raw dates and the last tournament name). ``bootstrap_core()`` therefore
has nothing to create.

Import side effects: none (agent-pitfalls #1). Qt-free, like everything under
``src/core``.
"""
from . import Top8Model as top8
from . import StateSchema as schema
from .StateManager import StateManager

PLAYERS_PATH = f"{schema.TOP8_ROOT}.{schema.TOP8_PLAYERS_KEY}"
MATCHES_PATH = f"{schema.TOP8_ROOT}.{schema.TOP8_MATCHES_KEY}"
PHASE_LABEL_PATH = f"{schema.TOP8_ROOT}.{schema.TOP8_PHASE_LABEL_KEY}"


def PublishPlayers(players):
    """Publish the eight player entries as one subtree.

    Whole-subtree, not per-slot: a match row renders the *name* of whichever
    slot it points at, so a half-applied edit would show two players who were
    never on screen together.
    """
    StateManager.Set(PLAYERS_PATH, players)


def SetPhaseLabel(text):
    """Publish the overlay's phase caption, falling back to the default.

    The empty string is not a value the overlay can render -- it would draw a
    bracket with no heading -- so a cleared field means "back to TOP 8". The
    panel used to apply that rule on its way to the write; it is the rule
    itself, so it lives with the write.
    """
    label = (text or "").strip() or top8.DEFAULT_PHASE_LABEL
    StateManager.Set(PHASE_LABEL_PATH, label)
    return label


def ApplyScores(scores):
    """Apply the staged scores (反映) and propagate the winners.

    `scores` is `{match_id: [s1, s2]}` -- what the panel's spin boxes hold,
    which is deliberately *not* what the state holds: the TOP 8 panel stages
    edits and publishes them on 反映, so the spin values and `top8.matches`
    disagree until this runs.

    A match counts as finished when its applied score is not a tie; there is
    no separate confirm control. Recompute then rewrites every downstream
    match's slots (and can reset ones that are no longer reachable), so the
    caller re-renders from the returned dict rather than from what it sent.

    The caller is expected to pass every match's staged score, not just the
    ones that changed: any match id missing from `scores` is dropped from
    the result, since `matches` here is built only from what was iterated,
    not merged with `current`. The one existing call site (`AutonaTop8Widget
    ._onApplyScores`) always passes the full row of 8 matches, so this has
    not bitten in practice, but a partial dict silently loses the omitted
    matches' results.
    """
    # StateManager.Get returns a live reference -- never mutate it in place,
    # or DeepDiff sees no change and the delta is never broadcast.
    current = StateManager.Get(MATCHES_PATH) or {}
    matches = {}
    for matchId, score in scores.items():
        match = dict(current.get(matchId) or top8.DefaultMatch(matchId))
        # A list, not whatever sequence the caller passed: the score is
        # exported as JSON and compared against a restored one.
        score = list(score)
        match["score"] = score
        match["finished"] = score[0] != score[1]
        matches[matchId] = match
    matches = top8.Recompute(matches)
    StateManager.Set(MATCHES_PATH, matches)
    return matches


def ResetMatches():
    """Clear every score and finished flag (全リセット). Returns the result."""
    matches = top8.Recompute(
        {matchId: top8.DefaultMatch(matchId) for matchId in top8.MATCH_IDS})
    StateManager.Set(MATCHES_PATH, matches)
    return matches


def ImportBracket(players, matches):
    """Publish a Top 8 extracted from a Start.gg phase group.

    One save cycle for both subtrees: the imported matches point at the
    imported players by slot, and publishing them separately would put a
    frame of the overlay on screen with the new bracket and the old names.
    """
    matches = top8.Recompute(matches)
    with StateManager.SaveBlock():
        StateManager.Set(PLAYERS_PATH, players)
        StateManager.Set(MATCHES_PATH, matches)
    return matches
