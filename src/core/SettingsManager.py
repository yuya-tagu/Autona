import datetime
import os
import tempfile
import threading
import time
import orjson
from loguru import logger
from .DictHelper import *

# Default port for the built-in web server (OBS browser sources, Web UI,
# external control API). Deliberately NOT 5000: that is Flask's own default and
# the port the upstream TournamentStreamHelper family listens on, so on a
# streaming PC running several tools at once the collision is real. 5107 is
# unassigned by IANA and has no common consumer use.
# Keep in sync with web/src/env.js (dev fallback).
DEFAULT_WEBSERVER_PORT = 5107

# Relative to the working directory, as it always was.
SETTINGS_PATH = "./user_data/settings.json"

# SetMany's mark for "the key was not there" (a value may be None).
_ABSENT = object()

# A UTF-8 byte order mark: what Notepad before Windows 10 1903 and
# PowerShell 5's `Set-Content -Encoding UTF8` put before a file they save.
# orjson refuses it, so a hand-edited settings.json read as unreadable (and
# was moved aside at the first save); it is skipped when read. Saves never
# write one.
_UTF8_BOM = b"\xef\xbb\xbf"


def _WithoutBom(raw):
    return raw[len(_UTF8_BOM):] if raw.startswith(_UTF8_BOM) else raw


class SettingsUnreadable(OSError):
    """settings.json could not be read at launch and still cannot be opened
    (or moved aside), so a write would replace what it holds unread. Raised
    by SetMany() and PrepareWrite(); nothing was changed, in memory or on
    disk. An OSError: what stopped the write is the file system.

    PrepareWrite() also raises it when it read the file again (adopted it,
    or moved a corrupt one aside) but could not save the result: the caller
    refuses as it does for an unopenable file, and the next write saves."""


