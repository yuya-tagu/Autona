"""One player slot's state subtree: everything written under a player path.

The player card was the last place where a state key existed because a
designer had named a widget. Every free-text field went through one
``findChildren(QLineEdit)`` loop whose lambda wrote
``{path}.{element.objectName()}``; the combos went through the matching
``findChildren(QComboBox)`` loop; and the derived values (the merged display
name, the avatar, the sponsor crests, the character slots) were written from
inside the widget methods that computed them. That is category (a) of
docs/changes/headless-core-redesign.md §2 -- the last one, and the one the
key catalog could never describe because the .ui file *was* the catalog
(known-smells S-14).

Since headless core Step 5 (player instalment) the names live in
``StateSchema`` and the writes live here. Step 5 still had the card resolve
its combos' rows in Qt and hand the whole row dicts over; since ADR-005 S1d
the card hands over **identifiers only** -- which character (en_name), which
skin row, which variant (en_name), which country code, which region code --
and this class builds the values from the Qt-free catalogs
(``GameAssetCatalog`` / ``CountryCatalog``). Which row a combo shows is still
the card's business; what the state holds for that pick is decided here, so
it can be produced without a widget. Since ADR-005 S3e-3 (S2f) so is loading
a whole player -- the database merge, the bad-word filter, the location and
character lookups (``ApplyData``) -- and the card's clear and save
(``ClearSlot`` / ``SavePlayerToDB``): the card asks, then renders the slot.
And since ADR-005 S3e-5 the statistics a change of player or seed calls
for are asked for here too (``Announce``), not by the card's signals.

The values are byte-compatible with what the card used to publish. A Qt
model hands its dicts out as QVariantMaps -- the keys of every nested dict in
UTF-16 order -- and program_state.json and the Socket.IO delta are both
serialised in insertion order, so that order is reproduced
(``DictHelper.qvariantmap_copy``); tests/test_player_slot_publish.py pins it
against a recording of the old path.

One instance per player widget, bound to that widget's path, exactly as
``SetLoadService`` is bound to one board. The path is a board's slot,
``score.{N}.team.{T}.player.{M}`` (agent-pitfalls #7: the ``commentary.{N}``
and ``player_list.slot.{I}`` callers went with their panels, and the
``player_editor.1`` one with the Qt Player List, ADR-005 S4n).

**Writes are unconditional**, unlike ``SetLoadService._SetIfChanged`` /
``AutonaActions._SetIfChanged``. That is deliberate and it is not an
oversight to be tidied later: those two write keys that a *re-load* or a
*focus-out* re-asserts, where an identical write would light the 反映 button
for an edit nobody made. The writes here are reached from a widget that has
already changed -- a combo whose index moved, a line edit whose text
changed -- and two of them (the region combo's rebuild, the character slots)
legitimately publish the same empty value twice in a row as part of one set
load, which a guard would swallow. The two that *were* guarded in the widget
(``city`` and ``id``) keep their guards below, and a load (``ApplyData``)
skips what the card's SetData skipped -- a text, a location, a character
row it would not have moved -- so it writes what SetData wrote.

Import side effects: none (agent-pitfalls #1). Qt-free, like everything under
``src/core`` -- including ``SponsorHelper``, which is imported for the crest
lookup and which moved into core alongside this module's own instalment
(Step 5-5) precisely because this was its only caller
(``tests/test_core_qt_free.py`` pins both).
"""
import copy
import os
import re
import threading
from dataclasses import dataclass
from typing import NamedTuple, Optional

from loguru import logger

from . import StateSchema as schema
from .BadWordFilter import BadWordFilter
from .CountryCatalog import CountryCatalog
from .DictHelper import deep_clone, qvariantmap_copy
from .GameAssetCatalog import GameAssetCatalog
from .PlayerDB import PlayerDB
from .SponsorHelper import AutonaSponsorHelper
from .StateManager import StateManager

# Sentinel for PublishImages(onlineAvatar=...): "leave the key alone". It
# cannot be None, because None is meaningful there ("this player has no
# online avatar, clear the previous one"). Lived on
# AutonaBasePlayerWidget as _KEEP_ONLINE_AVATAR; the widget imports it from
# here now so the two sides cannot end up with different sentinels.
KEEP_ONLINE_AVATAR = object()

# What a request to Edit / EditCharacter did not send: that field stays as
# it is (ADR-005 S4z). Not None, which there means "none".
NOT_SENT = object()

# What Edit / EditCharacter refuse, writing nothing (ADR-005 S4z; v1's
# routes say what each one is answered with).
UNKNOWN_COUNTRY = "unknown_country"
UNKNOWN_REGION = "unknown_region"
NO_SUCH_ROW = "no_such_row"
UNKNOWN_CHARACTER = "unknown_character"
UNKNOWN_SKIN = "unknown_skin"
UNKNOWN_VARIANT = "unknown_variant"
OTHER_GAME = "other_game"

# Operator-supplied local avatars, matched by the player's sponsor + tag.
# Relative to the process CWD like every other mutable path (./out,
# ./user_data) -- see SetLoadService.TEAM_LOGO_DIR.
AVATAR_DIR = "./user_data/player_avatar"

# Characters that may not appear in the file name derived from a player's
# name. Verbatim from the widget (the sponsor helper keeps its own copy for
# the crest lookup, which splits on a slightly wider set).
_FILENAME_UNSAFE = re.compile(r"[,/|;:<>\\?*]")

# The line breaks the card's note box (a QPlainTextEdit) turned into "\n" on
# the way to the state: a note loaded into a slot is stored as the box would
# have handed it over.
_LINE_BREAKS = re.compile("\r\n|[\r\u2028\u2029]")


def NoteForDB(text: str) -> str:
    """A note as the player database stores it: one line in the file, its
    line breaks escaped."""
    return "\\n".join(text.splitlines())


def NoteFromDB(text: str) -> str:
    """A stored note as the card's note box hands it over: real line breaks,
    "\\n" only. Not an exact inverse of NoteForDB: a final line break did not
    survive that (splitlines), its rarer breaks come back as "\\n", and a
    backslash and an "n" typed as text come back as a line break."""
    return _LINE_BREAKS.sub("\n", text.replace("\\n", "\n"))


# A loaded player's fields, PlayerDB-shaped key -> slot key, in the order
# they were set (the gamer tag, the sponsor, the real name).
_NAME_FIELDS = (
    (schema.PLAYER_TAG_KEY, "gamerTag"),
    (schema.PLAYER_SPONSOR_KEY, "prefix"),
    (schema.PLAYER_REAL_NAME_KEY, "name"),
)

# Character slots per player on a fresh board, and on every board after a game
# load, whatever the game says (AutonaActions.SetRoster from SeedFreshBoard and
# ApplyGameRoster; it was the board forcing its charNumber back to this, until
# ADR-005 S3e-6).
DEFAULT_CHARACTERS_PER_PLAYER = 3


def DefaultPlayersPerTeam() -> int:
    """Player cards per team on a fresh 1v1 / team board.

    The loaded game's ``game.defaults.players_per_team``, 1 without one --
    what AutonaActions.SetRoster gives a new board (SeedFreshBoard), and every
    board on each game load (ApplyGameRoster). Both shells.
    """
    if StateManager.Get("game.defaults"):
        return StateManager.Get("game.defaults.players_per_team", 1)
    return 1


