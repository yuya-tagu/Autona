"""The settings window's settings, for the settings page (ADR-005 S4ae).

settings.json holds them; what reads each one is all over core and the
shells (the bad-word filter, the set follow, the player DB, the state's
exports, the web server, the locale...). This is the one table of the ones
the settings window shows -- the general switches and default colours, the
web server's port and LAN access, the three languages -- with each one's
key and default, and the page's read and write of them:

- Describe(): an allow-list of exactly those (never the access token, nor
  anything else settings.json holds), each with the value in effect, and
  whether a restart is still to apply what is saved (network, language).
- UpdateGeneral / UpdateNetwork / UpdateLanguage: validate every field
  first, then write once (SettingsManager.SetMany); nothing when nothing
  changes. When the launch could not read settings.json
  (SettingsManager.loadFailed), it is read again first and the changes are
  decided against what it holds (SettingsManager.PrepareWrite: adopted, or a
  corrupt one moved aside); nothing at all is written while it still cannot
  be opened (NOT_READY).

The defaults here are the readers' (tests/test_app_settings.py pins that
every reader passes the same one). The settings window keeps its own until
S4af replaces it, and one differs: it shows disable_autoupdate unticked
when nothing is saved, though the set follow reads it as on.

Hotkeys are core's HotkeyRules. Qt-free; what is the shell's comes in as a
callable (the language list, what the web server listens on). Importing
this module reads and writes nothing (agent-pitfalls #1).
"""
import os
import re
from collections import namedtuple

from loguru import logger

from .SettingsManager import (DEFAULT_WEBSERVER_PORT, SettingsManager,
                              SettingsUnreadable)

GENERAL = "general"
NETWORK = "network"
LANGUAGE = "language"

# How a value is validated and shown.
BOOL = "bool"
COLOR = "color"
PORT = "port"
LAN = "lan"
LANGUAGE_CODE = "language_code"

Setting = namedtuple("Setting", ("group", "name", "key", "default", "kind"))

# The page's names: a general setting keeps its settings.json name (the
# precedent of /display-options and /thumbnail); network and language use
# the API's camelCase names. The order is the settings window's.
SETTINGS = (
    Setting(GENERAL, "profanity_filter", "general.profanity_filter",
            True, BOOL),
    # Read as on when nothing is saved (SetFollowService.Polling); the
    # desktop saves it on at every launch (SetFollowService.
    # TurnOffPolling; at every board build until ADR-005 S4ac-1).
    Setting(GENERAL, "disable_autoupdate", "general.disable_autoupdate",
            True, BOOL),
    Setting(GENERAL, "disable_scoreupdate", "general.disable_scoreupdate",
            False, BOOL),
    Setting(GENERAL, "disable_export", "general.disable_export",
            False, BOOL),
    Setting(GENERAL, "disable_overwrite", "general.disable_overwrite",
            False, BOOL),
    # general.disable_individual_game_tracker went with the per-game
    # tracker (ADR-005 S4ac-4): a saved value is never read.
    Setting(GENERAL, "team_1_default_color", "general.team_1_default_color",
            "#fe3636", COLOR),
    Setting(GENERAL, "team_2_default_color", "general.team_2_default_color",
            "#2e89ff", COLOR),
    Setting(NETWORK, "port", "general.webserver_port",
            DEFAULT_WEBSERVER_PORT, PORT),
    # None: not decided yet. The desktop asks on its next start; the server
    # listens on this PC only meanwhile.
    Setting(NETWORK, "listenLan", "general.webserver_listen_lan", None, LAN),
    # "default": the system's language (the program), the program's (the
    # other two).
    Setting(LANGUAGE, "program", "program_language", "default",
            LANGUAGE_CODE),
    Setting(LANGUAGE, "gameAsset", "game_asset_language", "default",
            LANGUAGE_CODE),
    Setting(LANGUAGE, "fgTerm", "fg_term_language", "default",
            LANGUAGE_CODE),
)

PORT_RANGE = (1, 65535)
DEFAULT_LANGUAGE = "default"
_COLOR = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?")

# Refusals: an update's answer when it wrote nothing for a reason.
INVALID = "invalid"
NOT_READY = "not_ready"
Refusal = namedtuple("Refusal", ("code", "name", "detail"))


