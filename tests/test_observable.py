"""ObserverChannel: the delivery contract the whole core layer rests on.

`src/core/Observable.py` replaced four Qt signals with ~40 lines of plain
Python, and every state notification in the application now goes through it
(StateManagerBridge, and everything the bridge re-emits to). The semantics
it promises -- ordered, synchronous, failure-isolated delivery -- used to be
Qt's problem and were therefore never worth testing. They are ours now.

Deliberately Qt-free and QApplication-free, like the module under test: this
file is the demonstration that core logic can be exercised as plain Python
(ADR-004 D1). Nothing here imports the widget layer.
"""
import threading

import pytest
from loguru import logger

from src.core.Observable import ObserverChannel


@pytest.fixture
def loggedErrors():
    """Capture what the channel logs when a subscriber raises."""
    records = []
    sink = logger.add(records.append, level="ERROR")
    try:
        yield records
    finally:
        logger.remove(sink)


# -- ordering and arguments ------------------------------------------------


def test_subscribers_are_called_in_subscription_order():
    channel = ObserverChannel("ordered")
    seen = []

    channel.subscribe(lambda: seen.append("first"))
    channel.subscribe(lambda: seen.append("second"))
    channel.subscribe(lambda: seen.append("third"))
    channel.notify()

    assert seen == ["first", "second", "third"]


def test_notify_passes_its_arguments_through_unchanged():
    # held_dirty_changed(prefix, bool) is the two-argument channel; the
    # payload dict of state_updated must arrive as the same object, not a
    # copy (the copy happens later, in the Signal(dict) re-emit).
    channel = ObserverChannel("held_dirty_changed")
    seen = []
    channel.subscribe(lambda *args: seen.append(args))

    payload = {"revision": 7}
    channel.notify("score.1", True)
    channel.notify(payload)

    assert seen[0] == ("score.1", True)
    assert seen[1][0] is payload


def test_notify_with_no_subscribers_is_a_no_op():
    ObserverChannel("empty").notify("anything")


# -- registration bookkeeping ----------------------------------------------


def test_subscribing_the_same_callback_twice_delivers_twice():
    """Matches Qt's default (non-unique) connect, deliberately.

    The channel does not deduplicate, so double-registration is a real
    hazard -- which is why StateManagerBridge refuses to be built twice
    rather than relying on the channel to notice.
    """
    channel = ObserverChannel("duplicates")
    calls = []

    def callback():
        calls.append(1)

    channel.subscribe(callback)
    channel.subscribe(callback)
    channel.notify()

    assert calls == [1, 1]
    assert channel.subscriber_count() == 2


def test_unsubscribe_removes_one_registration_at_a_time():
    channel = ObserverChannel("duplicates")
    calls = []

    def callback():
        calls.append(1)

    channel.subscribe(callback)
    channel.subscribe(callback)
    channel.unsubscribe(callback)
    channel.notify()

    assert calls == [1], "unsubscribe removed both registrations, not one"

    channel.unsubscribe(callback)
    channel.notify()
    assert calls == [1]
    assert channel.subscriber_count() == 0


def test_unsubscribing_an_unknown_callback_is_silent():
    channel = ObserverChannel("unknown")
    channel.unsubscribe(lambda: None)          # never subscribed
    assert channel.subscriber_count() == 0


def test_subscribe_returns_the_callback_so_it_can_be_kept_for_removal():
    channel = ObserverChannel("returns")
    callback = channel.subscribe(lambda: None)
    channel.unsubscribe(callback)
    assert channel.subscriber_count() == 0


# -- re-entrancy: mutating the list from inside a notification -------------


def test_subscribing_during_a_notification_does_not_disturb_it():
    """The snapshot decides the round; the newcomer starts from the next.

    Without the snapshot this is a RuntimeError (list mutated while
    iterating) at best and an infinite loop at worst -- the new subscriber
    would be reached by the very notification that added it.
    """
    channel = ObserverChannel("reentrant_subscribe")
    seen = []

    def late():
        seen.append("late")

    def adder():
        seen.append("adder")
        channel.subscribe(late)

    channel.subscribe(adder)
    channel.notify()
    assert seen == ["adder"], "a subscriber added mid-notification was called"

    channel.notify()
    assert seen == ["adder", "adder", "late"]


