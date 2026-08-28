"""Step 1 compatibility shim. New code must import from `src.core`.

The state layer moved to `src/core/StateManager.py` (ADR-004 D1 / headless
core Step 1). This module re-exports it unchanged so the ~30 existing
`from .StateManager import StateManager` call sites keep working while the
migration runs; it is kept for one generation and then deleted.

Nothing may be added here. In particular the Qt signals that used to live
next to the state layer are gone: subscribe to `StateManager.observers` for
the Qt-free channels, or connect to `StateManagerBridge.instance` for the
Qt signals of the same names.
"""
from .core.StateManager import (  # noqa: F401
    StateManager,
    StateManagerObservers,
)
