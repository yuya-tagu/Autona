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
``StateSchema`` and the writes live here. The division of labour with the Qt
side is the one Step 4b drew for ``SetData``: **resolving a value stays in
Qt, deciding what the state holds is core's**. The country combo's row, the
character/skin/variant model rows and the bad-word filter are all Qt model
lookups and are still done by ``AutonaScoreboardPlayerWidget``; what it does
with the answer -- which key, in what shape, under which slot -- is a call
into this class.

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
``src/core`` -- ``AutonaSponsorHelper`` is imported for the crest lookup and
is itself Qt-free (``tests/test_core_qt_free.py`` pins both).
"""
import os
import re

from loguru import logger

from ..Helpers.AutonaSponsorHelper import AutonaSponsorHelper
from . import StateSchema as schema
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


class PlayerService:
    """The state subtree of one player slot."""

    def __init__(self, path: str):
        self.path = path

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

    def SetLocation(self, field, value) -> bool:
        """Set the country or the region. False when `field` is neither.

        The value is the combo's ``currentData()`` -- a dict carrying the
        code, the display name and the flag asset, or ``{}`` for the blank
        row every rebuild starts on. Resolving *which* row is Qt's job
        (``LoadStates`` / ``SetData``); this is where the answer lands.

        Written even when it is the same value: a set load empties the
        country and puts it back, and the empty publish in between is what
        ``tests/test_player_widgets`` counts to know the region combo's
        in-place refill still emits exactly one index change.
        """
        if field not in schema.PLAYER_LOCATION_KEYS:
            logger.warning(f"PlayerService.SetLocation: unknown field {field}")
            return False
        StateManager.Set(self.Key(field), value)
        return True

    # ── derived: the display name ────────────────────────────────────────

    def PublishMergedName(self, sponsor, tag, losers) -> str:
        """Publish the two display-name spellings overlays render.

        ``mergedName`` carries the sponsor prefix, ``mergedOnlyName`` never
        does; both carry the " [L]" suffix when the player's team came out of
        the losers bracket (the thumbnail generator reads it). One save cycle,
        because they are one fact -- the pair used to be two bare Sets, so a
        board could notify twice for a single name edit.
        """
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

    def PublishCharacters(self, characters, includeMains=False):
        """Publish the slot -> character mapping the widget just read.

        `characters` is keyed by 1-based slot number and each value is the
        shape the overlays expect ({name, en_name, assets, skin, variant}).
        Building it is Qt work (three combos per slot, each answering with a
        model row); the shape and where it goes are here.

        `includeMains` additionally republishes it as the player's mains,
        which is what a slot-count change does -- one save cycle for the two
        keys, since they are written from the same read.
        """
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