def Group(group):
    return tuple(setting for setting in SETTINGS if setting.group == group)


def _Same(a, b):
    # True == 1 and 1 == 1.0 in Python; a saved value of another type is
    # not the same value, and is replaced.
    return type(a) is type(b) and a == b


def _Language(value):
    # LoadLocale's reading: empty or "default" is the default.
    return value if isinstance(value, str) and value else DEFAULT_LANGUAGE


class AppSettings:
    """Built once by bootstrap_core() (`instance`)."""

    instance = None

    def __init__(self, languages=None, listening=None):
        """`languages()`: {code: [native name, English name]} (the locale's
        table, AutonaLocaleHelper.languages). `listening()`: (port,
        listen_lan) the web server is bound with, or None while none runs
        (WebServer.Listening). Both read when used."""
        self._languages = languages or (lambda: {})
        self._listening = listening or (lambda: None)
        # What this run started with: a change to these is applied by a
        # restart. Read here, in bootstrap_core(), before either shell reads
        # them (the locale, the web server).
        with SettingsManager.lock:
            self._launch = {
                setting.key: self._Raw(setting)
                for setting in Group(NETWORK) + Group(LANGUAGE)}

    # -- reading ----------------------------------------------------------

    @staticmethod
    def _Raw(setting):
        """What is saved, the default when nothing is (as the readers'
        SettingsManager.Get(key, default)). A language as LoadLocale takes
        it."""
        value = SettingsManager.Get(setting.key, setting.default)
        if setting.kind == LANGUAGE_CODE:
            return _Language(value)
        return value

    @classmethod
    def _Shown(cls, setting):
        """The value shown: what is saved if it is one of this setting's
        kind, else the default. A port out of range is shown as it is (the
        page says so; the server failed to listen on it)."""
        value = cls._Raw(setting)
        if setting.kind == BOOL:
            return value if isinstance(value, bool) else setting.default
        if setting.kind == COLOR:
            return value if isinstance(value, str) else setting.default
        if setting.kind == PORT:
            return (value if isinstance(value, int)
                    and not isinstance(value, bool) else setting.default)
        if setting.kind == LAN:
            return None if value is None else bool(value)
        return value

    def _Choices(self):
        try:
            languages = self._languages() or {}
        except Exception:
            logger.exception("Could not read the language list")
            languages = {}
        choices = []
        for code, names in languages.items():
            names = list(names) if isinstance(names, (list, tuple)) else []
            choices.append({
                "code": code,
                "name": names[0] if len(names) > 0 else code,
                "englishName": names[1] if len(names) > 1 else code,
            })
        return choices

    def _Bound(self):
        try:
            return self._listening()
        except Exception:
            logger.exception("Could not read what the web server listens on")
            return None

    def _NetworkPending(self, port, lan, bound):
        """Whether what is saved differs from what is in effect: the bound
        server's (`bound`), or what this run started with while none runs.
        LAN access compares as the server takes it (None is off)."""
        if bound is None:
            bound = (self._launch["general.webserver_port"],
                     self._launch["general.webserver_listen_lan"])
        return not _Same(port, bound[0]) or bool(lan) != bool(bound[1])

    def Describe(self):
        """{"general": {...}, "network": {port, listenLan, listening: {port,
        lan} | null, restartPending}, "language": {program, gameAsset,
        fgTerm, choices: [{code, name, englishName}], restartPending},
        "loadFailed", "movedAside"}. Only what SETTINGS lists, and whether
        settings.json could not be read (SettingsManager.loadFailed: the
        values shown are the defaults; a save reads it again first, and is
        refused while it still cannot be opened). False again once a write
        has read it again or moved a corrupt one aside. `movedAside`: the
        file name (not the path) a corrupt settings.json was moved aside to
        in this run, at launch or at a write; null when none was."""
        with SettingsManager.lock:
            loadFailed = bool(SettingsManager.loadFailed)
            movedAside = (os.path.basename(SettingsManager.movedAside)
                          if SettingsManager.movedAside else None)
            general = {setting.name: self._Shown(setting)
                       for setting in Group(GENERAL)}
            network = {setting.name: self._Shown(setting)
                       for setting in Group(NETWORK)}
            portRaw = self._Raw(Group(NETWORK)[0])
            lanRaw = self._Raw(Group(NETWORK)[1])
            language = {setting.name: self._Shown(setting)
                        for setting in Group(LANGUAGE)}
            languagePending = any(
                self._Raw(setting) != self._launch[setting.key]
                for setting in Group(LANGUAGE))
        bound = self._Bound()
        network["listening"] = (None if bound is None else
                                {"port": bound[0], "lan": bool(bound[1])})
        network["restartPending"] = self._NetworkPending(
            portRaw, lanRaw, bound)
        language["choices"] = self._Choices()
        language["restartPending"] = languagePending
        return {"general": general, "network": network, "language": language,
                "loadFailed": loadFailed, "movedAside": movedAside}

    # -- writing ----------------------------------------------------------

    def _Validate(self, group, fields):
        """(values, refusal): `fields` ({name: value}) checked against the
        group's settings, each value as it is saved."""
        settings = {setting.name: setting for setting in Group(group)}
        if not isinstance(fields, dict) or not fields:
            return None, Refusal(INVALID, None, "at least one setting is "
                                 f"required: {', '.join(settings)}")
        values = {}
        codes = None
        for name, value in fields.items():
            setting = settings.get(name)
            if setting is None:
                return None, Refusal(
                    INVALID, name,
                    f"is not a {group} setting ({', '.join(settings)})")
            if setting.kind == BOOL or setting.kind == LAN:
                if not isinstance(value, bool):
                    return None, Refusal(INVALID, name,
                                         "must be true or false")
            elif setting.kind == COLOR:
                if not isinstance(value, str) or not _COLOR.fullmatch(value):
                    return None, Refusal(INVALID, name,
                                         "must be a colour (#rgb / #rrggbb)")
                digits = value[1:]
                if len(digits) == 3:
                    digits = "".join(digit * 2 for digit in digits)
                value = "#" + digits.lower()
            elif setting.kind == PORT:
                if (isinstance(value, bool) or not isinstance(value, int)
                        or not PORT_RANGE[0] <= value <= PORT_RANGE[1]):
                    return None, Refusal(
                        INVALID, name,
                        f"must be an integer {PORT_RANGE[0]}-{PORT_RANGE[1]}")
            elif setting.kind == LANGUAGE_CODE:
                if codes is None:
                    codes = {choice["code"] for choice in self._Choices()}
                if not isinstance(value, str) or (
                        value != DEFAULT_LANGUAGE and value not in codes):
                    return None, Refusal(
                        INVALID, name,
                        "must be \"default\" or a language of the list")
            values[setting.key] = (setting, value)
        return values, None

    def _Update(self, group, fields):
        """Returns (refusal, written)."""
        values, refusal = self._Validate(group, fields)
        if refusal is not None:
            return refusal, False
        with SettingsManager.lock:
            # A launch that could not read settings.json reads it again
            # first: the comparisons below are with what it holds, not with
            # the defaults that stood in for it.
            try:
                SettingsManager.PrepareWrite()
            except SettingsUnreadable:
                return Refusal(NOT_READY, None,
                               "settings.json could not be read"), False
            pairs = []
            for key, (setting, value) in values.items():
                if setting.kind == LAN:
                    # Against what is saved, not the effect: an undecided
                    # (None) LAN access set to off is decided now.
                    current = SettingsManager.Get(key, None)
                elif setting.kind == LANGUAGE_CODE:
                    # None and "" are "default" too (LoadLocale).
                    current = _Language(
                        SettingsManager.Get(key, setting.default))
                else:
                    current = SettingsManager.Get(key, setting.default)
                if not _Same(current, value):
                    pairs.append((key, value))
            SettingsManager.SetMany(pairs)
        if pairs:
            logger.info(
                f"Settings saved ({group}): "
                + ", ".join(f"{key}={value!r}" for key, value in pairs))
        return None, bool(pairs)

    def UpdateGeneral(self, fields):
        """Some general settings ({name: value}). (refusal, written)."""
        return self._Update(GENERAL, fields)

    def UpdateNetwork(self, fields):
        """The port and / or LAN access, applied by a restart. (refusal,
        written)."""
        return self._Update(NETWORK, fields)

    def UpdateLanguage(self, fields):
        """Some of the three languages, applied by a restart. (refusal,
        written)."""
        return self._Update(LANGUAGE, fields)
