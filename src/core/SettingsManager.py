import os
import tempfile
import threading
import orjson
from .DictHelper import *

# Default port for the built-in web server (OBS browser sources, Web UI,
# external control API). Deliberately NOT 5000: that is Flask's own default and
# the port the upstream TournamentStreamHelper family listens on, so on a
# streaming PC running several tools at once the collision is real. 5107 is
# unassigned by IANA and has no common consumer use.
# Keep in sync with web/src/env.js (dev fallback).
DEFAULT_WEBSERVER_PORT = 5107

# SetMany's mark for "the key was not there" (a value may be None).
_ABSENT = object()


class SettingsManager:
    settings = {}
    _initialized = False
    # True when settings.json exists but LoadSettings() could not read it, so
    # `settings` is an empty stand-in rather than what the user saved. Lets a
    # reader tell "not set yet" from "unknown" (WebAccess.Token() does not
    # issue a new access token over one it could not read).
    loadFailed = False
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

        if not os.path.isfile("./user_data/settings.json"):
            SettingsManager.SaveSettings()

        SettingsManager.LoadSettings()

    def SaveSettings():
        with SettingsManager.lock:
            path = "./user_data/settings.json"
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
        with SettingsManager.lock:
            try:
                with open("./user_data/settings.json", 'rb') as file:
                    SettingsManager.settings = orjson.loads(file.read())
                SettingsManager.loadFailed = False
            except:
                SettingsManager.settings = {}
                SettingsManager.loadFailed = True

    def Set(key: str, value):
        with SettingsManager.lock:
            deep_set(SettingsManager.settings, key, value)
            SettingsManager.SaveSettings()

    def SetMany(pairs):
        """Set each (key, value) of `pairs` and save once, under one hold of
        the lock: a reader sees all of them or none, and settings.json is
        written one time (the settings page's saves, ADR-005 S4ae). An empty
        `pairs` writes and saves nothing.

        When a set or the save raises, the settings in memory are put back
        as they were before the call (what was not there is removed again)
        and the error is raised: memory never holds what settings.json does
        not, which a reader would show and the next Set() would write
        silently."""
        pairs = list(pairs)
        if not pairs:
            return
        with SettingsManager.lock:
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

    def Unset(key: str):
        with SettingsManager.lock:
            deep_unset(SettingsManager.settings, key)
            SettingsManager.SaveSettings()

    def Get(key: str, default=None):
        with SettingsManager.lock:
            return deep_get(SettingsManager.settings, key, default)
