"""The hotkeys' rules: what they are, how a shortcut is read, what may be
saved (ADR-005 S4ae).

Qt-free, so both shells and the web operations read the same rules. Until
S4ae they were split between the desktop's AutonaHotkeys (the defaults and
the parser that turns "Ctrl+Shift+O" into a Win32 registration) and
Helpers/AutonaHotkeyValidator (the duplicate check and the OS / OBS
warnings the settings window shows). The registration itself stays the
desktop's (AutonaHotkeys: RegisterHotKey is bound to the GUI thread's
message queue); this module only decides.

- DEFAULT_KEYS / ParseShortcut / CurrentKeys: the defaults, the parser
  AutonaHotkeys registers with, and the saved shortcuts merged over the
  defaults (what it registers). ParseShortcut answers as the old
  AutonaHotkeys._parse_shortcut did for a string (tests/test_hotkey_rules.py
  compares the two across the vocabulary); anything else is not read.
- NormalizeShortcut / FindDuplicates / Conflicts / WarningFor /
  CollisionSummary: the old validator's functions, comparing shortcuts as
  the keys they name ("Ctrl+Return" is "Ctrl+Enter", Qt's "Esc" is
  "Escape"). A warning is a code (WARNING_OS_RESERVED / WARNING_OBS_LIKELY
  / WARNING_NO_MODIFIER) now; the Qt settings window builds the old text
  from the first two, and the web page its own.
- Vocabulary / Canonical: what a client may compose (Ctrl / Alt / Shift and
  one key the parser reads) and the spelling a saved value gets.
- Describe / Update: the settings page's read and write (GET / PATCH
  /api/v1/settings/hotkeys). Update validates everything first and writes
  once (SettingsManager.SetMany), and nothing when nothing changes.
  `changes` is notified after a write, outside every lock; the Qt bridge
  turns it into hotkeys_changed, which re-registers on the GUI thread
  (AutonaHotkeys.ReloadHotkeys, connected by bootstrap_gui()).
- `applied`: whether this shell registers hotkeys with the OS at all. The
  desktop sets it (bootstrap_gui()); the headless shell has no hotkeys, and
  a save there only reaches settings.json.

Importing this module reads and writes nothing (agent-pitfalls #1).
"""
from collections import namedtuple

from loguru import logger

from .Observable import ObserverChannel
from .SettingsManager import SettingsManager, SettingsUnreadable

# Win32 RegisterHotKey modifiers (winuser.h).
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

# Virtual key codes of the named keys the parser reads (letters and digits
# are their own upper-case code points).
VK_MAP = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "tab": 0x09, "return": 0x0D, "enter": 0x0D,
    "escape": 0x1B, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}

# Use Ctrl+Shift+ prefix to avoid globally swallowing common app shortcuts
# (Ctrl+S=Save, Ctrl+O=Open, Ctrl+P=Print, Ctrl+R=Reload). Score buttons use
# top-row digits 1..4 -- F-keys are reserved on many keyboards for OS / OBS
# hotkeys and operators prefer to keep them free for streaming software.
# The order is the settings window's and the page's.
DEFAULT_KEYS = {
    "load_set": "Ctrl+Shift+O",
    "team1_score_up": "Ctrl+Shift+1",
    "team1_score_down": "Ctrl+Shift+2",
    "team2_score_up": "Ctrl+Shift+3",
    "team2_score_down": "Ctrl+Shift+4",
    "reset_scores": "Ctrl+Shift+R",
    "swap_teams": "Ctrl+Shift+S",
    "refresh_phase_group": "Ctrl+Shift+P",
    "apply_changes": "Ctrl+Shift+L",
}

# settings.json: hotkeys.<action> (a shortcut string) and the switch, in
# the same subtree.
ROOT = "hotkeys"
ENABLED_KEY = "hotkeys.hotkeys_enabled"
DEFAULT_ENABLED = True