@dataclass(frozen=True)
class CharacterSlot:
    """One character slot as the card picked it: identifiers only.

    `character` / `variant` are en_names (the keys of the game's tables);
    None means no row is picked -- the blank row, or a name typed into the
    editable combo that matches no row. `skin` is the row of the
    character's skin combo, -1 for none; that row index is what the slot has
    always published as `skin`.

    `text` becomes the slot's name when the catalog has no row for
    `character`: the typed text when no row is picked, and the picked row's
    own `name` when the loaded game does not have it (the moment between a
    game load and the combos' rebind -- the combo's text would be the
    "display name / en_name" label there, which is no name an overlay ever
    saw).
    """
    character: Optional[str] = None
    skin: int = -1
    variant: Optional[str] = None
    text: str = ""


# The two values a pick is published as, built from identifiers against the
# catalogs. Module functions because a player slot is not their only writer:
# the Top 8 builds its players' country and character with the same two
# (core/Top8Service, ADR-005 S4h), so the overlays read one shape whichever
# panel wrote it. PlayerService.CountryValue / CharacterValue are these,
# against the slot's own catalogs.

def BuildCountryValue(code, countries=None) -> dict:
    """What a player's `country` holds for the country (or custom flag)
    `code`.

    The country combo's row for that code -- code, names, flag asset,
    emoji, coordinates -- in the key order the combo handed it out in.
    {} for no country, which is also the answer for a code the catalog
    does not know: a name typed into the editable combo that matches no
    row, or the custom-flag separator. Those two used to publish null;
    they now have the type every other "no country" has always had.
    `countries` defaults to ``CountryCatalog.instance`` at call time.
    """
    catalog = countries if countries is not None else CountryCatalog.instance
    row = catalog.CountryByCode(code) if code else None
    return qvariantmap_copy(row) if row else {}


def BuildCharacterValue(slot: CharacterSlot, games=None,
                        snapshot=None) -> dict:
    """What one character slot holds for `slot`.

    The shape the overlays read: the character row (name, en_name,
    display_name, codename, modded), with the skin row's name / en_name
    over it when that skin is a different character (Dr. Mario), then
    `assets` (every asset pack's file for that skin), `skin` and
    `variant`. Without a character row the slot is just its name
    (CharacterSlot.text): {name, assets: {}, skin, variant}.

    Dict keys come in the order the combos handed their rows out in
    (module docstring); the three appended keys come last, as they
    always did. `snapshot` is GameAssetCatalog.Current(), taken once by
    the caller so every slot of a card comes from one game. `games`
    defaults to ``GameAssetCatalog.instance`` at call time.
    """
    games = games if games is not None else GameAssetCatalog.instance
    snap = snapshot if snapshot is not None else games.Current()

    row = games.CharacterRow(slot.character, snapshot=snap) \
        if slot.character is not None else None
    if row is None:
        # No row picked (or the picked one is not in the loaded game,
        # e.g. for the moment between a game load and the combos'
        # rebind): the card has no skin rows either.
        value = {"name": slot.text}
        skin = None
    else:
        value = qvariantmap_copy(row)
        skin = games.SkinRow(slot.character, slot.skin, snapshot=snap)

    if skin:
        if skin.get("name", ""):
            value["name"] = skin["name"]
        if skin.get("en_name", ""):
            value["en_name"] = skin["en_name"]
        value["assets"] = qvariantmap_copy(skin.get("assets", {}))
    if value.get("assets") is None:
        value["assets"] = {}

    value["skin"] = slot.skin

    variant = games.VariantRow(slot.variant, snapshot=snap) \
        if slot.variant is not None else None
    value["variant"] = qvariantmap_copy(variant) if variant else {}
    return value


class SlotChange(NamedTuple):
    """What a write did to the two facts the board's statistics hang off:
    who is in the slot (`id`) and their seed.

    `PlayerService.Announce` asks the statistics for it (the recent sets
    and the last sets / standings for an id, the upset factor for a seed;
    ADR-005 S3e-5); the player card's own signals used to, one per widget
    change. A change is the slot's value after the operation against the
    one before, so a load that empties the seed and puts the same one back
    changes nothing.
    """
    idChanged: bool = False
    seedChanged: bool = False


class PreparedSave(NamedTuple):
    """A player database record read from a slot (PrepareSaveToDB), and
    its number in the order the slots were read -- what SaveRecordToDB
    keeps a later save of an older read from overwriting (ADR-005 S3e-4
    review)."""
    record: dict
    sequence: int


