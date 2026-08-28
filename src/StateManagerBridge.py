"""Adapter: core state notifications -> Qt signals for the widget layer.

`StateManager` lives in `src/core` and must not import Qt (ADR-004 D1,
pinned by tests/test_core_qt_free.py), so it publishes through the plain
callback channels in `src/core/Observable.py`. Everything Qt-side still
wants signals, and this object is the one place that converts between the
two. The four signals carry the same names and argument shapes the old
signal holder on StateManager did, so a connect site only had to change
which object it names -- the attribute itself is gone, deliberately, so
that nothing can re-introduce Qt into the core layer by adding a signal
next to the state.

THREAD SEMANTICS -- the property this file exists to preserve:

The bridge does no queueing, no invokeMethod and no thread hop of its own.
A channel notification runs synchronously on whichever thread wrote the
state (a Start.gg worker, the Flask thread, the GUI thread), and the
re-emit below happens on that same thread. Delivery to the actual consumer
is therefore decided exactly where it always was: by Qt's AutoConnection,
against the affinity of this object -- which bootstrap_core() creates on
the GUI thread, just as the module-level QObject that used to hold these
signals was created there. Measured, not assumed: a worker-thread write is
queued onto the GUI thread for QObject bound methods *and* for plain
callables (PySide6 routes the latter through its own receiver QObject), and
that is unchanged by this indirection. Adding a hop here would change it
for every consumer at once, so do not.

`state_updated` keeps its `Signal(dict)` signature deliberately, but not for
the reason first written here. The real property is the QVariantMap
conversion: PySide6 copies the dict -- nested lists and dicts included -- on
direct connections as well as queued ones, so a GUI slot never holds a live
reference into the delta payload that SaveState is still working on.
Switching to Signal(object) would hand out those references. That is a
behaviour change, not a cleanup.

What it does NOT do, contrary to the comment this replaces: raise TypeError
for a payload Qt cannot marshal. Measured on PySide6 6.11 -- emit() does not
raise; Shiboken prints "Cannot copy-convert ... (dict) to C++" to stderr and
the slot receives `{}`. So the `except TypeError` in `StateManager.SaveState`
never fires. It is documented in place as a defensive branch, together with
the limitation that follows from it (a marshalling failure would be silent
from the application's point of view) and the reason this payload cannot
actually trigger one.
"""
from qtpy.QtCore import QObject, Signal

from .core.StateManager import StateManager


class StateManagerBridge(QObject):
    # Created once by bootstrap_core(); widgets reach it as
    # StateManagerBridge.instance, matching the other singletons.
    instance = None

    state_big_change = Signal()
    state_updated = Signal(dict)
    # Deferred publication (Apply button). held_dirty_changed fires when a
    # held subtree gains its first pending change (True) or is committed
    # (False). held_state_updated fires (coalesced per save cycle) whenever
    # live-but-unpublished data under a held prefix changes, so desktop
    # widgets that render from the live state can refresh without waiting
    # for the commit.
    held_dirty_changed = Signal(str, bool)
    held_state_updated = Signal(str)

    def __init__(self, parent=None):
        # A second bridge would subscribe the same four channels again and
        # every state notification would reach the widget layer twice --
        # doubled repaints, doubled WebSocket delta broadcasts, and no error
        # anywhere to point at the cause. Refusing construction turns that
        # silent corruption into an immediate, attributable failure. The
        # guard lives here rather than only in bootstrap_core() so it also
        # covers a hand-rolled `StateManagerBridge()` in a test or a script.
        # Rebuilding on purpose means clearing `instance` first, which is
        # exactly the deliberate act that should be visible in the diff.
        if StateManagerBridge.instance is not None:
            raise RuntimeError(
                "StateManagerBridge already exists; it is a process-wide "
                "singleton created by bootstrap_core(). Use "
                "StateManagerBridge.instance instead of constructing another "
                "one -- a second bridge re-emits every state notification.")

        super().__init__(parent)
        observers = StateManager.observers
        observers.state_big_change.subscribe(self._onStateBigChange)
        observers.state_updated.subscribe(self._onStateUpdated)
        observers.held_dirty_changed.subscribe(self._onHeldDirtyChanged)
        observers.held_state_updated.subscribe(self._onHeldStateUpdated)

    # -- channel callbacks: re-emit, nothing else --------------------------

    def _onStateBigChange(self):
        self.state_big_change.emit()

    def _onStateUpdated(self, payload):
        self.state_updated.emit(payload)

    def _onHeldDirtyChanged(self, prefix, dirty):
        self.held_dirty_changed.emit(prefix, dirty)

    def _onHeldStateUpdated(self, prefix):
        self.held_state_updated.emit(prefix)
