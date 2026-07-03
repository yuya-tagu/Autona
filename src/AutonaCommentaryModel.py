"""Pure data model for the commentary (casters) panel.

Qt-free on purpose: this module is imported by AutonaCommentaryWidget
(GUI) and by unit tests via tests/conftest.direct_import(). It owns the
slot-fixed state shape and the normalization / display-selection rules.

State shape (StateManager keypath "commentary"):
    commentary.{1..8}       slot-fixed casters, always all 8 present
        name        str     gamer tag
        team        str     team / affiliation
        real_name   str
        twitter     str     handle without "@"
        role        str     one of ROLES ("" = no prefix)
        note        str     free comment shown on the overlay
        display     int     0 = hidden; 1..MAX_DISPLAY = position on the
                            overlay, numbered in the order the operator
                            checked the slots (1 = leftmost)
"""

SLOTS = [str(i) for i in range(1, 9)]

# The overlay renders at most this many casters at once.
MAX_DISPLAY = 4

# Role prefixes shown before the caster name. Stored verbatim in state so
# the overlay renders them as-is.
ROLES = ["", "実況", "解説", "選手", "主催"]

TEXT_FIELDS = ["name", "team", "real_name", "twitter", "role", "note"]


def DefaultSlot():
    return {
        "name": "",
        "team": "",
        "real_name": "",
        "twitter": "",
        "role": "",
        "note": "",
        "display": 0,
    }


def DefaultState():
    return {slot: DefaultSlot() for slot in SLOTS}


def NormalizeSlot(old):
    """Coerce one slot dict (new shape, legacy pre-rework shape, or
    garbage) into the current shape. The legacy custom_textbox carries
    over as note; the other legacy per-player fields (seed, country,
    state, character, mains, ...) are dropped."""
    slot = DefaultSlot()
    if not isinstance(old, dict):
        return slot
    for field in TEXT_FIELDS:
        value = old.get(field)
        if isinstance(value, str):
            slot[field] = value
    if slot["role"] not in ROLES:
        slot["role"] = ""
    if not slot["note"]:
        legacy_note = old.get("custom_textbox")
        if isinstance(legacy_note, str):
            slot["note"] = legacy_note
    slot["display"] = _coerce_display(old.get("display", 0))
    return slot


def _coerce_display(value):
    """int display order >= 0. Legacy bool True maps to 1 (NormalizeState
    then breaks ties by slot number, reproducing the old ascending
    behavior on migration)."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def NormalizeState(state):
    """Full commentary.* normalization: exactly slots 1..8 (non-numeric
    and out-of-range keys are dropped), each slot normalized, and display
    orders renumbered to a contiguous 1..MAX_DISPLAY (ordered by stored
    order, ties by slot number; slots beyond MAX_DISPLAY are hidden)."""
    result = {}
    source = state if isinstance(state, dict) else {}
    for slot in SLOTS:
        result[slot] = NormalizeSlot(source.get(slot))
    shown = sorted(
        (slot for slot in SLOTS if result[slot]["display"] > 0),
        key=lambda slot: (result[slot]["display"], int(slot)))
    for slot in SLOTS:
        result[slot]["display"] = 0
    for order, slot in enumerate(shown[:MAX_DISPLAY], start=1):
        result[slot]["display"] = order
    return result


def DisplayedSlots(state):
    """Slot numbers (str) the overlay should render left to right: the
    slots with display > 0 and a non-empty name, sorted by display order
    (ties by slot number), capped at MAX_DISPLAY. The overlay applies the
    same rule client-side as a second line of defense."""
    source = state if isinstance(state, dict) else {}
    candidates = []
    for slot in SLOTS:
        data = source.get(slot)
        if not isinstance(data, dict):
            continue
        order = _coerce_display(data.get("display", 0))
        if order > 0 and str(data.get("name") or "").strip():
            candidates.append((order, int(slot), slot))
    candidates.sort()
    return [slot for _order, _n, slot in candidates[:MAX_DISPLAY]]
