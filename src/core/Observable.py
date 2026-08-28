"""Qt-free notification channels for the core layer.

`StateManager` used to publish its four events through a `QObject` holding
Qt signals, which made the whole state layer depend on PySide6. The channels
here replace that: plain callback registries with no framework behind them
(ADR-004 D1).

Delivery semantics, deliberately chosen to be the ones a *direct* Qt
connection has, because that is what the notifier used to do for every
non-QObject receiver:

- synchronous, on the thread that called `notify()`;
- in subscription order;
- one subscriber's exception does not reach the notifier and does not stop
  the remaining subscribers. Measured, not assumed: a slot raising on a
  direct Qt connection does not propagate into `emit()` either -- PySide6
  prints the traceback and carries on to the next slot. Here the traceback
  goes to the application log instead of stderr, which is the only
  difference. Writers such as `StateManager.SaveState` therefore cannot be
  broken by a badly behaved observer, and a broken observer cannot be
  mistaken for a failed write.

Nothing here queues, defers or hops threads. The GUI thread boundary stays
where it always was, inside Qt: `src/StateManagerBridge.py` subscribes and
re-emits Qt signals, so a widget slot is still delivered by Qt's
AutoConnection (queued when the write came from a worker thread).

loguru is the app's logger everywhere and has no Qt dependency, so importing
it here keeps the core layer Qt-free (pinned by tests/test_core_qt_free.py).
"""
import threading

from loguru import logger


class ObserverChannel:
    """One event: subscribers in, `notify(*args)` out."""

    def __init__(self, name=""):
        self.name = name
        # Guards the subscriber list only. Callbacks run outside it so a
        # subscriber may subscribe/unsubscribe from inside a notification
        # without deadlocking (RLock would allow re-entry anyway, but a
        # list mutated mid-iteration would not be safe).
        self._lock = threading.RLock()
        self._callbacks = []

    def subscribe(self, callback):
        """Register `callback`; it is called with whatever `notify` passes.

        Duplicates are allowed and delivered twice, matching Qt's default
        (non-unique) connect. Callers that must not double-subscribe guard
        at their own construction site -- `StateManagerBridge.__init__`
        refuses a second instance outright, so its four subscriptions
        cannot be made twice.

        The channel holds a *strong* reference to `callback`, unlike a Qt
        connection to a bound method. Nothing is ever dropped implicitly,
        so a subscriber with a shorter life than the channel must
        unsubscribe itself. QWidgets therefore do not subscribe here at all;
        they connect to StateManagerBridge and let Qt disconnect them on
        destruction (pinned by tests/test_signal_lifetime.py).
        """
        with self._lock:
            self._callbacks.append(callback)
        return callback

    def unsubscribe(self, callback):
        """Remove one registration of `callback`; unknown callbacks are ok."""
        with self._lock:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass

    def notify(self, *args):
        """Deliver to every current subscriber, in order, isolating failures.

        The snapshot is taken under the lock so a subscriber that
        subscribes or unsubscribes from inside its own callback cannot
        mutate the list being iterated; the change takes effect from the
        next notification.

        Each callback is guarded on its own: a raising subscriber is logged
        with its traceback and the next one still runs. Notifiers are
        writers on the state path (SaveState, LoadState, CommitHeld) and
        must not be taken down -- nor half-delivered -- by one observer.
        """
        with self._lock:
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            try:
                callback(*args)
            except Exception:
                logger.exception(
                    f"observer for '{self.name}' raised; continuing with "
                    "the remaining subscribers")

    def subscriber_count(self):
        with self._lock:
            return len(self._callbacks)
