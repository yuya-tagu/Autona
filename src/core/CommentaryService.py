"""The caster line-up's writer (`commentary.*`, ADR-005 S3e-2).

Every write to the line-up goes through here: the Web's
``/update-commentary-<N>`` / ``/load-commentator-from-tag-<N>`` (and their
Socket.IO events) and the Commentary panel's edits. The shape and the
normalisation rules are ``CommentaryModel``'s; this module is what applies
them to the state.

Until S3e-2 the panel was the writer. A web call built the panel (it is built
on first reveal), filled one of its cards and had the panel publish *every*
card it held -- so a panel that had not seen a later change put its own,
older copy of the other eleven slots back, and nothing worked without a
panel. Now each operation reads the line-up from the state, changes what it
was asked to change and publishes; the React page (CommentaryPage) renders
the state and asks for its edits here.

What a web call publishes is unchanged (tests/fixtures/commentary/golden.json,
recorded through the panel on 8aa8c33b): one unconditional ``Set`` of the
whole ``commentary`` root -- all twelve slots, in slot order, each slot's
keys in ``DefaultSlot`` order -- with the slot's values as the panel's
inputs used to hand them over (stripped, the handle without its "@"). The
panel's own edits write the one field that changed instead
(``SetField``): rebuilding the root from the inputs was how a stale card
overwrote the other slots.

Stateless, so module functions (as ``Top8Service`` / ``ColorService``). The
bad-word filter and the player DB are read at call time
(``BadWordFilter.instance`` / ``PlayerDB.instance``, built by
``bootstrap_core()``). Import side effects: none (agent-pitfalls #1).
"""
from loguru import logger

from . import CommentaryModel as comms
from . import StateSchema as schema
from .BadWordFilter import BadWordFilter
from .PlayerDB import PlayerDB
from .StateManager import StateManager

# Caster field <- the key of a PlayerDB-shaped dict (a completer pick, the
# Web's payload and a DB lookup all deliver this shape; see
# core.PlayerDB.fieldnames). The order is the order they are censored in.
PLAYERDB_FIELD_MAP = (
    ("name", "gamerTag"),
    ("team", "prefix"),
    ("twitter", "twitter"),
    ("note", "custom_textbox"),
)


def _Key(slot, field=None) -> str:
    if field is None:
        return f"{schema.COMMENTARY_ROOT}.{slot}"
    return f"{schema.COMMENTARY_ROOT}.{slot}.{field}"


def FieldValue(field, value):
    """`value` as the line-up stores it in `field`.

    The text fields are stripped and the handle loses its leading "@"; the
    icon is a known stem or nothing; the display flag is 1 or 0. These are
    the panel's inputs' rules (what its ``_syncToState`` read off them), so a
    value written here and one typed into the panel end up the same.
    """
    if field == "display":
        return 1 if value else 0
    if field == "icon":
        return value if value in comms.ICONS else ""
    text = value if isinstance(value, str) else ""
    if field == "twitter":
        return text.strip().lstrip("@")
    return text.strip()


def _Slot(data) -> dict:
    """One slot as the line-up republishes it, in ``DefaultSlot`` order."""
    data = data if isinstance(data, dict) else {}
    return {field: FieldValue(field, data.get(field, default))
            for field, default in comms.DefaultSlot().items()}


def _LineUp() -> dict:
    """The whole line-up, every slot rebuilt (fresh dicts: StateManager.Get
    returns the live tree, and a changed copy of it is what makes the
    publish a delta)."""
    state = StateManager.Get(schema.COMMENTARY_ROOT)
    state = state if isinstance(state, dict) else {}
    return {slot: _Slot(state.get(slot)) for slot in comms.SLOTS}


