import os
import orjson
from ..Helpers.AutonaDictHelper import *

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
        with open("./user_data/settings.json", 'wb') as file:
            file.write(orjson.dumps(SettingsManager.settings, option=orjson.OPT_NON_STR_KEYS))

    def LoadSettings():
        try:
            with open("./user_data/settings.json", 'rb') as file:
                SettingsManager.settings = orjson.loads(file.read())
        except:
            SettingsManager.settings = {}

    def Set(key: str, value):
        deep_set(SettingsManager.settings, key, value)
        SettingsManager.SaveSettings()
    
    def Unset(key: str):
        deep_unset(SettingsManager.settings, key)
        SettingsManager.SaveSettings()

    def Get(key: str, default=None):
        return deep_get(SettingsManager.settings, key, default)
