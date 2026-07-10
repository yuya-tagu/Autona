"""Pure data model for the commentary (casters) panel.

Qt-free on purpose: this module is imported by AutonaCommentaryWidget
(GUI) and by unit tests via tests/conftest.direct_import(). It owns the
slot-fixed state shape and the normalization / display-selection rules.

State shape (StateManager keypath "commentary"):
    commentary.{1..12}      slot-fixed casters, always all 12 present
        name        str     gamer tag
        team        str     team / affiliation
        twitter     str     handle without "@"
        role        str     free text ("" = no badge); ROLES is only the
                            completer candidate list, not a whitelist
        icon        str     one of ICONS stems ("" = no icon badge);
                            picked from the icon pulldown, rendered as
                            an SVG glyph on the overlay
        note        str     free comment shown on the overlay
        display     int     0 = hidden; 1 = shown on the overlay. The
                            overlay order is the slot order (the panel's
                            card order — cards are drag-reorderable and
                            a drop moves the data between slots), capped
                            at MAX_DISPLAY. Legacy order values (1..4
                            check-order) collapse to 1.
"""

SLOTS = [str(i) for i in range(1, 13)]

# The overlay renders at most this many casters at once.
MAX_DISPLAY = 4

# Completer candidates for the role text box. The field itself is free
# text stored verbatim in state; the overlay renders it as-is.
ROLES = ["実況", "解説", "選手", "主催", "MC", "スタッフ"]

# Icon pulldown options: stem -> Japanese label. The stem is stored in
# state ("icon"); assets/icons/{stem}.svg on the desktop, the overlay
# inlines the same glyph in layout/commentators/index.js.
ICONS = {"mic": "マイク", "megaphone": "拡声器", "phone": "電話"}

# Legacy shapes stored the icon as role text (exact label match).
_ICON_LABEL_TO_STEM = {label: stem for stem, label in ICONS.items()}

TEXT_FIELDS = ["name", "team", "twitter", "role", "note"]


def DefaultSlot():
    return {
        "name": "",
        "team": "",
        "twitter": "",
        "role": "",
        "icon": "",
        "note": "",
        "display": 0,
    }


def DefaultState():
    return {slot: DefaultSlot() for slot in SLOTS}


def NormalizeSlot(old):
    """Coerce one slot dict (new shape, legacy pre-rework shape, or
    garbage) into the current shape. The legacy custom_textbox carries
    over as note; the other legacy per-player fields (real_name, seed,
    country, state, character, mains, ...) are dropped."""
    slot = DefaultSlot()
    if not isinstance(old, dict):
        return slot
    for field in TEXT_FIELDS:
        value = old.get(field)
        if isinstance(value, str):
            slot[field] = value
    icon = old.get("icon")
    slot["icon"] = icon if icon in ICONS else ""
    if not slot["role"]:
        # Legacy multi-select shape: "roles" list -> text roles joined,
        # icon-label roles moved to the icon field (first one wins).
        legacy_roles = old.get("roles")
        if isinstance(legacy_roles, list):
            strs = [r for r in legacy_roles if isinstance(r, str) and r]
            slot["role"] = "・".join(
                r for r in strs if r not in _ICON_LABEL_TO_STEM)
            if not slot["icon"]:
                stems = [_ICON_LABEL_TO_STEM[r] for r in strs
                         if r in _ICON_LABEL_TO_STEM]
                if stems:
                    slot["icon"] = stems[0]
    # Legacy free-text shape: an icon label typed as the role.
    if slot["role"] in _ICON_LABEL_TO_STEM and not slot["icon"]:
        slot["icon"] = _ICON_LABEL_TO_STEM[slot["role"]]
        slot["role"] = ""
    if not slot["note"]:
        legacy_note = old.get("custom_textbox")
        if isinstance(legacy_note, str):
            slot["note"] = legacy_note
    slot["display"] = _coerce_display(old.get("display", 0))
    return slot


def _coerce_display(value):
    """Display flag: 1 = shown, 0 = hidden. Legacy values (bool True,
    check-order 1..4) collapse to 1."""
    try:
        return 1 if int(value) > 0 else 0
    except (TypeError, ValueError):
        return 0


def NormalizeState(state):
    """Full commentary.* normalization: exactly slots 1..12 (non-numeric
    and out-of-range keys are dropped), each slot normalized, and at most
    MAX_DISPLAY slots shown (in slot order; the rest are hidden)."""
    result = {}
    source = state if isinstance(state, dict) else {}
    for slot in SLOTS:
        result[slot] = NormalizeSlot(source.get(slot))
    shown = 0
    for slot in SLOTS:
        if result[slot]["display"]:
            shown += 1
            if shown > MAX_DISPLAY:
                result[slot]["display"] = 0
    return result


def DisplayedSlots(state):
    """Slot numbers (str) the overlay should render left to right: the
    slots with display > 0 and a non-empty name, in slot order, capped
    at MAX_DISPLAY. The overlay applies the same rule client-side as a
    second line of defense."""
    source = state if isinstance(state, dict) else {}
    candidates = []
    for slot in SLOTS:
        data = source.get(slot)
        if not isinstance(data, dict):
            continue
        if _coerce_display(data.get("display", 0)) \
                and str(data.get("name") or "").strip():
            candidates.append(slot)
    return candidates[:MAX_DISPLAY]