def test_unsubscribing_during_a_notification_takes_effect_next_round():
    channel = ObserverChannel("reentrant_unsubscribe")
    seen = []

    def second():
        seen.append("second")

    def remover():
        seen.append("remover")
        channel.unsubscribe(second)

    channel.subscribe(remover)
    channel.subscribe(second)

    channel.notify()
    assert seen == ["remover", "second"], (
        "the snapshot was taken before the removal, so this round still "
        "delivers to it")

    channel.notify()
    assert seen == ["remover", "second", "remover"]


def test_a_subscriber_may_unsubscribe_itself_without_deadlocking():
    # A "deliver once" subscriber is the obvious use, and it would hang
    # forever if notify() held the lock across the callbacks.
    channel = ObserverChannel("self_removal")
    calls = []

    def once():
        calls.append(1)
        channel.unsubscribe(once)

    channel.subscribe(once)
    channel.notify()
    channel.notify()

    assert calls == [1]
    assert channel.subscriber_count() == 0


def test_notifying_from_inside_a_notification_does_not_deadlock():
    # The lock is an RLock and is released before the callbacks run, so a
    # cascade (one channel's subscriber writing state that notifies again)
    # cannot self-lock.
    channel = ObserverChannel("cascade")
    seen = []

    def cascading(depth):
        seen.append(depth)
        if depth < 3:
            channel.notify(depth + 1)

    channel.subscribe(cascading)
    channel.notify(1)

    assert seen == [1, 2, 3]


# -- failure isolation -----------------------------------------------------


def test_a_raising_subscriber_does_not_stop_the_others(loggedErrors):
    """The pin for the exception isolation added in review.

    Notifiers are writers on the state path (SaveState, LoadState,
    CommitHeld). One misbehaving observer must not abort the write, and must
    not silently cost every later observer its notification either.
    """
    channel = ObserverChannel("state_updated")
    seen = []

    channel.subscribe(lambda: seen.append("before"))

    def boom():
        raise ValueError("subscriber is broken")

    channel.subscribe(boom)
    channel.subscribe(lambda: seen.append("after"))

    channel.notify()          # must not raise

    assert seen == ["before", "after"]
    assert len(loggedErrors) == 1, "the failure was swallowed without a log"
    logged = loggedErrors[0]
    assert "state_updated" in logged, "the log does not name the channel"
    assert "ValueError" in logged and "subscriber is broken" in logged, (
        "the traceback is missing, so the log cannot be acted on")


def test_a_failing_subscriber_stays_subscribed(loggedErrors):
    # Deliberate: the channel does not decide that a subscriber is beyond
    # help. A transient failure (a half-built widget mid-teardown) would
    # otherwise unsubscribe a live consumer for good.
    channel = ObserverChannel("keeps_failing")
    attempts = []

    def flaky():
        attempts.append(len(attempts))
        if len(attempts) == 1:
            raise RuntimeError("first time only")

    channel.subscribe(flaky)
    channel.notify()
    channel.notify()

    assert attempts == [0, 1]
    assert len(loggedErrors) == 1


def test_an_exception_never_reaches_the_notifier(loggedErrors):
    # SaveState's `except TypeError` is documented as unreachable partly
    # because of this: a subscriber's TypeError is caught here, not there.
    channel = ObserverChannel("isolated")
    channel.subscribe(lambda payload: payload.missing_attribute)

    channel.notify({"revision": 1})

    assert len(loggedErrors) == 1


# -- concurrency smoke -----------------------------------------------------


def test_concurrent_subscribe_notify_and_unsubscribe_stay_consistent():
    """Writes come from Start.gg workers, the Flask thread and the GUI.

    Not a proof of thread safety -- a smoke test. It asserts the two things
    a broken lock would show first: no thread raises (a list mutated during
    iteration), and the registry is exactly empty once every subscriber has
    removed itself.
    """
    channel = ObserverChannel("threads")
    errors = []
    delivered = []
    start = threading.Barrier(9)

    def subscriber(_value):
        delivered.append(1)

    def churn(index):
        try:
            start.wait(timeout=5)
            callbacks = []
            for _ in range(50):
                callbacks.append(channel.subscribe(subscriber))
                channel.notify(index)
            for callback in callbacks:
                channel.unsubscribe(callback)
        except Exception as error:   # pragma: no cover - failure path
            errors.append(error)

    threads = [threading.Thread(target=churn, args=(index,))
               for index in range(8)]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a thread never finished (deadlock?)"

    assert not errors, f"concurrent access raised: {errors}"
    assert channel.subscriber_count() == 0, (
        "subscriptions leaked: every thread removed exactly what it added")
    assert delivered, "nothing was delivered at all"
