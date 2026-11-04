"""The write path for the TOP 8 bracket (``top8.*``).

The topology -- who plays whom, where a winner goes, what an undecided match
looks like -- has always been Qt-free, in ``src/core/Top8Model.py``. What was
not was the deciding *when* to write it: the panel merged staged spin values
into the published matches, re-ran ``Recompute``, and wrote the two subtrees
itself, from six ``StateManager.Set`` calls that spelled ``top8.players`` /
``top8.matches`` out by hand. This module is that half (headless core Step 5,
instalment 5).

The Top 8 page's edits of a player come here as one write (``UpdatePlayer``:
the fields of one PATCH /api/v1/top8/players/<slot>, resolved against one
snapshot of the catalogs, all or none, S4i); one field at a time,
``SetPlayerText`` / ``SetPlayerCountry`` / ``SetPlayerCharacter`` (ADR-005
S3e-8; the Qt panel's until S4i made it a React page writing through
/api/v1). The page renders ``top8.*`` and writes only the fields the
operator changed. Since ADR-005 S4h
a country and a character come as identifiers -- a country code, a
character's en_name and its skin row -- and the values are built here from
the catalogs, with the player cards' builders (``PlayerService
.BuildCountryValue`` / ``BuildCharacterValue``), with the ``assets`` the
overlay draws its icon from. An import writes the character's config as it
always did, with those keys added (``ImportedCharacterValue``).
``CharacterPick`` reads either back -- and the shapes a slot held before
S4h -- as the identifiers that build it.

The Start.gg import is here too (S3e-8): ``RefreshFromProvider`` fetches the
tournament's phases, picks the final double elimination group, fetches it,
extracts its Top 8 (``core/Bracket`` + ``Top8Model.FromBracket``), resolves
each player's country and character from the core catalogs and imports the
result. It used to be the panel's (a phases cache, the provider signals it
listened to, a build worker, the resolution against the Qt asset adapter);
the page's load button (POST /api/v1/bracket:refresh), the hotkey and the
other web routes all call this now, in either shell. The provider reads are the shell's,
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
from .PlayerService import (BuildCharacterValue, BuildCountryValue,
                            CharacterSlot)
from .StateManager import StateManager
from .TaskExecutor import TaskExecutor

PLAYERS_PATH = f"{schema.TOP8_ROOT}.{schema.TOP8_PLAYERS_KEY}"
MATCHES_PATH = f"{schema.TOP8_ROOT}.{schema.TOP8_MATCHES_KEY}"
PHASE_LABEL_PATH = f"{schema.TOP8_ROOT}.{schema.TOP8_PHASE_LABEL_KEY}"


# The text fields of a player the Top 8 page edits.
PLAYER_TEXT_FIELDS = ("name", "sponsor")

# A match score's bound (ApplyScores; the Qt panel's spin boxes' range,
# the Top 8 page's score fields' since ADR-005 S4i).
SCORE_MAX = 99


def _PlayerKey(slot, field):
    return f"{PLAYERS_PATH}.{slot}.{field}"


def SetPlayerText(slot, field, text) -> bool:
    """One text field of one player (the Top 8 page's name / sponsor),
    stored trimmed. True if the state changed.

    Only that key, and only when it changes (ADR-005 S3e-8). Until S3e-8
    every edit of the Qt panel republished all eight players from its
    inputs, which -- once the panel followed the state -- would have put
    whatever another input was still showing over a newer value (an
    import, another client).
    """
    slot = str(slot)
    if slot not in top8.PLAYER_SLOTS or field not in PLAYER_TEXT_FIELDS:
        logger.warning(f"Top8: ignoring {field!r} of player {slot}")
        return False
    value = text.strip() if isinstance(text, str) else ""
    with StateManager.lock:
        return StateManager.SetIfChanged(_PlayerKey(slot, field), value)


def CountryValue(code, countries=None) -> dict:
    """What a player's ``country`` holds for the country (or custom flag)
    `code`: the player cards' value (``PlayerService.BuildCountryValue``),
    same shape and key order. {} for none and for a code the catalog does
    not know."""
    return BuildCountryValue(code, countries)


def SetPlayerCountry(slot, code, countries=None) -> bool:
    """One player's country, by its code (a custom flag's is its name):
    the value is built from ``CountryCatalog`` (ADR-005 S4h; it used to be
    the panel's country row). None / "" empties it. A code the catalog does
    not know is not written (a warning; False), so a stray identifier never
    empties a country. Written only when it changes; True if it did."""
    slot = str(slot)
    if slot not in top8.PLAYER_SLOTS:
        logger.warning(f"Top8: ignoring the country of player {slot}")
        return False
    if code is None or code == "":
        value = {}
    elif not isinstance(code, str):
        logger.warning(f"Top8: ignoring the country {code!r} of player {slot}")
        return False
    else:
        value = CountryValue(code, countries)
        if not value:
            logger.warning(
                f"Top8: no country {code!r}, player {slot} left as it is")
            return False
    with StateManager.lock:
        return StateManager.SetIfChanged(_PlayerKey(slot, "country"), value)


# What UpdatePlayer refuses: the field whose identifier named nothing, or
# a character picked on another game's list than the loaded one.
UNKNOWN_COUNTRY = "country"
UNKNOWN_CHARACTER = "character"
UNKNOWN_SKIN = "skin"
OTHER_GAME = "game"


def UpdatePlayer(slot, fields, games=None, countries=None):
    """Some of one player's fields in one save cycle (the Top 8 page's
    ``PATCH /api/v1/top8/players/<slot>``, ADR-005 S4i).

    `fields` holds some of: ``name`` / ``sponsor`` (str); ``country`` (a
    code -- a custom flag's is its name -- or None); ``character``
    (``(en_name, skin row, codename)`` or None; the codename is the game
    whose list the character was picked on, None not to check it).

    Under the state lock, every identifier is resolved first, against one
    reference to each catalog -- one ``GameAssetCatalog.Current()``
    snapshot, one country lookup -- and the values written are the ones
    resolved. Nothing is written when one names nothing, and the answer
    says which (``UNKNOWN_*``); a character picked on another game's list
    than the one loaded (a game switch the page has not followed yet) is
    ``OTHER_GAME``. A skin that is not one of the character's rows is
    refused here (``CharacterValue`` alone would take the first: a client's
    typo never shows another colour); a character without skins takes any.
    Else None.

    The lock is taken before the save block (agent-pitfalls #17), and the
    game is read under it: a game switch empties the characters under the
    same lock after swapping its snapshot, so a pick checked against one
    game is never written after the switch to the next.
    """
    slot = str(slot)
    if slot not in top8.PLAYER_SLOTS:
        logger.warning(f"Top8: ignoring player {slot}")
        return None
    with StateManager.lock:
        values = {}
        if "country" in fields:
            code = fields["country"]
            if code:
                value = CountryValue(code, countries)
                if not value:
                    return UNKNOWN_COUNTRY
                values["country"] = value
            else:
                values["country"] = {}
        if "character" in fields:
            character = fields["character"]
            if character:
                en_name, skin, codename = character
                catalog = (games if games is not None
                           else GameAssetCatalog.instance)
                snap = catalog.Current() if catalog is not None else None
                if (codename is not None and snap is not None
                        and (snap.codename or "") != codename):
                    return OTHER_GAME
                value = CharacterValue(en_name, skin, catalog, snap)
                if not value:
                    return UNKNOWN_CHARACTER
                # -1: a character without skins, whatever was asked.
                if value.get("skin") not in (skin, -1):
                    return UNKNOWN_SKIN
                values["characters"] = {"1": value}
            else:
                values["characters"] = {}
        with StateManager.SaveBlock():
            for field in PLAYER_TEXT_FIELDS:
                if field in fields:
                    SetPlayerText(slot, field, fields[field])
            for key, value in values.items():
                StateManager.SetIfChanged(_PlayerKey(slot, key), value)
    return None


def _IsRow(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _IsScore(value) -> bool:
    """[s1, s2]: a list of two ints (not bools), 0..SCORE_MAX."""
    return isinstance(value, list) and len(value) == 2 \
        and all(_IsRow(s) and 0 <= s <= SCORE_MAX for s in value)


def _SkinRow(snapshot, en_name, skin) -> int:
    """`skin` when it is a row of `en_name`'s skin combo, else its first row
    -- -1 for a character without skins.

    The first, not none: a Top 8 player's colour has no blank choice (the
    panel's colour combo always shows one of the character's skins), and
    the overlay draws the skin's icon (``assets``). A player card keeps a
    skin past the rows as none (PlayerService._MainSlot); an imported main
    whose skin this game's pack does not have still gets an icon here.
    """
    rows = len(snapshot.skinRows.get(en_name, ()))
    if _IsRow(skin) and 0 <= skin < rows:
        return skin
    return 0 if rows else -1


def CharacterValue(en_name, skin=0, games=None, snapshot=None) -> dict:
    """What the panel's pick writes in a player's ``characters["1"]`` for
    the character `en_name` with skin row `skin` (an import adds these
    keys to the character's config: ``ImportedCharacterValue``): the
    player cards' character slot value
    (``PlayerService.BuildCharacterValue``) -- name, en_name, display_name,
    codename, modded, the skin's ``assets``, ``skin`` and ``variant`` ({},
    a Top 8 player has none). A skin that is not one of the character's
    rows is its first (``_SkinRow``). {} for no character, and for one the
    loaded game's character combo does not list.

    `snapshot` is a ``GameAssetCatalog.Current()``, taken once by a caller
    building several (an import); `games` defaults to the instance -- {}
    when there is none (nothing to build the value with).
    """
    if not isinstance(en_name, str) or not en_name:
        return {}
    games = games if games is not None else GameAssetCatalog.instance
    if games is None:
        return {}
    snap = snapshot if snapshot is not None else games.Current()
    if games.CharacterRow(en_name, snapshot=snap) is None:
        return {}
    return BuildCharacterValue(
        CharacterSlot(en_name, _SkinRow(snap, en_name, skin)), games, snap)


def SetPlayerCharacter(slot, en_name, skin=0, games=None) -> bool:
    """One player's character, by the character's en_name and its skin row
    (the colour): the value is built from ``GameAssetCatalog``
    (``CharacterValue``; ADR-005 S4h -- it used to be the panel's skin row,
    or its character row when the skin had no assets, so a slot held one of
    two shapes and only one had the icon), stored as ``{"1": character}``.
    None / "" empties it. A character the loaded game does not list is not
    written (a warning; False). Written only when it changes; True if it
    did.

    Built and written under the state lock, against the game loaded at
    that moment, as the import is (``ImportPhaseGroup``): a game switch
    empties the characters under the same lock, after swapping the game,
    so a pick written before the switch does not outlive it. A pick made
    after it -- from the panel's combos before they are rebuilt for the new
    game -- is built against the new game (an en_name both games have).
    """
    slot = str(slot)
    if slot not in top8.PLAYER_SLOTS:
        logger.warning(f"Top8: ignoring the character of player {slot}")
        return False
    if en_name is not None and not isinstance(en_name, str):
        logger.warning(
            f"Top8: ignoring the character {en_name!r} of player {slot}")
        return False
    with StateManager.lock:
        if not en_name:
            value = {}
        else:
            character = CharacterValue(en_name, skin, games)
            if not character:
                logger.warning(f"Top8: no character {en_name!r} in the "
                               f"loaded game, player {slot} left as it is")
                return False
            value = {"1": character}
        return StateManager.SetIfChanged(
            _PlayerKey(slot, "characters"), value)


def CharacterPick(value, games=None, snapshot=None):
    """The identifiers that show `value` -- a player's ``characters["1"]``
    -- as ``(en_name, skin row)``; ``(None, -1)`` for no character, or one
    the loaded game's character combo does not list.

    Reads every shape a slot can hold:

    - ``CharacterValue``'s (the panel's pick, S4h on). Matched as a player
      card matches its slot (``PlayerService.SlotFromValue``): the rows
      sharing the value's codename or en_name are the candidates, and the
      one whose value with the stored ``skin`` is `value` wins -- a skin
      that is a different character (Dr. Mario) puts that character's name
      and en_name in.
    - the import's (S4h on): the character's config with
      ``CharacterValue``'s keys added (``ImportedCharacterValue``) -- the
      candidates' skin row whose ``assets`` these are.
    - the panel's skin row, before S4h: ``{"assets"}`` only, plus the name
      and en_name of a different-character skin and no codename -- the
      skin row, among every character's, whose ``assets`` these are.
    - the panel's character row and the import's copy of the character's
      config, before S4h: no assets -- the character by codename /
      en_name, on its first skin (what the panel showed for them).

    The codename names the character; an en_name can be another
    character's -- Bowser Jr.'s skin "Roy" is a different character, and
    Roy is one too -- so the rows sharing the codename are tried first,
    whatever order the combo lists them in.
    """
    if not isinstance(value, dict) or not value:
        return None, -1
    games = games if games is not None else GameAssetCatalog.instance
    if snapshot is None and games is None:
        return None, -1
    snap = snapshot if snapshot is not None else games.Current()
    rows = [data for _text, data in snap.characterRows]
    codename = value.get("codename")
    en_name = value.get("en_name")
    byCodename = [data for data in rows
                  if codename and data.get("codename") == codename]
    byEnName = [data for data in rows
                if en_name and data.get("en_name") == en_name
                and not any(data is other for other in byCodename)]
    candidates = byCodename + byEnName

    skin = value.get("skin")
    if _IsRow(skin):
        for data in candidates:
            name = data.get("en_name")
            if CharacterValue(name, skin, games, snap) == value:
                return name, skin

    assets = value.get("assets")
    if isinstance(assets, dict) and assets:
        # The codename's character's skins; without one (the skin row
        # before S4h, whose en_name may be a different-character skin's)
        # nothing but the assets names the character: every character's
        # skins are searched, never the en_name's alone.
        searched = byCodename if byCodename else rows
        for data in searched:
            name = data.get("en_name")
            for row, (_skin, _text, skinData) in enumerate(
                    snap.skinRows.get(name, ())):
                if skinData.get("assets") == assets:
                    return name, row

    if candidates:
        name = candidates[0].get("en_name")
        return name, _SkinRow(snap, name, skin)
    return None, -1


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

    `scores` is `{match_id: [s1, s2]}` -- what the Top 8 page staged (the Qt
    panel's spin boxes until ADR-005 S4i), which is deliberately *not* what
    the state holds: the page stages edits and publishes them on 反映, so
    what it shows and `top8.matches` disagree until this runs.

    A match counts as finished when its applied score is not a tie; there is
    no separate confirm control. Recompute then rewrites every downstream
    match's slots (and can reset ones that are no longer reachable), so the
    caller re-renders from the returned dict rather than from what it sent.

    A merge (ADR-005 S4h): only the matches in `scores` are applied, every
    other one keeps its published score and result. It used to rebuild the
    matches from `scores` alone, so a match left out lost its result -- the
    panel always sends all of them, which is why it never showed; a client
    sending the one match it changed would have wiped the rest. An id that
    is not a Top 8 match is ignored (a warning). Read, merged and written
    under the state lock, so two merges do not drop each other's matches.

    A score is a list of two ints, 0..SCORE_MAX (the panel's spin boxes'
    range; a bool is not a score). When any applied one is not -- or
    `scores` is not a dict -- nothing is written and the answer is None
    (a warning): a client's bad value is refused, not half applied.
    """
    if not isinstance(scores, dict):
        logger.warning(f"Top8: refusing the scores {scores!r}")
        return None
    unknown = [matchId for matchId in scores
               if matchId not in top8.MATCH_IDS]
    if unknown:
        logger.warning(f"Top8: ignoring the scores of {unknown}")
    invalid = {matchId: score for matchId, score in scores.items()
               if matchId in top8.MATCH_IDS and not _IsScore(score)}
    if invalid:
        logger.warning(f"Top8: refusing the scores {invalid!r}")
        return None
    with StateManager.lock:
        # StateManager.Get returns a live reference -- never mutate it in
        # place, or DeepDiff sees no change and the delta is never
        # broadcast.
        current = StateManager.Get(MATCHES_PATH)
        current = current if isinstance(current, dict) else {}
        matches = {}
        for matchId in top8.MATCH_IDS:
            published = current.get(matchId)
            match = dict(published if isinstance(published, dict)
                         else top8.DefaultMatch(matchId))
            if matchId in scores:
                # A copy: the caller's list does not go into the state.
                score = list(scores[matchId])
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