def ApplyPlayerData(slot, data) -> bool:
    """Fill caster `slot` from a PlayerDB-shaped dict; True if it applied.

    Only the string values `data` carries are taken (censored, then
    normalised as ``FieldValue``); role, icon and display are left alone, and
    so is a field `data` does not carry or carries as something else. The
    other slots are republished as the state holds them. A slot that is not
    one of ``CommentaryModel.SLOTS`` or a `data` that is not a dict writes
    nothing.
    """
    slot = str(slot)
    if slot not in comms.SLOTS or not isinstance(data, dict):
        logger.warning(f"Commentary: ignoring player data for slot {slot}")
        return False
    censor = BadWordFilter.instance.Censor
    with StateManager.lock:
        lineUp = _LineUp()
        caster = lineUp[slot]
        for field, source in PLAYERDB_FIELD_MAP:
            value = data.get(source)
            if isinstance(value, str):
                caster[field] = FieldValue(field, censor(value, None) or "")
        StateManager.Set(schema.COMMENTARY_ROOT, lineUp)
    return True


def LoadFromTag(slot, tag) -> bool:
    """Fill caster `slot` from the local player DB entry whose gamer tag is
    `tag` (``PlayerDB.GetPlayerFromTag``). True if a player was found and
    the slot filled; False writes nothing."""
    if str(slot) not in comms.SLOTS:
        return False
    player = PlayerDB.instance.GetPlayerFromTag(str(tag))
    if not player:
        return False
    return ApplyPlayerData(slot, player)


def SetField(slot, field, value) -> bool:
    """One field of one caster, the panel's edit. True if the state changed.

    Only that key is written, and only when it changes: the panel's other
    inputs may be showing a value the state has since moved past. A caster
    is not shown on the overlay past ``MAX_DISPLAY`` others -- the panel
    disables the boxes at that count, and an edit that would exceed it
    anyway is refused here.
    """
    slot = str(slot)
    if slot not in comms.SLOTS or field not in comms.DefaultSlot():
        logger.warning(f"Commentary: ignoring {field!r} for slot {slot}")
        return False
    value = FieldValue(field, value)
    with StateManager.lock:
        if field == "display" and value:
            shown = _ShownOthers(slot)
            if shown >= comms.MAX_DISPLAY:
                logger.warning(
                    f"Commentary: slot {slot} not shown, {shown} already are")
                return False
        return StateManager.SetIfChanged(_Key(slot, field), value)


def CanShow(slot) -> bool:
    """Whether caster `slot` may be shown on the overlay: fewer than
    ``MAX_DISPLAY`` of the other slots are. A slot already shown can stay
    shown. What ``SetField`` checks before showing one; the Web's edit
    (``PATCH /api/v1/commentary/<slot>``) asks first, under the same hold
    of ``StateManager.lock`` as its writes, so a refused edit writes none
    of its fields."""
    return _ShownOthers(str(slot)) < comms.MAX_DISPLAY


def _ShownOthers(slot) -> int:
    """How many slots other than `slot` (a str) are shown."""
    with StateManager.lock:
        state = StateManager.Get(schema.COMMENTARY_ROOT)
        state = state if isinstance(state, dict) else {}
        return sum(
            1 for other in comms.SLOTS
            if other != slot and isinstance(state.get(other), dict)
            and state[other].get("display"))


def Reorder(order) -> bool:
    """Move the casters to new slots (the panel's drag). True if it applied.

    `order` names, for slot 1, 2, ... in turn, the slot whose caster goes
    there -- a permutation of ``CommentaryModel.SLOTS``. One write of the
    whole root: an overlay reading the slots must not see a caster in two
    of them.
    """
    order = [str(slot) for slot in (order or ())]
    if sorted(order) != sorted(comms.SLOTS):
        logger.warning(f"Commentary: ignoring the reorder {order}")
        return False
    with StateManager.lock:
        lineUp = _LineUp()
        StateManager.Set(
            schema.COMMENTARY_ROOT,
            {slot: lineUp[source] for slot, source in zip(comms.SLOTS, order)})
    return True
