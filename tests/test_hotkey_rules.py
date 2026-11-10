"""Core's hotkey rules (src/core/HotkeyRules.py, ADR-005 S4ae).

The defaults, the parser the desktop registers with, the old validator's
checks and the settings page's read and write moved here from
AutonaHotkeys and Helpers/AutonaHotkeyValidator (deleted). Pinned:

- the parser answers as the old AutonaHotkeys._parse_shortcut did, across
  the vocabulary and the spellings Qt records (a copy of the old code
  below is the reference);
- the duplicate check and the OS / OBS warnings answer as the validator
  did; a warning is a code now, and the Qt settings window builds the same
  words from it;
- what a client may compose (the vocabulary), and the spelling a saved
  shortcut gets;
- Describe: a saved value the registration cannot read is `valid: false`;
- Update: all or nothing, a duplicate refused naming the other action, the
  same value not written (nor announced), nothing written while
  settings.json could not be read, the announcement outside the lock.

Settings are a private copy here, never saved to disk.
"""
import itertools
import json
import threading

import pytest

from src.core import HotkeyRules
from src.core.SettingsManager import SettingsManager

# ── the reference: the old code, copied as it was at 551c8b8b ────────────

_OLD_MOD_ALT = 0x0001
_OLD_MOD_CONTROL = 0x0002
_OLD_MOD_SHIFT = 0x0004
_OLD_MOD_NOREPEAT = 0x4000

