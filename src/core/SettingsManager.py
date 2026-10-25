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

    def Unset(key: str):
        with SettingsManager.lock:
            deep_unset(SettingsManager.settings, key)
            SettingsManager.SaveSettings()

    def Get(key: str, default=None):
        with SettingsManager.lock:
            return deep_get(SettingsManager.settings, key, default)