class PlayerService:
    """The state subtree of one player slot."""

    # The order of the database saves (PrepareSaveToDB / SaveRecordToDB):
    # the last number handed out, and per database key the number of the
    # record saved last. One lock for both; nothing is waited for under it.
    _saveLock = threading.Lock()
    _saveSequence = 0
    _savedSequence = {}

    def __init__(self, path: str, games=None, countries=None):
        """`games` / `countries` default to the process catalogs
        (`GameAssetCatalog.instance` / `CountryCatalog.instance`), looked up
        at call time; a test hands its own."""
        self.path = path
        self._games = games
        self._countries = countries

    def _GameCatalog(self):
        return self._games if self._games is not None \
            else GameAssetCatalog.instance

    def _CountryCatalog(self):
        return self._countries if self._countries is not None \
            else CountryCatalog.instance

    # ── key paths ────────────────────────────────────────────────────────

    def Key(self, leaf: str) -> str:
        return f"{self.path}.{leaf}"

    def BoardNumber(self):
        """The board this slot belongs to, or None for a path that is no
        board's."""
        parts = self.path.split(".")
        if len(parts) < 2 or parts[0] != "score":
            return None
        try:
            return int(parts[1])
        except (TypeError, ValueError):
            return None

    def TeamNumber(self):
        """The team (1 / 2) this slot belongs to on its board, or None."""
        parts = self.path.split(".")
        if (len(parts) != 6 or parts[0] != "score" or parts[2] != "team"
                or parts[4] != "player"):
            return None
        team = self._asInt(parts[3], 0)
        return team if team in (1, 2) else None

    def Announce(self, change: SlotChange):
        """Ask the board's statistics for what `change` calls for
        (StatsService.PlayerChanged): another player in the slot, another
        seed. Into the caller's save cycle, as the card's signals did.

        A failure is logged, not raised: the write that changed the slot has
        been made, and its own error (if any) is the one that propagates.
        """
        board = self.BoardNumber()
        team = self.TeamNumber()
        if board is None or team is None or not (
                change.idChanged or change.seedChanged):
            return
        # Imported here: StatsService reads this module's roster default.
        from .StatsService import StatsService
        try:
            StatsService(board).PlayerChanged(team, change)
        except Exception:
            logger.exception(f"{self.path}: asking for the statistics failed")

    def TeamLosersKey(self):
        """``score.{N}.team.{T}.losers`` for a board slot, None otherwise.

        The flag the slot's display name carries as " [L]".
        """
        parts = self.path.split(".")
        if (len(parts) != 6 or parts[0] != "score" or parts[2] != "team"
                or parts[4] != "player"):
            return None
        return ".".join(parts[:4] + [schema.TEAM_LOSERS_KEY])

    @staticmethod
    def _asInt(value, default=0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ── the card's own inputs ────────────────────────────────────────────

    def SetText(self, field, value) -> bool:
        """Set one free-text field. False when `field` is not one of them.

        ``StateSchema.PLAYER_TEXT_KEYS`` -- the four QLineEdits of
        AutonaScoreboardPlayer.ui plus the note box built in code. The old
        loop accepted whatever objectName it found, which is how the editable
        country/state combos' *internal* line edits (objectName "") ended up
        connected too: finishing an edit in either of them wrote a key with
        an empty final segment under the player. Rejecting an unknown field
        is what ends that.
        """
        if field not in schema.PLAYER_TEXT_KEYS:
            logger.warning(f"PlayerService.SetText: unknown field {field}")
            return False
        StateManager.Set(self.Key(field), "" if value is None else str(value))
        return True

    def SetSeed(self, value, announce=False) -> bool:
        """Publish the seed as a number; with `announce`, recalculate the
        upset factor too (the card's seed spinbox: Announce).

        Three callers used to write it in two different types: the spinbox's
        ``valueChanged`` and ``SetData`` wrote an int, while the spinbox's
        internal line edit (reached through the same objectName loop as the
        text fields, via the ``qt_spinbox_lineedit`` -> parent fallback) wrote
        its *text* -- so a Clear() published the string "0". Normalised here;
        anything non-numeric means unseeded, which is the rule ``SetData``
        already applied to provider payloads.
        """
        StateManager.Set(
            self.Key(schema.PLAYER_SEED_KEY), self._asInt(value, 0))
        if announce:
            self.Announce(SlotChange(seedChanged=True))
        return True

    def SetBirthday(self, value) -> bool:
        StateManager.Set(self.Key(schema.PLAYER_BIRTHDAY_KEY), bool(value))
        return True

    # ── the location ─────────────────────────────────────────────────────
    #
    # Written even when it is the same value: a set load empties the country
    # and puts it back, and the empty publish in between is what
    # tests/test_player_widgets counts to know the region combo's in-place
    # refill still emits exactly one index change.

    def CountryValue(self, code) -> dict:
        """What `country` holds for the country (or custom flag) `code`
        (``BuildCountryValue``, against this slot's catalog)."""
        return BuildCountryValue(code, self._CountryCatalog())

    def RegionValue(self, countryCode, regionCode) -> dict:
        """What `state` holds for region `regionCode` of `countryCode`.

        The region combo's row (with the flag asset resolved now, as the
        combo's rebuild resolved it), in the combo's key order; {} for no
        region or one the catalog does not know -- a name typed into the
        editable region combo, which used to publish null (see CountryValue).
        """
        if not regionCode:
            return {}
        return qvariantmap_copy(
            self._CountryCatalog().State(countryCode, regionCode))

    def SetCountry(self, code):
        """Publish the country picked by code (None: no country)."""
        StateManager.Set(self.Key(schema.PLAYER_COUNTRY_KEY),
                         self.CountryValue(code))

    def SetRegion(self, countryCode, regionCode):
        """Publish the region picked by code, of the country whose regions
        the card is showing (None: no region)."""
        StateManager.Set(self.Key(schema.PLAYER_STATE_KEY),
                         self.RegionValue(countryCode, regionCode))

    # ── derived: the display name ────────────────────────────────────────

    def PublishMergedName(self, sponsor, tag) -> str:
        """Publish the two display-name spellings overlays render.

        ``mergedName`` carries the sponsor prefix, ``mergedOnlyName`` never
        does; both carry the " [L]" suffix when the player's team came out of
        the losers bracket (the thumbnail generator reads it). One save cycle,
        because they are one fact -- the pair used to be two bare Sets, so a
        board could notify twice for a single name edit.

        The losers flag is read from the state (``TeamLosersKey``) since
        ADR-005 S2b. The card used to hand it over from a copy of its own
        (``self.losers``, kept in step by the checkbox's second handler and
        two board helpers), which is what made " [L]" a per-card write; the
        flag's writers now re-publish the team's names themselves
        (``RepublishTeamMergedNames``). `sponsor` / `tag` still come from the
        caller (the card's text). On a name edit the card has written that
        text to the state already (``_OnTextChanged`` runs before
        ``NameChanged``), so a team rebuild landing in between, from any
        thread, builds the same name.
        """
        with StateManager.lock:
            losersKey = self.TeamLosersKey()
            losers = bool(StateManager.Get(losersKey, False)) \
                if losersKey else False
            merged = f"{sponsor} | {tag}" if sponsor else f"{tag}"
            nameOnly = f"{tag}"
            if losers:
                merged += " [L]"
                nameOnly += " [L]"
            with StateManager.SaveBlock():
                StateManager.Set(self.Key("mergedName"), merged)
                StateManager.Set(self.Key("mergedOnlyName"), nameOnly)
        return merged

    # ── derived: the images ──────────────────────────────────────────────

    def PublishImages(self, sponsor, tag, onlineAvatar=KEEP_ONLINE_AVATAR):
        """Re-publish the image state derived from the player's name.

        The local avatar and the sponsor crests are looked up from the
        sponsor + tag pair, so every name change has to run this again.
        ``online_avatar`` is NOT name-derived -- it comes from the tournament
        provider, and only a caller that knows *which player is now in this
        slot* may write it:

          - a URL/path -> published
          - None       -> explicitly cleared (Clear(), or SetData for a
                          player the API gave no avatar for)
          - omitted    -> left untouched

        The default used to be None, so both deferred refreshes (SetData's
        own finally block and NameChanged) wiped the start.gg avatar written
        moments earlier: every set loaded from start.gg ended with
        online_avatar = None, and editing a tag by hand dropped the avatar of
        a player that still had one (LIVE-BUG-1).
        """
        with StateManager.SaveBlock():
            merged = f"{sponsor} {tag}" if sponsor else f"{tag}"
            merged = _FILENAME_UNSAFE.sub("_", merged)

            if onlineAvatar is not KEEP_ONLINE_AVATAR:
                StateManager.Set(self.Key("online_avatar"), onlineAvatar)

            localPath = f"{AVATAR_DIR}/{merged}.png"
            StateManager.Set(
                self.Key("avatar"),
                localPath if os.path.exists(localPath) else None)

            AutonaSponsorHelper.ExportValidSponsors(sponsor, self.path)

    # ── derived: the character slots ─────────────────────────────────────

    def CharacterValue(self, slot: CharacterSlot, snapshot=None) -> dict:
        """What one slot of `character` holds for `slot`
        (``BuildCharacterValue``, against this slot's catalog)."""
        return BuildCharacterValue(slot, self._GameCatalog(), snapshot)

    def PublishCharacterSlots(self, slots, includeMains=False,
                              snapshot=None):
        """Publish the card's character slots (`character`, keyed by 1-based
        slot number), built from what each slot picked.

        `includeMains` additionally republishes them as the player's mains,
        which is what a slot-count change does -- one save cycle for the two
        keys, since they are written from the same read (and, as before, the
        same dict). `snapshot` is the GameAssetCatalog.Current() the slots
        were resolved against, when the caller took one (a resize keeps
        picks read from the state: AutonaActions.SetRoster).
        """
        snap = snapshot if snapshot is not None \
            else self._GameCatalog().Current()
        characters = {index + 1: self.CharacterValue(slot, snap)
                      for index, slot in enumerate(slots)}
        if not includeMains:
            StateManager.Set(self.Key("character"), characters)
            return
        with StateManager.SaveBlock():
            StateManager.Set(self.Key("character"), characters)
            StateManager.Set(self.Key("mains"), characters)

    # ── provider-supplied identity ───────────────────────────────────────

    def SetCity(self, city) -> bool:
        """Set the player's city. True when the state changed.

        Guarded, as it was on the widget: the city arrives with a start.gg
        entrant and is re-asserted by every refresh of the same set, and an
        identical write would mark a held board dirty for nothing.
        """
        if StateManager.Get(self.Key("city")) == city:
            return False
        StateManager.Set(self.Key("city"), city)
        return True

    def SetPlayerId(self, value, announce=False) -> bool:
        """Set the provider's player id. True when the state changed.

        With `announce` (the card's renamed player: NameChanged), a change
        asks for the statistics (Announce): they hang off "the player in
        this slot is now someone else", so they are asked for on a change
        and not on a re-assertion.
        """
        if StateManager.Get(self.Key("id")) == value:
            return False
        StateManager.Set(self.Key("id"), value)
        if announce:
            self.Announce(SlotChange(idChanged=True))
        return True

    def SetOnlineAvatar(self, value):
        """Write the provider-supplied avatar on its own.

        Separate from PublishImages because Clear() states it rather than
        letting it fall out of a name-derived refresh -- which is the accident
        that used to wipe avatars that were meant to stay.
        """
        StateManager.Set(self.Key("online_avatar"), value)

    # ── the card's fields, from a request (ADR-005 S4z) ──────────────────
    #
    # What the card's inputs write when the operator finishes them, for the
    # fields of one request and in one save cycle: v1's PATCH .../fields and
    # .../characters/{row}, which the React SCORE page edits a slot with. Not
    # ApplyData, which is a load (a blank value is "not carried", the local
    # database is merged in, a new name empties the city): here a field is
    # what it is sent as, "" included, as the card's are.

    def Edit(self, texts, country=NOT_SENT, region=NOT_SENT):
        """Some of the card's fields, as finishing them on the card writes
        them. One save cycle. Returns None, or -- having written nothing --
        UNKNOWN_COUNTRY / UNKNOWN_REGION.

        - `texts` maps fields of ``PLAYER_TEXT_KEYS`` to their text, each
          written when the slot does not hold that text already (the card's
          rule, _OnTextChanged: a field the slot has never had is written,
          "" too). The note's line breaks are "\\n", as the note box hands
          them over -- the text as shown, not the database's escaped form.
        - `country` / `region` are codes, "" or None for none, NOT_SENT to
          leave it. A country is one of the country combo's rows (a custom
          flag's included); a region one of that country's -- the country
          sent, else the one the slot shows. Another country empties the
          region (a pick publishes the blank region first, then the country),
          unless a region of it is sent too. The same country or region as
          shown writes nothing (a combo picked on its own row emits nothing).
        - a sponsor or a tag that changed does what the card's NameChanged
          does: the display name and the images re-derived (the online
          avatar kept: LIVE-BUG-1), the id emptied and the statistics asked
          for when that changed it (Announce, outside the lock). The city
          and the seed stay, as on the card. No bad-word filter, no
          database: the operator's text is the operator's.

        The lock is taken inside the save block and released before the
        statistics are asked for (a fetch is started there), as ApplyData's.
        And ApplyData's guarantee when a step raises half-way (S4z review):
        for what was written, the id is emptied and the display name and the
        images re-derived from what the slot holds, and the statistics are
        asked for; an error there is logged rather than raised over the
        step's own. A sponsor or a tag is never left beside the previous
        player's id.
        """
        for field in texts:
            if field not in schema.PLAYER_TEXT_KEYS:
                raise ValueError(f"not a text field of a player: {field!r}")
        catalog = self._CountryCatalog()
        identity = None
        failed = False
        with StateManager.SaveBlock():
            try:
                with StateManager.lock:
                    slot = self._Slot()
                    shownValue = slot.get(schema.PLAYER_COUNTRY_KEY)
                    shown = (shownValue.get("code") or None) \
                        if isinstance(shownValue, dict) else None
                    regionValue = slot.get(schema.PLAYER_STATE_KEY)
                    shownRegion = (regionValue.get("code") or None) \
                        if isinstance(regionValue, dict) else None

                    # Resolved before anything is written: a stray code never
                    # empties what is on air.
                    target = shown
                    if country is not NOT_SENT:
                        if country and catalog.CountryByCode(country) is None:
                            return UNKNOWN_COUNTRY
                        target = country or None
                    targetRegion = shownRegion if target == shown else None
                    if region is not NOT_SENT:
                        if region:
                            # The codes only (StateCodes): no flag looked up
                            # on disk under the lock. A region's code is its
                            # key.
                            rows = catalog.StateCodes(target) \
                                if target else None
                            if not rows or region not in rows:
                                return UNKNOWN_REGION
                        targetRegion = region or None

                    identity = self.Identity()
                    before = (self._Text(schema.PLAYER_SPONSOR_KEY),
                              self._Text(schema.PLAYER_TAG_KEY))
                    try:
                        for field in schema.PLAYER_TEXT_KEYS:
                            if field not in texts:
                                continue
                            value = "" if texts[field] is None \
                                else str(texts[field])
                            if field == schema.PLAYER_NOTE_KEY:
                                value = _LINE_BREAKS.sub("\n", value)
                            if StateManager.Get(self.Key(field)) != value:
                                self.SetText(field, value)

                        if target != shown:
                            # The region rows are rebuilt for the new country
                            # first, which publishes the blank region
                            # (LoadStates).
                            self.SetRegion(None, None)
                            shownRegion = None
                            self.SetCountry(target)
                        if targetRegion != shownRegion:
                            self.SetRegion(target, targetRegion)
                    except BaseException:
                        failed = True
                        raise
                    finally:
                        # Also when a step raised: for what was written.
                        after = (self._Text(schema.PLAYER_SPONSOR_KEY),
                                 self._Text(schema.PLAYER_TAG_KEY))
                        if after != before:
                            # The card's order (NameChanged): the name, the
                            # images, the id -- which is emptied even when
                            # the name or the images raised.
                            published = False
                            try:
                                self.PublishMergedName(*after)
                                self.PublishImages(*after)
                                published = True
                            finally:
                                try:
                                    self.SetPlayerId(None)
                                except Exception as e:
                                    if published and not failed:
                                        raise
                                    # The first exception is what propagates.
                                    logger.opt(exception=e).error(
                                        f"{self.path}: the previous player's "
                                        f"id was not emptied after a failed "
                                        f"edit")
            except BaseException:
                failed = True
                raise
            finally:
                if identity is not None:
                    try:
                        self.Announce(self.ChangeSince(identity))
                    except Exception as e:
                        if not failed:
                            raise
                        # As emptying the id above.
                        logger.opt(exception=e).error(
                            f"{self.path}: the statistics were not asked for "
                            f"after a failed edit")
        return None

    def EditCharacter(self, row, character=NOT_SENT, skin=NOT_SENT,
                      variant=NOT_SENT, game=None):
        """One character row of the card, as picking on its three combos
        writes it: `character` an en_name ("" or None: the blank row),
        `skin` the skin combo's row, `variant` an en_name ("" or None: none);
        NOT_SENT leaves that combo as it is. `game`: the codename of the game
        whose list the pick was made on, None not to check it. Returns None,
        or -- having written nothing -- NO_SUCH_ROW (`row` is not one of the
        slot's rows, 1-based), UNKNOWN_CHARACTER / UNKNOWN_SKIN /
        UNKNOWN_VARIANT (not in the loaded game), OTHER_GAME (`game` is not
        the game loaded: a list from before a switch).

        - another character takes its first skin, or none for a character
          without skins (the skin combo's new rows: LoadSkinOptions), unless
          a skin is sent; the variant stays unless one is sent.
        - a skin that is not one of the character's rows is refused; for a
          character without skins (or the blank row) any skin sent is none,
          as the Top 8's (Top8Service.UpdatePlayer).
        - the rows are published together (PublishCharacterSlots), the other
          rows as the slot holds them, and only when that changes the slot
          (a combo picked on its own row emits nothing).

        Under the state lock, against one GameAssetCatalog snapshot (as
        Top8Service.UpdatePlayer): the rows are resolved from one game. A
        pick that lands while a switch to another game is on its way is
        emptied with every other slot's characters by the roster step that
        follows the load (AutonaActions.ApplyGameRoster with `replaced`), as
        a pick on the card would be.
        """
        games = self._GameCatalog()
        with StateManager.lock:
            snap = games.Current()
            if game is not None and (snap.codename or "") != game:
                return OTHER_GAME
            existing = StateManager.Get(self.Key("character"))
            # The rows are the slot's: none when it has no character slots
            # (a roster of 0 characters), whatever _CharacterSlots would
            # make up for a load (S4z review).
            rows = len(existing) if isinstance(existing, dict) else 0
            if not 1 <= row <= rows:
                return NO_SUCH_ROW
            slots = self._CharacterSlots(existing, snap)
            current = slots[row - 1]

            name, text = current.character, current.text
            changed = False
            if character is not NOT_SENT:
                if character:
                    picked = games.CharacterRow(character, snapshot=snap)
                    if picked is None:
                        return UNKNOWN_CHARACTER
                    name, text = character, picked.get("name") or ""
                else:
                    name, text = None, ""
                changed = name != current.character
            skins = len(snap.skinRows.get(name, ())) if name else 0
            if skin is not NOT_SENT:
                if skins:
                    if not 0 <= skin < skins:
                        return UNKNOWN_SKIN
                    number = skin
                else:
                    number = -1
            elif changed:
                number = 0 if skins else -1
            else:
                number = current.skin
            picks = current.variant
            if variant is not NOT_SENT:
                if variant and games.VariantRow(variant, snap) is None:
                    return UNKNOWN_VARIANT
                picks = variant or None

            slots[row - 1] = CharacterSlot(name, number, picks, text)
            characters = {index + 1: self.CharacterValue(pick, snap)
                          for index, pick in enumerate(slots)}
            held = {}
            if isinstance(existing, dict):
                held = {self._asInt(key, key): value
                        for key, value in existing.items()}
            if characters != held:
                self.PublishCharacterSlots(slots, snapshot=snap)
        return None

    # ── what the slot holds ──────────────────────────────────────────────

    def _Slot(self) -> dict:
        """A copy of the slot ({} for none). Caller holds the lock."""
        slot = StateManager.Get(self.path)
        return deep_clone(slot) if isinstance(slot, dict) else {}

    def _Text(self, field) -> str:
        """A text field as the card shows it ("" for none). Caller holds
        the lock."""
        value = StateManager.Get(self.Key(field))
        return "" if value is None else str(value)

    def Identity(self) -> tuple:
        """(id, seed) of the slot: what SlotChange compares."""
        with StateManager.lock:
            return (deep_clone(StateManager.Get(self.Key("id"))),
                    self._asInt(StateManager.Get(
                        self.Key(schema.PLAYER_SEED_KEY)), 0))

    def ChangeSince(self, identity) -> SlotChange:
        """What changed since `identity` (an earlier Identity())."""
        playerId, seed = self.Identity()
        return SlotChange(idChanged=playerId != identity[0],
                          seedChanged=seed != identity[1])

    def SlotFromValue(self, value, snapshot=None) -> CharacterSlot:
        """The identifiers that build `value`, one slot of `character`: the
        inverse of CharacterValue, i.e. the pick a card shows for it.

        Matched through the value rather than its `en_name`: a skin that is
        a different character (Dr. Mario) puts that character's name and
        en_name into the slot over the picked row's. `codename` is the row's
        own, so the rows sharing it (or, without one, the en_name) are the
        candidates, and the one that builds the value wins -- else the
        first. A value without an `en_name` is a typed name (or nothing).
        The skin is the value's own row number; the variant its en_name when
        the game has that variant.
        """
        if not isinstance(value, dict):
            return CharacterSlot()
        games = self._GameCatalog()
        snap = snapshot if snapshot is not None else games.Current()
        skin = value.get("skin")
        if not isinstance(skin, int) or isinstance(skin, bool):
            skin = -1
        variantValue = value.get("variant")
        variant = variantValue.get("en_name") \
            if isinstance(variantValue, dict) else None
        if variant is not None and games.VariantRow(variant, snap) is None:
            variant = None
        name = value.get("name")
        name = "" if name is None else str(name)
        if "en_name" not in value:
            return CharacterSlot(None, skin, variant, name)
        candidates = [
            data for _text, data in snap.characterRows
            if ("codename" in value
                and data.get("codename") == value.get("codename"))
            or data.get("en_name") == value.get("en_name")]
        for data in candidates:
            slot = CharacterSlot(data.get("en_name"), skin, variant,
                                 data.get("name") or "")
            if self.CharacterValue(slot, snap) == value:
                return slot
        if candidates:
            data = candidates[0]
            return CharacterSlot(data.get("en_name"), skin, variant,
                                 data.get("name") or "")
        return CharacterSlot(None, skin, variant, name)

    def _CharacterSlots(self, characters, snap) -> list:
        """The slot's character slots as picks, one per entry of
        `character` (the card's character rows; the default count when the
        slot has none yet). Keys are 1-based numbers, or their strings for
        a state read back from the store."""
        if not isinstance(characters, dict) or not characters:
            return [CharacterSlot()] * DEFAULT_CHARACTERS_PER_PLAYER
        return [self.SlotFromValue(
                    characters.get(index, characters.get(str(index))), snap)
                for index in range(1, len(characters) + 1)]

    # ── loading a player (ADR-005 S3e-3, S2f) ────────────────────────────
    #
    # The card's SetData was this, written as a form being filled: it set
    # the line edits' text and emitted their editingFinished, picked rows on
    # the country / region / character / skin / variant combos, and let the
    # handlers publish. What it published -- the writes, their order, the
    # save cycle -- is reproduced here from the state alone, so a set's
    # entrants, the Web's player edit, a tag lookup and the name completer
    # load a player without a widget.
    # tests/test_player_apply.py pins it against a recording of the widget
    # implementation (ae922c24); runs of writes to one key that the combos
    # made one per moved combo are one write here. Two of its bugs were
    # fixed in the S3e-3 review (the id and the city a load's clear kept, a
    # sponsor without a tag), and the steps they move were recorded again.

    def ApplyData(self, data, loadFromDB=True, clear=True,
                  no_mains=False) -> SlotChange:
        """Load the player in `data` (PlayerDB-shaped: gamerTag, prefix,
        name, twitter, custom_textbox, country_code, state_code, mains, plus
        a provider's id, seed, city, avatar) into the slot. One save cycle.

        - `clear` empties the slot first as a load did (`_Clear`): no
          display name, and the country hidden rather than published -- it
          is announced as gone only if no country follows. The id and the
          city are emptied too unless the load brings its own (S3e-3
          review: the card's clear kept the previous player's).
        - `loadFromDB` applies the local database's record for the player's
          tag (sponsor + gamer tag) first, then `data` over it -- when there
          is a tag to look up. `data` and the record are copies: the
          bad-word filter never edits either.
        - every text is censored for the player's country, and written only
          when it differs from the slot's (a field a load does not carry is
          left alone).
        - `no_mains` keeps the characters (of a card with one row; see
          `_Clear` for the old clear's limits) and ignores `mains`.
        - without `clear` (an update: the Web's player edit), a load that
          names another player -- the sponsor or the tag the slot ends up
          with is not the one it had -- empties the id and the city it does
          not write itself, as the clear does (`_NamesAnotherPlayer`,
          ADR-005 S3e-4). An update of the same player keeps them. The
          online avatar and the seed stay, as they do when the operator
          types a name on the desktop card (NameChanged; LIVE-BUG-1: fixing
          a typo in a tag must not cost the player a face).

        Which of the id and the city the load wrote is what it got to, not
        what it carries: a step that raises before the id leaves no previous
        player's id behind the new name, after a clear or an update (S3e-4
        review). The display name and the images are re-derived last, from
        what the slot ends up holding, even when a step raised -- and an
        error emptying the id there is logged rather than raised over the
        step's own. Returns what changed (SlotChange), which it has asked
        the statistics for (Announce) -- after a load that raised half-way
        too, for what it did write: inside its save cycle, outside the lock
        (a fetch is started there). An error working out what changed
        there is logged rather than raised over the step's own, too.
        """
        if not isinstance(data, dict):
            raise TypeError(f"player data for {self.path} is not a dict")
        data = copy.deepcopy(data)
        record = None
        if loadFromDB and data.get("gamerTag"):
            # The database is keyed by sponsor + tag (PlayerDB.PlayerTag):
            # without a tag, a sponsor names no record. The lookup used to
            # raise on one (after the clear), and the load stopped there.
            try:
                tag = PlayerDB.PlayerTag(data)
            except TypeError:
                logger.warning(
                    f"{self.path}: no database lookup for "
                    f"{data.get('prefix')!r} {data.get('gamerTag')!r}")
            else:
                db = PlayerDB.instance
                if db is not None:
                    with db.lock:
                        record = copy.deepcopy(db.Get(tag))
        # What the load writes over the previous player's own: the clear
        # leaves those to it rather than emptying them first.
        brings = {key for key in ("id", "city")
                  if data.get(key) or (record or {}).get(key)}

        change = before = None
        loadFailed = False
        with StateManager.SaveBlock():
            try:
                with StateManager.lock:
                    before = self.Identity()
                    slot = self._Slot()
                    country = slot.get(schema.PLAYER_COUNTRY_KEY)
                    region = slot.get(schema.PLAYER_STATE_KEY)
                    shown = country.get("code") \
                        if isinstance(country, dict) else None
                    # The card's location: the country it shows, the country
                    # its region rows are for (the same, until a load hides the
                    # country without telling), the region it shows.
                    location = {
                        "shown": shown, "rowsFor": shown,
                        "region": region.get("code")
                        if isinstance(region, dict) else None}
                    hidden = False
                    # Of the id and the city, those this load wrote.
                    wrote = set()
                    failed = False
                    try:
                        if clear and self._Clear(slot, no_mains, forLoad=True,
                                                 keep=brings):
                            hidden = location["shown"] is not None
                            location["shown"] = None
                            location["region"] = None
                        if record:
                            self._ApplyFields(record, no_mains, location,
                                              wrote=wrote)
                        self._ApplyFields(data, no_mains, location, hidden,
                                          wrote=wrote)
                    except BaseException:
                        failed = True
                        raise
                    finally:
                        sponsor = self._Text(schema.PLAYER_SPONSOR_KEY)
                        tag = self._Text(schema.PLAYER_TAG_KEY)
                        dropError = None
                        try:
                            # Also when a later step raised: the name is
                            # written. After a clear this only finds what the
                            # clear left for the load to write and it never did
                            # (a step raised).
                            if clear or self._NamesAnotherPlayer(slot, sponsor,
                                                                 tag):
                                self._DropIdentity(keep=wrote)
                        except Exception as e:
                            dropError = e
                        self.PublishMergedName(sponsor, tag)
                        self.PublishImages(sponsor, tag)
                        if dropError is not None:
                            if not failed:
                                raise dropError
                            # The step's own exception is what propagates.
                            logger.opt(exception=dropError).error(
                                f"{self.path}: the previous player's id was "
                                f"not emptied after a failed load")
                    change = self.ChangeSince(before)
            except BaseException:
                loadFailed = True
                raise
            finally:
                if before is not None:
                    try:
                        if change is None:
                            change = self.ChangeSince(before)
                        self.Announce(change)
                    except Exception as e:
                        if not loadFailed:
                            raise
                        # As emptying the id above: the load's own
                        # exception is what propagates (S3e-5 review).
                        logger.opt(exception=e).error(
                            f"{self.path}: the statistics were not asked "
                            f"for after a failed load")
        return change

    @staticmethod
    def _NamesAnotherPlayer(slot, sponsor, tag) -> bool:
        """Whether a slot that held `slot` (a copy read before the load) and
        now holds `sponsor` / `tag` is another player (ADR-005 S3e-4).

        The sponsor and the tag together name a player: they are the local
        database's key (PlayerDB.PlayerTag), and the desktop card drops the
        id when the operator finishes either of them with a new text
        (NameChanged). Compared as written -- censored, a blank field not
        carried -- and exactly: a spelling fix of the tag is another name,
        so the id goes with it (the statistics fetched again, under the new
        name, rather than kept for a name the slot no longer shows).
        """
        def text(key):
            value = slot.get(key)
            return "" if value is None else str(value)

        return (sponsor, tag) != (text(schema.PLAYER_SPONSOR_KEY),
                                  text(schema.PLAYER_TAG_KEY))

    def _DropIdentity(self, keep=()):
        """Empty the id and the city, but those in `keep` (what the load
        brings itself, and writes over them). Caller holds the lock and the
        block."""
        if "id" not in keep:
            self.SetPlayerId(None)
        if "city" not in keep:
            self.SetCity(None)

    def _ApplyFields(self, source, no_mains, location, hidden=False, *,
                     wrote):
        """One player dict's fields into the slot, in the order the card
        set them. `hidden`: a clear hid the country, which is announced
        (as no country) after the country step unless one was shown since.
        `wrote` (a set) collects "id" / "city" once written (for
        `_DropIdentity`'s `keep`). Caller holds the lock and the block."""
        countryCode = source.get("country_code")
        badWords = BadWordFilter.instance

        def censor(value):
            return badWords.Censor(value, countryCode) \
                if badWords is not None else value

        def text(field, raw):
            if not raw or raw == StateManager.Get(self.Key(field)):
                return
            value = str(censor(raw))
            if value != self._Text(field):
                self.SetText(field, value)

        for field, key in _NAME_FIELDS:
            text(field, source.get(key))

        if source.get("avatar"):
            self.PublishImages(self._Text(schema.PLAYER_SPONSOR_KEY),
                               self._Text(schema.PLAYER_TAG_KEY),
                               onlineAvatar=source.get("avatar"))
        if source.get("id"):
            self.SetPlayerId(source.get("id"))
            wrote.add("id")
        if source.get("city"):
            self.SetCity(source.get("city"))
            wrote.add("city")

        text(schema.PLAYER_TWITTER_KEY, source.get("twitter"))

        note = source.get("custom_textbox")
        if note and note != StateManager.Get(self.Key(schema.PLAYER_NOTE_KEY)):
            # Stored with its line breaks escaped (PlayerDB, SavePlayerToDB).
            value = NoteFromDB(f"{censor(note)}")
            if value != self._Text(schema.PLAYER_NOTE_KEY):
                self.SetText(schema.PLAYER_NOTE_KEY, value)

        if countryCode:
            # A code the catalog does not know shows no country.
            target = countryCode \
                if self._CountryCatalog().CountryByCode(countryCode) else None
            if target != location["shown"]:
                self._ShowCountry(target, location)
        if hidden and location["shown"] is None:
            self._ShowCountry(None, location)

        stateCode = source.get("state_code")
        if stateCode:
            catalog = self._CountryCatalog()
            # The codes only: States() resolves every region's flag on disk,
            # under the state lock here.
            rows = catalog.StateCodes(location["rowsFor"]) \
                if location["rowsFor"] else None
            target = self._RegionRow(rows, stateCode)
            if target is None and countryCode:
                # A free-form name (an older build's, or typed): the city
                # table resolves it, whose first read parses a file -- so
                # only when the code is not a region's own.
                resolved = catalog.FindState(countryCode, stateCode)
                if resolved:
                    target = self._RegionRow(rows, resolved)
            if target != location["region"]:
                self.SetRegion(location["rowsFor"], target)
                location["region"] = target

        mains = source.get("mains")
        if mains and no_mains != True:
            self._ApplyMains(mains)

        if "seed" in source:
            # None (unseeded), 0, a numeric string: anything but a positive
            # number is no seed, and clears the previous player's.
            seed = self._asInt(source.get("seed") or 0, 0)
            self.SetSeed(seed if seed > 0 else 0)

    def _ShowCountry(self, code, location):
        """Show country `code` (None: none) as a pick did: the region rows
        are rebuilt for it first when they are another country's, which
        publishes the blank region."""
        if code != location["rowsFor"]:
            self.SetRegion(None, None)
            location["rowsFor"] = code
            location["region"] = None
        self.SetCountry(code)
        location["shown"] = code

    @staticmethod
    def _RegionRow(rows, originalCode):
        """The code of the first region row whose `original_code` is
        `originalCode`, or None."""
        for row in (rows or {}).values():
            if row and row.get("original_code") == originalCode:
                return row.get("code")
        return None

    def _MainSlot(self, name, skin, variant, snap) -> CharacterSlot:
        """The pick a `mains` entry makes: the character row named `name`
        (none: the blank row), its skin row `skin` (none past the rows, or
        without a character), the variant as given."""
        row = self._GameCatalog().CharacterRow(name, snapshot=snap)
        if row is None:
            return CharacterSlot(None, -1, variant, "")
        skins = len(snap.skinRows.get(name, ()))
        return CharacterSlot(name, skin if 0 <= skin < skins else -1,
                             variant, row.get("name") or "")

    def _ApplyMains(self, mains):
        """The characters a player's `mains` name: the legacy list
        (["Mario", skin]) for the first row only, the current game's list
        of [name, skin, variant] from the first row on. Rows past the list
        keep what they had. `character` is written once, if it changed."""
        games = self._GameCatalog()
        snap = games.Current()
        existing = StateManager.Get(self.Key("character"))
        slots = self._CharacterSlots(existing, snap)
        if type(mains) == list:
            if slots:
                skin = self._SkinNumber(mains[1], mains[0]) \
                    if len(mains) > 1 else 0
                slots[0] = self._MainSlot(mains[0], skin, slots[0].variant,
                                          snap)
        elif type(mains) == dict:
            picked = mains.get((snap.game or {}).get("codename"), [])
            for index, main in enumerate(picked):
                if index >= len(slots):
                    continue
                skin = self._SkinNumber(main[1], main[0]) \
                    if len(main) > 1 else 0
                variant = main[2] if len(main) >= 3 \
                    and games.VariantRow(main[2], snap) is not None else None
                slots[index] = self._MainSlot(main[0], skin, variant, snap)
        else:
            return
        characters = {index + 1: self.CharacterValue(slot, snap)
                      for index, slot in enumerate(slots)}
        current = {}
        if isinstance(existing, dict):
            current = {self._asInt(key, key): value
                       for key, value in existing.items()}
        if characters != current:
            StateManager.Set(self.Key("character"), characters)

    def _SkinNumber(self, value, character) -> int:
        """A `mains` entry's skin row, or the first skin (0) for one that is
        not a number -- with a warning. The desktop's completer model
        rewrites a stored record's mains so (AutonaPlayerDB._BuildModel,
        S3c-2 decision 5) before anything loads it; reading them alike here
        gives both shells the same characters, a headless one included,
        where the record is loaded as stored. A dict main used to raise on
        it (ValueError, its characters unwritten); an infinity, an
        OverflowError."""
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            logger.warning(
                f"{self.path}: the skin of main {character!r} is not a "
                f"number ({value!r}); using the first skin")
            return 0

    def SavePlayerToDB(self):
        """The card's save button: PrepareSaveToDB, then SaveRecordToDB.
        The caller holds no lock (see SaveRecordToDB)."""
        self.SaveRecordToDB(self.PrepareSaveToDB())

    @staticmethod
    def SaveRecordToDB(prepared: PreparedSave) -> bool:
        """The database half of SavePlayerToDB: the record PrepareSaveToDB
        read into the local player database. The record keeps the
        database's other games' mains (PlayerDB.AddPlayers overwrite), those
        of a stored JSON string too (PlayerDB.KeepStoredMains).

        Called with no lock held -- a board host's included: the database
        announces the change (PlayerDB.changed), and its relay rebuilds the
        completer model on the main thread (AutonaPlayerDB.SetupModel, both
        shells; ADR-005 S3e-4 review), which until ADR-005 S3f-2 also waited
        for it.

        Saves of one player can therefore reach here out of the order their
        slots were read in (two Web edits, each saved after its board host
        let go). A record read before the one saved last under the same
        database key is dropped: False. The merge is made under the save
        lock, so the check and the write are one step; the file write and
        the announcement come after it, as AddPlayers makes them."""
        record, sequence = prepared
        db = PlayerDB.instance
        tag = PlayerDB.PlayerTag(record)
        with PlayerService._saveLock:
            if PlayerService._savedSequence.get(tag, 0) > sequence:
                # No tag in the message: it is a player's name (ADR-005 S4m).
                logger.info("Player DB: an older save of a player dropped "
                            "(a newer one is saved)")
                return False
            PlayerService._savedSequence[tag] = sequence
            with db.lock:
                db.KeepStoredMains(tag)
                db.AddPlayers([record], overwrite=True, deferRefresh=True)
        db.SaveDB()
        db.NotifyChanged()
        return True

    @staticmethod
    def NextSaveSequence() -> int:
        """The next number of the order SaveRecordToDB keeps: taken when a
        record is read, by PrepareSaveToDB from a slot and by
        PlayerDirectory.Save from a request (ADR-005 S4m), so saves from
        either keep the same order."""
        with PlayerService._saveLock:
            PlayerService._saveSequence += 1
            return PlayerService._saveSequence

    def PrepareSaveToDB(self) -> PreparedSave:
        """The state half of SavePlayerToDB: the slot as the local player
        database stores a player (PlayerDB.fieldnames' shape, the current
        game's characters as its mains), numbered in the order slots are
        read and returned for SaveRecordToDB, with the characters
        re-published as the slot's mains too. Writes the state only, so it
        goes in the caller's save block and lock; the record is read from
        the slot here, and whenever it is written to the database it is
        what the slot held at this point."""
        snap = self._GameCatalog().Current()
        with StateManager.lock:
            slot = self._Slot()
            sequence = PlayerService.NextSaveSequence()

        def text(field):
            value = slot.get(field)
            return "" if value is None else str(value)

        player = {
            "prefix": text(schema.PLAYER_SPONSOR_KEY),
            "gamerTag": text(schema.PLAYER_TAG_KEY),
            "name": text(schema.PLAYER_REAL_NAME_KEY),
            "twitter": text(schema.PLAYER_TWITTER_KEY),
            # One line in the file: the line breaks escaped.
            "custom_textbox": NoteForDB(text(schema.PLAYER_NOTE_KEY)),
        }
        slots = self._CharacterSlots(slot.get("character"), snap)
        codename = (snap.game or {}).get("codename")
        if codename:
            player["mains"] = {codename: [
                [pick.character, pick.skin, pick.variant or ""]
                for pick in slots if pick.character]}
        for key, field in (("country_code", schema.PLAYER_COUNTRY_KEY),
                           ("state_code", schema.PLAYER_STATE_KEY)):
            value = slot.get(field)
            if isinstance(value, dict) and value:
                player[key] = value.get("code")

        self.PublishCharacterSlots(slots, includeMains=True)
        return PreparedSave(player, sequence)

    # ── slot lifecycle ───────────────────────────────────────────────────

    def SeedEmpty(self, characters=DEFAULT_CHARACTERS_PER_PLAYER):
        """Publish what an empty player card publishes as it is built.

        The card's construction wrote the seed, the birthday flag and the
        two location combos (_WireInputs), then its character slots and
        mains once the board set the slot count -- every one of them empty,
        in that order. The line edits write nothing until an edit. This is
        that write without the card, in one save cycle: since ADR-005 S3e-6
        for every new slot of a roster (AutonaActions.SetRoster), in both
        shells -- a board card is built on a seeded slot and writes nothing
        as it is. tests/test_headless_seed.py pins it against a desktop board.
        """
        with StateManager.SaveBlock():
            self.SetSeed(0)
            self.SetBirthday(False)
            self.SetCountry(None)
            self.SetRegion(None, None)
            self.PublishCharacterSlots(
                [CharacterSlot()] * characters, includeMains=True)

    def ClearSlot(self, no_mains=False, announce=True) -> SlotChange:
        """Empty the slot as the card's clear button did (`_Clear`). One
        save cycle; returns what changed, which it asks the statistics for
        unless told not to (`AutonaActions.ClearPlayers` asks once for the
        whole board instead)."""
        with StateManager.SaveBlock():
            with StateManager.lock:
                before = self.Identity()
                self._Clear(self._Slot(), no_mains)
                change = self.ChangeSince(before)
            if announce:
                self.Announce(change)
        return change

    def _Clear(self, slot, no_mains, forLoad=False, keep=()) -> bool:
        """Empty the slot as the card's Clear() did; `slot` is a copy of it
        read before the first write. Caller holds the lock and the block.
        True when the location was cleared (see `no_mains` below).

        Clear() emptied the card's widgets and let their handlers publish,
        so what it wrote depended on what each widget showed -- which, since
        S2a, is the slot itself:

        - `online_avatar`: always None (it stated that outright).
        - the four line edits and the note: emptied when they held text.
        - the display name, the images and the id: re-published for an
          empty name when the sponsor or the tag held text (the name
          fields' NameChanged); a slot with an id but no name keeps it.
        - the seed: always written, as 0 (its line edit's editingFinished).
        - the country and the region: emptied when shown (a country takes
          its region with it, the region going first, as LoadStates
          publishes it).
        - the characters: all of the card's slots blank, when one of them
          was not -- the card has as many as `character` holds, since every
          slot-count change republishes it.

        `no_mains` kept the characters by skipping the combos of the card's
        character rows, in a loop that only did so for a card with a single
        row: with two or more, every row's combos were reset by the others'
        pass, and with none, the country and region combos were not reset
        either. Reproduced as it was -- a set load with `no_mains` publishes
        this state until the set arrives (and its load clears the same
        way).

        `forLoad` is the clear a load starts with (ApplyData), which the
        card ran with its name handlers held back and its country combo
        silenced: no display name or images, and the country left in the
        state (hidden; ApplyData announces it gone if no country follows).
        Its region rows stayed too, so only a shown region is published as
        emptied. It kept the id and the city as well, so a player loaded
        without them carried the previous one's -- and the statistics,
        keyed by the id, stayed the previous player's (S3e-3 review). Both
        are emptied now, after the name fields, unless they are in `keep`:
        what the load brings itself and writes over them (and, should a
        step raise before it does, ApplyData empties them after all).
        """
        def text(key):
            value = slot.get(key)
            return "" if value is None else str(value)

        named = bool(text(schema.PLAYER_SPONSOR_KEY)
                     or text(schema.PLAYER_TAG_KEY))
        characters = slot.get("character")
        rows = len(characters) if isinstance(characters, dict) else 0

        self.SetOnlineAvatar(None)

        for field in schema.PLAYER_LINE_EDIT_KEYS:
            if text(field):
                self.SetText(field, "")
        if named and not forLoad:
            self.PublishMergedName("", "")
            self.PublishImages("", "")
            self.SetPlayerId(None)
        if forLoad:
            self._DropIdentity(keep)

        self.SetSeed(0)

        if text(schema.PLAYER_NOTE_KEY):
            self.SetText(schema.PLAYER_NOTE_KEY, "")

        locationCleared = not (no_mains and rows == 0)
        if locationCleared:
            if slot.get(schema.PLAYER_COUNTRY_KEY) and not forLoad:
                self.SetRegion(None, None)
                self.SetCountry(None)
            elif slot.get(schema.PLAYER_STATE_KEY):
                self.SetRegion(None, None)

        if rows and not (no_mains and rows == 1):
            blank = self.CharacterValue(CharacterSlot())
            if any(value != blank for value in characters.values()):
                self.PublishCharacterSlots([CharacterSlot()] * rows)
        return locationCleared


def RepublishTeamMergedNames(board, team) -> list:
    """Re-publish the display names of every slot of one team, from the state.

    The other half of a losers-flag write (ADR-005 S2b): the flag's writers
    -- ``AutonaActions.SetTeamLosers`` and ``SetLoadService.ApplyLosers`` --
    call this in the save block of the flag itself, so flipping it is one
    save cycle instead of one per player card. Each slot's name is rebuilt
    from what it holds (``team`` / ``name``) through ``PublishMergedName``,
    which reads the new flag.

    Every slot the state carries for the team, named or not -- the per-card
    handler this replaces re-exported every card's name, the blank ones
    included. Returns the slot keys re-published.
    """
    prefix = f"score.{board}.team.{team}.player"
    with StateManager.lock:
        players = StateManager.Get(prefix)
        if not isinstance(players, dict):
            return []
        names = [(slot, value.get(schema.PLAYER_SPONSOR_KEY) or "",
                  value.get(schema.PLAYER_TAG_KEY) or "")
                 for slot, value in list(players.items())
                 if isinstance(value, dict)]
        with StateManager.SaveBlock():
            for slot, sponsor, tag in names:
                PlayerService(f"{prefix}.{slot}").PublishMergedName(
                    sponsor, tag)
    return [slot for slot, _sponsor, _tag in names]