_OLD_VK_MAP = {
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

_OLD_KEYS = {
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


def _old_parse_shortcut(shortcut_str):
    """AutonaHotkeys._parse_shortcut (the warning log left out)."""
    parts = shortcut_str.lower().split("+")
    modifiers = _OLD_MOD_NOREPEAT
    vk = 0

    for part in parts:
        part = part.strip()
        if part == "ctrl":
            modifiers |= _OLD_MOD_CONTROL
        elif part == "alt":
            modifiers |= _OLD_MOD_ALT
        elif part == "shift":
            modifiers |= _OLD_MOD_SHIFT
        elif part in _OLD_VK_MAP:
            vk = _OLD_VK_MAP[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            return None

    if vk == 0:
        return None
    return (modifiers, vk)


def _old_normalize_shortcut(shortcut):
    """Helpers/AutonaHotkeyValidator.normalize_shortcut."""
    if not shortcut:
        return ""
    parts = [p.strip().lower() for p in shortcut.split("+") if p.strip()]
    if not parts:
        return ""
    mods = sorted(p for p in parts[:-1])
    key = parts[-1]
    return "+".join([*mods, key])


def _old_find_app_duplicates(hotkeys):
    """Helpers/AutonaHotkeyValidator.find_app_duplicates."""
    by_shortcut = {}
    for action_key, shortcut in (hotkeys or {}).items():
        if not isinstance(shortcut, str):
            continue
        canon = _old_normalize_shortcut(shortcut)
        if not canon:
            continue
        by_shortcut.setdefault(canon, []).append(action_key)
    return {k: v for k, v in by_shortcut.items() if len(v) >= 2}


_OLD_OS_RESERVED = {
    "ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+y", "ctrl+a",
    "ctrl+s", "ctrl+o", "ctrl+n", "ctrl+w", "ctrl+p", "ctrl+f",
    "ctrl+r", "ctrl+t", "ctrl+q",
    "alt+f4", "alt+tab", "alt+shift+tab",
    "ctrl+tab", "ctrl+shift+tab",
    "ctrl+esc", "ctrl+shift+esc",
}
_OLD_OBS_LIKELY = {
    *(f"f{n}" for n in range(1, 13)),
    "ctrl+shift+r",
}


def _old_os_warning_for(shortcut):
    """Helpers/AutonaHotkeyValidator.os_warning_for: the window's words."""
    canon = _old_normalize_shortcut(shortcut)
    if not canon:
        return None
    if canon in _OLD_OS_RESERVED:
        return (
            f"'{shortcut}' は OS や一般的なアプリ "
            "(コピー・保存・ウィンドウ操作など) で広く予約されています。"
            "他アプリにフォーカスがあると Autona に届きません。"
        )
    if canon in _OLD_OBS_LIKELY:
        return (
            f"'{shortcut}' は OBS Studio で録画開始 / シーン切替などに "
            "割り当てられがちです。配信中の意図しない動作を避けるため、"
            "OBS 側のホットキー設定と重複しないか確認してください。"
        )
    return None


# ── the strings compared ─────────────────────────────────────────────────

# Every key token: the vocabulary, every name the old table read, the
# letters and digits, what Qt's QKeySequenceEdit records for some keys
# (Esc, Del, Ins, PgUp, PgDown, Return) and punctuation it can record.
_KEY_TOKENS = sorted(
    set(HotkeyRules.KEYS)
    | set(_OLD_VK_MAP)
    | {"Esc", "Del", "Ins", "PgUp", "PgDown", "Return", "Print", "Pause",
       ";", ",", ".", "/", "-", "=", "`", "[", "]", "Ä", "é", "11", "F13",
       "F0", "Win", "Meta", "Num+"})
_MODIFIER_SPELLINGS = ("Ctrl", "Alt", "Shift")


def _cases(token):
    return {token, token.lower(), token.upper()}


def _shortcuts():
    """Each key with each set of modifiers, in each case, modifiers first
    and last; and some that are not shortcuts at all."""
    found = set()
    for key in _KEY_TOKENS:
        for count in range(4):
            for modifiers in itertools.combinations(_MODIFIER_SPELLINGS, count):
                for spelling in _cases(key):
                    found.add("+".join([*modifiers, spelling]))
                    found.add("+".join([spelling, *modifiers]).lower())
                    found.add(" + ".join([*modifiers, spelling]))
    found |= {"", "+", "++", "Ctrl+", "Ctrl++", "Ctrl", "Ctrl+Shift",
              "Ctrl+Alt+Shift", "A+B", "Ctrl+A+B", "Ctrl+Ctrl+A",
              "Shift+Shift", " ", "Ctrl+ ", "Ctrl+Shift+F1+",
              "ctrl+shift+o", "CTRL+SHIFT+O", "Alt+F4", "Ctrl+Esc"}
    return sorted(found)


SHORTCUTS = _shortcuts()


def test_the_comparison_covers_every_kind_of_answer():
    # Not vacuous: the grid has shortcuts the parser reads, ones it does not,
    # and ones with no key.
    answers = [_old_parse_shortcut(s) for s in SHORTCUTS]
    assert len(SHORTCUTS) > 3000
    assert sum(a is None for a in answers) > 500
    assert sum(a is not None for a in answers) > 1000


def test_the_parser_answers_as_the_old_one_did():
    different = {s: (HotkeyRules.ParseShortcut(s), _old_parse_shortcut(s))
                 for s in SHORTCUTS
                 if HotkeyRules.ParseShortcut(s) != _old_parse_shortcut(s)}
    assert different == {}


def test_the_constants_and_the_defaults_are_the_old_ones():
    assert HotkeyRules.VK_MAP == _OLD_VK_MAP
    assert (HotkeyRules.MOD_ALT, HotkeyRules.MOD_CONTROL,
            HotkeyRules.MOD_SHIFT, HotkeyRules.MOD_NOREPEAT) == (
        _OLD_MOD_ALT, _OLD_MOD_CONTROL, _OLD_MOD_SHIFT, _OLD_MOD_NOREPEAT)
    # The order too: the window and the page list them in it.
    assert list(HotkeyRules.DEFAULT_KEYS.items()) == list(_OLD_KEYS.items())


def test_the_desktop_registers_with_cores_defaults_and_parser():
    # What AutonaHotkeys registers is what the page validates against: its
    # defaults are a copy of core's (not the same dict: nothing may change
    # core's table through it), and SetupHotkeys parses with core's parser.
    import inspect

    from src import AutonaHotkeys as hotkeysModule

    assert hotkeysModule.AutonaHotkeys.keys == HotkeyRules.DEFAULT_KEYS
    assert hotkeysModule.AutonaHotkeys.keys is not HotkeyRules.DEFAULT_KEYS
    source = inspect.getsource(hotkeysModule.AutonaHotkeys.SetupHotkeys)
    assert "HotkeyRules.ParseShortcut(" in source
    assert not hasattr(hotkeysModule, "_parse_shortcut")
    assert not hasattr(hotkeysModule, "_VK_MAP")


@pytest.mark.parametrize("value", [None, 5, 1.5, True, ["Ctrl", "A"],
                                   {"key": "A"}, b"Ctrl+A"])
def test_a_value_that_is_not_a_string_is_not_read(value):
    # A hand-edited settings.json's null or 5: the old parser raised on it
    # (`.lower()`), inside the registration at start-up.
    assert HotkeyRules.ParseShortcut(value) is None
    assert HotkeyRules.Readable(value) is False
    assert HotkeyRules.WarningFor(value) is None


def test_an_unknown_token_is_logged_by_the_parser_but_not_by_readable():
    from loguru import logger

    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="DEBUG")
    try:
        assert HotkeyRules.Readable("Ctrl+Esc") is False
        quiet = list(messages)
        assert HotkeyRules.ParseShortcut("Ctrl+Esc") is None
    finally:
        logger.remove(sink)

    assert quiet == []
    assert any("Unknown key in shortcut: esc" in m for m in messages)


# ── the old validator's ──────────────────────────────────────────────────

def test_normalize_answers_as_the_validator_did():
    different = {s: (HotkeyRules.NormalizeShortcut(s), _old_normalize_shortcut(s))
                 for s in SHORTCUTS
                 if HotkeyRules.NormalizeShortcut(s) != _old_normalize_shortcut(s)}
    assert different == {}
    assert HotkeyRules.NormalizeShortcut(None) == ""


@pytest.mark.parametrize("hotkeys", [
    {},
    None,
    dict(_OLD_KEYS),
    {"load_set": "Ctrl+Shift+S", "swap_teams": "shift+ctrl+s"},
    {"a": "F1", "b": "f1", "c": "F1", "d": "F2", "e": "f2"},
    # A blank slot is not a collision; the switch is not a shortcut.
    {"a": "", "b": "", "hotkeys_enabled": True, "c": None, "d": 5},
])
def test_duplicates_answer_as_the_validator_did(hotkeys):
    # Where the spellings name their keys the way the validator compared
    # them (case, spaces, the order of the modifiers).
    assert HotkeyRules.FindDuplicates(hotkeys) == _old_find_app_duplicates(hotkeys)


def test_a_duplicate_is_found_whatever_the_spelling():
    assert HotkeyRules.FindDuplicates(
        {"load_set": "Ctrl+Shift+S", "swap_teams": "shift+ctrl+s",
         "reset_scores": ""}) == {"ctrl+shift+s": ["load_set", "swap_teams"]}
    assert HotkeyRules.CollisionSummary(["b", "a", "b"]) == "a, b"


# The Qt recorder's spellings and the keys they name: the reference for the
# comparison (the parser itself does not read the first ones).
_QT_SPELLINGS = {"esc": "escape", "del": "delete", "ins": "insert",
                 "pgup": "pageup", "pgdown": "pagedown"}


def _key_named(shortcut):
    """The registration `shortcut` names: the old parser's answer, over the
    Qt recorder's spellings read as the keys they name. None when it names
    no key the parser knows."""
    return _old_parse_shortcut("+".join(
        _QT_SPELLINGS.get(token.strip().lower(), token)
        for token in shortcut.split("+")))


@pytest.mark.parametrize("first, second", [
    # Qt records the main Enter key as "Return"; the page saves "Enter".
    # The same VK (0x0D): Win32 refuses the second registration.
    ("Ctrl+Return", "Ctrl+Enter"),
    ("Ctrl+Esc", "Ctrl+Escape"),
    ("Ctrl+Shift+Esc", "Ctrl+Shift+Escape"),
    ("Ctrl+PgUp", "Ctrl+PageUp"),
    ("Alt+PgDown", "Alt+PageDown"),
    ("Ctrl+Del", "Ctrl+Delete"),
    ("Ctrl+Ins", "Ctrl+Insert"),
    ("Esc", "escape"),
    # The parser reads the tokens in any order.
    ("Ctrl+A", "A+Ctrl"),
    ("Ctrl+Shift+F1", "f1 + shift + ctrl"),
    # Names no key the parser knows: by the spelling, as before.
    ("Ctrl+;", "ctrl + ;"),
])
def test_a_duplicate_is_the_same_key_however_it_is_spelled(first, second):
    assert HotkeyRules.FindDuplicates({"a": first, "b": second}) == {
        HotkeyRules.NormalizeShortcut(first): ["a", "b"]}
    assert HotkeyRules.Conflicts({"a": first, "b": second, "c": "F9"},
                                 "b") == ["a"]


@pytest.mark.parametrize("first, second", [
    ("Ctrl+Return", "Ctrl+Space"),
    ("Ctrl+A", "Ctrl+Alt+A"),
    ("Ctrl+Esc", "Esc"),
    ("Ctrl+;", "Ctrl+,"),
    ("Ctrl+Escape", "Ctrl+Escape+"),
])
def test_different_keys_are_not_duplicates(first, second):
    assert HotkeyRules.FindDuplicates({"a": first, "b": second}) == {}
    assert HotkeyRules.Conflicts({"a": first, "b": second}, "a") == []


def test_every_spelling_of_a_key_is_a_duplicate_of_the_saved_one():
    # Each shortcut of the grid the parser reads against the spelling a save
    # gives the same key: the case, the spaces, the order and "Return".
    checked = 0
    for shortcut in SHORTCUTS:
        saved = HotkeyRules.Canonical(shortcut)
        if saved is None or _old_parse_shortcut(shortcut) is None:
            continue
        assert HotkeyRules.Conflicts({"a": shortcut, "b": saved}, "b") == \
            ["a"], shortcut
        checked += 1
    assert checked > 1000


_OLD_OS_KEYS = {_key_named(shortcut) for shortcut in _OLD_OS_RESERVED}
_OLD_OBS_KEYS = {_key_named(shortcut) for shortcut in _OLD_OBS_LIKELY}


def _expected_warning(shortcut):
    """The rule: the validator's two lists, as the keys they name; a key
    with no Ctrl / Alt / Shift but a function key is "no_modifier". What
    names no key the parser knows: the lists by spelling, as before."""
    key = _key_named(shortcut)
    if key is None:
        old = _old_os_warning_for(shortcut)
        if old is None:
            return None
        return "os_reserved" if "OS や一般的なアプリ" in old else "obs_likely"
    if key in _OLD_OS_KEYS:
        return "os_reserved"
    if key in _OLD_OBS_KEYS:
        return "obs_likely"
    modifiers, vk = key
    if (not modifiers & (_OLD_MOD_CONTROL | _OLD_MOD_ALT | _OLD_MOD_SHIFT)
            and not 0x70 <= vk <= 0x87):
        return "no_modifier"
    return None


def test_the_warning_codes_follow_the_validators_lists_by_key():
    different = {shortcut: (HotkeyRules.WarningFor(shortcut),
                            _expected_warning(shortcut))
                 for shortcut in SHORTCUTS
                 if HotkeyRules.WarningFor(shortcut)
                 != _expected_warning(shortcut)}
    assert different == {}


def test_every_warning_the_validator_gave_is_given_still():
    for shortcut in SHORTCUTS:
        old = _old_os_warning_for(shortcut)
        if old is None:
            continue
        assert HotkeyRules.WarningFor(shortcut) == (
            "os_reserved" if "OS や一般的なアプリ" in old else "obs_likely"), \
            shortcut


@pytest.mark.parametrize("shortcut, code", [
    ("Ctrl+S", "os_reserved"),
    ("Ctrl+A", "os_reserved"),
    # The page's spelling of the Windows shell's "ctrl+esc" entries.
    ("Ctrl+Escape", "os_reserved"),
    ("Ctrl+Shift+Escape", "os_reserved"),
    ("Shift+Ctrl+Esc", "os_reserved"),
    ("A+Ctrl", "os_reserved"),
    ("F5", "obs_likely"),
    ("F12", "obs_likely"),
    ("Ctrl+Shift+R", "obs_likely"),
    # No modifier: taken from every app on this PC.
    ("A", "no_modifier"),
    ("1", "no_modifier"),
    ("Space", "no_modifier"),
    ("Enter", "no_modifier"),
    ("Return", "no_modifier"),
    ("Tab", "no_modifier"),
    ("Escape", "no_modifier"),
    ("Ctrl+B", None),
    ("Shift+A", None),
    ("Alt+Space", None),
    ("Ctrl+Shift+O", None),
    ("", None),
    ("Ctrl+;", None),
    # Names no key the parser knows (a trailing "+"): the lists by the
    # spelling, as the validator read it.
    ("Ctrl+Esc+", "os_reserved"),
    ("F5+", "obs_likely"),
])
def test_the_warning_of(shortcut, code):
    assert HotkeyRules.WarningFor(shortcut) == code


def test_a_function_key_alone_is_not_a_key_with_no_modifier(monkeypatch):
    # Hidden today by the OBS list (bare F1-F12 warn of OBS first; the
    # parser reads no F13-F24): without it, a function key alone is still
    # the usual hotkey, not typing.
    monkeypatch.setattr(HotkeyRules, "_OBS_LIKELY_KEYS", frozenset())

    assert HotkeyRules.WarningFor("F5") is None
    assert HotkeyRules.WarningFor("F12") is None
    assert HotkeyRules.WarningFor("A") == "no_modifier"


def _old_words(kind, shortcut):
    """The validator's text of each kind, for `shortcut`."""
    sample = {"os_reserved": "ctrl+c", "obs_likely": "f1"}[kind]
    return _old_os_warning_for(sample).replace(f"'{sample}'", f"'{shortcut}'")


def test_the_settings_window_shows_the_words_it_showed():
    # The rules are core's; the window builds its old text from the code,
    # and has none for "no_modifier" (no dialog: it goes in S4af).
    from src.Settings.SettingsWidget import _HotkeyWarningText

    seen = set()
    for shortcut in SHORTCUTS + ["Ctrl+S", "F5", "Alt+F4", "Ctrl+Shift+R"]:
        code = HotkeyRules.WarningFor(shortcut)
        seen.add(code)
        expected = (_old_words(code, shortcut)
                    if code in ("os_reserved", "obs_likely") else None)
        assert _HotkeyWarningText(shortcut, code) == expected, shortcut
    assert seen == {None, "os_reserved", "obs_likely", "no_modifier"}
    assert _HotkeyWarningText("A", "a_code_from_later") is None


def test_the_validator_module_is_gone():
    # No forwarding layer was left behind.
    import importlib.util

    assert importlib.util.find_spec("src.Helpers.AutonaHotkeyValidator") is None


# ── what a client may compose ────────────────────────────────────────────

def test_every_composable_shortcut_is_one_the_parser_reads():
    vocabulary = HotkeyRules.Vocabulary()
    assert vocabulary["modifiers"] == ["Ctrl", "Alt", "Shift"]
    assert vocabulary["keys"] == list(HotkeyRules.KEYS)
    assert len(set(vocabulary["keys"])) == len(vocabulary["keys"])
    for key in vocabulary["keys"]:
        for count in range(4):
            for modifiers in itertools.combinations(
                    vocabulary["modifiers"], count):
                shortcut = "+".join([*modifiers, key])
                assert HotkeyRules.Canonical(shortcut) == shortcut
                assert HotkeyRules.ParseShortcut(shortcut) is not None
                assert HotkeyRules.ParseShortcut(shortcut) == \
                    _old_parse_shortcut(shortcut)
                assert HotkeyRules.Readable(shortcut) is True


@pytest.mark.parametrize("given, saved", [
    ("shift+ctrl+a", "Ctrl+Shift+A"),
    (" alt + CTRL + p ", "Ctrl+Alt+P"),
    ("ctrl+return", "Ctrl+Enter"),
    ("f5", "F5"),
    ("ctrl+pageup", "Ctrl+PageUp"),
    ("Shift+escape", "Shift+Escape"),
    ("a", "A"),
])
def test_a_shortcut_is_saved_in_one_spelling(given, saved):
    assert HotkeyRules.Canonical(given) == saved


@pytest.mark.parametrize("given", [
    "", " ", "Ctrl", "Ctrl+Shift", "Ctrl+Ctrl+A", "A+B", "Ctrl+A+F1",
    "Win+A", "Ctrl+Esc", "Ctrl+PgUp", "Del", "Ctrl+;", "Ctrl+", "Ctrl++A",
    "F13", None, 5, ["Ctrl", "A"],
])
def test_what_is_not_a_composed_shortcut_has_no_saved_spelling(given):
    assert HotkeyRules.Canonical(given) is None


# ── Describe / Update ────────────────────────────────────────────────────

@pytest.fixture
def settings(monkeypatch):
    """A private settings dict, never saved; the saves counted."""
    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    saves = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: saves.append(json.dumps(
                            SettingsManager.settings, sort_keys=True)))
    return saves


@pytest.fixture
def announced():
    calls = []

    def told():
        calls.append(threading.current_thread())

    HotkeyRules.changes.subscribe(told)
    try:
        yield calls
    finally:
        HotkeyRules.changes.unsubscribe(told)


def _described(action):
    return HotkeyRules.Describe()["keys"][action]


def test_describe_lists_every_action_with_its_default(settings):
    described = HotkeyRules.Describe()

    assert set(described) == {"enabled", "keys", "vocabulary", "applied"}
    assert described["enabled"] is True
    assert list(described["keys"]) == list(_OLD_KEYS)
    for action, default in _OLD_KEYS.items():
        assert described["keys"][action] == {
            "value": default, "default": default, "valid": True,
            "warning": HotkeyRules.WarningFor(default)}
    assert described["keys"]["reset_scores"]["warning"] == "obs_likely"
    assert described["vocabulary"] == HotkeyRules.Vocabulary()
    assert described["applied"] is HotkeyRules.applied


def test_a_saved_value_the_registration_cannot_read_is_not_valid(settings):
    # Qt's key recorder writes "Esc" and "PgUp", which the parser has never
    # read: the desktop silently registered nothing. The page is told.
    SettingsManager.settings["hotkeys"] = {
        "load_set": "Ctrl+Esc", "swap_teams": 5, "reset_scores": "",
        "apply_changes": "Ctrl+PgUp", "team1_score_up": "ctrl+shift+1",
        "hotkeys_enabled": False}

    assert _described("load_set") == {
        "value": "Ctrl+Esc", "default": "Ctrl+Shift+O", "valid": False,
        "warning": "os_reserved"}
    assert _described("swap_teams") == {
        "value": None, "default": "Ctrl+Shift+S", "valid": False,
        "warning": None}
    # No shortcut is a valid answer.
    assert _described("reset_scores") == {
        "value": "", "default": "Ctrl+Shift+R", "valid": True,
        "warning": None}
    assert _described("apply_changes")["valid"] is False
    assert _described("team1_score_up")["valid"] is True
    assert _described("team1_score_up")["value"] == "ctrl+shift+1"
    assert HotkeyRules.Describe()["enabled"] is False


@pytest.mark.parametrize("stored, shown", [
    (False, False), (True, True), (None, True), ("no", True), (0, False)])
def test_the_switch_is_read_as_the_desktop_reads_it(settings, stored, shown):
    # AutonaHotkeys: anything but False is on (0 == False in Python).
    SettingsManager.settings["hotkeys"] = {"hotkeys_enabled": stored}
    assert HotkeyRules.Describe()["enabled"] is shown


@pytest.mark.parametrize("enabled, keys, name", [
    ("yes", None, "enabled"),
    (1, None, "enabled"),
    (None, ["Ctrl+A"], "keys"),
    (None, {"nope": "Ctrl+A"}, "nope"),
    (None, {"hotkeys_enabled": "Ctrl+A"}, "hotkeys_enabled"),
    (None, {"load_set": 5}, "load_set"),
    (None, {"load_set": None}, "load_set"),
    (None, {"load_set": "Ctrl+Esc"}, "load_set"),
    (None, {"load_set": "Ctrl+Ctrl+O"}, "load_set"),
    (None, {"load_set": "Ctrl+Alt+O", "swap_teams": "PgUp"}, "swap_teams"),
    (True, {"load_set": "Ctrl+Alt+O", "swap_teams": "A+B"}, "swap_teams"),
])
def test_a_value_that_is_not_one_writes_nothing(settings, announced, enabled,
                                                keys, name):
    refusal, warnings, written = HotkeyRules.Update(enabled=enabled, keys=keys)

    assert refusal.code == HotkeyRules.INVALID
    assert refusal.name == name
    assert (warnings, written) == ([], False)
    assert SettingsManager.settings == {}
    assert settings == [] and announced == []


def test_a_shortcut_another_action_has_is_refused_naming_it(settings, announced):
    refusal, warnings, written = HotkeyRules.Update(
        keys={"load_set": "shift+ctrl+s"})

    assert refusal == HotkeyRules.Refusal(
        HotkeyRules.CONFLICT, "load_set", "swap_teams")
    assert (warnings, written) == ([], False)
    assert settings == [] and announced == []


def test_every_other_action_with_it_is_named(settings):
    SettingsManager.settings["hotkeys"] = {"reset_scores": "Ctrl+Alt+X",
                                           "swap_teams": "Ctrl+Alt+X"}
    refusal, _, _ = HotkeyRules.Update(keys={"load_set": "Ctrl+Alt+X"})
    assert refusal.detail == "reset_scores, swap_teams"


def test_two_actions_can_trade_shortcuts_in_one_request(settings, announced):
    refusal, _, written = HotkeyRules.Update(
        keys={"load_set": "Ctrl+Shift+S", "swap_teams": "Ctrl+Shift+O"})

    assert (refusal, written) == (None, True)
    assert SettingsManager.settings["hotkeys"] == {
        "load_set": "Ctrl+Shift+S", "swap_teams": "Ctrl+Shift+O"}
    # One write of settings.json, one announcement.
    assert len(settings) == 1 and len(announced) == 1


def test_a_duplicate_among_untouched_actions_does_not_block_others(settings):
    # As in the window: only a shortcut asked for is checked. A switch
    # change, or another action's, goes through.
    SettingsManager.settings["hotkeys"] = {"swap_teams": "Ctrl+Shift+O"}

    assert HotkeyRules.Update(enabled=False)[0] is None
    assert HotkeyRules.Update(keys={"reset_scores": "Ctrl+Alt+R"})[0] is None
    # Asking for one of the two again is asking for a duplicate.
    refusal, _, _ = HotkeyRules.Update(keys={"load_set": "Ctrl+Shift+O"})
    assert refusal == HotkeyRules.Refusal(
        HotkeyRules.CONFLICT, "load_set", "swap_teams")


def test_no_shortcut_is_never_a_duplicate(settings):
    refusal, warnings, written = HotkeyRules.Update(
        keys={"load_set": "", "swap_teams": " "})

    assert (refusal, warnings, written) == (None, [], True)
    assert SettingsManager.settings["hotkeys"] == {
        "load_set": "", "swap_teams": ""}


def test_the_value_in_effect_is_not_written_again(settings, announced):
    # Nothing saved: the defaults are in effect.
    for update in ({"keys": {"load_set": "Ctrl+Shift+O"}},
                   {"keys": {"load_set": "shift+ctrl+o"}},
                   {"enabled": True},
                   {"enabled": True, "keys": {}},
                   {}):
        assert HotkeyRules.Update(**update) == (None, [], False), update

    assert SettingsManager.settings == {}
    assert settings == [] and announced == []


def test_the_same_shortcut_spelled_otherwise_is_rewritten_only_if_unreadable(
        settings):
    # A saved spelling the parser reads registers the same: kept. One it
    # cannot read (a trailing "+") is replaced by the one asked for.
    SettingsManager.settings["hotkeys"] = {"load_set": "ctrl+alt+p",
                                           "swap_teams": "CTRL+ESCAPE+"}
    assert HotkeyRules.Readable("CTRL+ESCAPE+") is False

    _, _, written = HotkeyRules.Update(keys={"load_set": "Alt+Ctrl+P"})
    assert written is False
    _, _, written = HotkeyRules.Update(keys={"swap_teams": "Ctrl+Escape"})
    assert written is True
    assert SettingsManager.settings["hotkeys"] == {
        "load_set": "ctrl+alt+p", "swap_teams": "Ctrl+Escape"}


def test_a_switch_saved_as_another_type_is_replaced(settings):
    SettingsManager.settings["hotkeys"] = {"hotkeys_enabled": 1}
    assert HotkeyRules.Update(enabled=True)[2] is True
    assert SettingsManager.settings["hotkeys"]["hotkeys_enabled"] is True


def test_a_save_writes_once_spelled_canonically_and_announces_once(
        settings, announced):
    from loguru import logger

    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        refusal, warnings, written = HotkeyRules.Update(
            enabled=False, keys={"load_set": "alt+ctrl+o", "swap_teams": "f5"})
    finally:
        logger.remove(sink)

    assert (refusal, written) == (None, True)
    assert warnings == [{"action": "swap_teams", "code": "obs_likely"}]
    assert SettingsManager.settings == {"hotkeys": {
        "hotkeys_enabled": False, "load_set": "Ctrl+Alt+O",
        "swap_teams": "F5"}}
    assert len(settings) == 1 and len(announced) == 1
    assert any("hotkeys.load_set='Ctrl+Alt+O'" in m for m in messages)


def test_a_clashing_shortcut_is_saved_with_its_warning(settings):
    refusal, warnings, written = HotkeyRules.Update(
        keys={"load_set": "Ctrl+S"})

    assert (refusal, written) == (None, True)
    assert warnings == [{"action": "load_set", "code": "os_reserved"}]
    assert SettingsManager.settings["hotkeys"]["load_set"] == "Ctrl+S"


def test_a_key_with_no_modifier_is_saved_with_its_warning(settings):
    # Warned, not refused: settings saved before have such keys.
    refusal, warnings, written = HotkeyRules.Update(keys={"load_set": "a"})

    assert (refusal, written) == (None, True)
    assert warnings == [{"action": "load_set", "code": "no_modifier"}]
    assert SettingsManager.settings["hotkeys"]["load_set"] == "A"
    assert _described("load_set")["warning"] == "no_modifier"
    assert _described("load_set")["valid"] is True


@pytest.mark.parametrize("saved, asked", [
    # What Qt's recorder saved, and the page's spelling of the same key.
    ("Ctrl+Return", "Ctrl+Enter"),
    ("Ctrl+Esc", "Ctrl+Escape"),
    ("Ctrl+PgUp", "Ctrl+PageUp"),
    ("Ctrl+PgDown", "Ctrl+PageDown"),
    ("Ctrl+Del", "Ctrl+Delete"),
    ("Ctrl+Ins", "Ctrl+Insert"),
    ("A+Ctrl+Alt", "Ctrl+Alt+A"),
])
def test_another_spelling_of_a_saved_key_is_a_duplicate(settings, announced,
                                                        saved, asked):
    SettingsManager.settings["hotkeys"] = {"swap_teams": saved}

    refusal, warnings, written = HotkeyRules.Update(keys={"load_set": asked})

    assert refusal == HotkeyRules.Refusal(
        HotkeyRules.CONFLICT, "load_set", "swap_teams")
    assert (warnings, written) == ([], False)
    assert SettingsManager.settings["hotkeys"] == {"swap_teams": saved}
    assert settings == [] and announced == []


@pytest.mark.parametrize("stored, keys", [
    ({"load_set": None, "swap_teams": 5, "reset_scores": ["x"],
      "hotkeys_enabled": False, "nope": "Ctrl+N"},
     dict(_OLD_KEYS, load_set=None, swap_teams=5, reset_scores=["x"])),
    (["Ctrl+A"], dict(_OLD_KEYS)),
    ("Ctrl+A", dict(_OLD_KEYS)),
    (None, dict(_OLD_KEYS)),
])
def test_the_current_keys_are_the_saved_ones_over_the_defaults(
        settings, stored, keys):
    # The one merge: what the desktop registers and what a new shortcut is
    # checked against. A value that is not a string stays (the parser skips
    # it); a subtree that is not an object is the defaults.
    SettingsManager.settings["hotkeys"] = stored

    current = HotkeyRules.CurrentKeys()

    assert current == keys
    assert list(current) == list(_OLD_KEYS)
    current["load_set"] = "changed"
    assert HotkeyRules.CurrentKeys()["load_set"] == keys["load_set"]
    assert HotkeyRules.DEFAULT_KEYS == _OLD_KEYS


def test_a_save_that_fails_leaves_the_settings_as_they_were(
        settings, announced, monkeypatch):
    # Memory must not keep what settings.json does not: the page would show
    # it, and the next save of anything would write it.
    SettingsManager.settings["hotkeys"] = {"swap_teams": "Ctrl+Alt+S"}
    before = json.loads(json.dumps(SettingsManager.settings))

    def fail():
        raise OSError("disk full")

    monkeypatch.setattr(SettingsManager, "SaveSettings", fail)

    with pytest.raises(OSError):
        HotkeyRules.Update(enabled=False, keys={"load_set": "Ctrl+Alt+O",
                                                "swap_teams": "Ctrl+Alt+T"})

    assert SettingsManager.settings == before
    assert announced == []
    assert HotkeyRules.Describe()["keys"]["load_set"]["value"] == "Ctrl+Shift+O"


def test_nothing_is_written_while_settings_json_could_not_be_read(
        settings, announced, monkeypatch):
    # Writing would replace the unread file with this one key (and lose the
    # access token with the rest).
    monkeypatch.setattr(SettingsManager, "loadFailed", True)

    refusal, warnings, written = HotkeyRules.Update(
        enabled=False, keys={"load_set": "Ctrl+Alt+O"})

    assert refusal.code == HotkeyRules.NOT_READY
    assert (warnings, written) == ([], False)
    assert SettingsManager.settings == {}
    assert settings == [] and announced == []


def test_the_announcement_comes_after_the_lock_is_released(settings):
    # The bridge re-emits it, and the desktop re-registers on the GUI
    # thread: never with the settings lock held.
    held = []

    def told():
        result = []

        def probe():
            # Another thread gets the lock only if this one does not hold it.
            got = SettingsManager.lock.acquire(timeout=2)
            if got:
                SettingsManager.lock.release()
            result.append(got)

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join()
        held.append(not result[0])

    HotkeyRules.changes.subscribe(told)
    try:
        assert HotkeyRules.Update(keys={"load_set": "Ctrl+Alt+O"})[2] is True
    finally:
        HotkeyRules.changes.unsubscribe(told)

    assert held == [False]


# ── the Qt settings window's save (until S4af) ───────────────────────────
#
# SettingsWidget's hotkey rows as AutonaSettingsWindow builds them, a key
# recorded into one, through the real keySequenceChanged handler
# (_makeHotkeyValidator). The window's dialogs are recorded, not shown.

class _Window:
    """The rows' widget, its capture fields by action, and what happened."""


@pytest.fixture
def window(autonaApp, settings, monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    from src.Settings.SettingsWidget import HotkeyCaptureEdit, SettingsWidget

    dialogs = []
    monkeypatch.setattr(QMessageBox, "critical",
                        lambda parent, title, text: dialogs.append(
                            ("critical", text)))
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda parent, title, text: dialogs.append(
                            ("warning", text)))
    reloads = []
    widget = SettingsWidget("hotkeys", [
        (action, action, "hotkey", default, lambda: reloads.append(1))
        for action, default in HotkeyRules.DEFAULT_KEYS.items()])
    result = _Window()
    result.edits = dict(zip(HotkeyRules.DEFAULT_KEYS,
                            widget.findChildren(HotkeyCaptureEdit)))
    result.dialogs, result.reloads, result.saves = dialogs, reloads, settings
    try:
        yield result
    finally:
        widget.deleteLater()
        autonaApp.settle()


def _record(window, action, shortcut):
    from qtpy.QtGui import QKeySequence

    window.edits[action].setKeySequence(QKeySequence(shortcut))
    return window.edits[action].keySequence().toString()


def test_the_window_saves_a_recorded_key(window):
    assert _record(window, "load_set", "Ctrl+Alt+O") == "Ctrl+Alt+O"

    assert SettingsManager.settings["hotkeys"] == {"load_set": "Ctrl+Alt+O"}
    assert window.dialogs == [] and window.reloads == [1]


def test_the_window_refuses_a_never_saved_actions_default(window):
    # A fresh install saves no hotkey: swap_teams takes its default
    # (Ctrl+Shift+S), and a second action may not take it too.
    shown = _record(window, "load_set", "Ctrl+Shift+S")

    assert [kind for kind, _ in window.dialogs] == ["critical"]
    assert "swap_teams" in window.dialogs[0][1]
    assert shown == "Ctrl+Shift+O"
    assert "hotkeys" not in SettingsManager.settings
    assert window.saves == [] and window.reloads == []


def test_the_window_refuses_the_same_key_spelled_otherwise(window):
    # Qt records the main Enter key as "Return", the page saves "Enter":
    # the same key (one Win32 registration).
    SettingsManager.settings["hotkeys"] = {"swap_teams": "Ctrl+Return"}

    shown = _record(window, "load_set", "Ctrl+Enter")

    assert [kind for kind, _ in window.dialogs] == ["critical"]
    assert "swap_teams" in window.dialogs[0][1]
    assert shown == "Ctrl+Shift+O"
    assert SettingsManager.settings["hotkeys"] == {"swap_teams": "Ctrl+Return"}


def test_the_window_warns_with_its_old_words_and_saves(window):
    _record(window, "load_set", "Ctrl+S")

    assert window.dialogs == [("warning", _old_words("os_reserved", "Ctrl+S"))]
    assert SettingsManager.settings["hotkeys"]["load_set"] == "Ctrl+S"
    assert window.reloads == [1]


def test_the_window_saves_a_key_with_no_modifier_without_a_dialog(window):
    # "no_modifier" has no words here (the page tells it); no dialog, no
    # error.
    assert HotkeyRules.WarningFor("A") == "no_modifier"

    _record(window, "load_set", "A")

    assert window.dialogs == []
    assert SettingsManager.settings["hotkeys"]["load_set"] == "A"
    assert window.reloads == [1]


def test_the_windows_port_stops_at_the_last_port(autonaApp, settings):
    # 1-65535, as the page (core's AppSettings.PORT_RANGE). A port saved out
    # of range is shown at the limit, and not written back.
    from qtpy.QtWidgets import QSpinBox

    from src.core.AppSettings import PORT_RANGE
    from src.Settings.SettingsWidget import SettingsWidget

    SettingsManager.settings["general"] = {"webserver_port": 70000}
    widget = SettingsWidget("general", [
        ("Port", "webserver_port", "spinbox", 5107)])
    try:
        spin = widget.findChild(QSpinBox)
        assert (spin.minimum(), spin.maximum()) == PORT_RANGE
        assert spin.value() == 65535
        assert settings == []
        assert SettingsManager.settings["general"]["webserver_port"] == 70000
    finally:
        widget.deleteLater()
        autonaApp.settle()