# One import at a time, whoever asked (the Top 8 page, the hotkey, the
# other web routes): the in-progress flag, read and set under its lock.
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
        elif (TaskExecutor.instance is not None
              and TaskExecutor.instance.stopping.is_set()):
            # The fetcher starts nothing once the app is quitting.
            logger.info("Top8: the app is quitting, skipping the import")
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
        resolved = {slot: ResolveImportedPlayer(player, snapshot, countries,
                                                games)
                    for slot, player in players.items()}
        ImportBracket(resolved, matches)
    return True


def ResolveImportedPlayer(imported, snapshot, countries, games=None):
    """A ``FromBracket`` player as the state holds it: name and sponsor, the
    country from its code (``CountryCatalog.BasicCountryInfo``), the
    character from the first of the player's mains in the loaded game.

    `snapshot` is a ``GameAssetCatalog.Current()`` of `games` (default: the
    instance). The main names the character by its key (en_name) or else
    by its codename; the value is ``ImportedCharacterValue``'s, on the
    main's skin (its first when the main has none -- Start.gg's never do --
    or one past the pack's). It used to be the copy of the character's
    config alone: no ``assets``, so the overlay drew no icon for an
    imported player (ADR-005 S4h).
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
            main = game_mains[0]
            en_name = _CharacterKey(snapshot, main[0])
            skin = _MainSkin(main[1]) if len(main) > 1 else 0
            char = ImportedCharacterValue(en_name, skin, games, snapshot)
            if char:
                player["characters"] = {"1": char}
    return player


def ImportedCharacterValue(en_name, skin=0, games=None, snapshot=None):
    """What an imported player's ``characters["1"]`` holds for the
    character `en_name` on skin row `skin`.

    The character's config entry as the game defines it -- a copy of
    ``snapshot.characters[en_name]`` (codename, display_name, export_name,
    en_name and whatever the config carries: smashgg_name, locale,
    skin_name...), what an import wrote before ADR-005 S4h, key order and
    values unchanged -- followed by the keys of ``CharacterValue`` it
    lacks (modded, name, assets, skin, variant): the overlay draws the
    icon from ``assets``. Additive only, for the exported text files
    (``out/top8/players/{n}/characters/1/*``) and a user's own layout: a
    key the config has keeps the config's value, so a different-character
    skin's en_name stays the character's here (Bowser Jr. for its "Roy"
    skin), where the panel's pick names the skin's character.

    A character the combo does not list -- a modded one while mods are
    off -- or no catalog to build with: the copy alone, as before S4h. {}
    for a character the game does not have.
    """
    if snapshot is None:
        games = games if games is not None else GameAssetCatalog.instance
        if games is None:
            return {}
        snapshot = games.Current()
    entry = snapshot.characters.get(en_name) \
        if isinstance(en_name, str) else None
    if not isinstance(entry, dict):
        return {}
    # A copy: the entry is the catalog's own dict.
    value = deep_clone(entry)
    for key, item in CharacterValue(en_name, skin, games, snapshot).items():
        if key not in value:
            value[key] = item
    return value


def _CharacterKey(snapshot, name):
    """The en_name of the character a main names: by its key, else by its
    codename (Start.gg's mains carry the key; a player database's may carry
    either). None when the game has no such character."""
    characters = snapshot.characters
    if isinstance(name, str) and name in characters:
        return name
    for key, value in characters.items():
        if value.get("codename") == name:
            return key
    return None


def _MainSkin(value):
    """A main's skin row: a number, or its string (a player database's
    record); anything else -- an infinity included -- is the first skin."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
