"""Step 1 compatibility shim. New code must import from `src.core`.

The settings layer moved to `src/core/SettingsManager.py` (ADR-004 D1 /
headless core Step 1). This module re-exports it unchanged so the existing
`from .SettingsManager import SettingsManager` call sites keep working while
the migration runs; it is kept for one generation and then deleted.

The star import mirrors the old module's export surface exactly: it used to
do `from .Helpers.AutonaDictHelper import *` at the top, so
`from .SettingsManager import *` (AutonaThumbnailSettingsWidget does that)
also pulled in deep_get/deep_set/... Dropping it here would quietly change
what that widget's module namespace contains.
"""
from .core.SettingsManager import *  # noqa: F401,F403
from .core.SettingsManager import SettingsManager  # noqa: F401
