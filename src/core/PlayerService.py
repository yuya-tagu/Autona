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

The values are byte-compatible with what the card used to publish. A Qt
model hands its dicts out as QVariantMaps -- the keys of every nested dict in
UTF-16 order -- and program_state.json and the Socket.IO delta are both
serialised in insertion order, so that order is reproduced
(``DictHelper.qvariantmap_copy``); tests/test_player_slot_publish.py pins it
against a recording of the old path.

One instance per player widget, bound to that widget's path, exactly as
``SetLoadService`` is bound to one board. The path is one of two shapes
(agent-pitfalls #7 lists three; the ``commentary.{N}`` and
``player_list.slot.{I}`` callers are gone -- the commentary panel has its own
cards and the roster editor was retired):

- ``score.{N}.team.{T}.player.{M}`` -- a board's player card
- ``player_editor.1``               -- the PLAYERS panel's editor dialog

``BoardNumber()`` is what tells them apart, and it is why nothing here
hard-codes ``score.``.

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

# A loaded player's fields, PlayerDB-shaped key -> slot key, in the order
# they were set (the gamer tag, the sponsor, the real name).
_NAME_FIELDS = (
    (schema.PLAYER_TAG_KEY, "gamerTag"),
    (schema.PLAYER_SPONSOR_KEY, "prefix"),
    (schema.PLAYER_REAL_NAME_KEY, "name"),
)

# Character slots per player card on a freshly built board: the board
# forces its charNumber back to this whatever the game says
# (AutonaScoreboardWidget.SetDefaultsFromAssets).
DEFAULT_CHARACTERS_PER_PLAYER = 3


