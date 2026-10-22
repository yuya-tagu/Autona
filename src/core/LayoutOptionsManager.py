"""Per-layout option storage (``./user_data/layout_options.json``).

The persisted source of truth for every option an OBS layout exposes; the
mirror the overlays actually render from is ``layout_options.*`` in
StateManager. Both sides are written together by
``AutonaActions.SetLayoutOption``, which is why this module lives in core:
splitting a mirror across the layer boundary is what "half converted" means,
and the widget would otherwise keep owning one of the two writes.

Moved here from ``src/LayoutOptionsManager.py`` in headless core Step 5
(instalment 5). ``src/LayoutOptions/LayoutOptionsWidget.py`` was its only
importer.

Import side effects: none (agent-pitfalls #1) -- the load and the
create-if-missing that used to run at import time are ``Initialize()`` now,
called from ``src/bootstrap.py``, exactly like ``SettingsManager``'s.
"""
import os

import orjson

from .DictHelper import *


class LayoutOptionsManager:
    settings = {}
    _initialized = False

    def Initialize():
        """Create ./user_data/layout_options.json if missing and load it.

        Idempotent, for the same reason SettingsManager.Initialize() is: a
        second call must not silently reload over options an operator has
        already changed in this session.
        """
        if LayoutOptionsManager._initialized:
            return
        LayoutOptionsManager._initialized = True

        # SettingsManager.Initialize() runs first in bootstrap_core() and
        # creates ./user_data, but this must not depend on that ordering --
        # the import-time version did, and raised on a first run in a clean
        # directory.
        os.makedirs("./user_data", exist_ok=True)

        if not os.path.isfile("./user_data/layout_options.json"):
            LayoutOptionsManager.SaveSettings()

        LayoutOptionsManager.LoadSettings()

    def SaveSettings():
        with open("./user_data/layout_options.json", 'wb') as file:
            file.write(orjson.dumps(LayoutOptionsManager.settings, option=orjson.OPT_NON_STR_KEYS))

    def LoadSettings():
        try:
            with open("./user_data/layout_options.json", 'rb') as file:
                LayoutOptionsManager.settings = orjson.loads(file.read())
        except:
            LayoutOptionsManager.settings = {}

    def Set(key: str, value):
        deep_set(LayoutOptionsManager.settings, key, value)
        LayoutOptionsManager.SaveSettings()

    def Unset(key: str):
        deep_unset(LayoutOptionsManager.settings, key)
        LayoutOptionsManager.SaveSettings()

    def Get(key: str, default=None):
        return deep_get(LayoutOptionsManager.settings, key, default)