class SettingsManager:
    settings = {}
    _initialized = False
    # True when settings.json exists but could not be read at launch -- not
    # even after Initialize()'s retries -- so `settings` is an empty stand-in
    # rather than what the user saved. Lets a reader tell "not set yet" from
    # "unknown" (WebAccess.Token() does not issue a new access token over one
    # it could not read).
    #
    # It can turn False later in the run: every write while it is True reads
    # the file again first (_Settle) and adopts what it holds, or moves a
    # corrupt one aside. A reader that kept a decision made while it was True
    # asks again (WebAccess does not keep the token it could not read).
    loadFailed = False
    # Where settings.json was moved aside to when it was unusable (read, but
    # not JSON or not an object), at launch or at a later write; None when it
    # was not. For the settings page to tell (GET /api/v1/settings).
    movedAside = None
    # While loadFailed and the file still cannot be opened: what Set() /
    # Unset() changed in memory only, in order, as (key, value) -- value
    # _ABSENT for an Unset. Put back over what the file holds when it can be
    # read (_Settle), then saved; dropped when it is moved aside or gone
    # (memory, which is saved then, holds them already).
    _unsaved = []
    # The waits (seconds) before each further read at launch of a file that
    # could not be opened (an antivirus scan or a sync tool holding it):
    # about a second in all, before anything reads a setting.
    LAUNCH_RETRY_WAITS = (0.1, 0.3, 0.6)
    # Set() is called from the GUI thread and from Flask threads (e.g. Figma
    # pairing code issue/regenerate). Re-entrant because Set -> SaveSettings
    # both take it. Creating a lock has no side effect at import time.
    lock = threading.RLock()

    def Initialize():
        """Create ./user_data/settings.json if missing and load it.

        Used to run at import time; it is now called explicitly from
        src/bootstrap.py so that importing this module has no side effect.
        Idempotent: later calls are a no-op, so an already-loaded (and
        possibly modified) settings dict is never silently reloaded.
        """
        if SettingsManager._initialized:
            return
        SettingsManager._initialized = True

        # The old import-time tail assumed ./user_data already existed and
        # raised on a first run in a clean directory. Creating it here makes
        # the first launch work without relying on another module having
        # created the directory first.
        os.makedirs("./user_data", exist_ok=True)

        if not os.path.isfile(SETTINGS_PATH):
            SettingsManager.SaveSettings()

        SettingsManager.LoadSettings()
        SettingsManager._SettleAtLaunch()

    def _SettleAtLaunch():
        """When LoadSettings() could not read settings.json: what a write
        would do first (PrepareWrite), done now, before anything reads a
        setting. The port, the languages, the hotkeys, the battle format and
        the rest are read once at launch; a file adopted at a later write
        would be saved but not in effect until the next launch.

        A file that cannot be opened is read again after each of
        LAUNCH_RETRY_WAITS (about a second in all). A corrupt one is moved
        aside at once (not waited for: it would read the same) and the
        settings start from the defaults -- the backup loses nothing, and
        they are right before anything reads them. Still unopenable after
        that: `loadFailed` stays True, and every write tries again."""
        waits = list(SettingsManager.LAUNCH_RETRY_WAITS)
        while True:
            try:
                SettingsManager.PrepareWrite()
                return
            except SettingsUnreadable as error:
                if SettingsManager.loadFailed and waits:
                    time.sleep(waits.pop(0))
                    continue
                if SettingsManager.loadFailed:
                    logger.warning(
                        f"Settings - {error}. This run uses the defaults; a "
                        f"setting changed is in effect for this run only "
                        f"until the file can be read (each change tries "
                        f"again)")
                else:
                    logger.warning(f"Settings - {error}; the next change "
                                   f"saves them")
                return

    def SaveSettings():
        with SettingsManager.lock:
            path = SETTINGS_PATH
            data = orjson.dumps(SettingsManager.settings, option=orjson.OPT_NON_STR_KEYS)
            # Write a sibling temp file and os.replace() it, so a crash or a
            # failure mid-write never leaves a truncated settings.json.
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="settings.", suffix=".tmp")
            try:
                with os.fdopen(fd, 'wb') as file:
                    file.write(data)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise

    def LoadSettings():
        """Read settings.json. When it cannot be opened or does not hold a
        JSON object, `settings` is {} and `loadFailed` True, and the file is
        left as it is: Initialize() then reads it again (_SettleAtLaunch),
        and so does every write while it still cannot be opened (_Settle)."""
        with SettingsManager.lock:
            try:
                with open(SETTINGS_PATH, 'rb') as file:
                    settings = orjson.loads(_WithoutBom(file.read()))
                problem = None if isinstance(settings, dict) else (
                    "it does not hold a JSON object")
            except Exception as error:
                settings = None
                problem = error
            if problem is None:
                SettingsManager.settings = settings
                SettingsManager.loadFailed = False
                return
            SettingsManager.settings = {}
            SettingsManager.loadFailed = True
            logger.warning(
                f"Could not read {SETTINGS_PATH} ({problem}). The settings "
                f"are the defaults until it is read again; the file is left "
                f"as it is")

    def _Settle():
        """Before a write while `loadFailed` (caller holds the lock): read
        settings.json again, so the write never replaces what it holds
        unread.

        - A JSON object: adopted (`settings` replaced), the launch's failure
          was passing (an antivirus or sync tool holding the file); what
          Set() / Unset() changed in memory meanwhile is put back over it,
          in order (_unsaved).
        - Missing: nothing to lose; the write creates it.
        - Read but corrupt (not JSON, not an object): moved aside to
          settings.json.unreadable-<time>.bak (_BackupPath, kept in
          `movedAside`) and logged; `settings` stays the defaults ({}) with
          the changes made meanwhile.
        - Cannot be opened, or moved aside: SettingsUnreadable, and nothing
          is changed (`loadFailed` stays True; the next write tries again).

        `loadFailed` is False after anything but the last. True when
        settings.json is to be saved now even if the caller then writes
        nothing: it was moved aside (the defaults go in its place), or
        changes made meanwhile are in memory and not in the file. False
        (a no-op) when the launch read the file."""
        if not SettingsManager.loadFailed:
            return False
        try:
            with open(SETTINGS_PATH, 'rb') as file:
                raw = file.read()
        except FileNotFoundError:
            SettingsManager.loadFailed = False
            logger.warning(
                f"{SETTINGS_PATH}, unreadable at launch, is gone; saving the "
                f"settings from the defaults")
            return SettingsManager._DropUnsaved()
        except OSError as error:
            raise SettingsUnreadable(
                f"{SETTINGS_PATH} could not be read at launch and cannot be "
                f"opened now ({error})") from error
        try:
            data = orjson.loads(_WithoutBom(raw))
            problem = None if isinstance(data, dict) else (
                "it does not hold a JSON object")
        except ValueError as error:   # orjson.JSONDecodeError
            data = None
            problem = error
        if problem is None:
            SettingsManager.settings = data
            SettingsManager.loadFailed = False
            logger.info(
                f"Read {SETTINGS_PATH} again: it could not be read at "
                f"launch, and now could")
            return SettingsManager._PutUnsavedBack()
        backup = SettingsManager._BackupPath()
        try:
            os.replace(SETTINGS_PATH, backup)
        except OSError as error:
            raise SettingsUnreadable(
                f"{SETTINGS_PATH} is unusable ({problem}) and could not be "
                f"moved aside ({error})") from error
        SettingsManager.loadFailed = False
        SettingsManager.movedAside = backup
        SettingsManager._DropUnsaved()
        logger.warning(
            f"{SETTINGS_PATH} is unusable ({problem}); moved it to {backup}. "
            f"The settings saved from now on start from the defaults")
        return True

    def _PutUnsavedBack():
        """The changes Set() / Unset() made while the file could not be
        opened, applied again in order over what it holds (just adopted):
        what this run changed wins over what is saved. A later change of a
        key replaced what was recorded for it and under it (_Remember), so
        the order left is the one that matters: a parent's change before or
        after its child's. True when there was any."""
        unsaved, SettingsManager._unsaved = SettingsManager._unsaved, []
        for key, value in unsaved:
            try:
                SettingsManager._Apply(key, value)
            except Exception as error:
                # The file holds something other than an object on the way
                # down to the key (hand-edited): what it holds stays.
                logger.warning(
                    f"Settings - {key}, changed while {SETTINGS_PATH} could "
                    f"not be read, could not be put back over it ({error})")
        if unsaved:
            logger.info(
                f"Settings - put back {len(unsaved)} change(s) made while "
                f"{SETTINGS_PATH} could not be read")
        return bool(unsaved)

    def _DropUnsaved():
        """Forget the changes made while the file could not be opened:
        memory, saved now, holds them. True when there was any."""
        unsaved, SettingsManager._unsaved = SettingsManager._unsaved, []
        return bool(unsaved)

    def _Remember(key, value):
        """Record a change made in memory only (_unsaved). It replaces what
        was recorded for `key` and under it -- it decides that whole subtree,
        as it did in memory -- and goes after a change of a parent recorded
        earlier, which stays before it (the child is applied over the parent
        when put back, as it was in memory)."""
        under = key + "."
        SettingsManager._unsaved = [
            (k, v) for k, v in SettingsManager._unsaved
            if k != key and not k.startswith(under)]
        SettingsManager._unsaved.append((key, value))

    def _BackupPath():
        """``settings.json.unreadable-<time>.bak``, never an existing file
        (a second corrupt file must not replace the first's copy).
        StatePersistence._BackupPath's naming."""
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        base = f"{SETTINGS_PATH}.unreadable-{stamp}"
        candidate = f"{base}.bak"
        suffix = 2
        while os.path.exists(candidate):
            candidate = f"{base}-{suffix}.bak"
            suffix += 1
        return candidate

    def PrepareWrite():
        """What a write does first while settings.json could not be read at
        launch (_Settle), for a caller that decides what to write from what
        is saved (AppSettings and HotkeyRules compare with it; the stream
        channels, the thumbnail's and the result's config and the Figma
        pairing code change it), so that it reads the file and not the empty
        stand-in. Call it first under the lock and hold the lock to the
        write (tests/test_settings_manager_load_failed.py pins every block
        that writes under the lock). Raises SettingsUnreadable while the
        file still cannot be opened (nothing changed); a no-op when the
        launch read it.

        When it moved a corrupt file aside, or put back changes made while
        it could not be opened, settings.json is saved at once, even if the
        caller then writes nothing. A failure of that save raises
        SettingsUnreadable too (what is read stays in memory; the next write
        saves it)."""
        with SettingsManager.lock:
            if not SettingsManager._Settle():
                return
            try:
                SettingsManager.SaveSettings()
            except OSError as error:
                raise SettingsUnreadable(
                    f"{SETTINGS_PATH} was read again but could not be saved "
                    f"({error})") from error

    def Set(key: str, value):
        """Set `key` and save. While settings.json could not be read at
        launch and still cannot be opened, the change is made in memory
        only -- in effect for this run, not saved -- and logged; it is put
        back over the file when a later write can read it (_Settle). Its
        callers (SetFollowService.TurnOffPolling, the battle format and the
        rest; the Qt settings window until ADR-005 S4af) do not expect an
        error. A caller that decides what to write from what is saved calls
        PrepareWrite() first instead (strict)."""
        SettingsManager._Change(key, value)

    def Unset(key: str):
        """Remove `key` and save; nothing is saved when it is not there
        (and no empty parent is made on the way down). While settings.json
        cannot be opened: as Set()."""
        SettingsManager._Change(key, _ABSENT)

    def _Change(key, value):
        with SettingsManager.lock:
            try:
                save = SettingsManager._Settle()
            except SettingsUnreadable as error:
                SettingsManager._Apply(key, value)
                SettingsManager._Remember(key, value)
                logger.warning(
                    f"Settings - {key} is in effect for this run only and was "
                    f"not saved: {error}. It is saved when the file can be "
                    f"read")
                return
            if SettingsManager._Apply(key, value) or save:
                SettingsManager.SaveSettings()

    def _Apply(key, value):
        """deep_set `key` to `value`, or remove it for _ABSENT. True when
        `settings` may have changed (a removal of what is not there does
        not)."""
        if value is not _ABSENT:
            deep_set(SettingsManager.settings, key, value)
            return True
        parent, _, leaf = key.rpartition(".")
        node = (deep_get(SettingsManager.settings, parent) if parent
                else SettingsManager.settings)
        if isinstance(node, dict) and leaf in node:
            del node[leaf]
            return True
        return False

    def SetMany(pairs):
        """Set each (key, value) of `pairs` and save once, under one hold of
        the lock: a reader sees all of them or none, and settings.json is
        written one time (the settings page's saves, ADR-005 S4ae). An empty
        `pairs` writes and saves nothing.

        When a set or the save raises, the settings in memory are put back
        as they were before the call (what was not there is removed again)
        and the error is raised: memory never holds what settings.json does
        not, which a reader would show and the next Set() would write
        silently. So while settings.json could not be read at launch and
        still cannot be opened, it raises SettingsUnreadable and sets
        nothing (strict, unlike Set() / Unset(), which change memory only).
        What it reads again first (_Settle) is what a failure puts back:
        the adopted file, not the stand-in."""
        pairs = list(pairs)
        if not pairs:
            return
        with SettingsManager.lock:
            SettingsManager._Settle()
            undo = []
            try:
                for key, value in pairs:
                    undo.append(SettingsManager._Previous(key))
                    deep_set(SettingsManager.settings, key, value)
                SettingsManager.SaveSettings()
            except BaseException:
                # Newest first: a later pair may have replaced the parent of
                # an earlier one's key.
                for path, previous in reversed(undo):
                    SettingsManager._PutBack(path, previous)
                raise

    def _Previous(key):
        """(path, previous) to put `key` back with after deep_set: the key
        and its value when it is there; else the shallowest part of it that
        is missing (deep_set creates it) and _ABSENT; (None, None) when an
        ancestor is not a dict (deep_set raises before changing anything)."""
        parts = key.split(".")
        node = SettingsManager.settings
        for depth, part in enumerate(parts):
            if not isinstance(node, dict):
                return None, None
            if part not in node:
                return ".".join(parts[:depth + 1]), _ABSENT
            if depth == len(parts) - 1:
                return key, node[part]
            node = node[part]
        return None, None

    def _PutBack(path, previous):
        if path is None:
            return
        if previous is not _ABSENT:
            deep_set(SettingsManager.settings, path, previous)
            return
        parent, _, leaf = path.rpartition(".")
        node = (deep_get(SettingsManager.settings, parent) if parent
                else SettingsManager.settings)
        if isinstance(node, dict):
            node.pop(leaf, None)

    def Get(key: str, default=None):
        with SettingsManager.lock:
            return deep_get(SettingsManager.settings, key, default)
