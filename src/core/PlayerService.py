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
(``GameAssetCatalog`` / ``CountryCatalog``). Which row a combo shows (and the
bad-word filter) is still the card's business; what the state holds for that
pick is decided here, so it can be produced without a widget.

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
(``city`` and ``id``) keep their guards below.

Import side effects: none (agent-pitfalls #1). Qt-free, like everything under
``src/core`` -- including ``SponsorHelper``, which is imported for the crest
lookup and which moved into core alongside this module's own instalment
(Step 5-5) precisely because this was its only caller
(``tests/test_core_qt_free.py`` pins both).
"""
import os
import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from . import StateSchema as schema
from .CountryCatalog import CountryCatalog
from .DictHelper import qvariantmap_copy
from .GameAssetCatalog import GameAssetCatalog
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

    # ── slot lifecycle ───────────────────────────────────────────────────

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
