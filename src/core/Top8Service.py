"""The write path for the TOP 8 bracket (``top8.*``).

The topology -- who plays whom, where a winner goes, what an undecided match
looks like -- has always been Qt-free, in ``src/core/Top8Model.py``. What was
not was the deciding *when* to write it: the panel merged staged spin values
into the published matches, re-ran ``Recompute``, and wrote the two subtrees
itself, from six ``StateManager.Set`` calls that spelled ``top8.players`` /
``top8.matches`` out by hand. This module is that half (headless core Step 5,
instalment 5).

The panel's edits come here one field at a time (``SetPlayerText`` /
``SetPlayerCountry`` / ``SetPlayerCharacter``, ADR-005 S3e-8): it renders
``top8.*`` and writes only the field the operator changed. A player's country
and character still come out of the panel's Qt models (the country combo's
row, the skin's data), which is display work; the S4 rework replaces them
with identifiers, as it did for the player cards.

The Start.gg import is here too (S3e-8): ``RefreshFromProvider`` fetches the
tournament's phases, picks the final double elimination group, fetches it,
extracts its Top 8 (``core/Bracket`` + ``Top8Model.FromBracket``), resolves
each player's country and character from the core catalogs and imports the
result. It used to be the panel's (a phases cache, the provider signals it
listened to, a build worker, the resolution against the Qt asset adapter);
the panel's load button, the hotkey and the web routes all call this now, in
either shell, built panel or not. The provider reads are the shell's,
through the registered ``fetcher`` (core imports no ``src`` module), which
runs the whole import -- fetches, build, write -- on the provider's worker
thread.

A game switch empties the players' characters (``ForgetCharacters``):
a character of one game means nothing in another. The catalog calls it
from ``SelectGame`` -- the one writer of ``game.*``, shared by every shell
-- in the same save cycle as the switch.

Module-level functions rather than a class with an ``instance``: there is
nothing per-board (contrast ``SetLoadService``, one per board, or
``TournamentInfoService``, which keeps the raw dates and the last tournament
name). The only thing remembered between calls is whether an import is in
progress. ``bootstrap_core()`` creates nothing; it registers the fetcher.

Import side effects: none (agent-pitfalls #1). Qt-free, like everything under
``src/core``.
"""
import threading

from loguru import logger

from . import Top8Model as top8
from . import StateSchema as schema
from .Bracket import Bracket
from .CountryCatalog import CountryCatalog
from .DictHelper import deep_clone
from .GameAssetCatalog import GameAssetCatalog
from .StateManager import StateManager

PLAYERS_PATH = f"{schema.TOP8_ROOT}.{schema.TOP8_PLAYERS_KEY}"
MATCHES_PATH = f"{schema.TOP8_ROOT}.{schema.TOP8_MATCHES_KEY}"
PHASE_LABEL_PATH = f"{schema.TOP8_ROOT}.{schema.TOP8_PHASE_LABEL_KEY}"


# The text fields of a player the panel edits.
PLAYER_TEXT_FIELDS = ("name", "sponsor")


def _PlayerKey(slot, field):
    return f"{PLAYERS_PATH}.{slot}.{field}"


def SetPlayerText(slot, field, text) -> bool:
    """One text field of one player (the panel's name / sponsor), stored
    trimmed. True if the state changed.

    Only that key, and only when it changes (ADR-005 S3e-8). Until S3e-8
    every edit republished all eight players from the panel's inputs, which
    once the panel follows the state would put whatever another input was
    still showing over a newer value (an import, another client).
    """
    slot = str(slot)
    if slot not in top8.PLAYER_SLOTS or field not in PLAYER_TEXT_FIELDS:
        logger.warning(f"Top8: ignoring {field!r} of player {slot}")
        return False
    value = text.strip() if isinstance(text, str) else ""
    with StateManager.lock:
        return StateManager.SetIfChanged(_PlayerKey(slot, field), value)


def SetPlayerCountry(slot, country) -> bool:
    """One player's country, as the panel's country row holds it ({} for
    none). Written only when it changes; True if it did."""
    slot = str(slot)
    if slot not in top8.PLAYER_SLOTS:
        logger.warning(f"Top8: ignoring the country of player {slot}")
        return False
    value = country if isinstance(country, dict) else {}
    with StateManager.lock:
        return StateManager.SetIfChanged(_PlayerKey(slot, "country"), value)