def ParseShortcut(shortcut_str):
    """Parse 'Ctrl+F1' style string into (modifiers, vk) tuple, or None.

    Moved from AutonaHotkeys._parse_shortcut (ADR-005 S4ae): a string is
    read exactly as before. Logs a warning for a token it does not know. A
    value that is not a string (a hand-edited settings.json's null or 5) is
    None, not read: the old parser raised on it, from inside the
    registration at start-up."""
    return _Parse(shortcut_str, warn=True)


def _Parse(shortcut_str, warn):
    if not isinstance(shortcut_str, str):
        return None
    parts = shortcut_str.lower().split("+")
    modifiers = MOD_NOREPEAT
    vk = 0

    for part in parts:
        part = part.strip()
        if part == "ctrl":
            modifiers |= MOD_CONTROL
        elif part == "alt":
            modifiers |= MOD_ALT
        elif part == "shift":
            modifiers |= MOD_SHIFT
        elif part in VK_MAP:
            vk = VK_MAP[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            if warn:
                logger.warning(f"Unknown key in shortcut: {part}")
            return None

    if vk == 0:
        return None
    return (modifiers, vk)


# ── The old validator's (Helpers/AutonaHotkeyValidator, Design.md §6.4.2 /
# §8 T-17) ────────────────────────────────────────────────────────────────

def NormalizeShortcut(shortcut):
    """Canonical form for comparing: modifiers sorted alphabetically,
    lower-case, '+'-joined, the last token taken as the key. "" for an
    empty one. "Ctrl+Shift+F1" and "shift+ctrl+f1" compare equal."""
    if not shortcut:
        return ""
    parts = [p.strip().lower() for p in shortcut.split("+") if p.strip()]
    if not parts:
        return ""
    # Stable order: modifiers first (sorted), then key. Treat the last
    # token as the key so we don't sort it into the modifier slot.
    mods = sorted(p for p in parts[:-1])
    key = parts[-1]
    return "+".join([*mods, key])


# The spellings Qt's key recorder (QKeySequenceEdit) writes for keys the
# parser names otherwise. The parser does not read them -- a saved
# "Ctrl+Esc" registers nothing, and widening it would change what registers
# -- but they name the same key: the duplicate check and the warnings
# compare them as that key.
_QT_SPELLINGS = {"esc": "escape", "del": "delete", "ins": "insert",
                 "pgup": "pageup", "pgdown": "pagedown"}


def _KeyOf(shortcut):
    """What `shortcut` names, for comparing two: ("key", modifiers, vk) --
    the registration it makes, the Qt recorder's spellings read as the keys
    they name ("Ctrl+Return" and "Ctrl+Enter" are one key, as are "A+Ctrl"
    and "Ctrl+A", "Esc" and "Escape") -- or ("text", NormalizeShortcut) for
    one that names no key the parser knows. None for no shortcut ("", or
    not a string: the switch shares the subtree)."""
    if not isinstance(shortcut, str):
        return None
    normalized = NormalizeShortcut(shortcut)
    if not normalized:
        return None
    tokens = [_QT_SPELLINGS.get(token.strip().lower(), token)
              for token in shortcut.split("+")]
    parsed = _Parse("+".join(tokens), warn=False)
    if parsed is None:
        return ("text", normalized)
    return ("key",) + parsed


def FindDuplicates(hotkeys):
    """Group the hotkey assignments that take the same key.

    Returns a {shortcut: [action_key, ...]} dict containing only entries
    with 2+ actions, keyed by the first one's NormalizeShortcut. Two
    shortcuts are the same when they register the same key (modifiers and
    virtual key, _KeyOf: Win32 refuses the second registration of one), or,
    for ones the parser cannot read, when they normalize the same. Empty /
    non-string ones are ignored (a blank slot is not a collision)."""
    groups = {}
    for action_key, shortcut in (hotkeys or {}).items():
        key = _KeyOf(shortcut)
        if key is None:
            continue
        groups.setdefault(key, (NormalizeShortcut(shortcut), []))[1].append(
            action_key)
    return {canon: actions for canon, actions in groups.values()
            if len(actions) >= 2}


def Conflicts(hotkeys, action):
    """The other actions of `hotkeys` that take the key `action` takes
    (FindDuplicates), [] when none does."""
    for actions in FindDuplicates(hotkeys).values():
        if action in actions:
            return [other for other in actions if other != action]
    return []


# Shortcuts the OS or virtually any GUI app binds. Matching one of these
# usually means the user can never trigger the Autona action because the
# OS / focused app eats the keypress first. Listed in canonical form
# (modifiers sorted alphabetically, lower-case).
_OS_RESERVED = {
    # Edit / clipboard primitives
    "ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+y", "ctrl+a",
    # Common file / window operations
    "ctrl+s", "ctrl+o", "ctrl+n", "ctrl+w", "ctrl+p", "ctrl+f",
    "ctrl+r", "ctrl+t", "ctrl+q",
    "alt+f4", "alt+tab", "alt+shift+tab",
    "ctrl+tab", "ctrl+shift+tab",
    # Windows shell
    "ctrl+esc", "ctrl+shift+esc",
}

# OBS Studio's out-of-the-box defaults are mostly *unbound*, but operators
# routinely bind plain F-keys to recording / scene switches. Flag bare
# F1..F12 (no modifiers) as potential OBS-collision territory, plus the
# Studio-Mode / Replay-Buffer style chord.
_OBS_LIKELY = {
    *(f"f{n}" for n in range(1, 13)),
    "ctrl+shift+r",  # frequently bound to "Restart record"
}

# The same, as the keys they name (_KeyOf): "Ctrl+Escape" (the page's
# spelling) is "ctrl+esc", "A+Ctrl" is "ctrl+a".
_OS_RESERVED_KEYS = frozenset(_KeyOf(shortcut) for shortcut in _OS_RESERVED)
_OBS_LIKELY_KEYS = frozenset(_KeyOf(shortcut) for shortcut in _OBS_LIKELY)

# VK_F1..VK_F24: a bare function key is the usual hotkey, not typing.
_FUNCTION_KEYS = range(0x70, 0x88)

# The warnings a shortcut can get: saved all the same (the operator's
# environment may not use the clashing binding), only told.
WARNING_OS_RESERVED = "os_reserved"
WARNING_OBS_LIKELY = "obs_likely"
# A key with no Ctrl / Alt / Shift (A, Space, Enter, a digit; the function
# keys excepted): registered, it is taken from every app on this PC.
# Warned, not refused: saved settings have such keys, and refusing would
# break them.
WARNING_NO_MODIFIER = "no_modifier"


def WarningFor(shortcut):
    """The warning `shortcut` gets (WARNING_OS_RESERVED /
    WARNING_OBS_LIKELY for clashing with a well-known OS / OBS binding,
    WARNING_NO_MODIFIER for a key with no modifier), else None. Compared as
    the key it names (_KeyOf); a shortcut that names no key the parser
    knows is compared by its spelling, against the two lists only. The OS
    one wins, then the OBS one."""
    key = _KeyOf(shortcut)
    if key is None:
        return None
    if key[0] == "text":
        if key[1] in _OS_RESERVED:
            return WARNING_OS_RESERVED
        if key[1] in _OBS_LIKELY:
            return WARNING_OBS_LIKELY
        return None
    if key in _OS_RESERVED_KEYS:
        return WARNING_OS_RESERVED
    if key in _OBS_LIKELY_KEYS:
        return WARNING_OBS_LIKELY
    _, modifiers, vk = key
    if (not modifiers & (MOD_CONTROL | MOD_ALT | MOD_SHIFT)
            and vk not in _FUNCTION_KEYS):
        return WARNING_NO_MODIFIER
    return None


def CollisionSummary(action_keys):
    """Render a short comma-separated list for error dialogs."""
    return ", ".join(sorted(set(action_keys)))


# ── What a client may compose ────────────────────────────────────────────

MODIFIERS = ("Ctrl", "Alt", "Shift")
# The keys a shortcut may end in, spelled as a saved value spells them: the
# parser reads every one (VK_MAP, or a letter / digit). "Return" is read
# too, as "Enter".
KEYS = (
    tuple(chr(code) for code in range(ord("A"), ord("Z") + 1))
    + tuple(str(digit) for digit in range(10))
    + tuple(f"F{n}" for n in range(1, 13))
    + ("Tab", "Enter", "Escape", "Space", "Backspace", "Delete", "Insert",
       "Home", "End", "PageUp", "PageDown", "Up", "Down", "Left", "Right"))
_KEY_SPELLING = {key.lower(): key for key in KEYS}
_KEY_SPELLING["return"] = "Enter"


def Vocabulary():
    """{"modifiers": [...], "keys": [...]}: what the page composes from."""
    return {"modifiers": list(MODIFIERS), "keys": list(KEYS)}


def Canonical(shortcut):
    """`shortcut` as it is saved -- each modifier at most once, in Ctrl,
    Alt, Shift order, then exactly one key of the vocabulary -- or None
    when it is not one (an unknown or repeated token, no key, two keys).
    Case and spaces around the tokens do not matter."""
    if not isinstance(shortcut, str):
        return None
    tokens = [token.strip().lower() for token in shortcut.split("+")]
    modifiers = set()
    key = None
    for token in tokens:
        if token in ("ctrl", "alt", "shift"):
            if token in modifiers:
                return None
            modifiers.add(token)
        elif token in _KEY_SPELLING:
            if key is not None:
                return None
            key = _KEY_SPELLING[token]
        else:
            return None
    if key is None:
        return None
    return "+".join([modifier for modifier in MODIFIERS
                     if modifier.lower() in modifiers] + [key])


def Readable(shortcut):
    """Whether a saved value is one the registration reads: "" (no
    shortcut) or what the parser takes. Quiet: nothing is logged."""
    if not isinstance(shortcut, str):
        return False
    return shortcut == "" or _Parse(shortcut, warn=False) is not None


# ── The settings page's read and write ───────────────────────────────────

# Notified (no arguments) after Update wrote something, outside every lock.
# The Qt bridge turns it into hotkeys_changed (src/StateManagerBridge.py).
changes = ObserverChannel("hotkeys_changes")

# Whether this shell registers hotkeys with the OS (bootstrap_gui()).
applied = False

# Refusals: Update's answer when it wrote nothing for a reason.
INVALID = "invalid"
CONFLICT = "conflict"
NOT_READY = "not_ready"
Refusal = namedtuple("Refusal", ("code", "name", "detail"))


def _Enabled(value):
    # What AutonaHotkeys does with the stored value: anything but False is
    # on.
    return value != False


def CurrentKeys():
    """{action: shortcut} as the registration takes it: the saved value
    where there is one (whatever its type: the parser skips one that is not
    a string), else the default; the defaults alone when the saved subtree
    is not an object. The one merge of the saved shortcuts over the
    defaults: the desktop registers it (AutonaHotkeys.LoadUserHotkeys), and
    the page and the Qt settings window check a new shortcut against it --
    an action never saved still takes its default's key."""
    with SettingsManager.lock:
        stored = SettingsManager.Get(ROOT, {})
        if not isinstance(stored, dict):
            stored = {}
        keys = dict(DEFAULT_KEYS)
        keys.update((action, value) for action, value in stored.items()
                    if action in DEFAULT_KEYS)
    return keys


def Describe():
    """{"enabled", "keys": {action: {"value", "default", "valid",
    "warning"}}, "vocabulary", "applied"}. `value` is what is saved (the
    default when nothing is), None for a saved value that is not a string;
    `valid` whether the registration reads it ("" -- no shortcut -- is);
    `warning` a WarningFor code or None."""
    with SettingsManager.lock:
        enabled = _Enabled(SettingsManager.Get(ENABLED_KEY, DEFAULT_ENABLED))
        keys = CurrentKeys()
    described = {}
    for action, default in DEFAULT_KEYS.items():
        value = keys[action]
        if not isinstance(value, str):
            value = None
        described[action] = {
            "value": value,
            "default": default,
            "valid": Readable(value),
            "warning": WarningFor(value) if value else None,
        }
    return {"enabled": enabled, "keys": described,
            "vocabulary": Vocabulary(), "applied": applied}


def Update(enabled=None, keys=None):
    """Save the switch and / or some shortcuts ({action: shortcut}; "" for
    none), all or nothing. Returns (refusal, warnings, written):

    - refusal: None, or a Refusal -- NOT_READY when settings.json could not
      be read at launch and still cannot be opened (nothing it holds is
      known; when it can be now, it is read again first:
      SettingsManager.PrepareWrite), INVALID for a value that is not
      one (`name` the field), CONFLICT when a shortcut would be another
      action's too (`name` the action asked for, `detail` the others).
    - warnings: [{"action", "code"}] for the shortcuts asked for that clash
      with an OS / OBS binding; saved all the same.
    - written: whether anything was written (and `changes` notified).

    A shortcut is saved spelled as Canonical spells it. What equals the
    value in effect (the saved one, or the default when nothing is saved)
    is not written; nothing written, nothing notified."""
    if enabled is not None and not isinstance(enabled, bool):
        return Refusal(INVALID, "enabled", "must be true or false"), [], False
    if keys is None:
        keys = {}
    if not isinstance(keys, dict):
        return (Refusal(INVALID, "keys", "must be an object of shortcuts"),
                [], False)
    asked = {}
    for action, value in keys.items():
        if action not in DEFAULT_KEYS:
            return (Refusal(INVALID, action, "is not a hotkey action"),
                    [], False)
        if not isinstance(value, str):
            return Refusal(INVALID, action, "must be a string"), [], False
        if value.strip() == "":
            asked[action] = ""
            continue
        canonical = Canonical(value)
        if canonical is None:
            return (Refusal(INVALID, action,
                            "is not a shortcut (Ctrl / Alt / Shift and one "
                            "key of the vocabulary)"), [], False)
        asked[action] = canonical

    with SettingsManager.lock:
        # A launch that could not read settings.json reads it again first:
        # the conflicts and the comparisons below are with the shortcuts it
        # holds, not with the defaults that stood in for them.
        try:
            SettingsManager.PrepareWrite()
        except SettingsUnreadable:
            return (Refusal(NOT_READY, None,
                            "settings.json could not be read"), [], False)
        current = CurrentKeys()
        result = dict(current)
        result.update(asked)
        for group in FindDuplicates(result).values():
            mine = [action for action in group if action in asked]
            if mine:
                action = mine[0]
                others = [other for other in group if other != action]
                return (Refusal(CONFLICT, action, CollisionSummary(others)),
                        [], False)
        pairs = []
        if enabled is not None:
            stored = SettingsManager.Get(ENABLED_KEY, DEFAULT_ENABLED)
            if not (type(stored) is bool and stored == enabled):
                pairs.append((ENABLED_KEY, enabled))
        for action, value in asked.items():
            previous = current[action]
            if isinstance(previous, str) and previous == value:
                continue
            if (isinstance(previous, str) and value
                    and NormalizeShortcut(previous) == NormalizeShortcut(value)
                    and Readable(previous)):
                # The same shortcut spelled another way: the registration
                # is the same.
                continue
            pairs.append((f"{ROOT}.{action}", value))
        SettingsManager.SetMany(pairs)

    warnings = [{"action": action, "code": WarningFor(value)}
                for action, value in asked.items()
                if value and WarningFor(value)]
    if pairs:
        logger.info(
            "Hotkey settings saved: "
            + ", ".join(f"{key}={value!r}" for key, value in pairs))
        changes.notify()
    return None, warnings, bool(pairs)