def DefaultPlayersPerTeam() -> int:
    """Player cards per team on a freshly built 1v1 / team board.

    The loaded game's ``game.defaults.players_per_team``, 1 without one --
    what AutonaScoreboardWidget.SetDefaultsFromAssets applies to a board
    right after building it, and again on each game load.
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


class SlotChange(NamedTuple):
    """What a write did to the two facts the board's statistics hang off:
    who is in the slot (`id`) and their seed.

    The board fires its statistics triggers from it (the recent sets and
    the last sets / standings for an id, the upset factor for a seed); the
    player card's own signals used to, one per widget change. A change is
    the slot's value after the operation against the one before, so a load
    that empties the seed and puts the same one back changes nothing.
    """
    idChanged: bool = False
    seedChanged: bool = False


class PlayerService:
    """The state subtree of one player slot."""

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
        """The board this slot belongs to, or None when it is not on one.

        ``player_editor.1`` (the PLAYERS panel's editor) is the same widget
        on a path that has no board, so anything board-shaped -- the 反映
        commit behind the details dialog's button -- has to ask first.
        """
        parts = self.path.split(".")
        if len(parts) < 2 or parts[0] != "score":
            return None
        try:
            return int(parts[1])
        except (TypeError, ValueError):
            return None

    def TeamLosersKey(self):
        """``score.{N}.team.{T}.losers`` for a board slot, None otherwise.

        The flag the slot's display name carries as " [L]". The editor's
        ``player_editor.1`` belongs to no team, so it never has the suffix.
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

    def SetSeed(self, value) -> bool:
        """Publish the seed as a number.

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
        """What `country` holds for the country (or custom flag) `code`.

        The country combo's row for that code -- code, names, flag asset,
        emoji, coordinates -- in the key order the combo handed it out in.
        {} for no country, which is also the answer for a code the catalog
        does not know: a name typed into the editable combo that matches no
        row, or the custom-flag separator. Those two used to publish null;
        they now have the type every other "no country" has always had.
        """
        row = self._CountryCatalog().CountryByCode(code) if code else None
        return qvariantmap_copy(row) if row else {}

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
        """What one slot of `character` holds for `slot`.

        The shape the overlays read: the character row (name, en_name,
        display_name, codename, modded), with the skin row's name / en_name
        over it when that skin is a different character (Dr. Mario), then
        `assets` (every asset pack's file for that skin), `skin` and
        `variant`. Without a character row the slot is just its name
        (CharacterSlot.text): {name, assets: {}, skin, variant}.

        Dict keys come in the order the combos handed their rows out in
        (module docstring); the three appended keys come last, as they
        always did. `snapshot` is GameAssetCatalog.Current(), taken once by
        the caller so every slot of a card comes from one game.
        """
        games = self._GameCatalog()
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

    def PublishCharacterSlots(self, slots, includeMains=False):
        """Publish the card's character slots (`character`, keyed by 1-based
        slot number), built from what each slot picked.

        `includeMains` additionally republishes them as the player's mains,
        which is what a slot-count change does -- one save cycle for the two
        keys, since they are written from the same read (and, as before, the
        same dict).
        """
        snap = self._GameCatalog().Current()
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

    def SetPlayerId(self, value) -> bool:
        """Set the provider's player id. True when the state changed.

        The caller uses the return value: the board's statistics fetches hang
        off "the player in this slot is now someone else", so they must fire
        on a change and not on a re-assertion.
        """
        if StateManager.Get(self.Key("id")) == value:
            return False
        StateManager.Set(self.Key("id"), value)
        return True

    def SetOnlineAvatar(self, value):
        """Write the provider-supplied avatar on its own.

        Separate from PublishImages because Clear() states it rather than
        letting it fall out of a name-derived refresh -- which is the accident
        that used to wipe avatars that were meant to stay.
        """
        StateManager.Set(self.Key("online_avatar"), value)

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
    # entrants, the Web's player edit, a tag lookup, the name completer and
    # the PLAYERS editor load a player without a widget.
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

        The display name and the images are re-derived last, from what the
        slot ends up holding, even when a step raised. Returns what changed
        for the statistics (SlotChange).
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

        with StateManager.SaveBlock():
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
                try:
                    if clear and self._Clear(slot, no_mains, forLoad=True,
                                             keep=brings):
                        hidden = location["shown"] is not None
                        location["shown"] = None
                        location["region"] = None
                    if record:
                        self._ApplyFields(record, no_mains, location)
                    self._ApplyFields(data, no_mains, location, hidden)
                finally:
                    sponsor = self._Text(schema.PLAYER_SPONSOR_KEY)
                    tag = self._Text(schema.PLAYER_TAG_KEY)
                    self.PublishMergedName(sponsor, tag)
                    self.PublishImages(sponsor, tag)
                return self.ChangeSince(before)

    def _ApplyFields(self, source, no_mains, location, hidden=False):
        """One player dict's fields into the slot, in the order the card
        set them. `hidden`: a clear hid the country, which is announced
        (as no country) after the country step unless one was shown since.
        Caller holds the lock and the block."""
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
        if source.get("city"):
            self.SetCity(source.get("city"))

        text(schema.PLAYER_TWITTER_KEY, source.get("twitter"))

        note = source.get("custom_textbox")
        if note and note != StateManager.Get(self.Key(schema.PLAYER_NOTE_KEY)):
            # Stored with its line breaks escaped (PlayerDB, SavePlayerToDB).
            value = _LINE_BREAKS.sub(
                "\n", f"{censor(note)}".replace("\\n", "\n"))
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
        it (ValueError, its characters unwritten)."""
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning(
                f"{self.path}: the skin of main {character!r} is not a "
                f"number ({value!r}); using the first skin")
            return 0

    def SavePlayerToDB(self):
        """The card's save button: the slot as the local player database
        stores a player (PlayerDB.fieldnames' shape, the current game's
        characters as its mains), then the characters re-published as the
        slot's mains too. The record keeps the database's other games'
        mains (PlayerDB.AddPlayers overwrite)."""
        snap = self._GameCatalog().Current()
        with StateManager.lock:
            slot = self._Slot()

        def text(field):
            value = slot.get(field)
            return "" if value is None else str(value)

        player = {
            "prefix": text(schema.PLAYER_SPONSOR_KEY),
            "gamerTag": text(schema.PLAYER_TAG_KEY),
            "name": text(schema.PLAYER_REAL_NAME_KEY),
            "twitter": text(schema.PLAYER_TWITTER_KEY),
            # One line in the file: the line breaks escaped.
            "custom_textbox": "\\n".join(
                text(schema.PLAYER_NOTE_KEY).splitlines()),
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

        PlayerDB.instance.AddPlayers([player], overwrite=True)
        self.PublishCharacterSlots(slots, includeMains=True)

    # ── slot lifecycle ───────────────────────────────────────────────────

    def SeedEmpty(self, characters=DEFAULT_CHARACTERS_PER_PLAYER):
        """Publish what an empty player card publishes as it is built.

        The card's construction writes the seed, the birthday flag and the
        two location combos (_WireInputs), then its character slots and
        mains once the board sets the slot count -- every one of them empty,
        in that order. The line edits write nothing until an edit. This is
        that write without the card, in one save cycle, for a shell that has
        no player cards (the headless one, ADR-005 S3d): the overlays draw a
        board only once its team 1 has a player slot.
        tests/test_headless_seed.py pins it against a desktop board.
        """
        with StateManager.SaveBlock():
            self.SetSeed(0)
            self.SetBirthday(False)
            self.SetCountry(None)
            self.SetRegion(None, None)
            self.PublishCharacterSlots(
                [CharacterSlot()] * characters, includeMains=True)

    def ClearSlot(self, no_mains=False) -> SlotChange:
        """Empty the slot as the card's clear button did (`_Clear`). One
        save cycle; returns what changed for the statistics."""
        with StateManager.SaveBlock():
            with StateManager.lock:
                before = self.Identity()
                self._Clear(self._Slot(), no_mains)
                return self.ChangeSince(before)

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
        what the load brings itself and writes over them.
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
            if "id" not in keep:
                self.SetPlayerId(None)
            if "city" not in keep:
                self.SetCity(None)

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

    def Unset(self):
        """Drop this slot's whole subtree.

        Called when a player widget is torn down by a players-per-team
        change. The state has to go with the widget: a leftover
        ``player.3`` keeps a third name on the overlay of a board that only
        shows two.
        """
        StateManager.Unset(self.path)


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


def SeedEmptyRoster(board, players=None,
                    characters=DEFAULT_CHARACTERS_PER_PLAYER) -> int:
    """Seed both teams of a 1v1 / team board with empty player slots, as a
    freshly built board's player cards publish them; returns the slots per
    team. One save cycle.

    `players` defaults to DefaultPlayersPerTeam(), the count the desktop
    gives a board it has just built. A launch seed (the headless shell's,
    after ``StateSchema.ResetScoreRoot`` / ``SeedBoard``): never for a live
    board, whose cards own their slots.
    """
    if players is None:
        players = DefaultPlayersPerTeam()
    players = PlayerService._asInt(players, 1)
    with StateManager.SaveBlock():
        # The order the board builds its cards in: team 1's, then team 2's,
        # one index at a time (AutonaScoreboardWidget._setPlayersPerTeamImpl).
        for slot in range(1, players + 1):
            for team in (1, 2):
                PlayerService(
                    f"score.{board}.team.{team}.player.{slot}"
                ).SeedEmpty(characters)
    return players