def SetPlayerCharacter(slot, character) -> bool:
    """One player's character, as the panel resolves it from its combos
    (the skin's data, else the character's), stored as ``{"1": character}``
    -- ``{}`` for none. Written only when it changes; True if it did."""
    slot = str(slot)
    if slot not in top8.PLAYER_SLOTS:
        logger.warning(f"Top8: ignoring the character of player {slot}")
        return False
    value = {"1": character} if isinstance(character, dict) and character \
        else {}
    with StateManager.lock:
        return StateManager.SetIfChanged(
            _PlayerKey(slot, "characters"), value)


def ForgetCharacters():
    """Empty every player's character -- the skin goes with it, it is the
    character entry -- because the loaded game changed to another one.

    Called by ``GameAssetCatalog.SelectGame`` when a load replaces one game
    with a different one, never on the first load of a session (the saved
    Top 8 is restored with its characters) nor on a reload of the same
    game. A slot without a character is not written, so the delta carries
    only what changed; one save cycle for all of them (and, under the
    catalog's save block, the same cycle as the switch itself). Returns the
    slots emptied.
    """
    with StateManager.SaveBlock():
        with StateManager.lock:
            players = StateManager.Get(PLAYERS_PATH)
            players = players if isinstance(players, dict) else {}
            slots = [slot for slot in top8.PLAYER_SLOTS
                     if isinstance(players.get(slot), dict)
                     and players[slot].get("characters")]
            for slot in slots:
                StateManager.Set(_PlayerKey(slot, "characters"), {})
    return slots


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


# ── the Start.gg import (ADR-005 S3e-8) ──────────────────────────────────

# How the import reaches the tournament provider, registered by the shell
# (bootstrap_core(): AutonaTournamentDataProvider.RunWithProvider).
# fetcher(job, status) runs job(provider) on the provider's worker thread --
# showing `status` while it runs, where the shell has somewhere to show it
# -- and returns True, or returns False, running nothing, when no
# tournament is loaded. `provider` answers GetTournamentPhases() and
# GetTournamentPhaseGroup(id), blocking, in the Start.gg provider's shapes
# (an empty list / dict when a request failed). None: nothing can import.
fetcher = None

IMPORT_STATUS = "TOP 8 を構築中..."

# What RefreshFromProvider answers.
STARTED = "started"
BUSY = "busy"
NO_TOURNAMENT = "no tournament"

# One import at a time, whoever asked (the panel, the hotkey, the web
# routes): the in-progress flag, read and set under its lock.
_refreshLock = threading.Lock()
_refreshing = False


def Refreshing() -> bool:
    """Whether an import is in progress."""
    with _refreshLock:
        return _refreshing


def RefreshFromProvider(done=None):
    """Import the tournament's Top 8 from Start.gg, off the calling thread.

    Answers at once: ``STARTED`` (the import runs on the provider's worker),
    ``BUSY`` (one is already in progress; this request is dropped, not
    queued -- it would fetch the same thing) or ``NO_TOURNAMENT`` (no
    tournament is loaded, or no shell registered a fetcher). `done()` is
    called once the attempt is over, whichever way it went: on the worker
    thread after the flag is released, or right away when nothing started
    (not for ``BUSY``: that request never began).

    What the import does: ``ImportFromProvider``.
    """
    global _refreshing
    with _refreshLock:
        if _refreshing:
            logger.info("Top8: an import is already in progress, skipping")
            return BUSY
        _refreshing = True

    def job(provider):
        try:
            ImportFromProvider(provider)
        except Exception:
            logger.exception("Top8: the import failed")
        finally:
            _Finish(done)

    try:
        started = fetcher is not None and bool(fetcher(job, IMPORT_STATUS))
    except Exception:
        _Finish(done)
        raise
    if not started:
        if fetcher is None:
            logger.warning("Top8: no fetcher registered, cannot import")
        else:
            logger.info("Top8: no tournament set, skipping the import")
        _Finish(done)
        return NO_TOURNAMENT
    return STARTED


def _Finish(done):
    global _refreshing
    with _refreshLock:
        _refreshing = False
    if done is not None:
        try:
            done()
        except Exception:
            logger.exception("Top8: the import's completion callback failed")


def TargetPhaseGroup(phases):
    """The phase group the import reads: in the last phase (Start.gg's
    order) that has a double elimination group, its first such group (a
    finals phase normally has exactly one). None when there is none, or it
    has no id."""
    for phase in reversed(phases if isinstance(phases, list) else []):
        groups = phase.get("groups") if isinstance(phase, dict) else None
        de_groups = [group for group in (groups or [])
                     if isinstance(group, dict)
                     and group.get("bracketType") == "DOUBLE_ELIMINATION"]
        if de_groups:
            return de_groups[0] if de_groups[0].get("id") is not None \
                else None
    return None


def ImportFromProvider(provider) -> bool:
    """Fetch the final double elimination group from `provider` and import
    its Top 8. Blocking (the fetcher's worker thread). True if imported.

    The phases are fetched every time: they are the tournament's, read
    when it is asked for, so a switch of tournament or a new phase cannot
    leave a stale target behind (the panel used to cache them). Every way
    it can come back short -- no phases, no such group, an empty or
    unmappable group (agent-pitfalls #11: a failed request is an empty
    result) -- writes nothing.
    """
    group = TargetPhaseGroup(provider.GetTournamentPhases())
    if group is None:
        logger.info("Top8: no double elimination phase found, skipping")
        return False
    return ImportPhaseGroup(provider.GetTournamentPhaseGroup(group.get("id")))


def ImportPhaseGroup(phaseGroupData, games=None, countries=None) -> bool:
    """Extract the Top 8 of a fetched phase group and import it.

    The bracket's final 8 (``Top8Model.FromBracket``), each player resolved
    (``ResolveImportedPlayer``, against one ``GameAssetCatalog.Current()``),
    then ``ImportBracket``: players and matches in one save cycle. Nothing
    is written when the group cannot be mapped. True if imported. The
    catalogs default to the ``.instance`` at call time.
    """
    if not isinstance(phaseGroupData, dict):
        logger.warning("Top8: the phase group is not a mapping, skipping")
        return False
    # No round names (matchNames): FromBracket reads the bracket's
    # topology, never its labels -- Top8Model names its own matches.
    extracted = top8.FromBracket(
        Bracket(phaseGroupData), phaseGroupData.get("entrants") or [])
    if extracted is None:
        logger.warning("Top8: could not extract a Top 8 from the phase group")
        return False
    players, matches = extracted
    games = games or GameAssetCatalog.instance
    countries = countries or CountryCatalog.instance
    # Resolved and written under the state lock, against the game loaded at
    # that moment. A game switch swaps the snapshot first and only then
    # empties the characters (ForgetCharacters), which needs this lock: if
    # this read saw the old game, the emptying comes after this write. An
    # old game's characters are never left behind on the new one.
    with StateManager.lock:
        snapshot = games.Current()
        resolved = {slot: ResolveImportedPlayer(player, snapshot, countries)
                    for slot, player in players.items()}
        ImportBracket(resolved, matches)
    return True


def ResolveImportedPlayer(imported, snapshot, countries):
    """A ``FromBracket`` player as the state holds it: name and sponsor, the
    country from its code (``CountryCatalog.BasicCountryInfo``), the
    character from the first of the player's mains in the loaded game.

    `snapshot` is a ``GameAssetCatalog.Current()``. The character is the
    catalog's entry (a copy), found by its key (en_name) or else by its
    codename -- what the panel resolved through the Qt asset adapter,
    which reads the same snapshot, until S3e-8.
    """
    player = top8.DefaultPlayer()
    player["name"] = imported.get("name", "")
    player["sponsor"] = imported.get("sponsor", "")

    code = imported.get("country_code")
    if code:
        country = countries.BasicCountryInfo(code)
        if country:
            player["country"] = country

    mains = imported.get("mains") or {}
    game = snapshot.game
    if game and isinstance(mains, dict):
        game_mains = mains.get(game.get("codename", ""), [])
        if game_mains and game_mains[0]:
            char = _CharacterFromCodename(snapshot, game_mains[0][0])
            if char:
                player["characters"] = {"1": char}
    return player


def _CharacterFromCodename(snapshot, codename):
    characters = snapshot.characters
    entry = characters.get(codename)
    if not entry:
        for value in characters.values():
            if value.get("codename") == codename:
                entry = value
                break
    # A copy: the entry is the catalog's own dict, and this one goes into
    # the state (top8.*.characters).
    return deep_clone(entry) if entry else {}
